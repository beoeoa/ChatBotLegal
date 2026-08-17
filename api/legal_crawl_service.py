from __future__ import annotations
import difflib
import shutil
import asyncio
import hashlib
import os
import re
import unicodedata
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
from urllib.robotparser import RobotFileParser
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from pathlib import Path
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from api.sources_service import SourcesService
from api.observability import telemetry
from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES
from api.crawlers.legal_document_pipeline import fetch_normalized_legal_document
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_delete,
    repo_query,
    repo_update,
)
from open_notebook.domain.notebook import Asset, Source
from surreal_commands import submit_command
from open_notebook.ai.provision import provision_langchain_model


VBPL_SITEMAP_INDEX = "https://vbpl.vn/sitemap.xml"
WEEKLY_CRAWL_INTERVAL_MINUTES = 7 * 24 * 60
LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://127.0.0.1:8765"
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

# Internal queues are useful provenance records, but they do not represent
# public websites and must never be passed to the HTTP crawler.
INTERNAL_SOURCE_TYPES = frozenset({"officer_proposal", "source_gap_candidate"})
CRAWLABLE_SOURCE_TYPES = frozenset({"vbpl_listing", "official_listing", "vbpl_sitemap"})


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


def _compact_candidate_list_item(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return safe, bounded review-list data without shipping full legal text."""
    compact = dict(candidate)
    content = str(compact.pop("content", "") or "")
    compact["content_characters"] = len(content)
    extraction = compact.get("extraction_result")
    if isinstance(extraction, dict):
        compact_extraction = dict(extraction)
        preview = str(compact_extraction.get("preview") or "")
        if len(preview) > 2000:
            compact_extraction["preview"] = preview[:2000].rstrip() + "…"
        compact["extraction_result"] = compact_extraction
    return compact


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
        scope = str(candidate.get("scope") or raw.get("scope") or "").lower()
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
        source_verified = bool(raw.get("confirmed_official_source")) and parsed_source.scheme == "https" and official_host
        if not source_verified:
            if not source_url:
                hard_gate_failures.append("Thiếu URL nguồn chính thức.")
            elif parsed_source.scheme != "https" or not official_host:
                hard_gate_failures.append("URL nguồn chưa thuộc website HTTPS của cơ quan nhà nước đã cho phép.")
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
            chars >= 500
            and ocr_status not in {"failed", "unavailable", "empty", "pending", "partial"}
            and extraction.get("complete") is not False
            and not extraction.get("failed_pages")
        )
        if not extraction_complete:
            if ocr_status in {"failed", "unavailable", "empty", "pending", "partial"}:
                hard_gate_failures.append("Trích xuất/OCR chưa hoàn tất hoặc không dùng được.")
            elif chars < 500:
                hard_gate_failures.append("Nội dung trích xuất chưa đủ 500 ký tự để kiểm tra pháp lý nghiêm ngặt.")
            else:
                hard_gate_failures.append("Trích xuất chưa đủ toàn bộ nội dung hoặc còn trang lỗi.")

        content_type_allowed = source_type not in {"form", "procedure"}
        if not content_type_allowed:
            hard_gate_failures.append("Đây là biểu mẫu/thủ tục, không được đưa vào kho văn bản pháp luật.")
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
        return rows[0]

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
            errors.append("Biểu mẫu không được import vào legal RAG; hãy duyệt qua Form Catalog.")
        review_status = str(candidate.get("review_status") or candidate.get("status") or "").strip().lower()
        if require_approved and review_status not in {"approved", "import_failed", "import_queued"}:
            errors.append("Candidate chưa ở trạng thái admin approved để import.")
        title = str(candidate.get("title") or "").strip()
        if not title:
            errors.append("Thiếu tên văn bản.")
        elif _repair_mojibake_text(title) != title:
            errors.append("Tên văn bản bị lỗi mã hóa; admin phải đối chiếu và sửa từ nguồn gốc.")
        if not str(candidate.get("law_number") or "").strip():
            errors.append("Thiếu số hiệu văn bản đã xác minh.")
        if not str(candidate.get("document_type") or raw.get("document_type") or "").strip():
            errors.append("Thiếu loại văn bản đã xác minh.")
        if not str(candidate.get("issuing_agency") or raw.get("issuing_agency") or "").strip():
            errors.append("Thiếu cơ quan ban hành đã xác minh.")
        try:
            date.fromisoformat(str(raw.get("issued_date")))
        except (TypeError, ValueError):
            errors.append("Thiếu hoặc sai ngày ban hành (YYYY-MM-DD).")
        if not bool(raw.get("confirmed_official_source")):
            errors.append("Admin chưa xác nhận nguồn văn bản chính thức.")
        source_url = str(candidate.get("source_url") or raw.get("source_url") or "").strip()
        if not source_url.startswith("https://"):
            errors.append("Nguồn gốc phải là URL HTTPS chính thức đã kiểm tra.")
        scope = str(candidate.get("scope") or raw.get("scope") or "").strip().lower()
        if scope not in {"central", "haiphong", "local"}:
            errors.append("Phạm vi áp dụng phải là central, haiphong hoặc local.")
        effective_date = raw.get("effective_date")
        try:
            effective = date.fromisoformat(str(effective_date))
            if effective > date.today():
                errors.append("Văn bản chưa có hiệu lực tại thời điểm import.")
        except (TypeError, ValueError):
            errors.append("Thiếu hoặc sai ngày có hiệu lực (YYYY-MM-DD).")
        expired_date = raw.get("expired_date")
        if expired_date:
            try:
                if date.fromisoformat(str(expired_date)) <= date.today():
                    errors.append("Văn bản đã hết hiệu lực.")
            except ValueError:
                errors.append("Sai ngày hết hiệu lực (YYYY-MM-DD).")
        extraction = candidate.get("extraction_result") or {}
        ocr_status = str(extraction.get("ocr_status") or raw.get("ocr_status") or "not_required")
        if ocr_status in {"failed", "unavailable", "empty", "pending", "partial"}:
            errors.append("OCR/trích xuất chưa hoàn tất hoặc không dùng được; admin phải đối chiếu file gốc trước khi import.")
        processed_pages = int(extraction.get("processed_pages") or 0)
        total_pages = int(extraction.get("total_pages") or 0)
        if total_pages and (
            processed_pages != total_pages
            or extraction.get("complete") is False
            or extraction.get("failed_pages")
        ):
            errors.append(
                "OCR/trích xuất chưa đủ toàn bộ số trang; candidate phải tiếp tục fail-closed."
            )
        content = str(candidate.get("content") or "").strip()
        if len(content) < 100:
            errors.append("Nội dung trích xuất chưa đủ 100 ký tự để chuẩn hóa/chunk.")
        elif _repair_mojibake_text(content) != content:
            errors.append("Nội dung bị lỗi mã hóa; không được import trước khi đối chiếu nguồn gốc.")
        if candidate.get("duplicate_candidates"):
            errors.append("Có candidate trùng cần được admin xử lý trước khi import.")
        return errors

    @classmethod
    async def prepare_candidate_for_import(cls, candidate_id: str) -> dict[str, Any]:
        """Fetch and validate official evidence before recording approval."""

        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Không tìm thấy candidate.")
        source_url = str(
            candidate.get("source_url")
            or candidate.get("detail_url")
            or (candidate.get("raw_metadata") or {}).get("source_url")
            or ""
        ).strip()
        raw = dict(candidate.get("raw_metadata") or {})
        await repo_update(
            "legal_crawl_candidate",
            candidate_id,
            {
                "pipeline_stage": "fetching_source",
                "preparation_status": "running",
                "blockers": [],
                "preparation_started_at": _utcnow(),
            },
        )
        normalized: dict[str, Any] | None = None
        fetch_reason: str | None = None
        needs_fetch = (
            len(str(candidate.get("content") or "").strip()) < 100
            or bool(raw.get("metadata_only"))
            or not raw.get("effective_date")
            or not raw.get("confirmed_official_source")
        )
        if needs_fetch and source_url:
            try:
                normalized = await fetch_normalized_legal_document(
                    source_url,
                    scope=str(candidate.get("scope") or raw.get("scope") or "central"),
                    timeout_seconds=45,
                )
            except Exception as exc:
                fetch_reason = f"Không tải được nguồn chính thức ({exc.__class__.__name__})."

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
        normalized_has_evidence = bool(content) and str((normalized or {}).get("status")) != "rejected"
        if normalized_has_evidence:
            raw["confirmed_official_source"] = True
            raw["source_verified_at"] = _utcnow().isoformat()
            raw["source_verification_method"] = str(
                extraction.get("official_payload") or extraction.get("method") or "verified_http"
            )
        raw.update(
            {
                "source_url": final_url or source_url,
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
        if fetch_reason and not content:
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
        rows = await repo_update("legal_crawl_candidate", candidate_id, updates)
        return rows[0] if rows else {**merged, **updates}

    @staticmethod
    async def find_runtime_document_conflict(
        candidate: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return an active runtime document with the same law number.

        This is preflight only. A timeout or unavailable retrieval service is
        never treated as proof that a document is new; the import worker keeps
        the final duplicate enforcement.
        """
        law_number = str(candidate.get("law_number") or "").strip()
        if not law_number:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{LEGAL_SEARCH_URL}/documents/lookup",
                    params={"law_number": law_number},
                )
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                logger.warning(
                    "Duplicate preflight unavailable for {}: HTTP {}",
                    law_number,
                    response.status_code,
                )
                return None
            document = response.json().get("document") or {}
            return dict(document) if isinstance(document, dict) else None
        except Exception as exc:
            logger.warning(
                "Duplicate preflight unavailable for {}: {}",
                law_number,
                exc.__class__.__name__,
            )
            return None

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
        conflict_note = (
            "Phát hiện văn bản đã có trong kho runtime với cùng số hiệu: "
            f"{runtime_conflict.get('law_number') or candidate.get('law_number')} "
            f"(ID {runtime_conflict.get('id')}). Hãy chọn dùng bản hiện có "
            "hoặc đối chiếu quan hệ sửa đổi/thay thế trước khi duyệt nhập."
        )
        rows = await repo_update(
            "legal_crawl_candidate",
            candidate_id,
            {
                "status": "changes_requested",
                "review_status": "changes_requested",
                "import_status": "duplicate_conflict",
                "review_note": conflict_note,
                "review_reason": conflict_note,
                "requested_changes_note": conflict_note,
                "duplicate_runtime_document": runtime_conflict,
            },
        )
        return rows[0] if rows else {
            **candidate,
            "status": "changes_requested",
            "review_status": "changes_requested",
            "import_status": "duplicate_conflict",
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
        import_payload = {
            "title": title,
            "law_number": str(candidate.get("law_number") or "").strip(),
            "document_type": raw_metadata.get("document_type") or candidate.get("document_type") or "Văn bản pháp luật",
            "issuing_agency": raw_metadata.get("issuing_agency") or candidate.get("issuing_agency") or "Chưa xác định",
            "scope": candidate.get("scope") or raw_metadata.get("scope") or "central",
            "sector": raw_metadata.get("sector") or "",
            "field_id": domain_field_map.get(domain, int(raw_metadata.get("field_id") or 1)),
            "domain_slug": domain,
            "issued_date": raw_metadata.get("issued_date"),
            "effective_date": raw_metadata.get("effective_date"),
            "expired_date": raw_metadata.get("expired_date"),
            "source_url": source_url,
            "applicability_info": raw_metadata.get("applicability_info") or "",
            "content": content_text,
            "confirmed_official_source": bool(raw_metadata.get("confirmed_official_source")),
            "structure": "auto",
        }
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(f"{LEGAL_SEARCH_URL}/import", json=import_payload)
            if response.status_code != 200:
                raise RuntimeError(f"Legal Search import rejected: HTTP {response.status_code}: {response.text[:500]}")
            legal_import_result = response.json() if response.content else {}
            document_id = legal_import_result.get("document_id")
            try:
                chunk_count = int(legal_import_result.get("chunk_count") or 0)
            except (TypeError, ValueError):
                chunk_count = 0
            if (
                legal_import_result.get("status") != "embedded_active"
                or legal_import_result.get("activation_status") != "active"
                or not str(document_id or "").strip()
                or chunk_count < 1
            ):
                raise RuntimeError(
                    "Embedding chưa xác nhận đủ document active, mã văn bản và vector chunk; "
                    "import không được đánh dấu hoàn tất."
                )
        except Exception as exc:
            await repo_update(
                "legal_crawl_candidate", candidate_id,
                {
                    "status": "import_failed",
                    "review_status": "approved",
                    "import_status": "failed",
                    "pipeline_stage": "failed",
                    "review_note": f"Import/embed failed: {str(exc)[:1000]}",
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
                "review_note": "Đã chuẩn hóa, chia đoạn, embedding và kích hoạt kho tra cứu.",
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
                {"status": "completed", "completed_at": _utcnow(), "document_id": document_id, "structure": legal_import_result.get("structure"), "article_count": legal_import_result.get("article_count"), "chunk_count": legal_import_result.get("chunk_count"), "source_asset": source_asset, "embedding_result": legal_import_result, "error_reason": None},
            )
        return {"id": None, "title": title, "legal_import_ok": True, "legal_import_result": legal_import_result, "source_asset": source_asset}

    @classmethod
    async def enqueue_import_job(
        cls,
        candidate_id: str,
        *,
        approval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a queue item; worker performs expensive import/embed off-request."""
        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Không tìm thấy candidate.")
        if candidate.get("status") not in {
            "pending", "changes_requested", "approved", "import_failed", "import_queued"
        }:
            raise ValueError("Candidate không ở trạng thái có thể xếp hàng nhập kho.")
        errors = cls.validate_candidate_for_import(candidate, require_approved=False)
        if errors:
            raise ValueError(" | ".join(errors))
        runtime_conflict = await cls.find_runtime_document_conflict(candidate)
        if runtime_conflict:
            await cls.mark_runtime_document_conflict(candidate, runtime_conflict)
            raise ValueError(
                "Văn bản đã có số hiệu trùng trong kho runtime; "
                "candidate đã được chuyển sang cần đối chiếu."
            )
        rows = await repo_query(
            "SELECT * FROM legal_import_job WHERE candidate = $candidate AND status IN ['queued', 'running'] LIMIT 1;",
            {"candidate": ensure_record_id(candidate["id"])},
        )
        if rows:
            await repo_update("legal_crawl_candidate", candidate["id"], {
                "status": "import_queued",
                "review_status": "approved",
                "import_status": rows[0].get("status") or "queued",
                "pipeline_stage": "queued",
                "preparation_status": "ready",
                "blockers": [],
                **(approval or {}),
            })
            return rows[0]
        created = await repo_create("legal_import_job", {
            "candidate": ensure_record_id(candidate["id"]), "status": "queued", "attempts": 0,
            "error_reason": None, "created_at": _utcnow(), "started_at": None, "completed_at": None,
            "document_id": None, "structure": None, "article_count": None, "chunk_count": None,
            "source_asset": None, "embedding_result": None,
        })
        job = created[0] if isinstance(created, list) else created
        await repo_update("legal_crawl_candidate", candidate["id"], {
            "status": "import_queued", "review_status": "approved", "import_status": "queued",
            "pipeline_stage": "queued", "preparation_status": "ready", "blockers": [],
            "import_job": ensure_record_id(job["id"]), "review_note": "Đã xếp hàng normalize/import/embed.",
            **(approval or {}),
        })
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
            await repo_update(
                "legal_crawl_candidate",
                job["candidate"],
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
                "review_note": f"Import job failed: {message}",
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
    ) -> dict[str, Any]:
        """Review a candidate and auto-import if approved. Week 1."""
        if decision not in {"approved", "rejected", "changes_requested"}:
            raise ValueError("Invalid candidate decision")
        current = await cls.get_candidate(candidate_id)
        if not current:
            raise ValueError("Không tìm thấy candidate.")
        current_status = str(current.get("status") or "").strip().lower()
        if current_status not in {"pending", "changes_requested"}:
            raise ValueError(
                f"Candidate ở trạng thái {current_status or 'không xác định'} "
                "không thể nhận quyết định duyệt mới. Hãy dùng thao tác nhập lại "
                "cho lỗi import hoặc xem bản ghi đã nhập ở chế độ chỉ đọc."
            )
        if current_status == "changes_requested" and decision == "changes_requested":
            raise ValueError(
                "Candidate đã ở trạng thái cần bổ sung; chỉ có thể duyệt hoặc bỏ qua "
                "sau khi dữ liệu được cập nhật."
            )
        if decision == "approved":
            runtime_conflict = await cls.find_runtime_document_conflict(current)
            if runtime_conflict:
                return await cls.mark_runtime_document_conflict(current, runtime_conflict)
            prepared = await cls.prepare_candidate_for_import(candidate_id)
            blockers = list(prepared.get("blockers") or [])
            if blockers or prepared.get("preparation_status") != "ready":
                return prepared
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
                "review_note": "Đã xếp hàng chuẩn hóa, chia đoạn và embedding.",
            }
        requested_changes_note = review_note.strip() if decision == "changes_requested" else None
        rows = await repo_update(
            "legal_crawl_candidate",
            candidate_id,
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
        candidate = rows[0] if rows else {}
        
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
        await cls._migrate_legacy_default_source_intervals()
        await cls.ensure_vbpl_sources()

    @classmethod
    async def _migrate_legacy_default_source_intervals(cls) -> None:
        """Retire duplicate legacy sitemap roots while preserving their run history."""
        for base_url in ("https://vbpl.vn", "https://vbpl.vn/haiphong"):
            rows = await repo_query(
                "SELECT id, interval_minutes, enabled FROM legal_crawl_source "
                "WHERE source_type = 'vbpl_sitemap' AND base_url = $base_url LIMIT 1;",
                {"base_url": base_url},
            )
            if not rows:
                continue
            await repo_update(
                "legal_crawl_source",
                str(rows[0]["id"]),
                {
                    "enabled": False,
                    "interval_minutes": WEEKLY_CRAWL_INTERVAL_MINUTES,
                    "last_error": "Nguồn legacy đã dừng; dùng listing VBPL Trung ương/Hải Phòng hiện hành.",
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
            total_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate GROUP ALL;")
            total = total_rows[0]["count"] if total_rows else 0
            
            pending_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'pending' GROUP ALL;")
            pending = pending_rows[0]["count"] if pending_rows else 0
            pending_document_rows = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate "
                "WHERE status = 'pending' AND (source_type = NONE OR source_type != 'form') GROUP ALL;"
            )
            pending_documents = pending_document_rows[0]["count"] if pending_document_rows else 0
            
            approved_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'approved' GROUP ALL;")
            approved = approved_rows[0]["count"] if approved_rows else 0

            imported_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'imported' GROUP ALL;")
            imported = imported_rows[0]["count"] if imported_rows else 0
            imported_candidate_rows = await repo_query(
                "SELECT status, import_status, imported_document FROM legal_crawl_candidate "
                "WHERE status = 'imported';"
            )
            activated_candidates = sum(
                1
                for candidate in imported_candidate_rows
                if isinstance(candidate, dict) and cls._has_active_import_confirmation(candidate)
            )
            unverified_imported_candidates = max(int(imported or 0) - activated_candidates, 0)
            
            rejected_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'rejected' GROUP ALL;")
            rejected = rejected_rows[0]["count"] if rejected_rows else 0
            import_job_rows = await repo_query(
                "SELECT status, count() AS count FROM legal_import_job GROUP BY status;"
            )
            import_queue = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
            for row in import_job_rows:
                status = str(row.get("status") or "")
                if status in import_queue:
                    import_queue[status] = int(row.get("count") or 0)
            metric_job_rows = await repo_query(
                "SELECT status, created_at, started_at, completed_at "
                "FROM legal_import_job ORDER BY created_at DESC LIMIT 200;"
            )
            validation_rows = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate "
                "WHERE import_status = 'validation_failed' GROUP ALL;"
            )
            duplicate_rows = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate "
                "WHERE import_status = 'duplicate_conflict' GROUP ALL;"
            )
            import_metrics = cls._build_import_queue_metrics(
                metric_job_rows,
                import_queue=import_queue,
                activated_candidates=activated_candidates,
                validation_failed_candidates=int(validation_rows[0]["count"] or 0) if validation_rows else 0,
                duplicate_conflict_candidates=int(duplicate_rows[0]["count"] or 0) if duplicate_rows else 0,
            )
            domain_rows = await repo_query(
                "SELECT domain, count() AS count FROM legal_crawl_candidate GROUP BY domain;"
            )
            source_rows = await repo_query(
                "SELECT source, count() AS count FROM legal_crawl_candidate GROUP BY source;"
            )
            by_domain = {str(row.get("domain") or "unclassified"): row.get("count", 0) for row in domain_rows}
            by_source = {str(row.get("source") or "unknown"): row.get("count", 0) for row in source_rows}
        except Exception as e:
            logger.error(f"Error getting crawl summary: {e}")
            total = pending = pending_documents = approved = imported = rejected = 0
            activated_candidates = unverified_imported_candidates = 0
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
            
        return {
            "total_candidates": total,
            "pending_candidates": pending,
            "pending_document_candidates": pending_documents,
            "approved_candidates": approved,
            "imported_candidates": imported,
            "activated_candidates": activated_candidates,
            "unverified_imported_candidates": unverified_imported_candidates,
            "rejected_candidates": rejected,
            "import_queue": import_queue,
            "import_metrics": import_metrics,
            "by_domain": by_domain,
            "by_source": by_source,
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
                # Internal queues are archived instead of physically deleted.
                # This keeps candidate/run record references valid while
                # removing the queue from the operational Admin surface.
                if cls.is_internal_source(source) and source.get("last_status") == "deleted":
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
            return []

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

    @classmethod
    def _source_for_admin(cls, source: dict[str, Any]) -> dict[str, Any]:
        result = dict(source)
        internal = cls.is_internal_source(source)
        default = cls.is_default_source(source)
        result.update({
            "source_kind": "internal_queue" if internal else "web_crawler",
            "is_default": default,
            "can_delete": internal or not default,
            "can_scan": cls.is_crawlable_source(source),
            "purpose": (
                "Hàng đợi đề xuất nội bộ; không quét web."
                if internal
                else "Trang công bố chính thức được quét theo lịch."
            ),
        })
        return result

    @classmethod
    async def create_source(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a disabled custom official listing source.

        New sources are deliberately saved disabled.  The Admin can review the
        official URL and then explicitly enable it; candidates and source runs
        are never created by this configuration operation.
        """
        base_url = cls._validate_official_source_url(str(payload.get("base_url") or ""))
        name = str(payload.get("name") or "").strip()
        if len(name) < 3:
            raise ValueError("Tên nguồn cần ít nhất 3 ký tự.")
        scope = str(payload.get("sitemap_scope") or "").strip().lower()
        if scope not in {"central", "haiphong", "local"}:
            raise ValueError("Phạm vi nguồn phải là central, haiphong hoặc local.")
        existing = await repo_query(
            "SELECT id FROM legal_crawl_source WHERE base_url = $base_url LIMIT 1;",
            {"base_url": base_url},
        )
        if existing:
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
            "domains": list(LEGAL_DOMAINS),
            "rate_limit_seconds": max(0.2, min(float(payload.get("rate_limit_seconds") or 1.5), 30.0)),
            "content_fetch_allowed": False,
            "last_status": "not_checked",
            "last_error": None,
            "created": _utcnow(),
            "updated": _utcnow(),
        }

        created = await repo_create("legal_crawl_source", source)
        record = created[0] if isinstance(created, list) else created
        return cls._source_for_admin(record if isinstance(record, dict) else {**source, "id": record})

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
                "content_fetch_allowed",
            }
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
        if "base_url" in updates:
            updates["base_url"] = cls._validate_official_source_url(str(updates["base_url"]))
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
        if cls.is_default_source(source):
            raise ValueError("Nguồn web mặc định không thể xóa; bạn có thể tắt nguồn này.")
        await repo_delete(source["id"])
        return {
            "id": str(source["id"]),
            "deleted": True,
            "archived": False,
            "history_preserved": True,
            "name": source.get("name"),
        }

    @classmethod
    async def ensure_vbpl_sources(cls) -> list[dict[str, Any]]:
        """Create the conservative official default sources once.

        Listing endpoints are crawled for metadata only. Detail pages are not
        downloaded unless an admin turns on `content_fetch_allowed` per source.
        """
        defaults = (
            {
                "name": "VBPL Trung ương",
                "source_type": "vbpl_listing",
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
                "SELECT id, interval_minutes, domains FROM legal_crawl_source WHERE base_url = $base_url LIMIT 1;",
                {"base_url": payload["base_url"]},
            )
            if not rows:
                await repo_create("legal_crawl_source", payload)
            else:
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
                if updates:
                    updates["updated"] = _utcnow()
                    await repo_update("legal_crawl_source", str(rows[0]["id"]), updates)
        return await cls.list_sources()

    @staticmethod
    async def _robots_allowed(url: str) -> tuple[bool, str | None]:
        """Fetch and honour robots.txt without attempting to circumvent it."""
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": CRAWLER_USER_AGENT}) as client:
                response = await client.get(robots_url)
            if response.status_code == 404:
                return True, None
            if response.status_code >= 400:
                return False, f"robots.txt trả HTTP {response.status_code}"
            parser.parse(response.text.splitlines())
            if not parser.can_fetch(CRAWLER_USER_AGENT, url):
                return False, "robots.txt không cho phép crawler truy cập URL này"
            return True, None
        except httpx.HTTPError as exc:
            # A source with an unavailable robots policy is not crawled by
            # default. This is deliberately conservative for legal sources.
            return False, f"không kiểm tra được robots.txt: {exc.__class__.__name__}"

    @staticmethod
    async def _fetch_with_backoff(url: str, rate_limit_seconds: float) -> str:
        """Fetch one allowed listing with bounded retries and exponential backoff."""
        delay = max(0.2, min(float(rate_limit_seconds or 1.5), 30.0))
        error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    timeout=30,
                    follow_redirects=True,
                    headers={"User-Agent": CRAWLER_USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                ) as client:
                    response = await client.get(url)
                if response.status_code in {429, 503}:
                    raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)
                response.raise_for_status()
                response_html = response.text
                parsed_url = urlparse(url)
                is_dynamic_vbpl_listing = (
                    (parsed_url.hostname or "").lower().endswith("vbpl.vn")
                    and parsed_url.path.rstrip("/")
                    in {"/van-ban/trung-uong", "/van-ban/dia-phuong"}
                )
                if is_dynamic_vbpl_listing:
                    try:
                        from open_notebook.utils.vbpl_crawler import (
                            crawl_vbpl_listing,
                        )

                        rendered = await crawl_vbpl_listing(url)
                    except Exception as exc:
                        raise RuntimeError(
                            "VBPL dynamic listing unavailable: "
                            f"{exc.__class__.__name__}"
                        ) from exc
                    rendered_html = str(rendered.get("html") or "")
                    if not LegalCrawlService._parse_listing(rendered_html, url):
                        raise RuntimeError(
                            "VBPL dynamic listing returned no exact document records"
                        )
                    return rendered_html
                return response_html
            except (httpx.HTTPError, UnicodeError, RuntimeError) as exc:
                error = exc
                if attempt < 2:
                    await asyncio.sleep(delay * (2**attempt))
        raise RuntimeError(f"không tải được listing sau 3 lần: {error}")

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
    def _parse_listing(html: str, base_url: str) -> list[dict[str, str]]:
        """Parse document/form listing metadata only; never fetch detail pages."""
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
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
            is_form = parsed.path.lower().endswith(FORM_EXTENSIONS)
            looks_like_document = "/van-ban/" in parsed.path or "toanvan" in url.lower() or "/chi-tiet/" in parsed.path
            # Skip pagination / navigation anchors so they never become candidates.
            anchor_rel = " ".join(anchor.get("rel") or []).lower()
            anchor_text = _plain_text(anchor.get_text(" ", strip=True))
            if anchor_rel == "next" or any(tok in anchor_text for tok in ("next", "sau", "tiep", "trang ke", ">>")):
                continue
            if not is_form and not looks_like_document:
                continue
            seen.add(url)
            container = anchor.find_parent(["article", "li", "tr", "div"])
            context = _repair_mojibake_text(
                " ".join(container.get_text(" ", strip=True).split())
                if container
                else title
            )
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
            results.append({
                "url": url,
                "title": title[:1000],
                "context": context[:4000],
                "source_type": "form" if is_form else "document",
                "law_number": number or "",
                "issued_date": issued_date or "",
                "effective_date": effective_date or "",
                "document_type": document_type,
                "issuing_agency": agency,
            })
        return results

    @staticmethod
    def _next_listing_url(html: str, current_url: str, base_url: str) -> str | None:
        """Find one safe next-page link. Page navigation is never guessed."""
        soup = BeautifulSoup(html, "html.parser")
        candidates = soup.select('a[rel="next"][href], .pagination a[href], .pager a[href], a[href*="page="]')
        current = LegalCrawlService._canonical_listing_url(current_url)
        root = urlparse(base_url)
        for anchor in candidates:
            label = _plain_text(anchor.get_text(" ", strip=True))
            rel = " ".join(anchor.get("rel") or []).lower()
            if rel != "next" and not any(token in label for token in ("next", "sau", "tiep", "trang ke", ">")):
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
    ) -> bool:
        """Deduplicate listings against queue records before candidate creation."""
        rows = await repo_query(
            "SELECT id FROM legal_crawl_candidate WHERE source_url = $source_url LIMIT 1;",
            {"source_url": source_url},
        )
        if rows:
            return True
        if law_number and issued_date:
            rows = await repo_query(
                "SELECT id FROM legal_crawl_candidate WHERE law_number = $law_number "
                "AND raw_metadata.issued_date = $issued_date LIMIT 1;",
                {"law_number": law_number, "issued_date": issued_date},
            )
            if rows:
                return True
        if law_number:
            rows = await repo_query(
                "SELECT id FROM legal_crawl_candidate WHERE law_number = $law_number LIMIT 1;",
                {"law_number": law_number},
            )
            if rows:
                return True
            runtime_conflict = await cls.find_runtime_document_conflict(
                {"law_number": law_number}
            )
            if runtime_conflict:
                return True
        rows = await repo_query(
            "SELECT id FROM legal_crawl_candidate WHERE content_hash = $fingerprint LIMIT 1;",
            {"fingerprint": fingerprint},
        )
        return bool(rows)

    @classmethod
    async def _create_listing_candidate(
        cls, source: dict[str, Any], item: dict[str, str], run_id: Any
    ) -> tuple[bool, str | None]:
        title = item["title"]
        context = item["context"]
        law_number = item.get("law_number") or _law_number_from_text(f"{title} {context}")
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
            item["source_type"] == "document"
            and _detail_fetch_enabled()
            and bool(source.get("content_fetch_allowed"))
        )
        if should_fetch_detail:
            normalized = await fetch_normalized_legal_document(
                item["url"],
                scope=str(source.get("sitemap_scope") or "central"),
                timeout_seconds=45,
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
            if item["source_type"] == "form":
                domain = "unclassified"
                domain_evidence = ["form:requires_admin_domain_assignment"]
            else:
                return False, "outside_domain"
        if matched_domains and not allowed_domains.intersection(matched_domains):
            return False, "outside_domain"
        if domain != "unclassified" and domain not in allowed_domains:
            return False, "outside_domain"
        if await cls._already_known(item["url"], law_number, issued_date, fingerprint):
            return False, "duplicate"
        external_id = _stable_external_id(item["url"], item["source_type"])
        metadata = {
            "candidate_origin": "vbpl_listing_scan",
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
        }
        payload = {
            "source": ensure_record_id(source["id"]),
            "external_id": external_id,
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
            "source_type": item["source_type"],
            "proposal_reason": "Phát hiện tự động từ listing VBPL; chờ admin kiểm tra nguồn và hiệu lực.",
            "submitted_by": None,
        }
        if content:
            payload["content"] = content
        if extraction_result:
            payload["extraction_result"] = extraction_result
        created = await repo_create("legal_crawl_candidate", payload)
        candidate = created[0] if isinstance(created, list) else created
        if isinstance(candidate, dict):
            await cls.persist_automatic_assessment({**payload, **candidate})
        return True, None

    @classmethod
    async def scan_source(cls, source_id: str) -> dict[str, Any]:
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
                metadata={"source_type": "vbpl_listing"},
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
                # Only listing URLs discovered from same-origin pagination are used.
                html = await cls._fetch_with_backoff(next_cursor, float(source.get("rate_limit_seconds") or 1.5))
                pagination_stats["listing_pages"] += 1
                items = cls._parse_listing(html, next_cursor)
                # max_documents_per_run limits *new candidates* across the entire
                # run, not parsed listing links.  We finish the current page so
                # duplicate records cannot trap the cursor, then continue on the
                # next scheduled/admin run when the new-candidate budget is full.
                candidate_limit_reached = False
                for item in items:
                    stats["discovered"] += 1
                    if item["source_type"] == "form":
                        stats["forms"] += 1
                    if stats["created"] >= max_per_run:
                        candidate_limit_reached = True
                        continue
                    created, skipped = await cls._create_listing_candidate(source, item, run_id)
                    if created:
                        stats["created"] += 1
                    elif skipped == "duplicate":
                        stats["duplicates"] += 1
                    elif skipped == "outside_domain":
                        stats["outside_domain"] += 1
                    elif skipped == "extraction_failed":
                        stats["extraction_failed"] = stats.get("extraction_failed", 0) + 1
                next_cursor = cls._next_listing_url(html, next_cursor, source["base_url"])
                if not next_cursor:
                    completed_catalog = True
                    break
                if candidate_limit_reached:
                    # Persist the safely discovered next-page URL.  This avoids
                    # a high-volume full-catalog burst while ensuring later pages
                    # remain reachable across repeated runs.
                    break
                # A successful listing page must respect the configured pacing
                # before the next page request; retry backoff is additional.
                await asyncio.sleep(max(0.2, min(float(source.get("rate_limit_seconds") or 1.5), 30.0)))

            if completed_catalog:
                persisted_cursor = source["base_url"]
                pagination_stats["cursor_reset"] = 1
            else:
                persisted_cursor = next_cursor or cursor
            status = "completed"
            failure_reason = None
        except Exception as exc:
            status = "failed"
            failure_reason = str(exc)[:1000]
            persisted_cursor = cursor
            logger.warning(f"Crawler source {source_id} failed: {failure_reason}")

        finished_at = _utcnow()
        await repo_update("legal_crawl_run", run_id, {
            "status": status, "finished_at": finished_at, "statistics": stats,
            "failure_reason": failure_reason, "source_freshness": finished_at if status == "completed" else None,
        })
        await repo_update("legal_crawl_source", source["id"], {
            "last_checked_at": finished_at,
            "last_success_at": finished_at if status == "completed" else source.get("last_success_at"),
            "last_error": failure_reason,
            "last_run_stats": {**stats, **pagination_stats},
            "source_freshness": finished_at if status == "completed" else source.get("source_freshness"),
            "listing_cursor": persisted_cursor,
            "last_status": status,
            "updated": finished_at,
        })
        telemetry.record_operation(
            category="crawler",
            route="worker:listing_scan",
            duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000,
            outcome="success" if status == "completed" else "failed",
            metadata={"source_type": "vbpl_listing"},
        )
        if status != "completed":
            telemetry.record_issue("crawler_failed", category="crawler", error_class="listing_scan")
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
            "catalog_completed": completed_catalog if status == "completed" else False,
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
            })
            statuses.append(item)
        return statuses

    @classmethod
    async def scan_due_sources(cls) -> dict[str, Any]:
        """Run sources that are due; a slow/broken source does not stop others."""
        sources = await cls.ensure_vbpl_sources()
        now = _utcnow()
        results = []
        for source in sources:
            if not source.get("enabled", False) or not cls.is_crawlable_source(source):
                continue
            last_checked = source.get("last_checked_at")
            interval = timedelta(minutes=max(15, int(source.get("interval_minutes") or WEEKLY_CRAWL_INTERVAL_MINUTES)))
            if isinstance(last_checked, datetime) and last_checked + interval > now:
                continue
            results.append(await cls.scan_source(str(source["id"])))
        return {"status": "completed", "runs": results, "run_count": len(results)}

    @classmethod
    async def list_candidates(
        cls,
        status: str | None = None,
        limit: int = 100,
        domain: str | None = None,
        source_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List candidate metadata with optional exact review filters."""
        try:
            predicates: list[str] = []
            params: dict[str, Any] = {"limit": min(max(int(limit), 1), 200)}
            if status == "needs_attention":
                predicates.append(
                    "(status IN ['import_failed', 'changes_requested'] "
                    "OR (status = 'approved' AND import_status = 'validation_failed'))"
                )
            elif status:
                predicates.append("status = $status")
                params["status"] = status
            if domain:
                predicates.append("domain = $domain")
                params["domain"] = domain
            if source_type:
                predicates.append("source_type = $source_type")
                params["source_type"] = source_type
            where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
            rows = await repo_query(
                f"SELECT * FROM legal_crawl_candidate{where} LIMIT $limit;",
                params,
            )
            repaired_rows = _repair_display_metadata(rows)
            return [
                _compact_candidate_list_item(row)
                for row in repaired_rows
                if isinstance(row, dict)
            ]
        except Exception as e:
            logger.error(f"Error listing candidates: {e}")
            return []

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

# Crawler scheduler loop - added for Week 1
async def legal_crawl_scheduler_loop():
    """Background task to periodically check and crawl legal sources."""
    logger.info("Starting legal crawl scheduler loop")
    
    while True:
        try:
            # Check if crawler is enabled
            if os.getenv("LEGAL_CRAWLER_ENABLED", "true").lower() in {"false", "0", "no"}:
                await asyncio.sleep(60)
                continue
            
            # Candidate-first scan: sources are individually rate limited and
            # only due sources are processed. A failure is recorded per source.
            result = await LegalCrawlService.scan_due_sources()
            logger.debug(f"Crawler scheduler tick: {result.get('run_count', 0)} source runs")
            if os.getenv("LEGAL_SOURCE_GAP_ENABLED", "true").lower() not in {"false", "0", "no"}:
                from api.source_gap_jobs import run_due_source_gap_jobs

                source_gap_result = await asyncio.to_thread(run_due_source_gap_jobs)
                logger.debug(
                    "Source-gap scheduler tick: "
                    f"{source_gap_result.get('processed_count', 0)} jobs"
                )

            # Complete the official-form pipeline once per legal date.  The
            # detached campaign remains fail-closed: it may discover, download,
            # checksum and prepare candidates, but it cannot approve them.
            if os.getenv("LEGAL_FORM_COMPLETION_ENABLED", "true").lower() not in {
                "false",
                "0",
                "no",
            }:
                from api.form_completion_campaign import (
                    is_form_completion_campaign_due,
                    launch_form_completion_campaign,
                    load_campaign_status,
                )

                project_root = Path(__file__).resolve().parents[1]
                status_path = (
                    project_root
                    / "data"
                    / "form_completion_campaign"
                    / "status_v1.json"
                )
                legal_as_of = date.today().isoformat()
                campaign_status = load_campaign_status(status_path)
                if is_form_completion_campaign_due(
                    campaign_status,
                    legal_as_of=legal_as_of,
                ):
                    launch_result = await asyncio.to_thread(
                        launch_form_completion_campaign,
                        project_root=project_root,
                        legal_as_of=legal_as_of,
                        status_path=status_path,
                    )
                    logger.info(
                        "Daily form-completion campaign: "
                        f"{launch_result.get('launch_status', 'unknown')}"
                    )
            
            # Sleep for interval (default 1 hour)
            interval_minutes = int(os.getenv("LEGAL_CRAWL_INTERVAL_MINUTES", "60"))
            await asyncio.sleep(interval_minutes * 60)
            
        except asyncio.CancelledError:
            logger.info("Crawler scheduler cancelled")
            break
        except Exception as e:
            logger.error(f"Error in crawler scheduler: {e}")
            await asyncio.sleep(300)  # Sleep 5 minutes on error


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
