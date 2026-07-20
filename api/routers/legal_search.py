import hashlib
import json
import os
import re
import uuid
from time import perf_counter
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id
from api.legal_crawl_service import LEGAL_DOMAINS, LegalCrawlService
from api.user_service import get_user_profile, write_audit_log
from api.rag_anything_adapter import RagAnythingUnavailable, extract_with_rag_anything
from open_notebook.database.repository import ensure_record_id, repo_create, repo_query, repo_update
from api.utils.pii_detector import redact_upload_text
from api.observability import telemetry

router = APIRouter(prefix="/legal", tags=["legal-search"])
LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://127.0.0.1:8765"
).rstrip("/")


class LegalSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=8, ge=1, le=30)
    candidate_count: int = Field(default=120, ge=20, le=500)
    domain: str | None = None
    include_trace: bool = False


class LegalImportRequest(BaseModel):
    title: str
    law_number: str
    document_type: str
    issuing_agency: str
    scope: str
    sector: str = ""
    field_id: int
    issued_date: str | None = None
    effective_date: str
    expired_date: str | None = None
    source_url: str = ""
    applicability_info: str = ""
    content: str
    confirmed_official_source: bool = False


class LegalCrawlPreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4000)


class LegalCrawlSourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)
    lookback_days: int | None = Field(default=None, ge=1, le=3650)
    max_documents_per_run: int | None = Field(default=None, ge=1, le=200)
    max_listing_pages_per_run: int | None = Field(default=None, ge=1, le=50)
    rate_limit_seconds: float | None = Field(default=None, ge=0.2, le=30)
    filter_keyword: str | None = Field(default=None, max_length=255)


class LegalCandidateDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    review_note: str = Field(default="", max_length=2000)


class LegalCandidateMetadataUpdateRequest(BaseModel):
    law_number: str | None = Field(default=None, max_length=255)
    document_type: str | None = Field(default=None, max_length=255)
    issuing_agency: str | None = Field(default=None, max_length=255)
    scope: str | None = Field(default=None, pattern="^(central|haiphong|local)$")
    sector: str | None = Field(default=None, max_length=255)
    issued_date: str | None = Field(default=None, max_length=20)
    effective_date: str | None = Field(default=None, max_length=20)
    expired_date: str | None = Field(default=None, max_length=20)
    source_url: str | None = Field(default=None, max_length=4000)
    confirmed_official_source: bool | None = None


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=900) as client:
            response = await client.request(
                method, f"{LEGAL_SEARCH_URL}{path}", **kwargs
            )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Dịch vụ tìm kiếm pháp luật chưa sẵn sàng: {exc}",
        ) from exc


DOMAIN_ALIASES: dict[str, set[str]] = {
    "ho_tich_chung_thuc": {"ho_tich_chung_thuc", "ho_tich", "chung_thuc"},
    "dat_dai_xay_dung": {"dat_dai_xay_dung", "dat_dai", "xay_dung"},
    "an_sinh_y_te_giao_duc": {"an_sinh_y_te_giao_duc", "an_sinh", "y_te", "giao_duc"},
    "hanh_chinh_cong": {"hanh_chinh_cong", "hanh_chinh"},
    "cu_tru_an_ninh": {"cu_tru_an_ninh", "cu_tru", "an_ninh"},
    "khieu_nai_to_cao_xu_phat": {
        "khieu_nai_to_cao_xu_phat",
        "khieu_nai",
        "to_cao",
        "xu_phat",
    },
    "trat_tu_do_thi": {"trat_tu_do_thi", "trat_tu"},
}


def _require_crawl_admin(request: Request) -> None:
    """Restrict candidate-crawler administration to the final reviewer role."""
    if get_request_role(request) != "admin":
        raise HTTPException(
            status_code=403,
            detail="Chỉ admin được cấu hình, quét, duyệt hoặc import candidate crawler.",
        )


def _domain_allowed(domain: str, allowed_domains: list[str]) -> bool:
    domain_norm = domain.strip()
    if not allowed_domains:
        return False
    if domain_norm in allowed_domains:
        return True
    aliases = DOMAIN_ALIASES.get(domain_norm, {domain_norm})
    return any(
        allowed in aliases or domain_norm in DOMAIN_ALIASES.get(allowed, {allowed})
        for allowed in allowed_domains
    )


def _safe_upload_name(filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(filename).name).strip(".-")
    return safe or "proposal-upload"


def _user_account_record(value: str) -> Any:
    text = str(value).strip()
    if text.startswith("user_account:"):
        return ensure_record_id(text)
    if ":" in text:
        text = text.split(":", 1)[1]
    return ensure_record_id(f"user_account:{text}")


async def _save_proposal_upload(file: UploadFile, content: bytes) -> dict[str, Any]:
    upload_dir = Path("data") / "uploads" / "officer_proposals"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_upload_name(file.filename or "proposal-upload")
    stem = Path(safe_name).stem or "proposal-upload"
    suffix = Path(safe_name).suffix or ".bin"
    target = upload_dir / f"{stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}{suffix}"
    target.write_bytes(content)
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(content),
        "path": str(target),
    }


def _text_review_metadata(
    text: str,
    *,
    filename: str,
    extractor_used: str,
    language: str = "vie_or_unicode_text",
) -> dict[str, Any]:
    """Attach stable review evidence without altering valid Unicode text."""
    normalized = text.strip()
    return {
        "status": "ok" if normalized else "review_required",
        "extractor_used": extractor_used,
        "filename": filename,
        "pdf_kind": "not_pdf",
        "ocr_status": "not_applicable",
        "ocr_confidence": None,
        "language": language,
        "page_count": 0,
        "preview": normalized[:2000],
        "text_fingerprint": hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None,
    }


