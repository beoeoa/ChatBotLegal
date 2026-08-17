"""Deterministic, role-neutral normalization for official legal web pages.

The module has no database side effects.  It validates the initial and final
URL, removes presentation chrome without rewriting legal wording, extracts only
evidenced metadata, and records explainable matches to the five canonical
Officer domains.  Optional browser rendering is isolated behind a graceful
verified-HTTP fallback.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import ipaddress
import json
import re
import unicodedata
from datetime import date
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES
from api.official_http import build_verified_ssl_context


CANONICAL_DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "hanh_chinh_cong",
    "trat_tu_do_thi",
)

DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "ho_tich_chung_thuc": (
        "hộ tịch", "khai sinh", "khai tử", "kết hôn", "chứng thực",
        "nuôi con nuôi", "quốc tịch",
    ),
    "dat_dai_xay_dung": (
        "đất đai", "quyền sử dụng đất", "địa chính", "xây dựng", "nhà ở",
        "quy hoạch", "giấy phép xây dựng", "môi trường",
    ),
    "an_sinh_y_te_giao_duc": (
        "an sinh", "bảo trợ", "người có công", "y tế", "giáo dục",
        "bảo hiểm", "trẻ em", "lao động", "trợ cấp",
    ),
    "hanh_chinh_cong": (
        "thủ tục hành chính", "cư trú", "tạm trú", "thường trú", "căn cước",
        "hộ chiếu", "một cửa", "dịch vụ công", "an ninh trật tự",
    ),
    "trat_tu_do_thi": (
        "trật tự đô thị", "lòng đường", "hè phố", "vỉa hè", "giao thông",
        "xử phạt", "vi phạm hành chính", "khiếu nại", "tố cáo", "phòng cháy",
    ),
}

_LAW_NUMBER_RE = re.compile(
    r"\b(\d{1,4}/\d{2,4}/[A-ZÀ-Ỹ0-9Đ\-]+(?:/[A-ZÀ-Ỹ0-9Đ\-]+)*)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_VI_DATE_RE = re.compile(
    r"\bngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})\b",
    re.IGNORECASE,
)
_NOISE_PATTERN = re.compile(
    r"(?:^|[-_\s])(nav|menu|header|footer|breadcrumb|advert|banner|social|share|"
    r"pagination|pager|cookie|related|sidebar)(?:$|[-_\s])",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(
    r"\b(Luật|Nghị định|Thông tư|Quyết định|Nghị quyết|Chỉ thị|Pháp lệnh)\b",
    re.IGNORECASE,
)
_VBPL_ACTION_ASSIGNMENT_RE = re.compile(
    r"(?P<variable>[A-Za-z_$][\w$]*)=\(0,[A-Za-z_$][\w$]*\.\$\)"
    r"\(\"(?P<action>[0-9a-f]{40})\"\)"
)
_VBPL_DETAIL_CALL_RE = re.compile(
    r'queryKey:\["documents","detail",[^\]]+\].{0,500}?'
    r"return\s+(?P<variable>[A-Za-z_$][\w$]*)\(",
    re.DOTALL,
)


def _plain_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", _compact_text(value)).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def _is_loading_shell(value: Any) -> bool:
    plain = _plain_text(value)
    markers = (
        "dang tai du lieu",
        "vui long cho trong giay lat",
        "portal vbpl - dang tai noi dung",
    )
    return any(marker in plain for marker in markers) and not re.search(
        r"\b(dieu|chuong)\s+\d+\b", plain
    )


def _legislation_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    """Return only the page's authoritative Legislation JSON-LD object."""

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Legislation":
                return item
    return {}


def _discover_vbpl_detail_action_hash(javascript_sources: Sequence[str]) -> str | None:
    """Discover the public VBPL detail action without pinning a build hash."""

    for javascript in javascript_sources:
        source = str(javascript or "")
        call = _VBPL_DETAIL_CALL_RE.search(source)
        if not call:
            continue
        assignments = {
            match.group("variable"): match.group("action")
            for match in _VBPL_ACTION_ASSIGNMENT_RE.finditer(source)
        }
        action = assignments.get(call.group("variable"))
        if action:
            return action
    return None


