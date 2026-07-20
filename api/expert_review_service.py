"""Atomic, machine-validated storage for the 334 legal expert reviews."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPERT_REVIEW_PATH = ROOT / "notebook_data" / "legal-golden-expert-review.json"
ALLOWED_STATUSES = {"pending", "approved", "expert_disputed", "needs_revalidation"}
EDITABLE_FIELDS = {
    "expected_documents", "expected_articles", "forbidden_documents",
    "expected_authority", "forbidden_authority", "mandatory_documents",
    "conditional_documents", "processing_time", "fee", "penalty_range",
    "remedial_measures", "official_form_ids", "expected_conclusion",
    "allowed_conditional_conclusions", "critical_errors", "missing_facts_to_ask",
    "expert_review_status", "expert_score", "expert_name", "review_version",
    "second_expert_name",
}
REQUIRED_APPROVAL_FIELDS = {
    "expected_documents", "expected_articles", "expected_authority",
    "mandatory_documents", "conditional_documents", "processing_time", "fee",
    "official_form_ids", "expected_conclusion",
}
_WRITE_LOCK = threading.Lock()


class ExpertReviewError(ValueError):
    """Raised when a review update would violate the release contract."""


def load_reviews(path: Path = EXPERT_REVIEW_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpertReviewError("Không thể đọc bộ duyệt chuyên gia 334 ca.") from exc


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _validate_approval(record: dict[str, Any], previous_status: str) -> None:
    missing = [field for field in sorted(REQUIRED_APPROVAL_FIELDS) if record.get(field) is None]
    score = record.get("expert_score")
    if missing:
        raise ExpertReviewError(f"Chưa đủ trường bắt buộc để duyệt: {', '.join(missing)}.")
    if not str(record.get("expert_name") or "").strip():
        raise ExpertReviewError("Phải ghi tên chuyên gia duyệt.")
    if not isinstance(score, (int, float)) or not 0 <= float(score) <= 10:
        raise ExpertReviewError("Điểm chuyên gia phải nằm trong khoảng 0-10.")
    if previous_status == "expert_disputed":
        second = str(record.get("second_expert_name") or "").strip()
        first = str(record.get("expert_name") or "").strip()
        if not second or second.casefold() == first.casefold():
            raise ExpertReviewError("Ca tranh chấp phải có chuyên gia thứ hai độc lập xác nhận.")


def update_review(review_id: str, changes: dict[str, Any], *, path: Path = EXPERT_REVIEW_PATH) -> dict[str, Any]:
    unknown = sorted(set(changes) - EDITABLE_FIELDS)
    if unknown:
        raise ExpertReviewError(f"Không được sửa các trường: {', '.join(unknown)}.")
    with _WRITE_LOCK:
        payload = load_reviews(path)
        records = payload.get("records") or []
        record = next((item for item in records if item.get("review_id") == review_id), None)
        if not record:
            raise KeyError(review_id)
        previous_status = str(record.get("expert_review_status") or "pending")
        candidate = {**record, **changes}
        status = str(candidate.get("expert_review_status") or "pending")
        if status not in ALLOWED_STATUSES:
            raise ExpertReviewError("Trạng thái duyệt chuyên gia không hợp lệ.")
        if status == "approved":
            _validate_approval(candidate, previous_status)
            candidate["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        else:
            candidate["reviewed_at"] = None
        record.clear()
        record.update(candidate)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, payload)
        return dict(record)


def invalidate_reviews_for_laws(
    law_numbers: set[str],
    *,
    path: Path = EXPERT_REVIEW_PATH,
) -> int:
    """Mark approved answers stale after a referenced legal document changes."""
    normalized = {value.strip().casefold() for value in law_numbers if value.strip()}
    if not normalized or not path.exists():
        return 0
    with _WRITE_LOCK:
        payload = load_reviews(path)
        changed = 0
        for record in payload.get("records") or []:
            if record.get("expert_review_status") != "approved":
                continue
            documents = " ".join(str(value) for value in record.get("expected_documents") or []).casefold()
            matched = sorted(value for value in normalized if value in documents)
            if not matched:
                continue
            record["expert_review_status"] = "needs_revalidation"
            record["reviewed_at"] = None
            record["revalidation_reason"] = "referenced_document_changed"
            record["changed_law_numbers"] = matched
            changed += 1
        if changed:
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(path, payload)
        return changed
