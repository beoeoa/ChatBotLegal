from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import shutil
import threading
import unicodedata
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from api.crawlers.crawl4ai_fetcher import fetch_rendered
from api.crawlers.legal_document_pipeline import fetch_normalized_legal_document
from api.legal_candidate_duplicates import (
    CandidateChanged,
    DuplicateCheckUnavailable,
    annotate_candidate_duplicates,
    candidate_duplicate_rows,
    identity_metadata,
    public_duplicate_match,
    update_candidate_if_current,
    warehouse_duplicate_rows,
)
from api.legal_document_identity import (
    candidate_external_id,
    compare_legal_documents,
    legal_identity,
)
from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES
from api.legal_post_activation_smoke import verify_post_activation_retrieval
from api.observability import telemetry
from api.organization_service import (
    propose_candidate_assignment,
    replace_document_unit_assignments,
    routing_mode,
)
from api.system_settings import active_organization_units, active_settings
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_delete,
    repo_query,
    repo_update,
)

VBPL_SITEMAP_INDEX = "https://vbpl.vn/sitemap.xml"
WEEKLY_CRAWL_INTERVAL_MINUTES = 7 * 24 * 60
LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://127.0.0.1:8766"
).rstrip("/")
LEGAL_MANAGEMENT_URL = os.getenv(
    "LEGAL_MANAGEMENT_URL", "http://127.0.0.1:8765"
).rstrip("/")

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
LAW_NUMBER_PATTERN = re.compile(
    r"\b(\d{1,4}/(?:\d{2,4}/)?"
    r"(?=[A-Z0-9ĐƠƯÂÊÔ\-/]*[A-ZĐƠƯÂÊÔ])"
    r"[A-Z0-9ĐƠƯÂÊÔ\-]+(?:/[A-Z0-9ĐƠƯÂÊÔ\-]+)*)\b",
    re.IGNORECASE,
)

# The candidate crawler follows the five officer desks used by the ward pilot.
# Classification is deterministic and always remains a review hint, never an
# automatic approval/import decision.
LEGAL_DOMAINS = {
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "hanh_chinh_cong",
    "trat_tu_do_thi",
}
LEGACY_DOMAIN_ALIASES = {
    "cu_tru_an_ninh": "hanh_chinh_cong",
    "khieu_nai_to_cao_xu_phat": "trat_tu_do_thi",
}
DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ho_tich_chung_thuc", ("hộ tịch", "khai sinh", "khai tử", "kết hôn", "chứng thực", "nuôi con nuôi", "quốc tịch")),
    ("dat_dai_xay_dung", ("đất đai", "quyền sử dụng đất", "xây dựng", "nhà ở", "quy hoạch", "địa chính", "giấy phép xây dựng")),
    ("an_sinh_y_te_giao_duc", ("an sinh", "bảo trợ", "người có công", "y tế", "giáo dục", "bảo hiểm", "trẻ em", "lao động")),
    ("hanh_chinh_cong", ("thủ tục hành chính", "cư trú", "tạm trú", "thường trú", "căn cước", "hộ chiếu", "một cửa", "dịch vụ công")),
    ("trat_tu_do_thi", ("trật tự đô thị", "lòng đường", "hè phố", "vỉa hè", "giao thông", "xử phạt", "vi phạm hành chính", "khiếu nại", "tố cáo", "phòng cháy")),
)
DOMAIN_AGENCY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ho_tich_chung_thuc", ("bộ tư pháp", "sở tư pháp")),
    ("dat_dai_xay_dung", ("bộ xây dựng", "bộ tài nguyên", "sở xây dựng", "sở tài nguyên")),
    ("an_sinh_y_te_giao_duc", ("bộ y tế", "bộ giáo dục", "bộ lao động", "sở y tế", "sở giáo dục")),
    ("hanh_chinh_cong", ("bộ công an", "văn phòng chính phủ", "ủy ban nhân dân")),
    ("trat_tu_do_thi", ("bộ giao thông", "cảnh sát giao thông", "thanh tra giao thông")),
)
# Canonical UTF-8 rules used for new crawls. Legacy garbled rules above remain
# readable for old fixtures, but must not classify newly fetched Vietnamese.
CANONICAL_DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ho_tich_chung_thuc", ("hộ tịch", "khai sinh", "khai tử", "kết hôn", "chứng thực", "nuôi con nuôi", "quốc tịch")),
    ("dat_dai_xay_dung", ("đất đai", "quyền sử dụng đất", "xây dựng", "nhà ở", "quy hoạch", "địa chính", "giấy phép xây dựng", "môi trường")),
    ("an_sinh_y_te_giao_duc", ("an sinh", "bảo trợ", "người có công", "y tế", "giáo dục", "bảo hiểm", "trẻ em", "lao động", "trợ cấp")),
    ("hanh_chinh_cong", ("thủ tục hành chính", "cư trú", "tạm trú", "thường trú", "căn cước", "hộ chiếu", "một cửa", "dịch vụ công", "an ninh trật tự")),
    ("trat_tu_do_thi", ("trật tự đô thị", "lòng đường", "hè phố", "vỉa hè", "giao thông", "xử phạt", "vi phạm hành chính", "khiếu nại", "tố cáo", "phòng cháy")),
)
CANONICAL_DOMAIN_AGENCY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ho_tich_chung_thuc", ("bộ tư pháp", "sở tư pháp")),
    ("dat_dai_xay_dung", ("bộ xây dựng", "bộ tài nguyên", "bộ nông nghiệp và môi trường", "sở xây dựng", "sở tài nguyên", "sở nông nghiệp và môi trường")),
    ("an_sinh_y_te_giao_duc", ("bộ y tế", "bộ giáo dục", "bộ lao động", "sở y tế", "sở giáo dục", "sở nội vụ")),
    ("hanh_chinh_cong", ("bộ công an", "văn phòng chính phủ", "ủy ban nhân dân")),
    ("trat_tu_do_thi", ("bộ giao thông", "bộ công an", "cảnh sát giao thông", "thanh tra giao thông")),
)

LISTING_DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
FORM_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx")
CRAWLER_USER_AGENT = "HaiPhongLegalAssistant/1.0 (+candidate-first; contact: admin)"
MOJIBAKE_MARKERS = ("Ã", "Â", "Ä", "Æ", "áº", "á»", "â€")
# Keep the strict review, preparation and final import gates aligned.  A short
# listing description is useful discovery metadata, but it is not enough legal
# text to chunk, embed and verify through retrieval.
MIN_VERIFIED_LEGAL_CONTENT_CHARS = 500
DVC_LISTING_PATH = "/p/home/dvc-thu-tuc-hanh-chinh.html"
DVC_FORMALITY_SEARCH_ENDPOINT = (
    "https://dichvucong.gov.vn/api/v1/submitting/"
    "formality/list-all-public-formality-by-citizen"
)
DVC_CURSOR_PARAM = "_crawler_last_id"
DVC_LIMIT_PARAM = "_crawler_limit"
DVC_SEEN_PARAM = "_crawler_seen"
DVC_MAX_PAGE_SIZE = 50

# Internal queues are useful provenance records, but they do not represent
# public websites and must never be passed to the HTTP crawler.
INTERNAL_SOURCE_TYPES = frozenset({"officer_proposal", "source_gap_candidate"})
CRAWLABLE_SOURCE_TYPES = frozenset({"vbpl_listing", "official_listing"})
CRAWL_WEBSITE_TYPES = frozenset({
    "mixed_official",
    "legal_documents",
    "procedures",
    "forms",
    "reference",
})

# The local runtime uses one API worker, but the scheduler and an Admin click
# can still reach the same source concurrently. Keep a process-wide, immediate
# lease so cursor/status updates cannot race each other. Cross-process safety
# remains enforced by candidate external_id uniqueness and final import guards.
_SOURCE_SCAN_GUARD = threading.Lock()
_ACTIVE_SOURCE_SCANS: set[str] = set()


def _claim_source_scan(source_id: str) -> bool:
    with _SOURCE_SCAN_GUARD:
        if source_id in _ACTIVE_SOURCE_SCANS:
            return False
        _ACTIVE_SOURCE_SCANS.add(source_id)
        return True


def _release_source_scan(source_id: str) -> None:
    with _SOURCE_SCAN_GUARD:
        _ACTIVE_SOURCE_SCANS.discard(source_id)