def _parse_vbpl_server_action_payload(payload: str) -> dict[str, Any]:
    """Parse length-delimited RSC text and evidenced document metadata."""

    data = str(payload or "").encode("utf-8")
    chunks: dict[str, str] = {}
    records: list[Any] = []
    position = 0
    while position < len(data):
        if data[position : position + 1] in {b"\r", b"\n"}:
            position += 1
            continue
        text_header = re.match(rb"([0-9A-Za-z]+):T([0-9A-Fa-f]+),", data[position:])
        if text_header:
            chunk_id = text_header.group(1).decode("ascii")
            length = int(text_header.group(2).decode("ascii"), 16)
            start = position + text_header.end()
            end = start + length
            if end > len(data):
                raise ValueError("VBPL trả về text chunk không đầy đủ.")
            chunks[chunk_id] = data[start:end].decode("utf-8")
            position = end
            continue
        line_end = data.find(b"\n", position)
        if line_end < 0:
            line_end = len(data)
        line = data[position:line_end].rstrip(b"\r").decode("utf-8", "replace")
        _record_id, separator, raw_json = line.partition(":")
        if separator and raw_json[:1] in {"{", "["}:
            try:
                records.append(json.loads(raw_json))
            except json.JSONDecodeError:
                pass
        position = line_end + 1

    def find_document(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if value.get("docNum") and (
                value.get("documentContent") is not None
                or value.get("effFrom") is not None
            ):
                return value
            for nested in value.values():
                found = find_document(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = find_document(nested)
                if found:
                    return found
        return None

    document = next(
        (found for record in records if (found := find_document(record))),
        None,
    )
    if not document:
        raise ValueError("VBPL không trả về bản ghi chi tiết văn bản.")
    content_value = document.get("documentContent")
    if isinstance(content_value, dict):
        content_value = content_value.get("content")
    html = ""
    if isinstance(content_value, str) and re.fullmatch(r"\$[0-9A-Za-z]+", content_value):
        html = chunks.get(content_value[1:], "")
    elif isinstance(content_value, str):
        html = content_value
    if not html and chunks:
        html = max(chunks.values(), key=len)
    if len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)) < 100:
        raise ValueError("Toàn văn VBPL trả về quá ngắn hoặc không đầy đủ.")

    document_type = document.get("docType")
    if isinstance(document_type, dict):
        document_type = document_type.get("name")
    return {
        "html": html,
        "title": _compact_text(document.get("title")),
        "law_number": _compact_text(document.get("docNum")),
        "document_type": _compact_text(document_type),
        "issuing_agency": _compact_text(document.get("agencyName")),
        "issued_date": _iso_date(str(document.get("issueDate") or "")),
        "effective_date": _iso_date(str(document.get("effFrom") or "")),
        "expired_date": _iso_date(str(document.get("effTo") or "")),
        "vbpl_document_id": str(document.get("id") or "").strip(),
    }


async def _fetch_vbpl_server_action_detail(
    client: httpx.AsyncClient,
    source_url: str,
    page_html: str,
) -> dict[str, Any]:
    """Fetch the same official detail payload used by the public VBPL page."""

    document_id = urlparse(source_url).path.rstrip("/").split("/")[-1]
    document_id = document_id.split("--")[-1]
    if not re.fullmatch(r"[0-9a-fA-F-]{16,64}", document_id):
        raise ValueError("URL VBPL không chứa định danh văn bản hợp lệ.")
    soup = BeautifulSoup(page_html, "html.parser")
    script_urls = []
    for script in soup.select("script[src]"):
        script_url = urljoin(source_url, str(script.get("src") or ""))
        parsed = urlparse(script_url)
        if parsed.scheme == "https" and (parsed.hostname or "").lower().endswith("vbpl.vn"):
            script_urls.append(script_url)
    javascript_sources: list[str] = []
    for script_url in dict.fromkeys(script_urls):
        response = await client.get(script_url)
        if response.status_code == 200 and len(response.text) <= 2_000_000:
            javascript_sources.append(response.text)
    action = _discover_vbpl_detail_action_hash(javascript_sources)
    if not action:
        raise ValueError("Không tìm thấy contract chi tiết của phiên bản VBPL hiện tại.")
    response = await client.post(
        source_url,
        headers={
            "Next-Action": action,
            "Accept": "text/x-component",
            "Origin": "https://vbpl.vn",
            "Referer": source_url,
        },
        content=json.dumps([document_id], ensure_ascii=False),
    )
    response.raise_for_status()
    return _parse_vbpl_server_action_payload(response.text)


