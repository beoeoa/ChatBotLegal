"""Durable audit record for replacing an expired legal document.

The management service owns crawl/parse/chunk/embed, retrieval verification
and the serving-state switch. This sidecar records that operation; it never
changes the core manifest or repeats the management service's smoke test.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.retrieval_release_contracts import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "release-data" / "legal" / "staging" / "legal-replacement-workflows-v1.json"
_LOCK = threading.RLock()


@contextmanager
def _workflow_lock():
    """Serialize the whole read/modify/write across threads and API workers."""
    if not _LOCK.acquire(timeout=5):
        raise TimeoutError("replacement_audit_busy")
    try:
        WORKFLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WORKFLOW_PATH.with_suffix(WORKFLOW_PATH.suffix + ".lock").open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("replacement_audit_busy") from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        _LOCK.release()


def _serialized(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _workflow_lock():
            return function(*args, **kwargs)
    return locked


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> list[dict[str, Any]]:
    try:
        payload = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RuntimeError("replacement_audit_unreadable") from exc
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise RuntimeError("replacement_audit_invalid")
    return payload


def _write(items: list[dict[str, Any]]) -> None:
    WORKFLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = WORKFLOW_PATH.with_name(f".{WORKFLOW_PATH.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(items, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(WORKFLOW_PATH)
    finally:
        temporary.unlink(missing_ok=True)


@_serialized
def create_replacement_workflow(
    *,
    old_document_id: str,
    source_url: str,
    preview: dict[str, Any],
    reason: str,
    requested_by: str,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    release_id: str | None = None,
    manifest_sha256: str | None = None,
) -> dict[str, Any]:
    items = _read()
    if idempotency_key:
        for item in items:
            if item.get("idempotency_key") == idempotency_key:
                existing_fingerprint = str(item.get("request_fingerprint") or "")
                if (
                    request_fingerprint
                    and existing_fingerprint
                    and existing_fingerprint != request_fingerprint
                ):
                    raise ValueError("replacement_idempotency_conflict")
                return {**item, "idempotent_replay": True}
    created_at = _now()
    workflow_id = f"replacement-{uuid4().hex}"
    identity_verified = bool(preview.get("title") or preview.get("normalized"))
    source_fetch_failed = bool(preview.get("fetch_error"))
    item = {
        "workflow_id": workflow_id,
        "workflow_type": "legal_document_replacement",
        "status": "source_review_required" if source_fetch_failed else "staging_quarantine",
        "publish_mode": "after_index_and_retrieval_verified",
        "old_document_id": str(old_document_id),
        "source_url": source_url,
        "preview": preview,
        "reason": reason,
        "requested_by": requested_by,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
        "release_id": release_id,
        "manifest_sha256": manifest_sha256,
        "created_at": created_at,
        "updated_at": created_at,
        "steps": {
            "official_url_identity": "blocked_source_fetch" if source_fetch_failed else "passed",
            "official_relation": "pending_admin_review",
            "crawl": "not_started",
            "normalize": "not_started",
            "chunk": "not_started",
            "embedding": "not_started",
            "exact_lexical_rebuild": "not_started",
            "manifest_verify": "not_started",
            "retrieval_smoke": "not_started",
            "publish": "pending_import_activation",
        },
        "hard_gates": {
            "pointer_unchanged": True,
            "active_release_unchanged": True,
            "official_identity_verified": identity_verified,
            "replacement_relation_recorded": False,
            "import_active": False,
            "chunks_indexed": False,
            "vector_collection_available": False,
        },
        "result": None,
        "error": None,
        "workflow_fingerprint": canonical_sha256({
            "workflow_id": workflow_id,
            "old_document_id": str(old_document_id),
            "source_url": source_url,
            "preview": preview,
            "reason": reason,
            "release_id": release_id,
        }),
    }
    items.append(item)
    _write(items)
    return item


@_serialized
def get_replacement_workflow(workflow_id: str) -> dict[str, Any] | None:
    return next((item for item in reversed(_read()) if item.get("workflow_id") == workflow_id), None)


@_serialized
def get_replacement_workflow_by_idempotency_key(
    idempotency_key: str,
) -> dict[str, Any] | None:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    return next(
        (item for item in reversed(_read()) if item.get("idempotency_key") == key),
        None,
    )


@_serialized
def find_retryable_replacement_workflow(
    *,
    old_document_id: str,
    source_url: str,
    source_content_hash: str | None = None,
    uploaded_content_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Find the same recoverable operation without using the audit reason.

    An Admin may naturally edit the explanation before pressing retry.  The
    reason is audit metadata, not document identity, and must not create a
    second imported row.  URL retries additionally bind to the freshly crawled
    content hash; upload retries bind to the exact extracted-content hash.
    Legacy URL workflows already persisted ``preview.content_hash`` and can be
    resumed safely.  Legacy upload workflows without a content hash remain
    fail-closed and require their original idempotency key.
    """

    canonical_old = str(old_document_id or "").split(":")[-1].strip()
    normalized_url = str(source_url or "").strip().rstrip("/")
    normalized_source_hash = str(source_content_hash or "").strip()
    normalized_upload_hash = str(uploaded_content_sha256 or "").strip()
    if not canonical_old or (not normalized_url and not normalized_upload_hash):
        return None

    for item in reversed(_read()):
        if item.get("status") != "pending_retry":
            continue
        if (item.get("steps") or {}).get("activation_journal") != "prepared":
            continue
        item_old = str(item.get("old_document_id") or "").split(":")[-1].strip()
        if item_old != canonical_old:
            continue
        preview = item.get("preview") if isinstance(item.get("preview"), dict) else {}
        item_url = str(item.get("source_url") or "").strip().rstrip("/")
        if normalized_url:
            if item_url != normalized_url:
                continue
            item_source_hash = str(preview.get("content_hash") or "").strip()
            if normalized_source_hash and item_source_hash != normalized_source_hash:
                continue
            return dict(item)
        item_upload_hash = str(preview.get("uploaded_content_sha256") or "").strip()
        if item_upload_hash and item_upload_hash == normalized_upload_hash:
            return dict(item)
    return None


