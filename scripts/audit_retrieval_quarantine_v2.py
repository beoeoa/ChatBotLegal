#!/usr/bin/env python3
"""Audit all quarantined source records without changing legal state or indexes.

The command is deliberately evidence-only.  A successful HTTP response proves
only that a URL responded at observation time; it does not prove that the
document is official, in force, in jurisdiction, or safe to serve.  Every
record therefore remains ``legal_review_status=required`` and
``approved_for_serving=false`` until an authorised legal review attaches an
attestation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_INPUT = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "quarantine-audit-v4.json"
ALLOWED_HOSTS = ("vbpl.vn",)
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def allowed_official_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and bool(host)
        and any(host == item or host.endswith("." + item) for item in ALLOWED_HOSTS)
    )


def _as_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def propose_quarantine_disposition(
    row: Mapping[str, Any], *, legal_as_of: date
) -> dict[str, Any]:
    """Return a conservative review proposal; never return legal approval."""

    status = str(row.get("status_observed") or row.get("status") or "").strip().casefold()
    included = bool(
        row.get("scope_included_observed")
        if "scope_included_observed" in row
        else row.get("scope_included")
    )
    effective = _as_date(row.get("effective_date"))
    expired = _as_date(row.get("expired_date"))

    if status == "staging":
        candidate = "quarantined"
        reason = "staging_record"
    elif effective is not None and effective > legal_as_of:
        candidate = "quarantined"
        reason = "future_effective"
    elif status in {"expired", "repealed", "replaced", "superseded", "suspended", "historical", "archived", "inactive"} or (
        expired is not None and expired <= legal_as_of
    ):
        candidate = "historical_only"
        reason = "expired_and_in_scope" if included else "expired_and_outside_scope"
    else:
        candidate = "quarantined"
        reason = "unresolved_status_or_effectivity"

    return {
        "provisional_candidate_state": candidate,
        "review_reason": reason,
        "legal_review_status": "required",
        "approved_for_serving": False,
        "approval_basis": None,
    }


def _fetch_transport(url: str, *, timeout: float) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    if not allowed_official_url(url):
        return {
            "url": url,
            "fetched": False,
            "reason": "official_host_not_allowlisted",
            "observed_at": observed_at,
        }
    request = Request(
        url,
        headers={
            "User-Agent": "ChatBotLegal-retrieval-release-v2-quarantine-audit/1.0",
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
            truncated = len(content) > MAX_RESPONSE_BYTES
            if truncated:
                content = content[:MAX_RESPONSE_BYTES]
            return {
                "url": url,
                "final_url": response.geturl(),
                "status_code": int(response.status),
                "content_type": str(response.headers.get("Content-Type") or ""),
                "bytes_observed": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "truncated": truncated,
                "fetched": True,
                "observed_at": observed_at,
            }
    except HTTPError as exc:
        return {
            "url": url,
            "fetched": False,
            "status_code": int(exc.code),
            "reason": "http_404" if int(exc.code) == 404 else "http_error",
            "observed_at": observed_at,
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "fetched": False,
            "reason": type(exc).__name__,
            "observed_at": observed_at,
        }


def summarize_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    proposals = Counter(str(row.get("provisional_candidate_state") or "") for row in records)
    reasons = Counter(str(row.get("review_reason") or "") for row in records)
    statuses = Counter(
        str((row.get("transport_observation") or {}).get("status_code"))
        for row in records
        if (row.get("transport_observation") or {}).get("status_code") is not None
    )
    fetched_count = sum(bool((row.get("transport_observation") or {}).get("fetched")) for row in records)
    approved_count = sum(bool(row.get("approved_for_serving")) for row in records)
    return {
        "record_count": len(records),
        "fetched_count": fetched_count,
        "approved_for_serving_count": approved_count,
        "provisional_candidate_state_counts": dict(sorted(proposals.items())),
        "review_reason_counts": dict(sorted(reasons.items())),
        "http_status_counts": dict(sorted(statuses.items())),
    }


def _base_record(row: Mapping[str, Any], *, legal_as_of: date) -> dict[str, Any]:
    record = {
        "document_id": int(row["document_id"]),
        "law_number": row.get("law_number"),
        "status_observed": row.get("status_observed"),
        "current_serving_state": row.get("serving_state"),
        "classification_basis_observed": row.get("classification_basis"),
        "scope_included_observed": bool(row.get("scope_included_observed")),
        "effective_date": row.get("effective_date"),
        "expired_date": row.get("expired_date"),
        "article_count": int(row.get("article_count") or 0),
        "chunk_count": int(row.get("chunk_count") or 0),
        "source_url": row.get("source_url"),
        "official_url_allowlisted": allowed_official_url(str(row.get("source_url") or "")),
        **propose_quarantine_disposition(row, legal_as_of=legal_as_of),
    }
    record["transport_observation"] = {
        "fetched": False,
        "reason": "fetch_pending",
    }
    return record


def build_report(
    *,
    input_path: Path,
    output_path: Path,
    fetch: bool = True,
    workers: int = 4,
    timeout: float = 25.0,
    resume: bool = False,
) -> dict[str, Any]:
    inventory = load_json(input_path)
    documents = list(inventory.get("documents") or [])
    if len(documents) != 12_236:
        raise RuntimeError(f"inventory_document_count:{len(documents)}!=12236")
    quarantine = [row for row in documents if row.get("serving_state") == "quarantined"]
    if not quarantine:
        raise RuntimeError("quarantine_document_count:0")
    legal_as_of = _as_date(inventory.get("legal_as_of"))
    if legal_as_of is None:
        raise RuntimeError("legal_as_of_required")

    prior: dict[str, Mapping[str, Any]] = {}
    if resume and output_path.is_file():
        prior_report = load_json(output_path)
        prior = {str(row.get("document_id")): row for row in prior_report.get("records") or []}
    records = [_base_record(row, legal_as_of=legal_as_of) for row in quarantine]
    pending: list[dict[str, Any]] = []
    for record in records:
        old = prior.get(str(record["document_id"]))
        old_transport = (old or {}).get("transport_observation") or {}
        old_reason = str(old_transport.get("reason") or "")
        # A no-fetch report is a plan/checkpoint, not a completed transport
        # observation.  Do not let it suppress the real HTTP audit on resume.
        old_is_completed = bool(old) and old_reason not in {"fetch_disabled", "fetch_pending"}
        if old_is_completed:
            record["transport_observation"] = old_transport
        elif fetch:
            pending.append(record)
        else:
            record["transport_observation"] = {
                "fetched": False,
                "reason": "fetch_disabled",
            }

    if fetch and pending:
        worker_count = max(1, min(int(workers), 16))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(_fetch_transport, str(record.get("source_url") or ""), timeout=timeout): record
                for record in pending
            }
            for index, future in enumerate(as_completed(futures), start=1):
                record = futures[future]
                try:
                    record["transport_observation"] = future.result()
                except Exception as exc:  # defensive: one URL must not abort the audit
                    record["transport_observation"] = {
                        "url": record.get("source_url"),
                        "fetched": False,
                        "reason": type(exc).__name__,
                    }
                if index % 100 == 0:
                    print(json.dumps({"stage": "quarantine_transport_audit", "completed": index, "total": len(pending)}), flush=True)

    records.sort(key=lambda row: int(row["document_id"]))
    report = {
        "schema_version": "legal-retrieval-quarantine-audit-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_file_sha256": file_sha256(input_path),
        "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        "inventory_document_count": len(documents),
        "quarantine_document_count": len(records),
        "legal_as_of": legal_as_of.isoformat(),
        "active_pointer": inventory.get("active_pointer"),
        "fetch_enabled": bool(fetch),
        "transport_workers": max(1, min(int(workers), 16)) if fetch else 0,
        "transport_timeout_seconds": timeout if fetch else None,
        "summary": summarize_records(records),
        "records": records,
        "legal_review_required": True,
        "approved_for_serving": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(
        f"{file_sha256(output_path)}  {output_path.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args(argv)
    report = build_report(
        input_path=args.input.resolve(),
        output_path=args.output.resolve(),
        fetch=not args.no_fetch,
        workers=args.workers,
        timeout=args.timeout,
        resume=args.resume,
    )
    print(json.dumps({"status": "EVIDENCE_ONLY", "quarantine_document_count": report["quarantine_document_count"], "summary": report["summary"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
