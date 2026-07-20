# -*- coding: utf-8 -*-
"""Crawl/match official forms only for the priority procedure catalog.

Priority source of truth:
  notebook_data/forms/priority_200_forms.json

This crawler does NOT invent forms. It:
1) Loads the priority procedure/form catalog.
2) Matches against local official candidates/index/classified inventory first.
3) Optionally probes preferred source URLs for missing items (lightweight HEAD/GET).
4) Writes status for every procedure:
   - missing
   - candidate_found
   - official_approved
   - broken_source

Output:
  notebook_data/forms/priority_200_forms_status.json
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "notebook_data" / "forms" / "priority_200_forms.json"
STATUS_PATH = ROOT / "notebook_data" / "forms" / "priority_200_forms_status.json"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "priority_official"

SOURCE_POOLS = [
    ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json",
    ROOT / "notebook_data" / "forms" / "haiphong_official_forms_catalog.json",
    ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json",
    ROOT / "notebook_data" / "forms" / "priority_official_forms.json",
    ROOT / "notebook_data" / "forms" / "forms_manifest.json",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def fold(text: str) -> str:
    value = unicodedata.normalize("NFD", text or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def extract_forms(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("forms", "records", "items", "candidates", "procedures"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def form_title_of(row: dict[str, Any]) -> str:
    for key in (
        "form_title",
        "detected_form_name",
        "name",
        "title",
        "form_name",
        "procedure_name",
    ):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def local_path_of(row: dict[str, Any]) -> str:
    for key in (
        "local_path",
        "priority_path",
        "file_path",
        "source_package_path",
        "path",
    ):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def source_url_of(row: dict[str, Any]) -> str:
    for key in ("source_download_url", "download_url", "source_url", "source_page_url", "page_url"):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def review_status_of(row: dict[str, Any]) -> str:
    return str(row.get("review_status") or ("approved" if row.get("is_approved") else "")).strip().lower()


def official_level_of(row: dict[str, Any]) -> str:
    level = str(row.get("official_level") or row.get("catalog_status") or "").strip().lower()
    if row.get("is_canonical") or "official" in level or row.get("publisher"):
        return "official"
    return level or "unknown"


def has_real_file(row: dict[str, Any]) -> bool:
    path = local_path_of(row)
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = ROOT / path
        if p.exists() and p.is_file() and p.stat().st_size > 100:
            return True
    if row.get("has_official_file") is True:
        return True
    if row.get("size_bytes") and int(row.get("size_bytes") or 0) > 100:
        # path may be relative under uploads
        path2 = local_path_of(row)
        if path2:
            p2 = ROOT / path2
            if p2.exists() and p2.stat().st_size > 100:
                return True
    return False



def token_overlap(a: str, b: str) -> float:
    ta = set(fold(a).split())
    tb = set(fold(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta))


def score_match(procedure: dict[str, Any], expected_form: str, candidate: dict[str, Any]) -> float:
    title = fold(form_title_of(candidate))
    if not title:
        return 0.0
    expected = fold(expected_form)
    proc_name = fold(str(procedure.get("procedure_name") or ""))
    keywords = [fold(k) for k in (procedure.get("keywords") or []) if fold(k)]
    score = 0.0
    overlap = token_overlap(expected, title)
    if expected and expected in title:
        score += 6.0
    elif title and title in expected and len(title) >= 12:
        score += 4.5
    else:
        score += overlap * 4.0
    # Hard gate against weak lexical matches.
    if overlap < 0.45 and expected not in title and title not in expected:
        return 0.0
    for kw in keywords:
        if kw and kw in title:
            score += 0.6
    if proc_name:
        score += token_overlap(proc_name, title) * 0.8
    cand_domain = fold(str(candidate.get("domain") or candidate.get("suggested_domain") or candidate.get("domain_slug") or ""))
    proc_domain = fold(str(procedure.get("domain") or ""))
    if cand_domain and proc_domain and (cand_domain == proc_domain or cand_domain in proc_domain or proc_domain in cand_domain):
        score += 0.4
    if review_status_of(candidate) in {"approved", "admin_curated_source"}:
        score += 1.0
    if official_level_of(candidate) == "official":
        score += 0.5
    if has_real_file(candidate):
        score += 0.8
    return score

def load_candidate_pool() -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    for path in SOURCE_POOLS:
        payload = load_json(path)
        rows = extract_forms(payload)
        for row in rows:
            enriched = dict(row)
            enriched["_pool_source"] = path.name
            pool.append(enriched)
    return pool


def build_search_urls(procedure: dict[str, Any], expected_form: str) -> list[dict[str, str]]:
    q = expected_form or procedure.get("procedure_name") or procedure.get("procedure_id") or ""
    q = str(q).strip()
    if not q:
        return []
    encoded = quote_plus(q)
    return [
        {
            "source": "Cổng Dịch vụ công Quốc gia",
            "url": f"https://dichvucong.gov.vn/p/home/dvc-tthc-thu-tuc-hanh-chinh.html?keyword={encoded}",
        },
        {
            "source": "Cổng thông tin UBND Hải Phòng",
            "url": f"https://haiphong.gov.vn/tim-kiem?q={encoded}",
        },
        {
            "source": "Website UBND phường/xã Hải Phòng",
            "url": f"https://phulien.haiphong.gov.vn/tim-kiem?q={encoded}",
        },
    ]


def probe_urls(urls: list[dict[str, str]], timeout: float = 8.0) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not urls:
        return results
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for item in urls:
            url = item["url"]
            source = item["source"]
            status = "broken_source"
            http_status = None
            detail = ""
            try:
                resp = client.head(url)
                http_status = resp.status_code
                if resp.status_code >= 400 or resp.status_code in {405, 403}:
                    resp = client.get(url)
                    http_status = resp.status_code
                if 200 <= (http_status or 0) < 400:
                    status = "candidate_found"
                    detail = "source_reachable"
                else:
                    status = "broken_source"
                    detail = f"http_{http_status}"
            except Exception as exc:  # noqa: BLE001
                status = "broken_source"
                detail = f"error:{type(exc).__name__}"
            results.append(
                {
                    "source": source,
                    "url": url,
                    "status": status,
                    "http_status": http_status,
                    "detail": detail,
                }
            )
    return results


def classify_procedure_status(
    procedure: dict[str, Any],
    matches: list[dict[str, Any]],
    probe_results: list[dict[str, Any]],
) -> str:
    if any(m.get("status") == "official_approved" for m in matches):
        return "official_approved"
    if any(m.get("status") == "candidate_found" for m in matches):
        return "candidate_found"
    if matches:
        # matched metadata but no file / not approved
        return "candidate_found"
    if probe_results and all(p.get("status") == "broken_source" for p in probe_results):
        return "broken_source"
    if probe_results and any(p.get("status") == "candidate_found" for p in probe_results):
        return "candidate_found"
    return "missing"



def match_procedure(procedure: dict[str, Any], pool: list[dict[str, Any]], min_score: float = 4.5) -> list[dict[str, Any]]:
    expected_forms = list(procedure.get("expected_form_names") or []) or [procedure.get("procedure_name") or procedure.get("procedure_id")]
    found: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for expected in expected_forms:
        best = None
        best_score = 0.0
        for cand in pool:
            score = score_match(procedure, str(expected), cand)
            if score > best_score:
                best_score = score
                best = cand
        if not best or best_score < min_score:
            continue
        title = form_title_of(best)
        overlap = token_overlap(str(expected), title)
        if overlap < 0.55 and fold(str(expected)) not in fold(title) and fold(title) not in fold(str(expected)):
            continue
        cand_id = str(best.get("id") or best.get("sha256") or title or id(best))
        used_ids.add(cand_id)
        review = review_status_of(best)
        has_file = has_real_file(best)
        strong_title = overlap >= 0.7 or fold(str(expected)) in fold(title) or fold(title) in fold(str(expected))
        if (
            review in {"approved", "admin_curated_source"}
            and has_file
            and official_level_of(best) in {"official", "unknown"}
            and strong_title
            and best_score >= 6.0
        ):
            status = "official_approved"
        else:
            status = "candidate_found"
        found.append(
            {
                "expected_form_name": expected,
                "status": status,
                "match_score": round(best_score, 3),
                "title_overlap": round(overlap, 3),
                "matched_form_title": title,
                "matched_id": cand_id,
                "review_status": review or None,
                "official_level": official_level_of(best),
                "has_real_file": has_file,
                "source_url": source_url_of(best),
                "local_path": local_path_of(best),
                "pool_source": best.get("_pool_source"),
            }
        )
    return found

def run(probe_missing: bool = True, max_probe_per_procedure: int = 1) -> dict[str, Any]:
    catalog = load_json(CATALOG_PATH)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("procedures"), list):
        raise SystemExit(f"Invalid catalog schema at {CATALOG_PATH}. Expected procedures[].")

    procedures = [p for p in catalog["procedures"] if isinstance(p, dict)]
    pool = load_candidate_pool()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()

    for procedure in procedures:
        matches = match_procedure(procedure, pool)
        probe_results: list[dict[str, Any]] = []
        status = classify_procedure_status(procedure, matches, probe_results)
        if probe_missing and status in {"missing", "broken_source"}:
            # probe only first expected form to stay light
            expected = (procedure.get("expected_form_names") or [procedure.get("procedure_name")])[:max_probe_per_procedure]
            urls: list[dict[str, str]] = []
            for exp in expected:
                urls.extend(build_search_urls(procedure, str(exp)))
            # unique urls
            seen_u = set()
            uniq = []
            for u in urls:
                if u["url"] in seen_u:
                    continue
                seen_u.add(u["url"])
                uniq.append(u)
            probe_results = probe_urls(uniq[:3])
            status = classify_procedure_status(procedure, matches, probe_results)

        status_counter[status] += 1
        domain_counter[str(procedure.get("domain") or "unknown")] += 1
        rows.append(
            {
                "procedure_id": procedure.get("procedure_id"),
                "procedure_name": procedure.get("procedure_name"),
                "domain": procedure.get("domain"),
                "department": procedure.get("department"),
                "expected_form_names": procedure.get("expected_form_names") or [],
                "preferred_sources": procedure.get("preferred_sources") or catalog.get("preferred_sources_default") or [],
                "official_level_required": procedure.get("official_level_required") or "official",
                "status": status,
                "matched_forms": matches,
                "matched_form_count": len(matches),
                "expected_form_count": len(procedure.get("expected_form_names") or []),
                "source_probes": probe_results,
                "has_official_approved_file": any(m.get("status") == "official_approved" for m in matches),
                "has_candidate": any(m.get("status") == "candidate_found" for m in matches) or status == "candidate_found",
                "priority_rank": procedure.get("priority_rank"),
            }
        )

    # Convenience groups
    by_status = {
        "official_approved": [r["procedure_id"] for r in rows if r["status"] == "official_approved"],
        "candidate_found": [r["procedure_id"] for r in rows if r["status"] == "candidate_found"],
        "missing": [r["procedure_id"] for r in rows if r["status"] == "missing"],
        "broken_source": [r["procedure_id"] for r in rows if r["status"] == "broken_source"],
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_path": str(CATALOG_PATH.relative_to(ROOT)).replace("\\", "/"),
        "status_path": str(STATUS_PATH.relative_to(ROOT)).replace("\\", "/"),
        "crawl_scope": "priority_200_forms_only",
        "pool_sources": [str(p.relative_to(ROOT)).replace("\\", "/") for p in SOURCE_POOLS if p.exists()],
        "pool_size": len(pool),
        "summary": {
            "procedure_count": len(rows),
            "status_counts": dict(status_counter),
            "domain_counts": dict(domain_counter),
            "with_real_or_candidate_form": status_counter.get("official_approved", 0) + status_counter.get("candidate_found", 0),
            "still_missing": status_counter.get("missing", 0),
            "broken_source": status_counter.get("broken_source", 0),
            "official_approved": status_counter.get("official_approved", 0),
            "candidate_found": status_counter.get("candidate_found", 0),
        },
        "by_status": by_status,
        "procedures": rows,
        "notes": [
            "Status official_approved requires matched official form + approved review + real file.",
            "Status candidate_found means local candidate/metadata matched or preferred source reachable.",
            "Status missing means no local match and no usable source probe hit.",
            "Status broken_source means preferred source endpoints failed for that procedure.",
            "Crawler intentionally prioritizes only procedures listed in priority_200_forms.json.",
        ],
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl/match official forms for priority_200 catalog")
    parser.add_argument("--no-probe", action="store_true", help="Skip remote preferred-source probing")
    parser.add_argument("--max-probe-per-procedure", type=int, default=1)
    args = parser.parse_args()
    report = run(probe_missing=not args.no_probe, max_probe_per_procedure=max(1, args.max_probe_per_procedure))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Status report: {STATUS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
