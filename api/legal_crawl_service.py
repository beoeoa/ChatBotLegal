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
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
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
    r"\b(\d{1,4}/\d{2,4}/[A-Z0-9ĐƠƯÂÊÔ\-]+"
    r"(?:/[A-Z0-9ĐƠƯÂÊÔ\-]+)*)\b",
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
        """Create explainable review hints; this never changes candidate status."""
        raw = candidate.get("raw_metadata") or {}
        extraction = candidate.get("extraction_result") or {}
        domain = candidate.get("domain") or raw.get("domain")
        scope = candidate.get("scope") or raw.get("scope")
        source_type = candidate.get("source_type") or raw.get("source_type")
        chars = int(extraction.get("characters") or len(candidate.get("content") or ""))
        ocr_status = str(extraction.get("ocr_status") or raw.get("ocr_status") or "not_required")
        confidence = extraction.get("ocr_confidence")
        duplicate_count = len(candidate.get("duplicate_candidates") or [])
        source_url = candidate.get("source_url") or raw.get("source_url")
        pdf_kind = str(extraction.get("pdf_kind") or raw.get("pdf_kind") or "unknown")
        preview = str(extraction.get("preview") or "").strip()
        fingerprint = extraction.get("text_fingerprint") or raw.get("text_fingerprint")

        extraction_quality = 0
        if chars >= 500:
            extraction_quality = 80
        elif chars >= 100:
            extraction_quality = 55
        elif chars > 0:
            extraction_quality = 25
        if ocr_status in {"failed", "unavailable", "empty"}:
            extraction_quality = min(extraction_quality, 20)
        elif confidence is not None:
            try:
                extraction_quality = min(100, max(extraction_quality, round(float(confidence))))
            except (TypeError, ValueError):
                pass

        scores = {
            "domain_fit": 90 if domain in LEGAL_DOMAINS else 20,
            "ward_haiphong_relevance": 90 if scope in {"haiphong", "local"} else 60 if scope == "central" else 20,
            "form_relevance": 90 if source_type == "form" else 55,
            "duplicate_risk": 15 if duplicate_count else 85,
            "extraction_quality": extraction_quality,
            "estimated_usefulness": min(100, round((80 if domain in LEGAL_DOMAINS else 20) * 0.45 + extraction_quality * 0.35 + (70 if source_url else 20) * 0.2)),
        }
        evidence = []
        evidence.append("Đã gán lĩnh vực theo metadata/luật từ khóa." if domain in LEGAL_DOMAINS else "Chưa xác định được lĩnh vực trong phạm vi hệ thống.")
        if duplicate_count:
            evidence.append(f"Có {duplicate_count} candidate trùng số hiệu, URL hoặc fingerprint cần đối chiếu.")
        if ocr_status in {"failed", "unavailable", "empty"}:
            evidence.append(f"OCR chưa dùng được ({ocr_status}); admin cần xem file gốc.")
        elif pdf_kind == "scan" and ocr_status == "ok":
            evidence.append("PDF scan đã có kết quả OCR; cần đối chiếu bản gốc trước khi duyệt.")
        elif chars < 100:
            evidence.append("Nội dung trích xuất quá ngắn; chưa đủ để kiểm tra pháp lý.")
        else:
            evidence.append(f"Đã có {chars} ký tự trích xuất để admin đối chiếu với nguồn.")
        if not source_url:
            evidence.append("Thiếu URL nguồn; không thể xác nhận provenance từ candidate này.")
        snippets = []
        if preview:
            snippets.append({"kind": "extraction_preview", "text": preview[:500]})
        if fingerprint:
            snippets.append({"kind": "text_fingerprint", "text": str(fingerprint)[:16]})
        return {
            "kind": "review_recommendation",
            "action": "manual_review_required",
            "scores": scores,
            "evidence": evidence,
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

    @staticmethod
    def validate_candidate_for_import(candidate: dict[str, Any]) -> list[str]:
        """Validate approved candidate metadata before it can reach legal RAG."""
        raw = candidate.get("raw_metadata") or {}
        errors: list[str] = []
        source_type = str(candidate.get("source_type") or raw.get("source_type") or "document")
        if source_type == "form":
            errors.append("Biểu mẫu không được import vào legal RAG; hãy duyệt qua Form Catalog.")
        review_status = str(candidate.get("review_status") or candidate.get("status") or "").strip().lower()
        if review_status not in {"approved", "import_failed", "import_queued"}:
            errors.append("Candidate chưa ở trạng thái admin approved để import.")
        title = str(candidate.get("title") or "").strip()
        if not title:
            errors.append("Thiếu tên văn bản.")
        elif _repair_mojibake_text(title) != title:
            errors.append("Tên văn bản bị lỗi mã hóa; admin phải đối chiếu và sửa từ nguồn gốc.")
        if not str(candidate.get("law_number") or "").strip():
            errors.append("Thiếu số hiệu văn bản đã xác minh.")
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
        if ocr_status in {"failed", "unavailable", "empty", "pending"}:
            errors.append("OCR/trích xuất chưa hoàn tất hoặc không dùng được; admin phải đối chiếu file gốc trước khi import.")
        content = str(candidate.get("content") or "").strip()
        if len(content) < 100:
            errors.append("Nội dung trích xuất chưa đủ 100 ký tự để chuẩn hóa/chunk.")
        elif _repair_mojibake_text(content) != content:
            errors.append("Nội dung bị lỗi mã hóa; không được import trước khi đối chiếu nguồn gốc.")
        if candidate.get("duplicate_candidates"):
            errors.append("Có candidate trùng cần được admin xử lý trước khi import.")
        return errors

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
        if not content_text and not title:
            await repo_update("legal_crawl_candidate", candidate["id"], {"review_recommendation": recommendation})
            candidate["review_recommendation"] = recommendation
            return candidate
            
        # Construct enhanced prompt
        prompt = f"""You are a Vietnamese administrative-law document reviewer.
Analyze the candidate metadata and the first part of its content. Return JSON only.

Title: {title}
Law number: {law_number}
Scope: {candidate.get('scope', 'unknown')}
Content excerpt:
{content_text[:3000]}

Allowed domain values: ho_tich_chung_thuc, dat_dai_xay_dung,
an_sinh_y_te_giao_duc, hanh_chinh_cong, trat_tu_do_thi.
The deterministic candidate.domain is authoritative for routing. Your domain
value is a review suggestion only and must never replace it automatically.
Allowed scope values: central, haiphong, local.
Allowed official_level values: official, reference, internal.
Allowed effective_status values: con_hieu_luc, het_hieu_luc, chua_co_hieu_luc, khong_ro.
Allowed duplicate_risk values: none, possible, likely.
Return this schema: {{
  "domain": "...", "scope": "...", "official_level": "...",
  "effective_status": "...", "duplicate_risk": "...",
  "confidence": 0.0, "reasons": ["short reason"]
}}

Never invent a law number, effective date, legal status, deadline, fee, or authority.
Use low confidence when the metadata or content is incomplete. Do not use markdown.
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
                {"status": "approved", "review_status": "approved", "import_status": "validation_failed", "review_note": f"Chưa thể import: {reason}"},
            )
            raise ValueError(reason)

        raw_metadata = candidate.get("raw_metadata") or {}
        domain = candidate.get("domain") or raw_metadata.get("domain")
        domain_field_map = {
            "ho_tich_chung_thuc": 1,
            "dat_dai_xay_dung": 2,
            "an_sinh_y_te_giao_duc": 3,
            "hanh_chinh_cong": 4,
            "trat_tu_do_thi": 5,
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
            if legal_import_result.get("activation_status") != "active":
                raise RuntimeError("Embedding chưa kích hoạt document; import không được đánh dấu hoàn tất.")
        except Exception as exc:
            await repo_update(
                "legal_crawl_candidate", candidate_id,
                {"status": "import_failed", "review_status": "approved", "import_status": "failed", "review_note": f"Import/embed failed: {str(exc)[:1000]}"},
            )
            raise RuntimeError(str(exc)) from exc

        document_id = str(legal_import_result["document_id"])
        source_asset = cls._persist_approved_source_asset(candidate, document_id)
        imported_document = {
            "document_id": document_id,
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
            {"status": "imported", "review_status": "imported", "import_status": "completed", "review_note": "Đã normalize, embed và kích hoạt retrieval.", "imported_document": imported_document, "source_asset": source_asset},
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
    async def enqueue_import_job(cls, candidate_id: str) -> dict[str, Any]:
        """Persist a queue item; worker performs expensive import/embed off-request."""
        candidate = await cls.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Không tìm thấy candidate.")
        if candidate.get("status") not in {"approved", "import_failed"}:
            raise ValueError("Candidate phải ở trạng thái approved hoặc import_failed.")
        errors = cls.validate_candidate_for_import(candidate)
        if errors:
            raise ValueError(" | ".join(errors))
        rows = await repo_query(
            "SELECT * FROM legal_import_job WHERE candidate = $candidate AND status IN ['queued', 'running'] LIMIT 1;",
            {"candidate": ensure_record_id(candidate["id"])},
        )
        if rows:
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
            "import_job": ensure_record_id(job["id"]), "review_note": "Đã xếp hàng normalize/import/embed.",
        })
        return job

    @classmethod
    async def process_next_import_job(cls) -> dict[str, Any] | None:
        """Run one persistent job. Failures are visible and retryable, never active."""
        rows = await repo_query(
            "SELECT * FROM legal_import_job WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1;"
        )
        if not rows:
            return None
        job = rows[0]
        started_tick = asyncio.get_running_loop().time()
        attempts = int(job.get("attempts") or 0) + 1
        await repo_update("legal_import_job", job["id"], {"status": "running", "attempts": attempts, "started_at": _utcnow(), "error_reason": None})
        try:
            result = await cls.import_candidate(str(job["candidate"]), job_id=str(job["id"]))
            telemetry.record_operation(category="import_job", route="worker:normalize_import_embed", duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000, metadata={"job_type": "import_embed"})
            return {"job_id": str(job["id"]), "status": "completed", "result": result}
        except Exception as exc:
            message = str(exc)[:1000]
            # import_candidate normally records import_failed. Keep a defensive
            # candidate update here so every worker-side exception is explicit.
            await repo_update("legal_import_job", job["id"], {"status": "failed", "completed_at": _utcnow(), "error_reason": message})
            await repo_update("legal_crawl_candidate", job["candidate"], {
                "status": "import_failed", "review_status": "approved", "import_status": "failed",
                "review_note": f"Import job failed: {message}",
            })
            telemetry.record_operation(category="import_job", route="worker:normalize_import_embed", duration_ms=(asyncio.get_running_loop().time() - started_tick) * 1000, outcome="failed", metadata={"job_type": "import_embed"})
            telemetry.record_issue("import_failed", category="import_job", error_class=exc.__class__.__name__)
            return {"job_id": str(job["id"]), "status": "failed", "error_reason": message}

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
    async def process_next_processing_job(cls) -> dict[str, Any] | None:
        """Run one local extraction/OCR job in the background worker."""
        rows = await repo_query(
            "SELECT * FROM legal_candidate_processing_job WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1;"
        )
        if not rows:
            return None
        job = rows[0]
        started = _utcnow()
        started_tick = asyncio.get_running_loop().time()
        attempts = int(job.get("attempts") or 0) + 1
        await repo_update("legal_candidate_processing_job", job["id"], {
            "status": "running", "attempts": attempts, "started_at": started, "error_reason": None,
        })
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
        
        # Approval only queues expensive normalization/import/embedding. The
        # background worker owns activation and records a durable failure state.
        if decision == "approved":
            try:
                job = await cls.enqueue_import_job(candidate_id)
                candidate["status"] = "import_queued"
                candidate["import_job"] = job.get("id")
                candidate["import_status"] = "queued"
            except Exception as exc:
                await repo_update("legal_crawl_candidate", candidate_id, {
                    "status": "approved", "review_status": "approved", "import_status": "validation_failed",
                    "review_note": f"Đã duyệt nhưng chưa thể xếp hàng import: {str(exc)[:1000]}",
                })
                refreshed = await cls.get_candidate(candidate_id)
                if refreshed:
                    candidate = refreshed
        
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

    @classmethod
    async def summary(cls) -> dict[str, Any]:
        """Get summary of crawler status and candidate counts."""
        try:
            total_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate GROUP ALL;")
            total = total_rows[0]["count"] if total_rows else 0
            
            pending_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'pending' GROUP ALL;")
            pending = pending_rows[0]["count"] if pending_rows else 0
            
            approved_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'approved' GROUP ALL;")
            approved = approved_rows[0]["count"] if approved_rows else 0

            imported_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'imported' GROUP ALL;")
            imported = imported_rows[0]["count"] if imported_rows else 0
            
            rejected_rows = await repo_query("SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'rejected' GROUP ALL;")
            rejected = rejected_rows[0]["count"] if rejected_rows else 0
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
            total = pending = approved = imported = rejected = 0
            by_domain = {}
            by_source = {}
            
        return {
            "total_candidates": total,
            "pending_candidates": pending,
            "approved_candidates": approved,
            "imported_candidates": imported,
            "rejected_candidates": rejected,
            "by_domain": by_domain,
            "by_source": by_source,
            "last_crawl_time": None,
            "is_running": False
        }

    @classmethod
    async def list_sources(cls) -> list[dict[str, Any]]:
        """List crawlers/sources."""
        try:
            return await repo_query("SELECT * FROM legal_crawl_source;")
        except Exception as e:
            logger.error(f"Error listing sources: {e}")
            return []

    @classmethod
    async def update_source(cls, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a crawler source configuration."""
        rows = await repo_update("legal_crawl_source", source_id, payload)
        return rows[0] if rows else {}

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
                return response.text
            except (httpx.HTTPError, UnicodeError) as exc:
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
            title = " ".join(anchor.get_text(" ", strip=True).split())
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
            context = " ".join(container.get_text(" ", strip=True).split()) if container else title
            metadata_text = context
            number = _law_number_from_text(metadata_text)
            issued_date = _issued_date_from_text(metadata_text)
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
        document_type = item.get("document_type") or None
        issuing_agency = item.get("issuing_agency") or None
        fingerprint_seed = "|".join((str(law_number or ""), str(issued_date or ""), context, item["url"])).strip()
        fingerprint = hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest()
        domain, domain_evidence = _deterministic_domain(title, issuing_agency or "", document_type or "", context)
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
        if domain != "unclassified" and domain not in allowed_domains:
            return False, "outside_domain"
        if await cls._already_known(item["url"], law_number, issued_date, fingerprint):
            return False, "duplicate"
        external_id = _stable_external_id(item["url"], item["source_type"])
        metadata = {
            "candidate_origin": "vbpl_listing_scan",
            "listing_context": context,
            "issued_date": issued_date,
            "crawl_run": str(run_id),
            "metadata_only": True,
            "content_fetch_allowed": bool(source.get("content_fetch_allowed")),
            "domain_mapping": {"domain": domain, "evidence": domain_evidence},
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
        await repo_create("legal_crawl_candidate", payload)
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
        return [
            {
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
            }
            for source in sources
        ]

    @classmethod
    async def scan_due_sources(cls) -> dict[str, Any]:
        """Run sources that are due; a slow/broken source does not stop others."""
        sources = await cls.ensure_vbpl_sources()
        now = _utcnow()
        results = []
        for source in sources:
            if not source.get("enabled", False):
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
            if status:
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
            return _repair_display_metadata(rows)
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
    while True:
        try:
            if os.getenv("LEGAL_IMPORT_WORKER_ENABLED", "true").lower() in {"false", "0", "no"}:
                await asyncio.sleep(30)
                continue
            processing_job = await LegalCrawlService.process_next_processing_job()
            import_job = None if processing_job else await LegalCrawlService.process_next_import_job()
            await asyncio.sleep(1 if processing_job or import_job else 5)
        except asyncio.CancelledError:
            logger.info("Legal processing/import worker cancelled")
            break
        except Exception as exc:
            logger.error(f"Legal processing/import worker error: {exc}")
            await asyncio.sleep(15)