def validate_official_public_url(url: str) -> str:
    """Validate one official HTTPS URL and reject local/private destinations."""

    value = str(url or "").strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname:
        raise ValueError("Nguồn pháp luật phải là URL HTTPS hợp lệ.")
    if hostname == "localhost":
        raise ValueError("Không được crawl địa chỉ nội bộ hoặc localhost.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ValueError("Không được crawl địa chỉ mạng riêng hoặc địa chỉ đặc biệt.")
    official = any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in OFFICIAL_HOST_SUFFIXES
    )
    if not official:
        raise ValueError("URL không thuộc danh sách nguồn chính thức đã cho phép.")
    return value


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _table_markdown(table: Tag) -> str:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [
            _compact_text(cell.get_text(" ", strip=True)).replace("|", "\\|")
            for cell in row.find_all(["th", "td"], recursive=False)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:]
    rendered = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    rendered.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(rendered)


_EXTRACTION_BLOCK_TYPES = {"text", "table", "image", "ocr", "equation"}


def _block_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return bbox if bbox[2] > bbox[0] and bbox[3] > bbox[1] else None


def make_extraction_block(
    *,
    block_type: str,
    text: str,
    extractor: str,
    extractor_version: str = "unknown",
    page_number: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
    bounding_box: Sequence[float] | None = None,
    confidence: float | None = None,
    table_path: str | None = None,
    row_index: int | None = None,
    column_index: int | None = None,
    source_asset_sha256: str | None = None,
) -> dict[str, Any]:
    """Create one deterministic, public-neutral extraction block."""

    kind = str(block_type or "").strip().casefold()
    content = unicodedata.normalize("NFC", str(text or "")).strip()
    if kind not in _EXTRACTION_BLOCK_TYPES or not content:
        raise ValueError("invalid_extraction_block")
    if page_number is not None and int(page_number) < 1:
        raise ValueError("invalid_extraction_page")
    if (char_start is None) != (char_end is None):
        raise ValueError("invalid_extraction_offsets")
    if char_start is not None and (
        int(char_start) < 0 or int(char_end) <= int(char_start)
    ):
        raise ValueError("invalid_extraction_offsets")
    bbox = _block_bbox(bounding_box)
    if bounding_box is not None and bbox is None:
        raise ValueError("invalid_extraction_bbox")
    score = None if confidence is None else float(confidence)
    if score is not None and not 0 <= score <= 100:
        raise ValueError("invalid_extraction_confidence")
    asset_hash = str(source_asset_sha256 or "").strip().casefold() or None
    if asset_hash and not re.fullmatch(r"[a-f0-9]{64}", asset_hash):
        raise ValueError("invalid_extraction_asset_hash")
    identity = "|".join(
        str(value or "")
        for value in (
            kind,
            page_number,
            char_start,
            char_end,
            table_path,
            content,
            asset_hash,
        )
    )
    physical = bool(
        page_number is not None
        and bbox is not None
        and char_start is not None
        and char_end is not None
        and asset_hash
    )
    return {
        "block_id": "xb-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "block_type": kind,
        "text": content,
        "page_number": int(page_number) if page_number is not None else None,
        "char_start": int(char_start) if char_start is not None else None,
        "char_end": int(char_end) if char_end is not None else None,
        "bounding_box": bbox,
        "confidence": round(score, 2) if score is not None else None,
        "table_path": str(table_path or "").strip() or None,
        "row_index": int(row_index) if row_index is not None else None,
        "column_index": int(column_index) if column_index is not None else None,
        "source_asset_sha256": asset_hash,
        "text_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "extractor": str(extractor or "unknown").strip() or "unknown",
        "extractor_version": str(extractor_version or "unknown").strip() or "unknown",
        "citation_level": "physical_span" if physical else "content_quote",
    }