def _extract_upload_text(filename: str, content: bytes) -> tuple[str, dict[str, Any]]:
    """Extract review-only candidate text; OCR errors never become text content."""
    lower_name = filename.lower()
    if lower_name.endswith((".txt", ".md")):
        text = content.decode("utf-8", errors="replace")
        return text, _text_review_metadata(text, filename=filename, extractor_used="utf8_text")
    if lower_name.endswith(".docx"):
        text = _extract_docx(content)
        return text, _text_review_metadata(text, filename=filename, extractor_used="python_docx")
    if lower_name.endswith(".pdf"):
        from api.crawlers.pdf_extractor import extract_pdf_bytes_for_review

        result = extract_pdf_bytes_for_review(content, filename=filename)
        return str(result.get("text") or ""), result
    raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file TXT, MD, DOCX hoặc PDF.")


async def _candidate_source_for(scope: str = "officer_proposal") -> Any:
    rows = await repo_query(
        "SELECT * FROM legal_crawl_source WHERE source_type = 'officer_proposal' LIMIT 1;"
    )
    if rows:
        source = rows[0]
        # This record is a review-queue owner, not a crawlable website. Older
        # versions marked it enabled, which made the weekly scheduler request
        # ``local://officer-proposals/robots.txt`` and report a false failure.
        if source.get("enabled") or source.get("last_error") or source.get("last_status") == "failed":
            await repo_update(
                "legal_crawl_source",
                source["id"],
                {
                    "enabled": False,
                    "last_error": None,
                    "last_status": "internal_queue",
                    "updated": datetime.now(timezone.utc),
                },
            )
        return ensure_record_id(rows[0]["id"])
    created = await repo_create(
        "legal_crawl_source",
        {
            "name": "Officer document proposals",
            "source_type": "officer_proposal",
            "sitemap_scope": scope,
            "base_url": "local://officer-proposals",
            "enabled": True,
            "interval_minutes": 1440,
            "lookback_days": 3650,
            "max_documents_per_run": 0,
            "filter_keyword": None,
            "last_checked_at": None,
            "last_success_at": None,
            "last_error": None,
        },
    )
    row = created[0] if isinstance(created, list) else created
    return ensure_record_id(row["id"])


async def _duplicate_candidates(
    law_number: str | None,
    source_url: str | None,
    content_hash: str | None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: dict[str, Any] = {"limit": 5}
    if law_number:
        filters.append("law_number = $law_number")
        params["law_number"] = law_number
    if source_url:
        filters.append("source_url = $source_url")
        params["source_url"] = source_url
    if content_hash:
        filters.append("content_hash = $content_hash")
        params["content_hash"] = content_hash
    if not filters:
        return []
    rows = await repo_query(
        f"SELECT id, title, law_number, status, source_url FROM legal_crawl_candidate WHERE {' OR '.join(filters)} LIMIT $limit;",
        params,
    )
    return [
        {
            "id": str(row.get("id")),
            "title": row.get("title"),
            "law_number": row.get("law_number"),
            "status": row.get("status"),
            "source_url": row.get("source_url"),
        }
        for row in rows or []
    ]


def _legal_validity_flags(
    *,
    effective_date: str | None,
    expired_date: str | None,
    source_url: str | None,
) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "has_source_url": bool(source_url),
        "has_effective_date": bool(effective_date),
        "has_expired_date": bool(expired_date),
        "appears_not_yet_effective": bool(effective_date and effective_date > today),
        "appears_expired": bool(expired_date and expired_date <= today),
        "requires_admin_legal_check": True,
    }


def _candidate_review_preview(candidate: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = candidate.get("raw_metadata") or {}
    content = candidate.get("content") or ""
    return {
        "title": candidate.get("title"),
        "law_number": candidate.get("law_number"),
        "domain": candidate.get("domain") or raw_metadata.get("domain"),
        "source_type": candidate.get("source_type") or raw_metadata.get("source_type"),
        "submitted_by": str(candidate.get("submitted_by") or raw_metadata.get("submitted_by") or ""),
        "proposal_reason": candidate.get("proposal_reason") or raw_metadata.get("proposal_reason"),
        "metadata": raw_metadata,
        "extraction_result": candidate.get("extraction_result") or {},
        "duplicates": candidate.get("duplicate_candidates") or [],
        "legal_validity_flags": candidate.get("legal_validity_flags") or {},
        "review_recommendation": candidate.get("review_recommendation") or {},
        "ai_assessment": candidate.get("ai_assessment") or raw_metadata.get("ai_assessment") or {},
        "content_preview": (candidate.get("extraction_result") or {}).get("preview") or content[:2000],
        "content_characters": len(content),
        "status": candidate.get("status"),
        "review_status": candidate.get("review_status"),
    }


@router.get("/health")
async def legal_search_health() -> dict[str, Any]:
    return await _request("GET", "/health")


@router.post("/search")
async def legal_search(request: LegalSearchRequest) -> dict[str, Any]:
    return await _request("POST", "/search", json=request.model_dump())


@router.get("/import/fields")
async def legal_import_fields() -> dict[str, Any]:
    return await _request("GET", "/import/fields")


@router.get("/domains")
async def legal_domains() -> dict[str, Any]:
    return await _request("GET", "/domains")


@router.get("/documents/lookup")
async def legal_document_lookup(law_number: str) -> dict[str, Any]:
    return await _request("GET", "/documents/lookup", params={"law_number": law_number})


@router.get("/documents")
async def list_legal_documents(
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    """List effective documents from the same corpus used by legal RAG."""
    params: dict[str, Any] = {
        "q": q,
        "tier": tier,
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }
    if domain:
        params["domain"] = domain
    if as_of:
        params["as_of"] = as_of
    return await _request("GET", "/documents", params=params)


@router.get("/docs/{doc_id}")
async def get_legal_document(
    doc_id: str,
    response: Response,
    article: str | None = Query(default=None),
    include_content: bool = Query(default=False),
) -> dict[str, Any]:
    """Return local legal document metadata + content/chunks.

    Used by Ask citations so users can open the in-system source even when the
    original VBPL source_url is broken.
    """
    started = perf_counter()
    params: dict[str, str] | None = None
    if article or include_content:
        params = {}
    if article:
        params["article"] = article
    if include_content:
        params["include_content"] = "true"
    try:
        payload = await _request("GET", f"/documents/{doc_id}", params=params)
    except HTTPException as exc:
        telemetry.record_operation(
            category="document_view", route="/api/legal/docs/{doc_id}",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=exc.status_code,
        )
        if exc.status_code == 404:
            telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="not_found")
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy văn bản pháp lý trong kho nội bộ.",
            ) from exc
        raise
    document = payload.get("document") if isinstance(payload, dict) else payload
    if not document:
        telemetry.record_operation(
            category="document_view", route="/api/legal/docs/{doc_id}",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=404,
        )
        telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="empty_document")
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy văn bản pháp lý trong kho nội bộ.",
        )
    # Only expose documents that are already in the official indexed legal corpus.
    # Candidate/unapproved documents must not be opened from Ask citations.
    effective_status = str(document.get("effective_status") or document.get("status") or "").strip().lower()
    if effective_status and effective_status != "active":
        telemetry.record_operation(
            category="document_view", route="/api/legal/docs/{doc_id}",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=404,
        )
        telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="inactive_document")
        raise HTTPException(
            status_code=404,
            detail="Văn bản chưa được duyệt hoặc không còn hiệu lực trong kho tra cứu.",
        )
    # Source-asset provenance lives in the review database, separate from the
    # PostgreSQL retrieval index. Expose only the approved copy, never a pending
    # upload path.
    try:
        candidates = await repo_query(
            "SELECT source_asset FROM legal_crawl_candidate WHERE imported_document.document_id = $document_id AND status = 'imported' LIMIT 1;",
            {"document_id": str(document.get("doc_id") or doc_id)},
        )
        if candidates and candidates[0].get("source_asset"):
            document["source_asset"] = candidates[0]["source_asset"]
            document["source_file_available"] = True
    except Exception:
        # Viewer remains available with the internally generated PDF if the
        # optional provenance lookup is temporarily unavailable.
        document.setdefault("source_asset", None)
    duration_ms = (perf_counter() - started) * 1000
    response.headers["X-Legal-View-Ms"] = str(round(duration_ms, 1))
    response.headers["Cache-Control"] = "private, max-age=300" if not include_content and not article else "no-store"
    telemetry.record_operation(
        category="document_view", route="/api/legal/docs/{doc_id}", duration_ms=duration_ms,
        metadata={"cached": not include_content and not bool(article)},
    )
    return document


