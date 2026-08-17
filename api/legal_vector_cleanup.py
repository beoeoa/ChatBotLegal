"""Fail-closed manifests and exact-ID vector cleanup for blocked documents."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from api.legal_validity_models import normalize_law_number

MANIFEST_SCHEMA = "legal-vector-cleanup-v1"
_JOB_ID = re.compile(r"^[a-f0-9]{64}$")
_VECTOR_ID = re.compile(r"^chunk-[1-9][0-9]*$")
_VERIFIED_FULL_STATUSES = {"expired", "repealed", "replaced", "suspended"}
_TRANSITIONS = {
    "blocking_applied": {"vector_cleanup_running", "vector_cleanup_failed"},
    "vector_cleanup_running": {
        "vector_cleanup_completed",
        "vector_cleanup_partial",
        "vector_cleanup_failed",
    },
    "vector_cleanup_partial": {"vector_cleanup_running"},
    "vector_cleanup_failed": {"vector_cleanup_running"},
    "vector_cleanup_completed": {"vector_cleanup_completed"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _document_token(value: Any) -> str:
    text = str(value or "").strip()
    return text.rsplit(":", 1)[-1]


def _snapshot_entry(
    snapshot: Mapping[str, Any] | None,
    *,
    document_id: str,
    law_number: str,
) -> tuple[str, Mapping[str, Any]] | None:
    if not isinstance(snapshot, Mapping):
        return None
    documents = snapshot.get("documents")
    if not isinstance(documents, Mapping):
        return None
    normalized_number = normalize_law_number(law_number)
    document_ids = snapshot.get("document_ids")
    mapped_number = None
    if isinstance(document_ids, Mapping):
        for stored_id, stored_number in document_ids.items():
            if _document_token(stored_id) == _document_token(document_id):
                mapped_number = normalize_law_number(stored_number)
                break
    lookup_number = mapped_number or normalized_number
    entry = documents.get(lookup_number) if lookup_number else None
    if not isinstance(entry, Mapping):
        return None
    entry_document_id = entry.get("document_id")
    if entry_document_id and _document_token(entry_document_id) != _document_token(document_id):
        return None
    return str(lookup_number), entry


def _verified_full_block(entry: Mapping[str, Any]) -> bool:
    return (
        str(entry.get("serving_action") or "") in {"historical_only", "block_document"}
        and str(entry.get("normalized_status") or "") in _VERIFIED_FULL_STATUSES
        and str(entry.get("identity_status") or "") == "exact"
        and str(entry.get("evidence_status") or "") == "sufficient"
        and bool(str(entry.get("fingerprint") or "").strip())
    )


def build_vector_cleanup_manifest(
    *,
    document_id: str,
    law_number: str,
    chunk_ids: Sequence[int],
    snapshot: Mapping[str, Any] | None,
    requested_by: str,
    reason: str,
) -> dict[str, Any]:
    """Build a stable manifest only after a verified full-document block."""

    matched = _snapshot_entry(
        snapshot, document_id=document_id, law_number=law_number
    )
    if matched is None or not _verified_full_block(matched[1]):
        raise ValueError("verified_full_document_block_required")
    matched_number, entry = matched
    actor = str(requested_by or "").strip()
    explanation = str(reason or "").strip()
    if not actor:
        raise ValueError("cleanup_actor_required")
    if len(explanation) < 10:
        raise ValueError("cleanup_reason_required")

    normalized_chunks = sorted({int(value) for value in chunk_ids if int(value) > 0})
    vector_ids = [f"chunk-{value}" for value in normalized_chunks]
    identity = json.dumps(
        {
            "document_id": _document_token(document_id),
            "fingerprint": str(entry.get("fingerprint")),
            "vector_ids": vector_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    job_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    now = _utc_now()
    return {
        "schema_version": MANIFEST_SCHEMA,
        "job_id": job_id,
        "state": "blocking_applied",
        "blocking_applied": True,
        "document_id": _document_token(document_id),
        "law_number": matched_number,
        "normalized_status": str(entry.get("normalized_status")),
        "source_fingerprint": str(entry.get("fingerprint")),
        "legal_source_url": str(entry.get("source_url") or ""),
        "vector_ids": vector_ids,
        "expected_vector_count": len(vector_ids),
        "requested_by": actor,
        "reason": explanation,
        "created_at": now,
        "updated_at": now,
        "collections": {},
    }


def _existing_ids(collection: Any, vector_ids: Sequence[str]) -> set[str]:
    payload = collection.get(ids=list(vector_ids), include=["metadatas"])
    raw_ids = payload.get("ids", []) if isinstance(payload, Mapping) else []
    if raw_ids and isinstance(raw_ids[0], list):
        raw_ids = [item for group in raw_ids for item in group]
    return {str(item) for item in raw_ids if str(item) in vector_ids}


def execute_exact_vector_cleanup(
    collections: Mapping[str, Any], vector_ids: Sequence[str]
) -> dict[str, Any]:
    """Delete only manifest IDs and verify absence in every named collection."""

    exact_ids = sorted({str(value) for value in vector_ids})
    if any(not _VECTOR_ID.fullmatch(value) for value in exact_ids):
        raise ValueError("invalid_vector_id")
    if not exact_ids:
        return {
            "state": "vector_cleanup_completed",
            "already_absent": True,
            "collections": {
                str(name): {"before": 0, "after": 0, "deleted": 0}
                for name in collections
            },
        }
    reports: MutableMapping[str, dict[str, Any]] = {}
    had_error = False
    total_before = 0
    for name, collection in collections.items():
        report: dict[str, Any] = {"before": 0, "after": 0, "deleted": 0}
        try:
            before = _existing_ids(collection, exact_ids)
            report["before"] = len(before)
            total_before += len(before)
            if before:
                collection.delete(ids=sorted(before))
            after = _existing_ids(collection, exact_ids)
            report["after"] = len(after)
            report["deleted"] = len(before - after)
            if after:
                report["error_code"] = "vector_ids_still_present"
                had_error = True
        except Exception:
            report["error_code"] = "vector_delete_failed"
            had_error = True
        reports[str(name)] = report
    if not collections:
        had_error = True
    return {
        "state": "vector_cleanup_partial" if had_error else "vector_cleanup_completed",
        "already_absent": total_before == 0 and not had_error,
        "collections": dict(reports),
    }


class VectorCleanupManifestStore:
    """Atomic JSON job store; no imported legal record is changed or removed."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(str(job_id or "")):
            raise ValueError("invalid_vector_cleanup_job_id")
        return self.directory / f"{job_id}.json"

    def load(self, job_id: str) -> dict[str, Any]:
        path = self._path(job_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA:
            raise ValueError("invalid_vector_cleanup_manifest")
        return payload

    def save(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(manifest)
        if payload.get("schema_version") != MANIFEST_SCHEMA:
            raise ValueError("invalid_vector_cleanup_manifest")
        path = self._path(str(payload.get("job_id") or ""))
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
        return payload

    def transition(
        self,
        job_id: str,
        state: str,
        *,
        collections: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.load(job_id)
        current = str(payload.get("state") or "")
        if state not in _TRANSITIONS.get(current, set()):
            raise ValueError("invalid_vector_cleanup_transition")
        payload["state"] = state
        payload["updated_at"] = _utc_now()
        if collections is not None:
            payload["collections"] = dict(collections)
        return self.save(payload)


__all__ = [
    "MANIFEST_SCHEMA",
    "VectorCleanupManifestStore",
    "build_vector_cleanup_manifest",
    "execute_exact_vector_cleanup",
]
