"""Fail-closed source-gap discovery using the existing candidate crawler rules.

Jobs contain opaque case identifiers and public-source metadata only.  A
successful download is always a pending candidate; this module never approves,
indexes, embeds, or changes the active retrieval collection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from api.crawlers.crawl4ai_fetcher import fetch_rendered
from api.official_http import build_verified_ssl_context
from scripts.crawl_canonical_forms import (
    _extract_links,
    _score_link,
    candidate_from_download,
    is_allowed_official_url,
    validate_download,
)

GAP_TYPES = {"MISSING_FORM_SOURCE", "MISSING_LEGAL_SOURCE"}
TERMINAL_STATUSES = {
    "downloaded_candidate",
    "verified_gap",
    "blocked_external",
}
MAX_DISTINCT_CHECK_DAYS = 7
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_PATH = ROOT / "data" / "source_gap_jobs" / "source_gap_jobs_v1.json"
DEFAULT_CANDIDATE_DIR = ROOT / "data" / "uploads" / "source_gap_candidates"
DEFAULT_FORM_REVIEW_QUEUE_PATH = (
    ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
)
DEFAULT_FORM_REVIEW_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates"
_CORRUPT_IDENTITY_MARKERS = ("\ufffd", "Ã", "Â", "Ä", "áº", "á»")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_official_identity(value: Any) -> str:
    text = str(value or "").strip()
    if not text or "?" in text or any(marker in text for marker in _CORRUPT_IDENTITY_MARKERS):
        raise ValueError("invalid_official_identity_unicode")
    return text


def build_candidate_queue_payload(
    job: Mapping[str, Any],
    *,
    extracted_text: str | None = None,
) -> dict[str, Any]:
    """Build a fail-closed review-queue payload from a downloaded source gap.

    Metadata comes only from the source-gap job's deterministic official-source
    extraction.  This function never approves, imports, embeds, or serves the
    candidate.
    """

    if str(job.get("status") or "") != "downloaded_candidate":
        raise ValueError("downloaded_candidate_required")
    candidate = job.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("downloaded_candidate_payload_required")
    metadata = job.get("official_metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("official_metadata_required")
    if metadata.get("confirmed_official_source") is not True:
        raise ValueError("official_source_confirmation_required")

    gap_type = str(job.get("gap_type") or "")
    is_legal_source = gap_type == "MISSING_LEGAL_SOURCE"
    if job.get("expected_code"):
        _validate_official_identity(job.get("expected_code"))
    if job.get("expected_name"):
        _validate_official_identity(job.get("expected_name"))
    expected_code = _validate_official_identity(
        metadata.get("law_number") or job.get("expected_code")
    )
    expected_name = _validate_official_identity(
        metadata.get("title") or job.get("expected_name")
    )
    issuing_agency = _validate_official_identity(metadata.get("issuing_agency"))
    document_type = _validate_official_identity(metadata.get("document_type"))
    source_page = str(
        (candidate.get("provenance") or {}).get("source_page")
        or candidate.get("source_page")
        or ""
    ).strip()
    download_url = str(
        (candidate.get("provenance") or {}).get("download_url")
        or candidate.get("download_url")
        or ""
    ).strip()
    if not source_page or not download_url:
        raise ValueError("official_source_url_required")
    if not is_allowed_official_url(source_page) or not is_allowed_official_url(download_url):
        raise ValueError("source_host_not_allowed")

    checksum = str(candidate.get("sha256") or "").strip().casefold()
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise ValueError("candidate_checksum_invalid")
    local_path = str(candidate.get("local_path") or "").strip()
    if not local_path:
        raise ValueError("candidate_local_file_required")

    issued_date = str(metadata.get("issued_date") or "").strip()
    effective_date = str(metadata.get("effective_date") or "").strip()
    scope = str(metadata.get("scope") or "").strip()
    if is_legal_source and (not issued_date or not effective_date or not scope):
        raise ValueError("official_effectivity_metadata_required")

    extraction = job.get("extraction_result")
    if not isinstance(extraction, Mapping):
        extraction = {
            "status": "review_required",
            "ocr_status": "pending" if str(candidate.get("file_format") or "").casefold() == "pdf" else "not_required",
            "processed_pages": 0,
            "total_pages": 0,
            "reason_code": "EXTRACTION_PENDING_REVIEW",
        }
    extraction_complete = (
        str(extraction.get("ocr_status") or extraction.get("status") or "") == "ok"
        and extraction.get("complete") is True
        and int(extraction.get("processed_pages") or 0)
        == int(extraction.get("total_pages") or 0)
        and int(extraction.get("total_pages") or 0) > 0
        and not extraction.get("failed_pages")
        and len(str(extracted_text or "").strip()) >= 100
    )
    provenance = dict(candidate.get("provenance") or {})
    provenance.update(
        {
            "source_page": source_page,
            "download_url": download_url,
            "sha256": checksum,
        }
    )
    raw_metadata = {
        "candidate_origin": "source_gap_job",
        "source_gap_job_id": str(job.get("job_id") or ""),
        "confirmed_official_source": True,
        "legal_as_of": str(job.get("legal_as_of") or ""),
        "issued_date": issued_date or None,
        "effective_date": effective_date or None,
        "scope": scope or None,
        "document_type": document_type,
        "issuing_agency": issuing_agency,
        "procedure_id": str(job.get("procedure_id") or ""),
        "provenance": provenance,
    }
    pending_status = "pending" if extraction_complete else "changes_requested"
    hard_gate_reasons = ["HUMAN_LEGAL_REVIEW_REQUIRED"]
    if not extraction_complete:
        hard_gate_reasons.insert(0, "EXTRACTION_REVIEW_REQUIRED")
    return {
        "external_id": f"source-gap:{str(job.get('job_id') or '').strip()}",
        "detail_url": source_page,
        "source_url": source_page,
        "law_number": expected_code if is_legal_source else None,
        "title": expected_name,
        "description": None,
        "document_type": document_type,
        "issuing_agency": issuing_agency,
        "scope": scope or "central",
        "status": pending_status,
        "review_status": pending_status,
        "approved": False,
        "legal_review_status": "pending",
        "suggested_action": (
            "human_legal_review"
            if extraction_complete
            else "complete_extraction_then_legal_review"
        ),
        "comparison_status": "new",
        "detected_changes": [],
        "review_note": None,
        "raw_metadata": raw_metadata,
        "content_hash": str(extraction.get("text_fingerprint") or checksum),
        "content": str(extracted_text or "").strip() or None,
        "domain": str(metadata.get("domain") or "") or None,
        "source_type": str(candidate.get("source_type") or "legal_document_candidate"),
        "proposal_reason": (
            "Nguồn chính thức đã tải và kiểm tra checksum; chờ hoàn tất trích xuất "
            "và rà soát pháp lý."
        ),
        "submitted_by": None,
        "uploaded_file": {
            "path": local_path,
            "filename": str(candidate.get("file_name") or Path(local_path).name),
            "file_format": str(candidate.get("file_format") or ""),
            "sha256": checksum,
            "size_bytes": int(candidate.get("size_bytes") or 0),
            "download_url": download_url,
        },
        "extraction_result": dict(extraction),
        "legal_validity_flags": {
            "official_source": True,
            "legal_review_required": True,
            "effective_date_from_official_metadata": effective_date or None,
            "serving_eligible": False,
        },
        "review_recommendation": {
            "hard_gate": {
                "eligible": False,
                "reason_codes": hard_gate_reasons,
            }
        },
        "created": datetime.now(timezone.utc),
        "updated": datetime.now(timezone.utc),
    }


def sync_downloaded_form_candidate_to_review_queue(
    job: Mapping[str, Any],
    *,
    project_root: Path = ROOT,
    queue_path: Path = DEFAULT_FORM_REVIEW_QUEUE_PATH,
    review_dir: Path = DEFAULT_FORM_REVIEW_DIR,
) -> dict[str, Any]:
    """Copy one verified form artifact into the existing human-review queue.

    This bridge is file-only and fail-closed: it never changes the canonical
    catalog, procedure bindings, official index, approval state, or runtime
    serving eligibility.
    """

    if str(job.get("gap_type") or "") != "MISSING_FORM_SOURCE":
        raise ValueError("form_source_gap_required")
    if str(job.get("status") or "") != "downloaded_candidate":
        raise ValueError("downloaded_candidate_required")
    candidate = job.get("candidate")
    metadata = job.get("official_metadata")
    if not isinstance(candidate, Mapping):
        raise ValueError("downloaded_candidate_payload_required")
    if not isinstance(metadata, Mapping) or metadata.get(
        "confirmed_official_source"
    ) is not True:
        raise ValueError("official_source_confirmation_required")

    procedure_id = _validate_official_identity(job.get("procedure_id"))
    form_name = _validate_official_identity(
        candidate.get("canonical_name") or job.get("expected_name")
    )
    raw_official_code = (
        job.get("official_procedure_code")
        or job.get("expected_code")
        or metadata.get("official_procedure_code")
    )
    if not str(raw_official_code or "").strip():
        raise ValueError("official_procedure_code_required")
    official_code = _validate_official_identity(raw_official_code)
    source_page = str(
        (candidate.get("provenance") or {}).get("source_page")
        or candidate.get("source_page")
        or ""
    ).strip()
    download_url = str(
        (candidate.get("provenance") or {}).get("download_url")
        or candidate.get("download_url")
        or ""
    ).strip()
    if not is_allowed_official_url(source_page) or not is_allowed_official_url(
        download_url
    ):
        raise ValueError("source_host_not_allowed")

    expected_checksum = str(candidate.get("sha256") or "").strip().casefold()
    if len(expected_checksum) != 64 or any(
        char not in "0123456789abcdef" for char in expected_checksum
    ):
        raise ValueError("candidate_checksum_invalid")
    source_path = Path(str(candidate.get("local_path") or ""))
    if not source_path.is_absolute():
        source_path = (project_root / source_path).resolve()
    else:
        source_path = source_path.resolve()
    try:
        source_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError("candidate_path_outside_project") from exc
    if not source_path.is_file():
        raise ValueError("candidate_local_file_required")
    actual_checksum = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if actual_checksum != expected_checksum:
        raise ValueError("candidate_checksum_drift")

    suffix = source_path.suffix.casefold()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise ValueError("unsupported_form_file")
    review_dir.mkdir(parents=True, exist_ok=True)
    destination = review_dir / f"source-gap-{expected_checksum[:24]}{suffix}"
    if destination.exists():
        destination_checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
        if destination_checksum != expected_checksum:
            raise ValueError("review_candidate_checksum_drift")
    else:
        shutil.copy2(source_path, destination)

    try:
        relative_destination = str(
            destination.resolve().relative_to(project_root.resolve())
        ).replace("\\", "/")
    except ValueError as exc:
        raise ValueError("review_candidate_path_outside_project") from exc

    queue = (
        json.loads(queue_path.read_text(encoding="utf-8"))
        if queue_path.is_file()
        else {"summary": {}, "records": []}
    )
    records = list(queue.get("records") or [])
    job_suffix = re.sub(
        r"[^0-9a-z]+",
        "-",
        str(job.get("job_id") or "").casefold(),
    ).strip("-")[-24:]
    record_id = f"source-gap-{job_suffix}-{expected_checksum[:16]}"
    existing_index = next(
        (
            index
            for index, item in enumerate(records)
            if item.get("source_gap_job_id") == job.get("job_id")
            or item.get("id") == record_id
        ),
        None,
    )
    provenance = dict(candidate.get("provenance") or {})
    legal_basis = [
        str(item).strip()
        for item in (metadata.get("legal_basis") or [])
        if str(item).strip()
    ]
    record = {
        "id": record_id,
        "source_gap_job_id": str(job.get("job_id") or ""),
        "detected_form_name": form_name,
        "form_title": form_name,
        "procedure_id": procedure_id,
        "suggested_procedure_id": procedure_id,
        "official_procedure_code": official_code,
        "form_code": str(job.get("expected_code") or "").strip() or None,
        "domain": str(metadata.get("domain") or "unknown"),
        "suggested_domain": str(metadata.get("domain") or "unknown"),
        "file_name": destination.name,
        "file_path": relative_destination,
        "local_path": relative_destination,
        "sha256": expected_checksum,
        "source_sha256": provenance.get("source_sha256") or expected_checksum,
        "size_bytes": destination.stat().st_size,
        "source_url": source_page,
        "page_url": source_page,
        "source_page_url": source_page,
        "source_download_url": download_url,
        "publisher": provenance.get("publisher"),
        "official_level": "official",
        "legal_basis": legal_basis,
        "effective_status": "effectivity_review_required",
        "provenance": {
            **provenance,
            "kind": "official_source_gap_download",
            "source_page_url": source_page,
            "source_download_url": download_url,
        },
        "preparation_status": "ready_for_human_review",
        "catalog_status": "candidate_pending_review",
        "legal_review_status": "candidate_pending_review",
        "review_status": "candidate_pending_review",
        "is_approved": False,
        "approved": False,
        "is_canonical": False,
        "runtime_eligible": False,
        "has_official_file": True,
        "has_download": False,
        "download_url": None,
        "hard_gate_reason_codes": [
            "HUMAN_LEGAL_REVIEW_REQUIRED",
            "CANONICAL_BINDING_NOT_FOUND",
            "EFFECTIVITY_REVIEW_REQUIRED",
        ],
    }
    action = "created"
    if existing_index is not None:
        existing = records[existing_index]
        if str(existing.get("sha256") or "").casefold() not in {
            "",
            expected_checksum,
        }:
            raise ValueError("candidate_checksum_drift")
        if existing == record:
            return {
                "action": "unchanged",
                "candidate_id": record_id,
                "checksum": expected_checksum,
            }
        records[existing_index] = record
        action = "updated"
    else:
        records.append(record)

    status_counts: dict[str, int] = {}
    for item in records:
        status = str(item.get("review_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    queue["records"] = records
    queue["summary"] = {
        **dict(queue.get("summary") or {}),
        "total": len(records),
        "review_status_counts": status_counts,
        "auto_approved": 0,
    }
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = queue_path.with_suffix(f"{queue_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, queue_path)
    return {
        "action": action,
        "candidate_id": record_id,
        "checksum": expected_checksum,
    }


async def sync_downloaded_candidate_to_review_queue(
    job: Mapping[str, Any],
    *,
    extracted_text: str | None = None,
    query: Any = None,
    create: Any = None,
    update: Any = None,
) -> dict[str, Any]:
    """Idempotently bridge one downloaded gap into the existing Admin queue."""

    if str(job.get("gap_type") or "") == "MISSING_FORM_SOURCE":
        return sync_downloaded_form_candidate_to_review_queue(job)

    from open_notebook.database.repository import (
        ensure_record_id,
        repo_create,
        repo_query,
        repo_update,
    )

    query = query or repo_query
    create = create or repo_create
    update = update or repo_update
    payload = build_candidate_queue_payload(job, extracted_text=extracted_text)

    source_rows = await query(
        "SELECT id FROM legal_crawl_source "
        "WHERE source_type = 'source_gap_candidate' LIMIT 1;"
    )
    if source_rows:
        source_id = source_rows[0]["id"]
    else:
        created_source = await create(
            "legal_crawl_source",
            {
                "name": "Official source-gap candidates",
                "source_type": "source_gap_candidate",
                "sitemap_scope": "official_candidate_only",
                "base_url": "local://source-gap-candidates",
                "enabled": False,
                "interval_minutes": 1440,
                "lookback_days": 3650,
                "max_documents_per_run": 0,
                "filter_keyword": None,
                "last_checked_at": None,
                "last_success_at": None,
                "last_error": None,
                "created": datetime.now(timezone.utc),
                "updated": datetime.now(timezone.utc),
            },
        )
        source_row = created_source[0] if isinstance(created_source, list) else created_source
        source_id = source_row["id"]
    payload["source"] = ensure_record_id(source_id)

    existing_rows = await query(
        "SELECT * FROM legal_crawl_candidate WHERE external_id = $external_id LIMIT 1;",
        {"external_id": payload["external_id"]},
    )
    if existing_rows:
        existing = existing_rows[0]
        old_checksum = str((existing.get("uploaded_file") or {}).get("sha256") or "").casefold()
        new_checksum = str(payload["uploaded_file"]["sha256"]).casefold()
        if old_checksum and old_checksum != new_checksum:
            raise ValueError("candidate_checksum_drift")
        already_synced = (
            old_checksum == new_checksum
            and existing.get("approved") is False
            and str(existing.get("legal_review_status") or "") == "pending"
            and str(
                (existing.get("raw_metadata") or {}).get("source_gap_job_id") or ""
            )
            == str(job.get("job_id") or "")
            and dict(existing.get("extraction_result") or {})
            == dict(payload.get("extraction_result") or {})
        )
        if already_synced:
            return {
                "action": "unchanged",
                "external_id": payload["external_id"],
                "checksum": new_checksum,
            }
        payload.pop("created", None)
        await update("legal_crawl_candidate", existing["id"], payload)
        return {
            "action": "updated",
            "external_id": payload["external_id"],
            "checksum": new_checksum,
        }

    created_candidate = await create("legal_crawl_candidate", payload)
    return {
        "action": "created",
        "external_id": payload["external_id"],
        "checksum": payload["uploaded_file"]["sha256"],
        "candidate_created": bool(created_candidate),
    }


def create_source_gap_job(
    *,
    case_id: str,
    gap_type: str,
    source_pages: Sequence[str],
    legal_as_of: str,
    procedure_id: str | None = None,
    expected_name: str | None = None,
    expected_code: str | None = None,
    source_pages_zero_based: Sequence[int] | None = None,
) -> dict[str, Any] | None:
    """Create an opaque candidate-discovery job, or skip retrieval regressions."""

    normalized_type = str(gap_type or "").strip().upper()
    if normalized_type == "FOUND_NOT_RETRIEVED":
        return None
    if normalized_type not in GAP_TYPES:
        raise ValueError("unsupported_gap_type")
    pages = list(dict.fromkeys(str(item).strip() for item in source_pages if str(item).strip()))
    if not pages:
        raise ValueError("official_source_seed_required")
    if any(not is_allowed_official_url(item) for item in pages):
        raise ValueError("source_host_not_allowed")
    page_indexes = sorted(
        {
            int(item)
            for item in (source_pages_zero_based or [])
            if int(item) >= 0
        }
    )
    if source_pages_zero_based and len(page_indexes) != len(
        set(source_pages_zero_based)
    ):
        raise ValueError("invalid_source_page_indexes")
    material = "\n".join(
        [
            str(case_id),
            normalized_type,
            str(legal_as_of),
            ",".join(str(item) for item in page_indexes),
            *pages,
        ]
    )
    return {
        "schema_version": "source-gap-job-v1",
        "job_id": f"sgj-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}",
        "case_id": str(case_id),
        "gap_type": normalized_type,
        "status": "queued",
        "reason_code": "SOURCE_GAP_QUEUED",
        "source_pages": pages,
        "legal_as_of": str(legal_as_of),
        "procedure_id": str(procedure_id or ""),
        "expected_name": str(expected_name or ""),
        "expected_code": str(expected_code or ""),
        "source_pages_zero_based": page_indexes,
        "checked_dates": [],
        "attempt_count": 0,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }


def enqueue_answer_source_gap_notice(
    *,
    question: str,
    missing_facets: Sequence[str],
    domain: str,
    legal_as_of: str,
    actor_role: str,
    store_path: Path = DEFAULT_STORE_PATH,
) -> dict[str, Any]:
    """Queue a privacy-safe missing-evidence notice for Admin review only.

    The notice is deliberately not a crawl/import job: it carries no raw
    question and has every mutation flag disabled.  Admin must first map it to
    an official source before the existing candidate-review workflow can act.
    """

    if str(actor_role or "").strip().casefold() not in {"system", "admin"}:
        raise PermissionError("system_or_admin_role_required")
    facets = sorted(
        {
            str(item or "").strip().casefold()
            for item in missing_facets
            if str(item or "").strip()
        }
    )
    if not facets:
        raise ValueError("missing_facets_required")
    normalized_question = " ".join(str(question or "").split()).casefold()
    question_hash = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()
    material = "|".join(
        (question_hash, str(domain or ""), str(legal_as_of or ""), *facets)
    )
    notice = {
        "schema_version": "answer-source-gap-v1",
        "job_id": f"asg-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}",
        "case_id": "",
        "gap_type": "ANSWER_EVIDENCE_MISSING",
        "status": "admin_review_required",
        "reason_code": "ANSWER_EVIDENCE_MISSING",
        "visibility": "admin",
        "question_sha256": question_hash,
        "missing_facets": facets,
        "domain": str(domain or "").strip(),
        "legal_as_of": str(legal_as_of or "").strip(),
        "source_pages": [],
        "approval_required": True,
        "auto_import": False,
        "auto_approve": False,
        "auto_activate": False,
        "checked_dates": [],
        "attempt_count": 0,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    stored = enqueue_source_gap_job(notice, store_path=store_path)
    return dict(stored or notice)


def list_answer_source_gap_notices(
    *,
    role: str,
    store_path: Path = DEFAULT_STORE_PATH,
) -> list[dict[str, Any]]:
    if str(role or "").strip().casefold() != "admin":
        raise PermissionError("admin_role_required")
    return [
        dict(item)
        for item in load_source_gap_jobs(store_path)
        if item.get("schema_version") == "answer-source-gap-v1"
        and item.get("visibility") == "admin"
    ]


def _record_check(job: Mapping[str, Any], checked_on: date) -> dict[str, Any]:
    checked_dates = list(dict.fromkeys([
        *[str(item) for item in job.get("checked_dates") or []],
        checked_on.isoformat(),
    ]))
    return {
        **dict(job),
        "checked_dates": checked_dates,
        "attempt_count": len(checked_dates),
        "updated_at": _utcnow(),
    }


def _basic_malware_scan(content: bytes) -> tuple[bool, str]:
    lowered = content.lower()
    if b"eicar-standard-antivirus-test-file" in lowered:
        return False, "MALWARE_SIGNATURE_QUARANTINED"
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = [name.casefold() for name in archive.namelist()]
        except zipfile.BadZipFile:
            return False, "MAGIC_BYTES_MISMATCH"
        if any(
            name.endswith("vbaproject.bin")
            or name.endswith(".exe")
            or name.endswith(".dll")
            for name in names
        ):
            return False, "ACTIVE_CONTENT_QUARANTINED"
    return True, "BASIC_SCAN_CLEAR"


def _redirect_chain(response: httpx.Response) -> list[str]:
    return [
        *[str(item.url) for item in response.history],
        str(response.url),
    ]


def _blocked_response(response: httpx.Response) -> bool:
    folded = response.text[:20000].casefold()
    return response.status_code in {401, 403} or any(
        marker in folded
        for marker in ("captcha", "access denied", "dang nhap", "đăng nhập")
    )


def _crawl4ai_source_page(url: str) -> dict[str, Any]:
    """Render an HTML source page through Crawl4AI for the sync gap worker."""

    rendered = asyncio.run(
        fetch_rendered(
            url,
            timeout_ms=20000,
            wait_until="networkidle",
            check_robots_txt=True,
        )
    )
    html = str(rendered.get("html") or "")
    return {
        "status": rendered.get("status"),
        "status_code": int(rendered.get("status_code") or 0),
        "url": str(rendered.get("final_url") or url),
        "html": html,
        "text": str(rendered.get("rendered_text") or html),
        "reason": str(rendered.get("reason") or ""),
    }


def _candidate_with_provenance(
    *,
    job: Mapping[str, Any],
    source_page: str,
    response: httpx.Response,
    candidate_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    final_url = str(response.url)
    redirect_chain = _redirect_chain(response)
    if any(not is_allowed_official_url(item) for item in redirect_chain):
        return None, "REDIRECT_HOST_NOT_ALLOWED"
    source_content = response.content
    if len(source_content) > MAX_DOWNLOAD_BYTES:
        return None, "FILE_TOO_LARGE"
    valid, reason, _format = validate_download(
        source_content,
        response.headers.get("content-type"),
        final_url,
    )
    if not valid:
        return None, reason
    scan_ok, scan_reason = _basic_malware_scan(source_content)
    if not scan_ok:
        return None, scan_reason

    source_digest = hashlib.sha256(source_content).hexdigest()
    content = source_content
    page_indexes = list(job.get("source_pages_zero_based") or [])
    if page_indexes:
        if job.get("gap_type") != "MISSING_FORM_SOURCE" or _format != "pdf":
            return None, "PDF_PAGE_EXTRACTION_NOT_APPLICABLE"
        try:
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(BytesIO(source_content))
            if any(int(index) >= len(reader.pages) for index in page_indexes):
                return None, "PDF_PAGE_INDEX_OUT_OF_RANGE"
            writer = PdfWriter()
            for index in page_indexes:
                writer.add_page(reader.pages[int(index)])
            output = BytesIO()
            writer.write(output)
            content = output.getvalue()
        except Exception:
            return None, "PDF_PAGE_EXTRACTION_FAILED"
        extracted_valid, extracted_reason, _ = validate_download(
            content,
            "application/pdf",
            final_url,
        )
        if not extracted_valid:
            return None, extracted_reason

    digest = hashlib.sha256(content).hexdigest()
    duplicate = any(
        path.is_file() and path.name.startswith(digest[:24])
        for path in candidate_dir.glob(f"{digest[:24]}.*")
    ) if candidate_dir.exists() else False
    candidate = candidate_from_download(
        requirement_id=str(job["job_id"]),
        procedure_id=str(job.get("procedure_id") or ""),
        canonical_name=str(job.get("expected_name") or "Candidate source"),
        source_page=source_page,
        download_url=final_url,
        content=content,
        content_type=response.headers.get("content-type"),
        download_dir=candidate_dir,
    )
    if candidate.get("status") != "AVAILABLE_OFFICIAL_FILE":
        return None, str(candidate.get("reason_code") or "DOWNLOAD_VALIDATION_FAILED")
    publisher = str(urlparse(final_url).hostname or "").casefold()
    candidate.update(
        {
            "source_type": (
                "form"
                if job.get("gap_type") == "MISSING_FORM_SOURCE"
                else "legal_document_candidate"
            ),
            "form_code": str(job.get("expected_code") or "") or None,
            "approved": False,
            "review_status": "candidate_pending_review",
            "legal_review_status": "pending",
            "provenance": {
                "source_page": source_page,
                "download_url": final_url,
                "redirect_chain": redirect_chain,
                "publisher": publisher,
                "downloaded_at": _utcnow(),
                "legal_as_of": job.get("legal_as_of"),
                "sha256": digest,
                "source_sha256": source_digest,
                "source_pages_zero_based": page_indexes,
                "content_type": response.headers.get("content-type"),
                "size_bytes": len(content),
                "source_size_bytes": len(source_content),
                "basic_scan": scan_reason,
            },
        }
    )
    return candidate, "DUPLICATE_CHECKSUM_REUSED" if duplicate else "DOWNLOADED_PENDING_REVIEW"


def process_source_gap_job(
    job: Mapping[str, Any],
    *,
    client: httpx.Client | None = None,
    candidate_dir: Path,
    checked_on: date | None = None,
) -> dict[str, Any]:
    """Run one bounded attempt against preselected official source pages."""

    current = dict(job)
    if current.get("status") in TERMINAL_STATUSES:
        return current
    checked_on = checked_on or date.today()
    current = _record_check(
        {**current, "status": "discovering", "reason_code": "DISCOVERING"},
        checked_on,
    )
    owns_client = client is None
    active_client = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": "ChatBotLegal-SourceGap/1.0 (+candidate-only)"},
        verify=build_verified_ssl_context(),
    )
    last_reason = "OFFICIAL_PAGE_HAS_NO_FILE_LINK"
    try:
        for source_page in current.get("source_pages") or []:
            if not is_allowed_official_url(source_page):
                return {
                    **current,
                    "status": "blocked_external",
                    "reason_code": "SOURCE_HOST_NOT_ALLOWED",
                }
            direct_seed = Path(urlparse(source_page).path).suffix.casefold() in {
                ".pdf", ".doc", ".docx", ".xls", ".xlsx"
            }
            if client is None and not direct_seed:
                try:
                    rendered_page = _crawl4ai_source_page(source_page)
                except Exception:
                    last_reason = "NETWORK_ERROR_RETRYABLE"
                    continue
                page_status = int(rendered_page.get("status_code") or 0)
                page_url = str(rendered_page.get("url") or source_page)
                page_html = str(rendered_page.get("html") or "")
                page_text = str(rendered_page.get("text") or page_html)
                folded = page_text[:20000].casefold()
                if page_status in {401, 403} or any(
                    marker in folded
                    for marker in ("captcha", "access denied", "dang nhap", "đăng nhập")
                ):
                    return {
                        **current,
                        "status": "blocked_external",
                        "reason_code": "CAPTCHA_OR_AUTH_REQUIRED",
                    }
                if page_status == 429 or page_status >= 500:
                    last_reason = f"HTTP_{page_status}_RETRYABLE"
                    continue
                if page_status >= 400 or rendered_page.get("status") != "ok":
                    last_reason = f"HTTP_{page_status}" if page_status else "NETWORK_ERROR_RETRYABLE"
                    continue
                if not page_html:
                    last_reason = "OFFICIAL_PAGE_HAS_NO_FILE_LINK"
                    continue
                links = _extract_links(page_html, page_url)
            else:
                try:
                    page = active_client.get(source_page)
                except httpx.HTTPError:
                    last_reason = "NETWORK_ERROR_RETRYABLE"
                    continue
                if _blocked_response(page):
                    return {
                        **current,
                        "status": "blocked_external",
                        "reason_code": "CAPTCHA_OR_AUTH_REQUIRED",
                    }
                if page.status_code == 429 or page.status_code >= 500:
                    last_reason = f"HTTP_{page.status_code}_RETRYABLE"
                    continue
                if page.status_code >= 400:
                    last_reason = f"HTTP_{page.status_code}"
                    continue
                direct_suffix = Path(urlparse(str(page.url)).path).suffix.casefold()
                direct_mime = str(page.headers.get("content-type") or "").casefold()
                direct_file_mime = any(
                    marker in direct_mime
                    for marker in (
                        "application/pdf",
                        "application/msword",
                        "application/vnd.ms-",
                        "application/vnd.openxmlformats-officedocument",
                    )
                )
                if direct_suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx"} or direct_file_mime:
                    candidate, reason = _candidate_with_provenance(
                        job=current,
                        source_page=source_page,
                        response=page,
                        candidate_dir=candidate_dir,
                    )
                    if candidate is not None:
                        return {
                            **current,
                            "status": "downloaded_candidate",
                            "reason_code": reason,
                            "candidate": candidate,
                        }
                    last_reason = reason
                    continue
                links = _extract_links(page.text, str(page.url))
            if not links:
                last_reason = "OFFICIAL_PAGE_HAS_NO_FILE_LINK"
                continue
            best_link = max(
                links,
                key=lambda link: _score_link(
                    str(current.get("expected_name") or ""),
                    str(current.get("expected_code") or "") or None,
                    link,
                ),
            )
            try:
                downloaded = active_client.get(best_link)
            except httpx.HTTPError:
                last_reason = "NETWORK_ERROR_RETRYABLE"
                continue
            if _blocked_response(downloaded):
                return {
                    **current,
                    "status": "blocked_external",
                    "reason_code": "CAPTCHA_OR_AUTH_REQUIRED",
                }
            if downloaded.status_code == 429 or downloaded.status_code >= 500:
                last_reason = f"HTTP_{downloaded.status_code}_RETRYABLE"
                continue
            if downloaded.status_code >= 400:
                last_reason = f"HTTP_{downloaded.status_code}"
                continue
            candidate, reason = _candidate_with_provenance(
                job=current,
                source_page=source_page,
                response=downloaded,
                candidate_dir=candidate_dir,
            )
            if candidate is None:
                last_reason = reason
                continue
            return {
                **current,
                "status": "downloaded_candidate",
                "reason_code": reason,
                "candidate": candidate,
            }
    finally:
        if owns_client:
            active_client.close()

    if (
        len(current.get("checked_dates") or []) >= MAX_DISTINCT_CHECK_DAYS
        and last_reason == "OFFICIAL_PAGE_HAS_NO_FILE_LINK"
    ):
        return {
            **current,
            "status": "verified_gap",
            "reason_code": "OFFICIAL_SOURCES_CHECKED_NO_PUBLIC_FILE",
        }
    if len(current.get("checked_dates") or []) >= MAX_DISTINCT_CHECK_DAYS:
        return {
            **current,
            "status": "blocked_external",
            "reason_code": last_reason,
        }
    return {
        **current,
        "status": "failed_retryable",
        "reason_code": last_reason,
    }


def reopen_for_weekly_monitor(
    job: Mapping[str, Any],
    *,
    checked_on: date,
) -> dict[str, Any]:
    """Reopen a verified gap for a later weekly official-source check."""

    if job.get("status") != "verified_gap":
        return dict(job)
    last_checked = max(str(item) for item in job.get("checked_dates") or [""])
    if last_checked and (checked_on - date.fromisoformat(last_checked)).days < 7:
        return dict(job)
    return {
        **dict(job),
        "status": "queued",
        "reason_code": "WEEKLY_RECHECK_QUEUED",
        "updated_at": _utcnow(),
    }


def load_source_gap_jobs(
    store_path: Path = DEFAULT_STORE_PATH,
) -> list[dict[str, Any]]:
    if not store_path.exists():
        return []
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "source-gap-store-v1":
        raise ValueError("unsupported_source_gap_store")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("invalid_source_gap_store")
    return [dict(item) for item in jobs if isinstance(item, Mapping)]


def _save_source_gap_jobs(
    jobs: Sequence[Mapping[str, Any]],
    *,
    store_path: Path = DEFAULT_STORE_PATH,
) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "source-gap-store-v1",
        "updated_at": _utcnow(),
        "jobs": [dict(item) for item in jobs],
    }
    temporary = store_path.with_suffix(f"{store_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, store_path)


def enqueue_source_gap_job(
    job: Mapping[str, Any] | None,
    *,
    store_path: Path = DEFAULT_STORE_PATH,
) -> dict[str, Any] | None:
    if job is None:
        return None
    jobs = load_source_gap_jobs(store_path)
    existing = next(
        (item for item in jobs if item.get("job_id") == job.get("job_id")),
        None,
    )
    if existing is not None:
        if dict(existing) == dict(job):
            return existing
        # A worker may enqueue the queued checkpoint before it finishes the
        # download. Preserve idempotency while allowing the newer checkpoint
        # to replace it. Never downgrade a terminal or candidate state to a
        # transient queued/discovering state.
        status_rank = {
            "queued": 10,
            "discovering": 20,
            "failed_retryable": 30,
            "downloaded_candidate": 40,
            "verified_gap": 40,
            "blocked_external": 40,
        }
        old_status = str(existing.get("status") or "")
        new_status = str(job.get("status") or "")
        if status_rank.get(new_status, 0) < status_rank.get(old_status, 0):
            return existing
        merged = {**dict(existing), **dict(job)}
        index = jobs.index(existing)
        jobs[index] = merged
        _save_source_gap_jobs(jobs, store_path=store_path)
        return merged
    jobs.append(dict(job))
    _save_source_gap_jobs(jobs, store_path=store_path)
    return dict(job)


def run_due_source_gap_jobs(
    *,
    store_path: Path = DEFAULT_STORE_PATH,
    candidate_dir: Path = DEFAULT_CANDIDATE_DIR,
    checked_on: date | None = None,
) -> dict[str, Any]:
    """Run at most one attempt per job/day inside the existing crawl scheduler."""

    checked_on = checked_on or date.today()
    jobs = load_source_gap_jobs(store_path)
    processed = 0
    updated: list[dict[str, Any]] = []
    for stored in jobs:
        job = reopen_for_weekly_monitor(stored, checked_on=checked_on)
        if job.get("status") in {
            "downloaded_candidate",
            "blocked_external",
            "admin_review_required",
        }:
            updated.append(job)
            continue
        if (
            job.get("status") == "verified_gap"
            or checked_on.isoformat() in (job.get("checked_dates") or [])
        ):
            updated.append(job)
            continue
        updated.append(
            process_source_gap_job(
                job,
                candidate_dir=candidate_dir,
                checked_on=checked_on,
            )
        )
        processed += 1
    if jobs:
        _save_source_gap_jobs(updated, store_path=store_path)
    counts: dict[str, int] = {}
    for item in updated:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "job_count": len(updated),
        "processed_count": processed,
        "status_counts": dict(sorted(counts.items())),
    }