@router.get("/docs/{doc_id}/download.pdf")
async def download_legal_document_pdf(
    doc_id: str,
    article: str | None = Query(default=None),
) -> Response:
    """Return an authenticated PDF for an approved/indexed legal document.

    Prefer a physical internal PDF when available. Otherwise generate/proxy a PDF
    from the local legal-search service. Never expose candidate/unapproved docs.
    """
    started = perf_counter()
    from pathlib import Path
    from fastapi.responses import FileResponse

    # Gate access through the same detail endpoint: only active indexed documents
    # can be opened/downloaded from Ask citations.
    doc = await get_legal_document(doc_id, Response(), article=article, include_content=False)

    uploads_root = Path("data") / "uploads"
    source_asset = doc.get("source_asset") or {}
    source_asset_path = Path(str(source_asset.get("path") or "")).resolve() if source_asset.get("path") else None
    approved_asset_root = (uploads_root / "legal_sources").resolve()
    pdf_path = source_asset_path if (
        source_asset_path and source_asset_path.is_file() and approved_asset_root in source_asset_path.parents
        and source_asset_path.suffix.lower() == ".pdf"
    ) else uploads_root / "pdfs" / f"{doc_id}.pdf"
    if pdf_path.is_file():
        filename = f"{doc_id}.pdf"
        law_number = str(doc.get("law_number") or "").strip()
        if law_number:
            import re

            safe_law = re.sub(r"[^A-Za-z0-9_.-]+", "-", law_number).strip("-")
            safe_law = re.sub(r"-+", "-", safe_law)
            if safe_law:
                filename = f"{safe_law}.pdf"
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000, metadata={"origin": "original-file", "cached": True},
        )
        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=filename,
            headers={
                "X-Legal-Pdf-Origin": "original-file",
                "X-Legal-Download-Ms": str(round((perf_counter() - started) * 1000, 1)),
                "Cache-Control": "private, max-age=86400",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(
                f"{LEGAL_SEARCH_URL}/documents/{doc_id}/download.pdf",
                params={"article": article} if article else None,
            )
        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Chưa có file PDF nội bộ hoặc không tạo được PDF từ văn bản đã index.",
            )
        response.raise_for_status()
        filename = "legal-document.pdf"
        disposition = response.headers.get("content-disposition") or ""
        if "filename=" in disposition:
            filename = disposition.split("filename=")[-1].strip().strip('"')
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000,
            metadata={"origin": "system-extract", "cached": response.headers.get("x-legal-pdf-cache", "unknown")},
        )
        return Response(
            content=response.content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Legal-Pdf-Origin": "system-extract",
                "X-Legal-Download-Ms": str(round((perf_counter() - started) * 1000, 1)),
                "Cache-Control": response.headers.get("cache-control", "private, max-age=86400"),
                "X-Legal-Pdf-Cache": response.headers.get("x-legal-pdf-cache", "unknown"),
                "X-Legal-Export-Ms": response.headers.get("x-legal-export-ms", ""),
            },
        )
    except HTTPException as exc:
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=exc.status_code,
        )
        telemetry.record_issue("pdf_export_failed", category="pdf_stream_export", status_code=exc.status_code, error_class="http_error")
        raise
    except httpx.HTTPError as exc:
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=503,
        )
        telemetry.record_issue("pdf_export_failed", category="pdf_stream_export", status_code=503, error_class=exc.__class__.__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Dịch vụ tạo PDF văn bản pháp luật chưa sẵn sàng: {exc}",
        ) from exc



def _extract_docx(content: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Máy chủ chưa có thư viện python-docx để đọc file Word.",
        ) from exc
    document = Document(BytesIO(content))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
    table_rows = []
    for table in document.tables:
        for row in table.rows:
            values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if values:
                table_rows.append(" | ".join(values))
    return "\n".join([part for part in paragraphs + table_rows if part])