def _detail_fetch_enabled() -> bool:
    return os.getenv("LEGAL_CRAWL_DETAIL_FETCH_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }
DEFAULT_CRAWL_SOURCE_KEYS = frozenset({
    ("vbpl_listing", "https://vbpl.vn/van-ban/trung-uong"),
    ("vbpl_listing", "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong"),
    ("official_listing", "https://dichvucong.gov.vn/p/home/dvc-thu-tuc-hanh-chinh.html"),
    ("official_listing", "https://haiphong.gov.vn/Van-ban-quy-pham-phap-luat"),
    ("official_listing", "https://haiphong.gov.vn/thu-tuc-hanh-chinh-76761"),
    ("official_listing", "https://sotp.haiphong.gov.vn/van-ban-quy-pham-phap-luat"),
})


def _plain_text(value: str | None) -> str:
    """Lowercase Vietnamese text for stable keyword matching."""
    normalized = unicodedata.normalize("NFD", value or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return normalized.replace("đ", "d").lower()


def _is_next_listing_label(value: str | None) -> bool:
    """Recognize paginator labels without matching words inside legal titles."""
    label = " ".join(_plain_text(value).split())
    if label in {"next", "sau", "tiep", "trang sau", "trang tiep", "trang ke", ">", ">>", "›", "»"}:
        return True
    return bool(re.fullmatch(r"(?:trang\s+)?(?:sau|tiep|ke\s+tiep)\s*\d*", label))


def _repair_mojibake_text(value: str) -> str:
    """Repair legacy crawler display metadata without changing stored evidence."""
    current = value
    for _ in range(2):
        current_score = sum(current.count(marker) for marker in MOJIBAKE_MARKERS)
        if current_score == 0:
            break
        best = current
        best_score = current_score
        for encoding in ("latin-1", "cp1252"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            score = sum(candidate.count(marker) for marker in MOJIBAKE_MARKERS)
            if score < best_score:
                best = candidate
                best_score = score
        if best == current:
            break
        current = best
    return current


def _repair_display_metadata(value: Any) -> Any:
    if isinstance(value, str):
        return _repair_mojibake_text(value)
    if isinstance(value, list):
        return [_repair_display_metadata(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_display_metadata(item) for key, item in value.items()}
    return value


def _compact_candidate_list_item(candidate: dict[str, Any], *, summary_only: bool = False) -> dict[str, Any]:
    """Return safe, bounded review-list data without shipping full legal text."""
    compact = dict(candidate)
    current_law_number = str(compact.get("law_number") or "").strip()
    if not current_law_number or LISTING_DATE_PATTERN.fullmatch(current_law_number):
        raw = compact.get("raw_metadata") or {}
        extraction = compact.get("extraction_result") or {}
        evidence_text = " ".join(
            str(value or "")
            for value in (
                compact.get("title"),
                compact.get("description"),
                raw.get("context") if isinstance(raw, dict) else "",
                extraction.get("preview") if isinstance(extraction, dict) else "",
            )
        )
        repaired_law_number = _law_number_from_text(evidence_text)
        if repaired_law_number:
            # Historical crawler rows may contain the issue date in this
            # column. Repair only the read projection from evidenced text;
            # approval still refetches and persists official metadata.
            compact["law_number"] = repaired_law_number
    content = str(compact.pop("content", "") or "")
    compact["content_characters"] = len(content)
    extraction = compact.get("extraction_result")
    if isinstance(extraction, dict):
        compact_extraction = dict(extraction)
        if summary_only:
            # Detailed extraction remains available from get_candidate. The
            # paged review screen only consumes the bounded preview and status.
            compact_extraction.pop("extraction_blocks", None)
        preview = str(compact_extraction.get("preview") or "")
        if len(preview) > 2000:
            compact_extraction["preview"] = preview[:2000].rstrip() + "…"
        compact["extraction_result"] = compact_extraction
    if summary_only:
        compact.pop("review_recommendation", None)
    return compact


def _candidate_list_filter(status=None, domain=None, source_type=None, origin=None):
    """Shared predicates: a page and its total must describe the same queue."""
    predicates = []
    params = {}
    status = str(status or "").strip().lower()
    source_type = str(source_type or "").strip().lower()
    if status == "needs_attention":
        predicates.append("status IN ['import_failed', 'changes_requested', 'approved']")
    elif status and status != "all":
        predicates.append("status = $status")
        params["status"] = status
    if domain:
        predicates.append("domain = $domain")
        params["domain"] = domain
    if source_type == "unclassified":
        predicates.append("(source_type = NONE OR source_type = '' OR source_type = 'legal_document_candidate')")
    elif source_type and source_type != "all":
        predicates.append("source_type = $source_type")
        params["source_type"] = source_type
    if origin == "officer":
        predicates.append("raw_metadata.candidate_origin = 'officer_document_proposal'")
    elif origin == "crawler":
        predicates.append("(raw_metadata.candidate_origin = NONE OR raw_metadata.candidate_origin != 'officer_document_proposal')")
    return (f" WHERE {' AND '.join(predicates)}" if predicates else ""), params


def _deterministic_domain(
    title: str,
    issuing_agency: str = "",
    document_type: str = "",
    context: str = "",
) -> tuple[str | None, list[str]]:
    """Map a VBPL listing item by fixed agency/type/title/context rules.

    A tie is intentionally returned as unclassified. AI review may explain the
    item later, but never overrides this crawler classification automatically.
    """
    text = _plain_text(" ".join((title, context)))
    agency = _plain_text(issuing_agency)
    doc_type = _plain_text(document_type)
    scored: list[tuple[str, int, list[str]]] = []
    for domain, terms in CANONICAL_DOMAIN_RULES:
        evidence = [f"keyword:{term}" for term in terms if _plain_text(term) in text]
        score = len(evidence) * 3
        agency_terms = next((items for name, items in CANONICAL_DOMAIN_AGENCY_RULES if name == domain), ())
        for term in agency_terms:
            if _plain_text(term) in agency:
                evidence.append(f"agency:{term}")
                score += 2
        if domain == "trat_tu_do_thi" and any(term in doc_type for term in ("nghị định", "thông tư")) and score:
            evidence.append("document_type:legal_instrument")
            score += 1
        if score:
            scored.append((domain, score, evidence))
    if not scored:
        return None, []
    best_score = max(score for _domain, score, _evidence in scored)
    winners = [(domain, evidence) for domain, score, evidence in scored if score == best_score]
    if len(winners) != 1:
        return None, [f"ambiguous:{domain}" for domain, _evidence in winners]
    return winners[0]


def _law_number_from_text(value: str) -> str | None:
    labelled = re.search(
        r"(?:Số hiệu|Số|Ký hiệu)\s*:\s*([^|;\n]{2,100})",
        value or "",
        re.IGNORECASE,
    )
    if labelled:
        match = LAW_NUMBER_PATTERN.search(labelled.group(1))
        if match:
            return match.group(1)
    match = LAW_NUMBER_PATTERN.search(value or "")
    return match.group(1) if match else None


def _issued_date_from_text(value: str) -> str | None:
    """Extract an unambiguous DD/MM/YYYY or DD-MM-YYYY listing date."""
    match = LISTING_DATE_PATTERN.search(value or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _labelled_listing_date(value: str, *labels: str) -> str | None:
    """Read a date only from its official listing label."""

    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*:\s*([^|;\n]{{1,80}})",
            value or "",
            re.IGNORECASE,
        )
        if match:
            parsed = _issued_date_from_text(match.group(1))
            if parsed:
                return parsed
    return None


def _stable_external_id(source_url: str, source_type: str) -> str:
    digest = hashlib.sha256(f"{source_type}|{source_url}".encode("utf-8")).hexdigest()
    return f"{source_type}-{digest[:40]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _detect_scope(law_number: str | None, title: str, content: str, source_url: str | None = None) -> str:
    """Auto-detect scope of legal document. Week 4."""
    text_to_check = _plain_text(
        f"{law_number or ''} {title} {content} {source_url or ''}"
    )
    
    # Local patterns
    local_patterns = [
        r'\bphuong\s+\w+', r'\bxa\s+\w+', r'\bubnd\s+phuong', r'\bubnd\s+xa',
    ]
    
    # Hai Phong patterns
    haiphong_patterns = [
        r'\bhai phong\b', r'\btp hai phong\b', r'\bthanh pho hai phong\b',
        r'ubnd\s+tp\s*hai', r'\bo\s+hai\s+phong\b',
    ]
    
    for pattern in local_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return "local"
    
    for pattern in haiphong_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return "haiphong"
    
    return "central"


def _canonical_scope(value: Any) -> str | None:
    """Normalize legacy/display scope labels before they reach import APIs.

    Crawl listings created before the canonical scope contract may contain
    labels such as ``Trung ương - toàn quốc``. Those labels are useful for
    display, but management/retrieval accepts only the three stable values.
    Keep the conversion deterministic and conservative so an unknown label is
    still blocked instead of being guessed.
    """

    text = unicodedata.normalize("NFD", str(value or "").casefold()).replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = " ".join(text.split())
    if text in {"central", "haiphong", "local"}:
        return text
    if "hai phong" in text:
        return "haiphong"
    if "trung uong" in text or "toan quoc" in text:
        return "central"
    if "dia phuong" in text or "cap tinh" in text or "cap xa" in text:
        return "local"
    return None


class LegalCrawlService:
    """Service for crawling and processing legal documents from VBPL."""

    @staticmethod
    def build_review_recommendation(candidate: dict[str, Any]) -> dict[str, Any]:
        """Create strict, explainable Vietnamese review guidance.

        The guidance is deterministic and advisory only.  A failed gate never
        changes a candidate's review/import state; it instead tells an Admin
        exactly what must be verified before a human decision.
        """
        raw = candidate.get("raw_metadata") or {}
        extraction = candidate.get("extraction_result") or {}
        domain = candidate.get("domain") or raw.get("domain")
        scope = _canonical_scope(candidate.get("scope") or raw.get("scope")) or ""
        source_type = str(candidate.get("source_type") or raw.get("source_type") or "document")
        title = str(candidate.get("title") or "").strip()
        law_number = str(candidate.get("law_number") or raw.get("law_number") or "").strip()
        document_type = str(candidate.get("document_type") or raw.get("document_type") or "").strip()
        issuing_agency = str(candidate.get("issuing_agency") or raw.get("issuing_agency") or "").strip()
        chars = int(extraction.get("characters") or len(candidate.get("content") or ""))
        ocr_status = str(extraction.get("ocr_status") or raw.get("ocr_status") or "not_required")
        duplicate_count = len(candidate.get("duplicate_candidates") or [])
        source_url = str(candidate.get("source_url") or raw.get("source_url") or "").strip()
        pdf_kind = str(extraction.get("pdf_kind") or raw.get("pdf_kind") or "unknown")
        preview = str(extraction.get("preview") or "").strip()
        fingerprint = extraction.get("text_fingerprint") or raw.get("text_fingerprint")
        evidence: list[str] = []
        hard_gate_failures: list[str] = []

        parsed_source = urlparse(source_url)
        source_host = (parsed_source.hostname or "").lower().rstrip(".")
        official_host = any(
            source_host == suffix or source_host.endswith(f".{suffix}")
            for suffix in OFFICIAL_HOST_SUFFIXES
        )
        from api.legal_source_input import valid_source_reference
        has_original = bool((candidate.get('uploaded_file') or {}).get('sha256'))
        source_verified = bool(raw.get("confirmed_official_source")) and (valid_source_reference(source_url) or has_original)
        if not source_verified:
            if not source_url:
                hard_gate_failures.append("Thiếu URL nguồn chính thức.")
            elif not valid_source_reference(source_url) and not has_original:
                hard_gate_failures.append("Bổ sung liên kết HTTP/HTTPS hoặc tệp gốc để đối chiếu.")
            else:
                hard_gate_failures.append("Nguồn chính thức chưa được Admin xác minh.")

        metadata_missing = []
        if not title:
            metadata_missing.append("tên văn bản")
        if not law_number:
            metadata_missing.append("số, ký hiệu")
        if not document_type:
            metadata_missing.append("loại văn bản")
        if not issuing_agency:
            metadata_missing.append("cơ quan ban hành")
        if scope not in {"central", "haiphong", "local"}:
            metadata_missing.append("phạm vi áp dụng")
        if metadata_missing:
            hard_gate_failures.append(f"Thiếu hoặc chưa xác minh: {', '.join(metadata_missing)}.")

        effective_date = raw.get("effective_date")
        effectivity_verified = False
        try:
            effective = date.fromisoformat(str(effective_date))
            expired_value = raw.get("expired_date")
            expired = date.fromisoformat(str(expired_value)) if expired_value else None
            effectivity_verified = effective <= date.today() and (expired is None or expired > date.today())
            if effective > date.today():
                hard_gate_failures.append("Văn bản chưa có hiệu lực tại thời điểm đánh giá.")
            elif expired is not None and expired <= date.today():
                hard_gate_failures.append("Văn bản đã hết hiệu lực hoặc cần kiểm tra văn bản thay thế.")
        except (TypeError, ValueError):
            hard_gate_failures.append("Thiếu hoặc sai bằng chứng về ngày có hiệu lực.")

        extraction_complete = (
            chars >= MIN_VERIFIED_LEGAL_CONTENT_CHARS
            and ocr_status not in {"failed", "unavailable", "empty", "pending", "partial"}
            and extraction.get("complete") is not False
            and not extraction.get("failed_pages")
        )
        if not extraction_complete:
            if ocr_status in {"failed", "unavailable", "empty", "pending", "partial"}:
                hard_gate_failures.append("Trích xuất/OCR chưa hoàn tất hoặc không dùng được.")
            elif chars < MIN_VERIFIED_LEGAL_CONTENT_CHARS:
                hard_gate_failures.append(
                    "Nội dung trích xuất chưa đủ "
                    f"{MIN_VERIFIED_LEGAL_CONTENT_CHARS} ký tự để kiểm tra pháp lý nghiêm ngặt."
                )
            else:
                hard_gate_failures.append("Trích xuất chưa đủ toàn bộ nội dung hoặc còn trang lỗi.")

        content_type_allowed = source_type == "document"
        if not content_type_allowed:
            hard_gate_failures.append(
                "Dữ liệu này không phải văn bản pháp luật; không được đưa vào kho căn cứ pháp lý."
            )
        if duplicate_count:
            hard_gate_failures.append(f"Có {duplicate_count} ứng viên trùng số hiệu, URL hoặc fingerprint chưa được xử lý.")
        if domain not in LEGAL_DOMAINS:
            hard_gate_failures.append("Chưa xác định được lĩnh vực phục vụ cấp phường/xã.")

        scores = {
            "nguon_chinh_thuc": 100 if source_verified else 0,
            "du_lieu_bat_buoc": 100 if not metadata_missing else max(0, 100 - len(metadata_missing) * 20),
            "hieu_luc_ap_dung": 100 if effectivity_verified else 0,
            "trich_xuat_noi_dung": 100 if extraction_complete else min(60, round(chars / 10)),
            "kiem_tra_trung_lap": 100 if not duplicate_count else 0,
            "phu_hop_cap_phuong_xa": 100 if domain in LEGAL_DOMAINS and scope in {"central", "haiphong", "local"} else 0,
        }
        if source_verified:
            evidence.append("Nguồn HTTPS chính thức đã được xác minh.")
        if domain in LEGAL_DOMAINS:
            evidence.append("Đã xác định lĩnh vực phục vụ theo quy tắc dữ liệu.")
        if extraction_complete:
            evidence.append(f"Đã có {chars} ký tự trích xuất đủ ngưỡng kiểm tra ban đầu.")
        elif pdf_kind == "scan" and ocr_status == "ok":
            evidence.append("PDF quét đã có OCR nhưng vẫn phải đối chiếu bản gốc trước khi duyệt.")
        evidence.extend(hard_gate_failures)

        snippets = []
        if preview:
            snippets.append({"kind": "trich_doan_noi_dung", "text": preview[:500]})
        if fingerprint:
            snippets.append({"kind": "dau_van_tay_noi_dung", "text": str(fingerprint)[:16]})
        passed_hard_gates = not hard_gate_failures
        if not content_type_allowed:
            action = "recommended_rejection"
        elif not passed_hard_gates:
            action = "hold_for_evidence"
        else:
            action = "manual_review_required"
        return {
            "kind": "strict_automatic_review_v1",
            "action": action,
            "scores": scores,
            "evidence": evidence,
            "hard_gate_failures": hard_gate_failures,
            "passed_hard_gates": passed_hard_gates,
            "evidence_snippets": snippets,
            "generated_at": _utcnow().isoformat(),
        }

    @classmethod
    async def get_candidate(cls, candidate_id: str) -> dict[str, Any] | None:
        """Get candidate by ID without running AI assessment."""
        if not candidate_id:
            return None
        if ":" not in candidate_id:
            candidate_id = f"legal_crawl_candidate:{candidate_id}"
        rows = await repo_query(
            "SELECT * FROM legal_crawl_candidate WHERE id = $id FETCH source",
            {"id": ensure_record_id(candidate_id)},
        )
        if not rows:
            return None
        return await cls._enrich_import_projection(rows[0])

    @classmethod
    async def _enrich_import_projection(cls, candidate: dict[str, Any]) -> dict[str, Any]:
        """Backfill read-only serving fields for records created before migration 46."""
        if not isinstance(candidate, dict):
            return candidate
        if candidate.get("vector_collection") is not None and candidate.get("chatbot_ready") is not None:
            return candidate
        job_ref = candidate.get("import_job")
        if not job_ref:
            return candidate
        try:
            rows = await repo_query(
                "SELECT * FROM legal_import_job WHERE id = $id LIMIT 1",
                {"id": ensure_record_id(job_ref)},
            )
            job = rows[0] if rows else {}
            embedding = job.get("embedding_result") or {}
            vector_collection = job.get("vector_collection") or embedding.get("vector_collection")
            if vector_collection is not None:
                candidate["vector_collection"] = vector_collection
            candidate["chatbot_ready"] = bool(
                str(candidate.get("status") or "").casefold() == "imported"
                and str(job.get("status") or "").casefold() == "completed"
                and int(candidate.get("chunk_count") or job.get("chunk_count") or 0) > 0
                and vector_collection
            )
        except Exception:
            # Projection enrichment is advisory; it must never hide the
            # candidate or make the review queue fail closed.
            return candidate
        return candidate

    @staticmethod
    def _normalized_law_number(value: Any) -> str:
        """Normalize a legal identifier for an exact, accent-insensitive comparison."""

        text = unicodedata.normalize("NFD", str(value or "").casefold())
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        text = text.replace("đ", "d")
        return re.sub(r"[^a-z0-9]", "", text)

    @staticmethod
    def _reconciliation_document_ids(
        candidate: Mapping[str, Any], job: Mapping[str, Any] | None,
    ) -> list[str]:
        """Return document references without trusting a single stale receipt field."""

        values: list[Any] = [
            candidate.get("document_id"),
            candidate.get("failed_document_id"),
            (candidate.get("imported_document") or {}).get("document_id")
            if isinstance(candidate.get("imported_document"), Mapping)
            else None,
            job.get("document_id") if isinstance(job, Mapping) else None,
        ]
        for receipt in (candidate.get("failed_import_result"), (job or {}).get("embedding_result")):
            if isinstance(receipt, Mapping):
                values.append(receipt.get("document_id"))
        result: list[str] = []
        for value in values:
            text = str(value if value is not None else "").strip()
            if not text:
                continue
            if text.startswith(("document:", "legal_document:")):
                text = text.rsplit(":", 1)[-1]
            if text not in result:
                result.append(text)
        return result

    @staticmethod
    async def _quarantine_missing_import_documents(
        candidate: Mapping[str, Any], job: Mapping[str, Any], job_id: str | None,
        document_ids: list[str], management_checks: list[dict[str, Any]], *, apply: bool,
    ) -> dict[str, Any]:
        """Keep evidence and remove only proven-orphan authority, atomically.

        SQL documents/vectors and the previous job result are never modified.
        The persisted snapshot also survives a failure of the router's actor audit.
        """
        before = deepcopy({key: candidate.get(key) for key in (
            "status", "review_status", "import_status", "pipeline_stage", "chatbot_ready",
            "document_id", "failed_document_id", "imported_document", "failed_import_result",
            "import_job", "updated", "reconciliation",
        )})
        receipt = {
            "schema_version": "legal-import-reconciliation-v1",
            "status": "missing_document_quarantined",
            "document_ids": document_ids,
            "management_checks": management_checks,
            "previous_imported_document": deepcopy(candidate.get("imported_document")),
            "previous_candidate_status": candidate.get("status"),
            "previous_job_status": job.get("status"),
            "previous_job_result": deepcopy(job.get("embedding_result")),
            "before": before,
            "reconciled_at": _utcnow(),
        }
        result = {
            "status": "missing_document_quarantined", "applied": False,
            "candidate_id": str(candidate["id"]), "job_id": job_id,
            "document_ids": document_ids, "before": before,
            "management_checks": management_checks,
        }
        if not apply:
            return result

        params: dict[str, Any] = {
            "id": ensure_record_id(candidate["id"]),
            "job_id": ensure_record_id(job_id) if job_id else None,
            "document_ids": document_ids, "receipt": receipt,
            "data": {
                "status": "changes_requested", "review_status": "changes_requested",
                "import_status": "failed", "pipeline_stage": "blocked", "chatbot_ready": False,
                "review_note": "Văn bản được tham chiếu không còn trong kho. Cần kiểm tra lại nguồn trước khi duyệt; kết quả nhập cũ được giữ trong lịch sử.",
                "updated": datetime.now(UTC),
                "reconciliation": receipt,
            },
        }

        def unchanged(record: Mapping[str, Any], fields: tuple[str, ...], prefix: str) -> str:
            clauses = []
            for field in fields:
                value = record.get(field)
                if value is None:
                    clauses.append(f"({field} = NONE OR {field} = NULL)")
                else:
                    key = f"{prefix}_{field}"
                    params[key] = ensure_record_id(value) if field in {"import_job", "candidate"} else value
                    clauses.append(f"{field} = ${key}")
            return " AND ".join(clauses)

        candidate_guard = unchanged(candidate, (
            "updated", "status", "imported_document", "document_id", "failed_document_id",
            "failed_import_result", "import_job",
        ), "expected")
        job_guard = unchanged(job, ("updated", "status", "document_id", "embedding_result", "candidate"), "job")
        # A missing job must remain missing; a supplied job must remain unchanged.
        job_check = (
            "LET $jobs = (SELECT id FROM legal_import_job WHERE id = $job_id "
            + (f"AND {job_guard}" if job else "")
            + "); IF array::len($jobs) != " + ("1" if job else "0")
            + " { THROW 'reconciliation_candidate_changed'; }; "
        )
        try:
            rows = await repo_query(
                "BEGIN TRANSACTION; "
                "LET $active_jobs = (SELECT id FROM legal_import_job "
                "WHERE (candidate = $id OR id = $job_id) AND status IN ['queued', 'running']); "
                "IF array::len($active_jobs) > 0 { THROW 'reconciliation_job_still_running'; }; "
                + job_check
                + "LET $authority_before = { "
                "assignments: (SELECT * FROM document_organization_assignment WHERE document_id IN $document_ids), "
                "relations: (SELECT * FROM document_organization_unit WHERE document_id IN $document_ids) }; "
                "LET $changed = (UPDATE legal_crawl_candidate "
                "MERGE $data "
                f"WHERE id = $id AND {candidate_guard} RETURN AFTER); "
                "IF array::len($changed) != 1 { THROW 'reconciliation_candidate_changed'; }; "
                "LET $saved = (UPDATE legal_crawl_candidate SET "
                "reconciliation.previous_document_authority = $authority_before "
                "WHERE id = $id RETURN AFTER); "
                # Persist the receipt and authority snapshot BEFORE either deletion.
                "DELETE document_organization_unit WHERE document_id IN $document_ids; "
                "DELETE document_organization_assignment WHERE document_id IN $document_ids; "
                # RETURN sets the transaction result (the SDK reads its first result).
                "RETURN $saved; COMMIT TRANSACTION;",
                params,
            )
        except RuntimeError as exc:
            if "reconciliation_job_still_running" in str(exc):
                return {**result, "status": "failed", "reason_code": "import_job_still_running"}
            if "reconciliation_candidate_changed" in str(exc):
                return {**result, "status": "failed", "reason_code": "candidate_changed"}
            raise
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise RuntimeError("Missing-document quarantine transaction was not confirmed")
        saved = rows[0].get("reconciliation") or {}
        if saved.get("status") != receipt["status"] or saved.get("document_ids") != document_ids:
            raise RuntimeError("Missing-document quarantine receipt was not confirmed")
        return {**result, "applied": True, "reconciliation": saved}

    @staticmethod
    def _validate_reconciliation_serving_state(
        candidate: Mapping[str, Any],
        payload: Mapping[str, Any],
        document_id: str,
    ) -> dict[str, Any]:
        """Validate the authoritative management response before any receipt rewrite."""

        document = payload.get("document")
        vectors = payload.get("vectors")
        document = document if isinstance(document, Mapping) else {}
        vectors = vectors if isinstance(vectors, Mapping) else {}
        failures: list[str] = []

        expected_law = LegalCrawlService._normalized_law_number(
            candidate.get("law_number")
            or (candidate.get("raw_metadata") or {}).get("law_number")
        )
        actual_law = LegalCrawlService._normalized_law_number(document.get("law_number"))
        if not expected_law or not actual_law or expected_law != actual_law:
            failures.append("law_number_mismatch")

        if str(document.get("doc_id") or document.get("document_id") or "").strip():
            actual_id = str(document.get("doc_id") or document.get("document_id"))
            if ":" in actual_id:
                actual_id = actual_id.rsplit(":", 1)[-1]
            if actual_id != str(document_id):
                failures.append("document_id_mismatch")
        else:
            failures.append("document_id_missing")

        if str(document.get("stored_status") or "").casefold() != "active":
            failures.append("document_not_active")
        if str(document.get("serving_state") or document.get("serving_status") or "").casefold() != "current_retrievable":
            failures.append("document_not_current_retrievable")
        if document.get("search_included") is not True:
            failures.append("document_excluded_from_search")
        if document.get("current_answer_eligible") is not True:
            failures.append("document_not_answer_eligible")

        # The management response has changed shape across releases. Accept
        # either per-collection flags or the top-level readiness projection,
        # but never infer readiness from a collection name alone.
        current_ready = vectors.get("current_retrieval_ready")
        if current_ready is None and isinstance(vectors.get("current"), Mapping):
            current_ready = vectors["current"].get("retrieval_ready")
        historical_ready = vectors.get("historical_retrieval_ready")
        if historical_ready is None and isinstance(vectors.get("temporal"), Mapping):
            historical_ready = vectors["temporal"].get("retrieval_ready")
        collections = vectors.get("collections")
        collections = collections if isinstance(collections, Mapping) else {}
        incremental = vectors.get("incremental")
        if not isinstance(incremental, Mapping):
            incremental = collections.get("incremental")
        incremental = incremental if isinstance(incremental, Mapping) else {}
        if current_ready is not True:
            failures.append("current_vectors_not_ready")
        if historical_ready is not True:
            failures.append("historical_vectors_not_ready")
        try:
            expected_chunks = int(
                vectors.get("expected")
                or incremental.get("expected")
                or document.get("chunk_count")
                or 0
            )
            present_chunks = int(incremental.get("present") or 0)
            missing_chunks = int(incremental.get("missing") or max(expected_chunks - present_chunks, 0))
        except (TypeError, ValueError):
            expected_chunks = present_chunks = missing_chunks = 0
        if expected_chunks <= 0 or present_chunks != expected_chunks or missing_chunks != 0:
            failures.append("incremental_vector_membership_incomplete")
        try:
            document_chunks = int(document.get("chunk_count") or 0)
        except (TypeError, ValueError):
            document_chunks = 0
        if document_chunks and expected_chunks != document_chunks:
            failures.append("chunk_count_mismatch")

        return {
            "passed": not failures,
            "document_id": str(document_id),
            "law_number": document.get("law_number"),
            "chunk_count": expected_chunks,
            "vector_collection": document.get("vector_collection")
            or vectors.get("vector_collection")
            or "legal_chunks_admin_approved_local_v1",
            "failures": failures,
            "serving_state": {
                "stored_status": document.get("stored_status"),
                "serving_state": document.get("serving_state") or document.get("serving_status"),
                "search_included": document.get("search_included"),
                "current_answer_eligible": document.get("current_answer_eligible"),
                "current_retrieval_ready": current_ready,
                "historical_retrieval_ready": historical_ready,
                "incremental_expected": expected_chunks,
                "incremental_present": present_chunks,
                "incremental_missing": missing_chunks,
            },
        }

    @classmethod
    async def reconcile_import_projection(
        cls,
        candidate_id: str,
        *,
        job_id: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Reconcile a stale import receipt only after live serving verification.

        This is intentionally an explicit operation. It never turns a failed
        import into success from SQL fields alone: the authoritative lifecycle
        endpoint and the real Retrieval V2 exact/semantic probes must both pass.
        The previous failure receipt remains embedded in the audit fields.
        Imported receipts with only authoritative document_not_found responses
        may instead be quarantined; ambiguous or mixed responses never mutate.
        """

        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            return {"status": "failed", "reason_code": "candidate_not_found"}
        candidate_status = str(candidate.get("status") or "").casefold()
        if candidate_status not in {"import_failed", "imported"}:
            return {
                "status": "failed",
                "reason_code": "candidate_state_not_reconcilable",
                "candidate_status": candidate_status,
            }

        job: dict[str, Any] = {}
        resolved_job_id = job_id or str(candidate.get("import_job") or "").strip() or None
        if resolved_job_id:
            rows = await repo_query(
                "SELECT * FROM legal_import_job WHERE id = $id LIMIT 1;",
                {"id": ensure_record_id(resolved_job_id)},
            )
            job = rows[0] if rows else {}
        else:
            rows = await repo_query(
                "SELECT * FROM legal_import_job WHERE candidate = $candidate ORDER BY created_at DESC LIMIT 1;",
                {"candidate": ensure_record_id(candidate["id"])},
            )
            job = rows[0] if rows else {}
            resolved_job_id = str(job.get("id") or "").strip() or None
        if str(job.get("status") or "").casefold() in {"queued", "running"}:
            return {"status": "failed", "reason_code": "import_job_still_running", "job_id": resolved_job_id}
        if candidate_status == "imported" and (
            (job.get("candidate") and str(job["candidate"]) != str(candidate["id"]))
            or (job_id and candidate.get("import_job") and str(candidate["import_job"]) != job_id)
        ):
            return {"status": "failed", "reason_code": "import_job_candidate_mismatch"}

        document_ids = cls._reconciliation_document_ids(candidate, job)
        if candidate_status == "imported" and (
            not document_ids or any(not re.fullmatch(r"[1-9][0-9]*", doc_id) for doc_id in document_ids)
        ):
            return {"status": "failed", "reason_code": "document_reference_missing_or_invalid"}
        candidate_law_number = str(
            candidate.get("law_number")
            or (candidate.get("raw_metadata") or {}).get("law_number")
            or ""
        ).strip()
        if not document_ids and not candidate_law_number:
            return {
                "status": "failed",
                "reason_code": "document_reference_missing",
                "job_id": resolved_job_id,
            }

        management_checks: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            if not document_ids:
                # Older failed jobs did not persist the document id because the
                # field did not exist yet. Resolve it through the authoritative
                # management index, then still require the detail endpoint
                # below; a law-number search result alone is never enough.
                try:
                    lookup_response = await client.get(
                        f"{LEGAL_MANAGEMENT_URL}/management/documents",
                        params={"q": candidate_law_number, "limit": 100},
                    )
                    lookup_payload = lookup_response.json() if lookup_response.content else {}
                    items = lookup_payload.get("items") if isinstance(lookup_payload, Mapping) else []
                    if isinstance(items, list):
                        document_ids = [
                            str(item.get("doc_id") or item.get("document_id"))
                            for item in items
                            if isinstance(item, Mapping)
                            and cls._normalized_law_number(item.get("law_number"))
                            == cls._normalized_law_number(candidate_law_number)
                            and str(item.get("doc_id") or item.get("document_id") or "").strip()
                        ]
                except Exception as exc:
                    management_checks.append({
                        "passed": False,
                        "failures": [f"management_lookup_{exc.__class__.__name__}"],
                    })
            if not document_ids:
                return {
                    "status": "failed",
                    "reason_code": "document_reference_missing",
                    "job_id": resolved_job_id,
                    "management_checks": management_checks,
                }
            for document_id in document_ids:
                try:
                    response = await client.get(
                        f"{LEGAL_MANAGEMENT_URL}/management/documents/{document_id}"
                    )
                    payload = response.json() if response.content else {}
                    if response.status_code != 200 or not isinstance(payload, Mapping):
                        detail = payload.get("detail") if isinstance(payload, Mapping) else None
                        management_checks.append({
                            "document_id": document_id,
                            "passed": False,
                            "confirmed_missing": response.status_code == 404
                            and isinstance(detail, Mapping) and detail.get("code") == "document_not_found",
                            "failures": [f"management_http_{response.status_code}"],
                        })
                        continue
                    check = cls._validate_reconciliation_serving_state(
                        candidate, payload, document_id
                    )
                    management_checks.append(check)
                except Exception as exc:
                    management_checks.append({
                        "document_id": document_id,
                        "passed": False,
                        "failures": [f"management_{exc.__class__.__name__}"],
                    })

        if candidate_status == "imported":
            if len(management_checks) == len(document_ids) and all(
                check.get("confirmed_missing") is True for check in management_checks
            ):
                return await cls._quarantine_missing_import_documents(
                    candidate, job, resolved_job_id, document_ids, management_checks, apply=apply,
                )
        # Neither recovery nor quarantine may rewrite a partially verified receipt.
        if any(not check.get("passed") for check in management_checks):
            return {
                "status": "failed", "reason_code": "serving_state_not_reconciled",
                "job_id": resolved_job_id, "management_checks": management_checks,
            }

        serving_check = next(
            (check for check in management_checks if check.get("passed")), None
        )
        if not serving_check:
            return {
                "status": "failed",
                "reason_code": "serving_state_not_reconciled",
                "job_id": resolved_job_id,
                "management_checks": management_checks,
            }

        content = str(candidate.get("content") or "").strip()
        if not content:
            return {
                "status": "failed",
                "reason_code": "candidate_content_missing_for_smoke",
                "job_id": resolved_job_id,
                "management_checks": management_checks,
            }
        raw_metadata = candidate.get("raw_metadata") or {}
        smoke = await verify_post_activation_retrieval(
            document_id=serving_check["document_id"],
            law_number=str(candidate.get("law_number") or raw_metadata.get("law_number") or ""),
            title=str(candidate.get("title") or ""),
            content=content,
            domain=str(candidate.get("domain") or raw_metadata.get("domain") or "") or None,
            base_url=LEGAL_SEARCH_URL,
        )
        if not smoke.get("passed"):
            return {
                "status": "failed",
                "reason_code": "retrieval_smoke_not_reconciled",
                "job_id": resolved_job_id,
                "management_check": serving_check,
                "retrieval_smoke": smoke,
            }

        receipt = {
            "schema_version": "legal-import-reconciliation-v1",
            "status": "passed",
            "document_id": serving_check["document_id"],
            "management": serving_check,
            "retrieval_smoke": smoke,
            "previous_candidate_status": candidate_status,
            "previous_job_status": job.get("status"),
            "reconciled_at": _utcnow(),
        }
        result: dict[str, Any] = {
            "status": "verified",
            "applied": bool(apply),
            "candidate_id": str(candidate["id"]),
            "job_id": resolved_job_id,
            "document_id": int(serving_check["document_id"])
            if str(serving_check["document_id"]).isdigit()
            else serving_check["document_id"],
            "chunk_count": serving_check["chunk_count"],
            "vector_collection": serving_check["vector_collection"],
            "management_check": serving_check,
            "retrieval_smoke": smoke,
        }
        if not apply:
            return result

        imported_document = dict(candidate.get("imported_document") or {})
        imported_document.update({
            "document_id": result["document_id"],
            "law_number": candidate.get("law_number") or raw_metadata.get("law_number"),
            "chunk_count": serving_check["chunk_count"],
            "status": "embedded_active",
            "activation_status": "active",
            "vector_collection": serving_check["vector_collection"],
            "retrieval_smoke": smoke,
        })
        candidate_update = {
            "status": "imported",
            "review_status": "imported",
            "import_status": "completed",
            "pipeline_stage": "active",
            "preparation_status": "completed",
            "blockers": [],
            "document_id": result["document_id"],
            "chunk_count": serving_check["chunk_count"],
            "indexed_at": _utcnow(),
            "vector_collection": serving_check["vector_collection"],
            "chatbot_ready": True,
            "imported_document": imported_document,
            "reconciliation": receipt,
            "review_note": "Đã đối soát lại receipt cũ với trạng thái phục vụ, vector membership và Retrieval V2; giữ nguyên lỗi cũ trong audit.",
        }
        await repo_update("legal_crawl_candidate", candidate["id"], candidate_update)
        if resolved_job_id:
            embedding_result = dict(job.get("embedding_result") or {})
            embedding_result["reconciliation"] = receipt
            await repo_update(
                "legal_import_job",
                resolved_job_id,
                {
                    "status": "completed",
                    "completed_at": _utcnow(),
                    "document_id": str(serving_check["document_id"]),
                    "chunk_count": serving_check["chunk_count"],
                    "vector_collection": serving_check["vector_collection"],
                    "pipeline_stage": "active",
                    "reconciliation": receipt,
                    "embedding_result": embedding_result,
                    "error_reason": None,
                },
            )
        return result

    @classmethod
    async def persist_automatic_assessment(cls, candidate: dict[str, Any]) -> dict[str, Any]:
        """Persist immediate strict guidance without changing legal-review state.

        This path is local and deterministic so a crawl does not wait for an
        optional model. A manual retry may enrich it later, but the evidence
        gates are present the moment a candidate is created or re-extracted.
        """
        recommendation = cls.build_review_recommendation(candidate)
        candidate_id = candidate.get("id")
        if candidate_id:
            await repo_update(
                "legal_crawl_candidate",
                candidate_id,
                {"review_recommendation": recommendation, "assessment_updated_at": _utcnow()},
            )
        candidate["review_recommendation"] = recommendation
        return candidate

    @staticmethod
    def validate_candidate_for_import(
        candidate: dict[str, Any], *, require_approved: bool = True
    ) -> list[str]:
        """Validate approved candidate metadata before it can reach legal RAG."""
        raw = candidate.get("raw_metadata") or {}
        errors: list[str] = []
        source_type = str(candidate.get("source_type") or raw.get("source_type") or "document")
        if source_type == "form":
            errors.append("Biểu mẫu cần được duyệt tại mục Quản lý biểu mẫu, không nhập như văn bản pháp luật.")
        elif source_type == "procedure":
            errors.append(
                "Thủ tục hành chính không được nhập như văn bản pháp luật; "
                "hãy đối chiếu và duyệt qua kho thủ tục."
            )
        review_status = str(candidate.get("review_status") or candidate.get("status") or "").strip().lower()
        if require_approved and review_status not in {"approved", "import_failed", "import_queued"}:
            errors.append("Đề xuất chưa được quản trị viên duyệt để nhập kho.")
        title = str(candidate.get("title") or "").strip()
        if not title:
            errors.append("Thiếu tên văn bản.")
        elif _repair_mojibake_text(title) != title:
            errors.append("Tên văn bản bị lỗi hiển thị chữ; hãy đối chiếu và sửa từ nguồn gốc.")
        if not str(candidate.get("law_number") or "").strip():
            errors.append("Thiếu số hiệu văn bản đã xác minh.")
        if not str(candidate.get("document_type") or raw.get("document_type") or "").strip():
            errors.append("Thiếu loại văn bản đã xác minh.")
        if not str(candidate.get("issuing_agency") or raw.get("issuing_agency") or "").strip():
            errors.append("Thiếu cơ quan ban hành đã xác minh.")
        try:
            date.fromisoformat(str(raw.get("issued_date")))
        except (TypeError, ValueError):
            errors.append("Thiếu hoặc sai ngày ban hành. Hãy chọn ngày đúng theo văn bản gốc.")
        if not bool(raw.get("confirmed_official_source")):
            errors.append("Nguồn văn bản chính thức chưa được xác nhận.")
        source_url = str(candidate.get("source_url") or raw.get("source_url") or "").strip()
        from api.legal_source_input import valid_source_reference
        if not valid_source_reference(source_url) and not re.fullmatch(r'[a-f0-9]{64}', str(raw.get('uploaded_pdf_sha256') or '')):
            errors.append("Bổ sung liên kết HTTP/HTTPS hoặc tệp gốc để người duyệt đối chiếu.")
        scope = _canonical_scope(candidate.get("scope") or raw.get("scope")) or ""
        if scope not in {"central", "haiphong", "local"}:
            errors.append("Hãy chọn phạm vi áp dụng: Trung ương, tỉnh/thành phố hoặc phường/xã.")
        effective_date = raw.get("effective_date")
        try:
            effective = date.fromisoformat(str(effective_date))
            if effective > date.today():
                errors.append("Văn bản chưa có hiệu lực tại thời điểm nhập kho.")
        except (TypeError, ValueError):
            errors.append("Thiếu hoặc sai ngày có hiệu lực. Hãy chọn ngày đúng theo văn bản gốc.")
        expired_date = raw.get("expired_date")
        if expired_date:
            try:
                if date.fromisoformat(str(expired_date)) <= date.today():
                    errors.append("Văn bản đã hết hiệu lực.")
            except ValueError:
                errors.append("Ngày hết hiệu lực không hợp lệ. Hãy chọn lại ngày.")
        extraction = candidate.get("extraction_result") or {}
        ocr_status = str(extraction.get("ocr_status") or raw.get("ocr_status") or "not_required")
        if ocr_status in {"failed", "unavailable", "empty", "pending", "partial"}:
            errors.append("Nhận dạng chữ hoặc đọc tệp chưa hoàn tất; hãy đối chiếu tệp gốc trước khi nhập kho.")
        processed_pages = int(extraction.get("processed_pages") or 0)
        total_pages = int(extraction.get("total_pages") or 0)
        if total_pages and (
            processed_pages != total_pages
            or extraction.get("complete") is False
            or extraction.get("failed_pages")
        ):
            errors.append(
                "Chưa đọc đủ toàn bộ số trang; đề xuất được giữ lại và chưa thể nhập kho."
            )
        content = str(candidate.get("content") or "").strip()
        if len(content) < MIN_VERIFIED_LEGAL_CONTENT_CHARS:
            errors.append(
                "Nội dung trích xuất chưa đủ "
                f"{MIN_VERIFIED_LEGAL_CONTENT_CHARS} ký tự để lập dữ liệu tra cứu."
            )
        elif _repair_mojibake_text(content) != content:
            errors.append("Nội dung bị lỗi hiển thị chữ; hãy đối chiếu nguồn gốc trước khi nhập kho.")
        if candidate.get("duplicate_candidates"):
            errors.append("Có đề xuất nghi trùng; quản trị viên cần đối chiếu trước khi nhập kho.")
        return errors

    @classmethod
    async def prepare_candidate_for_import(cls, candidate_id: str) -> dict[str, Any]:
        """Fetch and validate official evidence before recording approval."""

        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Không tìm thấy candidate.")
        if candidate.get("status") not in {"pending", "changes_requested", "approved", "import_failed", "import_queued"}:
            raise CandidateChanged("Candidate đã xử lý hoặc đã lưu trữ; không được tự mở lại để nhập.")
        source_url = str(
            candidate.get("source_url")
            or candidate.get("detail_url")
            or (candidate.get("raw_metadata") or {}).get("source_url")
            or ""
        ).strip()
        raw = dict(candidate.get("raw_metadata") or {})
        candidate_source = candidate.get("source")
        source_compatibility = (
            candidate_source.get("compatibility_mode")
            if isinstance(candidate_source, Mapping) else "standard"
        )
        canonical_scope = _canonical_scope(
            candidate.get("scope") or raw.get("scope")
        ) or _detect_scope(
            candidate.get("law_number"),
            str(candidate.get("title") or ""),
            str(candidate.get("content") or ""),
            source_url,
        )
        candidate = await update_candidate_if_current(
            candidate,
            {
                "pipeline_stage": "fetching_source",
                "preparation_status": "running",
                "blockers": [],
                "preparation_started_at": _utcnow(),
            },
        )
        normalized: dict[str, Any] | None = None
        fetch_reason: str | None = None
        uploaded = candidate.get("uploaded_file") or {}
        has_uploaded_source = bool(
            str(uploaded.get("path") or "").strip()
            and str(uploaded.get("sha256") or uploaded.get("file_fingerprint") or "").strip()
        )
        # A listing excerpt (including a VBPL Server Action payload) is
        # discovery metadata, never authoritative full text.  Every URL-based
        # approval/retry therefore refreshes the detail page with Crawl4AI,
        # regardless of the excerpt length or its legacy ``complete`` flag.
        # Uploaded files follow the local extraction/OCR path and must not be
        # silently replaced by a web response.
        manually_reviewed_content = bool(
            raw.get('candidate_origin') == 'admin_manual_import'
            and raw.get('confirmed_official_source')
            and str(candidate.get('content') or '').strip()
        )
        needs_fetch = not has_uploaded_source and not manually_reviewed_content and bool(source_url)
        if needs_fetch and source_url:
            try:
                normalized = await fetch_normalized_legal_document(
                    source_url,
                    scope=canonical_scope,
                    timeout_seconds=45,
                    **({"compatibility_mode": "high"} if source_compatibility == "high" else {}),
                )
            except Exception as exc:
                logger.warning("Candidate source refresh failed: {}", exc.__class__.__name__)
                fetch_reason = "Chưa tải lại được văn bản từ nguồn chính thức. Hãy thử lại hoặc tải tệp gốc để đối chiếu."

        # Stored excerpts and old verification flags cannot prove that this
        # refresh succeeded. Keep them for review, never as approval evidence.
        fresh_content = str((normalized or {}).get("clean_markdown") or "").strip()
        fresh_extraction = (normalized or {}).get("extraction") or {}
        normalized_has_evidence = bool(
            needs_fetch
            and normalized
            and normalized.get("status") in {"ok", "ready", "needs_review"}
            and fresh_content
            and fresh_extraction.get("complete") is not False
        )
        if needs_fetch and not normalized_has_evidence:
            raw["confirmed_official_source"] = False
            raw.pop("source_verified_at", None)
            raw.pop("source_verification_method", None)
            fetch_reason = fetch_reason or (
                "Chưa đọc được toàn văn hợp lệ từ đường dẫn nguồn. "
                "Hãy kiểm tra đường dẫn văn bản cụ thể, quét lại hoặc tải tệp gốc để đối chiếu."
            )
            normalized = None

        listing_context = str(raw.get("listing_context") or candidate.get("description") or "")
        listing_effective_date = _labelled_listing_date(
            listing_context, "Ngày hiệu lực", "Ngày có hiệu lực"
        )
        listing_issued_date = _labelled_listing_date(
            listing_context, "Ngày ban hành", "Ngày ký"
        )
        content = str((normalized or {}).get("clean_markdown") or candidate.get("content") or "").strip()
        extraction = dict(
            (normalized or {}).get("extraction")
            or candidate.get("extraction_result")
            or {}
        )
        extraction["characters"] = len(content)
        final_url = str((normalized or {}).get("final_url") or source_url).strip()
        if normalized_has_evidence:
            raw["confirmed_official_source"] = True
            raw["source_verified_at"] = _utcnow().isoformat()
            raw["source_verification_method"] = str(
                extraction.get("official_payload") or extraction.get("method") or "crawl4ai"
            )
        raw.update(
            {
                "source_url": final_url or source_url,
                "scope": canonical_scope,
                "issued_date": (normalized or {}).get("issued_date")
                or raw.get("issued_date")
                or listing_issued_date,
                "effective_date": (normalized or {}).get("effective_date")
                or raw.get("effective_date")
                or listing_effective_date,
                "expired_date": (normalized or {}).get("expired_date")
                or raw.get("expired_date"),
                "document_type": (normalized or {}).get("document_type")
                or raw.get("document_type")
                or candidate.get("document_type"),
                "issuing_agency": (normalized or {}).get("issuing_agency")
                or raw.get("issuing_agency")
                or candidate.get("issuing_agency"),
                "metadata_only": not bool(content),
                "preparation_updated_at": _utcnow().isoformat(),
            }
        )
        updates = {
            "title": (normalized or {}).get("title") or candidate.get("title"),
            "law_number": (normalized or {}).get("law_number") or candidate.get("law_number"),
            "document_type": raw.get("document_type"),
            "issuing_agency": raw.get("issuing_agency"),
            "source_url": final_url or source_url,
            "detail_url": final_url or source_url,
            "scope": canonical_scope,
            "content": content or None,
            "content_hash": (normalized or {}).get("content_hash") or candidate.get("content_hash"),
            "extraction_result": extraction,
            "raw_metadata": raw,
            "pipeline_stage": "validating",
            "preparation_status": "validating",
            "preparation_updated_at": _utcnow(),
        }
        merged = {**candidate, **updates}
        blockers = cls.validate_candidate_for_import(merged, require_approved=False)
        if fetch_reason:
            blockers.insert(0, fetch_reason)
        recommendation = cls.build_review_recommendation(merged)
        if blockers:
            note = "Cần bổ sung trước khi duyệt: " + " | ".join(dict.fromkeys(blockers))
            updates.update(
                {
                    "status": "changes_requested",
                    "review_status": "changes_requested",
                    "import_status": "preflight_blocked",
                    "pipeline_stage": "blocked",
                    "preparation_status": "blocked",
                    "blockers": list(dict.fromkeys(blockers)),
                    "review_note": note[:2000],
                    "requested_changes_note": note[:2000],
                    "review_recommendation": recommendation,
                    "approval_attempted_at": _utcnow(),
                }
            )
        else:
            updates.update(
                {
                    "pipeline_stage": "validated",
                    "preparation_status": "ready",
                    "import_status": None,
                    "blockers": [],
                    "review_recommendation": recommendation,
                }
            )
        raw["legal_identity"] = identity_metadata(merged)
        return await update_candidate_if_current(candidate, updates)

    @staticmethod
    async def find_runtime_document_conflict(
        candidate: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a business-identity conflict in any warehouse lifecycle state.

        This is preflight only. A timeout or unavailable retrieval service is
        never treated as proof that a document is new; the import worker keeps
        the final duplicate enforcement.
        """
        try:
            documents = await warehouse_duplicate_rows([candidate], LEGAL_MANAGEMENT_URL)
            for document in documents:
                match = public_duplicate_match(candidate, document, "document")
                if match:
                    return {**document, **match}
        except DuplicateCheckUnavailable:
            logger.warning("Warehouse duplicate preflight unavailable; final import guard remains required")
            return None
        return None

    @staticmethod
    async def find_runtime_document_conflicts(
        candidates: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Candidate-keyed matches: a law number is not a unique identity."""
        documents = await warehouse_duplicate_rows(candidates, LEGAL_MANAGEMENT_URL)
        result = {}
        for candidate in candidates:
            for document in documents:
                if match := public_duplicate_match(candidate, document, "document"):
                    result[str(candidate.get("id") or "")] = {**document, **match}
                    break
        return result

    @classmethod
    async def mark_runtime_document_conflict(
        cls,
        candidate: dict[str, Any],
        runtime_conflict: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep a duplicate out of the import queue with an auditable reason."""
        candidate_id = str(candidate.get("id") or "").strip()
        if not candidate_id:
            raise ValueError("Candidate thiếu định danh để ghi nhận xung đột trùng lặp.")
        conflict_kind = str(runtime_conflict.get("kind") or "identity_match")
        conflict_label = {
            "duplicate_content": "trùng toàn văn",
            "content_changed": "cùng định danh nhưng toàn văn đã thay đổi",
            "metadata_conflict": "metadata định danh mâu thuẫn",
            "identity_match": "cùng định danh nhưng chưa đối chiếu đủ toàn văn",
        }.get(conflict_kind, "có quan hệ định danh cần đối chiếu")
        conflict_note = (
            f"Phát hiện văn bản trong kho {conflict_label}: "
            f"{runtime_conflict.get('law_number') or candidate.get('law_number')} "
            f"(ID {runtime_conflict.get('id')}). Hãy chọn dùng bản hiện có "
            "hoặc đối chiếu quan hệ sửa đổi/thay thế trước khi duyệt nhập."
        )
        raw = dict(candidate.get("raw_metadata") or {})
        raw["duplicate_runtime_document"] = runtime_conflict
        updated = await update_candidate_if_current(
            candidate,
            {
                "status": "changes_requested",
                "review_status": "changes_requested",
                "import_status": "duplicate_conflict",
                "review_note": conflict_note,
                "review_reason": conflict_note,
                "requested_changes_note": conflict_note,
                "pipeline_stage": "blocked",
                "raw_metadata": raw,
            },
        )
        return {
            **updated,
            "duplicate_runtime_document": runtime_conflict,
        }

    @classmethod
    async def assess_candidate(cls, candidate: dict, force: bool = False) -> dict:
        """Use LLM to assess candidate with structured review fields.
        
        Returns ai_assessment with:
        - domain: slug lĩnh vực
        - scope: central|haiphong|local
        - official_level: official|reference|internal
        - effective_status: con_hieu_luc|het_hieu_luc|chua_co_hieu_luc|khong_ro
        - duplicate_risk: none|possible|likely
        - confidence: 0.0-1.0
        - reasons: list of Vietnamese strings explaining the assessment
        
        AI does NOT auto-approve - it only suggests.
        """
        raw_metadata = candidate.get("raw_metadata") or {}
        existing_assessment = candidate.get("ai_assessment") or raw_metadata.get("ai_assessment")
        
        # Return cached assessment unless forced
        if existing_assessment and not force:
            candidate["ai_assessment"] = existing_assessment
            # Keep deterministic routing visible to legacy callers. The AI
            # classification is exposed separately as a non-authoritative hint.
            candidate["inferred_domain"] = candidate.get("domain")
            candidate["ai_suggested_domain"] = existing_assessment.get("domain")
            candidate["suitability_recommendation"] = "; ".join(existing_assessment.get("reasons") or [])
            candidate["review_recommendation"] = candidate.get("review_recommendation") or cls.build_review_recommendation(candidate)
            return candidate
            
        content_text = candidate.get("content") or ""
        title = candidate.get("title") or ""
        law_number = candidate.get("law_number") or ""
        
        recommendation = cls.build_review_recommendation(candidate)
        if not content_text:
            return await cls.persist_automatic_assessment(candidate)
            
        # Construct enhanced prompt
        prompt = f"""Bạn là trợ lý đánh giá hồ sơ văn bản hành chính Việt Nam.
Chỉ phân tích metadata và đoạn nội dung dưới đây. Trả về JSON thuần, bằng tiếng Việt.

Tên: {title}
Số, ký hiệu: {law_number}
Phạm vi: {candidate.get('scope', 'unknown')}
Đoạn nội dung:
{content_text[:3000]}

Đánh giá theo hướng nghiêm ngặt: phải có nguồn HTTPS chính thức đã xác minh,
đủ tên/số hiệu/loại/cơ quan/phạm vi, bằng chứng hiệu lực, trích xuất hoàn chỉnh
và không còn trùng lặp chưa xử lý. Nếu thiếu dữ liệu phải nêu rõ bằng tiếng Việt
và dùng độ tin cậy thấp. candidate.domain theo quy tắc là dữ liệu định tuyến có
thẩm quyền; đề xuất của bạn không được tự ghi đè.

Giá trị domain hợp lệ: ho_tich_chung_thuc, dat_dai_xay_dung,
an_sinh_y_te_giao_duc, hanh_chinh_cong, trat_tu_do_thi.
scope: central, haiphong, local. official_level: official, reference, internal.
effective_status: con_hieu_luc, het_hieu_luc, chua_co_hieu_luc, khong_ro.
duplicate_risk: none, possible, likely.
Schema: {{
  "domain": "...", "scope": "...", "official_level": "...",
  "effective_status": "...", "duplicate_risk": "...",
  "confidence": 0.0, "reasons": ["lý do ngắn bằng tiếng Việt"]
}}

Không bịa số hiệu, ngày hiệu lực, tình trạng pháp lý, thời hạn, lệ phí hoặc cơ quan ban hành.
Không phê duyệt, công bố hoặc yêu cầu nhập kho. Không dùng Markdown.
"""

        assessment = None
        try:
            model = await provision_langchain_model(
                content=prompt,
                model_id=None,
                default_type="chat",
                temperature=0.1
            )
            response = await model.ainvoke(prompt)
            text_response = response.content if hasattr(response, "content") else str(response)
            
            assessment = cls._parse_ai_assessment(text_response)
        except Exception as e:
            logger.error(f"Failed to run AI assessment for candidate: {e}")
            
        if assessment:
            # Save in dedicated field
            update_data: dict[str, Any] = {
                "ai_assessment": assessment,
                "review_recommendation": recommendation,
            }
            
            # Also update raw_metadata for backward compat
            raw_metadata["ai_assessment"] = assessment
            # AI classification is deliberately stored as a suggestion. It must
            # not overwrite deterministic domain routing for candidate visibility
            # or official import.
            raw_metadata["ai_suggested_domain"] = assessment.get("domain")
            raw_metadata["suitability_recommendation"] = "; ".join(assessment.get("reasons") or [])
            update_data["raw_metadata"] = raw_metadata
            
            await repo_update(
                "legal_crawl_candidate",
                candidate["id"],
                update_data
            )
            candidate["ai_assessment"] = assessment
            candidate["raw_metadata"] = raw_metadata
            candidate["inferred_domain"] = candidate.get("domain")
            candidate["ai_suggested_domain"] = assessment.get("domain")
            candidate["suitability_recommendation"] = "; ".join(assessment.get("reasons") or [])
            logger.info(f"AI assessed candidate {candidate['id']}: domain={assessment.get('domain')}, confidence={assessment.get('confidence')}")
        else:
            await repo_update(
                "legal_crawl_candidate",
                candidate["id"],
                {"review_recommendation": recommendation},
            )
        candidate["review_recommendation"] = recommendation
        
        return candidate

    @staticmethod
    def _parse_ai_assessment(text_response: str) -> dict[str, Any] | None:
        """Parse structured AI assessment from LLM response. Handles markdown wrapping and malformed JSON."""
        import json as _json
        
        json_str = text_response.strip()
        # Strip markdown code blocks
        if "```" in json_str:
            parts = json_str.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):
                    json_str = stripped[4:].strip()
                    break
                elif stripped.startswith("{"):
                    json_str = stripped
                    break
        
        # Try standard parse
        try:
            data = _json.loads(json_str)
        except Exception:
            # Regex fallback for each field
            data = {}
            import re
            domain_m = re.search(r'"domain"\s*:\s*"([^"]+)"', json_str)
            scope_m = re.search(r'"scope"\s*:\s*"([^"]+)"', json_str)
            level_m = re.search(r'"official_level"\s*:\s*"([^"]+)"', json_str)
            status_m = re.search(r'"effective_status"\s*:\s*"([^"]+)"', json_str)
            dup_m = re.search(r'"duplicate_risk"\s*:\s*"([^"]+)"', json_str)
            conf_m = re.search(r'"confidence"\s*:\s*([\d.]+)', json_str)
            reasons_m = re.search(r'"reasons"\s*:\s*\[([^\]]*)\]', json_str)
            
            if domain_m:
                data["domain"] = domain_m.group(1)
            if scope_m:
                data["scope"] = scope_m.group(1)
            if level_m:
                data["official_level"] = level_m.group(1)
            if status_m:
                data["effective_status"] = status_m.group(1)
            if dup_m:
                data["duplicate_risk"] = dup_m.group(1)
            if conf_m:
                try:
                    data["confidence"] = float(conf_m.group(1))
                except ValueError:
                    pass
            if reasons_m:
                raw_reasons = reasons_m.group(1)
                data["reasons"] = [r.strip().strip('"').strip("'") for r in raw_reasons.split(",") if r.strip()]
        
        # Validate required fields
        domain = data.get("domain")
        if not domain or domain not in LEGAL_DOMAINS:
            return None
        
        # Normalize and fill defaults
        return {
            "domain": domain,
            "scope": data.get("scope", "central"),
            "official_level": data.get("official_level", "reference"),
            "effective_status": data.get("effective_status", "khong_ro"),
            "duplicate_risk": data.get("duplicate_risk", "none"),
            "confidence": min(1.0, max(0.0, float(data.get("confidence", 0.5)))),
            "reasons": data.get("reasons") or [],
        }

    @classmethod
    def _persist_approved_source_asset(
        cls, candidate: dict[str, Any], document_id: str | int
    ) -> dict[str, Any] | None:
        """Copy an admin-verified uploaded source into the active asset store.

        Listing candidates do not have a local file by design. A source file is
        copied only after the legal-search import has embedded successfully.
        """
        uploaded = candidate.get("uploaded_file") or {}
        raw_path = str(uploaded.get("path") or "").strip()
        if not raw_path:
            return None
        uploads_root = (Path("data") / "uploads").resolve()
        source_path = Path(raw_path).resolve()
        if not source_path.is_file() or uploads_root not in source_path.parents:
            return None
        if source_path.stat().st_size > 25 * 1024 * 1024:
            return None
        extension = source_path.suffix.lower()
        if extension not in {".pdf", ".doc", ".docx", ".txt"}:
            return None
        target_root = uploads_root / "legal_sources"
        target_root.mkdir(parents=True, exist_ok=True)
        target_path = target_root / f"{document_id}{extension}"
        shutil.copy2(source_path, target_path)
        return {
            "path": str(target_path),
            "filename": str(uploaded.get("filename") or source_path.name),
            "content_type": str(uploaded.get("content_type") or "application/octet-stream"),
            "sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
            "bytes": target_path.stat().st_size,
            "is_original": extension == ".pdf",
        }

    @staticmethod
    async def _exclude_failed_activation(
        document_id: Any,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Keep an imported-but-unretrievable document out of live answers."""

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{LEGAL_MANAGEMENT_URL}/documents/{document_id}/search-state",
                    json={
                        "action": "exclude",
                        "requested_by": "post-activation-smoke",
                        "reason": reason[:1000],
                    },
                )
            if response.status_code != 200:
                return {
                    "status": "failed",
                    "http_status": response.status_code,
                }
            payload = response.json() if response.content else {}
            return {
                "status": "excluded",
                "document_id": str(document_id),
                "result": payload if isinstance(payload, dict) else {},
            }
        except Exception as exc:
            return {"status": "failed", "error_class": exc.__class__.__name__}

    @classmethod
    async def import_candidate(
        cls,
        candidate_id: str,
        notebook_id: str | None = None,
        embed: bool = True,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Run approved candidate normalization/import/embedding atomically.

        Legal-search creates the document as ``staging`` and only changes it to
        active after both vector collections accept embeddings. No secondary
        notebook embedding participates in activation; it is audit-only.
        """
        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found")
        if candidate.get("status") not in {"approved", "import_failed", "import_queued"}:
            raise ValueError("Candidate phải được admin duyệt trước khi normalize/import/embed.")

        settings = await active_settings()
        mode = routing_mode(getattr(settings, "organization_routing_mode", "legacy"))
        if mode != "legacy":
            candidate = await cls._ensure_candidate_assignment(candidate)
        if (
            mode in {"hybrid", "unit_primary"}
            and candidate.get("assignment_state") not in {"assigned", "shared"}
        ):
            raise ValueError(
                "Candidate chưa được phân công phòng ban hoặc xác nhận dùng chung."
            )

        validation_errors = cls.validate_candidate_for_import(candidate)
        if validation_errors:
            reason = " | ".join(validation_errors)
            await repo_update(
                "legal_crawl_candidate", candidate_id,
                {
                    "status": "changes_requested",
                    "review_status": "changes_requested",
                    "import_status": "preflight_blocked",
                    "pipeline_stage": "blocked",
                    "preparation_status": "blocked",
                    "blockers": validation_errors,
                    "review_note": f"Cần bổ sung trước khi nhập kho: {reason}",
                },
            )
            raise ValueError(reason)

        raw_metadata = candidate.get("raw_metadata") or {}
        domain = candidate.get("domain") or raw_metadata.get("domain")
        domain_field_map = {
            "ho_tich_chung_thuc": 7,
            "dat_dai_xay_dung": 8,
            "an_sinh_y_te_giao_duc": 2,
            "hanh_chinh_cong": 2,
            "trat_tu_do_thi": 10,
            "cu_tru_an_ninh": 9,
            "khieu_nai_to_cao_xu_phat": 10,
        }
        title = str(candidate.get("title") or "").strip()
        content_text = str(candidate.get("content") or "").strip()
        source_url = str(candidate.get("source_url") or candidate.get("detail_url") or "").strip()
        canonical_scope = _canonical_scope(
            candidate.get("scope") or raw_metadata.get("scope")
        ) or _detect_scope(
            candidate.get("law_number"), title, content_text, source_url
        )
        import_payload = {
            "title": title,
            "law_number": str(candidate.get("law_number") or "").strip(),
            "document_type": raw_metadata.get("document_type") or candidate.get("document_type") or "Văn bản pháp luật",
            "issuing_agency": raw_metadata.get("issuing_agency") or candidate.get("issuing_agency") or "Chưa xác định",
            "scope": canonical_scope or "central",
            "sector": raw_metadata.get("sector") or "",
            "field_id": domain_field_map.get(domain, int(raw_metadata.get("field_id") or 1)),
            "domain_slug": domain,
            "domain_codes": list(
                dict.fromkeys(
                    str(item)
                    for item in (raw_metadata.get("matched_domains") or [domain])
                    if item
                )
            ),
            "primary_organization_unit_id": candidate.get(
                "primary_organization_unit_id"
            ),
            "organization_unit_ids": list(
                dict.fromkeys(
                    [
                        *list(candidate.get("proposed_organization_unit_ids") or []),
                        *(
                            [candidate.get("primary_organization_unit_id")]
                            if candidate.get("primary_organization_unit_id")
                            else []
                        ),
                    ]
                )
            ),
            "organization_assignment_state": candidate.get("assignment_state"),
            "issued_date": raw_metadata.get("issued_date"),
            "effective_date": raw_metadata.get("effective_date"),
            "expired_date": raw_metadata.get("expired_date"),
            "source_url": source_url,
            "applicability_info": raw_metadata.get("applicability_info") or "",
            "content": content_text,
            "confirmed_official_source": bool(raw_metadata.get("confirmed_official_source")),
            "uploaded_pdf_sha256": raw_metadata.get('uploaded_pdf_sha256') or (candidate.get('uploaded_file') or {}).get('sha256'),
            "structure": "auto",
        }
        # Keep an audit envelope outside the import try-block.  The management
        # service may have created a staging/active document before the final
        # retrieval smoke check fails; losing that identifier makes the real
        # database state impossible to reconcile from the candidate screen.
        legal_import_result: dict[str, Any] = {}
        document_id: Any = None
        vector_collection = ""
        chunk_count = 0
        retrieval_smoke: dict[str, Any] = {}
        compensation: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{LEGAL_MANAGEMENT_URL}/import", json=import_payload
                )
            if response.status_code != 200:
                raise RuntimeError(f"Legal Search import rejected: HTTP {response.status_code}: {response.text[:500]}")
            legal_import_result = response.json() if response.content else {}
            document_id = legal_import_result.get("document_id")
            vector_collection = str(
                legal_import_result.get("vector_collection") or ""
            ).strip()
            try:
                chunk_count = int(legal_import_result.get("chunk_count") or 0)
            except (TypeError, ValueError):
                chunk_count = 0
            if (
                legal_import_result.get("status") != "embedded_active"
                or legal_import_result.get("activation_status") != "active"
                or not str(document_id or "").strip()
                or chunk_count < 1
                or not vector_collection
                or not (legal_import_result.get("activation_receipt") or {}).get(
                    "passed"
                )
            ):
                raise RuntimeError(
                    "Embedding chưa xác nhận đủ document active, mã văn bản, vector chunk và collection phục vụ; "
                    "import không được đánh dấu hoàn tất."
                )
            retrieval_smoke = await verify_post_activation_retrieval(
                document_id=document_id,
                law_number=str(
                    legal_import_result.get("law_number")
                    or import_payload.get("law_number")
                    or ""
                ),
                title=title,
                content=content_text,
                domain=str(domain or "") or None,
                base_url=LEGAL_SEARCH_URL,
            )
            if not retrieval_smoke.get("passed"):
                compensation = await cls._exclude_failed_activation(
                    document_id,
                    reason=(
                        "Tự động loại khỏi tra cứu vì kiểm tra sau kích hoạt không tìm thấy "
                        "văn bản bằng cả số hiệu và nội dung ngữ nghĩa."
                    ),
                )
                raise RuntimeError(
                    "Post-activation retrieval smoke failed; "
                    f"new document compensation={compensation.get('status')}."
                )
            legal_import_result = {
                **legal_import_result,
                "retrieval_smoke": retrieval_smoke,
                "chatbot_ready": True,
            }
        except Exception as exc:
            failed_import_result = {
                "status": "failed",
                "document_id": document_id,
                "law_number": legal_import_result.get("law_number") or import_payload.get("law_number"),
                "chunk_count": chunk_count,
                "vector_collection": vector_collection or None,
                "retrieval_smoke": retrieval_smoke,
                "compensation": compensation,
                "error_class": exc.__class__.__name__,
                "error_reason": str(exc)[:1000],
            }
            await repo_update(
                "legal_crawl_candidate", candidate_id,
                {
                    "status": "import_failed",
                    "review_status": "approved",
                    "import_status": "failed",
                    "pipeline_stage": "failed",
                    "chatbot_ready": False,
                    "failed_document_id": document_id,
                    "failed_import_result": failed_import_result,
                    "review_note": f"Import/embed failed: {str(exc)[:1000]}",
                },
            )
            if job_id:
                await repo_update(
                    "legal_import_job",
                    job_id,
                    {
                        "status": "failed",
                        "completed_at": _utcnow(),
                        "document_id": str(document_id) if document_id is not None else None,
                        "chunk_count": chunk_count,
                        "vector_collection": vector_collection or None,
                        "embedding_result": failed_import_result,
                        "error_reason": str(exc)[:1000],
                    },
                )
            raise RuntimeError(str(exc)) from exc

        raw_document_id = legal_import_result["document_id"]
        try:
            candidate_document_id = int(raw_document_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Legal Search trả về mã văn bản không phải số; chưa thể lưu xác nhận nhập kho."
            ) from exc
        document_id = str(candidate_document_id)
        try:
            await replace_document_unit_assignments(
                document_id=document_id,
                primary_organization_unit_id=candidate.get(
                    "primary_organization_unit_id"
                ),
                organization_unit_ids=candidate.get(
                    "proposed_organization_unit_ids"
                )
                or (),
                assignment_source="crawler",
                confidence=float(
                    candidate.get("organization_assignment_confidence") or 0.0
                ),
                assignment_state=str(
                    candidate.get("assignment_state")
                    or (
                        "assigned"
                        if candidate.get("primary_organization_unit_id")
                        else "unassigned"
                    )
                ),
            )
        except Exception as exc:
            if mode in {"hybrid", "unit_primary"}:
                await cls._exclude_failed_activation(
                    document_id,
                    reason=(
                        "Tự động loại khỏi tra cứu vì chưa đồng bộ được phòng ban "
                        "chịu trách nhiệm sau khi nhập kho."
                    ),
                )
                raise RuntimeError(
                    "Không thể đồng bộ phòng ban của văn bản; văn bản đã được loại "
                    "khỏi tra cứu để tránh trạng thái nửa vời."
                ) from exc
            logger.warning(
                "Document {} unit projection unavailable in {} mode: {}",
                document_id,
                mode,
                exc.__class__.__name__,
            )
        source_asset = cls._persist_approved_source_asset(candidate, document_id)
        imported_document = {
            # Migration 26 intentionally stores this relation key as a number.
            # Keep the import-job copy as text for its separate audit schema.
            "document_id": candidate_document_id,
            "law_number": legal_import_result.get("law_number"),
            "structure": legal_import_result.get("structure"),
            "article_count": legal_import_result.get("article_count"),
            "chunk_count": legal_import_result.get("chunk_count"),
            "status": legal_import_result.get("status"),
            "activation_status": legal_import_result.get("activation_status"),
            "model": legal_import_result.get("model"),
            "vector_collection": vector_collection,
            "activation_receipt": legal_import_result.get(
                "activation_receipt"
            ),
            "retrieval_smoke": legal_import_result.get("retrieval_smoke"),
            "source_asset": source_asset,
        }
        await repo_update(
            "legal_crawl_candidate", candidate_id,
            {
                "status": "imported",
                "review_status": "imported",
                "import_status": "completed",
                "pipeline_stage": "active",
                "preparation_status": "completed",
                "blockers": [],
                "document_id": candidate_document_id,
                "chunk_count": int(legal_import_result.get("chunk_count") or 0),
                "indexed_at": _utcnow(),
                "vector_collection": vector_collection,
                "chatbot_ready": bool(
                    vector_collection
                    and (legal_import_result.get("retrieval_smoke") or {}).get("passed")
                ),
                "review_note": (
                    "Đã chuẩn hóa, chia đoạn, embedding, kích hoạt và kiểm tra truy vấn "
                    "theo số hiệu lẫn nội dung ngữ nghĩa."
                ),
                "imported_document": imported_document,
                "source_asset": source_asset,
            },
        )
        law_number = str(legal_import_result.get("law_number") or import_payload.get("law_number") or "").strip()
        if law_number:
            # A newly activated copy may change evidence used by an approved
            # golden answer. Matching reviews must be checked by an expert again.
            from api.expert_review_service import invalidate_reviews_for_laws

            invalidate_reviews_for_laws({law_number})
        if job_id:
            await repo_update(
                "legal_import_job", job_id,
                {"status": "completed", "completed_at": _utcnow(), "document_id": document_id, "structure": legal_import_result.get("structure"), "article_count": legal_import_result.get("article_count"), "chunk_count": legal_import_result.get("chunk_count"), "indexed_at": _utcnow(), "vector_collection": vector_collection, "source_asset": source_asset, "embedding_result": legal_import_result, "error_reason": None},
            )
        return {"id": None, "title": title, "legal_import_ok": True, "legal_import_result": legal_import_result, "source_asset": source_asset}

    @classmethod
    async def enqueue_import_job(
        cls,
        candidate_id: str,
        *,
        approval: dict[str, Any] | None = None,
        defer_preflight: bool = False,
    ) -> dict[str, Any]:
        """Persist a queue item; worker performs expensive import/embed off-request.

        ``defer_preflight`` is used only after an explicit admin approval. The
        approval is durable even when source or retrieval services are
        temporarily unavailable; the worker still applies every guard before
        activation.
        """
        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Không tìm thấy candidate.")
        if candidate.get("status") not in {
            "pending", "changes_requested", "approved", "import_failed", "import_queued"
        }:
            raise ValueError("Candidate không ở trạng thái có thể xếp hàng nhập kho.")
        rows = await repo_query(
            "SELECT * FROM legal_import_job WHERE candidate = $candidate AND status IN ['queued', 'running'] LIMIT 1;",
            {"candidate": ensure_record_id(candidate["id"])},
        )
        if rows:
            return rows[0]
        if candidate.get("status") == "import_queued":
            raise CandidateChanged("Candidate đang được xếp hàng; hãy chờ hoặc kiểm tra tác vụ trước khi thử lại.")
        errors = cls.validate_candidate_for_import(candidate, require_approved=False)
        if errors and not defer_preflight:
            raise ValueError(" | ".join(errors))
        runtime_conflict = await cls.find_runtime_document_conflict(candidate)
        if runtime_conflict:
            await cls.mark_runtime_document_conflict(candidate, runtime_conflict)
            raise ValueError(
                "Văn bản có định danh xung đột trong kho runtime; "
                "candidate đã được chuyển sang cần đối chiếu."
            )
        reservation = await update_candidate_if_current(candidate, {
            "status": "import_queued", "review_status": "approved", "import_status": "queued",
            "pipeline_stage": "queued", "preparation_status": "queued", "blockers": list(candidate.get("blockers") or []) if defer_preflight else [],
            "chatbot_ready": False,
            "review_note": "Đã duyệt; hệ thống đang tự chuẩn hóa, nhập kho và kích hoạt tra cứu.",
            **(approval or {}),
        })
        try:
            created = await repo_create("legal_import_job", {
                "candidate": ensure_record_id(candidate["id"]), "status": "queued", "attempts": 0,
                "error_reason": None, "created_at": _utcnow(), "started_at": None, "completed_at": None,
                "document_id": None, "structure": None, "article_count": None, "chunk_count": None,
                "source_asset": None, "embedding_result": None,
            })
        except Exception:
            await update_candidate_if_current(reservation, {
                "status": "import_failed", "import_status": "queue_failed", "pipeline_stage": "blocked",
                "review_note": "Không tạo được tác vụ nhập kho. Kiểm tra kết nối và thử lại.",
            })
            raise
        job = created[0] if isinstance(created, list) else created
        # The worker may already own the job. Link its receipt without changing
        # its newer running/completed state back to queued.
        await repo_update("legal_crawl_candidate", candidate["id"], {"import_job": ensure_record_id(job["id"])})
        return job

    @classmethod
    async def _claim_next_import_job(cls) -> dict[str, Any] | None:
        """Atomically move one queued import job into ``running``.

        Multiple worker processes can poll at the same time.  Selecting a job
        and then updating it unconditionally lets two workers import the same
        candidate.  The second conditional update is the ownership boundary:
        only the worker that changes ``queued`` to ``running`` receives a job.
        """
        queued_rows = await repo_query(
            "SELECT * FROM legal_import_job WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1;"
        )
        if not queued_rows:
            return None

        queued_job = queued_rows[0]
        attempts = int(queued_job.get("attempts") or 0) + 1
        claimed_rows = await repo_query(
            "UPDATE legal_import_job MERGE $data "
            "WHERE id = $id AND status = 'queued' RETURN AFTER;",
            {
                "id": ensure_record_id(queued_job["id"]),
                "data": {
                    "status": "running",
                    "attempts": attempts,
                    "started_at": _utcnow(),
                    "error_reason": None,
                },
            },
        )
        return claimed_rows[0] if claimed_rows else None

    @classmethod
    async def process_next_import_job(cls) -> dict[str, Any] | None:
        """Run one persistent job. Failures are visible and retryable, never active."""
        job = await cls._claim_next_import_job()
        if not job:
            return None
        started_tick = asyncio.get_running_loop().time()
        try:
            current = await cls.get_candidate(str(job["candidate"]))
            if not current or current.get("status") not in {"import_queued", "approved"}:
                raise CandidateChanged("Candidate không còn được phép nhập; tác vụ cũ đã bị hủy.")
            await update_candidate_if_current(
                current,
                {
                    "status": "import_queued",
                    "review_status": "approved",
                    "import_status": "running",
                    "pipeline_stage": "chunking_embedding",
                    "review_note": "Đang chuẩn hóa, chia đoạn và embedding.",
                },
            )
            result = await cls.import_candidate(str(job["candidate"]), job_id=str(job["id"]))
            telemetry.record_operation(category="import_job", route="worker:normalize_import_embed", duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000, metadata={"job_type": "import_embed"})
            return {"job_id": str(job["id"]), "status": "completed", "result": result}
        except CandidateChanged as exc:
            await repo_update("legal_import_job", job["id"], {"status": "cancelled", "completed_at": _utcnow(), "error_reason": str(exc)})
            return {"job_id": str(job["id"]), "status": "cancelled", "failure_kind": "candidate_changed"}
        except Exception as exc:
            message = str(exc)[:1000]
            # A legacy queued job may have been created before duplicate
            # preflight existed.  If the import boundary confirms a duplicate,
            # turn that into the same auditable human-review state as a new
            # preflight conflict instead of offering an endlessly failing retry.
            normalized_error = message.casefold()
            duplicate_markers = (
                "đã tồn tại",
                "already exists",
                "duplicate",
            )
            if any(marker in normalized_error for marker in duplicate_markers):
                try:
                    failed_candidate = await cls.get_candidate(str(job["candidate"]))
                    runtime_conflict = (
                        await cls.find_runtime_document_conflict(failed_candidate)
                        if failed_candidate
                        else None
                    )
                except Exception as conflict_error:
                    logger.warning(
                        "Unable to classify failed import duplicate for {}: {}",
                        job["id"],
                        conflict_error.__class__.__name__,
                    )
                    runtime_conflict = None
                    failed_candidate = None
                if failed_candidate and runtime_conflict:
                    await repo_update(
                        "legal_import_job",
                        job["id"],
                        {
                            "status": "failed",
                            "completed_at": _utcnow(),
                            "error_reason": message,
                        },
                    )
                    await cls.mark_runtime_document_conflict(
                        failed_candidate,
                        runtime_conflict,
                    )
                    telemetry.record_operation(
                        category="import_job",
                        route="worker:normalize_import_embed",
                        duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000,
                        outcome="duplicate_conflict",
                        metadata={"job_type": "import_embed"},
                    )
                    telemetry.record_issue(
                        "import_duplicate_conflict",
                        category="import_job",
                        error_class=exc.__class__.__name__,
                    )
                    return {
                        "job_id": str(job["id"]),
                        "status": "failed",
                        "failure_kind": "duplicate_conflict",
                        "error_reason": message,
                    }
            # import_candidate normally records import_failed. Keep a defensive
            # candidate update here so every worker-side exception is explicit.
            await repo_update("legal_import_job", job["id"], {"status": "failed", "completed_at": _utcnow(), "error_reason": message})
            await repo_update("legal_crawl_candidate", job["candidate"], {
                "status": "import_failed", "review_status": "approved", "import_status": "failed",
                "pipeline_stage": "failed",
                "review_note": f"Không nhập được văn bản vào kho: {message}",
            })
            telemetry.record_operation(category="import_job", route="worker:normalize_import_embed", duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000, outcome="failed", metadata={"job_type": "import_embed"})
            telemetry.record_issue("import_failed", category="import_job", error_class=exc.__class__.__name__)
            return {"job_id": str(job["id"]), "status": "failed", "error_reason": message}

    @staticmethod
    def _import_job_stale_after_seconds() -> float:
        try:
            value = float(os.getenv("LEGAL_IMPORT_JOB_STALE_AFTER_SECONDS", "1800"))
        except (TypeError, ValueError):
            value = 1800.0
        return min(max(value, 300.0), 24 * 60 * 60.0)

    @staticmethod
    def _as_utc_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    async def recover_stale_import_jobs(cls) -> int:
        """Return timed-out running jobs to the durable queue after a restart.

        A worker crash can leave a job marked ``running`` indefinitely.  Do not
        make the document active or silently discard the job: put it back in the
        queue with an audit-visible reason, then let normal validation/import
        run again.  Jobs without a trustworthy timestamp are intentionally not
        touched automatically.
        """
        rows = await repo_query(
            "SELECT id, candidate, status, started_at FROM legal_import_job WHERE status = 'running' ORDER BY started_at ASC LIMIT 100;"
        )
        now = _utcnow()
        stale_after = timedelta(seconds=cls._import_job_stale_after_seconds())
        recovered = 0
        for job in rows:
            started_at = cls._as_utc_datetime(job.get("started_at"))
            if not started_at or now - started_at <= stale_after:
                continue
            reason = "Worker nhập kho đã dừng hoặc quá hạn; job được đưa lại vào hàng chờ."
            await repo_update(
                "legal_import_job",
                job["id"],
                {
                    "status": "queued",
                    "started_at": None,
                    "completed_at": None,
                    "error_reason": reason,
                },
            )
            await repo_update(
                "legal_crawl_candidate",
                job["candidate"],
                {
                    "status": "import_queued",
                    "review_status": "approved",
                    "import_status": "queued",
                    "review_note": reason,
                },
            )
            recovered += 1
        return recovered

    @classmethod
    async def recover_stale_processing_jobs(cls) -> int:
        """Return timed-out OCR/extraction jobs to the durable queue.

        Extraction uses the same background worker as embedding. A crash after
        the job is claimed must not leave a candidate permanently marked as
        ``running``. As with import recovery, missing timestamps are not
        guessed and no extracted content is activated by recovery itself.
        """
        rows = await repo_query(
            "SELECT id, candidate, status, started_at FROM legal_candidate_processing_job "
            "WHERE status = 'running' ORDER BY started_at ASC LIMIT 100;"
        )
        now = _utcnow()
        stale_after = timedelta(seconds=cls._import_job_stale_after_seconds())
        recovered = 0
        for job in rows:
            started_at = cls._as_utc_datetime(job.get("started_at"))
            if not started_at or now - started_at <= stale_after:
                continue
            reason = "Worker trích xuất/OCR đã dừng hoặc quá hạn; job được đưa lại vào hàng chờ."
            await repo_update(
                "legal_candidate_processing_job",
                job["id"],
                {
                    "status": "queued",
                    "started_at": None,
                    "completed_at": None,
                    "error_reason": reason,
                },
            )
            await repo_update(
                "legal_crawl_candidate",
                job["candidate"],
                {
                    "processing_status": "queued",
                    "updated": _utcnow(),
                },
            )
            recovered += 1
        return recovered

    @classmethod
    async def enqueue_processing_job(cls, candidate_id: str) -> dict[str, Any]:
        """Queue local candidate extraction/OCR; never fetch public URLs here."""
        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Không tìm thấy candidate.")
        uploaded = candidate.get("uploaded_file") or {}
        raw_path = str(uploaded.get("path") or "")
        uploads_root = (Path("data") / "uploads").resolve()
        file_path = Path(raw_path).resolve() if raw_path else None
        if not file_path or not file_path.is_file() or uploads_root not in file_path.parents:
            raise ValueError("Candidate này mới có metadata/link. Admin cần tải file nguồn hợp lệ lên trước khi trích xuất/OCR.")
        if file_path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("File vượt giới hạn 25 MB cho một lượt trích xuất.")
        rows = await repo_query(
            "SELECT * FROM legal_candidate_processing_job WHERE candidate = $candidate AND status IN ['queued', 'running'] LIMIT 1;",
            {"candidate": ensure_record_id(candidate["id"])},
        )
        if rows:
            return rows[0]
        created = await repo_create("legal_candidate_processing_job", {
            "candidate": ensure_record_id(candidate["id"]), "job_type": "extract_ocr", "status": "queued",
            "attempts": 0, "error_reason": None, "created_at": _utcnow(), "started_at": None,
            "completed_at": None, "result_summary": None,
        })
        job = created[0] if isinstance(created, list) else created
        await repo_update("legal_crawl_candidate", candidate["id"], {
            "processing_job": ensure_record_id(job["id"]), "processing_status": "queued",
            "updated": _utcnow(),
        })
        telemetry.record_operation(category="ocr", route="worker:extract_ocr", duration_ms=0, outcome="queued", metadata={"job_type": "extract_ocr"})
        return job

    @classmethod
    async def _claim_next_processing_job(cls) -> dict[str, Any] | None:
        """Atomically claim one OCR/extraction job before reading its file."""
        queued_rows = await repo_query(
            "SELECT * FROM legal_candidate_processing_job "
            "WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1;"
        )
        if not queued_rows:
            return None

        queued_job = queued_rows[0]
        attempts = int(queued_job.get("attempts") or 0) + 1
        claimed_rows = await repo_query(
            "UPDATE legal_candidate_processing_job MERGE $data "
            "WHERE id = $id AND status = 'queued' RETURN AFTER;",
            {
                "id": ensure_record_id(queued_job["id"]),
                "data": {
                    "status": "running",
                    "attempts": attempts,
                    "started_at": _utcnow(),
                    "error_reason": None,
                },
            },
        )
        return claimed_rows[0] if claimed_rows else None

    @classmethod
    async def process_next_processing_job(cls) -> dict[str, Any] | None:
        """Run one local extraction/OCR job in the background worker."""
        job = await cls._claim_next_processing_job()
        if not job:
            return None
        started_tick = asyncio.get_running_loop().time()
        candidate_id = str(job["candidate"])
        try:
            candidate = await cls.get_candidate(candidate_id)
            if not candidate:
                raise ValueError("Candidate không còn tồn tại.")
            uploaded = candidate.get("uploaded_file") or {}
            uploads_root = (Path("data") / "uploads").resolve()
            file_path = Path(str(uploaded.get("path") or "")).resolve()
            if not file_path.is_file() or uploads_root not in file_path.parents:
                raise ValueError("Không tìm thấy file nguồn đã upload an toàn.")
            if file_path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError("File vượt giới hạn 25 MB cho một lượt trích xuất.")
            # Import lazily so this worker does not make the crawler depend on
            # optional OCR adapters at startup.
            from api.routers.legal_search import _extract_upload_text
            from api.utils.pii_detector import redact_upload_text

            extracted_text, extraction = await asyncio.to_thread(
                _extract_upload_text, str(uploaded.get("filename") or file_path.name), file_path.read_bytes()
            )
            pii = redact_upload_text(extracted_text or "")
            extracted_text = pii["text"]
            extraction.update({
                "source": "candidate_upload", "filename": uploaded.get("filename") or file_path.name,
                "characters": len(extracted_text), "contains_pii": pii["contains_pii"],
                "pii_types": pii["pii_types"], "pii_counts": pii["pii_counts"],
                "pii_masked": pii["masked"], "retention": pii["retention"],
            })
            raw_metadata = candidate.get("raw_metadata") or {}
            raw_metadata.update({
                "ocr_status": extraction.get("ocr_status", "not_required"),
                "ocr_confidence": extraction.get("ocr_confidence"), "ocr_reason": extraction.get("reason", ""),
                "pdf_kind": extraction.get("pdf_kind", "not_pdf"), "language": extraction.get("language", "unknown"),
                "page_count": extraction.get("page_count", 0), "text_fingerprint": extraction.get("text_fingerprint"),
                "file_fingerprint": extraction.get("file_fingerprint"), "extraction_preview": extraction.get("preview", ""),
                "extraction_updated_at": _utcnow().isoformat(),
            })
            recommendation = cls.build_review_recommendation({
                **candidate, "content": extracted_text, "extraction_result": extraction, "raw_metadata": raw_metadata,
            })
            await repo_update("legal_crawl_candidate", candidate["id"], {
                "content": extracted_text or None,
                "content_hash": extraction.get("text_fingerprint") if extracted_text else candidate.get("content_hash"),
                "extraction_result": extraction, "raw_metadata": raw_metadata,
                "review_recommendation": recommendation, "processing_status": "completed", "updated": _utcnow(),
            })
            elapsed_ms = (asyncio.get_running_loop().time() - started_tick) * 1000
            summary = {"ocr_status": extraction.get("ocr_status"), "characters": len(extracted_text), "pdf_kind": extraction.get("pdf_kind")}
            await repo_update("legal_candidate_processing_job", job["id"], {
                "status": "completed", "completed_at": _utcnow(), "result_summary": summary, "error_reason": None,
            })
            outcome = "success" if extraction.get("ocr_status") != "failed" else "error"
            telemetry.record_operation(category="ocr", route="worker:extract_ocr", duration_ms=elapsed_ms, outcome=outcome, metadata={"job_type": "extract_ocr", "source_type": extraction.get("pdf_kind", "unknown")})
            if outcome == "error":
                telemetry.record_issue("ocr_failed", category="ocr", error_class="extraction_failed")
            return {"job_id": str(job["id"]), "status": "completed", "result_summary": summary}
        except Exception as exc:
            elapsed_ms = (asyncio.get_running_loop().time() - started_tick) * 1000
            message = str(exc)[:1000]
            await repo_update("legal_candidate_processing_job", job["id"], {"status": "failed", "completed_at": _utcnow(), "error_reason": message})
            await repo_update("legal_crawl_candidate", job["candidate"], {"processing_status": "failed", "updated": _utcnow()})
            telemetry.record_operation(category="ocr", route="worker:extract_ocr", duration_ms=elapsed_ms, outcome="failed", metadata={"job_type": "extract_ocr"})
            telemetry.record_issue("ocr_failed", category="ocr", error_class=exc.__class__.__name__)
            return {"job_id": str(job["id"]), "status": "failed", "error_reason": message}

    @classmethod
    async def review_candidate(
        cls,
        candidate_id: str,
        decision: str,
        review_note: str = "",
        reviewed_by: str | None = None,
        reviewed_role: str | None = None,
        organization_routing_mode: str = "legacy",
    ) -> dict[str, Any]:
        """Review a candidate and auto-import if approved. Week 1."""
        if decision not in {"approved", "rejected", "changes_requested"}:
            raise ValueError("Invalid candidate decision")
        current = await cls.get_candidate(candidate_id)
        if not current:
            raise ValueError("Không tìm thấy candidate.")
        current_status = str(current.get("status") or "").strip().lower()
        if current_status not in {"pending", "changes_requested", "approved", "import_failed"}:
            raise ValueError(
                f"Candidate ở trạng thái {current_status or 'không xác định'} "
                "không thể nhận quyết định duyệt mới. Hãy dùng thao tác nhập lại "
                "cho lỗi import hoặc xem bản ghi đã nhập ở chế độ chỉ đọc."
            )
        # A candidate whose import failed may be retried or explicitly closed
        # by an administrator. Keep an actively approved candidate immutable
        # because its background import may still be running concurrently.
        if current_status == "approved" and decision != "approved":
            raise ValueError(
                f"Candidate ở trạng thái {current_status} không thể nhận quyết định duyệt mới."
            )
        if current_status == "changes_requested" and decision == "changes_requested":
            raise ValueError(
                "Candidate đã ở trạng thái cần bổ sung; chỉ có thể duyệt hoặc bỏ qua "
                "sau khi dữ liệu được cập nhật."
            )
        if decision == "approved":
            mode = routing_mode(organization_routing_mode)
            if mode != "legacy":
                current = await cls._ensure_candidate_assignment(current)
            if (
                mode in {"hybrid", "unit_primary"}
                and current.get("assignment_state") not in {"assigned", "shared"}
            ):
                raise ValueError(
                    "Chưa phân công phòng ban. Hãy chọn phòng ban chịu trách nhiệm "
                    "hoặc xác nhận tài liệu dùng chung trước khi duyệt."
                )
            runtime_conflict = await cls.find_runtime_document_conflict(current)
            if runtime_conflict:
                return await cls.mark_runtime_document_conflict(current, runtime_conflict)
            try:
                prepared = await cls.prepare_candidate_for_import(candidate_id)
            except CandidateChanged:
                raise
            except Exception as exc:
                # Approval is the admin's final decision. A transient source
                # or preparation failure must not turn the click into a 500;
                # keep the evidence in the candidate and let the durable job
                # retry it in the background.
                prepared = {
                    **current,
                    "blockers": [f"Chuẩn bị nguồn tạm thời chưa hoàn tất ({exc.__class__.__name__})."],
                    "preparation_status": "queued",
                }
            approval = {
                "reviewed_at": _utcnow(),
                "reviewed_by": ensure_record_id(reviewed_by) if reviewed_by else None,
                "reviewed_role": reviewed_role,
                "review_reason": review_note.strip() or None,
                "approved_by": ensure_record_id(reviewed_by) if reviewed_by else None,
                "approved_at": _utcnow(),
                "requested_changes_note": None,
                "ai_review": None,
            }
            try:
                job = await cls.enqueue_import_job(
                    candidate_id,
                    approval=approval,
                    defer_preflight=True,
                )
            except Exception:
                refreshed = await cls.get_candidate(candidate_id)
                if (
                    refreshed
                    and refreshed.get("status") == "changes_requested"
                    and refreshed.get("import_status") == "duplicate_conflict"
                ):
                    return refreshed
                raise
            return {
                **prepared,
                **approval,
                "reviewed_by": str(reviewed_by) if reviewed_by else None,
                "approved_by": str(reviewed_by) if reviewed_by else None,
                "status": "import_queued",
                "review_status": "approved",
                "import_status": str(job.get("status") or "queued"),
                "pipeline_stage": "queued",
                "preparation_status": "ready",
                "blockers": [],
                "import_job": str(job.get("id") or "") or None,
                "review_note": "Đã xếp hàng chuẩn hóa, chia đoạn và lập dữ liệu tra cứu.",
            }
        requested_changes_note = review_note.strip() if decision == "changes_requested" else None
        candidate = await update_candidate_if_current(
            current,
            {
                "status": decision,
                "review_note": review_note.strip() or None,
                "reviewed_at": _utcnow(),
                "reviewed_by": ensure_record_id(reviewed_by) if reviewed_by else None,
                "reviewed_role": reviewed_role,
                "review_reason": review_note.strip() or None,
                "review_status": decision,
                "approved_by": ensure_record_id(reviewed_by) if reviewed_by and decision == "approved" else None,
                "approved_at": _utcnow() if decision == "approved" else None,
                "requested_changes_note": requested_changes_note,
                "ai_review": None,
            },
        )
        return candidate

    @classmethod
    async def ensure_default_sources(cls):
        """Ensure default legal documents and notebooks are populated."""
        try:
            # Check if default notebook exists (use Record ID style without quotes)
            notebooks = await repo_query("SELECT * FROM notebook WHERE id = notebook:legal_documents;")
            if not notebooks:
                logger.info("Creating default notebook: notebook:legal_documents")
                await repo_query(
                    "INSERT INTO notebook { id: 'legal_documents', name: 'Kho Văn bản Pháp luật', description: 'Thư viện lưu trữ văn bản pháp luật Hải Phòng và Trung ương', archived: false };"
                )
        except Exception as e:
            logger.error(f"Failed to ensure default notebook: {e}")
        await cls.ensure_vbpl_sources()

    @classmethod
    async def _migrate_legacy_default_source_intervals(cls) -> None:
        """Retire every unsupported sitemap source without deleting its history.

        Legacy installations can contain several ``vbpl_sitemap`` rows pointing
        at the same sitemap.xml. The candidate-first crawler parses official
        listing records, not XML sitemaps; treating these rows as crawlable made
        an empty parse look like a successful run forever.
        """
        retirement_reason = (
            "Nguồn sitemap cũ đã dừng vì không cung cấp bản ghi listing "
            "có thể kiểm chứng; dùng nguồn VBPL Trung ương/Hải Phòng hiện hành."
        )
        rows = await repo_query(
            "SELECT id, interval_minutes, enabled, last_status, last_error FROM legal_crawl_source "
            "WHERE source_type = 'vbpl_sitemap';"
        )
        for row in rows:
            if (
                row.get("enabled") is False
                and int(row.get("interval_minutes") or 0) == WEEKLY_CRAWL_INTERVAL_MINUTES
                and row.get("last_status") == "retired"
                and row.get("last_error") == retirement_reason
            ):
                continue
            await repo_update(
                "legal_crawl_source",
                str(row["id"]),
                {
                    "enabled": False,
                    "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                    "last_status": "retired",
                    "last_error": retirement_reason,
                    "updated": _utcnow(),
                },
            )

    @staticmethod
    def _has_active_import_confirmation(candidate: dict[str, Any]) -> bool:
        """Return true only for a fully confirmed, retrieval-ready import."""
        imported_document = candidate.get("imported_document")
        if not isinstance(imported_document, dict):
            return False
        document_id = imported_document.get("document_id")
        try:
            chunk_count = int(imported_document.get("chunk_count") or 0)
        except (TypeError, ValueError):
            chunk_count = 0
        return (
            candidate.get("status") == "imported"
            and candidate.get("import_status") == "completed"
            and imported_document.get("activation_status") == "active"
            and bool(str(document_id or "").strip())
            and chunk_count > 0
        )

    @classmethod
    async def summary(cls) -> dict[str, Any]:
        """Get summary of crawler status and candidate counts."""
        try:
            (
                total_rows,
                pending_rows,
                pending_document_rows,
                approved_rows,
                imported_rows,
                imported_candidate_rows,
                rejected_rows,
                duplicate_state_rows,
                import_job_rows,
                metric_job_rows,
                validation_rows,
                duplicate_rows,
                domain_rows,
                source_rows,
                assignment_rows,
            ) = await asyncio.gather(
                repo_query("SELECT count() AS count FROM legal_crawl_candidate GROUP ALL;"),
                repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'pending' GROUP ALL;"),
                repo_query(
                    "SELECT count() AS count FROM legal_crawl_candidate "
                    "WHERE status = 'pending' AND "
                    "(source_type = NONE OR source_type NOT IN ['form', 'procedure']) GROUP ALL;"
                ),
                repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'approved' GROUP ALL;"),
                repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'imported' GROUP ALL;"),
                repo_query(
                    "SELECT status, import_status, imported_document FROM legal_crawl_candidate "
                    "WHERE status = 'imported';"
                ),
                repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'rejected' GROUP ALL;"),
                repo_query(
                    "SELECT status, count() AS count FROM legal_crawl_candidate "
                    "WHERE status IN ['duplicate_archived', 'replacement_review', 'changes_requested', 'import_failed'] "
                    "GROUP BY status;"
                ),
                repo_query("SELECT status, count() AS count FROM legal_import_job GROUP BY status;"),
                repo_query(
                    "SELECT status, created_at, started_at, completed_at "
                    "FROM legal_import_job ORDER BY created_at DESC LIMIT 200;"
                ),
                repo_query(
                    "SELECT count() AS count FROM legal_crawl_candidate "
                    "WHERE import_status = 'validation_failed' GROUP ALL;"
                ),
                repo_query(
                    "SELECT count() AS count FROM legal_crawl_candidate "
                    "WHERE import_status = 'duplicate_conflict' GROUP ALL;"
                ),
                repo_query("SELECT domain, count() AS count FROM legal_crawl_candidate GROUP BY domain;"),
                repo_query("SELECT source, count() AS count FROM legal_crawl_candidate GROUP BY source;"),
                repo_query(
                    "SELECT assignment_state, primary_organization_unit_id, count() AS count "
                    "FROM legal_crawl_candidate GROUP BY assignment_state, primary_organization_unit_id;"
                ),
            )
            total = total_rows[0]["count"] if total_rows else 0
            pending = pending_rows[0]["count"] if pending_rows else 0
            pending_documents = pending_document_rows[0]["count"] if pending_document_rows else 0
            approved = approved_rows[0]["count"] if approved_rows else 0
            imported = imported_rows[0]["count"] if imported_rows else 0
            activated_candidates = sum(
                1
                for candidate in imported_candidate_rows
                if isinstance(candidate, dict) and cls._has_active_import_confirmation(candidate)
            )
            unverified_imported_candidates = max(int(imported or 0) - activated_candidates, 0)
            
            rejected = rejected_rows[0]["count"] if rejected_rows else 0
            duplicate_state_counts = {
                "duplicate_archived": 0,
                "replacement_review": 0,
                "changes_requested": 0,
                "import_failed": 0,
            }
            for row in duplicate_state_rows:
                row_status = str(row.get("status") or "")
                if row_status in duplicate_state_counts:
                    duplicate_state_counts[row_status] = int(row.get("count") or 0)
            import_queue = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
            for row in import_job_rows:
                status = str(row.get("status") or "")
                if status in import_queue:
                    import_queue[status] = int(row.get("count") or 0)
            import_metrics = cls._build_import_queue_metrics(
                metric_job_rows,
                import_queue=import_queue,
                activated_candidates=activated_candidates,
                validation_failed_candidates=int(validation_rows[0]["count"] or 0) if validation_rows else 0,
                duplicate_conflict_candidates=int(duplicate_rows[0]["count"] or 0) if duplicate_rows else 0,
            )
            by_domain = {str(row.get("domain") or "unclassified"): row.get("count", 0) for row in domain_rows}
            by_source = {str(row.get("source") or "unknown"): row.get("count", 0) for row in source_rows}
            by_organization_unit: dict[str, int] = {}
            by_assignment_state = {"assigned": 0, "shared": 0, "unassigned": 0}
            for row in assignment_rows:
                count = int(row.get("count") or 0)
                state = str(row.get("assignment_state") or "unassigned")
                if state in by_assignment_state:
                    by_assignment_state[state] += count
                unit_id = str(row.get("primary_organization_unit_id") or "").strip()
                if state == "assigned" and unit_id:
                    by_organization_unit[unit_id] = (
                        by_organization_unit.get(unit_id, 0) + count
                    )
        except Exception as e:
            logger.error(f"Error getting crawl summary: {e}")
            total = pending = pending_documents = approved = imported = rejected = 0
            activated_candidates = unverified_imported_candidates = 0
            duplicate_state_counts = {
                "duplicate_archived": 0,
                "replacement_review": 0,
                "changes_requested": 0,
                "import_failed": 0,
            }
            import_queue = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
            import_metrics = cls._build_import_queue_metrics(
                [],
                import_queue=import_queue,
                activated_candidates=0,
                validation_failed_candidates=0,
                duplicate_conflict_candidates=0,
            )
            by_domain = {}
            by_source = {}
            by_organization_unit = {}
            by_assignment_state = {"assigned": 0, "shared": 0, "unassigned": 0}
            
        return {
            "total_candidates": total,
            "pending_candidates": pending,
            "pending_document_candidates": pending_documents,
            "approved_candidates": approved,
            "imported_candidates": imported,
            "activated_candidates": activated_candidates,
            "unverified_imported_candidates": unverified_imported_candidates,
            "rejected_candidates": rejected,
            "duplicate_archived_candidates": duplicate_state_counts["duplicate_archived"],
            "replacement_review_candidates": duplicate_state_counts["replacement_review"],
            "needs_attention_candidates": (
                duplicate_state_counts["replacement_review"]
                + duplicate_state_counts["changes_requested"]
                + duplicate_state_counts["import_failed"]
            ),
            "import_queue": import_queue,
            "import_metrics": import_metrics,
            "by_domain": by_domain,
            "by_source": by_source,
            "by_organization_unit": by_organization_unit,
            "by_assignment_state": by_assignment_state,
            "last_crawl_time": None,
            "is_running": False
        }

    @classmethod
    def _build_import_queue_metrics(
        cls,
        jobs: list[dict[str, Any]],
        *,
        import_queue: dict[str, int],
        activated_candidates: int,
        validation_failed_candidates: int,
        duplicate_conflict_candidates: int,
    ) -> dict[str, Any]:
        """Summarize queue timing without exposing document content or job IDs.

        The latest 200 job timestamps are enough for an operational indicator;
        aggregate success counts remain exact because they come from the status
        query. Missing timestamps are excluded rather than guessed.
        """
        now = _utcnow()
        queue_waits: list[float] = []
        processing_durations: list[float] = []
        for job in jobs:
            status = str(job.get("status") or "").strip().lower()
            created_at = cls._as_utc_datetime(job.get("created_at"))
            started_at = cls._as_utc_datetime(job.get("started_at"))
            completed_at = cls._as_utc_datetime(job.get("completed_at"))
            if created_at and status in {"queued", "running", "completed", "failed"}:
                queue_end = started_at or now
                if queue_end >= created_at:
                    queue_waits.append((queue_end - created_at).total_seconds())
            if started_at and completed_at and completed_at >= started_at and status in {"completed", "failed"}:
                processing_durations.append((completed_at - started_at).total_seconds())

        terminal_total = int(import_queue.get("completed") or 0) + int(import_queue.get("failed") or 0)
        success_rate = (
            round((int(import_queue.get("completed") or 0) / terminal_total) * 100, 1)
            if terminal_total
            else None
        )
        return {
            "observed_job_count": len(jobs),
            "success_rate_percent": success_rate,
            "average_queue_seconds": round(sum(queue_waits) / len(queue_waits), 1) if queue_waits else None,
            "average_processing_seconds": round(sum(processing_durations) / len(processing_durations), 1) if processing_durations else None,
            "validation_failed_candidates": validation_failed_candidates,
            "duplicate_conflict_candidates": duplicate_conflict_candidates,
            "activated_candidates": activated_candidates,
        }

    @classmethod
    async def list_sources(cls) -> list[dict[str, Any]]:
        """List source configuration and repair legacy internal queue status."""
        try:
            sources = await repo_query("SELECT * FROM legal_crawl_source;")
            visible_sources: list[dict[str, Any]] = []
            for source in sources:
                # Every source is archived instead of physically deleted.
                # Keeping the row prevents a built-in template from being
                # seeded again and preserves candidate/run/audit references.
                if source.get("last_status") == "deleted":
                    continue
                if not cls.is_internal_source(source):
                    visible_sources.append(source)
                    continue
                if source.get("last_status") == "internal" and not source.get("last_error"):
                    visible_sources.append(source)
                    continue
                updates = {
                    "last_status": "internal",
                    "last_error": None,
                    "last_run_stats": source.get("last_run_stats") or {},
                    "updated": _utcnow(),
                }
                await repo_update("legal_crawl_source", source["id"], updates)
                source.update(updates)
                visible_sources.append(source)
            return visible_sources
        except Exception as e:
            logger.error(f"Error listing sources: {e}")
            raise RuntimeError("Không đọc được cấu hình nguồn crawler.") from e

    @staticmethod
    def is_internal_source(source: dict[str, Any]) -> bool:
        return str(source.get("source_type") or "") in INTERNAL_SOURCE_TYPES or str(source.get("base_url") or "").startswith("local://")

    @staticmethod
    def is_crawlable_source(source: dict[str, Any]) -> bool:
        return (
            not LegalCrawlService.is_internal_source(source)
            and str(source.get("source_type") or "") in CRAWLABLE_SOURCE_TYPES
            and str(source.get("base_url") or "").startswith("https://")
        )

    @staticmethod
    def is_default_source(source: dict[str, Any]) -> bool:
        key = (str(source.get("source_type") or ""), str(source.get("base_url") or ""))
        return key in DEFAULT_CRAWL_SOURCE_KEYS or LegalCrawlService.is_internal_source(source)

    @staticmethod
    def _validate_official_source_url(base_url: str) -> str:
        value = str(base_url or "").strip()
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            raise ValueError("Nguồn crawler phải dùng URL HTTPS.")
        if not hostname or not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in OFFICIAL_HOST_SUFFIXES):
            raise ValueError("Chỉ được thêm nguồn chính thức thuộc danh sách cơ quan nhà nước đã cho phép.")
        return value

    @staticmethod
    def _validate_public_source_url(base_url: str, website_type: str) -> str:
        """Validate one Admin-configured source without allowing obvious SSRF targets."""
        value = str(base_url or "").strip()
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            raise ValueError("Nguồn crawler phải dùng URL HTTPS.")
        if not hostname or hostname in {"localhost", "localhost.localdomain"}:
            raise ValueError("Địa chỉ nguồn không hợp lệ.")
        if hostname.endswith((".local", ".internal", ".localhost")):
            raise ValueError("Không được cấu hình crawler truy cập mạng nội bộ.")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ValueError("Không được cấu hình crawler truy cập địa chỉ IP nội bộ.")
        if website_type != "reference":
            official_suffixes = tuple(OFFICIAL_HOST_SUFFIXES) + ("gov.vn",)
            if not any(
                hostname == suffix or hostname.endswith(f".{suffix}")
                for suffix in official_suffixes
            ):
                raise ValueError(
                    "Chỉ được thêm nguồn chính thức thuộc tên miền cơ quan nhà nước "
                    "cho văn bản, thủ tục hoặc biểu mẫu."
                )
        return value

    @staticmethod
    def _source_website_type(source: Mapping[str, Any] | None) -> str:
        source = source or {}
        configured = str(source.get("website_type") or "").strip().lower()
        if configured in CRAWL_WEBSITE_TYPES:
            return configured
        # Existing rows created before website profiles keep the legacy mixed
        # parser until an Admin explicitly chooses a profile. New built-in and
        # custom sources persist the field at creation time.
        return "mixed_official"

    @staticmethod
    def _normalize_source_patterns(value: Any) -> list[str]:
        if isinstance(value, str):
            values = re.split(r"[,;\n]+", value)
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = []
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()[:500]
            if text and text not in result:
                result.append(text)
            if len(result) >= 20:
                break
        return result

    @classmethod
    def _validate_source_discovery_config(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        website_type = str(payload.get("website_type") or "mixed_official").strip().lower()
        if website_type not in CRAWL_WEBSITE_TYPES:
            raise ValueError("Loại website crawler chưa được hỗ trợ.")
        link_selector = str(payload.get("link_selector") or "").strip()[:500] or None
        next_page_selector = str(payload.get("next_page_selector") or "").strip()[:500] or None
        include_patterns = cls._normalize_source_patterns(payload.get("include_patterns"))
        exclude_patterns = cls._normalize_source_patterns(payload.get("exclude_patterns"))
        if website_type == "reference" and not link_selector and not include_patterns:
            raise ValueError(
                "Nguồn tham khảo cần CSS selector liên kết hoặc mẫu URL bao gồm để tránh thu thập toàn bộ website."
            )
        return {
            "website_type": website_type,
            "link_selector": link_selector,
            "next_page_selector": next_page_selector,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
        }

    @classmethod
    async def _validate_source_organization_config(
        cls, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        configured_domains = list(
            dict.fromkeys(
                str(item).strip()
                for item in (payload.get("domains") or LEGAL_DOMAINS)
                if str(item).strip()
            )
        )
        unknown_domains = sorted(set(configured_domains) - LEGAL_DOMAINS)
        if unknown_domains:
            raise ValueError(
                "Nguồn có lĩnh vực không được hệ thống hỗ trợ: "
                + ", ".join(unknown_domains)
            )
        default_unit_id = str(
            payload.get("default_organization_unit_id") or ""
        ).strip() or None
        if default_unit_id:
            settings = await active_settings()
            units = await active_organization_units(settings)
            if not any(item.id == default_unit_id and item.is_active for item in units):
                raise ValueError("Phòng ban mặc định của nguồn không tồn tại hoặc đã ngừng hoạt động.")
        unassigned_policy = str(
            payload.get("unassigned_policy") or "unassigned"
        ).strip().lower()
        if unassigned_policy not in {"unassigned", "shared"}:
            raise ValueError("Cách xử lý khi chưa xác định phòng ban không hợp lệ.")
        return {
            "domains": configured_domains,
            "default_organization_unit_id": default_unit_id,
            "unassigned_policy": unassigned_policy,
        }

    @classmethod
    async def _organization_assignment_for_candidate(
        cls,
        *,
        domain: str | None,
        source: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        source_default_unit_id = str(
            (source or {}).get("default_organization_unit_id") or ""
        ).strip() or None
        unassigned_policy = str(
            (source or {}).get("unassigned_policy") or "unassigned"
        ).strip().lower()
        if not source_default_unit_id and domain not in LEGAL_DOMAINS:
            return {
                "assignment_state": (
                    "shared" if unassigned_policy == "shared" else "unassigned"
                ),
                "primary_organization_unit_id": None,
                "proposed_organization_unit_ids": [],
                "organization_assignment_confidence": 0.0,
            }
        try:
            settings = await active_settings()
            units = await active_organization_units(settings)
        except Exception as exc:
            # Department routing is an additive review projection. Discovery
            # must remain usable during a settings/database outage, but it
            # must fail closed to ``unassigned`` instead of trusting a stale
            # source default or inventing ownership.
            logger.warning(
                "Could not load organization units for crawler candidate; "
                "keeping candidate unassigned: {}",
                exc,
            )
            units = []
        proposal = propose_candidate_assignment(
            domain=domain,
            source_default_unit_id=source_default_unit_id,
            units=units,
        )
        if (
            proposal["assignment_state"] == "unassigned"
            and unassigned_policy == "shared"
        ):
            proposal["assignment_state"] = "shared"
        return proposal

    @classmethod
    async def _ensure_candidate_assignment(
        cls, candidate: dict[str, Any]
    ) -> dict[str, Any]:
        state = str(candidate.get("assignment_state") or "").strip().lower()
        primary_unit_id = str(
            candidate.get("primary_organization_unit_id") or ""
        ).strip()
        if state == "shared":
            return candidate
        if state == "assigned" and primary_unit_id:
            settings = await active_settings()
            units = await active_organization_units(settings)
            active_ids = {unit.id for unit in units if unit.is_active}
            assigned_ids = {primary_unit_id, *(candidate.get("proposed_organization_unit_ids") or [])}
            if not assigned_ids.issubset(active_ids):
                raise ValueError("Phòng ban phân công đã ngừng hoạt động hoặc không còn tồn tại. Hãy phân công lại trước khi duyệt/nhập kho.")
            return candidate
        source = candidate.get("source")
        if not isinstance(source, Mapping):
            source = {}
        proposal = await cls._organization_assignment_for_candidate(
            domain=str(candidate.get("domain") or "") or None,
            source=source,
        )
        rows = await repo_update(
            "legal_crawl_candidate",
            candidate["id"],
            {**proposal, "updated": _utcnow()},
        )
        return {**candidate, **proposal, **(rows[0] if rows else {})}

    @classmethod
    def _source_for_admin(cls, source: dict[str, Any]) -> dict[str, Any]:
        result = dict(source)
        internal = cls.is_internal_source(source)
        default = cls.is_default_source(source)
        result.update({
            "source_kind": "internal_queue" if internal else "web_crawler",
            "is_default": default,
            # "Default" now means seeded from the installation template, not
            # immutable. Admins must be able to remove locality-specific URLs.
            "can_delete": True,
            "can_scan": cls.is_crawlable_source(source),
            "purpose": (
                "Hàng đợi đề xuất nội bộ; không quét web."
                if internal
                else "Trang công bố chính thức được quét theo lịch."
            ),
            "website_type": cls._source_website_type(source),
            "compatibility_mode": (
                "high" if str(source.get("compatibility_mode") or "").lower() == "high"
                else "standard"
            ),
            "include_patterns": cls._normalize_source_patterns(source.get("include_patterns")),
            "exclude_patterns": cls._normalize_source_patterns(source.get("exclude_patterns")),
        })
        return result

    @classmethod
    async def create_source(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a disabled custom official listing source.

        New sources are deliberately saved disabled.  The Admin can review the
        official URL and then explicitly enable it; candidates and source runs
        are never created by this configuration operation.
        """
        discovery = cls._validate_source_discovery_config(payload)
        organization = await cls._validate_source_organization_config(payload)
        base_url = cls._validate_public_source_url(
            str(payload.get("base_url") or ""), discovery["website_type"]
        )
        name = str(payload.get("name") or "").strip()
        if len(name) < 3:
            raise ValueError("Tên nguồn cần ít nhất 3 ký tự.")
        scope = str(payload.get("sitemap_scope") or "").strip().lower()
        if scope not in {"central", "haiphong", "local"}:
            raise ValueError("Phạm vi nguồn phải là central, haiphong hoặc local.")
        existing = await repo_query(
            "SELECT * FROM legal_crawl_source WHERE base_url = $base_url LIMIT 1;",
            {"base_url": base_url},
        )
        if existing and str(existing[0].get("last_status") or "") != "deleted":
            raise ValueError("URL này đã có trong danh sách nguồn crawler.")
        source = {
            "name": name[:255],
            "source_type": "official_listing",
            "sitemap_scope": scope,
            "base_url": base_url,
            "enabled": False,
            "interval_minutes": max(15, min(int(payload.get("interval_minutes") or WEEKLY_CRAWL_INTERVAL_MINUTES), WEEKLY_CRAWL_INTERVAL_MINUTES)),
            "lookback_days": max(1, min(int(payload.get("lookback_days") or 30), 3650)),
            "max_documents_per_run": max(1, min(int(payload.get("max_documents_per_run") or 30), 200)),
            "max_listing_pages_per_run": max(1, min(int(payload.get("max_listing_pages_per_run") or 10), 50)),
            "listing_cursor": base_url,
            "filter_keyword": str(payload.get("filter_keyword") or "").strip() or None,
            **organization,
            "rate_limit_seconds": max(0.2, min(float(payload.get("rate_limit_seconds") or 1.5), 30.0)),
            "compatibility_mode": (
                "high" if str(payload.get("compatibility_mode") or "").lower() == "high"
                else "standard"
            ),
            "content_fetch_allowed": False,
            **discovery,
            "last_status": "not_checked",
            "last_error": None,
            "created": _utcnow(),
            "updated": _utcnow(),
        }

        if existing:
            # Re-adding an archived URL is an explicit Admin decision. Reuse
            # its identity so historical runs/candidates remain connected.
            rows = await repo_update("legal_crawl_source", existing[0]["id"], source)
            record = rows[0] if rows else {**existing[0], **source}
        else:
            created = await repo_create("legal_crawl_source", source)
            record = created[0] if isinstance(created, list) else created
        return cls._source_for_admin(record if isinstance(record, dict) else {**source, "id": record})

    @classmethod
    async def preview_source(cls, payload: Mapping[str, Any], *, limit: int = 10) -> dict[str, Any]:
        """Render and parse one source without creating source/candidate/run records."""
        discovery = cls._validate_source_discovery_config(payload)
        base_url = cls._validate_public_source_url(
            str(payload.get("base_url") or ""), discovery["website_type"]
        )
        source = {
            **dict(payload),
            **discovery,
            "base_url": base_url,
            "source_type": "official_listing",
        }
        allowed, reason = await cls._robots_allowed(base_url)
        if not allowed:
            raise ValueError(reason or "robots.txt không cho phép quét nguồn này.")
        try:
            compatibility_mode = str(payload.get("compatibility_mode") or "standard")
            if compatibility_mode == "high":
                html = await cls._fetch_with_backoff(
                    base_url,
                    float(payload.get("rate_limit_seconds") or 1.5),
                    "high",
                )
            else:
                html = await cls._fetch_with_backoff(
                    base_url, float(payload.get("rate_limit_seconds") or 1.5)
                )
        except Exception as exc:
            raise ValueError(cls._public_crawl_failure(exc)) from exc
        items = cls._parse_listing(html, base_url, source)
        filtered_count = 0
        if not items and str(source.get("filter_keyword") or "").strip():
            # A valid list with no match is normal for a scheduled source.
            # Reparse the same HTML (no extra fetch) to distinguish a narrow
            # keyword from a broken page; retain URL/type/selector restrictions.
            filtered_count = len(cls._parse_listing(html, base_url, {**source, "filter_keyword": ""}))
        if not items and not filtered_count and not cls._listing_page_diagnostics(html).get("valid"):
            host = (urlparse(base_url).hostname or "").lower()
            if host == "vbpl.vn" or host.endswith(".vbpl.vn"):
                raise ValueError(cls._public_crawl_failure(
                    "vbpl_official_service_unavailable: listing_no_exact_records"
                ))
            raise ValueError(
                "Chưa tìm thấy liên kết tới từng nội dung cụ thể. "
                "Hãy chọn trang danh sách có nút mở chi tiết từng văn bản, "
                "thủ tục hoặc biểu mẫu rồi kiểm tra lại."
            )
        bounded = max(1, min(int(limit), 20))
        return {
            "status": "ok",
            "mutation_performed": False,
            "base_url": base_url,
            "website_type": discovery["website_type"],
            "discovered_count": len(items),
            "preview_count": min(len(items), bounded),
            "truncated": len(items) > bounded,
            "candidates": items[:bounded],
            "filtered_out_count": filtered_count,
            "listing_verified": True,
            "notice": "Nguồn đọc được nhưng hiện chưa có nội dung khớp điều kiện. Có thể giữ cấu hình để kiểm tra vào lần sau." if not items else None,
        }

    @classmethod
    async def update_source(cls, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update allowed source fields without weakening default provenance."""
        source_rows = await repo_query(
            "SELECT * FROM legal_crawl_source WHERE id = $id LIMIT 1;",
            {"id": ensure_record_id(source_id)},
        )
        if not source_rows:
            raise ValueError("Không tìm thấy nguồn crawler.")
        existing = source_rows[0]
        if cls.is_internal_source(existing):
            # The display label is operational metadata. The technical
            # identity stays immutable so an internal queue can never be
            # converted into a web crawler through this generic endpoint.
            allowed = {"name"}
        else:
            allowed = {
                "enabled", "interval_minutes", "lookback_days", "max_documents_per_run",
                "max_listing_pages_per_run", "rate_limit_seconds", "filter_keyword",
                "content_fetch_allowed", "website_type", "link_selector",
                "next_page_selector", "include_patterns", "exclude_patterns",
                "compatibility_mode",
                "domains", "default_organization_unit_id", "unassigned_policy",
            }
            if payload.get("enabled") is True and not cls.is_crawlable_source(existing):
                raise ValueError(
                    "Loại nguồn cũ này đã ngừng hỗ trợ và không thể bật lại; "
                    "hãy dùng nguồn listing chính thức hiện hành."
                )
        if not cls.is_default_source(existing):
            allowed.update({"name", "base_url", "sitemap_scope"})
        forbidden = set(payload) - allowed
        if forbidden:
            if cls.is_internal_source(existing):
                raise ValueError(
                    "Nguồn nội bộ chỉ được đổi tên hiển thị; loại nguồn và địa chỉ local:// được khóa."
                )
            raise ValueError("Nguồn mặc định chỉ được bật, tắt hoặc chỉnh lịch quét.")
        updates = {key: value for key, value in payload.items() if key in allowed}
        if any(
            key in updates
            for key in ("domains", "default_organization_unit_id", "unassigned_policy")
        ):
            updates.update(
                await cls._validate_source_organization_config({**existing, **updates})
            )
        if "compatibility_mode" in updates:
            updates["compatibility_mode"] = str(
                updates.get("compatibility_mode") or "standard"
            ).strip().lower()
            if updates["compatibility_mode"] not in {"standard", "high"}:
                raise ValueError("Chế độ tải trang phải là tiêu chuẩn hoặc tương thích cao.")
        if any(
            key in updates
            for key in ("website_type", "link_selector", "next_page_selector", "include_patterns", "exclude_patterns")
        ):
            updates.update(cls._validate_source_discovery_config({**existing, **updates}))
        for text_key in ("filter_keyword", "link_selector", "next_page_selector"):
            if text_key in updates:
                updates[text_key] = str(updates[text_key] or "").strip() or None
        if "website_type" in updates and "base_url" not in updates:
            cls._validate_public_source_url(
                str(existing.get("base_url") or ""), str(updates["website_type"])
            )
        if "base_url" in updates:
            updates["base_url"] = cls._validate_public_source_url(
                str(updates["base_url"]),
                str(updates.get("website_type") or cls._source_website_type(existing)),
            )
            updates["listing_cursor"] = updates["base_url"]
        if "name" in updates:
            updates["name"] = str(updates["name"] or "").strip()[:255]
            if len(updates["name"]) < 3:
                raise ValueError("Tên nguồn cần ít nhất 3 ký tự.")
        if "sitemap_scope" in updates:
            updates["sitemap_scope"] = str(updates["sitemap_scope"] or "").strip().lower()
            if updates["sitemap_scope"] not in {"central", "haiphong", "local"}:
                raise ValueError("Phạm vi nguồn phải là central, haiphong hoặc local.")
        updates["updated"] = _utcnow()
        rows = await repo_update("legal_crawl_source", existing["id"], updates)
        record = rows[0] if rows else {**existing, **updates}
        return cls._source_for_admin(record)

    @classmethod
    async def delete_source(cls, source_id: str) -> dict[str, Any]:
        """Delete an eligible custom configuration, never its candidates/history."""
        source_rows = await repo_query(
            "SELECT * FROM legal_crawl_source WHERE id = $id LIMIT 1;",
            {"id": ensure_record_id(source_id)},
        )
        if not source_rows:
            raise ValueError("Không tìm thấy nguồn crawler.")
        source = source_rows[0]
        if str(source.get("last_status") or "").strip().lower() == "deleted":
            # Idempotent deletion prevents double-clicks/retries from mutating
            # the same configuration again while keeping the history intact.
            return {
                "id": str(source["id"]),
                "deleted": True,
                "already_deleted": True,
                "archived": True,
                "history_preserved": True,
                "name": source.get("name"),
            }
        if cls.is_internal_source(source):
            updates = {
                "enabled": False,
                "last_status": "deleted",
                "last_error": None,
                "updated": _utcnow(),
            }
            await repo_update("legal_crawl_source", source["id"], updates)
            return {
                "id": str(source["id"]),
                "deleted": True,
                "archived": True,
                "history_preserved": True,
                "name": source.get("name"),
            }
        updates = {
            "enabled": False,
            "last_status": "deleted",
            "last_error": None,
            "deleted_at": _utcnow(),
            "updated": _utcnow(),
        }
        await repo_update("legal_crawl_source", source["id"], updates)
        return {
            "id": str(source["id"]),
            "deleted": True,
            "archived": True,
            "history_preserved": True,
            "name": source.get("name"),
        }

    @classmethod
    async def ensure_vbpl_sources(cls) -> list[dict[str, Any]]:
        """Create the conservative official default sources once.

        Listing endpoints are crawled for metadata only. Detail pages are not
        downloaded unless an admin turns on `content_fetch_allowed` per source.
        """
        await cls._migrate_legacy_default_source_intervals()
        defaults = (
            {
                "name": "VBPL Trung ương",
                "source_type": "vbpl_listing",
                "website_type": "legal_documents",
                "sitemap_scope": "central",
                "base_url": "https://vbpl.vn/van-ban/trung-uong",
                "enabled": True,
                "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                "lookback_days": 30,
                "max_documents_per_run": 30,
                "max_listing_pages_per_run": 10,
                "listing_cursor": "https://vbpl.vn/van-ban/trung-uong",
                "filter_keyword": None,
                "domains": list(LEGAL_DOMAINS),
                "rate_limit_seconds": 1.5,
                "content_fetch_allowed": False,
                "created": _utcnow(),
                "updated": _utcnow(),
            },
            {
                "name": "VBPL Hải Phòng",
                "source_type": "vbpl_listing",
                "website_type": "legal_documents",
                "sitemap_scope": "haiphong",
                "base_url": "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong",
                "enabled": True,
                "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                "lookback_days": 30,
                "max_documents_per_run": 30,
                "max_listing_pages_per_run": 10,
                "listing_cursor": "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong",
                "filter_keyword": None,
                "domains": list(LEGAL_DOMAINS),
                "rate_limit_seconds": 1.5,
                "content_fetch_allowed": False,
                "created": _utcnow(),
                "updated": _utcnow(),
            },
            {
                "name": "Cổng Dịch vụ công Quốc gia",
                "source_type": "official_listing",
                "website_type": "procedures",
                "sitemap_scope": "central",
                "base_url": "https://dichvucong.gov.vn/p/home/dvc-thu-tuc-hanh-chinh.html",
                "enabled": True,
                "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                "lookback_days": 90,
                "max_documents_per_run": 20,
                "max_listing_pages_per_run": 5,
                "listing_cursor": "https://dichvucong.gov.vn/p/home/dvc-thu-tuc-hanh-chinh.html",
                "filter_keyword": None,
                "domains": list(LEGAL_DOMAINS),
                "rate_limit_seconds": 2.0,
                "content_fetch_allowed": False,
                "created": _utcnow(),
                "updated": _utcnow(),
            },
            {
                "name": "Cổng Hải Phòng - Văn bản quy phạm pháp luật",
                "source_type": "official_listing",
                "website_type": "legal_documents",
                "sitemap_scope": "haiphong",
                "base_url": "https://haiphong.gov.vn/Van-ban-quy-pham-phap-luat",
                "enabled": True,
                "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                "lookback_days": 60,
                "max_documents_per_run": 30,
                "max_listing_pages_per_run": 10,
                "listing_cursor": "https://haiphong.gov.vn/Van-ban-quy-pham-phap-luat",
                "filter_keyword": None,
                "domains": list(LEGAL_DOMAINS),
                "rate_limit_seconds": 1.5,
                "content_fetch_allowed": False,
                "created": _utcnow(),
                "updated": _utcnow(),
            },
            {
                "name": "Cổng Hải Phòng - Thủ tục hành chính",
                "source_type": "official_listing",
                "website_type": "procedures",
                "sitemap_scope": "haiphong",
                "base_url": "https://haiphong.gov.vn/thu-tuc-hanh-chinh-76761",
                "enabled": True,
                "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                "lookback_days": 90,
                "max_documents_per_run": 30,
                "max_listing_pages_per_run": 10,
                "listing_cursor": "https://haiphong.gov.vn/thu-tuc-hanh-chinh-76761",
                "filter_keyword": None,
                "domains": list(LEGAL_DOMAINS),
                "rate_limit_seconds": 1.5,
                "content_fetch_allowed": False,
                "created": _utcnow(),
                "updated": _utcnow(),
            },
            {
                "name": "Sở Tư pháp Hải Phòng - Văn bản pháp luật",
                "source_type": "official_listing",
                "website_type": "legal_documents",
                "sitemap_scope": "haiphong",
                "base_url": "https://sotp.haiphong.gov.vn/van-ban-quy-pham-phap-luat",
                "enabled": True,
                "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                "lookback_days": 90,
                "max_documents_per_run": 20,
                "max_listing_pages_per_run": 5,
                "listing_cursor": "https://sotp.haiphong.gov.vn/van-ban-quy-pham-phap-luat",
                "filter_keyword": None,
                "domains": ["ho_tich_chung_thuc", "hanh_chinh_cong", "trat_tu_do_thi"],
                "rate_limit_seconds": 1.5,
                "content_fetch_allowed": False,
                "created": _utcnow(),
                "updated": _utcnow(),
            },
        )
        for payload in defaults:
            rows = await repo_query(
                "SELECT id, interval_minutes, domains, last_status, website_type "
                "FROM legal_crawl_source WHERE base_url = $base_url LIMIT 1;",
                {"base_url": payload["base_url"]},
            )
            if not rows:
                await repo_create("legal_crawl_source", payload)
            else:
                # A deleted source is a durable opt-out from the installation
                # template. Never recreate or silently reactivate it.
                if str(rows[0].get("last_status") or "") == "deleted":
                    continue
                # Migrate the former one-day default to the requested weekly
                # schedule without overwriting an admin's custom interval.
                current_interval = rows[0].get("interval_minutes")
                updates: dict[str, Any] = {}
                if current_interval is not None and int(current_interval) == 1440:
                    updates["interval_minutes"] = WEEKLY_CRAWL_INTERVAL_MINUTES
                current_domains = list(rows[0].get("domains") or [])
                if not current_domains or any(item in LEGACY_DOMAIN_ALIASES for item in current_domains):
                    updates["domains"] = [
                        LEGACY_DOMAIN_ALIASES.get(item, item) for item in (current_domains or payload["domains"])
                    ]
                # Rows created before website profiles existed should start
                # using the deterministic parser that matches their seeded
                # purpose. Never overwrite a profile explicitly chosen later
                # by an Admin.
                if not str(rows[0].get("website_type") or "").strip():
                    updates["website_type"] = payload["website_type"]
                if updates:
                    updates["updated"] = _utcnow()
                    await repo_update("legal_crawl_source", str(rows[0]["id"]), updates)
        return await cls.list_sources()

    @staticmethod
    async def _robots_allowed(url: str) -> tuple[bool, str | None]:
        """Fetch and honour robots.txt through the shared Crawl4AI runtime."""
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        rendered = await fetch_rendered(
            robots_url,
            timeout_ms=15000,
            wait_until="domcontentloaded",
            settle_ms=0,
            check_robots_txt=False,
        )
        status_code = rendered.get("status_code")
        if status_code == 404:
            return True, None
        raw_robots = str(rendered.get("rendered_text") or rendered.get("html") or "")
        robots_text = BeautifulSoup(raw_robots, "html.parser").get_text("\n", strip=True)
        # robots.txt is often only a few lines. Crawl4AI's structural page
        # detector can classify that legitimate short text as an anti-bot
        # response. Accept it only when HTTP 200 and it contains real robots
        # directives; arbitrary short block pages still fail closed.
        has_robots_directives = bool(
            re.search(r"(?im)^\s*(?:user-agent|allow|disallow|sitemap)\s*:", robots_text)
        )
        if rendered.get("status") != "ok" and not (
            status_code == 200 and has_robots_directives
        ):
            return False, (
                "Chưa kiểm tra được quy tắc truy cập tự động của website. "
                "Nguồn chưa được quét và dữ liệu hiện có vẫn được giữ nguyên."
            )
        parser.parse(robots_text.splitlines())
        if not parser.can_fetch(CRAWLER_USER_AGENT, url):
            return False, "robots.txt không cho phép crawler truy cập URL này"
        return True, None

    @staticmethod
    def _public_crawl_failure(error: Exception | str) -> str:
        """Convert crawler diagnostics to safe Vietnamese operational copy."""
        raw = str(error or "").lower()
        if "vbpl_official_service_unavailable" in raw or (
            "vbpl.vn" in raw
            and any(token in raw for token in ("500", "502", "503", "service unavailable"))
        ):
            return (
                "Cổng VBPL chính thức hiện chưa trả được danh sách văn bản chi tiết. "
                "Đây là gián đoạn từ dịch vụ nguồn, không phải do đường dẫn quản trị; "
                "cấu hình và dữ liệu đã có vẫn được giữ nguyên. Hãy thử lại sau."
            )
        if "listing_no_exact_records" in raw or "không trả bản ghi" in raw:
            return (
                "Chưa tìm thấy liên kết tới từng văn bản cụ thể. Hãy chọn trang "
                "danh sách có nút mở chi tiết hoặc kiểm tra lại loại nội dung cần lấy."
            )
        if "robots" in raw:
            return (
                "Website không cho phép hoặc chưa xác nhận quyền truy cập tự động. "
                "Nguồn chưa được quét và dữ liệu hiện có vẫn được giữ nguyên."
            )
        if any(token in raw for token in ("anti-bot", "minimal_text", "access denied", "cloudflare", "datadome")):
            return (
                "Website đang hạn chế truy cập tự động hoặc chưa trả đủ nội dung. "
                "Hãy bật chế độ Tương thích cao hoặc dùng API, sitemap, RSS hay PDF chính thức."
            )
        if any(token in raw for token in ("timeout", "timed out", "rate limited", "429")):
            return "Website phản hồi quá chậm hoặc đang giới hạn truy cập. Hãy thử lại sau."
        return (
            "Không tải được nội dung từ website. Hãy kiểm tra đường dẫn, thử chế độ "
            "Tương thích cao hoặc chọn nguồn chính thức khác."
        )

    @staticmethod
    async def _fetch_with_backoff(
        url: str,
        rate_limit_seconds: float,
        compatibility_mode: str = "standard",
    ) -> str:
        """Fetch one allowed listing with bounded retries and exponential backoff."""
        delay = max(0.2, min(float(rate_limit_seconds or 1.5), 30.0))
        error: Exception | None = None
        parsed_input = urlparse(url)
        is_dvc_listing = (
            (parsed_input.hostname or "").lower() == "dichvucong.gov.vn"
            and parsed_input.path.rstrip("/") == DVC_LISTING_PATH.rstrip("/")
        )
        for attempt in range(3):
            try:
                if is_dvc_listing:
                    # This is a structured public API, not an HTML crawl.
                    async with httpx.AsyncClient(
                        timeout=30,
                        follow_redirects=True,
                        headers={"User-Agent": CRAWLER_USER_AGENT, "Accept": "application/json"},
                    ) as client:
                        query = dict(parse_qsl(parsed_input.query, keep_blank_values=True))
                        try:
                            requested_limit = int(query.get(DVC_LIMIT_PARAM) or DVC_MAX_PAGE_SIZE)
                        except (TypeError, ValueError):
                            requested_limit = DVC_MAX_PAGE_SIZE
                        response = await client.post(
                            DVC_FORMALITY_SEARCH_ENDPOINT,
                            json={
                                "limit": max(1, min(requested_limit, DVC_MAX_PAGE_SIZE)),
                                "lastId": str(query.get(DVC_CURSOR_PARAM) or ""),
                                "q": "",
                                "categoryId": "",
                                "departmentCode": "",
                            },
                        )
                    if response.status_code in {429, 503}:
                        raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)
                    response.raise_for_status()
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise RuntimeError("DVC listing API returned invalid JSON") from exc
                    return LegalCrawlService._build_dvc_listing_html(payload, url)
                if compatibility_mode == "high":
                    rendered = await fetch_rendered(
                        url,
                        timeout_ms=45000,
                        wait_until="networkidle",
                        settle_ms=5000,
                        check_robots_txt=True,
                        compatibility_mode="high",
                    )
                else:
                    rendered = await fetch_rendered(
                        url,
                        timeout_ms=30000,
                        wait_until="networkidle",
                        check_robots_txt=True,
                    )
                html = str(rendered.get("html") or "")
                if rendered.get("status") != "ok" or not html:
                    raise RuntimeError(
                        "crawl4ai_listing_failed:"
                        + str(rendered.get("reason") or rendered.get("status") or "empty")
                    )
                return html
            except (httpx.HTTPError, UnicodeError, RuntimeError) as exc:
                error = exc
                if attempt < 2:
                    await asyncio.sleep(delay * (2**attempt))
        raise RuntimeError(f"không tải được listing sau 3 lần: {error}")

    @staticmethod
    def _dvc_procedure_tier(item: dict[str, Any]) -> str | None:
        """Keep only nationwide standards and verified Hai Phong overrides."""
        state = str(item.get("state") or "").strip().upper()
        if state not in {"ACTIVE", "UPDATED"}:
            return None
        formality_type = str(item.get("type") or "").strip().upper()
        agency = _plain_text(str(item.get("departmentPromulgate") or ""))
        if formality_type == "STANDARD":
            return "central"
        if formality_type == "SPECIFIC" and (
            "hai phong" in agency or "haiphong" in agency
        ):
            return "haiphong_override"
        return None

    @staticmethod
    def _build_dvc_listing_html(payload: Any, listing_url: str) -> str:
        """Convert the official DVC API page into deterministic listing HTML.

        The public DVC landing page is a JavaScript shell and contains no
        procedure records.  Its official citizen API is therefore adapted to
        the same candidate-first parser used by other sources.  API values are
        retained as unverified listing metadata; none are promoted directly to
        the legal corpus.
        """
        if not isinstance(payload, dict) or payload.get("code") != "OK":
            raise RuntimeError("DVC listing API returned a non-OK response")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("DVC listing API omitted its data object")

        def labels(value: Any) -> str:
            if not isinstance(value, list):
                return str(value or "").strip()
            output: list[str] = []
            for entry in value:
                if isinstance(entry, dict):
                    label = str(entry.get("name") or entry.get("title") or "").strip()
                else:
                    label = str(entry or "").strip()
                if label and label not in output:
                    output.append(label)
            return "; ".join(output)

        raw_items = data.get("items") if isinstance(data.get("items"), list) else []
        records: list[str] = []
        outside_scope_count = 0
        malformed_count = 0
        for item in raw_items:
            if not isinstance(item, dict):
                malformed_count += 1
                continue
            source_tier = LegalCrawlService._dvc_procedure_tier(item)
            if source_tier is None:
                outside_scope_count += 1
                continue
            item_id = str(item.get("id") or "").strip()
            title = _repair_mojibake_text(str(item.get("name") or "").strip())
            if not item_id or not title:
                malformed_count += 1
                continue
            procedure_code = str(item.get("codeNotation") or item.get("code") or "").strip()
            state = str(item.get("state") or "").strip()
            agency = _repair_mojibake_text(str(item.get("departmentPromulgate") or "").strip())
            departments = _repair_mojibake_text(labels(item.get("departments")))
            categories = _repair_mojibake_text(labels(item.get("categories")))
            detail_url = f"https://dichvucong.gov.vn/thu-tuc-hanh-chinh/{item_id}"
            context_parts = [
                f"Mã thủ tục: {procedure_code}" if procedure_code else "",
                f"Cơ quan ban hành: {agency}" if agency else "",
                f"Cơ quan thực hiện: {departments}" if departments else "",
                f"Lĩnh vực: {categories}" if categories else "",
                f"Trạng thái nguồn: {state}" if state else "",
            ]
            context = " | ".join(part for part in context_parts if part)
            records.append(
                "<article "
                'data-crawler-source-type="procedure" '
                f'data-procedure-code="{escape(procedure_code, quote=True)}" '
                f'data-procedure-state="{escape(state, quote=True)}" '
                f'data-procedure-tier="{escape(source_tier, quote=True)}" '
                f'data-procedure-categories="{escape(categories, quote=True)}" '
                f'data-procedure-departments="{escape(departments, quote=True)}">'
                f'<a href="{escape(detail_url, quote=True)}">{escape(title)}</a>'
                f"<p>{escape(context)}</p>"
                "</article>"
            )

        query = dict(parse_qsl(urlparse(listing_url).query, keep_blank_values=True))
        next_id = str(data.get("lastId") or "").strip()
        current_id = str(query.get(DVC_CURSOR_PARAM) or "").strip()
        try:
            seen_count = max(0, int(query.get(DVC_SEEN_PARAM) or 0))
        except (TypeError, ValueError):
            seen_count = 0
        seen_count += len(raw_items)
        try:
            total_count = max(0, int(data.get("total") or 0))
        except (TypeError, ValueError):
            total_count = 0
        next_link = ""
        if (
            raw_items
            and next_id
            and next_id != current_id
            and (not total_count or seen_count < total_count)
        ):
            query[DVC_CURSOR_PARAM] = next_id
            query[DVC_SEEN_PARAM] = str(seen_count)
            parsed = urlparse(listing_url)
            next_url = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(sorted(query.items())),
                    "",
                )
            )
            next_link = (
                f'<a rel="next" href="{escape(next_url, quote=True)}">Trang sau</a>'
            )
        page_valid = bool(raw_items) or total_count == 0 or (
            bool(total_count) and seen_count >= total_count
        )
        return (
            "<html><body "
            f'data-crawler-page-valid="{str(page_valid).lower()}" '
            f'data-crawler-outside-scope="{outside_scope_count}" '
            f'data-crawler-malformed="{malformed_count}">'
            + "".join(records)
            + next_link
            + "</body></html>"
        )

    @staticmethod
    def _canonical_listing_url(url: str) -> str:
        """Keep stable listing/detail URLs while dropping tracking fragments."""
        parsed = urlparse(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith(("utm_", "fbclid", "gclid"))
        ]
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", parsed.params, urlencode(query, doseq=True), ""))

    @staticmethod
    def _is_generic_listing_link(title: str, url: str) -> bool:
        """Reject category/listing navigation before it becomes a candidate."""
        label = " ".join(_plain_text(title).split()).strip(" -:|")
        generic_labels = {
            "van ban", "van ban phap luat", "van ban dia phuong",
            "van ban trung uong", "danh sach van ban", "kho van ban",
            "thu tuc hanh chinh", "danh muc thu tuc hanh chinh",
            "bieu mau", "danh sach bieu mau", "xem tat ca", "xem them",
        }
        if label in generic_labels or label.startswith("danh sach van ban "):
            return True
        path = urlparse(url).path.lower().rstrip("/")
        generic_paths = {
            "/van-ban", "/van-ban/trung-uong", "/van-ban/dia-phuong",
            "/van-ban-quy-pham-phap-luat", "/thu-tuc-hanh-chinh",
            "/danh-muc-thu-tuc-hanh-chinh", "/bieu-mau",
        }
        return path in generic_paths and not _law_number_from_text(title)

    @staticmethod
    def _parse_listing(
        html: str,
        base_url: str,
        source: Mapping[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Parse exact document/form/procedure records; never fetch details."""
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        source = source or {}
        website_type = LegalCrawlService._source_website_type(source)
        link_selector = str(source.get("link_selector") or "").strip()
        selector = link_selector or "a[href]"
        try:
            anchors = soup.select(selector)
        except Exception as exc:
            raise ValueError("CSS selector liên kết không hợp lệ.") from exc
        include_patterns = [value.casefold() for value in LegalCrawlService._normalize_source_patterns(source.get("include_patterns"))]
        exclude_patterns = [value.casefold() for value in LegalCrawlService._normalize_source_patterns(source.get("exclude_patterns"))]
        keyword_terms = [
            term.casefold()
            for term in re.split(r"[,;\n]+", str(source.get("filter_keyword") or ""))
            if term.strip()
        ]
        configured_discovery = bool(link_selector or include_patterns)
        for selected in anchors:
            anchor = selected if getattr(selected, "name", None) == "a" else selected.find("a", href=True)
            if anchor is None or not anchor.get("href"):
                continue
            href = (anchor.get("href") or "").strip()
            title = _repair_mojibake_text(
                " ".join(anchor.get_text(" ", strip=True).split())
            )
            if not href or not title or href.startswith(("#", "javascript:", "mailto:")):
                continue
            url = LegalCrawlService._canonical_listing_url(urljoin(base_url, href))
            if url == LegalCrawlService._canonical_listing_url(base_url) or url in seen:
                continue
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            comparable_url = url.casefold()
            if include_patterns and not any(pattern in comparable_url for pattern in include_patterns):
                continue
            if exclude_patterns and any(pattern in comparable_url for pattern in exclude_patterns):
                continue
            # Skip pagination / navigation anchors so they never become candidates.
            anchor_rel = " ".join(anchor.get("rel") or []).lower()
            if anchor_rel == "next" or _is_next_listing_label(
                anchor.get_text(" ", strip=True)
            ):
                continue

            container = anchor.find_parent(["article", "li", "tr", "div"])
            explicit_type = str(
                (container.get("data-crawler-source-type") if container else "") or ""
            ).strip().lower()
            if not explicit_type and LegalCrawlService._is_generic_listing_link(title, url):
                continue
            context = _repair_mojibake_text(
                " ".join(container.get_text(" ", strip=True).split())
                if container
                else title
            )
            path = parsed.path.lower().rstrip("/")
            host = (parsed.hostname or "").lower().rstrip(".")
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            is_form = path.endswith(FORM_EXTENSIONS)
            is_haiphong_steering_record = (
                host == "haiphong.gov.vn"
                and path in {"", "/"}
                and bool(query.get("p_steering"))
                and query.get("pageid") == "27218"
            )
            is_haiphong_procedure_publication = (
                host == "haiphong.gov.vn"
                and path.startswith("/danh-muc-thu-tuc-hanh-chinh/")
            )
            is_haiphong_legal_detail = (
                (host == "haiphong.gov.vn" or host.endswith(".haiphong.gov.vn"))
                and path.startswith("/van-ban-quy-pham-phap-luat/")
            )
            looks_like_document = (
                "/van-ban/" in path
                or "toanvan" in url.lower()
                or "/chi-tiet/" in path
                or is_haiphong_steering_record
                or is_haiphong_procedure_publication
                or is_haiphong_legal_detail
            )
            identity_text = f"{title} {context}"
            title_has_legal_identity = bool(
                _law_number_from_text(identity_text)
                or re.search(
                    r"\b(Luật|Nghị định|Thông tư|Quyết định|Nghị quyết|Chỉ thị)\b",
                    identity_text,
                    re.IGNORECASE,
                )
            )
            if explicit_type == "procedure":
                source_type = "procedure"
            elif explicit_type == "form":
                source_type = "form"
            elif explicit_type == "document":
                source_type = "document"
            elif website_type == "reference" and configured_discovery:
                source_type = "reference"
            elif website_type == "forms" and is_form:
                source_type = "form"
            elif website_type == "procedures" and (
                "thu-tuc" in path
                or "procedure" in path
                or is_haiphong_procedure_publication
                or (configured_discovery and "thủ tục" in identity_text.casefold())
            ):
                source_type = "procedure"
            elif website_type == "legal_documents" and (
                looks_like_document
                or (configured_discovery and title_has_legal_identity)
            ):
                source_type = "document"
            elif is_form:
                source_type = "form"
            elif looks_like_document:
                source_type = "document"
            else:
                continue
            if keyword_terms:
                searchable = f"{title} {context} {url}".casefold()
                if not any(term.strip() in searchable for term in keyword_terms):
                    continue
            seen.add(url)
            metadata_text = context
            number = _law_number_from_text(metadata_text)
            issued_date = _labelled_listing_date(
                metadata_text, "Ngày ban hành", "Ngày ký"
            ) or _issued_date_from_text(metadata_text)
            effective_date = _labelled_listing_date(
                metadata_text, "Ngày hiệu lực", "Ngày có hiệu lực"
            )
            document_type = ""
            doc_match = re.search(r"\b(Lu\u1eadt|Ngh\u1ecb \u0111\u1ecbnh|Th\u00f4ng t\u01b0|Quy\u1ebft \u0111\u1ecbnh|Ngh\u1ecb quy\u1ebft|Ch\u1ec9 th\u1ecb)\b", metadata_text, re.IGNORECASE)
            if doc_match:
                document_type = doc_match.group(1)
            agency = ""
            agency_match = re.search(r"(?:Cơ quan ban hành|Ban hành bởi|Cơ quan):\s*([^|;\n]{3,160})", metadata_text, re.IGNORECASE)
            if agency_match:
                agency = agency_match.group(1).strip()
            if source_type == "procedure":
                # A DVC procedure code (for example 1.001193) is not a legal
                # document number. Keep it in procedure metadata and never let
                # a cited law number in the title become this record's identity.
                number = None
                document_type = document_type or "Thủ tục hành chính"
            results.append({
                "url": url,
                "title": title[:1000],
                "context": context[:4000],
                "source_type": source_type,
                "law_number": number or "",
                "issued_date": issued_date or "",
                "effective_date": effective_date or "",
                "document_type": document_type,
                "issuing_agency": agency,
                "procedure_code": str(
                    (container.get("data-procedure-code") if container else "") or ""
                )[:255],
                "procedure_state": str(
                    (container.get("data-procedure-state") if container else "") or ""
                )[:100],
                "procedure_tier": str(
                    (container.get("data-procedure-tier") if container else "") or ""
                )[:100],
                "procedure_categories": str(
                    (container.get("data-procedure-categories") if container else "") or ""
                )[:1000],
                "procedure_departments": str(
                    (container.get("data-procedure-departments") if container else "") or ""
                )[:2000],
            })
        return results

    @staticmethod
    def _has_unfollowable_pagination(html: str) -> bool:
        """Detect ASP.NET postback paging without guessing a request payload."""
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select('a[href^="javascript:__doPostBack"]'):
            if "page$" in str(anchor.get("href") or "").lower():
                return True
        return False

    @staticmethod
    def _listing_page_diagnostics(html: str) -> dict[str, int | bool]:
        """Read adapter diagnostics without turning filtered records into errors."""
        soup = BeautifulSoup(html, "html.parser")
        body = soup.body
        if body is None:
            return {"valid": False, "outside_scope": 0, "malformed": 0}

        def count(name: str) -> int:
            try:
                return max(0, int(body.get(name) or 0))
            except (TypeError, ValueError):
                return 0

        return {
            "valid": str(body.get("data-crawler-page-valid") or "").lower() == "true",
            "outside_scope": count("data-crawler-outside-scope"),
            "malformed": count("data-crawler-malformed"),
        }

    @staticmethod
    def _next_listing_url(
        html: str,
        current_url: str,
        base_url: str,
        source: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Find one safe next-page link. Page navigation is never guessed."""
        soup = BeautifulSoup(html, "html.parser")
        selector = str((source or {}).get("next_page_selector") or "").strip()
        try:
            candidates = soup.select(
                selector or 'a[rel="next"][href], .pagination a[href], .pager a[href], a[href*="page="]'
            )
        except Exception as exc:
            raise ValueError("CSS selector trang tiếp theo không hợp lệ.") from exc
        current = LegalCrawlService._canonical_listing_url(current_url)
        root = urlparse(base_url)
        for anchor in candidates:
            rel = " ".join(anchor.get("rel") or []).lower()
            if not selector and rel != "next" and not _is_next_listing_label(
                anchor.get_text(" ", strip=True)
            ):
                continue
            candidate = LegalCrawlService._canonical_listing_url(urljoin(current_url, anchor.get("href") or ""))
            parsed = urlparse(candidate)
            if not candidate or candidate == current or parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc != root.netloc or not parsed.path.startswith(root.path.rstrip("/")):
                continue
            return candidate
        return None

    @classmethod
    async def _already_known(
        cls,
        source_url: str,
        law_number: str | None,
        issued_date: str | None,
        fingerprint: str,
        incoming: dict[str, Any] | None = None,
    ) -> bool:
        """Suppress the same observation, not a changed document at the same URL.

        Archived candidates stay in this read: their original evidence is the
        tombstone. A newly fetched body is compared, never hidden by that marker.
        """
        incoming = incoming or {"source_url": source_url, "law_number": law_number,
                                "raw_metadata": {"issued_date": issued_date, "metadata_only": True}}
        identity = legal_identity(incoming)
        rows = await candidate_duplicate_rows([incoming], query=repo_query)
        for row in rows:
            comparison = compare_legal_documents(incoming, row)
            if comparison["kind"] == "duplicate_content":
                return True
            if comparison["kind"] != "identity_match" or not identity["source_url"]:
                continue
            previous = legal_identity(row)
            if identity["source_url"] != previous["source_url"]:
                continue
            # A full-text observation must not be swallowed by an old listing
            # that never fetched the body. It gets a versioned candidate key.
            if identity["content_sha256"]:
                continue
            raw = row.get("raw_metadata") or {}
            incoming_context = (incoming.get("raw_metadata") or {}).get("listing_context")
            if row.get("content_hash") == fingerprint or (incoming_context and incoming_context == raw.get("listing_context")):
                return True
        return False

    @classmethod
    async def _create_listing_candidate(
        cls, source: dict[str, Any], item: dict[str, str], run_id: Any
    ) -> tuple[bool, str | None]:
        title = item["title"]
        context = item["context"]
        source_type = str(item.get("source_type") or "document")
        law_number = (
            None
            if source_type == "procedure"
            else item.get("law_number") or _law_number_from_text(f"{title} {context}")
        )
        issued_date = item.get("issued_date") or _issued_date_from_text(context)
        effective_date = item.get("effective_date") or _labelled_listing_date(
            context, "Ngày hiệu lực", "Ngày có hiệu lực"
        )
        document_type = item.get("document_type") or None
        issuing_agency = item.get("issuing_agency") or None
        content = ""
        extraction_result: dict[str, Any] | None = None
        normalized: dict[str, Any] | None = None
        matched_domains: list[str] = []
        domain_evidence_map: dict[str, list[str]] = {}
        should_fetch_detail = (
            source_type == "document"
            and _detail_fetch_enabled()
            and bool(source.get("content_fetch_allowed"))
        )
        if should_fetch_detail:
            normalized = await fetch_normalized_legal_document(
                item["url"],
                scope=str(source.get("sitemap_scope") or "central"),
                timeout_seconds=45,
                compatibility_mode=str(source.get("compatibility_mode") or "standard"),
            )
            if normalized.get("status") == "rejected" or not normalized.get("clean_markdown"):
                return False, "extraction_failed"
            title = str(normalized.get("title") or title)
            law_number = normalized.get("law_number") or law_number
            issued_date = normalized.get("issued_date") or issued_date
            effective_date = normalized.get("effective_date") or effective_date
            document_type = normalized.get("document_type") or document_type
            issuing_agency = normalized.get("issuing_agency") or issuing_agency
            content = str(normalized.get("clean_markdown") or "")
            extraction_result = dict(normalized.get("extraction") or {})
            extraction_result["characters"] = len(content)
            matched_domains = [
                str(value) for value in normalized.get("matched_domains") or []
                if str(value) in LEGAL_DOMAINS
            ]
            domain_evidence_map = {
                str(key): [str(value) for value in values]
                for key, values in (normalized.get("domain_evidence") or {}).items()
                if str(key) in LEGAL_DOMAINS and isinstance(values, list)
            }

        fingerprint_seed = "|".join((str(law_number or ""), str(issued_date or ""), context, item["url"])).strip()
        fingerprint = str((normalized or {}).get("content_hash") or "") or hashlib.sha256(
            fingerprint_seed.encode("utf-8")
        ).hexdigest()
        domain, domain_evidence = _deterministic_domain(title, issuing_agency or "", document_type or "", content or context)
        if matched_domains:
            domain = str((normalized or {}).get("primary_domain") or matched_domains[0])
            domain_evidence = domain_evidence_map.get(domain, [])
        allowed_domains = set(source.get("domains") or LEGAL_DOMAINS)
        if not domain:
            # Keep a discovered form in the admin review queue even when its
            # filename/title is too generic to route safely to one of the five
            # officer domains.  It remains unclassified, pending, and invisible
            # to officers until an admin assigns a verified domain.
            # A source configured for the complete catalog must follow the same
            # lossless rule for legal documents.  Finance, defence and other
            # records outside the legacy five-domain taxonomy are still valid
            # review candidates; dropping them here made a successful crawl
            # look incomplete.  A deliberately narrowed source keeps its old
            # domain filter and may reject out-of-scope records.
            catalog_is_unrestricted = allowed_domains.issuperset(LEGAL_DOMAINS)
            if source_type in {"form", "procedure", "reference"} or catalog_is_unrestricted:
                domain = "unclassified"
                domain_evidence = [f"{source_type}:requires_admin_domain_assignment"]
            else:
                return False, "outside_domain"
        if matched_domains and not allowed_domains.intersection(matched_domains):
            return False, "outside_domain"
        if domain != "unclassified" and domain not in allowed_domains:
            return False, "outside_domain"
        metadata = {
            "candidate_origin": (
                "vbpl_listing_scan"
                if source.get("source_type") == "vbpl_listing"
                else "official_listing_scan"
            ),
            "listing_context": context,
            "issued_date": issued_date,
            "effective_date": (normalized or {}).get("effective_date") or effective_date,
            "expired_date": (normalized or {}).get("expired_date"),
            "crawl_run": str(run_id),
            "metadata_only": not bool(content),
            "content_fetch_allowed": bool(source.get("content_fetch_allowed")),
            "domain_mapping": {"domain": domain, "evidence": domain_evidence},
            "matched_domains": matched_domains or ([domain] if domain in LEGAL_DOMAINS else []),
            "domain_evidence": domain_evidence_map or ({domain: domain_evidence} if domain in LEGAL_DOMAINS else {}),
            "final_url": (normalized or {}).get("final_url") or item["url"],
            "confirmed_official_source": False,
            "procedure_code": item.get("procedure_code") or None,
            "procedure_state": item.get("procedure_state") or None,
            "procedure_tier": item.get("procedure_tier") or None,
            "procedure_categories": item.get("procedure_categories") or None,
            "procedure_departments": item.get("procedure_departments") or None,
            "website_type": cls._source_website_type(source),
        }
        organization_assignment = await cls._organization_assignment_for_candidate(
            domain=domain,
            source=source,
        )
        payload = {
            "source": ensure_record_id(source["id"]),
            "detail_url": item["url"],
            "source_url": item["url"],
            "sitemap_url": source.get("base_url"),
            "sitemap_lastmod": None,
            "law_number": law_number,
            "title": title,
            "description": context,
            "document_type": document_type,
            "issuing_agency": issuing_agency,
            "scope": source.get("sitemap_scope") or "central",
            "status": "pending",
            "review_status": "pending",
            "suggested_action": "review_listing_metadata",
            "comparison_status": "new",
            "detected_changes": [],
            "review_note": None,
            "imported_document": None,
            "raw_metadata": metadata,
            "content_hash": fingerprint,
            "created": _utcnow(),
            "updated": _utcnow(),
            "domain": domain,
            **organization_assignment,
            "source_type": source_type,
            "proposal_reason": (
                f"Phát hiện tự động từ {str(source.get('name') or 'nguồn chính thức')[:255]}; "
                "chờ admin kiểm tra nguồn, nội dung và hiệu lực."
            ),
            "submitted_by": None,
        }
        if content:
            payload["content"] = content
        if extraction_result:
            payload["extraction_result"] = extraction_result
        metadata["legal_identity"] = identity_metadata(payload)
        payload["external_id"] = candidate_external_id(payload, origin=f"crawl:{source_type}")
        if await cls._already_known(item["url"], law_number, issued_date, fingerprint, payload):
            return False, "duplicate"
        try:
            created = await repo_create("legal_crawl_candidate", payload)
        except RuntimeError:
            # Two concurrent scans can observe the same absent candidate. The
            # existing unique external_id index is the final idempotency guard.
            existing = await repo_query(
                "SELECT id FROM legal_crawl_candidate WHERE external_id = $external_id LIMIT 1;",
                {"external_id": payload["external_id"]},
            )
            if existing:
                return False, "duplicate"
            raise
        candidate = created[0] if isinstance(created, list) else created
        if isinstance(candidate, dict):
            await cls.persist_automatic_assessment({**payload, **candidate})
        return True, None

    @classmethod
    async def scan_source(cls, source_id: str) -> dict[str, Any]:
        """Run exactly one scan per source in this API process.

        The background scheduler and an Admin request can fire at the same
        time. Returning ``busy`` is safer than allowing both runs to advance
        the same persisted cursor and overwrite one another's status.
        """
        scan_key = str(source_id)
        if not _claim_source_scan(scan_key):
            return {
                "status": "busy",
                "source_id": scan_key,
                "reason": "Nguồn đang được quét bởi một tác vụ khác.",
            }
        try:
            return await cls._scan_source_once(source_id)
        finally:
            _release_source_scan(scan_key)

    @classmethod
    async def _scan_source_once(cls, source_id: str) -> dict[str, Any]:
        """Scan one source page-by-page into pending candidates only.

        A cursor is persisted after every successful page.  This lets a source
        with hundreds of pages progress safely across scheduled, bounded runs;
        it does not fan out or fetch document/PDF detail URLs.
        """
        started_tick = asyncio.get_running_loop().time()
        source_rows = await repo_query(
            "SELECT * FROM legal_crawl_source WHERE id = $id LIMIT 1;",
            {"id": ensure_record_id(source_id)},
        )
        if not source_rows:
            raise ValueError("Không tìm thấy nguồn crawler.")
        source = source_rows[0]
        source_type = str(source.get("source_type") or "unknown")
        if cls.is_internal_source(source):
            await repo_update("legal_crawl_source", source["id"], {
                "last_status": "internal",
                "last_error": None,
                "updated": _utcnow(),
            })
            return {
                "status": "internal",
                "source_id": str(source["id"]),
                "reason": "Đây là hàng đợi nội bộ, không quét web.",
            }
        if not cls.is_crawlable_source(source):
            return {
                "status": "skipped",
                "source_id": str(source["id"]),
                "reason": "Loại nguồn chưa được hỗ trợ cho quét web an toàn.",
            }
        if not source.get("enabled", False):
            telemetry.record_operation(
                category="crawler",
                route="worker:listing_scan",
                duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000,
                outcome="skipped",
                metadata={"source_type": source_type},
            )
            return {"status": "skipped", "source_id": source_id, "reason": "Nguồn đang tắt."}

        now = _utcnow()
        run = await repo_create("legal_crawl_run", {
            "source": ensure_record_id(source["id"]), "status": "running", "started_at": now,
            "finished_at": None, "statistics": {}, "failure_reason": None,
            "source_freshness": None,
        })
        run_record = run[0] if isinstance(run, list) else run
        run_id = run_record.get("id") if isinstance(run_record, dict) else run_record
        max_per_run = max(1, min(int(source.get("max_documents_per_run") or 30), 200))
        # Listing traversal is bounded per run, but the persisted cursor lets
        # scheduled runs eventually cover catalogs with hundreds of pages.
        # The default is intentionally conservative; only an admin can raise it.
        max_pages = max(1, min(int(source.get("max_listing_pages_per_run") or 10), 50))
        stats = {
            "discovered": 0, "created": 0, "duplicates": 0, "outside_domain": 0,
            "forms": 0,
        }
        pagination_stats = {"listing_pages": 0, "cursor_reset": 0}
        cursor = str(source.get("listing_cursor") or source["base_url"])
        completed_catalog = False
        next_cursor: str | None = cursor
        try:
            allowed, reason = await cls._robots_allowed(source["base_url"])
            if not allowed:
                raise RuntimeError(reason or "robots.txt không cho phép quét")

            for _page_index in range(max_pages):
                if not next_cursor:
                    break
                current_page_cursor = next_cursor
                parsed_cursor = urlparse(current_page_cursor)
                if (
                    (parsed_cursor.hostname or "").lower() == "dichvucong.gov.vn"
                    and parsed_cursor.path.rstrip("/") == DVC_LISTING_PATH.rstrip("/")
                ):
                    cursor_query = dict(parse_qsl(parsed_cursor.query, keep_blank_values=True))
                    cursor_query[DVC_LIMIT_PARAM] = str(
                        max(1, min(max_per_run, DVC_MAX_PAGE_SIZE))
                    )
                    current_page_cursor = urlunparse(
                        (
                            parsed_cursor.scheme,
                            parsed_cursor.netloc,
                            parsed_cursor.path,
                            parsed_cursor.params,
                            urlencode(sorted(cursor_query.items())),
                            "",
                        )
                    )
                # Only listing URLs discovered from same-origin pagination are used.
                if str(source.get("compatibility_mode") or "standard") == "high":
                    html = await cls._fetch_with_backoff(
                        current_page_cursor,
                        float(source.get("rate_limit_seconds") or 1.5),
                        "high",
                    )
                else:
                    html = await cls._fetch_with_backoff(
                        current_page_cursor,
                        float(source.get("rate_limit_seconds") or 1.5),
                    )
                pagination_stats["listing_pages"] += 1
                items = cls._parse_listing(html, current_page_cursor, source)
                page_diagnostics = cls._listing_page_diagnostics(html)
                if not items and str(source.get("filter_keyword") or "").strip():
                    filtered_count = len(cls._parse_listing(html, current_page_cursor, {**source, "filter_keyword": ""}))
                    if filtered_count:
                        page_diagnostics = {**page_diagnostics, "valid": True}
                        stats["filtered_out"] = stats.get("filtered_out", 0) + filtered_count
                outside_scope = int(page_diagnostics.get("outside_scope") or 0)
                malformed = int(page_diagnostics.get("malformed") or 0)
                if outside_scope:
                    stats["outside_scope"] = stats.get("outside_scope", 0) + outside_scope
                if malformed:
                    stats["item_errors"] = stats.get("item_errors", 0) + malformed
                if not items and not page_diagnostics.get("valid"):
                    host = (urlparse(current_page_cursor).hostname or "").lower()
                    source_failure = (
                        "vbpl_official_service_unavailable: "
                        if host == "vbpl.vn" or host.endswith(".vbpl.vn")
                        else ""
                    )
                    raise RuntimeError(
                        source_failure
                        + "listing_no_exact_records: trang nguồn không trả bản ghi "
                        "văn bản có thể kiểm chứng"
                    )
                # max_documents_per_run limits *new candidates* across the entire
                # run, not parsed listing links.  We finish the current page so
                # duplicate records cannot trap the cursor, then continue on the
                # next scheduled/admin run when the new-candidate budget is full.
                candidate_limit_reached = False
                for item in items:
                    stats["discovered"] += 1
                    if item["source_type"] == "form":
                        stats["forms"] += 1
                    elif item["source_type"] == "procedure":
                        stats["procedures"] = stats.get("procedures", 0) + 1
                    if stats["created"] >= max_per_run:
                        candidate_limit_reached = True
                        continue
                    try:
                        created, skipped = await cls._create_listing_candidate(source, item, run_id)
                    except Exception as exc:
                        # One malformed/unavailable detail record must not keep
                        # the source cursor stuck on this page forever. Preserve
                        # a numeric warning only; URLs and document text are not
                        # written to operational telemetry.
                        stats["item_errors"] = stats.get("item_errors", 0) + 1
                        logger.warning(
                            "Crawler candidate item failed: source={} error={}",
                            str(source.get("id") or source_id),
                            exc.__class__.__name__,
                        )
                        continue
                    if created:
                        stats["created"] += 1
                    elif skipped == "duplicate":
                        stats["duplicates"] += 1
                    elif skipped == "outside_domain":
                        stats["outside_domain"] += 1
                    elif skipped == "extraction_failed":
                        stats["extraction_failed"] = stats.get("extraction_failed", 0) + 1
                discovered_next_cursor = cls._next_listing_url(
                    html, current_page_cursor, source["base_url"], source
                )
                if candidate_limit_reached:
                    # Retry this exact page on the next run. Newly-created rows
                    # will then deduplicate, allowing the remaining records to
                    # be processed without silently dropping the tail of a page.
                    next_cursor = current_page_cursor
                else:
                    next_cursor = discovered_next_cursor
                if not next_cursor and cls._has_unfollowable_pagination(html):
                    # Some official ASP.NET listings expose only a JavaScript
                    # postback pager. Guessing its hidden form state can skip or
                    # duplicate records, so retain the current page and surface
                    # bounded coverage as a visible warning.
                    pagination_stats["pagination_unavailable"] = 1
                    next_cursor = current_page_cursor
                # Persist progress after every successfully parsed page, not
                # only at the end of the whole run. If the API process stops
                # between pages, the next run resumes from the next safe URL.
                page_cursor = next_cursor or source["base_url"]
                await repo_update(
                    "legal_crawl_source",
                    source["id"],
                    {"listing_cursor": page_cursor, "updated": _utcnow()},
                )
                if pagination_stats.get("pagination_unavailable"):
                    break
                if not next_cursor:
                    completed_catalog = True
                    break
                if candidate_limit_reached or stats["created"] >= max_per_run:
                    # The next safe cursor is already persisted. Stop before
                    # downloading another page that cannot create candidates in
                    # this bounded run.
                    break
                # A successful listing page must respect the configured pacing
                # before the next page request; retry backoff is additional.
                await asyncio.sleep(max(0.2, min(float(source.get("rate_limit_seconds") or 1.5), 30.0)))

            if completed_catalog:
                persisted_cursor = source["base_url"]
                pagination_stats["cursor_reset"] = 1
            else:
                persisted_cursor = next_cursor or cursor
            warning_count = (
                int(stats.get("item_errors") or 0)
                + int(stats.get("extraction_failed") or 0)
                + int(pagination_stats.get("pagination_unavailable") or 0)
            )
            if warning_count:
                status = "completed_with_warnings"
                warning_reasons: list[str] = []
                failed_items = int(stats.get("item_errors") or 0) + int(
                    stats.get("extraction_failed") or 0
                )
                if failed_items:
                    warning_reasons.append(
                        f"{failed_items} bản ghi không xử lý được"
                    )
                if pagination_stats.get("pagination_unavailable"):
                    warning_reasons.append(
                        "nguồn dùng phân trang postback nên mới quét được trang hiện tại"
                    )
                failure_reason = (
                    "Phiên quét hoàn tất có cảnh báo: "
                    + "; ".join(warning_reasons)
                    + ". Các bản ghi hợp lệ vẫn được lưu vào hàng chờ."
                )
            else:
                status = "completed"
                failure_reason = None
        except Exception as exc:
            status = "failed"
            failure_reason = cls._public_crawl_failure(exc)
            persisted_cursor = cursor
            logger.warning(
                "Crawler source failed: source={} error={}",
                source_id,
                exc.__class__.__name__,
            )

        finished_at = _utcnow()
        await repo_update("legal_crawl_run", run_id, {
            "status": status, "finished_at": finished_at, "statistics": stats,
            "failure_reason": failure_reason,
            "source_freshness": finished_at if status in {"completed", "completed_with_warnings"} else None,
        })
        await repo_update("legal_crawl_source", source["id"], {
            "last_checked_at": finished_at,
            "last_success_at": finished_at if status == "completed" else source.get("last_success_at"),
            "last_error": failure_reason,
            "last_run_stats": {**stats, **pagination_stats},
            "source_freshness": (
                finished_at
                if status in {"completed", "completed_with_warnings"}
                else source.get("source_freshness")
            ),
            "listing_cursor": persisted_cursor,
            "last_status": status,
            "updated": finished_at,
        })
        telemetry.record_operation(
            category="crawler",
            route="worker:listing_scan",
            duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000,
            outcome=(
                "success" if status == "completed"
                else "degraded" if status == "completed_with_warnings"
                else "failed"
            ),
            metadata={"source_type": source_type},
        )
        if status == "failed":
            telemetry.record_issue("crawler_failed", category="crawler", error_class="listing_scan")
        elif status == "completed_with_warnings":
            telemetry.record_issue(
                "crawler_degraded",
                category="crawler",
                error_class=(
                    "pagination_unavailable"
                    if pagination_stats.get("pagination_unavailable")
                    else "candidate_item"
                ),
            )
        if stats["created"]:
            await repo_create("legal_admin_notification", {
                "type": "crawler_candidates", "title": "Có văn bản mới chờ duyệt",
                "message": f"Nguồn {source.get('name', source_id)} phát hiện {stats['created']} candidate; chưa có văn bản nào được import.",
                "candidate": None, "read_at": None, "created": finished_at, "updated": finished_at,
            })
        return {
            "status": status,
            "source_id": str(source["id"]),
            "run_id": str(run_id),
            "statistics": stats,
            "pagination": pagination_stats,
            "failure_reason": failure_reason,
            "next_listing_cursor": persisted_cursor,
            "catalog_completed": (
                completed_catalog
                if status in {"completed", "completed_with_warnings"}
                else False
            ),
        }

    @classmethod
    async def source_status(cls) -> list[dict[str, Any]]:
        """Return source freshness and configuration safe for the admin dashboard."""
        sources = await cls.list_sources()
        statuses = []
        for source in sources:
            item = cls._source_for_admin(source)
            item.update({
                "id": str(source.get("id") or ""),
                "name": source.get("name"),
                "scope": source.get("sitemap_scope"),
                "source_type": source.get("source_type"),
                "base_url": source.get("base_url"),
                "domains": list(source.get("domains") or LEGAL_DOMAINS),
                "enabled": bool(source.get("enabled")),
                "interval_minutes": int(source.get("interval_minutes") or WEEKLY_CRAWL_INTERVAL_MINUTES),
                "listing_cursor": source.get("listing_cursor") or source.get("base_url"),
                "last_checked_at": source.get("last_checked_at"),
                "last_success_at": source.get("last_success_at"),
                "source_freshness": source.get("source_freshness"),
                "last_status": source.get("last_status"),
                "last_error": source.get("last_error"),
                "last_run_stats": source.get("last_run_stats") or {},
                "lookback_days": int(source.get("lookback_days") or 30),
                "max_documents_per_run": int(source.get("max_documents_per_run") or 30),
                "max_listing_pages_per_run": int(source.get("max_listing_pages_per_run") or 10),
                "rate_limit_seconds": float(source.get("rate_limit_seconds") or 1.5),
                "filter_keyword": source.get("filter_keyword"),
                "website_type": cls._source_website_type(source),
                "link_selector": source.get("link_selector"),
                "next_page_selector": source.get("next_page_selector"),
                "include_patterns": cls._normalize_source_patterns(source.get("include_patterns")),
                "exclude_patterns": cls._normalize_source_patterns(source.get("exclude_patterns")),
            })
            statuses.append(item)
        return statuses

    @staticmethod
    def summarize_scan_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
        """Build one stable operational result for scheduled and manual scans."""
        statuses = [str(run.get("status") or "failed").strip().lower() for run in runs]
        failed_count = statuses.count("failed")
        warning_count = statuses.count("completed_with_warnings")
        busy_count = statuses.count("busy")
        skipped_count = sum(status in {"skipped", "internal"} for status in statuses)
        created = sum(int((run.get("statistics") or {}).get("created") or 0) for run in runs)
        updated = sum(int((run.get("statistics") or {}).get("updated") or 0) for run in runs)

        if not runs:
            status = "skipped"
        elif failed_count == len(runs):
            status = "failed"
        elif busy_count == len(runs):
            status = "busy"
        elif warning_count and not failed_count and not busy_count:
            status = "completed_with_warnings"
        elif failed_count or warning_count or busy_count:
            status = "partial"
        elif skipped_count == len(runs):
            status = "skipped"
        else:
            status = "completed"

        errors = []
        for run, run_status in zip(runs, statuses):
            if run_status not in {"failed", "completed_with_warnings", "busy"}:
                continue
            message = str(run.get("failure_reason") or run.get("reason") or "").strip()
            errors.append({
                "source_id": str(run.get("source_id") or ""),
                "status": run_status,
                "message": message[:500] or "Không có thông tin lỗi chi tiết.",
            })

        return {
            "status": status,
            "runs": runs,
            "run_count": len(runs),
            "created": created,
            "updated": updated,
            "failed_count": failed_count,
            "warning_count": warning_count,
            "busy_count": busy_count,
            "skipped_count": skipped_count,
            "errors": errors,
        }

    @classmethod
    async def scan_due_sources(cls) -> dict[str, Any]:
        """Run sources that are due; a slow/broken source does not stop others."""
        sources = await cls.ensure_vbpl_sources()
        now = _utcnow()
        results = []
        for source in sources:
            if not source.get("enabled", False) or not cls.is_crawlable_source(source):
                continue
            last_checked = cls._as_utc_datetime(source.get("last_checked_at"))
            interval = timedelta(minutes=max(15, int(source.get("interval_minutes") or WEEKLY_CRAWL_INTERVAL_MINUTES)))
            if last_checked and last_checked + interval > now:
                continue
            try:
                results.append(await cls.scan_source(str(source["id"])))
            except Exception as exc:
                logger.warning(
                    "Crawler due-source dispatch failed: source={} error={}",
                    str(source.get("id") or ""),
                    exc.__class__.__name__,
                )
                results.append({
                    "status": "failed",
                    "source_id": str(source.get("id") or ""),
                    "failure_reason": str(exc)[:500] or exc.__class__.__name__,
                    "statistics": {},
                })
        return cls.summarize_scan_runs(results)

    @classmethod
    async def list_candidates(
        cls,
        status: str | None = None,
        limit: int = 100,
        domain: str | None = None,
        source_type: str | None = None,
        offset: int = 0,
        origin: str | None = None,
        summary_only: bool = False,
        submitted_by: str | None = None,
        check_duplicates: bool = True,
    ) -> list[dict[str, Any]]:
        """List candidate metadata with optional exact review filters."""
        try:
            where, params = _candidate_list_filter(status, domain, source_type, origin)
            if submitted_by is not None:
                owner = str(submitted_by).strip()
                if not owner:
                    return []
                # Restrict ownership before pagination and expensive projection
                # work. Preserve old string IDs alongside typed record IDs.
                owner_record = owner if ":" in owner else f"user_account:{owner}"
                where += (" AND " if where else " WHERE ") + (
                    "submitted_by IN [$owner, $owner_record, $owner_suffix, type::record($owner_record)]"
                )
                params.update(owner=owner, owner_record=owner_record, owner_suffix=owner.split(":", 1)[-1])
            params.update(limit=min(max(int(limit), 1), 200), offset=max(int(offset), 0))
            rows = await repo_query(
                f"SELECT * FROM legal_crawl_candidate{where} ORDER BY created DESC, id ASC LIMIT $limit START $offset;",
                params,
            )
            repaired_rows = _repair_display_metadata(rows)
            candidate_rows = [row for row in repaired_rows if isinstance(row, dict)]
            enriched_rows = list(await asyncio.gather(*(
                cls._enrich_import_projection(row) for row in candidate_rows
            ))) if candidate_rows else []
            actionable = [row for row in enriched_rows if row.get("status") in {"pending", "changes_requested", "import_failed", "approved"}]
            annotated = await annotate_candidate_duplicates(actionable, LEGAL_MANAGEMENT_URL) if actionable and check_duplicates else []
            by_id = {str(row.get("id")): row for row in annotated}
            return [_compact_candidate_list_item({
                **row,
                "duplicate_resolution": (row.get("raw_metadata") or {}).get("duplicate_resolution"),
                **by_id.get(str(row.get("id")), {}),
            }, summary_only=summary_only) for row in enriched_rows]
        except Exception as e:
            logger.error("Error listing candidates: {}", e.__class__.__name__)
            raise DuplicateCheckUnavailable("Không tải được hàng chờ văn bản. Hãy thử lại; dữ liệu chưa bị xóa.") from e

    @classmethod
    async def list_candidate_page(cls, *, status=None, domain=None, source_type=None, origin=None, limit=20, offset=0):
        limit = min(max(int(limit), 1), 200)
        offset = max(int(offset), 0)
        where, params = _candidate_list_filter(status, domain, source_type, origin)
        try:
            counts, candidates = await asyncio.gather(
                repo_query(f"SELECT count() AS total FROM legal_crawl_candidate{where} GROUP ALL;", params),
                cls.list_candidates(
                    status=status, domain=domain, source_type=source_type, origin=origin,
                    limit=limit, offset=offset, summary_only=True,
                    # Duplicate enforcement remains mandatory in detail,
                    # approval and import actions. The bounded listing reads
                    # the stored resolution only and must not run warehouse
                    # duplicate analysis for every row on first paint.
                    check_duplicates=False,
                ),
            )
            total = int(counts[0].get("total", 0)) if counts else 0
            return {"candidates": candidates, "total": total, "limit": limit, "offset": offset}
        except DuplicateCheckUnavailable:
            raise
        except Exception as exc:
            logger.error("Error counting candidate page: {}", exc.__class__.__name__)
            raise DuplicateCheckUnavailable("Không tải được hàng chờ văn bản. Hãy thử lại; dữ liệu chưa bị xóa.") from exc

    @classmethod
    async def get_notifications(cls, unread_only: bool = False) -> list[dict[str, Any]]:
        """Get crawl notifications."""
        try:
            if unread_only:
                rows = await repo_query("SELECT * FROM legal_admin_notification WHERE read_at = NONE;")
            else:
                rows = await repo_query("SELECT * FROM legal_admin_notification;")
            return _repair_display_metadata(rows)
        except Exception as e:
            logger.error(f"Error getting notifications: {e}")
            return []

    @classmethod
    async def mark_notification_read(cls, notification_id: str) -> dict[str, Any]:
        """Mark notification as read."""
        try:
            rows = await repo_update("legal_admin_notification", notification_id, {"read_at": _utcnow()})
            return rows[0] if rows else {}
        except Exception as e:
            logger.error(f"Error marking notification as read: {e}")
            return {}

async def legal_crawl_scheduler_tick() -> dict[str, Any]:
    """Run independent discovery jobs once without cascading failures."""
    outcomes: dict[str, Any] = {}
    try:
        result = await LegalCrawlService.scan_due_sources()
        outcomes["crawler"] = result
        logger.debug(f"Crawler scheduler tick: {result.get('run_count', 0)} source runs")
    except Exception as exc:
        outcomes["crawler"] = {"status": "failed", "error_class": exc.__class__.__name__}
        logger.error("Crawler scheduler component failed: {}", exc.__class__.__name__)
        telemetry.record_issue("crawler_scheduler_failed", category="crawler", error_class=exc.__class__.__name__)

    if os.getenv("LEGAL_SOURCE_GAP_ENABLED", "true").lower() not in {"false", "0", "no"}:
        try:
            from api.source_gap_jobs import run_due_source_gap_jobs

            source_gap_result = await asyncio.to_thread(run_due_source_gap_jobs)
            outcomes["source_gap"] = {"status": "completed", **source_gap_result}
            logger.debug(
                "Source-gap scheduler tick: "
                f"{source_gap_result.get('processed_count', 0)} jobs"
            )
        except Exception as exc:
            outcomes["source_gap"] = {"status": "failed", "error_class": exc.__class__.__name__}
            logger.error("Source-gap scheduler component failed: {}", exc.__class__.__name__)

    # The detached form campaign may discover and prepare candidates, but it
    # never approves them. Keep it independent from listing/source-gap errors.
    if os.getenv("LEGAL_FORM_COMPLETION_ENABLED", "true").lower() not in {"false", "0", "no"}:
        try:
            from api.form_completion_campaign import (
                is_form_completion_campaign_due,
                launch_form_completion_campaign,
                load_campaign_status,
            )

            project_root = Path(__file__).resolve().parents[1]
            status_path = project_root / "data" / "form_completion_campaign" / "status_v1.json"
            legal_as_of = date.today().isoformat()
            campaign_status = load_campaign_status(status_path)
            if is_form_completion_campaign_due(campaign_status, legal_as_of=legal_as_of):
                launch_result = await asyncio.to_thread(
                    launch_form_completion_campaign,
                    project_root=project_root,
                    legal_as_of=legal_as_of,
                    status_path=status_path,
                )
                outcomes["forms"] = {"status": "completed", **launch_result}
                logger.info(
                    "Daily form-completion campaign: "
                    f"{launch_result.get('launch_status', 'unknown')}"
                )
            else:
                outcomes["forms"] = {"status": "skipped", "reason": "not_due"}
        except Exception as exc:
            outcomes["forms"] = {"status": "failed", "error_class": exc.__class__.__name__}
            logger.error("Form-completion scheduler component failed: {}", exc.__class__.__name__)
    return outcomes


# Crawler scheduler loop - added for Week 1
async def legal_crawl_scheduler_loop():
    """Background task to periodically check and crawl legal sources."""
    logger.info("Starting legal crawl scheduler loop")

    while True:
        try:
            if os.getenv("LEGAL_CRAWLER_ENABLED", "true").lower() in {"false", "0", "no"}:
                await asyncio.sleep(60)
                continue

            await legal_crawl_scheduler_tick()
            try:
                interval_minutes = int(os.getenv("LEGAL_CRAWL_INTERVAL_MINUTES", "60"))
            except ValueError:
                interval_minutes = 60
            await asyncio.sleep(max(1, interval_minutes) * 60)
        except asyncio.CancelledError:
            logger.info("Crawler scheduler cancelled")
            break
        except Exception as exc:
            logger.error("Error in crawler scheduler loop: {}", exc.__class__.__name__)
            await asyncio.sleep(300)


async def legal_import_worker_loop() -> None:
    """Background worker for extraction/OCR and approval -> import/embed jobs."""
    logger.info("Starting legal processing/import worker loop")
    # A document can take longer than the readiness heartbeat threshold to
    # parse or embed. Keep liveness independent from the current job so the UI
    # does not mistake a busy worker for a stopped worker.
    from api.import_worker_status import record_import_worker_heartbeat

    async def heartbeat_loop() -> None:
        while True:
            record_import_worker_heartbeat()
            await asyncio.sleep(5)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    last_recovery_tick = 0.0
    try:
        while True:
            try:
                if os.getenv("LEGAL_IMPORT_WORKER_ENABLED", "true").lower() in {"false", "0", "no"}:
                    await asyncio.sleep(30)
                    continue
                now_tick = asyncio.get_running_loop().time()
                if now_tick - last_recovery_tick >= 60:
                    await LegalCrawlService.recover_stale_import_jobs()
                    await LegalCrawlService.recover_stale_processing_jobs()
                    last_recovery_tick = now_tick
                processing_job = await LegalCrawlService.process_next_processing_job()
                import_job = None if processing_job else await LegalCrawlService.process_next_import_job()
                await asyncio.sleep(1 if processing_job or import_job else 5)
            except asyncio.CancelledError:
                logger.info("Legal processing/import worker cancelled")
                break
            except Exception as exc:
                logger.error(f"Legal processing/import worker error: {exc}")
                await asyncio.sleep(15)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