def normalize_extraction_blocks(
    blocks: Sequence[Mapping[str, Any]] | None,
    *,
    fallback_text: str,
    extractor: str,
    extractor_version: str = "unknown",
    source_asset_sha256: str | None = None,
) -> dict[str, Any]:
    """Normalize optional parser output and degrade explicitly on invalid layout."""

    normalized: list[dict[str, Any]] = []
    rejected = 0
    for item in blocks or []:
        try:
            normalized.append(
                make_extraction_block(
                    block_type=str(item.get("block_type") or item.get("type") or "text"),
                    text=str(item.get("text") or item.get("content") or ""),
                    extractor=str(item.get("extractor") or extractor),
                    extractor_version=str(
                        item.get("extractor_version") or extractor_version
                    ),
                    page_number=item.get("page_number"),
                    char_start=item.get("char_start"),
                    char_end=item.get("char_end"),
                    bounding_box=item.get("bounding_box") or item.get("bbox"),
                    confidence=item.get("confidence"),
                    table_path=item.get("table_path"),
                    row_index=item.get("row_index"),
                    column_index=item.get("column_index"),
                    source_asset_sha256=(
                        item.get("source_asset_sha256") or source_asset_sha256
                    ),
                )
            )
        except (TypeError, ValueError):
            rejected += 1
    if normalized:
        return {
            "status": "partial" if rejected else "ok",
            "reason_code": "EXTRACTION_BLOCKS_PARTIAL" if rejected else "",
            "blocks": normalized,
            "rejected_count": rejected,
        }
    fallback = unicodedata.normalize("NFC", str(fallback_text or "")).strip()
    if fallback:
        block = make_extraction_block(
            block_type="text",
            text=fallback,
            extractor=extractor,
            extractor_version=extractor_version,
            source_asset_sha256=source_asset_sha256,
        )
        return {
            "status": "fallback",
            "reason_code": "EXTRACTION_BLOCKS_INVALID",
            "blocks": [block],
            "rejected_count": rejected,
        }
    return {
        "status": "unavailable",
        "reason_code": "EXTRACTION_BLOCKS_UNAVAILABLE",
        "blocks": [],
        "rejected_count": rejected,
    }


def build_html_extraction_blocks(
    html: str,
    *,
    extractor: str,
    extractor_version: str = "beautifulsoup",
) -> list[dict[str, Any]]:
    """Keep HTML text/table cell order and stable row/column paths."""

    soup = BeautifulSoup(str(html or ""), "html.parser")
    root = soup.find("main") or soup.find("article") or soup.body or soup
    blocks: list[dict[str, Any]] = []
    table_index = 0
    for tag in root.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "table"],
        recursive=True,
    ):
        if tag.name != "table" and tag.find_parent("table") is not None:
            continue
        if tag.name == "p" and tag.find_parent("li") is not None:
            continue
        if tag.name == "table":
            for row_index, row in enumerate(tag.find_all("tr")):
                cells = row.find_all(["th", "td"], recursive=False)
                for column_index, cell in enumerate(cells):
                    cell_text = _compact_text(cell.get_text(" ", strip=True))
                    if not cell_text:
                        continue
                    blocks.append(
                        make_extraction_block(
                            block_type="table",
                            text=cell_text,
                            extractor=extractor,
                            extractor_version=extractor_version,
                            table_path=(
                                f"table:{table_index}/row:{row_index}/cell:{column_index}"
                            ),
                            row_index=row_index,
                            column_index=column_index,
                        )
                    )
            table_index += 1
            continue
        content = _compact_text(tag.get_text(" ", strip=True))
        if content:
            blocks.append(
                make_extraction_block(
                    block_type="text",
                    text=content,
                    extractor=extractor,
                    extractor_version=extractor_version,
                )
            )
    return blocks