def _extract_pdf(content: bytes) -> str:
    try:
        import fitz  # PyMuPDF

        document = fitz.open(stream=content, filetype="pdf")
        text_content = "\n".join(page.get_text("text") for page in document).strip()
        document.close()
        if text_content:
            return text_content
    except Exception:
        pass

    try:
        import pypdf  # type: ignore[import-not-found]
    except Exception as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "Máy chủ chưa có thư viện đọc PDF phù hợp. "
                "Hiện có thể nạp TXT/DOCX ngay; PDF cần PyMuPDF hoặc pypdf."
            ),
        ) from exc
    reader = pypdf.PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


@router.post("/import/extract")
async def legal_import_extract(
    file: UploadFile = File(...),
    extractor: str = "auto",
) -> dict[str, Any]:
    filename = file.filename or "uploaded"
    lower_name = filename.lower()
    content = await file.read()
    extractor = extractor.strip().lower()
    metadata: dict[str, Any] = {"extractor_used": "basic"}
    if extractor not in {"auto", "basic", "rag_anything"}:
        raise HTTPException(
            status_code=400,
            detail="Extractor phai la auto, basic hoac rag_anything.",
        )

    text_content = ""
    if extractor in {"auto", "rag_anything"} and lower_name.endswith((".pdf", ".docx")):
        try:
            text_content, extra_metadata = extract_with_rag_anything(filename, content)
            metadata.update(extra_metadata)
        except RagAnythingUnavailable as exc:
            if extractor == "rag_anything":
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            metadata["extractor_fallback_reason"] = str(exc)

    if not text_content:
        if lower_name.endswith((".txt", ".md")):
            text_content = content.decode("utf-8", errors="replace")
        elif lower_name.endswith(".docx"):
            text_content = _extract_docx(content)
        elif lower_name.endswith(".pdf"):
            text_content = _extract_pdf(content)
            # B2: OCR fallback for local import if text is empty (scanned image)
            if len(text_content.strip()) < 100:
                try:
                    from api.crawlers.ocr_extractor import run_ocr_on_pdf_bytes
                    ocr_text = run_ocr_on_pdf_bytes(content)
                    if "[LỖI OCR" not in ocr_text and "[LOI OCR" not in ocr_text:
                        text_content = ocr_text
                        metadata["extractor_used"] = "ocr_fallback"
                    else:
                        metadata["extractor_fallback_reason"] = ocr_text
                except Exception as e:
                    metadata["extractor_fallback_reason"] = f"OCR module error: {e}"
        else:
            raise HTTPException(
                status_code=400,
                detail="Chỉ hỗ trợ file TXT, MD, DOCX hoặc PDF.",
            )

    # Step 12/15: Detect and mask PII (CCCD/phone/email) before returning
    pii = redact_upload_text(text_content or "")
    text_content = pii["text"]
    metadata["contains_pii"] = pii["contains_pii"]
    metadata["pii_types"] = pii["pii_types"]
    metadata["pii_counts"] = pii["pii_counts"]
    metadata["pii_masked"] = pii["masked"]
    metadata["retention"] = pii["retention"]

    return {
        "filename": filename,
        "characters": len(text_content),
        "content": text_content,
        **metadata,
    }


def _clean_html_text(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text_content = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text_content.splitlines() if line.strip()]
    return title, "\n".join(lines)


async def _crawl_with_crawl4ai(url: str) -> dict[str, Any]:
    if "vbpl.vn" in url.lower():
        from open_notebook.utils.vbpl_crawler import crawl_vbpl_url
        res = await crawl_vbpl_url(url)
        return {
            "source_url": url,
            "title": res["title"],
            "characters": len(res["content"]),
            "content": res["content"],
            "crawler": "playwright-custom",
            "requires_manual_review": True,
        }

    from crawl4ai import AsyncWebCrawler

    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url)
    markdown = str(getattr(result, "markdown", "") or "").strip()
    title = str(getattr(result, "title", "") or "").strip()
    if not markdown:
        raise ValueError("crawl4ai returned empty markdown")
    return {
        "source_url": url,
        "title": title,
        "characters": len(markdown),
        "content": markdown,
        "crawler": "crawl4ai",
        "requires_manual_review": True,
    }


def _validate_official_proposal_url(url: str) -> str:
    """Allow proposal previews only from public official sources."""
    value = url.strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    is_official_host = hostname == "vbpl.vn" or hostname.endswith(".vbpl.vn") or hostname.endswith(".gov.vn")
    if parsed.scheme != "https" or not hostname or not is_official_host:
        raise HTTPException(
            status_code=400,
            detail="Cán bộ chỉ được quét URL HTTPS từ VBPL hoặc website cơ quan nhà nước (.gov.vn).",
        )
    return value


@router.post("/crawl/preview")
async def legal_crawl_preview(request: LegalCrawlPreviewRequest) -> dict[str, Any]:
    try:
        try:
            return await _crawl_with_crawl4ai(request.url)
        except Exception:
            pass
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(
                request.url,
                headers={
                    "User-Agent": "HaiPhongLegalAssistant/1.0 (+local preview crawler)"
                },
            )
        response.raise_for_status()
        title, content = _clean_html_text(response.text)
        return {
            "source_url": str(response.url),
            "title": title,
            "characters": len(content),
            "content": content,
            "crawler": "httpx+beautifulsoup-fallback",
            "requires_manual_review": True,
        }
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Không crawl được URL: {exc}",
        ) from exc


@router.post("/proposals/preview")
async def officer_proposal_preview(
    request: LegalCrawlPreviewRequest, raw_request: Request
) -> dict[str, Any]:
    """Preview an official URL without importing or approving its content."""
    role = get_request_role(raw_request)
    if role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc admin được quét nguồn đề xuất.")
    url = _validate_official_proposal_url(request.url)
    result = await legal_crawl_preview(LegalCrawlPreviewRequest(url=url))
    result["proposal_only"] = True
    result["review_status"] = "candidate_pending_review"
    return result


