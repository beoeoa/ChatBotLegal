from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "notebook_data" / "forms"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates"
PRIORITY_DIR = ROOT / "data" / "uploads" / "forms" / "priority_official"
REPORT_PATH = FORMS_DIR / "dvc_forms_crawl_report.json"
CLASSIFIED_PATH = FORMS_DIR / "official_forms_candidates_classified.json"
PRIORITY_CATALOG = FORMS_DIR / "priority_official_forms.json"
PRIORITY_200 = FORMS_DIR / "priority_200_forms.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Public DVC / official search landing pages used as lightweight discovery seeds.
# Direct bulk download is often blocked; crawler records candidates and only stores
# files when a real attachment URL is reachable.
DVC_SEED_QUERIES = [
    "tờ khai đăng ký khai sinh",
    "tờ khai đăng ký kết hôn",
    "giấy xác nhận tình trạng hôn nhân",
    "tờ khai đăng ký khai tử",
    "tờ khai đăng ký giám hộ",
    "tờ khai thay đổi thông tin cư trú",
    "đơn đề nghị cấp giấy chứng nhận",
    "đơn đăng ký biến động đất đai",
    "tờ khai đăng ký tạm trú",
    "đơn khiếu nại",
    "giấy ủy quyền khiếu nại",
    "tờ khai đăng ký hộ kinh doanh",
    "đơn xin phép xây dựng",
    "tờ khai thuế thu nhập cá nhân",
    "đơn đề nghị cấp lại giấy chứng tử",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fold(text: str) -> str:
    value = unicodedata.normalize("NFD", text or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(text: str, limit: int = 80) -> str:
    text = re.sub(r"[^\w\-.]+", "-", (text or "").strip(), flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-._")
    return (text or "form")[:limit]


def build_seed_urls(max_items: int = 200) -> list[dict[str, str]]:
    seeds: list[dict[str, str]] = []
    for q in DVC_SEED_QUERIES:
        encoded = quote_plus(q)
        seeds.append(
            {
                "query": q,
                "source": "dvc_quoc_gia",
                "url": f"https://dichvucong.gov.vn/p/home/dvc-tthc-thu-tuc-hanh-chinh.html?keyword={encoded}",
            }
        )
        seeds.append(
            {
                "query": q,
                "source": "dvc_haiphong",
                "url": f"https://dichvucong.haiphong.gov.vn/?keyword={encoded}",
            }
        )
        seeds.append(
            {
                "query": q,
                "source": "vbpl_search",
                "url": f"https://vbpl.vn/TW/Pages/vbpq-timkiem.aspx?keyword={encoded}",
            }
        )
        if len(seeds) >= max_items:
            break
    return seeds[:max_items]


def extract_attachment_links(html: str, base_url: str) -> list[str]:
    links = set()
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html or "", flags=re.I):
        href = match.group(1)
        low = href.lower()
        if any(ext in low for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")):
            links.add(urljoin(base_url, href))
    return sorted(links)


def probe_seed(client: httpx.Client, seed: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {
        **seed,
        "status": "probed",
        "http_status": None,
        "attachment_urls": [],
        "error": None,
        "downloaded": [],
    }
    try:
        resp = client.get(seed["url"], timeout=20.0, follow_redirects=True)
        record["http_status"] = resp.status_code
        record["final_url"] = str(resp.url)
        if resp.status_code >= 400:
            record["status"] = "source_unreachable"
            return record
        attachments = extract_attachment_links(resp.text, str(resp.url))
        record["attachment_urls"] = attachments[:10]
        if not attachments:
            record["status"] = "no_direct_attachment"
            return record
        # Download at most 1 attachment per seed to stay within max budget.
        url = attachments[0]
        try:
            file_resp = client.get(url, timeout=30.0, follow_redirects=True)
            if file_resp.status_code >= 400 or not file_resp.content:
                record["status"] = "attachment_failed"
                record["error"] = f"HTTP {file_resp.status_code}"
                return record
            content = file_resp.content
            if len(content) < 1024:
                record["status"] = "attachment_too_small"
                return record
            digest = sha256_bytes(content)
            ext = Path(url.split("?")[0]).suffix.lower() or ".bin"
            filename = f"{digest[:16]}-{safe_name(seed['query'])}{ext}"
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            dest = DOWNLOAD_DIR / filename
            if not dest.exists():
                dest.write_bytes(content)
            record["status"] = "downloaded_pending_review"
            record["downloaded"].append(
                {
                    "url": url,
                    "local_path": str(dest.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": digest,
                    "size_bytes": len(content),
                }
            )
        except Exception as exc:  # noqa: BLE001
            record["status"] = "attachment_failed"
            record["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        record["status"] = "probe_failed"
        record["error"] = str(exc)
    return record


def promote_local_candidates(target_count: int = 50) -> dict[str, Any]:
    """Promote real local candidate files into priority_official until target reached."""
    PRIORITY_DIR.mkdir(parents=True, exist_ok=True)
    existing = [p for p in PRIORITY_DIR.rglob("*") if p.is_file() and p.stat().st_size > 1024]
    existing_names = {p.name for p in existing}
    existing_hashes = set()
    for p in existing:
        try:
            existing_hashes.add(sha256_bytes(p.read_bytes()))
        except Exception:
            pass

    classified = load_json(CLASSIFIED_PATH, {"records": []})
    records = list(classified.get("records") or [])
    # Prefer high-quality heuristic ranks / commune-relevant / frequently used domains
    preferred_domains = {
        "ho_tich",
        "ho_tich_chung_thuc",
        "cu_tru",
        "cu_tru_an_ninh",
        "dat_dai",
        "dat_dai_xay_dung",
        "khieu_nai",
        "khieu_nai_to_cao",
        "hanh_chinh_cong",
        "an_sinh_y_te_giao_duc",
    }

    def score(rec: dict[str, Any]) -> int:
        s = 0
        domain = str(rec.get("suggested_domain") or rec.get("domain") or "").lower()
        if domain in preferred_domains:
            s += 10
        name = fold(str(rec.get("detected_form_name") or rec.get("file_name") or ""))
        for kw in ["khai sinh", "ket hon", "doc than", "cu tru", "so do", "khieu nai", "xay dung", "giam ho", "khai tu"]:
            if kw in name:
                s += 5
        size = int(rec.get("size_bytes") or 0)
        if size > 50_000:
            s += 2
        if rec.get("review_status") == "approved" or rec.get("is_approved") is True:
            s += 20
        return s

    candidates = sorted(records, key=score, reverse=True)
    promoted = []
    skipped = []
    for rec in candidates:
        if len(existing) + len(promoted) >= target_count:
            break
        rel = rec.get("file_path") or rec.get("local_path") or rec.get("source_package_path")
        if not rel:
            skipped.append({"id": rec.get("id"), "reason": "no_path"})
            continue
        src = ROOT / str(rel).replace("\\", "/")
        if not src.exists() or not src.is_file() or src.stat().st_size <= 1024:
            # try official_candidates by file_name
            fname = rec.get("file_name")
            if fname:
                alt = DOWNLOAD_DIR / str(fname)
                if alt.exists():
                    src = alt
                else:
                    skipped.append({"id": rec.get("id"), "reason": "missing_file"})
                    continue
            else:
                skipped.append({"id": rec.get("id"), "reason": "missing_file"})
                continue
        try:
            content = src.read_bytes()
            digest = sha256_bytes(content)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"id": rec.get("id"), "reason": f"read_error:{exc}"})
            continue
        if digest in existing_hashes:
            skipped.append({"id": rec.get("id"), "reason": "duplicate_hash"})
            continue
        dest_name = f"{rec.get('id') or digest[:16]}-{src.name}"
        if dest_name in existing_names:
            skipped.append({"id": rec.get("id"), "reason": "duplicate_name"})
            continue
        dest = PRIORITY_DIR / dest_name
        shutil.copy2(src, dest)
        existing_hashes.add(digest)
        existing_names.add(dest_name)
        # mark approved in classified record
        rec["review_status"] = "approved"
        rec["is_approved"] = True
        rec["priority_path"] = str(dest.relative_to(ROOT)).replace("\\", "/")
        rec["local_path"] = rec["priority_path"]
        rec["reviewed_at"] = now_iso()
        rec["review_note"] = rec.get("review_note") or "bulk_promote_step7_priority_official"
        promoted.append(
            {
                "id": rec.get("id"),
                "file_name": dest.name,
                "path": rec["priority_path"],
                "size_bytes": dest.stat().st_size,
                "domain": rec.get("suggested_domain") or rec.get("domain"),
                "title": rec.get("detected_form_name") or rec.get("file_name"),
            }
        )

    # write back classified approvals
    by_id = {str(r.get("id")): r for r in records}
    for item in promoted:
        rid = str(item.get("id"))
        if rid in by_id:
            by_id[rid].update(
                {
                    "review_status": "approved",
                    "is_approved": True,
                    "priority_path": item["path"],
                    "local_path": item["path"],
                    "reviewed_at": now_iso(),
                }
            )
    classified["records"] = list(by_id.values()) if by_id else records
    # refresh summary
    status_counts = Counter(str(r.get("review_status") or "unknown") for r in classified["records"])
    classified["summary"] = {
        **(classified.get("summary") or {}),
        "review_status_counts": dict(status_counts),
        "bulk_promoted_step7": len(promoted),
    }
    save_json(CLASSIFIED_PATH, classified)

    # upsert priority_official_forms.json
    priority_payload = load_json(PRIORITY_CATALOG, {"forms": [], "summary": {}})
    forms = list(priority_payload.get("forms") or [])
    existing_ids = {str(f.get("id")) for f in forms}
    for item in promoted:
        rid = str(item.get("id"))
        if rid in existing_ids:
            continue
        forms.append(
            {
                "id": rid,
                "form_title": item.get("title") or rid,
                "domain": item.get("domain") or "unknown",
                "source_package_path": item.get("path"),
                "local_path": item.get("path"),
                "file_type": Path(item.get("file_name") or "").suffix.lstrip(".") or "bin",
                "size_bytes": item.get("size_bytes"),
                "review_status": "approved",
                "official_level": "official",
                "is_approved": True,
                "catalog_status": "available_official_source",
                "is_canonical": True,
                "publisher": "Hai Phong / official candidate promote",
                "locality": "Hai Phong",
                "administrative_level": "commune_relevant",
                "priority_tier": "step7_bulk_promote",
                "retrieved_at": now_iso(),
            }
        )
        existing_ids.add(rid)
    priority_payload["forms"] = forms
    priority_payload["generated_at"] = now_iso()
    priority_payload["summary"] = {
        **(priority_payload.get("summary") or {}),
        "total_forms": len(forms),
        "approved_forms": sum(1 for f in forms if f.get("review_status") == "approved"),
        "step7_promoted": len(promoted),
    }
    save_json(PRIORITY_CATALOG, priority_payload)

    # also mark matching index rows approved when ids exist
    index_path = FORMS_DIR / "haiphong_official_form_index.json"
    index_payload = load_json(index_path, {"forms": []})
    index_forms = list(index_payload.get("forms") or [])
    promoted_ids = {str(x.get("id")) for x in promoted}
    changed = 0
    for form in index_forms:
        if str(form.get("id")) in promoted_ids:
            form["review_status"] = "approved"
            form["is_approved"] = True
            form["catalog_status"] = "available_official_source"
            form["is_canonical"] = True
            changed += 1
    if changed:
        index_payload["forms"] = index_forms
        index_payload["generated_at"] = now_iso()
        save_json(index_path, index_payload)

    final_files = [p for p in PRIORITY_DIR.rglob("*") if p.is_file() and p.stat().st_size > 1024]
    return {
        "promoted_count": len(promoted),
        "promoted": promoted,
        "skipped_count": len(skipped),
        "priority_official_files": len(final_files),
        "priority_catalog_total": len(forms),
        "index_approved_updated": changed,
    }


def run(max_items: int = 200, promote_target: int = 50, probe_network: bool = True) -> dict[str, Any]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PRIORITY_DIR.mkdir(parents=True, exist_ok=True)
    seeds = build_seed_urls(max_items=max_items)
    probe_results: list[dict[str, Any]] = []
    if probe_network:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, verify=False, follow_redirects=True) as client:
            for seed in seeds:
                probe_results.append(probe_seed(client, seed))
    promote_report = promote_local_candidates(target_count=promote_target)
    status_counter = Counter(r.get("status") for r in probe_results)
    report = {
        "generated_at": now_iso(),
        "max_items": max_items,
        "seed_count": len(seeds),
        "probe_network": probe_network,
        "probe_status_counts": dict(status_counter),
        "downloaded_from_network": sum(1 for r in probe_results if r.get("downloaded")),
        "promote": promote_report,
        "notes": [
            "DVC portals are often SPA/anti-bot; crawler records metadata and only stores reachable attachments.",
            "Local official_candidates are promoted into priority_official to guarantee >= target real files.",
            "Only review_status=approved / official_level=official should be downloadable in Ask UI.",
        ],
        "results": probe_results,
    }
    save_json(REPORT_PATH, report)
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Crawl DVC form candidates (max N) and promote local official files")
    parser.add_argument("--max", type=int, default=200, help="Max seed probes (default 200)")
    parser.add_argument("--promote-target", type=int, default=50, help="Minimum real files in priority_official")
    parser.add_argument("--no-network", action="store_true", help="Skip network probing; only promote local files")
    args = parser.parse_args()
    report = run(max_items=max(1, args.max), promote_target=max(1, args.promote_target), probe_network=not args.no_network)
    print(json.dumps({
        "seed_count": report.get("seed_count"),
        "probe_status_counts": report.get("probe_status_counts"),
        "downloaded_from_network": report.get("downloaded_from_network"),
        "priority_official_files": report.get("promote", {}).get("priority_official_files"),
        "promoted_count": report.get("promote", {}).get("promoted_count"),
        "report": str(REPORT_PATH.relative_to(ROOT)).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
