"""Prepare the four remaining priority form groups for legal review.

This module records proposed mappings only.  Every generated record is
non-canonical, non-runtime, and pending legal review.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES


class PriorityGapCandidateError(ValueError):
    pass


GROUPS: dict[str, dict[str, Any]] = {
    "land": {
        "id": "priority-gap-land-registration-change",
        "priority_group": "land_registration_change",
        "procedure_id": "sang_ten_so_do",
        "official_procedure_code": None,
        "canonical_form_name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất",
        "form_code": "11/ĐK",
        "requested_legacy_form_code": "09/ĐK",
        "legacy_form_status": "superseded_candidate_quarantined",
        "domain": "dat_dai_xay_dung",
        "legal_basis": ["101/2024/NĐ-CP"],
        "mapping_reason_code": "CURRENT_FORM_REPLACES_LEGACY_09_DK_PENDING_LEGAL_REVIEW",
    },
    "foreign_birth": {
        "id": "priority-gap-foreign-birth",
        "priority_group": "foreign_birth",
        "procedure_id": "dang_ky_khai_sinh_nuoc_ngoai",
        "official_procedure_code": "2.000528",
        "canonical_form_name": "Tờ khai đăng ký khai sinh",
        "form_code": None,
        "domain": "ho_tich_chung_thuc",
        "legal_basis": [
            "60/2014/QH13",
            "123/2015/NĐ-CP",
            "04/2020/TT-BTP",
            "1833/QĐ-BTP",
        ],
        "mapping_reason_code": "OFFICIAL_FOREIGN_BIRTH_PROCEDURE_MATCH_PENDING_LEGAL_REVIEW",
    },
    "foreign_marriage": {
        "id": "priority-gap-foreign-marriage",
        "priority_group": "foreign_marriage",
        "procedure_id": "dang_ky_ket_hon_nuoc_ngoai",
        "official_procedure_code": "2.000806",
        "canonical_form_name": "Tờ khai đăng ký kết hôn",
        "form_code": None,
        "domain": "ho_tich_chung_thuc",
        "legal_basis": [
            "52/2014/QH13",
            "60/2014/QH13",
            "123/2015/NĐ-CP",
            "04/2020/TT-BTP",
            "1833/QĐ-BTP",
        ],
        "mapping_reason_code": "OFFICIAL_FOREIGN_MARRIAGE_PROCEDURE_MATCH_PENDING_LEGAL_REVIEW",
    },
    "construction": {
        "id": "priority-gap-individual-house-building-permit",
        "priority_group": "construction",
        "procedure_id": "cap_giay_phep_xay_dung",
        "official_procedure_code": "1.009122",
        "canonical_form_name": "Mẫu số 01 - Đơn đề nghị cấp giấy phép xây dựng",
        "form_code": "01",
        "domain": "dat_dai_xay_dung",
        "legal_basis": [
            "50/2014/QH13",
            "62/2020/QH14",
            "175/2024/NĐ-CP",
            "32/VBHN-BXD",
        ],
        "mapping_reason_code": "OFFICIAL_BUILDING_FORM_MATCH_PENDING_LEGAL_REVIEW",
    },
}


def _official_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.casefold().rstrip(".")
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in OFFICIAL_HOST_SUFFIXES
    )


def _validate_asset(asset: Mapping[str, Any], project_root: Path) -> tuple[Path, str]:
    path = Path(asset.get("path") or "").resolve()
    root = project_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PriorityGapCandidateError("CANDIDATE_FILE_OUTSIDE_PROJECT") from exc
    if not path.is_file() or path.stat().st_size < 256:
        raise PriorityGapCandidateError("CANDIDATE_FILE_INVALID")
    if path.suffix.casefold() == ".pdf" and not path.read_bytes()[:8].startswith(b"%PDF"):
        raise PriorityGapCandidateError("CANDIDATE_FILE_INVALID")
    for key in ("source_page_url", "source_download_url"):
        if not _official_url(asset.get(key)):
            raise PriorityGapCandidateError(f"SOURCE_NOT_OFFICIAL:{key}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if str(asset.get("sha256") or "") != digest:
        raise PriorityGapCandidateError("CANDIDATE_CHECKSUM_MISMATCH")
    return path, digest


def build_priority_gap_candidates(
    assets: Mapping[str, Mapping[str, Any]],
    *,
    project_root: Path,
    prepared_at: str | None = None,
) -> list[dict[str, Any]]:
    if set(assets) != set(GROUPS):
        raise PriorityGapCandidateError("FOUR_PRIORITY_ASSETS_REQUIRED")
    timestamp = prepared_at or datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []
    for asset_key, definition in GROUPS.items():
        asset = assets[asset_key]
        path, digest = _validate_asset(asset, project_root)
        try:
            relative_path = str(path.relative_to(project_root.resolve())).replace("\\", "/")
        except ValueError:
            relative_path = str(path)
        records.append(
            {
                **definition,
                "detected_form_name": definition["canonical_form_name"],
                "form_title": definition["canonical_form_name"],
                "suggested_procedure_id": definition["procedure_id"],
                "suggested_domain": definition["domain"],
                "file_name": path.name,
                "file_path": relative_path,
                "local_path": relative_path,
                "sha256": digest,
                "source_sha256": digest,
                "size_bytes": path.stat().st_size,
                "source_url": str(asset["source_page_url"]),
                "page_url": str(asset["source_page_url"]),
                "source_page_url": str(asset["source_page_url"]),
                "source_download_url": str(asset["source_download_url"]),
                "publisher": urlparse(str(asset["source_download_url"])).hostname,
                "official_level": "official",
                "scope": "Hai Phong",
                "audience": "citizen",
                "effectivity_review_status": "pending",
                "effective_status": "pending_legal_review",
                "preparation_status": "ready_for_human_review",
                "catalog_status": "candidate_pending_review",
                "review_status": "candidate_pending_review",
                "legal_review_status": "candidate_pending_review",
                "reviewed_by": None,
                "reviewed_at": None,
                "is_approved": False,
                "is_canonical": False,
                "runtime_eligible": False,
                "has_official_file": True,
                "has_download": False,
                "download_url": None,
                "prepared_at": timestamp,
                "provenance": {
                    "kind": "official_source_download",
                    "source_page_url": str(asset["source_page_url"]),
                    "source_download_url": str(asset["source_download_url"]),
                    "source_package_sha256": str(asset.get("source_package_sha256") or ""),
                    "source_pages_zero_based": list(
                        asset.get("source_pages_zero_based") or []
                    ),
                    "prepared_at": timestamp,
                    "legal_as_of": "2026-07-27",
                },
            }
        )
    return records


def merge_priority_gap_candidates(
    payload: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    records = list(result.get("records") or [])
    by_id = {str(record.get("id")): index for index, record in enumerate(records)}
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or "")
        if not candidate_id:
            raise PriorityGapCandidateError("CANDIDATE_ID_MISSING")
        if candidate_id in by_id:
            current = records[by_id[candidate_id]]
            if (
                current.get("review_status") != "candidate_pending_review"
                or bool(current.get("is_approved"))
            ):
                raise PriorityGapCandidateError(
                    f"REVIEWED_CANDIDATE_IMMUTABLE:{candidate_id}"
                )
            records[by_id[candidate_id]] = dict(candidate)
        else:
            by_id[candidate_id] = len(records)
            records.append(dict(candidate))
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("review_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    result["records"] = records
    result["summary"] = {
        **dict(result.get("summary") or {}),
        "total": len(records),
        "total_records": len(records),
        "review_status_counts": counts,
        "priority_gap_ready_for_human_review": sum(
            record.get("preparation_status") == "ready_for_human_review"
            and str(record.get("id") or "").startswith("priority-gap-")
            for record in records
        ),
        "priority_gap_auto_approved": 0,
    }
    return result