@router.post("/import/preview")
async def legal_import_preview(request: LegalImportRequest) -> dict[str, Any]:
    return await _request("POST", "/import/preview", json=request.model_dump())


@router.post("/import")
async def legal_import_document(request: LegalImportRequest) -> dict[str, Any]:
    scope_val = "central"
    scope_lower = str(request.scope).lower()
    if "hải phòng" in scope_lower or "haiphong" in scope_lower:
        scope_val = "haiphong"
    elif "địa phương" in scope_lower or "phường" in scope_lower or "xã" in scope_lower:
        scope_val = "local"
        
    source_rows = await repo_query(
        "SELECT id FROM legal_crawl_source WHERE sitemap_scope = $scope LIMIT 1;",
        {"scope": scope_val},
    )
    if not source_rows:
        source_rows = await repo_query("SELECT id FROM legal_crawl_source LIMIT 1;")
    if not source_rows:
        raise HTTPException(
            status_code=503,
            detail="Hàng đợi duyệt văn bản chưa có nguồn crawler mặc định.",
        )

    from open_notebook.database.repository import ensure_record_id
    candidate_data = {
        "source": ensure_record_id(source_rows[0]["id"]),
        "title": request.title,
        "law_number": request.law_number,
        "description": request.applicability_info or request.title,
        "detail_url": request.source_url or "",
        "source_url": request.source_url or "",
        "document_type": request.document_type,
        "issuing_agency": request.issuing_agency,
        "scope": scope_val,
        "status": "pending",
        "suggested_action": "manual_import",
        "comparison_status": "new",
        "detected_changes": [],
        # SCHEMAFULL requires external_id; use law_number + hash-stable fallback.
        "external_id": request.law_number or f"manual-import:{request.title[:80]}",
        "raw_metadata": {
            "title": request.title,
            "law_number": request.law_number,
            "document_type": request.document_type,
            "issuing_agency": request.issuing_agency,
            "scope": request.scope,
            "sector": request.sector,
            "field_id": request.field_id,
            "issued_date": request.issued_date,
            "effective_date": request.effective_date,
            "expired_date": request.expired_date,
            "source_url": request.source_url,
            "applicability_info": request.applicability_info,
            "confirmed_official_source": request.confirmed_official_source,
        },
        "content": request.content,
    }
    
    res = await repo_create("legal_crawl_candidate", candidate_data)
    candidate_id = res[0].get("id") if isinstance(res, list) and len(res) > 0 else (res.get("id") if isinstance(res, dict) else str(res))
    
    return {
        "law_number": request.law_number,
        "article_count": 0,
        "chunk_count": 0,
        "message": "Văn bản đã được đưa vào hàng đợi duyệt của admin.",
        "candidate_id": str(candidate_id),
    }