@_serialized
def list_replacement_workflows_for_document(
    document_id: str,
) -> list[dict[str, Any]]:
    """Return durable replacement records that reference either side of a document.

    This is a read-only projection over the existing workflow audit file.  It
    deliberately does not depend on the optional immutable-version schema, so
    operators can still inspect a completed replacement from the management UI.
    """

    canonical_id = str(document_id or "").split(":")[-1].strip()
    if not canonical_id:
        return []

    matches: list[dict[str, Any]] = []
    for item in _read():
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        old_id = str(item.get("old_document_id") or "").split(":")[-1].strip()
        new_id = str(
            result.get("document_id")
            or (result.get("new_document") or {}).get("document_id")
            or ""
        ).split(":")[-1].strip()
        if canonical_id in {old_id, new_id}:
            matches.append(item)
    return matches


@_serialized
def update_replacement_workflow(
    workflow_id: str,
    *,
    status: str | None = None,
    steps: dict[str, str] | None = None,
    hard_gates: dict[str, bool] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    items = _read()
    for item in reversed(items):
        if item.get("workflow_id") != workflow_id:
            continue
        if status is not None:
            item["status"] = status
        if steps:
            item["steps"] = {**(item.get("steps") or {}), **steps}
        if hard_gates:
            item["hard_gates"] = {**(item.get("hard_gates") or {}), **hard_gates}
        if result is not None:
            item["result"] = result
        if error is not None:
            item["error"] = error
        item["updated_at"] = _now()
        _write(items)
        return item
    return None


__all__ = [
    "WORKFLOW_PATH",
    "create_replacement_workflow",
    "find_retryable_replacement_workflow",
    "get_replacement_workflow",
    "get_replacement_workflow_by_idempotency_key",
    "list_replacement_workflows_for_document",
    "update_replacement_workflow",
]