def build_pdf_text_blocks(
    pages: Sequence[str],
    *,
    source_asset_sha256: str,
    extractor: str,
    extractor_version: str = "unknown",
) -> list[dict[str, Any]]:
    """Keep page-local offsets for PDF text without claiming unavailable bbox."""

    blocks: list[dict[str, Any]] = []
    for page_number, raw_text in enumerate(pages, 1):
        text = unicodedata.normalize("NFC", str(raw_text or "")).strip()
        if not text:
            continue
        blocks.append(
            make_extraction_block(
                block_type="text",
                text=text,
                extractor=extractor,
                extractor_version=extractor_version,
                page_number=page_number,
                char_start=0,
                char_end=len(text),
                source_asset_sha256=source_asset_sha256,
            )
        )
    return blocks


def clean_legal_html(html: str) -> dict[str, Any]:
    """Return clean legal Markdown plus bounded, explainable cleanup metadata."""

    soup = BeautifulSoup(str(html or ""), "html.parser")
    removed: set[str] = set()
    for tag_name, reason in (
        ("script", "script"), ("style", "style"), ("noscript", "script"),
        ("svg", "decoration"), ("nav", "navigation"), ("header", "header"),
        ("footer", "footer"), ("aside", "sidebar"), ("form", "form"),
    ):
        for tag in list(soup.find_all(tag_name)):
            removed.add(reason)
            tag.decompose()
    for tag in list(soup.find_all(True)):
        # Decomposing a matching parent clears attrs on descendants that were
        # already materialized in this list. Skip those detached tags instead
        # of crashing the whole official-page normalization pass.
        if tag.parent is None or tag.attrs is None:
            continue
        marker = " ".join(
            [str(tag.get("id") or ""), *[str(item) for item in tag.get("class") or []]]
        )
        match = _NOISE_PATTERN.search(marker)
        if match and tag.parent is not None:
            removed.add(match.group(1).casefold())
            tag.decompose()

    root = (
        soup.select_one("#normalized-legal-content")
        or soup.select_one("div.preview-content, #toanvancontent, .prov-content")
        or soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )
    blocks: list[str] = []
    for tag in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre"], recursive=True):
        if tag.name == "table":
            table = _table_markdown(tag)
            if table:
                blocks.append(table)
            continue
        if tag.find_parent("table") is not None:
            continue
        if tag.name == "p" and tag.find_parent("li") is not None:
            continue
        text = _compact_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        if tag.name and tag.name.startswith("h"):
            blocks.append(f"{'#' * int(tag.name[1])} {text}")
        elif tag.name == "li":
            blocks.append(f"- {text}")
        elif tag.name == "pre":
            blocks.append(f"```\n{text}\n```")
        else:
            blocks.append(text)
    fallback_lines = [
        _compact_text(line)
        for line in root.get_text("\n", strip=True).splitlines()
        if _compact_text(line)
    ]
    fallback = "\n\n".join(fallback_lines)
    semantic_markdown = "\n\n".join(dict.fromkeys(blocks)).strip()
    # Some official portals render the legal text in nested div/span nodes.
    # If semantic tags cover only a small fraction, prefer the complete
    # deterministic text projection over silently truncating the document.
    if fallback and len(fallback) > max(200, int(len(semantic_markdown) * 1.5)):
        markdown = fallback
    else:
        markdown = semantic_markdown or fallback
    extraction_blocks = build_html_extraction_blocks(
        str(root),
        extractor="beautifulsoup",
        extractor_version="html-layout-v1",
    )
    return {
        "clean_markdown": markdown,
        "removed_noise": sorted(removed),
        "characters": len(markdown),
        "extraction_blocks": extraction_blocks,
        "layout_status": "available" if extraction_blocks else "fallback",
        "layout_reason": "" if extraction_blocks else "HTML_LAYOUT_BLOCKS_UNAVAILABLE",
    }


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for tag in soup.find_all("meta"):
        key = str(tag.get("name") or tag.get("property") or "").casefold()
        if key in wanted:
            return _compact_text(tag.get("content"))
    return ""


