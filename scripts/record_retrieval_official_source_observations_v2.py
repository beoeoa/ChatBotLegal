#!/usr/bin/env python3
"""Fetch and checksum official source pages for the V2 source-gap queue.

Only transport evidence is recorded.  The command never changes a legal
document, source-gap decision, Golden case or serving manifest, and it never
turns an HTTP 200 into legal approval.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.reconcile_retrieval_source_gaps_v2 import normalize_law


DEFAULT_GAPS = ROOT / "reports" / "retrieval-release-v2" / "source-gap-reconciliation-full-inventory-v2.json"
DEFAULT_INPUT = ROOT / "reports" / "retrieval-release-v2" / "official-source-observation-input-v2.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "official-source-observations-v2.json"
ALLOWED_HOSTS = ("vbpl.vn", "baohiemxahoi.gov.vn")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == item or host.endswith("." + item) for item in ALLOWED_HOSTS)


def _fetch(url: str) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    if not _allowed(url):
        return {"url": url, "fetched": False, "reason": "official_host_not_allowlisted", "observed_at": observed_at}
    request = Request(url, headers={"User-Agent": "ChatBotLegal-retrieval-release-v2/1.0", "Accept": "text/html,application/pdf,*/*"})
    try:
        with urlopen(request, timeout=25) as response:
            content = response.read(20 * 1024 * 1024 + 1)
            truncated = len(content) > 20 * 1024 * 1024
            if truncated:
                content = content[:20 * 1024 * 1024]
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
        reason = "http_404" if int(exc.code) == 404 else "http_error"
        return {"url": url, "fetched": False, "status_code": int(exc.code), "reason": reason, "observed_at": observed_at}
    except (URLError, TimeoutError, OSError) as exc:
        return {"url": url, "fetched": False, "reason": type(exc).__name__, "observed_at": observed_at}


def build(*, gaps_path: Path, input_path: Path, output: Path, fetch: bool = True) -> dict[str, Any]:
    gaps = _load(gaps_path)
    inputs = _load(input_path)
    observations = {normalize_law(row.get("law_number")): row for row in inputs.get("observations") or []}
    records: list[dict[str, Any]] = []
    transport_cache: dict[str, dict[str, Any]] = {}
    for reference in gaps.get("references") or []:
        law_key = normalize_law(reference.get("law_number"))
        source = observations.get(law_key)
        source_url = str(source.get("official_source_url")) if source else ""
        if source_url and source_url not in transport_cache:
            transport_cache[source_url] = _fetch(source_url) if fetch else {"url": source_url, "fetched": False, "reason": "fetch_disabled", "observed_at": datetime.now(timezone.utc).isoformat()}
        transport = transport_cache.get(source_url) or {"fetched": False, "reason": "no_official_observation_input"}
        records.append({
            "case_id": reference.get("case_id"),
            "law_number": reference.get("law_number"),
            "article": reference.get("article"),
            "official_source_url": source.get("official_source_url") if source else None,
            "transport_observation": transport,
            "legal_review_status": "required",
            "approved_for_import": False,
        })
    report = {
        "schema_version": "legal-retrieval-official-source-observations-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gap_manifest_file_sha256": file_sha256(gaps_path),
        "observation_input_file_sha256": file_sha256(input_path),
        "reference_count": len(records),
        "references_with_official_url": sum(bool(row.get("official_source_url")) for row in records),
        "references_fetched": sum(bool((row.get("transport_observation") or {}).get("fetched")) for row in records),
        "legal_review_required": True,
        "approved_for_import": False,
        "records": records,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{file_sha256(output)}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-fetch", action="store_true", help="Record allowlisted URLs without direct HTTP; no content checksum is claimed.")
    args = parser.parse_args(argv)
    report = build(gaps_path=args.gaps.resolve(), input_path=args.input.resolve(), output=args.output.resolve(), fetch=not args.no_fetch)
    print(json.dumps({"status": "EVIDENCE_ONLY", "reference_count": report["reference_count"], "references_fetched": report["references_fetched"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
