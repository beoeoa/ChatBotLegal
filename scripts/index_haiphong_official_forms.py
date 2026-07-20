"""Build a reviewable form index from official Hai Phong source packages.

This script never recreates a legal form. It only points to headings and page or
paragraph locations inside byte-verified official PDF/DOCX packages.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_candidates.json"
INDEX_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json"

FORM_PREFIXES = (
    "MẪU SỐ",
    "MẪU ĐƠN",
    "MẪU TỜ KHAI",
    "TỜ KHAI",
    "ĐƠN ĐỀ NGHỊ",
    "GIẤY ĐỀ NGHỊ",
    "PHIẾU ĐỀ NGHỊ",
    "PHIẾU ĐĂNG KÝ",
    "BẢN KHAI",
    "VĂN BẢN ĐỀ NGHỊ",
)

FORM_CONTEXT = (
    "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",
    "KÍNH GỬI",
    "HỌ VÀ TÊN",
    "NGƯỜI KHAI",
    "NGƯỜI LÀM ĐƠN",
    "KÝ, GHI RÕ HỌ TÊN",
)

IGNORED_HEADINGS = (
    "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",
    "ĐỘC LẬP - TỰ DO - HẠNH PHÚC",
    "PHỤ LỤC",
    "QUY TRÌNH",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalized_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = value.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def form_headings(text: str) -> list[str]:
    """Find strong form headings while rejecting mere table references."""
    cleaned = normalized(text.replace("\xa0", " "))
    upper = cleaned.upper()
    if not any(marker in upper for marker in FORM_CONTEXT):
        return []

    raw_lines = [normalized(line) for line in text.splitlines()]
    lines = [line for line in raw_lines if line]
    headings: list[str] = []
    consumed_indexes: set[int] = set()
    for index, line in enumerate(lines):
        if index in consumed_indexes:
            continue
        line_upper = line.upper()
        if not any(line_upper.startswith(prefix) for prefix in FORM_PREFIXES):
            continue
        if len(line) > 220:
            continue

        heading = line
        if line_upper.startswith("MẪU SỐ"):
            for candidate_index in range(index + 1, min(index + 6, len(lines))):
                candidate = lines[candidate_index]
                candidate_upper = candidate.upper()
                if candidate_upper in IGNORED_HEADINGS or len(candidate) > 180:
                    continue
                if any(candidate_upper.startswith(prefix) for prefix in FORM_PREFIXES[3:]):
                    heading = f"{line} - {candidate}"
                    consumed_indexes.add(candidate_index)
                    break

        key = normalized_key(heading)
        if key and key not in {normalized_key(item) for item in headings}:
            headings.append(heading)
    return headings


def docx_paragraphs(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            raw = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except (KeyError, zipfile.BadZipFile):
        return []
    raw = re.sub(r"</w:(?:p|tr)>", "\n", raw)
    raw = re.sub(r"<w:tab[^>]*/>", "\t", raw)
    raw = re.sub(r"<w:br[^>]*/>", "\n", raw)
    return [
        normalized(html.unescape(re.sub(r"<[^>]+>", "", line)))
        for line in raw.splitlines()
        if normalized(html.unescape(re.sub(r"<[^>]+>", "", line)))
    ]


def index_pdf(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reader = PdfReader(path)
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for heading in form_headings(text):
            rows.append(
                {
                    "form_title": heading,
                    "source_page": page_number,
                    "source_paragraph": None,
                }
            )
    return rows


def index_docx(path: Path) -> list[dict[str, Any]]:
    paragraphs = docx_paragraphs(path.read_bytes())
    rows: list[dict[str, Any]] = []
    for heading in form_headings("\n".join(paragraphs)):
        heading_key = normalized_key(heading)
        paragraph_number = None
        for index, paragraph in enumerate(paragraphs):
            paragraph_key = normalized_key(paragraph)
            if paragraph_key and (
                paragraph_key in heading_key or heading_key in paragraph_key
            ):
                paragraph_number = index + 1
                break
        rows.append(
            {
                "form_title": heading,
                "source_page": None,
                "source_paragraph": paragraph_number,
            }
        )
    return rows


def build_index() -> dict[str, Any]:
    payload = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8-sig"))
    form_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for record in payload.get("records", []):
        if record.get("status") != "downloaded_pending_review":
            continue
        relative_path = record.get("local_path")
        if not relative_path:
            continue
        path = ROOT / str(relative_path)
        if not path.is_file():
            errors.append({"path": str(relative_path), "error": "file_not_found"})
            continue
        try:
            if record.get("file_type") == "pdf":
                indexed = index_pdf(path)
            elif record.get("file_type") == "docx":
                indexed = index_docx(path)
            else:
                indexed = []
        except (OSError, ValueError) as exc:
            errors.append({"path": str(relative_path), "error": str(exc)})
            continue

        seen_titles: set[str] = set()
        for item in indexed:
            title_key = normalized_key(item["form_title"])
            if not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            raw_id = (
                f"{record.get('sha256')}|{item['source_page']}|"
                f"{item['source_paragraph']}|{item['form_title']}"
            )
            title_quality = (
                "needs_title_review"
                if re.fullmatch(r"mau so [a-z0-9./-]+", title_key)
                else "usable"
            )
            form_rows.append(
                {
                    "id": hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24],
                    **item,
                    "domain": record.get("domain"),
                    "source_package_title": record.get("title"),
                    "source_package_path": relative_path,
                    "source_page_url": record.get("source_page_url"),
                    "source_download_url": record.get("download_url"),
                    "source_sha256": record.get("sha256"),
                    "publisher": record.get("publisher"),
                    "locality": "Hải Phòng",
                    "administrative_level": "commune_candidate",
                    "review_status": "candidate_pending_review",
                    "legal_status": "needs_admin_effectivity_review",
                    "effectivity_flags": record.get("effectivity_flags") or [],
                    "title_quality": title_quality,
                }
            )

    domain_counts: dict[str, int] = {}
    for row in form_rows:
        domain = str(row.get("domain") or "unknown")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    group_counts: dict[str, int] = {}
    for row in form_rows:
        group_key = f"{row.get('domain')}|{normalized_key(row['form_title'])}"
        group_counts[group_key] = group_counts.get(group_key, 0) + 1
        row["duplicate_group"] = hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:16]
    duplicate_references = sum(count - 1 for count in group_counts.values())

    result = {
        "generated_at": utcnow(),
        "summary": {
            "official_source_packages": sum(
                1
                for record in payload.get("records", [])
                if record.get("status") == "downloaded_pending_review"
            ),
            "form_source_references": len(form_rows),
            "unique_form_title_groups": len(group_counts),
            "duplicate_source_references": duplicate_references,
            "needs_title_review": sum(
                1 for row in form_rows if row["title_quality"] != "usable"
            ),
            "domain_counts": domain_counts,
            "errors": len(errors),
        },
        "forms": form_rows,
        "errors": errors,
    }
    INDEX_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    result = build_index()
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"Form index: {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