@router.post("/proposals")
async def officer_document_proposal(
    raw_request: Request,
    domain: str = Form(...),
    source_type: str = Form(..., pattern="^(document|form|procedure|reference)$"),
    title: str = Form(..., min_length=5, max_length=500),
    reason: str = Form(..., min_length=10, max_length=2000),
    source_url: str = Form("", max_length=4000),
    law_number: str = Form("", max_length=255),
    document_type: str = Form("Tài liệu đề xuất", max_length=255),
    issuing_agency: str = Form("", max_length=255),
    scope: str = Form("central", max_length=100),
    sector: str = Form("", max_length=255),
    issued_date: str = Form(""),
    effective_date: str = Form(""),
    expired_date: str = Form(""),
    metadata_json: str = Form(""),
    content: str = Form(""),
    file: UploadFile | None = File(None),
) -> dict[str, Any]:
    role = get_request_role(raw_request)
    user_id = get_request_user_id(raw_request)
    if role != "officer":
        raise HTTPException(status_code=403, detail="Chi can bo moi duoc de xuat van ban.")
    if not user_id:
        raise HTTPException(status_code=403, detail="Khong xac dinh duoc tai khoan can bo.")

    profile = await get_user_profile(user_id)
    allowed_domains = (profile or {}).get("allowed_domains") or []
    if not _domain_allowed(domain, allowed_domains):
        raise HTTPException(
            status_code=403,
            detail="Can bo khong co quyen de xuat tai lieu ngoai linh vuc duoc phan cong.",
        )

    source_type = source_type.strip().lower()
    proposal_metadata: dict[str, Any] = {}
    if metadata_json.strip():
        try:
            parsed = json.loads(metadata_json)
            if not isinstance(parsed, dict):
                raise ValueError("metadata_json must be an object")
            proposal_metadata = parsed
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"metadata_json khong hop le: {exc}") from exc

    uploaded_file: dict[str, Any] | None = None
    extraction_result: dict[str, Any] = {
        "source": "manual_text" if content.strip() else "metadata_only",
        "characters": len(content or ""),
    }
    extracted_text = content.strip()
    if file:
        file_bytes = await file.read()
        uploaded_file = await _save_proposal_upload(file, file_bytes)
        extracted_text, extraction_result = _extract_upload_text(file.filename or "uploaded", file_bytes)
        pii = redact_upload_text(extracted_text)
        extracted_text = pii["text"]
        extraction_result.update(
            {
                "source": "upload",
                "filename": file.filename,
                "characters": len(extracted_text),
                "contains_pii": pii["contains_pii"],
                "pii_types": pii["pii_types"],
                "pii_counts": pii["pii_counts"],
                "pii_masked": pii["masked"],
                "retention": pii["retention"],
            }
        )

    if not source_url.strip() and not extracted_text:
        raise HTTPException(status_code=400, detail="Can nhap link nguon, noi dung hoac upload file de de xuat.")

    content_hash = extraction_result.get("text_fingerprint") or (
        hashlib.sha256(extracted_text.encode("utf-8")).hexdigest() if extracted_text else None
    )
    duplicates = await _duplicate_candidates(law_number.strip() or None, source_url.strip() or None, content_hash)
    validity_flags = _legal_validity_flags(
        effective_date=effective_date.strip() or None,
        expired_date=expired_date.strip() or None,
        source_url=source_url.strip() or None,
    )
    source_ref = await _candidate_source_for(scope.strip() or "officer_proposal")
    external_id_seed = "|".join(
        [
            "officer-proposal",
            str(user_id),
            domain,
            source_type,
            law_number.strip(),
            source_url.strip(),
            content_hash or title,
        ]
    )
    external_id = "officer-proposal:" + hashlib.sha256(external_id_seed.encode("utf-8")).hexdigest()[:32]

    raw_metadata = {
        **proposal_metadata,
        "ocr_status": extraction_result.get("ocr_status", "not_applicable"),
        "ocr_confidence": extraction_result.get("ocr_confidence"),
        "ocr_reason": extraction_result.get("reason", ""),
        "pdf_kind": extraction_result.get("pdf_kind", "not_pdf"),
        "language": extraction_result.get("language", "unknown"),
        "page_count": extraction_result.get("page_count", 0),
        "text_fingerprint": extraction_result.get("text_fingerprint"),
        "file_fingerprint": extraction_result.get("file_fingerprint"),
        "extraction_preview": extraction_result.get("preview", ""),
        "submitted_by": user_id,
        "domain": domain,
        "source_type": source_type,
        "proposal_reason": reason.strip(),
        "sector": sector.strip(),
        "issued_date": issued_date.strip() or None,
        "effective_date": effective_date.strip() or None,
        "expired_date": expired_date.strip() or None,
        "source_url": source_url.strip(),
        "confirmed_official_source": False,
        "candidate_origin": "officer_document_proposal",
    }
    candidate_data = {
        "source": source_ref,
        "external_id": external_id,
        "detail_url": source_url.strip(),
        "source_url": source_url.strip(),
        "sitemap_url": None,
        "sitemap_lastmod": None,
        "law_number": law_number.strip() or None,
        "title": title.strip(),
        "description": reason.strip(),
        "document_type": document_type.strip() or ("Biểu mẫu" if source_type == "form" else "Tài liệu đề xuất"),
        "issuing_agency": issuing_agency.strip() or None,
        "scope": scope.strip() or None,
        "status": "pending",
        "review_status": "pending",
        "suggested_action": "officer_proposal_review",
        "comparison_status": "duplicate_possible" if duplicates else "new",
        "detected_changes": [],
        "review_note": None,
        "imported_document": None,
        "raw_metadata": raw_metadata,
        "content": extracted_text or None,
        "content_hash": content_hash,
        "submitted_by": _user_account_record(user_id),
        "domain": domain,
        "source_type": source_type,
        "proposal_reason": reason.strip(),
        "uploaded_file": uploaded_file,
        "extraction_result": extraction_result,
        "duplicate_candidates": duplicates,
        "legal_validity_flags": validity_flags,
        "requested_changes_note": None,
    }
    created = await repo_create("legal_crawl_candidate", candidate_data)
    candidate = created[0] if isinstance(created, list) else created
    candidate_id = str(candidate.get("id") if isinstance(candidate, dict) else candidate)

    await write_audit_log(
        action="legal.proposal.create",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=user_id,
        actor_role=role,
        details={
            "domain": domain,
            "source_type": source_type,
            "title": title.strip(),
            "has_upload": uploaded_file is not None,
            "has_source_url": bool(source_url.strip()),
            "duplicate_count": len(duplicates),
            "reason": reason.strip(),
        },
        request=raw_request,
    )
    return {
        "candidate_id": candidate_id,
        "status": "pending",
        "review_status": "pending",
        "message": "De xuat da vao hang doi duyet cua admin. Chua duoc dung de tra loi RAG.",
        "preview": {
            "title": title.strip(),
            "domain": domain,
            "source_type": source_type,
            "characters": len(extracted_text or ""),
            "duplicates": duplicates,
            "legal_validity_flags": validity_flags,
            "extraction_result": extraction_result,
        },
    }


