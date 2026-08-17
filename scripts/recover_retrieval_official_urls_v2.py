#!/usr/bin/env python3
"""Recover candidate canonical official URLs from the VBPL sitemap.

The command is read-only and produces proposals only.  It never overwrites a
database URL, changes legal status, or treats a sitemap match as verified
content.  Each proposal must pass the source-content audit before it can be
attached to a serving manifest.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.audit_retrieval_source_content_v2 import load_json


DEFAULT_SITEMAP = "https://vbpl.vn/sitemap.xml"
DEFAULT_SNAPSHOT = ROOT / "reports" / "retrieval-release-v2" / "source-snapshot-12236.json"
DEFAULT_AUDIT = ROOT / "reports" / "retrieval-release-v2" / "source-content-audit-12236-v3.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "official-url-recovery-12236-v2.json"
MAX_SITEMAP_BYTES = 20 * 1024 * 1024
USER_AGENT = "ChatBotLegal-retrieval-release-v2-url-recovery/1.0"


def normalize_identity_text(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).casefold().replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def normalize_law_key(value: object) -> str:
    tokens = normalize_identity_text(value).split()
    return " ".join(tokens)


def _url_tokens(url: str) -> list[str]:
    return normalize_identity_text(url).split()


def _sequence_in_url(expected_tokens: list[str], url_tokens: list[str]) -> bool:
    if not expected_tokens:
        return False
    cursor = 0
    for token in expected_tokens:
        try:
            cursor = url_tokens.index(token, cursor) + 1
        except ValueError:
            return False
    return True


def match_sitemap_candidates(
    urls: list[str], *, law_number: object, title: object
) -> list[dict[str, object]]:
    expected_tokens = normalize_law_key(law_number).split()
    title_tokens = {token for token in normalize_identity_text(title).split() if len(token) > 2}
    matches: list[dict[str, object]] = []
    for url in urls:
        tokens = _url_tokens(url)
        law_match = _sequence_in_url(expected_tokens, tokens)
        token_overlap = len(title_tokens & set(tokens)) / len(title_tokens) if title_tokens else 0.0
        score = (100.0 if law_match else 0.0) + token_overlap * 20.0
        matches.append({
            "url": url,
            "law_number_match": law_match,
            "title_overlap": round(token_overlap, 4),
            "score": round(score, 4),
        })
    return sorted(matches, key=lambda item: (-float(item["score"]), str(item["url"])))


def parse_sitemap_locations(xml: str) -> list[str]:
    root = ET.fromstring(str(xml or ""))
    locations: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text:
            value = element.text.strip()
            parsed = urlparse(value)
            if parsed.scheme == "https" and (parsed.hostname or "").lower().endswith("vbpl.vn"):
                locations.append(value)
    return sorted(set(locations))


def _fetch_text(url: str, *, timeout: float) -> tuple[int, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,*/*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_SITEMAP_BYTES + 1)
            if len(data) > MAX_SITEMAP_BYTES:
                raise RuntimeError("sitemap_size_limit_exceeded")
            return int(response.status), data.decode("utf-8", errors="replace")
    except HTTPError as exc:
        return int(exc.code), ""
    except (URLError, TimeoutError, OSError):
        return 0, ""


def collect_sitemap_urls(*, sitemap_url: str, workers: int = 4, timeout: float = 30.0) -> dict[str, object]:
    index_status, index_xml = _fetch_text(sitemap_url, timeout=timeout)
    if index_status != 200:
        raise RuntimeError(f"sitemap_index_http_{index_status}")
    index_locations = parse_sitemap_locations(index_xml)
    # A sitemap index contains child sitemap URLs; a urlset is also accepted.
    root = ET.fromstring(index_xml)
    child_locations = [
        element.text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text and "/sitemap/" in element.text
    ]
    if not child_locations:
        return {"sitemap_index_status": index_status, "sitemap_count": 1, "sitemap_urls": index_locations}
    all_urls: set[str] = set()
    statuses: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 8))) as pool:
        futures = {pool.submit(_fetch_text, url, timeout=timeout): url for url in sorted(set(child_locations))}
        for future in as_completed(futures):
            url = futures[future]
            try:
                status, xml = future.result()
            except Exception:
                status, xml = 0, ""
            statuses[str(status)] += 1
            if status == 200:
                try:
                    all_urls.update(parse_sitemap_locations(xml))
                except ET.ParseError:
                    statuses["parse_error"] += 1
    return {
        "sitemap_index_status": index_status,
        "sitemap_count": len(set(child_locations)) + 1,
        "sitemap_child_status_counts": dict(sorted(statuses.items())),
        "sitemap_urls": sorted(all_urls),
    }


def build_report(*, snapshot_path: Path, audit_path: Path, output_path: Path, sitemap_url: str = DEFAULT_SITEMAP, workers: int = 4, timeout: float = 30.0) -> dict[str, object]:
    snapshot = load_json(snapshot_path)
    audit = load_json(audit_path) if audit_path.is_file() else {"records": []}
    documents = list(snapshot.get("documents") or [])
    urls_result = collect_sitemap_urls(sitemap_url=sitemap_url, workers=workers, timeout=timeout)
    sitemap_urls = list(urls_result.get("sitemap_urls") or [])
    records: list[dict[str, object]] = []
    for document in documents:
        candidates = match_sitemap_candidates(
            sitemap_urls,
            law_number=document.get("law_number"),
            title=document.get("title"),
        )
        law_matches = [row for row in candidates if bool(row["law_number_match"])]
        best = law_matches[0] if law_matches else None
        second_score = float(law_matches[1]["score"]) if len(law_matches) > 1 else None
        old_url = str(document.get("source_url") or "")
        audit_row = next((row for row in audit.get("records") or [] if int(row.get("document_id") or 0) == int(document["document_id"])), {})
        transport_status = str((audit_row.get("transport_observation") or {}).get("transport_status") or "")
        eligible = bool(best and float(best["score"]) >= 100.0 and (second_score is None or float(best["score"]) > second_score))
        records.append({
            "document_id": int(document["document_id"]),
            "law_number": document.get("law_number"),
            "title": document.get("title"),
            "old_source_url": old_url,
            "old_transport_status": transport_status,
            "candidate_canonical_url": best.get("url") if eligible and best else None,
            "candidate_score": best.get("score") if best else None,
            "candidate_title_overlap": best.get("title_overlap") if best else None,
            "candidate_law_number_match": bool(best and best.get("law_number_match")),
            "candidate_count_with_law_match": len(law_matches),
            "recovery_status": "candidate_needs_content_verification" if eligible else "not_found_or_ambiguous",
            "approved_for_db_update": False,
        })
    summary = Counter(str(row["recovery_status"]) for row in records)
    report: dict[str, object] = {
        "schema_version": "legal-retrieval-official-url-recovery-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_file_sha256": file_sha256(snapshot_path),
        "audit_file_sha256": file_sha256(audit_path) if audit_path.is_file() else None,
        "sitemap_url": sitemap_url,
        "sitemap_count": urls_result.get("sitemap_count"),
        "sitemap_url_count": len(sitemap_urls),
        "sitemap_child_status_counts": urls_result.get("sitemap_child_status_counts", {}),
        "document_count": len(documents),
        "summary": {"recovery_status_counts": dict(sorted(summary.items()))},
        "records": sorted(records, key=lambda row: int(row["document_id"])),
        "legal_review_required": True,
        "approved_for_db_update": False,
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
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    report = build_report(snapshot_path=args.snapshot.resolve(), audit_path=args.audit.resolve(), output_path=args.output.resolve(), sitemap_url=args.sitemap_url, workers=args.workers, timeout=args.timeout)
    print(json.dumps({"status": "EVIDENCE_ONLY", "document_count": report["document_count"], "sitemap_url_count": report["sitemap_url_count"], "summary": report["summary"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
