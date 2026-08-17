"""Run and score the nine-role Feature 005 matrix against the live local API.

The detailed report is private and must stay under the ignored reports folder.
The shareable summary contains only case IDs, counters, scores and root-cause
categories; it never contains questions, answers, citations, tokens or errors.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "tests" / "fixtures" / "feature005_role_matrix.json"
PRIVATE_DEFAULT = ROOT / "reports" / "feature005" / "private-role-evaluation.json"
SUMMARY_DEFAULT = ROOT / "reports" / "feature005" / "role-evaluation-summary.json"
INTERNAL_MARKER_RE = re.compile(
    r"#ref-source|\blegal:|chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id",
    re.IGNORECASE,
)

_TITLE_FACETS = {
    "authority": ("thẩm quyền", "nơi nộp", "cơ quan"),
    "documents": ("hồ sơ", "giấy tờ"),
    "procedure": ("trình tự", "thủ tục", "các bước"),
    "deadline": ("thời hạn", "thời gian"),
    "fee": ("lệ phí", "chi phí", "mức thu"),
    "form": ("biểu mẫu", "tờ khai", "mẫu đơn"),
    "condition": ("điều kiện", "ngoại lệ"),
    "rule": ("kết luận", "quy định", "xử lý"),
}

_ROLE_CUES = {
    "citizen": ("việc cần làm", "hồ sơ cần chuẩn bị", "nơi nộp", "các bước"),
    "officer": ("kết luận chuyên môn", "thẩm quyền", "quy trình xử lý", "điểm cần xác minh"),
    "admin": ("kết luận đã kiểm chứng", "coverage", "provenance", "nguồn"),
}


def _fold(value: Any) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(
        character
        for character in text
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def _uses_foreign_representation(question: str) -> bool:
    folded = _fold(question)
    return any(
        marker in folded
        for marker in (
            "co quan dai dien",
            "dai su quan",
            "lanh su quan",
            "co quan lanh su",
        )
    )


def _contains_foreign_representation_source(
    payload: Mapping[str, Any],
) -> bool:
    citations: list[Mapping[str, Any]] = []
    citations.extend(
        item
        for item in (payload.get("citations") or [])
        if isinstance(item, Mapping)
    )
    for section in payload.get("answer_sections") or []:
        if isinstance(section, Mapping):
            citations.extend(
                item
                for item in (section.get("citations") or [])
                if isinstance(item, Mapping)
            )
    return any(
        any(
            marker in _fold(citation.get("document_title"))
            for marker in (
                "co quan dai dien ngoai giao",
                "co quan dai dien lanh su",
                "co quan dai dien viet nam o nuoc ngoai",
            )
        )
        for citation in citations
    )


def _contains_known_wrong_procedure_source(
    case: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    question = _fold(case.get("question"))
    if "khai sinh" not in question:
        return False
    citations: list[Mapping[str, Any]] = []
    for section in payload.get("answer_sections") or []:
        if isinstance(section, Mapping):
            citations.extend(
                item
                for item in (section.get("citations") or [])
                if isinstance(item, Mapping)
            )
    return any(
        "hon nhan va gia dinh" in _fold(item.get("document_title"))
        and "ho tich" not in _fold(item.get("document_title"))
        for item in citations
    )


def _valid_form(item: Mapping[str, Any]) -> bool:
    return bool(
        item.get("official_level") == "official"
        and item.get("review_status") == "approved"
        and item.get("has_official_file") is True
        and str(item.get("download_url") or "").strip()
        and str(item.get("procedure_id") or "").strip()
    )


def _citation_contract_key(item: Mapping[str, Any]) -> tuple[str, str]:
    law_number = _fold(item.get("law_number")).upper()
    article_number = str(item.get("article_number") or "").strip()
    return law_number, article_number


def _matches_expected_citation(
    actual: set[tuple[str, str]],
    expected: Mapping[str, Any],
) -> bool:
    expected_law, expected_article = _citation_contract_key(expected)
    return any(
        law == expected_law
        and (not expected_article or article == expected_article)
        for law, article in actual
    )


def _section_facets(section: Mapping[str, Any]) -> set[str]:
    text = " ".join(
        str(section.get(key) or "")
        for key in ("title", "answer", "guidance")
    ).casefold()
    return {
        facet
        for facet, phrases in _TITLE_FACETS.items()
        if any(phrase in text for phrase in phrases)
    }


def score_response(case: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    answer = str(payload.get("answer") or "")
    visible_payload = json.dumps(
        {
            key: payload.get(key)
            for key in (
                "answer",
                "answer_sections",
                "citations",
                "recommended_forms",
                "forms_unavailable",
            )
        },
        ensure_ascii=False,
        default=str,
    )
    sections = [
        dict(item)
        for item in (payload.get("answer_sections") or [])
        if isinstance(item, Mapping)
    ]
    sufficient = [
        section
        for section in sections
        if section.get("status") == "sufficiently_evidenced"
    ]
    claim_source_ok = all(
        bool(section.get("answer")) and bool(section.get("citations"))
        for section in sufficient
    )
    citation_rows = [
        item
        for item in (payload.get("citations") or [])
        if isinstance(item, Mapping)
    ]
    for section in sections:
        citation_rows.extend(
            item
            for item in (section.get("citations") or [])
            if isinstance(item, Mapping)
        )
    actual_citations = {
        _citation_contract_key(item)
        for item in citation_rows
        if item.get("law_number")
    }
    required_citations = [
        item
        for item in (case.get("required_citations_all") or [])
        if isinstance(item, Mapping)
    ]
    missing_expected_citations = [
        (
            f"{item.get('law_number')}"
            f"#{item.get('article_number') or '*'}"
        )
        for item in required_citations
        if not _matches_expected_citation(actual_citations, item)
    ]
    requires_clarification = bool(case.get("requires_clarification"))
    has_clarifying_question = any(
        str(section.get("clarifying_question") or "").strip()
        for section in sections
    )
    clarification_ok = (
        not requires_clarification
        or (has_clarifying_question and not sufficient)
    )
    covered_facets: set[str] = set()
    for section in sufficient:
        covered_facets.update(_section_facets(section))
    unavailable_facets: set[str] = set()
    for section in sections:
        if section.get("status") != "sufficiently_evidenced":
            unavailable_facets.update(_section_facets(section))
    required = set(case.get("required_facets") or [])
    requires_official_form = bool(case.get("requires_official_form"))
    allows_verified_form_gap = bool(case.get("allows_verified_form_gap"))
    if requires_official_form and not allows_verified_form_gap:
        unavailable_facets.discard("form")
    if "rule" in required and sufficient:
        covered_facets.add("rule")

    forms = [
        dict(item)
        for item in (payload.get("recommended_forms") or [])
        if isinstance(item, Mapping)
    ]
    invalid_forms = [item for item in forms if not _valid_form(item)]
    form_requested = bool(case.get("requests_form"))
    if form_requested and forms and not invalid_forms:
        covered_facets.add("form")
    elif requires_official_form:
        covered_facets.discard("form")
    if (
        form_requested
        and payload.get("forms_unavailable") is True
        and (not requires_official_form or allows_verified_form_gap)
    ):
        unavailable_facets.add("form")
    handled_facets = covered_facets | unavailable_facets
    missing = sorted(required - handled_facets)
    coverage_ratio = len(required & handled_facets) / len(required) if required else 1.0
    form_status = (
        "verified"
        if form_requested and forms and not invalid_forms
        else "verified_gap"
        if (
            form_requested
            and payload.get("forms_unavailable") is True
            and allows_verified_form_gap
        )
        else "missing"
        if form_requested and payload.get("forms_unavailable") is True
        else "invalid"
        if form_requested
        else "not_applicable"
    )
    role = str(case.get("role") or "citizen")
    role_blob = " ".join(
        [
            answer,
            *[
                " ".join(
                    str(section.get(key) or "")
                    for key in ("title", "answer", "guidance", "limitation")
                )
                for section in sections
            ],
        ]
    ).casefold()
    if role == "admin":
        trace = payload.get("rag_trace") if isinstance(payload.get("rag_trace"), Mapping) else {}
        role_ok = bool(
            trace
            and trace.get("evidence_coverage") is not None
            and trace.get("form_provenance") is not None
        )
    else:
        role_ok = any(cue in role_blob for cue in _ROLE_CUES.get(role, ()))

    root_causes: set[str] = set()
    if not sections:
        root_causes.update(("model", "validator"))
    if missing:
        root_causes.update(("issue planner", "retrieval"))
    if missing_expected_citations:
        root_causes.add("retrieval")
    if not clarification_ok:
        root_causes.add("issue planner")
    if invalid_forms or form_status in {"missing", "invalid"}:
        root_causes.add("form catalog")
    wrong_jurisdiction = (
        not _uses_foreign_representation(str(case.get("question") or ""))
        and _contains_foreign_representation_source(payload)
    )
    wrong_procedure = _contains_known_wrong_procedure_source(case, payload)
    if INTERNAL_MARKER_RE.search(visible_payload):
        root_causes.add("renderer")
    if wrong_jurisdiction or wrong_procedure:
        root_causes.add("retrieval")
    if role == "admin" and not role_ok:
        root_causes.add("UI")

    critical_failure = bool(
        INTERNAL_MARKER_RE.search(visible_payload)
        or wrong_jurisdiction
        or wrong_procedure
        or invalid_forms
        or (
            requires_official_form
            and form_status not in {"verified", "verified_gap"}
        )
        or missing_expected_citations
        or not clarification_ok
        or any(
            section.get("status") == "sufficiently_evidenced"
            and (not section.get("answer") or not section.get("citations"))
            for section in sections
        )
    )
    score = round(
        10
        * (
            0.35 * coverage_ratio
            + 0.30 * float(claim_source_ok)
            + 0.20 * float(role_ok)
            + 0.15 * float(
                form_status in {"verified", "verified_gap", "not_applicable"}
                or (form_status == "missing" and not requires_official_form)
            )
        ),
        2,
    )
    return {
        "score": score,
        "pass": (
            not critical_failure
            and coverage_ratio >= 0.9
            and claim_source_ok
            and role_ok
            and clarification_ok
        ),
        "critical_failure": critical_failure,
        "coverage_ratio": round(coverage_ratio, 4),
        "covered_facets": sorted(covered_facets),
        "unavailable_facets": sorted(unavailable_facets),
        "handled_facets": sorted(handled_facets),
        "missing_facets": missing,
        "claim_source_ok": claim_source_ok,
        "missing_expected_citations": missing_expected_citations,
        "clarification_ok": clarification_ok,
        "role_ok": role_ok,
        "form_status": form_status,
        "root_causes": sorted(root_causes),
    }


def _role_token(client: httpx.Client, role: str) -> str:
    direct = os.getenv(f"FEATURE005_{role.upper()}_TOKEN", "").strip()
    if direct:
        return direct
    password = os.getenv(f"FEATURE005_{role.upper()}_PASSWORD", "").strip()
    identifier = os.getenv(f"FEATURE005_{role.upper()}_IDENTIFIER", role).strip()
    if not password:
        raise RuntimeError(f"Missing isolated local credentials for role {role}")
    response = client.post(
        "/api/auth/login",
        json={"identifier": identifier, "password": password, "role": role},
        timeout=30,
    )
    response.raise_for_status()
    token = str(response.json().get("token") or "")
    if not token:
        raise RuntimeError(f"Login did not return a token for role {role}")
    return token


def _public_evaluation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Remove source-contract details from the shareable role summary."""
    return {
        key: value
        for key, value in evaluation.items()
        if key != "missing_expected_citations"
    }