@router.get("/crawl/summary")
async def legal_crawl_summary(raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    summary = await LegalCrawlService.summary()
    sources = await LegalCrawlService.source_status()
    unread = await LegalCrawlService.get_notifications(unread_only=True)
    checked = [item.get("last_checked_at") for item in sources if item.get("last_checked_at")]
    summary.update({
        # Stable frontend contract. The old service names are retained below
        # for compatibility with scripts and tests.
        "pending_review_count": int(summary.get("pending_candidates") or 0),
        "unread_notification_count": len(unread),
        "source_count": len([item for item in sources if item.get("enabled")]),
        "last_checked_at": max(checked, key=str) if checked else None,
        "schedule_interval_minutes": 7 * 24 * 60,
        "sources": sources,
    })
    return summary


@router.get("/crawl/sources")
async def legal_crawl_sources(raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return {"sources": await LegalCrawlService.ensure_vbpl_sources()}


@router.get("/proposals/candidates")
async def officer_visible_candidates(raw_request: Request, status: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Officer-safe candidate view outside the admin-only crawler namespace."""
    role = get_request_role(raw_request)
    if role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc admin được xem candidate.")
    candidates = await LegalCrawlService.list_candidates(status=status, limit=min(max(limit, 1), 200))
    if role == "admin":
        return {"candidates": candidates}
    user_id = get_request_user_id(raw_request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản cán bộ.")
    profile = await get_user_profile(user_id)
    allowed_domains = list((profile or {}).get("allowed_domains") or [])
    visible = []
    for candidate in candidates:
        raw = candidate.get("raw_metadata") or {}
        submitted_by = str(candidate.get("submitted_by") or "")
        if raw.get("candidate_origin") != "officer_document_proposal":
            continue
        if submitted_by and submitted_by != str(user_id):
            continue
        if candidate.get("domain") and _domain_allowed(str(candidate["domain"]), allowed_domains):
            visible.append(candidate)
    # Hide unreviewed uploaded content; officers only need the review metadata.
    for candidate in visible:
        candidate.pop("content", None)
        candidate.pop("extraction_result", None)
    return {"candidates": visible}


@router.get("/proposals/weekly-monitor")
async def officer_weekly_monitor(raw_request: Request) -> dict[str, Any]:
    """Show an officer the weekly crawler status and candidates for assigned domains."""
    role = get_request_role(raw_request)
    if role != "officer":
        raise HTTPException(status_code=403, detail="Chỉ cán bộ được xem theo dõi quét theo lĩnh vực.")
    user_id = get_request_user_id(raw_request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản cán bộ.")

    profile = await get_user_profile(user_id)
    allowed_domains = [
        item for item in ((profile or {}).get("allowed_domains") or [])
        if item in LEGAL_DOMAINS
    ]
    if not allowed_domains:
        return {
            "enabled": False,
            "interval_minutes": 7 * 24 * 60,
            "allowed_domains": [],
            "sources": [],
            "pending_candidates": [],
            "pending_count": 0,
            "warning": "Tài khoản cán bộ chưa được admin gán lĩnh vực.",
        }

    await LegalCrawlService.ensure_vbpl_sources()
    source_rows = await LegalCrawlService.source_status()
    visible_sources = []
    for source in source_rows:
        if not source.get("enabled"):
            continue
        source_domains = set(source.get("domains") or LEGAL_DOMAINS)
        if not source_domains.intersection(allowed_domains):
            continue
        visible_sources.append({
            "id": source.get("id"),
            "name": source.get("name"),
            "scope": source.get("scope"),
            "base_url": source.get("base_url"),
            "enabled": source.get("enabled"),
            "interval_minutes": source.get("interval_minutes"),
            "last_checked_at": source.get("last_checked_at"),
            "last_success_at": source.get("last_success_at"),
            "last_status": source.get("last_status"),
            "last_error": source.get("last_error"),
            "last_run_stats": source.get("last_run_stats") or {},
        })

    candidates: list[dict[str, Any]] = []
    for domain in allowed_domains:
        rows = await LegalCrawlService.list_candidates(status="pending", limit=100, domain=domain)
        for candidate in rows:
            raw = candidate.get("raw_metadata") or {}
            if raw.get("candidate_origin") == "officer_document_proposal":
                continue
            candidates.append({
                "id": candidate.get("id"),
                "title": candidate.get("title"),
                "law_number": candidate.get("law_number"),
                "domain": candidate.get("domain"),
                "source_type": candidate.get("source_type"),
                "source_url": candidate.get("source_url"),
                "created": candidate.get("created"),
                "review_status": candidate.get("review_status") or candidate.get("status"),
            })

    candidates.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
    return {
        "enabled": True,
        "interval_minutes": 7 * 24 * 60,
        "allowed_domains": allowed_domains,
        "sources": visible_sources,
        "pending_candidates": candidates[:50],
        "pending_count": len(candidates),
        "candidate_policy": "candidate_pending_review",
        "admin_approval_required": True,
    }


@router.patch("/crawl/sources/{source_id}")
async def legal_crawl_update_source(
    source_id: str, request: LegalCrawlSourceUpdateRequest, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    payload = request.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=400, detail="Không có cấu hình cần cập nhật.")
    source = await LegalCrawlService.update_source(source_id, payload)
    await write_audit_log(
        action="legal.crawl.source.update",
        entity_type="legal_crawl_source",
        entity_id=source_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"fields": sorted(payload.keys())},
        request=raw_request,
    )
    return {"source": source}


@router.post("/crawl/scan")
async def legal_crawl_scan_now(raw_request: Request, source_id: str | None = None) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    if source_id:
        return await LegalCrawlService.scan_source(source_id)
    sources = await LegalCrawlService.ensure_vbpl_sources()
    runs = []
    for source in sources:
        if source.get("enabled", False):
            runs.append(await LegalCrawlService.scan_source(str(source["id"])))
    created = sum(int((run.get("statistics") or {}).get("created") or 0) for run in runs)
    updated = sum(int((run.get("statistics") or {}).get("updated") or 0) for run in runs)
    await write_audit_log(
        action="legal.crawl.run.manual",
        entity_type="legal_crawl_source",
        entity_id="all_enabled_sources",
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"run_count": len(runs), "created": created, "updated": updated},
        request=raw_request,
    )
    return {"status": "completed", "runs": runs, "run_count": len(runs), "created": created, "updated": updated}


@router.get("/crawl/candidates")
async def legal_crawl_candidates(
    raw_request: Request,
    status: str | None = None,
    domain: str | None = Query(default=None, max_length=100),
    source_type: str | None = Query(default=None, pattern="^(document|form|procedure|reference)$"),
    limit: int = 100,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return {
        "candidates": await LegalCrawlService.list_candidates(
            status=status, domain=domain, source_type=source_type, limit=limit
        )
    }


@router.get("/crawl/candidates/{candidate_id}")
async def legal_crawl_candidate(candidate_id: str, raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản chờ duyệt.")
    return {"candidate": candidate, "review_preview": _candidate_review_preview(candidate)}


@router.post("/crawl/candidates/{candidate_id}/review")
async def legal_crawl_review_candidate(
    candidate_id: str, request: LegalCandidateDecisionRequest, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    try:
        candidate = await LegalCrawlService.review_candidate(
            candidate_id,
            request.decision,
            request.review_note,
            reviewed_by=get_request_user_id(raw_request),
            reviewed_role=get_request_role(raw_request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        telemetry.record_issue(
            "candidate_review_failed",
            category="crawler_review",
            status_code=500,
            error_class=exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "candidate_review_failed",
                "message": "Không duyệt được văn bản ứng viên.",
                "suggestion": "Kiểm tra backend, database và trạng thái candidate. Nếu đang duyệt approve, hãy xem import_job/import_status để biết bước nào lỗi.",
                "trace_id": uuid.uuid4().hex,
            },
        ) from exc
    await write_audit_log(
        action="legal.candidate.review",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "decision": request.decision,
            "review_note": request.review_note,
        },
        request=raw_request,
    )
    return {"candidate": candidate}


@router.patch("/crawl/candidates/{candidate_id}/metadata")
async def legal_crawl_update_candidate_metadata(
    candidate_id: str,
    request: LegalCandidateMetadataUpdateRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """Admin corrects verified metadata before retrying an approved import."""
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy candidate.")
    changes = request.model_dump(exclude_none=True)
    raw_metadata = candidate.get("raw_metadata") or {}
    raw_metadata.update(changes)
    top_level = {
        key: value for key, value in changes.items()
        if key in {"law_number", "document_type", "issuing_agency", "scope", "source_url"}
    }
    if "source_url" in changes:
        top_level["detail_url"] = changes["source_url"]
    validity = _legal_validity_flags(
        effective_date=raw_metadata.get("effective_date"),
        expired_date=raw_metadata.get("expired_date"),
        source_url=raw_metadata.get("source_url") or candidate.get("source_url"),
    )
    recommendation = LegalCrawlService.build_review_recommendation({
        **candidate,
        **top_level,
        "raw_metadata": raw_metadata,
        "legal_validity_flags": validity,
    })
    rows = await repo_update("legal_crawl_candidate", candidate["id"], {
        **top_level,
        "raw_metadata": raw_metadata,
        "legal_validity_flags": validity,
        "review_recommendation": recommendation,
        "updated": datetime.now(timezone.utc),
    })
    updated = rows[0] if rows else {**candidate, **top_level, "raw_metadata": raw_metadata}
    await write_audit_log(
        action="legal.candidate.metadata.update",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"fields": sorted(changes.keys())},
        request=raw_request,
    )
    return {"candidate": updated, "validation_errors": LegalCrawlService.validate_candidate_for_import(updated)}


@router.get("/crawl/import-jobs/{job_id}")
async def legal_import_job_status(job_id: str, raw_request: Request) -> dict[str, Any]:
    """Poll a non-blocking approval → embed job; admin-only audit surface."""
    _require_crawl_admin(raw_request)
    record_id = job_id if ":" in job_id else f"legal_import_job:{job_id}"
    rows = await repo_query(
        "SELECT * FROM legal_import_job WHERE id = $id LIMIT 1;",
        {"id": ensure_record_id(record_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Không tìm thấy import job.")
    return {"job": rows[0]}


@router.post("/crawl/candidates/{candidate_id}/import")
async def legal_crawl_import_approved_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Retry the guarded import only after admin has completed verification."""
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy candidate.")
    if candidate.get("status") not in {"approved", "import_failed"}:
        raise HTTPException(status_code=409, detail="Candidate phải được admin duyệt trước khi import.")
    validation_errors = LegalCrawlService.validate_candidate_for_import(candidate)
    if validation_errors:
        raise HTTPException(status_code=409, detail={"validation_errors": validation_errors})
    try:
        job = await LegalCrawlService.enqueue_import_job(candidate_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.candidate.import.enqueue",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"job_id": str(job.get("id")), "status": job.get("status")},
        request=raw_request,
    )
    return {"job": job, "status": "queued"}


@router.post("/crawl/candidates/{candidate_id}/assess")
async def legal_crawl_assess_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Trigger AI assessment for a candidate without changing its status."""
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản chờ duyệt.")
    # Force re-assessment
    try:
        assessed = await LegalCrawlService.assess_candidate(candidate, force=True)
    except Exception as exc:
        telemetry.record_issue(
            "candidate_assess_failed",
            category="crawler_review",
            status_code=500,
            error_class=exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "candidate_assess_failed",
                "message": "AI đánh giá candidate chưa chạy được.",
                "suggestion": "Kiểm tra API key/model cloud hoặc chuyển sang kiểm tra rule-based; admin vẫn có thể duyệt thủ công nếu metadata và nguồn hợp lệ.",
                "trace_id": uuid.uuid4().hex,
            },
        ) from exc
    await write_audit_log(
        action="legal.candidate.assess",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"forced": True, "has_ai_assessment": bool(assessed.get("ai_assessment"))},
        request=raw_request,
    )
    warning = None
    if not assessed.get("ai_assessment"):
        warning = (
            "AI chưa trả về đánh giá hợp lệ. Hệ thống đã giữ candidate trong hàng duyệt "
            "và cập nhật khuyến nghị kiểm duyệt rule-based nếu có."
        )
    return {"candidate": assessed, "ai_assessment": assessed.get("ai_assessment"), "warning": warning}


@router.post("/crawl/candidates/{candidate_id}/extract", status_code=202)
async def legal_crawl_extract_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Queue local extraction/OCR so the admin page never waits for heavy work.

    The job only processes an already-uploaded local source file.  It never
    deep-fetches a public candidate URL and it never changes approval/import
    status.  Clients poll the returned job endpoint.
    """
    _require_crawl_admin(raw_request)
    try:
        job = await LegalCrawlService.enqueue_processing_job(candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.candidate.extract.queue",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"job_id": str(job.get("id")), "job_type": "extract_ocr"},
        request=raw_request,
    )
    return {"job": job, "status": "queued", "message": "Đã xếp hàng trích xuất/OCR. Có thể tiếp tục thao tác và quay lại xem kết quả."}


@router.get("/crawl/extract-jobs/{job_id}")
async def legal_crawl_extract_job_status(job_id: str, raw_request: Request) -> dict[str, Any]:
    """Poll status of a queued candidate extraction/OCR job; admin-only."""
    _require_crawl_admin(raw_request)
    record_id = job_id if ":" in job_id else f"legal_candidate_processing_job:{job_id}"
    rows = await repo_query(
        "SELECT * FROM legal_candidate_processing_job WHERE id = $id LIMIT 1;",
        {"id": ensure_record_id(record_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Không tìm thấy job trích xuất/OCR.")
    return {"job": rows[0]}


@router.get("/crawl/notifications")
async def legal_crawl_notifications(unread_only: bool = False) -> dict[str, Any]:
    return {
        "notifications": await LegalCrawlService.get_notifications(
            unread_only=unread_only
        )
    }


@router.post("/crawl/notifications/{notification_id}/read")
async def legal_crawl_mark_notification_read(notification_id: str) -> dict[str, Any]:
    return {
        "notification": await LegalCrawlService.mark_notification_read(notification_id)
    }