def _iso_date(value: str) -> str | None:
    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", str(value or ""))
    if iso_match:
        year, month, day = (int(item) for item in iso_match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    match = _DATE_RE.search(str(value or "")) or _VI_DATE_RE.search(str(value or ""))
    if not match:
        return None
    day, month, year = (int(item) for item in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _labelled_date(text: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*:?\s*([^\n]{{0,80}})", text, re.IGNORECASE
        )
        if match:
            parsed = _iso_date(match.group(1))
            if parsed:
                return parsed
    return None


def classify_legal_domains(*values: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return deterministic multi-domain matches and their exact rule evidence."""

    haystack = " ".join(_compact_text(value).casefold() for value in values if value)
    evidence: dict[str, list[str]] = {}
    for domain in CANONICAL_DOMAINS:
        matches = [term for term in DOMAIN_TERMS[domain] if term.casefold() in haystack]
        if matches:
            evidence[domain] = [f"term:{term}" for term in matches]
    return [domain for domain in CANONICAL_DOMAINS if domain in evidence], evidence


def normalize_legal_document(
    *,
    source_url: str,
    final_url: str,
    html: str,
    scope: str,
    method: str,
) -> dict[str, Any]:
    """Normalize one fetched official page without any persistence side effect."""

    source_url = validate_official_public_url(source_url)
    final_url = validate_official_public_url(final_url)
    soup = BeautifulSoup(str(html or ""), "html.parser")
    legislation = _legislation_json_ld(soup)
    cleaned = clean_legal_html(html)
    markdown = cleaned["clean_markdown"]
    title = _compact_text(legislation.get("name")) or _meta_content(
        soup, "title", "og:title", "dc.title"
    )
    if not title:
        heading = soup.find("h1")
        title = _compact_text(heading.get_text(" ", strip=True) if heading else "")
    if not title and soup.title:
        title = _compact_text(soup.title.get_text(" ", strip=True))

    full_text = markdown
    law_number = _compact_text(legislation.get("legislationIdentifier")) or _meta_content(
        soup, "law-number", "document-number", "dc.identifier"
    )
    match = _LAW_NUMBER_RE.search(f"{title} {full_text}")
    discovered_law_number = match.group(1) if match else ""
    # VBPL occasionally publishes a shortened JSON-LD identifier such as
    # ``253/2025`` while the signed heading and title contain the complete
    # ``253/2025/QĐ-UBND``. Prefer the longer deterministic match only when it
    # extends that exact prefix; never guess a missing suffix.
    if not law_number or (
        discovered_law_number
        and discovered_law_number.casefold().startswith(
            f"{law_number.casefold()}/"
        )
    ):
        law_number = discovered_law_number or law_number
    passed_by = legislation.get("legislationPassedBy")
    issuing_agency = _compact_text(
        passed_by.get("name") if isinstance(passed_by, dict) else passed_by
    ) or _meta_content(soup, "issuing-agency", "dc.creator", "author")
    if not issuing_agency:
        match = re.search(
            r"(?:Cơ quan ban hành|Ban hành bởi|Cơ quan)\s*:\s*([^|;\n]{3,160})",
            soup.get_text("\n", strip=True),
            re.IGNORECASE,
        )
        issuing_agency = _compact_text(match.group(1)) if match else ""
    issued_date = _iso_date(str(legislation.get("legislationDate") or "")) or _iso_date(
        _meta_content(soup, "issued-date", "dc.date")
    ) or _labelled_date(
        soup.get_text("\n", strip=True), "Ngày ban hành"
    )
    effective_date = _iso_date(_meta_content(soup, "effective-date")) or _labelled_date(
        soup.get_text("\n", strip=True),
        "Ngày hiệu lực",
        "Ngày có hiệu lực",
        "Hiệu lực thi hành kể từ",
        "Có hiệu lực thi hành kể từ",
        "Hiệu lực thi hành từ",
        "Có hiệu lực thi hành từ",
        "Hiệu lực kể từ",
        "Có hiệu lực kể từ",
        "Hiệu lực từ",
        "Có hiệu lực từ",
    )
    expired_date = _iso_date(_meta_content(soup, "expired-date")) or _labelled_date(
        soup.get_text("\n", strip=True), "Ngày hết hiệu lực", "Hết hiệu lực từ"
    )
    document_type = _compact_text(legislation.get("legislationType")) or None
    if not document_type:
        type_match = _TYPE_RE.search(title)
        document_type = type_match.group(1) if type_match else None
    matched_domains, evidence = classify_legal_domains(
        title, document_type or "", issuing_agency, markdown
    )
    reasons: list[str] = []
    loading_shell = _is_loading_shell(markdown)
    if loading_shell:
        reasons.append("loading_placeholder")
    if not matched_domains:
        reasons.append("domain_unresolved")
    if len(markdown) < 100:
        reasons.append("content_too_short")
    for field_name, value in (
        ("law_number_missing", law_number),
        ("document_type_missing", document_type),
        ("issuing_agency_missing", issuing_agency),
        ("issued_date_missing", issued_date),
        ("effective_date_missing", effective_date),
    ):
        if not value:
            reasons.append(field_name)
    status = "rejected" if loading_shell else ("ok" if not reasons else "needs_review")
    safe_markdown = "" if loading_shell else markdown
    fingerprint = hashlib.sha256(safe_markdown.encode("utf-8")).hexdigest() if safe_markdown else ""
    return {
        "status": status,
        "source_url": source_url,
        "final_url": final_url,
        "title": title,
        "law_number": law_number or None,
        "document_type": document_type,
        "issuing_agency": issuing_agency or None,
        "issued_date": issued_date,
        "effective_date": effective_date,
        "expired_date": expired_date,
        "scope": scope,
        "clean_markdown": safe_markdown,
        "characters": len(safe_markdown),
        "content_hash": fingerprint,
        "primary_domain": matched_domains[0] if matched_domains else None,
        "matched_domains": matched_domains,
        "domain_evidence": evidence,
        "extraction": {
            "method": method,
            "complete": bool(safe_markdown),
            "preview": markdown[:2000],
            "removed_noise": cleaned["removed_noise"],
            "reason": "|".join(reasons),
            "extraction_blocks": cleaned.get("extraction_blocks") or [],
            "layout_status": cleaned.get("layout_status") or "fallback",
            "layout_reason": cleaned.get("layout_reason") or "",
        },
    }


async def fetch_normalized_legal_document(
    url: str,
    *,
    scope: str,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Fetch with optional browser rendering and a verified HTTP fallback."""

    source_url = validate_official_public_url(url)
    browser_reason = ""
    if (urlparse(source_url).hostname or "").lower().endswith("vbpl.vn"):
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=True,
                verify=build_verified_ssl_context(),
                headers={"User-Agent": "HaiPhongLegalAssistant/1.0 (+candidate-first)"},
            ) as client:
                metadata_response = await client.get(source_url)
                metadata_response.raise_for_status()
                final_url = validate_official_public_url(str(metadata_response.url))
                server_detail = await _fetch_vbpl_server_action_detail(
                    client,
                    final_url,
                    metadata_response.text,
                )
            normalized_server_detail = normalize_legal_document(
                source_url=source_url,
                final_url=final_url,
                html=str(server_detail["html"]),
                scope=scope,
                method="vbpl_server_action",
            )
            for field in (
                "title",
                "law_number",
                "document_type",
                "issuing_agency",
                "issued_date",
                "effective_date",
                "expired_date",
            ):
                if server_detail.get(field):
                    normalized_server_detail[field] = server_detail[field]
            normalized_server_detail["extraction"]["vbpl_document_id"] = server_detail.get(
                "vbpl_document_id"
            )
            normalized_server_detail["extraction"]["official_payload"] = "next_server_action"
            normalized_server_detail["status"] = (
                "ok"
                if normalized_server_detail.get("clean_markdown")
                and all(
                    normalized_server_detail.get(field)
                    for field in (
                        "law_number",
                        "document_type",
                        "issuing_agency",
                        "issued_date",
                        "effective_date",
                    )
                )
                else "needs_review"
            )
            if normalized_server_detail["status"] == "ok":
                normalized_server_detail["extraction"]["reason"] = ""
            if normalized_server_detail.get("clean_markdown"):
                return normalized_server_detail

            from open_notebook.utils.vbpl_crawler import crawl_vbpl_url

            vbpl_result = await crawl_vbpl_url(final_url)
            metadata_soup = BeautifulSoup(metadata_response.text, "html.parser")
            legislation = _legislation_json_ld(metadata_soup)
            legislation_script = (
                '<script type="application/ld+json">'
                + json.dumps(legislation, ensure_ascii=False).replace("</", "<\\/")
                + "</script>"
                if legislation
                else ""
            )
            combined_html = (
                "<html>"
                + str(metadata_soup.head or "")
                + legislation_script
                + '<body><main id="normalized-legal-content">'
                + str(vbpl_result.get("html") or "")
                + "</main></body></html>"
            )
            normalized_vbpl = normalize_legal_document(
                source_url=source_url,
                final_url=validate_official_public_url(
                    str(vbpl_result.get("final_url") or final_url)
                ),
                html=combined_html,
                scope=scope,
                method="vbpl_playwright",
            )
            if normalized_vbpl.get("status") != "rejected" and normalized_vbpl.get(
                "clean_markdown"
            ):
                return normalized_vbpl
            browser_reason = str(
                (normalized_vbpl.get("extraction") or {}).get("reason")
                or "vbpl_render_incomplete"
            )
        except Exception as exc:  # optional VBPL browser adapter boundary
            browser_reason = f"vbpl_playwright:{exc.__class__.__name__}"

    try:
        from api.crawlers.crawl4ai_fetcher import fetch_rendered

        rendered = await fetch_rendered(source_url, timeout_ms=int(timeout_seconds * 1000))
    except Exception as exc:  # optional adapter boundary
        rendered = {"status": "unavailable", "reason": exc.__class__.__name__}
    if rendered.get("status") == "ok" and rendered.get("html"):
        final_url = validate_official_public_url(str(rendered.get("final_url") or source_url))
        normalized_rendered = normalize_legal_document(
            source_url=source_url,
            final_url=final_url,
            html=str(rendered.get("html") or ""),
            scope=scope,
            method="crawl4ai",
        )
        if normalized_rendered.get("status") != "rejected" and normalized_rendered.get(
            "clean_markdown"
        ):
            return normalized_rendered
        rendered_reason = str(
            (normalized_rendered.get("extraction") or {}).get("reason")
            or "render_incomplete"
        )
        browser_reason = "|".join(filter(None, (browser_reason, rendered_reason)))
    else:
        rendered_reason = str(rendered.get("reason") or rendered.get("status") or "unavailable")
        browser_reason = "|".join(filter(None, (browser_reason, rendered_reason)))

    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            verify=build_verified_ssl_context(),
            headers={"User-Agent": "HaiPhongLegalAssistant/1.0 (+candidate-first)"},
        ) as client:
            response = await client.get(source_url)
        response.raise_for_status()
        final_url = validate_official_public_url(str(response.url))
        result = normalize_legal_document(
            source_url=source_url,
            final_url=final_url,
            html=response.text,
            scope=scope,
            method="verified_http",
        )
        if browser_reason:
            result["extraction"]["browser_fallback_reason"] = browser_reason[:500]
        return result
    except Exception as exc:
        return {
            "status": "rejected",
            "source_url": source_url,
            "final_url": source_url,
            "title": "",
            "clean_markdown": "",
            "characters": 0,
            "content_hash": "",
            "primary_domain": None,
            "matched_domains": [],
            "domain_evidence": {},
            "scope": scope,
            "extraction": {
                "method": "verified_http",
                "complete": False,
                "preview": "",
                "removed_noise": [],
                "reason": f"fetch_failed:{exc.__class__.__name__}",
                "browser_fallback_reason": browser_reason[:500],
            },
        }