def run(
    base_url: str,
    *,
    request_timeout: float = 120.0,
    final_answer_model: str | None = None,
    case_delay_seconds: float = 0.0,
    case_ids: Sequence[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    selected_case_ids = set(case_ids or ())
    selected_cases = [
        case
        for case in matrix["cases"]
        if not selected_case_ids or case["id"] in selected_case_ids
    ]
    unknown_case_ids = selected_case_ids.difference(
        case["id"] for case in selected_cases
    )
    if unknown_case_ids:
        raise ValueError(
            f"Unknown Feature 005 case IDs: {sorted(unknown_case_ids)}"
        )
    private_rows: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url, timeout=request_timeout) as client:
        tokens = {role: _role_token(client, role) for role in ("citizen", "officer", "admin")}
        for case in selected_cases:
            role = case["role"]
            started = time.perf_counter()
            try:
                request_body = {
                    "question": case["question"],
                    "role": role,
                    "show_rag_trace": role == "admin",
                    "idempotency_key": f"feature005-matrix-{case['id']}-{time.time_ns()}",
                }
                if final_answer_model:
                    request_body["final_answer_model"] = final_answer_model
                response = client.post(
                    "/api/search/ask/simple",
                    headers={"Authorization": f"Bearer {tokens[role]}"},
                    json=request_body,
                )
                http_status = response.status_code
                payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                transport_error = None
            except httpx.TimeoutException:
                http_status = 0
                payload = {}
                transport_error = "timeout"
            except httpx.HTTPError:
                http_status = 0
                payload = {}
                transport_error = "transport_error"
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            scored = score_response(case, payload)
            if transport_error:
                scored["pass"] = False
                scored["critical_failure"] = True
                scored["root_causes"] = sorted(
                    set(scored["root_causes"]) | {"retrieval", "model"}
                )
            private_rows.append(
                {
                    "case": case,
                    "http_status": http_status,
                    "elapsed_ms": elapsed_ms,
                    "transport_error": transport_error,
                    "response": payload,
                    "evaluation": scored,
                }
            )
            if case_delay_seconds > 0:
                time.sleep(case_delay_seconds)

    root_causes = Counter(
        cause
        for row in private_rows
        for cause in row["evaluation"]["root_causes"]
    )
    summary_rows = [
        {
            "case_id": row["case"]["id"],
            "role": row["case"]["role"],
            "http_status": row["http_status"],
            "elapsed_ms": row["elapsed_ms"],
            **_public_evaluation(row["evaluation"]),
        }
        for row in private_rows
    ]
    summary = {
        "schema_version": 1,
        "model_override_used": bool(final_answer_model),
        "case_count": len(summary_rows),
        "pass_count": sum(1 for row in summary_rows if row["pass"]),
        "critical_failure_count": sum(1 for row in summary_rows if row["critical_failure"]),
        "root_cause_counts": dict(root_causes),
        "cases": summary_rows,
    }
    return {"schema_version": 1, "cases": private_rows}, summary


def _report_outputs(args, private: dict, summary: dict) -> list[tuple[Path, dict]]:
    """Select persisted artifacts without ever deriving public data from private rows."""
    outputs = [(args.summary, summary)]
    if not args.no_private_report:
        outputs.insert(0, (args.private_report, private))
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--final-answer-model")
    parser.add_argument("--case-delay", type=float, default=0.0)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only this reviewed case ID; may be repeated.",
    )
    parser.add_argument("--private-report", type=Path, default=PRIVATE_DEFAULT)
    parser.add_argument(
        "--no-private-report",
        action="store_true",
        help="Write only the privacy-safe aggregate summary.",
    )
    parser.add_argument("--summary", type=Path, default=SUMMARY_DEFAULT)
    args = parser.parse_args()
    private, summary = run(
        args.base_url,
        request_timeout=args.request_timeout,
        final_answer_model=args.final_answer_model,
        case_delay_seconds=max(0.0, args.case_delay),
        case_ids=args.case_ids,
    )
    for path, payload in _report_outputs(args, private, summary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("case_count", "pass_count", "critical_failure_count", "root_cause_counts")}, ensure_ascii=False))
    return 0 if summary["critical_failure_count"] == 0 and summary["pass_count"] == summary["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
