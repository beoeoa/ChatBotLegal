#!/usr/bin/env python3
"""Verify source URL transport and page identity for the complete inventory.

This is a read-only evidence job.  It never changes PostgreSQL, Chroma,
document status, serving manifests or active pointers.  HTTP 200 is only a
transport observation; a page is verified only when its content contains the
expected legal identity and substantive legal markers.  Soft-404s, loading
shells and identity mismatches remain unverified.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import html as html_lib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # local retrieval venv intentionally stays small
    from html.parser import HTMLParser

    class _FallbackHTMLParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.title_parts: list[str] = []
            self._skip_depth = 0
            self._in_title = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.casefold() in {"script", "style", "noscript", "svg"}:
                self._skip_depth += 1
            elif tag.casefold() == "title" and not self._skip_depth:
                self._in_title = True

        def handle_endtag(self, tag: str) -> None:
            if tag.casefold() in {"script", "style", "noscript", "svg"} and self._skip_depth:
                self._skip_depth -= 1
            elif tag.casefold() == "title":
                self._in_title = False

        def handle_data(self, data: str) -> None:
            if self._skip_depth:
                return
            text = " ".join(str(data).split())
            if text:
                self.parts.append(text)
                if self._in_title:
                    self.title_parts.append(text)

    class BeautifulSoup:  # type: ignore[no-redef]
        def __init__(self, html: str, parser: str) -> None:
            self._parser = _FallbackHTMLParser()
            self._parser.feed(str(html or ""))
            self.title = None

        def __call__(self, tags: list[str]):
            return []

        @property
        def stripped_strings(self):
            return iter(self._parser.parts)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES
from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_INPUT = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_SNAPSHOT = ROOT / "reports" / "retrieval-release-v2" / "source-snapshot-12236-v4.json"
DEFAULT_PRIOR_QUARANTINE = ROOT / "reports" / "retrieval-release-v2" / "quarantine-audit-v4.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "source-content-audit-12236-v4.json"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
SOFT_404_MARKERS = (
    "page not found",
    "404 not found",
    "khong tim thay van ban",
    "trang ban yeu cau khong ton tai",
    "van ban khong ton tai",
    "du lieu khong ton tai",
    "khong co ket qua",
)
LEGAL_MARKERS = (
    "dieu ",
    "chuong ",
    "can cu",
    "pham vi dieu chinh",
    "quy dinh",
    "hieu luc",
)
STOPWORDS = {
    "va", "cua", "cho", "ve", "theo", "tu", "den", "mot", "nhung", "cac",
    "quy", "dinh", "nghi", "luat", "thong", "tu", "so", "co", "quan", "ban",
    "hanh", "ngay", "nam", "tai", "bao", "bo", "voi", "duoc", "trong", "phap",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def normalize_identity_text(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).casefold().replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _tokens(value: Any) -> set[str]:
    return {token for token in normalize_identity_text(value).split() if len(token) > 1 and token not in STOPWORDS}


def _contains_identity(haystack: str, expected: str) -> bool:
    expected_tokens = normalize_identity_text(expected).split()
    if not expected_tokens:
        return False
    wanted = " ".join(expected_tokens)
    if wanted in haystack:
        return True
    # Some pages remove punctuation around law-number components.  Requiring
    # all normalized tokens avoids treating a shared year alone as a match.
    return len(expected_tokens) >= 2 and all(token in haystack.split() for token in expected_tokens)


def _overlap(expected: Any, actual: str) -> float | None:
    wanted = _tokens(expected)
    if not wanted:
        return None
    return len(wanted & _tokens(actual)) / len(wanted)


def classify_html_identity(
    html: str,
    *,
    law_number: Any,
    title: Any,
    issuing_agency: Any,
) -> dict[str, Any]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    visible_text = " ".join(soup.stripped_strings)
    normalized = normalize_identity_text(visible_text)
    page_title = " ".join(soup.title.stripped_strings) if soup.title else ""
    law_match = _contains_identity(normalized, law_number)
    title_overlap = _overlap(title, visible_text)
    agency_overlap = _overlap(issuing_agency, visible_text)
    # A short one-article official page can still be substantive.  The
    # separate empty-page gate remains strict enough to reject shells.
    legal_content = len(visible_text) >= 80 and any(marker in normalized for marker in LEGAL_MARKERS)
    soft_404 = any(marker in normalized for marker in SOFT_404_MARKERS)
    content_empty = len(visible_text.strip()) < 50
    identity_verified = bool(
        not soft_404
        and not content_empty
        and legal_content
        and law_match
        and (title_overlap is None or title_overlap >= 0.25)
        and (agency_overlap is None or agency_overlap >= 0.25)
    )
    if soft_404 or content_empty or not legal_content:
        verification_status = "rejected"
    elif identity_verified:
        verification_status = "verified"
    else:
        verification_status = "needs_review"
    reasons: list[str] = []
    if soft_404:
        reasons.append("soft_404")
    if content_empty:
        reasons.append("content_empty_or_too_short")
    if not legal_content:
        reasons.append("legal_content_markers_missing")
    if not law_match:
        reasons.append("law_number_mismatch")
    if title_overlap is not None and title_overlap < 0.25:
        reasons.append("title_identity_mismatch")
    if agency_overlap is not None and agency_overlap < 0.25:
        reasons.append("issuing_agency_identity_mismatch")
    return {
        "verification_status": verification_status,
        "identity_verified": identity_verified,
        "law_number_match": law_match,
        "title_overlap": title_overlap,
        "issuing_agency_overlap": agency_overlap,
        "legal_content_detected": legal_content,
        "soft_404": soft_404,
        "content_empty_or_too_short": content_empty,
        "visible_characters": len(visible_text),
        "page_title": page_title[:500],
        "content_reasons": reasons,
    }


def _allowed_official_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in OFFICIAL_HOST_SUFFIXES
    )


def _fetch(url: str, *, timeout: float) -> tuple[dict[str, Any], bytes]:
    observed_at = datetime.now(timezone.utc).isoformat()
    if not _allowed_official_url(url):
        return ({"transport_status": "official_host_not_allowlisted", "fetched": False, "observed_at": observed_at}, b"")
    request = Request(
        url,
        headers={
            "User-Agent": "ChatBotLegal-retrieval-release-v2-source-audit/1.0",
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            truncated = len(body) > MAX_RESPONSE_BYTES
            if truncated:
                body = body[:MAX_RESPONSE_BYTES]
            status = int(response.status)
            return (
                {
                    "transport_status": f"http_{status}",
                    "status_code": status,
                    "fetched": True,
                    "final_url": response.geturl(),
                    "content_type": str(response.headers.get("Content-Type") or ""),
                    "bytes_observed": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "truncated": truncated,
                    "observed_at": observed_at,
                },
                body,
            )
    except HTTPError as exc:
        return ({"transport_status": f"http_{int(exc.code)}", "status_code": int(exc.code), "fetched": False, "observed_at": observed_at}, b"")
    except (URLError, TimeoutError, OSError) as exc:
        return ({"transport_status": type(exc).__name__, "fetched": False, "observed_at": observed_at}, b"")


def summarize_verification(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    def transport_status(row: Mapping[str, Any]) -> str:
        nested = row.get("transport_observation")
        if isinstance(nested, Mapping) and nested.get("transport_status"):
            return str(nested.get("transport_status"))
        return str(row.get("transport_status") or "")

    transport = Counter(transport_status(row) for row in records)
    verification = Counter(str(row.get("verification_status") or "") for row in records)
    return {
        "record_count": len(records),
        "transport_status_counts": dict(sorted(transport.items())),
        "verification_status_counts": dict(sorted(verification.items())),
        "verified_count": int(verification["verified"]),
        "needs_review_count": int(verification["needs_review"]),
        "rejected_count": int(verification["rejected"]),
        "http_200_not_verified_count": sum(
            transport_status(row) == "http_200" and row.get("verification_status") != "verified"
            for row in records
        ),
    }


def _same_source_url(left: Any, right: Any) -> bool:
    """Return true only when checkpoint evidence belongs to this exact URL.

    A document ID is stable while its source URL can be corrected. Reusing a
    transport result for the old URL would make a post-correction audit report
    stale (for example, carrying an old 404 into a newly assigned canonical
    URL). URL changes therefore invalidate both resume and prior-quarantine
    checkpoints.
    """

    return str(left or "").strip() == str(right or "").strip()


def _prior_transport(
    prior: Mapping[str, Any], document_id: int, source_url: Any
) -> dict[str, Any] | None:
    for row in prior.get("records") or []:
        if (
            int(row.get("document_id") or 0) == document_id
            and _same_source_url(row.get("source_url"), source_url)
        ):
            transport = row.get("transport_observation")
            if isinstance(transport, dict) and transport.get("transport_status"):
                return dict(transport)
            if isinstance(transport, dict) and transport.get("status_code") is not None:
                code = int(transport["status_code"])
                return {**transport, "transport_status": f"http_{code}"}
    return None


def build_report(
    *,
    inventory_path: Path,
    snapshot_path: Path,
    output_path: Path,
    prior_quarantine_path: Path | None = None,
    fetch: bool = True,
    workers: int = 4,
    timeout: float = 25.0,
    resume: bool = False,
) -> dict[str, Any]:
    inventory = load_json(inventory_path)
    snapshot = load_json(snapshot_path)
    documents = list(inventory.get("documents") or [])
    snapshot_documents = list(snapshot.get("documents") or [])
    if len(documents) != 12_236 or len(snapshot_documents) != 12_236:
        raise RuntimeError(f"inventory_or_snapshot_count:{len(documents)}:{len(snapshot_documents)}")
    snapshot_by_id = {int(row["document_id"]): row for row in snapshot_documents}
    if len(snapshot_by_id) != 12_236:
        raise RuntimeError("snapshot_document_id_partition_invalid")
    prior: dict[str, Any] = load_json(prior_quarantine_path) if prior_quarantine_path and prior_quarantine_path.is_file() else {}
    prior_resume = load_json(output_path) if resume and output_path.is_file() else {}
    records: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for item in documents:
        document_id = int(item["document_id"])
        source = snapshot_by_id.get(document_id) or {}
        record = {
            "document_id": document_id,
            "law_number": source.get("law_number"),
            "title": source.get("title"),
            "issuing_agency": source.get("issuing_agency"),
            "status_observed": source.get("status"),
            "serving_state_observed": item.get("serving_state"),
            "scope_included_observed": item.get("scope_included_observed"),
            "effective_date": source.get("effective_date"),
            "expired_date": source.get("expired_date"),
            "source_url": source.get("source_url"),
            "official_source_url_allowlisted": _allowed_official_url(str(source.get("source_url") or "")),
            "legal_review_status": "required",
            "approved_for_serving": False,
        }
        old_resume = next(
            (
                row
                for row in prior_resume.get("records") or []
                if int(row.get("document_id") or 0) == document_id
                and _same_source_url(row.get("source_url"), record["source_url"])
            ),
            None,
        )
        old_transport = (old_resume or {}).get("transport_observation") or {}
        old_transport_status = str(old_transport.get("transport_status") or "")
        old_checkpoint_completed = bool(
            old_resume
            and old_transport_status
            and old_transport_status != "fetch_disabled"
            and old_resume.get("verification_status") in {"verified", "needs_review", "rejected"}
        )
        if old_checkpoint_completed:
            record.update({key: old_resume.get(key) for key in ("transport_observation", "verification_status", "identity_verified", "identity_evidence", "source_content_evidence") if key in old_resume})
            records.append(record)
            continue
        seeded = _prior_transport(prior, document_id, record["source_url"])
        # A prior non-200 observation is authoritative for transport at the
        # same audit time and avoids re-hitting known dead links.  HTTP 200 is
        # deliberately fetched again so page identity can be parsed.
        if seeded and seeded.get("status_code") != 200:
            record["transport_observation"] = seeded
            record["verification_status"] = "rejected"
            record["identity_verified"] = False
            record["identity_evidence"] = {"verification_status": "rejected", "content_reasons": ["transport_not_http_200"]}
            record["source_content_evidence"] = {"verified": False, "reason": "transport_not_http_200"}
            records.append(record)
        elif fetch:
            pending.append(record)
        else:
            record["transport_observation"] = {"transport_status": "fetch_disabled", "fetched": False}
            record["verification_status"] = "needs_review"
            record["identity_verified"] = False
            record["identity_evidence"] = {"verification_status": "needs_review", "content_reasons": ["fetch_disabled"]}
            record["source_content_evidence"] = {"verified": False, "reason": "fetch_disabled"}
            records.append(record)

    if fetch and pending:
        worker_count = max(1, min(int(workers), 16))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(_fetch, str(row.get("source_url") or ""), timeout=timeout): row
                for row in pending
            }
            for index, future in enumerate(as_completed(futures), start=1):
                record = futures[future]
                try:
                    transport, body = future.result()
                except Exception as exc:
                    transport, body = ({"transport_status": type(exc).__name__, "fetched": False}, b"")
                record["transport_observation"] = transport
                if transport.get("status_code") == 200 and body:
                    content_type = str(transport.get("content_type") or "").casefold()
                    if "html" in content_type or not content_type:
                        identity = classify_html_identity(
                            body.decode("utf-8", errors="replace"),
                            law_number=record.get("law_number"),
                            title=record.get("title"),
                            issuing_agency=record.get("issuing_agency"),
                        )
                    else:
                        identity = {"verification_status": "needs_review", "identity_verified": False, "content_reasons": ["unsupported_content_type"], "visible_characters": 0}
                    record["verification_status"] = identity["verification_status"]
                    record["identity_verified"] = bool(identity["identity_verified"])
                    record["identity_evidence"] = identity
                    record["source_content_evidence"] = {
                        "verified": bool(identity["identity_verified"]),
                        "content_sha256": transport.get("body_sha256"),
                        "reason": "parsed_html_identity" if identity["identity_verified"] else "identity_or_content_gate_failed",
                    }
                else:
                    record["verification_status"] = "rejected"
                    record["identity_verified"] = False
                    record["identity_evidence"] = {"verification_status": "rejected", "content_reasons": ["transport_not_http_200"]}
                    record["source_content_evidence"] = {"verified": False, "reason": "transport_not_http_200"}
                records.append(record)
                if index % 100 == 0:
                    print(json.dumps({"stage": "source_content_audit", "completed": index, "total": len(pending)}), flush=True)

    records.sort(key=lambda row: int(row["document_id"]))
    if len(records) != 12_236:
        raise RuntimeError(f"audit_record_count:{len(records)}!=12236")
    report = {
        "schema_version": "legal-retrieval-source-content-audit-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_file_sha256": file_sha256(inventory_path),
        "source_snapshot_file_sha256": file_sha256(snapshot_path),
        "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        "inventory_document_count": 12_236,
        "legal_as_of": inventory.get("legal_as_of"),
        "active_pointer": inventory.get("active_pointer"),
        "fetch_enabled": bool(fetch),
        "transport_workers": max(1, min(int(workers), 16)) if fetch else 0,
        "transport_timeout_seconds": timeout if fetch else None,
        "summary": summarize_verification(records),
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
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(f"{file_sha256(output_path)}  {output_path.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--prior-quarantine", type=Path, default=DEFAULT_PRIOR_QUARANTINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args(argv)
    report = build_report(
        inventory_path=args.inventory.resolve(),
        snapshot_path=args.snapshot.resolve(),
        output_path=args.output.resolve(),
        prior_quarantine_path=args.prior_quarantine.resolve(),
        fetch=not args.no_fetch,
        workers=args.workers,
        timeout=args.timeout,
        resume=args.resume,
    )
    print(json.dumps({"status": "EVIDENCE_ONLY", "summary": report["summary"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
