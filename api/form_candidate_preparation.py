"""Fail-closed preparation of official form candidates for human review.

This module only attaches verified files and provenance to records that are
already pending.  It deliberately cannot approve, promote, or expose a form to
runtime retrieval.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


OFFICIAL_SOURCE_HOSTS = {
    "bocongan.gov.vn",
    "cdn.haiphong.gov.vn",
    "dichvucong.bocongan.gov.vn",
    "dichvucong.gov.vn",
    "haiphong.gov.vn",
    "sotp.haiphong.gov.vn",
    "thanhtra.haiphong.gov.vn",
    "vanban.bocongan.gov.vn",
}
SUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx"}
MIN_FORM_BYTES = 1024


class CandidatePreparationError(ValueError):
    """Raised when a candidate cannot safely be prepared for review."""


def _official_https_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in OFFICIAL_SOURCE_HOSTS)


def _resolve_candidate_file(project_root: Path, relative_path: str) -> Path:
    normalized = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not normalized:
        raise CandidatePreparationError("CANDIDATE_FILE_MISSING: file_path is empty")

    root = project_root.resolve()
    candidate_root = (root / "data" / "uploads" / "forms" / "official_candidates").resolve()
    resolved = (root / normalized).resolve()
    try:
        resolved.relative_to(candidate_root)
    except ValueError as exc:
        raise CandidatePreparationError(
            f"CANDIDATE_FILE_OUTSIDE_REVIEW_AREA: {normalized}"
        ) from exc
    if not resolved.is_file():
        raise CandidatePreparationError(f"CANDIDATE_FILE_MISSING: {normalized}")
    return resolved


def _validate_file_integrity(path: Path) -> None:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise CandidatePreparationError(f"UNSUPPORTED_FORM_FILE: {path.name}")
    size = path.stat().st_size
    if size < MIN_FORM_BYTES:
        raise CandidatePreparationError(f"CANDIDATE_FILE_TOO_SMALL: {path.name}")

    header = path.read_bytes()[:8]
    if path.suffix.lower() == ".pdf" and not header.startswith(b"%PDF-"):
        raise CandidatePreparationError(f"INVALID_PDF_HEADER: {path.name}")
    if path.suffix.lower() == ".doc" and header != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise CandidatePreparationError(f"INVALID_DOC_HEADER: {path.name}")
    if path.suffix.lower() == ".docx" and not header.startswith(b"PK"):
        raise CandidatePreparationError(f"INVALID_DOCX_HEADER: {path.name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_spec(
    spec: Mapping[str, Any],
    *,
    project_root: Path,
    allowed_procedure_ids: set[str],
) -> tuple[Path, str]:
    candidate_id = str(spec.get("candidate_id") or "").strip()
    if not candidate_id:
        raise CandidatePreparationError("CANDIDATE_ID_MISSING")

    procedure_id = str(spec.get("procedure_id") or "").strip()
    if not procedure_id or procedure_id not in allowed_procedure_ids:
        raise CandidatePreparationError(
            f"UNKNOWN_PROCEDURE_ID: {procedure_id or '<empty>'}"
        )
    if not str(spec.get("official_procedure_code") or "").strip():
        raise CandidatePreparationError(f"OFFICIAL_PROCEDURE_CODE_MISSING: {candidate_id}")
    if not str(spec.get("canonical_form_name") or "").strip():
        raise CandidatePreparationError(f"CANONICAL_FORM_NAME_MISSING: {candidate_id}")
    if not str(spec.get("domain") or "").strip():
        raise CandidatePreparationError(f"DOMAIN_MISSING: {candidate_id}")

    for field in ("source_page_url", "source_download_url"):
        if not _official_https_url(spec.get(field)):
            raise CandidatePreparationError(
                f"SOURCE_NOT_OFFICIAL: {field}={spec.get(field)!r}"
            )

    legal_basis = spec.get("legal_basis")
    if not isinstance(legal_basis, list) or not any(str(item).strip() for item in legal_basis):
        raise CandidatePreparationError(f"LEGAL_BASIS_MISSING: {candidate_id}")

    path = _resolve_candidate_file(project_root, str(spec.get("file_path") or ""))
    _validate_file_integrity(path)
    return path, _sha256(path)


def prepare_candidate_payload(
    payload: Mapping[str, Any],
    preparations: Iterable[Mapping[str, Any]],
    *,
    project_root: Path,
    allowed_procedure_ids: set[str],
    prepared_at: str | None = None,
) -> dict[str, Any]:
    """Attach official files and provenance while preserving the review gate."""

    result = copy.deepcopy(dict(payload))
    records = list(result.get("records") or [])
    by_id = {str(record.get("id")): index for index, record in enumerate(records)}
    specs = list(preparations)
    spec_ids = [str(spec.get("candidate_id") or "") for spec in specs]
    if len(spec_ids) != len(set(spec_ids)):
        raise CandidatePreparationError("DUPLICATE_CANDIDATE_ASSIGNMENT")

    timestamp = prepared_at or datetime.now(timezone.utc).isoformat()
    prepared_count = 0
    for spec in specs:
        candidate_id = str(spec.get("candidate_id") or "").strip()
        if candidate_id not in by_id:
            raise CandidatePreparationError(f"CANDIDATE_NOT_FOUND: {candidate_id}")

        index = by_id[candidate_id]
        original = dict(records[index])
        if original.get("review_status") != "candidate_pending_review" or bool(
            original.get("is_approved")
        ):
            raise CandidatePreparationError(
                f"CANDIDATE_ALREADY_REVIEWED: {candidate_id}"
            )

        file_path, checksum = _validate_spec(
            spec,
            project_root=project_root,
            allowed_procedure_ids=allowed_procedure_ids,
        )
        relative_path = str(file_path.relative_to(project_root.resolve())).replace("\\", "/")
        prepared = {
            **original,
            "detected_form_name": str(spec["canonical_form_name"]).strip(),
            "form_title": str(spec["canonical_form_name"]).strip(),
            "suggested_procedure_id": str(spec["procedure_id"]).strip(),
            "procedure_id": str(spec["procedure_id"]).strip(),
            "official_procedure_code": str(spec["official_procedure_code"]).strip(),
            "suggested_domain": str(spec["domain"]).strip(),
            "domain": str(spec["domain"]).strip(),
            "file_name": file_path.name,
            "file_path": relative_path,
            "sha256": checksum,
            "source_sha256": checksum,
            "size_bytes": file_path.stat().st_size,
            "source_url": str(spec["source_page_url"]).strip(),
            "page_url": str(spec["source_page_url"]).strip(),
            "source_page_url": str(spec["source_page_url"]).strip(),
            "source_download_url": str(spec["source_download_url"]).strip(),
            "publisher": str(spec.get("publisher") or "").strip(),
            "official_level": "official",
            "legal_basis": list(spec["legal_basis"]),
            "effective_status": str(spec.get("effective_status") or "").strip(),
            "provenance": {
                "kind": "official_source_download",
                "source_page_url": str(spec["source_page_url"]).strip(),
                "source_download_url": str(spec["source_download_url"]).strip(),
                "source_sha256": str(spec.get("source_package_sha256") or "").strip() or None,
                "source_pages_zero_based": list(spec.get("source_pages_zero_based") or []),
                "prepared_at": timestamp,
            },
            "preparation_status": "ready_for_human_review",
            "prepared_at": timestamp,
            "catalog_status": "candidate_pending_review",
            "legal_review_status": "candidate_pending_review",
            "review_status": "candidate_pending_review",
            "is_approved": False,
            "is_canonical": False,
            "has_official_file": True,
            "has_download": False,
            "download_url": None,
        }
        records[index] = prepared
        prepared_count += 1

    status_counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("review_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    result["records"] = records
    result["summary"] = {
        **dict(result.get("summary") or {}),
        "review_status_counts": status_counts,
        "ready_for_human_review": sum(
            1 for record in records if record.get("preparation_status") == "ready_for_human_review"
        ),
        "prepared_in_run": prepared_count,
        "auto_approved": 0,
    }
    return result

