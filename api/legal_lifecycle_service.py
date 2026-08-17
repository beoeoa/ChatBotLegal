"""Fail-closed Phase 2 draft/version workflow for legal documents.

The normal runtime keeps writes and activation disabled. Drafts are operational
state and are never read by Citizen/Officer retrieval paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlparse

IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
DRAFT_ID_RE = re.compile(r"^legal_document_draft:[A-Za-z0-9_-]{1,160}$")
EDITABLE_STATES = {"draft", "duplicate_review", "changes_requested"}
REVIEW_DECISIONS = {"approved", "rejected", "changes_requested"}
OFFICIAL_HOSTS = {"vbpl.vn", "vanban.chinhphu.vn", "congbao.chinhphu.vn"}
MAX_CONTENT_CHARS = 2_000_000
MUTATION_RESPONSE_FIELDS = {
    "id", "logical_document_id", "state", "title", "law_number",
    "document_type", "issuing_agency", "scope", "sector", "source_url",
    "revision", "validation", "created_by", "submitted_by", "reviewed_by",
    "created", "updated", "version", "manifest", "activation_state",
}


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _actor_set(name: str) -> set[str]:
    return {item.strip() for item in str(os.getenv(name) or "").split(",") if item.strip()}


def lifecycle_capabilities(actor: str | None, role: str | None) -> dict[str, Any]:
    is_admin = str(role or "").strip().lower() == "admin" and bool(actor)
    actor_id = str(actor or "").strip()
    return {
        "actor_id": actor_id if is_admin else None,
        "authenticated_admin": is_admin,
        "writes_enabled": is_admin and _enabled("LEGAL_LIFECYCLE_WRITES_ENABLED"),
        "editor": is_admin and actor_id in _actor_set("LEGAL_LIFECYCLE_EDITORS"),
        "reviewer": is_admin and actor_id in _actor_set("LEGAL_LIFECYCLE_REVIEWERS"),
        "activation_enabled": is_admin and _enabled("LEGAL_LIFECYCLE_ACTIVATION_ENABLED"),
    }


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_text(value: Any) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")).strip()


class LifecycleBucket(str, Enum):
    ACTIVE = "active"
    FUTURE = "future"
    EXPIRING_90 = "expiring_90"
    EXPIRING_30 = "expiring_30"
    EXPIRING_7 = "expiring_7"
    EXPIRING_1 = "expiring_1"
    EXPIRED = "expired"
    PARTIALLY_EXPIRED = "partially_expired"
    REPLACED = "replaced"
    REPEALED = "repealed"
    SUSPENDED = "suspended"
    CORRECTED = "corrected"
    CONSOLIDATED = "consolidated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LegalProvisionEffectivity:
    document_id: str
    provision_identity: str
    effective_from: date | None = None
    effective_to: date | None = None
    status: str = "confirmed"
    source_url: str | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class LegalChangeEvent:
    id: str
    document_id: str
    event_type: str
    effective_from: date | None
    source_url: str | None
    status: str = "candidate"
    scope: str = "whole_document"
    provisions: tuple[str, ...] = ()
    effective_to: date | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    reviewer_user_id: str | None = None


@dataclass(frozen=True)
class LegalLifecycleDocument:
    id: str
    effective_from: date | None
    effective_to: date | None = None
    stored_status: str = "active"
    source_url: str | None = None


@dataclass(frozen=True)
class LifecycleProjection:
    document_id: str
    legal_as_of: date
    bucket: LifecycleBucket
    effective_from: date | None
    effective_to: date | None
    current_serving_allowed: bool
    effective_provisions: tuple[str, ...]
    inactive_provisions: tuple[str, ...]
    applied_event_ids: tuple[str, ...]
    ignored_event_ids: tuple[str, ...]


EVENT_BUCKETS = {
    "replace": LifecycleBucket.REPLACED,
    "replaced": LifecycleBucket.REPLACED,
    "repeal": LifecycleBucket.REPEALED,
    "repealed": LifecycleBucket.REPEALED,
    "suspend": LifecycleBucket.SUSPENDED,
    "suspended": LifecycleBucket.SUSPENDED,
    "correct": LifecycleBucket.CORRECTED,
    "corrected": LifecycleBucket.CORRECTED,
    "consolidate": LifecycleBucket.CONSOLIDATED,
    "consolidated": LifecycleBucket.CONSOLIDATED,
}
CURRENT_SERVING_BUCKETS = {
    LifecycleBucket.ACTIVE,
    LifecycleBucket.EXPIRING_90,
    LifecycleBucket.EXPIRING_30,
    LifecycleBucket.EXPIRING_7,
    LifecycleBucket.EXPIRING_1,
    LifecycleBucket.PARTIALLY_EXPIRED,
    LifecycleBucket.CORRECTED,
    LifecycleBucket.CONSOLIDATED,
}


def _expiry_bucket(effective_to: date, legal_as_of: date) -> LifecycleBucket:
    days = (effective_to - legal_as_of).days
    if days <= 0:
        return LifecycleBucket.EXPIRED
    if days <= 1:
        return LifecycleBucket.EXPIRING_1
    if days <= 7:
        return LifecycleBucket.EXPIRING_7
    if days <= 30:
        return LifecycleBucket.EXPIRING_30
    if days <= 90:
        return LifecycleBucket.EXPIRING_90
    return LifecycleBucket.ACTIVE


def provision_is_effective(
    provision: LegalProvisionEffectivity,
    *,
    legal_as_of: date,
) -> bool:
    if provision.status != "confirmed":
        return False
    if provision.effective_from and provision.effective_from > legal_as_of:
        return False
    if provision.effective_to and provision.effective_to <= legal_as_of:
        return False
    return True


class LifecycleProjectionService:
    """Read-only, evidence-gated lifecycle projection for serving decisions."""

    def project(
        self,
        document: LegalLifecycleDocument,
        *,
        legal_as_of: date,
        events: tuple[LegalChangeEvent, ...] = (),
        provisions: tuple[LegalProvisionEffectivity, ...] = (),
    ) -> LifecycleProjection:
        if not isinstance(legal_as_of, date):
            raise ValueError("legal_as_of_required")

        applicable: list[LegalChangeEvent] = []
        ignored: list[str] = []
        for event in events:
            evidence_ready = bool(event.source_url and event.effective_from)
            if (
                event.document_id != document.id
                or event.status != "confirmed"
                or not evidence_ready
                or event.effective_from > legal_as_of
            ):
                ignored.append(event.id)
                continue
            applicable.append(event)
        applicable.sort(key=lambda item: (item.effective_from or date.min, item.id))

        effective_to = document.effective_to
        whole_bucket: LifecycleBucket | None = None
        for event in applicable:
            if event.scope != "whole_document":
                continue
            event_type = event.event_type.casefold().strip()
            if event_type in {"expiry", "expire"}:
                effective_to = event.effective_from
                whole_bucket = None
            elif event_type in {"extend", "extension"} and event.effective_to:
                effective_to = event.effective_to
                whole_bucket = None
            elif event_type in {"restore", "restored"}:
                whole_bucket = None
            elif event_type in EVENT_BUCKETS:
                whole_bucket = EVENT_BUCKETS[event_type]

        relevant_provisions = tuple(
            item for item in provisions if item.document_id == document.id
        )
        effective_provisions = tuple(
            item.provision_identity
            for item in relevant_provisions
            if provision_is_effective(item, legal_as_of=legal_as_of)
        )
        inactive_provisions = tuple(
            item.provision_identity
            for item in relevant_provisions
            if not provision_is_effective(item, legal_as_of=legal_as_of)
        )

        stored = document.stored_status.casefold().strip()
        stored_bucket = next(
            (item for item in LifecycleBucket if item.value == stored),
            LifecycleBucket.UNKNOWN,
        )
        if document.effective_from and document.effective_from > legal_as_of:
            bucket = LifecycleBucket.FUTURE
        elif whole_bucket is not None:
            bucket = whole_bucket
        elif stored not in {"active", "approved"}:
            bucket = stored_bucket
        elif relevant_provisions and not effective_provisions:
            bucket = LifecycleBucket.EXPIRED
        elif inactive_provisions:
            bucket = LifecycleBucket.PARTIALLY_EXPIRED
        elif effective_to:
            bucket = _expiry_bucket(effective_to, legal_as_of)
        else:
            bucket = LifecycleBucket.ACTIVE

        return LifecycleProjection(
            document_id=document.id,
            legal_as_of=legal_as_of,
            bucket=bucket,
            effective_from=document.effective_from,
            effective_to=effective_to,
            current_serving_allowed=bucket in CURRENT_SERVING_BUCKETS,
            effective_provisions=effective_provisions,
            inactive_provisions=inactive_provisions,
            applied_event_ids=tuple(item.id for item in applicable),
            ignored_event_ids=tuple(ignored),
        )

    def summary(self, projections: list[LifecycleProjection]) -> dict[str, int]:
        result = {bucket.value: 0 for bucket in LifecycleBucket}
        for projection in projections:
            result[projection.bucket.value] += 1
        return result

    def alerts(self, projections: list[LifecycleProjection]) -> list[dict[str, Any]]:
        thresholds = {
            LifecycleBucket.EXPIRING_90: 90,
            LifecycleBucket.EXPIRING_30: 30,
            LifecycleBucket.EXPIRING_7: 7,
            LifecycleBucket.EXPIRING_1: 1,
        }
        return [
            {
                "document_id": item.document_id,
                "bucket": item.bucket.value,
                "threshold_days": thresholds[item.bucket],
                "effective_to": item.effective_to.isoformat() if item.effective_to else None,
                "legal_as_of": item.legal_as_of.isoformat(),
            }
            for item in projections
            if item.bucket in thresholds
        ]


def normalized_draft_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed = {
        "logical_document_id", "base_fingerprint", "title", "law_number",
        "document_type", "issuing_agency", "scope", "sector", "issued_date",
        "effective_date", "expired_date", "source_url", "source_asset", "content",
    }
    result = {key: deepcopy(value) for key, value in (existing or {}).items() if key in allowed}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key == "logical_document_id":
            result[key] = int(value) if value not in (None, "") else None
        elif key == "content":
            text = normalize_text(value)
            if len(text) > MAX_CONTENT_CHARS:
                raise LifecycleError("lifecycle_content_too_large", "Nội dung bản nháp vượt giới hạn cho phép.", status_code=413)
            result[key] = text
        else:
            result[key] = str(value or "").strip() or None
    result.setdefault("logical_document_id", None)
    result.setdefault("content", "")
    result["content_hash"] = hashlib.sha256(str(result.get("content") or "").encode("utf-8")).hexdigest() if result.get("content") else None
    return result


def _official_source(url: Any) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host in OFFICIAL_HOSTS or host.endswith(".gov.vn"))


def validate_draft_payload(draft: dict[str, Any], duplicates: list[dict[str, Any]]) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    for field in ("title", "law_number", "document_type", "issuing_agency"):
        if not str(draft.get(field) or "").strip():
            blocking.append(f"missing_{field}")
    if not _official_source(draft.get("source_url")):
        blocking.append("missing_official_source")
    if not draft.get("content") and not draft.get("source_asset"):
        blocking.append("missing_content_or_source_asset")
    parsed_dates: dict[str, date] = {}
    for field in ("issued_date", "effective_date", "expired_date"):
        value = draft.get(field)
        if not value:
            continue
        try:
            parsed_dates[field] = date.fromisoformat(str(value))
        except ValueError:
            blocking.append(f"invalid_{field}")
    if parsed_dates.get("effective_date") and parsed_dates.get("issued_date") and parsed_dates["effective_date"] < parsed_dates["issued_date"]:
        blocking.append("effective_before_issued")
    if parsed_dates.get("expired_date") and parsed_dates.get("effective_date") and parsed_dates["expired_date"] <= parsed_dates["effective_date"]:
        blocking.append("expired_not_after_effective")
    if duplicates:
        warnings.append("duplicate_candidates_require_review")
    return {
        "blocking": sorted(set(blocking)),
        "warnings": warnings,
        "duplicate_candidates": duplicates,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def safe_draft_projection(draft: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    projected = {key: deepcopy(value) for key, value in draft.items() if key not in {"content_hash", "base_fingerprint"}}
    if not include_content:
        projected.pop("content", None)
        projected.pop("source_asset", None)
    return projected


class LifecycleRepository(Protocol):
    async def get_draft(self, draft_id: str) -> dict[str, Any] | None: ...
    async def create_draft(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def update_draft(self, draft_id: str, data: dict[str, Any]) -> dict[str, Any]: ...
    async def list_drafts(self, *, actor: str, reviewer: bool, state: str | None, limit: int, offset: int) -> list[dict[str, Any]]: ...
    async def find_duplicates(self, draft: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def get_idempotency(self, scope_key: str) -> dict[str, Any] | None: ...
    async def save_idempotency(self, data: dict[str, Any]) -> None: ...
    async def add_approval(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def add_version(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def add_manifest(self, data: dict[str, Any]) -> dict[str, Any]: ...


class InMemoryLifecycleRepository:
    def __init__(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}
        self.approvals: list[dict[str, Any]] = []
        self.manifests: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, dict[str, Any]] = {}

    async def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        value = self.drafts.get(draft_id)
        return deepcopy(value) if value else None

    async def create_draft(self, data: dict[str, Any]) -> dict[str, Any]:
        draft_id = f"legal_document_draft:{uuid.uuid4().hex}"
        value = {**deepcopy(data), "id": draft_id}
        self.drafts[draft_id] = value
        return deepcopy(value)

    async def update_draft(self, draft_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.drafts[draft_id] = {**self.drafts[draft_id], **deepcopy(data), "id": draft_id}
        return deepcopy(self.drafts[draft_id])

    async def list_drafts(self, *, actor: str, reviewer: bool, state: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        rows = list(self.drafts.values())
        if reviewer:
            rows = [row for row in rows if row.get("state") in {"submitted", "approved", "rejected", "changes_requested"}]
        else:
            rows = [row for row in rows if row.get("created_by") == actor]
        if state:
            rows = [row for row in rows if row.get("state") == state]
        rows.sort(key=lambda row: str(row.get("updated") or ""), reverse=True)
        return deepcopy(rows[offset : offset + limit])

    async def find_duplicates(self, draft: dict[str, Any]) -> list[dict[str, Any]]:
        matches = []
        for row in self.drafts.values():
            if row.get("id") == draft.get("id"):
                continue
            evidence = []
            for field in ("law_number", "source_url", "content_hash"):
                if draft.get(field) and draft.get(field) == row.get(field):
                    evidence.append(field)
            if evidence:
                matches.append({"draft_id": row["id"], "evidence": evidence, "state": row.get("state")})
        return matches[:20]

    async def get_idempotency(self, scope_key: str) -> dict[str, Any] | None:
        return deepcopy(self.idempotency.get(scope_key))

    async def save_idempotency(self, data: dict[str, Any]) -> None:
        self.idempotency[data["scope_key"]] = deepcopy(data)

    async def add_approval(self, data: dict[str, Any]) -> dict[str, Any]:
        value = {**deepcopy(data), "id": f"legal_document_approval:{uuid.uuid4().hex}"}
        self.approvals.append(value)
        return deepcopy(value)

    async def add_version(self, data: dict[str, Any]) -> dict[str, Any]:
        key = data["version_key"]
        if key not in self.versions:
            self.versions[key] = {**deepcopy(data), "id": f"legal_document_version:{uuid.uuid4().hex}"}
        return deepcopy(self.versions[key])

    async def add_manifest(self, data: dict[str, Any]) -> dict[str, Any]:
        key = str(data["version"])
        if key not in self.manifests:
            self.manifests[key] = {**deepcopy(data), "id": f"legal_activation_manifest:{uuid.uuid4().hex}"}
        return deepcopy(self.manifests[key])


class SurrealLifecycleRepository:
    """Additive production adapter; normal runtime write gate is disabled."""

    @staticmethod
    def _row(value: Any) -> dict[str, Any] | None:
        if isinstance(value, list):
            return value[0] if value and isinstance(value[0], dict) else None
        return value if isinstance(value, dict) else None

    async def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        from open_notebook.database.repository import ensure_record_id, repo_query
        rows = await repo_query("SELECT * FROM $record;", {"record": ensure_record_id(draft_id)})
        return self._row(rows)

    async def create_draft(self, data: dict[str, Any]) -> dict[str, Any]:
        from open_notebook.database.repository import repo_create
        row = self._row(await repo_create("legal_document_draft", data))
        if not row:
            raise RuntimeError("lifecycle_create_failed")
        return row

    async def update_draft(self, draft_id: str, data: dict[str, Any]) -> dict[str, Any]:
        from open_notebook.database.repository import repo_update
        row = self._row(await repo_update("legal_document_draft", draft_id, data))
        if not row:
            raise RuntimeError("lifecycle_update_failed")
        return row

    async def list_drafts(self, *, actor: str, reviewer: bool, state: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        from open_notebook.database.repository import repo_query
        filters = ["state = $state"] if state else []
        if reviewer:
            filters.append("state IN ['submitted', 'approved', 'rejected', 'changes_requested']")
        else:
            filters.append("created_by = $actor")
        where = " AND ".join(filters)
        return await repo_query(
            f"SELECT * OMIT content, content_hash, source_asset FROM legal_document_draft WHERE {where} ORDER BY updated DESC LIMIT $limit START $offset;",
            {"state": state, "actor": actor, "limit": limit, "offset": offset},
        )

    async def find_duplicates(self, draft: dict[str, Any]) -> list[dict[str, Any]]:
        from open_notebook.database.repository import repo_query
        return await repo_query(
            "SELECT id, state, law_number, source_url, content_hash FROM legal_document_draft "
            "WHERE id != $id AND (law_number = $law_number OR source_url = $source_url OR content_hash = $content_hash) LIMIT 20;",
            {"id": draft.get("id"), "law_number": draft.get("law_number"), "source_url": draft.get("source_url"), "content_hash": draft.get("content_hash")},
        )

    async def get_idempotency(self, scope_key: str) -> dict[str, Any] | None:
        from open_notebook.database.repository import repo_query
        return self._row(await repo_query("SELECT * FROM legal_lifecycle_idempotency WHERE scope_key = $scope LIMIT 1;", {"scope": scope_key}))

    async def save_idempotency(self, data: dict[str, Any]) -> None:
        from open_notebook.database.repository import repo_create
        await repo_create("legal_lifecycle_idempotency", data)

    async def add_approval(self, data: dict[str, Any]) -> dict[str, Any]:
        from open_notebook.database.repository import repo_create
        row = self._row(await repo_create("legal_document_approval", data))
        if not row:
            raise RuntimeError("lifecycle_approval_failed")
        return row

    async def add_version(self, data: dict[str, Any]) -> dict[str, Any]:
        from open_notebook.database.repository import repo_create, repo_query
        existing = self._row(await repo_query("SELECT * FROM legal_document_version WHERE version_key = $key LIMIT 1;", {"key": data["version_key"]}))
        if existing:
            return existing
        row = self._row(await repo_create("legal_document_version", data))
        if not row:
            raise RuntimeError("lifecycle_version_failed")
        return row

    async def add_manifest(self, data: dict[str, Any]) -> dict[str, Any]:
        from open_notebook.database.repository import repo_create
        row = self._row(await repo_create("legal_activation_manifest", data))
        if not row:
            raise RuntimeError("lifecycle_manifest_failed")
        return row


AuditWriter = Callable[..., Awaitable[None]]
BaseFingerprintReader = Callable[[int], Awaitable[str | None]]
ActivationAdapter = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class LifecycleService:
    def __init__(
        self,
        repository: LifecycleRepository,
        *,
        audit_writer: AuditWriter | None = None,
        base_fingerprint_reader: BaseFingerprintReader | None = None,
        activation_adapter: ActivationAdapter | None = None,
    ) -> None:
        self.repository = repository
        self.audit_writer = audit_writer
        self.base_fingerprint_reader = base_fingerprint_reader
        self.activation_adapter = activation_adapter
        self._mutation_lock = asyncio.Lock()

    async def _audit(self, *, action: str, actor: str, role: str, entity_id: str, reason: str, details: dict[str, Any] | None = None) -> None:
        writer = self.audit_writer
        if writer is None:
            from api.user_service import write_audit_log
            writer = write_audit_log
        await writer(
            action=f"legal.lifecycle.{action}", entity_type="legal_document_lifecycle",
            entity_id=entity_id, actor_user_id=actor, actor_role=role,
            details={"reason": reason, **(details or {})}, request=None,
        )

    @staticmethod
    def _require(capabilities: dict[str, Any], capability: str, *, writes: bool = False) -> None:
        if not capabilities["authenticated_admin"] or not capabilities.get(capability, False):
            raise LifecycleError("lifecycle_forbidden", "Tài khoản không có quyền thực hiện thao tác vòng đời này.", status_code=403)
        if writes and not capabilities["writes_enabled"]:
            raise LifecycleError("lifecycle_live_writes_disabled", "Ghi dữ liệu vòng đời đang bị khóa trong cấu hình hiện tại.", status_code=503)

    @staticmethod
    def _validate_reason(reason: str) -> str:
        value = str(reason or "").strip()
        if not 10 <= len(value) <= 2000:
            raise LifecycleError("lifecycle_reason_required", "Lý do phải có từ 10 đến 2000 ký tự.")
        return value

    @staticmethod
    def _validate_key(key: str) -> str:
        value = str(key or "").strip()
        if not IDEMPOTENCY_RE.fullmatch(value):
            raise LifecycleError("lifecycle_idempotency_key_invalid", "Idempotency-Key không hợp lệ.")
        return value

    @staticmethod
    def _validate_draft_id(draft_id: str) -> str:
        value = str(draft_id or "").strip()
        if not DRAFT_ID_RE.fullmatch(value):
            raise LifecycleError("lifecycle_draft_id_invalid", "Mã bản nháp không hợp lệ.")
        return value

    async def _idempotent(self, *, actor: str, action: str, key: str, request: dict[str, Any], operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        key = self._validate_key(key)
        scope_key = f"{actor}:{action}:{key}"
        fingerprint = canonical_fingerprint(request)
        async with self._mutation_lock:
            existing = await self.repository.get_idempotency(scope_key)
            if existing:
                if existing.get("request_fingerprint") != fingerprint:
                    raise LifecycleError("lifecycle_idempotency_conflict", "Idempotency-Key đã được dùng cho nội dung khác.", status_code=409)
                return deepcopy(existing.get("response") or {})
            result = await operation()
            response = {key: deepcopy(value) for key, value in result.items() if key in MUTATION_RESPONSE_FIELDS}
            await self.repository.save_idempotency({
                "scope_key": scope_key, "actor": actor, "action": action,
                "request_fingerprint": fingerprint, "response": response,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return response

    async def _draft(self, draft_id: str) -> dict[str, Any]:
        draft_id = self._validate_draft_id(draft_id)
        try:
            draft = await self.repository.get_draft(draft_id)
        except LifecycleError:
            raise
        except Exception as exc:
            raise LifecycleError("lifecycle_schema_unavailable", "Kho bản nháp chưa sẵn sàng; migration live chưa được áp dụng.", status_code=503) from exc
        if not draft:
            raise LifecycleError("lifecycle_draft_not_found", "Không tìm thấy bản nháp.", status_code=404)
        return draft

    @staticmethod
    def _check_revision(draft: dict[str, Any], expected: int) -> None:
        if int(draft.get("revision") or 0) != int(expected):
            raise LifecycleError("lifecycle_revision_conflict", "Bản nháp đã thay đổi; hãy tải lại trước khi tiếp tục.", status_code=409)

    async def create_draft(self, *, actor: str, role: str, payload: dict[str, Any], reason: str, idempotency_key: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        self._require(caps, "editor", writes=True)
        reason = self._validate_reason(reason)
        normalized = normalized_draft_payload(payload)
        now = datetime.now(timezone.utc).isoformat()

        async def operation() -> dict[str, Any]:
            await self._audit(action="draft.create", actor=actor, role=role, entity_id=f"pending:{canonical_fingerprint(normalized)[:16]}", reason=reason)
            return await self.repository.create_draft({
                **normalized, "state": "draft", "revision": 1, "validation": None,
                "created_by": actor, "submitted_by": None, "reviewed_by": None,
                "created": now, "updated": now,
            })

        return await self._idempotent(actor=actor, action="create", key=idempotency_key, request={"payload": normalized, "reason": reason}, operation=operation)

    async def update_draft(self, draft_id: str, *, actor: str, role: str, payload: dict[str, Any], expected_revision: int, reason: str, idempotency_key: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        self._require(caps, "editor", writes=True)
        reason = self._validate_reason(reason)

        async def operation() -> dict[str, Any]:
            draft = await self._draft(draft_id)
            if draft.get("created_by") != actor or draft.get("state") not in EDITABLE_STATES:
                raise LifecycleError("lifecycle_draft_not_editable", "Bản nháp không ở trạng thái có thể chỉnh sửa.", status_code=409)
            self._check_revision(draft, expected_revision)
            normalized = normalized_draft_payload(payload, existing=draft)
            await self._audit(action="draft.update", actor=actor, role=role, entity_id=draft_id, reason=reason, details={"from_revision": expected_revision})
            return await self.repository.update_draft(draft_id, {
                **normalized, "state": "draft", "validation": None,
                "revision": expected_revision + 1, "updated": datetime.now(timezone.utc).isoformat(),
            })

        return await self._idempotent(actor=actor, action=f"update:{draft_id}", key=idempotency_key, request={"payload": payload, "revision": expected_revision, "reason": reason}, operation=operation)

    async def validate_draft(self, draft_id: str, *, actor: str, role: str, expected_revision: int, reason: str, idempotency_key: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        self._require(caps, "editor", writes=True)
        reason = self._validate_reason(reason)

        async def operation() -> dict[str, Any]:
            draft = await self._draft(draft_id)
            if draft.get("created_by") != actor or draft.get("state") not in EDITABLE_STATES:
                raise LifecycleError("lifecycle_draft_not_editable", "Bản nháp không ở trạng thái có thể kiểm tra.", status_code=409)
            self._check_revision(draft, expected_revision)
            duplicates = await self.repository.find_duplicates(draft)
            validation = validate_draft_payload(draft, duplicates)
            next_revision = expected_revision + 1
            validation["validated_revision"] = next_revision
            state = "duplicate_review" if duplicates else "draft"
            await self._audit(action="draft.validate", actor=actor, role=role, entity_id=draft_id, reason=reason, details={"blocking_count": len(validation["blocking"]), "duplicate_count": len(duplicates)})
            return await self.repository.update_draft(draft_id, {"state": state, "validation": validation, "revision": next_revision, "updated": datetime.now(timezone.utc).isoformat()})

        return await self._idempotent(actor=actor, action=f"validate:{draft_id}", key=idempotency_key, request={"revision": expected_revision, "reason": reason}, operation=operation)

    async def submit_draft(self, draft_id: str, *, actor: str, role: str, expected_revision: int, reason: str, idempotency_key: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        self._require(caps, "editor", writes=True)
        reason = self._validate_reason(reason)

        async def operation() -> dict[str, Any]:
            draft = await self._draft(draft_id)
            if draft.get("created_by") != actor or draft.get("state") not in EDITABLE_STATES:
                raise LifecycleError("lifecycle_draft_not_submittable", "Bản nháp không ở trạng thái có thể gửi duyệt.", status_code=409)
            self._check_revision(draft, expected_revision)
            validation = draft.get("validation") or {}
            if validation.get("blocking") or validation.get("validated_revision") != expected_revision:
                raise LifecycleError("lifecycle_validation_blocked", "Bản nháp chưa có kết quả kiểm tra hợp lệ cho revision hiện tại.", status_code=409)
            next_revision = expected_revision + 1
            await self._audit(action="draft.submit", actor=actor, role=role, entity_id=draft_id, reason=reason, details={"revision": expected_revision})
            await self.repository.add_approval({"draft": draft_id, "action": "submitted", "actor": actor, "reason": reason, "revision": next_revision, "request_fingerprint": canonical_fingerprint({"draft": draft_id, "action": "submitted", "revision": expected_revision, "actor": actor})})
            return await self.repository.update_draft(draft_id, {"state": "submitted", "submitted_by": actor, "revision": next_revision, "updated": datetime.now(timezone.utc).isoformat()})

        return await self._idempotent(actor=actor, action=f"submit:{draft_id}", key=idempotency_key, request={"revision": expected_revision, "reason": reason}, operation=operation)

    async def review_draft(self, draft_id: str, *, actor: str, role: str, expected_revision: int, decision: str, reason: str, idempotency_key: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        self._require(caps, "reviewer", writes=True)
        reason = self._validate_reason(reason)
        if decision not in REVIEW_DECISIONS:
            raise LifecycleError("lifecycle_review_decision_invalid", "Quyết định duyệt không hợp lệ.")

        async def operation() -> dict[str, Any]:
            draft = await self._draft(draft_id)
            if draft.get("state") != "submitted":
                raise LifecycleError("lifecycle_draft_not_reviewable", "Bản nháp không ở trạng thái chờ duyệt.", status_code=409)
            self._check_revision(draft, expected_revision)
            if draft.get("submitted_by") == actor:
                raise LifecycleError("lifecycle_self_review_forbidden", "Người gửi không được tự duyệt hồ sơ của mình.", status_code=403)
            next_revision = expected_revision + 1
            await self._audit(action=f"draft.review.{decision}", actor=actor, role=role, entity_id=draft_id, reason=reason, details={"submitter": draft.get("submitted_by")})
            version = None
            manifest = None
            if decision == "approved":
                snapshot = {key: deepcopy(value) for key, value in draft.items() if key not in {"id", "state", "revision", "validation", "created", "updated", "reviewed_by"}}
                version_key = canonical_fingerprint({"logical_document_id": draft.get("logical_document_id"), "snapshot": snapshot, "base_fingerprint": draft.get("base_fingerprint")})
                version = await self.repository.add_version({
                    "version_key": version_key, "logical_document_id": draft.get("logical_document_id"),
                    "draft": draft_id, "snapshot": snapshot, "content_hash": draft.get("content_hash"),
                    "base_fingerprint": draft.get("base_fingerprint"), "review_state": "approved",
                    "reviewed_by": actor, "approved_at": datetime.now(timezone.utc).isoformat(),
                    "activation_state": "not_requested",
                })
                manifest = await self.repository.add_manifest({
                    "version": version.get("id") or version_key, "version_key": version_key,
                    "content_hash": draft.get("content_hash"), "provision_ids": [], "chunk_ids": [],
                    "vector_ids": {"fast": [], "expanded": []}, "required_collections": ["fast", "expanded"],
                    "embedding_fingerprint": None, "ready": False,
                })
            await self.repository.add_approval({
                "draft": draft_id, "version": (version or {}).get("id"), "action": decision,
                "actor": actor, "submitted_by": draft.get("submitted_by"), "reason": reason,
                "revision": next_revision,
                "request_fingerprint": canonical_fingerprint({"draft": draft_id, "decision": decision, "revision": expected_revision, "actor": actor}),
            })
            updated = await self.repository.update_draft(draft_id, {"state": decision, "reviewed_by": actor, "revision": next_revision, "updated": datetime.now(timezone.utc).isoformat(), "version": safe_draft_projection(version or {}, include_content=False) or None, "manifest": safe_draft_projection(manifest or {}, include_content=False) or None})
            return updated

        return await self._idempotent(actor=actor, action=f"review:{draft_id}:{decision}", key=idempotency_key, request={"revision": expected_revision, "decision": decision, "reason": reason}, operation=operation)

    async def get_draft(self, draft_id: str, *, actor: str, role: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        if not caps["authenticated_admin"] or not (caps["editor"] or caps["reviewer"]):
            raise LifecycleError("lifecycle_forbidden", "Tài khoản không có quyền xem bản nháp.", status_code=403)
        draft = await self._draft(draft_id)
        if not caps["reviewer"] and draft.get("created_by") != actor:
            raise LifecycleError("lifecycle_forbidden", "Tài khoản không có quyền xem bản nháp này.", status_code=403)
        return safe_draft_projection(draft, include_content=True)

    async def list_drafts(self, *, actor: str, role: str, state: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        if not caps["authenticated_admin"] or not (caps["editor"] or caps["reviewer"]):
            raise LifecycleError("lifecycle_forbidden", "Tài khoản không có quyền xem hàng bản nháp.", status_code=403)
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        rows = await self.repository.list_drafts(actor=actor, reviewer=caps["reviewer"], state=state, limit=limit, offset=offset)
        return {"items": [safe_draft_projection(row) for row in rows], "limit": limit, "offset": offset}

    async def activation_preview(self, draft_id: str, *, actor: str, role: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        if not caps["authenticated_admin"] or not (caps["editor"] or caps["reviewer"]):
            raise LifecycleError("lifecycle_forbidden", "Tài khoản không có quyền xem tác động kích hoạt.", status_code=403)
        draft = await self._draft(draft_id)
        if draft.get("state") != "approved" or not draft.get("version"):
            raise LifecycleError("lifecycle_version_not_approved", "Chưa có phiên bản được duyệt để lập preview.", status_code=409)
        manifest = draft.get("manifest") or {}
        version = draft.get("version") or {}
        logical_document_id = draft.get("logical_document_id")
        base_fingerprint = draft.get("base_fingerprint")
        current_base_fingerprint = None
        base_match: bool | None = None
        if logical_document_id:
            if self.base_fingerprint_reader is not None:
                current_base_fingerprint = await self.base_fingerprint_reader(int(logical_document_id))
                base_match = bool(base_fingerprint and current_base_fingerprint == base_fingerprint)
        else:
            base_match = True
        manifest_ready = bool(manifest.get("ready"))
        return {
            "draft_id": draft_id, "logical_document_id": logical_document_id,
            "version_key": version.get("version_key"), "base_fingerprint": base_fingerprint,
            "current_base_fingerprint": current_base_fingerprint, "base_match": base_match,
            "manifest_ready": manifest_ready,
            "provision_count": len(manifest.get("provision_ids") or []),
            "chunk_count": len(manifest.get("chunk_ids") or []),
            "required_collections": manifest.get("required_collections") or [],
            "live_activation_enabled": caps["activation_enabled"],
            "status": "ready" if caps["activation_enabled"] and manifest_ready and base_match is True else "blocked",
        }

    async def activate_draft(self, draft_id: str, *, actor: str, role: str, expected_revision: int, reason: str, idempotency_key: str) -> dict[str, Any]:
        caps = lifecycle_capabilities(actor, role)
        self._require(caps, "reviewer", writes=True)
        reason = self._validate_reason(reason)

        async def operation() -> dict[str, Any]:
            draft = await self._draft(draft_id)
            self._check_revision(draft, expected_revision)
            if draft.get("submitted_by") == actor:
                raise LifecycleError("lifecycle_self_review_forbidden", "Người gửi không được tự kích hoạt hồ sơ của mình.", status_code=403)
            if not caps["activation_enabled"]:
                raise LifecycleError("lifecycle_live_activation_unconfigured", "Kích hoạt corpus/vector thật chưa được cấu hình cho Phase 2.", status_code=503)
            if self.activation_adapter is None:
                raise LifecycleError("lifecycle_activation_adapter_unavailable", "Chưa có activation adapter được phê duyệt.", status_code=503)
            preview = await self.activation_preview(draft_id, actor=actor, role=role)
            if preview["base_match"] is not True:
                raise LifecycleError("lifecycle_base_fingerprint_conflict", "Văn bản gốc đã thay đổi hoặc chưa thể xác minh fingerprint.", status_code=409)
            if not preview["manifest_ready"]:
                raise LifecycleError("lifecycle_manifest_incomplete", "Manifest SQL/vector chưa đủ điều kiện kích hoạt.", status_code=409)
            await self._audit(
                action="draft.activate.intent", actor=actor, role=role,
                entity_id=draft_id, reason=reason,
                details={"version_key": preview.get("version_key"), "required_collections": preview["required_collections"]},
            )
            adapter_result = await self.activation_adapter(preview)
            verified_collections = set(adapter_result.get("collections_verified") or [])
            if not adapter_result.get("sql_verified") or not set(preview["required_collections"]).issubset(verified_collections):
                raise LifecycleError("lifecycle_activation_verification_failed", "Kích hoạt không đạt kiểm đếm SQL/vector; phiên bản cũ tiếp tục phục vụ.", status_code=409)
            return await self.repository.update_draft(
                draft_id,
                {
                    "activation_state": "active",
                    "revision": expected_revision + 1,
                    "updated": datetime.now(timezone.utc).isoformat(),
                },
            )

        return await self._idempotent(
            actor=actor,
            action=f"activate:{draft_id}",
            key=idempotency_key,
            request={"revision": expected_revision, "reason": reason},
            operation=operation,
        )


default_lifecycle_service = LifecycleService(SurrealLifecycleRepository())
