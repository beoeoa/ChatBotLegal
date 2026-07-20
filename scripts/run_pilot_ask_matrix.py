"""Plan or explicitly run the full 30-case Ask endpoint/concurrency matrix.

The default mode is a no-network dry run. Live execution requires ``--execute``
and role bearer tokens supplied only through environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import math
import os
import re
import sys
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from scripts.pilot_quality_gate import (
        CONCURRENCY_LEVELS,
        DOMAINS,
        ENDPOINTS,
        ROLES,
        SCENARIO_CLASSES,
        validate_manifest,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from pilot_quality_gate import (  # type: ignore[no-redef]
        CONCURRENCY_LEVELS,
        DOMAINS,
        ENDPOINTS,
        ROLES,
        SCENARIO_CLASSES,
        validate_manifest,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "pilot" / "ask_quality_manifest.json"
DEFAULT_EXPERT = ROOT / "notebook_data" / "legal-golden-expert-review.json"
DEFAULT_OUTPUT = ROOT / "reports" / "pilot-ask-matrix-latest.json"
ROLE_TOKEN_ENV = {
    "citizen": "PILOT_CITIZEN_TOKEN",
    "officer": "PILOT_OFFICER_TOKEN",
}
_FORBIDDEN_REPORT_KEYS = {
    "answer",
    "authorization",
    "citations",
    "cookie",
    "credential",
    "password",
    "prompt",
    "question",
    "recommended_forms",
    "secret",
    "token",
}

Requester = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class RunnerConfigurationError(RuntimeError):
    """Raised before network I/O when the run is not safely configured."""


class ProgressResponseError(RuntimeError):
    """Raised when the progress stream finishes with a structured error."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        dest="mode",
        action="store_const",
        const="execute",
        help="Explicitly execute all 240 live Ask attempts.",
    )
    parser.set_defaults(mode="dry-run")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expert", type=Path, default=DEFAULT_EXPERT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--base-url",
        default=os.getenv("CHATBOT_API_URL", "http://127.0.0.1:5055"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerConfigurationError(f"Cannot read JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise RunnerConfigurationError(f"JSON artifact must be an object: {path}")
    return payload


def _expert_by_id(expert_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("review_id")): item
        for item in expert_payload.get("records") or []
        if isinstance(item, dict) and item.get("review_id")
    }


def _is_approved(item: dict[str, Any]) -> bool:
    score = item.get("expert_score")
    return bool(
        item.get("expert_review_status") == "approved"
        and str(item.get("expert_name") or "").strip()
        and str(item.get("reviewed_at") or "").strip()
        and not isinstance(score, bool)
        and isinstance(score, (int, float))
        and math.isfinite(float(score))
        and 0 <= float(score) <= 10
    )


def unapproved_review_ids(
    manifest: dict[str, Any], expert_payload: dict[str, Any]
) -> list[str]:
    expert_by_id = _expert_by_id(expert_payload)
    return sorted(
        review_id
        for review_id in (
            str(item.get("review_id") or "")
            for item in manifest.get("cases") or []
            if isinstance(item, dict)
        )
        if not _is_approved(expert_by_id.get(review_id, {}))
    )


def _idempotency_key(
    run_id: str, review_id: str, endpoint: str, concurrency: int
) -> str:
    digest = hashlib.sha256(
        f"{run_id}|{review_id}|{endpoint}|{concurrency}".encode("utf-8")
    ).hexdigest()[:24]
    return f"pilot-f4-{digest}"


def build_execution_plan(
    manifest: dict[str, Any],
    expert_payload: dict[str, Any],
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    manifest_errors = validate_manifest(manifest, expert_payload)
    if manifest_errors:
        raise RunnerConfigurationError(
            "Invalid pilot manifest: " + "; ".join(manifest_errors)
        )
    expert_by_id = _expert_by_id(expert_payload)
    plan: list[dict[str, Any]] = []
    for manifest_case, endpoint, concurrency in itertools.product(
        manifest.get("cases") or [], ENDPOINTS, CONCURRENCY_LEVELS
    ):
        review_id = str(manifest_case["review_id"])
        expert = expert_by_id[review_id]
        plan.append(
            {
                "review_id": review_id,
                "domain": str(manifest_case["domain"]),
                "role": str(manifest_case["role"]),
                "scenario_class": str(manifest_case["scenario_class"]),
                "endpoint": endpoint,
                "concurrency": concurrency,
                "question": str(expert.get("question") or ""),
                "legal_as_of": expert.get("legal_as_of"),
                "event_date": expert.get("event_date"),
                "expected_documents": expert.get("expected_documents") or [],
                "expected_articles": expert.get("expected_articles") or [],
                "official_form_ids": expert.get("official_form_ids") or [],
                "idempotency_key": _idempotency_key(
                    run_id, review_id, endpoint, concurrency
                ),
            }
        )
    return plan


def resolve_role_tokens(
    roles: Sequence[str], environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    tokens: dict[str, str] = {}
    for role in roles:
        env_name = ROLE_TOKEN_ENV.get(role)
        if not env_name:
            raise RunnerConfigurationError(f"No token environment mapping for role={role}")
        token = str(source.get(env_name) or "").strip()
        if not token:
            raise RunnerConfigurationError(
                f"Set {env_name} before using --execute; no fallback credential exists."
            )
        tokens[role] = token
    return tokens


def parse_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        if not data_lines:
            continue
        try:
            data = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as exc:
            raise ProgressResponseError("Invalid JSON in progress stream") from exc
        if isinstance(data, dict):
            events.append((event_name, data))
    return events


def parse_progress_response(body: str) -> dict[str, Any]:
    final_response: dict[str, Any] | None = None
    completed = False
    for event_name, data in parse_sse_events(body):
        if event_name == "error":
            code = str(data.get("code") or "ASK_PROGRESS_ERROR")
            raise ProgressResponseError(code)
        if event_name == "final" and isinstance(data.get("response"), dict):
            final_response = data["response"]
        if event_name == "complete":
            completed = True
    if final_response is None:
        raise ProgressResponseError("ASK_PROGRESS_FINAL_MISSING")
    if not completed:
        raise ProgressResponseError("ASK_PROGRESS_COMPLETE_MISSING")
    return final_response


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"\s+", " ", text).strip()


def _expected_labels(values: Any) -> list[str]:
    labels: list[str] = []
    for value in values or []:
        if isinstance(value, dict):
            label = next(
                (
                    str(value.get(key) or "").strip()
                    for key in ("law_number", "document_id", "id", "title", "name")
                    if value.get(key)
                ),
                "",
            )
        else:
            label = str(value or "").strip()
        if label:
            labels.append(label)
    return labels


def _labels_present(labels: Sequence[str], blob: str) -> bool:
    folded_blob = _fold(blob)
    for label in labels:
        folded_label = _fold(label)
        if folded_label and folded_label in folded_blob:
            continue
        law_number = re.search(r"\b\d{1,4}/\d{4}/[a-z0-9.-]+\b", folded_label)
        if law_number and law_number.group(0) in folded_blob:
            continue
        article = re.search(r"\b(?:dieu|article)\s*(\d+[a-z]?)\b", folded_label)
        if article and re.search(
            rf"\b(?:dieu|article)\s*{re.escape(article.group(1))}\b",
            folded_blob,
        ):
            continue
        return False
    return True


def _citation_has_identity_and_link(citation: dict[str, Any]) -> bool:
    identity = (
        citation.get("doc_id")
        or citation.get("document_id")
        or citation.get("law_number")
    )
    link = (
        citation.get("internal_url")
        or citation.get("source_url")
        or citation.get("pdf_url")
    )
    link_status = str(citation.get("link_status") or "").casefold()
    return bool(identity and link and link_status not in {"dead", "missing", "broken"})


def _critical_hallucination(response: dict[str, Any]) -> bool:
    if bool(response.get("critical_hallucination")):
        return True
    for flag in response.get("quality_flags") or []:
        value = flag.get("code") if isinstance(flag, dict) else flag
        if "critical_hallucination" in str(value or "").casefold():
            return True
    for claim in response.get("claim_validation") or []:
        if not isinstance(claim, dict) or claim.get("status") != "rejected":
            continue
        if str(claim.get("severity") or "critical").casefold() == "critical":
            return True
    return bool(response.get("critical_errors"))


def _repair_observation(response: dict[str, Any]) -> tuple[bool, bool]:
    if "repair_used" in response:
        return True, bool(response.get("repair_used"))
    trace = response.get("rag_trace") or {}
    if isinstance(trace, dict) and (
        "repair_used" in trace or "repair_reason" in trace
    ):
        return True, bool(trace.get("repair_used") or trace.get("repair_reason"))
    repair_flag = any(
        "repair" in str(flag.get("code") if isinstance(flag, dict) else flag).casefold()
        for flag in response.get("quality_flags") or []
    )
    return (True, True) if repair_flag else (False, False)


def _result_identity(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in (
            "review_id",
            "domain",
            "role",
            "scenario_class",
            "endpoint",
            "concurrency",
        )
    }


def _safe_latency_ms(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return round(parsed, 3)


def _safe_grounding_status(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold()
    allowed = {
        "grounded",
        "partially_grounded",
        "ungrounded",
        "not_applicable",
    }
    return normalized if normalized in allowed else None


def assess_response(task: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    citations = [
        item for item in response.get("citations") or [] if isinstance(item, dict)
    ]
    forms = [
        item
        for item in response.get("recommended_forms") or []
        if isinstance(item, dict)
    ]
    expected_documents = _expected_labels(task.get("expected_documents"))
    expected_articles = _expected_labels(task.get("expected_articles"))
    expected_forms = _expected_labels(task.get("official_form_ids"))
    citation_applicable = bool(expected_documents or expected_articles)
    form_applicable = bool(expected_forms)
    citation_blob = json.dumps(citations, ensure_ascii=False)
    citation_ok = bool(
        not citation_applicable
        or (
            citations
            and _labels_present(expected_documents + expected_articles, citation_blob)
            and all(_citation_has_identity_and_link(item) for item in citations)
        )
    )

    form_blob = json.dumps(forms, ensure_ascii=False)
    matching_forms = [
        item
        for item in forms
        if any(
            _fold(expected)
            in _fold(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("id", "form_id", "procedure_form_id", "name", "title")
                )
            )
            for expected in expected_forms
        )
    ]
    forms_ok = bool(
        not form_applicable
        or (
            _labels_present(expected_forms, form_blob)
            and matching_forms
            and all(
                item.get("review_status") == "approved"
                and item.get("official_level") == "official"
                and (item.get("download_url") or item.get("local_file"))
                for item in matching_forms
            )
        )
    )
    repair_observed, repair_used = _repair_observation(response)
    answer_fingerprint = hashlib.sha256(
        _fold(response.get("answer") or "").encode("utf-8")
    ).hexdigest()
    return {
        "citation_applicable": citation_applicable,
        "citation_ok": citation_ok,
        "form_applicable": form_applicable,
        "forms_ok": forms_ok,
        "critical_hallucination": _critical_hallucination(response),
        "repair_observed": repair_observed,
        "repair_used": repair_used,
        "answer_fingerprint": answer_fingerprint,
        "citation_count": len(citations),
        "valid_citation_count": sum(
            1 for item in citations if _citation_has_identity_and_link(item)
        ),
        "form_count": len(forms),
        "matched_form_count": len(matching_forms),
        "grounding_status": _safe_grounding_status(response.get("grounding_status")),
        "rejected_claim_count": sum(
            1
            for item in response.get("claim_validation") or []
            if isinstance(item, dict) and item.get("status") == "rejected"
        ),
        "latency_ms": _safe_latency_ms(response.get("latency_ms")),
    }


def completed_result(
    task: dict[str, Any],
    response: dict[str, Any],
    *,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        **_result_identity(task),
        "status": "completed",
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        **assess_response(task, response),
    }


def _failed_result(task: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        **_result_identity(task),
        "status": "failed",
        "citation_applicable": bool(
            task.get("expected_documents") or task.get("expected_articles")
        ),
        "citation_ok": False,
        "form_applicable": bool(task.get("official_form_ids")),
        "forms_ok": False,
        "critical_hallucination": False,
        "repair_observed": False,
        "repair_used": False,
        "error_type": type(exc).__name__,
    }


def assert_report_privacy(value: Any, *, path: str = "report") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_REPORT_KEYS:
                raise ValueError(f"Forbidden report field at {path}.{key}")
            assert_report_privacy(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_report_privacy(item, path=f"{path}[{index}]")


async def execute_batch(
    tasks: Sequence[dict[str, Any]],
    *,
    concurrency: int,
    requester: Requester,
) -> list[dict[str, Any]]:
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(task: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await requester(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - result must preserve batch coverage
                return _failed_result(task, exc)

    return list(await asyncio.gather(*(worker(task) for task in tasks)))


async def run_matrix(
    manifest: dict[str, Any],
    expert_payload: dict[str, Any],
    *,
    mode: str,
    requester: Requester | None,
    run_id: str,
) -> dict[str, Any]:
    plan = build_execution_plan(manifest, expert_payload, run_id=run_id)
    pending = unapproved_review_ids(manifest, expert_payload)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "manifest_cases": len(manifest.get("cases") or []),
        "endpoints": list(ENDPOINTS),
        "concurrency_levels": list(CONCURRENCY_LEVELS),
        "planned_attempts": len(plan),
        "executed_attempts": 0,
        "ready_for_execute": not pending,
        "unapproved_review_ids": pending,
        "results": [],
    }
    if mode == "dry-run":
        return summary
    if mode != "execute":
        raise RunnerConfigurationError(f"Unsupported mode: {mode}")
    if pending:
        raise RunnerConfigurationError(
            "Expert approval is incomplete; refusing live run for: "
            + ", ".join(pending)
        )
    if requester is None:
        raise RunnerConfigurationError("Live execution requires a requester.")

    results: list[dict[str, Any]] = []
    for concurrency in CONCURRENCY_LEVELS:
        batch = [item for item in plan if item["concurrency"] == concurrency]
        results.extend(
            await execute_batch(
                batch,
                concurrency=concurrency,
                requester=requester,
            )
        )
    summary["executed_attempts"] = len(results)
    summary["results"] = results
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    assert_report_privacy(summary)
    return summary


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    assert_report_privacy(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _read_json(args.manifest)
    experts = _read_json(args.expert)
    run_id = uuid.uuid4().hex
    if args.mode == "dry-run":
        return await run_matrix(
            manifest,
            experts,
            mode="dry-run",
            requester=None,
            run_id=run_id,
        )

    tokens = resolve_role_tokens(ROLES)
    timeout = httpx.Timeout(args.timeout_seconds, connect=15.0)
    base_url = str(args.base_url).rstrip("/")
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        follow_redirects=True,
    ) as client:

        async def request_one(task: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            payload = {
                "question": task["question"],
                "role": task["role"],
                "domain": task["domain"],
                "legal_as_of": task.get("legal_as_of"),
                "event_date": task.get("event_date"),
                "idempotency_key": task["idempotency_key"],
                "show_rag_trace": True,
            }
            response = await client.post(
                task["endpoint"],
                headers={
                    "Authorization": f"Bearer {tokens[task['role']]}",
                    "X-User-Role": task["role"],
                },
                json={key: value for key, value in payload.items() if value is not None},
            )
            response.raise_for_status()
            if task["endpoint"].endswith("/progress"):
                data = parse_progress_response(response.text)
            else:
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Ask simple response is not a JSON object")
            return completed_result(
                task,
                data,
                elapsed_seconds=time.perf_counter() - started,
            )

        report = await run_matrix(
            manifest,
            experts,
            mode="execute",
            requester=request_one,
            run_id=run_id,
        )
    _write_json_atomic(args.output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(_run_cli(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = {key: value for key, value in report.items() if key != "results"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.mode == "dry-run":
        return 0
    failed = sum(
        1 for item in report.get("results") or [] if item.get("status") != "completed"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
