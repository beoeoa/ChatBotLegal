"""Bounded asynchronous extraction jobs for chat document assets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.chat_upload_store import stored_upload, update_upload_metadata
from api.data_paths import notebook_data_dir
from api.document_contracts import ExtractionArtifact, ExtractionArtifactStore
from api.multimodal_preprocess import EXTRACTION_PIPELINE_VERSION, extract_artifact_from_file

MAX_ATTEMPTS = 2
_tasks: set[asyncio.Task[Any]] = set()
_active_jobs: set[str] = set()
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(owner: str) -> Path:
    owner_key = hashlib.sha256(owner.encode("utf-8")).hexdigest()
    return notebook_data_dir() / "chat_extraction_jobs" / owner_key


def _path(owner: str, job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("invalid_extraction_job_id")
    return _root(owner) / f"{job_id}.json"


def _write(owner: str, payload: dict[str, Any]) -> None:
    root = _root(owner)
    root.mkdir(parents=True, exist_ok=True)
    target = _path(owner, str(payload["job_id"]))
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def get_job(owner: str, job_id: str) -> dict[str, Any]:
    try:
        return json.loads(_path(owner, job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise KeyError(job_id) from exc


async def _run(owner: str, payload: dict[str, Any]) -> None:
    started = time.perf_counter()
    job_id = str(payload["job_id"])
    identity = str(payload["file_id"])
    payload.update(status="processing", started_at=_now(), error=None)
    payload["attempt_count"] = int(payload.get("attempt_count") or 0) + 1
    _write(owner, payload)
    update_upload_metadata(owner, identity, {
        "extraction_job_id": job_id,
        "extraction_status": "processing",
    })
    try:
        target, metadata = stored_upload(owner, identity)
        content = await asyncio.to_thread(target.read_bytes)
        result, source_type = await extract_artifact_from_file(
            content,
            str(metadata.get("name") or "document"),
            str(metadata.get("mime") or "application/octet-stream"),
        )
        artifact = ExtractionArtifact.from_result(str(metadata["sha256"]), result)
        await asyncio.to_thread(ExtractionArtifactStore().put, artifact)
        payload.update(
            status=artifact.status,
            completed_at=_now(),
            source_type=source_type,
            char_count=len(artifact.text),
            extractor=artifact.extractor,
            extractor_version=artifact.version,
            warnings=list(artifact.warnings),
            error=None,
        )
        update_upload_metadata(owner, identity, {
            "extraction_status": artifact.status,
            "extractor": artifact.extractor,
            "extractor_version": artifact.version,
            "warnings": list(artifact.warnings),
            "char_count": len(artifact.text),
        })
    except Exception as exc:
        payload.update(status="error", completed_at=_now(), error=str(exc)[:500])
        update_upload_metadata(owner, identity, {"extraction_status": "error"})
    finally:
        _write(owner, payload)
        logger.info(
            "chat_extraction_completed job_id=%s status=%s attempt=%s extraction_ms=%.1f chars=%s",
            job_id,
            payload.get("status"),
            payload.get("attempt_count"),
            (time.perf_counter() - started) * 1000,
            payload.get("char_count", 0),
        )


def submit_job(
    owner: str,
    *,
    file_id: str,
    sha256: str,
    source_type: str,
    force_retry: bool = False,
) -> dict[str, Any]:
    # The extraction contract is versioned. Reusing a terminal job from an
    # older engine/version would bypass artifact validation and return stale
    # OCR text even though the SHA-bound artifact was correctly rejected.
    job_id = hashlib.sha256(
        f"{owner}:{sha256}:{EXTRACTION_PIPELINE_VERSION}".encode("utf-8")
    ).hexdigest()[:32]
    try:
        payload = get_job(owner, job_id)
    except KeyError:
        payload = {
            "job_id": job_id,
            "file_id": file_id,
            "sha256": sha256,
            "source_type": source_type,
            "pipeline_version": EXTRACTION_PIPELINE_VERSION,
            "status": "processing",
            "attempt_count": 0,
            "max_attempts": MAX_ATTEMPTS,
            "created_at": _now(),
        }
    status = str(payload.get("status") or "error")
    if status in {"complete", "partial"} and not force_retry:
        return payload
    if status == "processing" and job_id in _active_jobs:
        return payload
    # A process restart can leave a persisted job in ``processing`` after its
    # task was interrupted.  The explicit Retry action is the recovery gate;
    # it starts a fresh bounded retry budget instead of treating the orphaned
    # state as a live worker forever.
    if force_retry and status in {"processing", "error"}:
        payload.update(status="processing", attempt_count=0, error=None, completed_at=None)
    if int(payload.get("attempt_count") or 0) >= MAX_ATTEMPTS:
        if status == "processing":
            payload.update(
                status="error",
                completed_at=_now(),
                error="extraction_worker_interrupted_after_retry_limit",
            )
            _write(owner, payload)
        return payload
    # Persist the initial state before returning so an immediate status poll can
    # never observe a missing job. A persisted processing job is resumed after a
    # process restart when it is not present in the in-memory active set.
    _write(owner, payload)
    _active_jobs.add(job_id)
    task = asyncio.create_task(_run(owner, payload), name=f"extract:{job_id}")
    _tasks.add(task)

    def _done(completed: asyncio.Task[Any]) -> None:
        _tasks.discard(completed)
        _active_jobs.discard(job_id)

    task.add_done_callback(_done)
    return payload


__all__ = ["get_job", "submit_job", "MAX_ATTEMPTS"]
