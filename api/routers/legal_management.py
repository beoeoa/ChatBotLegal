"""Feature 018 legal lifecycle/impact read and preview API.

The default runtime has no writable store configured. Tests and an explicitly
approved future adapter may inject one; index work remains preview-only here.
"""

from __future__ import annotations

import uuid
import json
import os
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username
from api.legal_impact_service import (
    LegalDependency,
    LegalImpactCase,
    LegalImpactEngine,
)
from api.legal_lifecycle_service import (
    canonical_fingerprint,
    LegalChangeEvent,
    LegalLifecycleDocument,
    LegalProvisionEffectivity,
    LifecycleProjection,
    LifecycleProjectionService,
)
from api.user_service import write_audit_log


router = APIRouter(prefix="/legal-management", tags=["Legal Management"])
_store_override: "InMemoryLegalManagementStore | None" = None
ROOT = Path(__file__).resolve().parents[2]
VECTOR_MANIFEST_PATH = Path(
    os.getenv(
        "FEATURE018_VECTOR_MANIFEST_PATH",
        str(ROOT / "reports" / "feature018" / "vector-manifest.json"),
    )
)


class ChangeEventCandidateRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=80)
    effective_from: date
    effective_to: date | None = None
    source_url: str = Field(min_length=8, max_length=2_000)
    scope: str = Field(default="whole_document", pattern="^(whole_document|provisions)$")
    provisions: list[str] = Field(default_factory=list, max_length=500)
    provenance: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=2_000)


class ConfirmChangeEventRequest(BaseModel):
    evidence_fingerprint: str = Field(pattern="^[0-9a-f]{64}$")
    reason: str = Field(min_length=10, max_length=2_000)


class ImpactDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(confirmed|rejected)$")
    reason: str = Field(min_length=10, max_length=2_000)


class IndexPreviewRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    provisions: list[str] = Field(default_factory=list, max_length=2_000)


class InMemoryLegalManagementStore:
    """Isolated contract store; not selected by the normal runtime."""

    def __init__(self) -> None:
        self.documents: dict[str, LegalLifecycleDocument] = {}
        self.events: dict[str, LegalChangeEvent] = {}
        self.provisions: list[LegalProvisionEffectivity] = []
        self.dependencies: list[LegalDependency] = []
        self.impact_cases: dict[str, LegalImpactCase] = {}
        self.vector_states: dict[str, str] = {}


def configure_legal_management_store(
    store: InMemoryLegalManagementStore | None,
) -> None:
    global _store_override
    _store_override = store


def _store() -> InMemoryLegalManagementStore:
    if _store_override is None:
        raise HTTPException(
            status_code=503,
            detail="Feature 018 legal management store is not active.",
        )
    return _store_override


def _admin_actor(request: Request) -> str:
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    actor = str(get_request_user_id(request) or get_request_username(request) or "").strip()
    if not actor:
        raise HTTPException(status_code=401, detail="Authenticated admin identity required.")
    return actor


def _projection_payload(item: LifecycleProjection, vector_state: str) -> dict[str, Any]:
    return {
        **asdict(item),
        "legal_as_of": item.legal_as_of.isoformat(),
        "effective_from": item.effective_from.isoformat() if item.effective_from else None,
        "effective_to": item.effective_to.isoformat() if item.effective_to else None,
        "bucket": item.bucket.value,
        "vector_state": vector_state,
    }


def _project_all(store: InMemoryLegalManagementStore, legal_as_of: date):
    service = LifecycleProjectionService()
    return [
        service.project(
            document,
            legal_as_of=legal_as_of,
            events=tuple(store.events.values()),
            provisions=tuple(store.provisions),
        )
        for document in store.documents.values()
    ]


def _event_evidence_fingerprint(event: LegalChangeEvent) -> str:
    return canonical_fingerprint(
        {
            "document_id": event.document_id,
            "event_type": event.event_type,
            "effective_from": event.effective_from.isoformat() if event.effective_from else None,
            "effective_to": event.effective_to.isoformat() if event.effective_to else None,
            "source_url": event.source_url,
            "scope": event.scope,
            "provisions": list(event.provisions),
            "provenance": event.provenance,
        }
    )


@router.get("/lifecycle/summary")
async def lifecycle_summary(request: Request, legal_as_of: date = Query(...)) -> dict[str, Any]:
    _admin_actor(request)
    store = _store()
    projections = _project_all(store, legal_as_of)
    return {
        "legal_as_of": legal_as_of.isoformat(),
        "counts": LifecycleProjectionService().summary(projections),
        "alerts": LifecycleProjectionService().alerts(projections),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/documents")
async def lifecycle_documents(
    request: Request,
    legal_as_of: date = Query(...),
    bucket: str | None = None,
    vector_state: str | None = None,
) -> list[dict[str, Any]]:
    _admin_actor(request)
    store = _store()
    rows = [
        _projection_payload(item, store.vector_states.get(item.document_id, "missing"))
        for item in _project_all(store, legal_as_of)
    ]
    if bucket:
        rows = [item for item in rows if item["bucket"] == bucket]
    if vector_state:
        rows = [item for item in rows if item["vector_state"] == vector_state]
    return rows


@router.get("/documents/{document_id}/timeline")
async def lifecycle_timeline(document_id: str, request: Request) -> dict[str, Any]:
    _admin_actor(request)
    store = _store()
    document = store.documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Legal document not found.")
    events = sorted(
        (item for item in store.events.values() if item.document_id == document_id),
        key=lambda item: (item.effective_from or date.min, item.id),
    )
    return {
        "document_id": document_id,
        "events": [
            {
                **asdict(item),
                "effective_from": item.effective_from.isoformat() if item.effective_from else None,
                "effective_to": item.effective_to.isoformat() if item.effective_to else None,
            }
            for item in events
        ],
        "provisions": [
            asdict(item) for item in store.provisions if item.document_id == document_id
        ],
        "vector_state": store.vector_states.get(document_id, "missing"),
    }


@router.post("/change-events/candidates", status_code=201)
async def create_change_event_candidate(
    body: ChangeEventCandidateRequest, request: Request
) -> dict[str, Any]:
    actor = _admin_actor(request)
    store = _store()
    if body.document_id not in store.documents:
        raise HTTPException(status_code=404, detail="Legal document not found.")
    event = LegalChangeEvent(
        id="change-" + uuid.uuid4().hex,
        document_id=body.document_id,
        event_type=body.event_type,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        source_url=body.source_url,
        status="candidate",
        scope=body.scope,
        provisions=tuple(body.provisions),
        provenance=body.provenance,
    )
    await write_audit_log(
        action="legal.lifecycle.change_candidate_created",
        entity_type="legal_change_event",
        entity_id=event.id,
        actor_user_id=actor,
        actor_role="admin",
        details={"reason": body.reason, "document_id": body.document_id},
        request=request,
    )
    store.events[event.id] = event
    return {
        **asdict(event),
        "effective_from": event.effective_from.isoformat(),
        "evidence_fingerprint": _event_evidence_fingerprint(event),
    }


@router.post("/change-events/{event_id}/confirm")
async def confirm_change_event(
    event_id: str, body: ConfirmChangeEventRequest, request: Request
) -> dict[str, Any]:
    actor = _admin_actor(request)
    store = _store()
    event = store.events.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Change event not found.")
    if event.status != "candidate":
        raise HTTPException(status_code=409, detail="Change event is not a candidate.")
    if body.evidence_fingerprint != _event_evidence_fingerprint(event):
        raise HTTPException(status_code=409, detail="Change event evidence fingerprint changed.")
    confirmed = replace(event, status="confirmed", reviewer_user_id=actor)
    cases = LegalImpactEngine().scan(confirmed, store.dependencies)
    await write_audit_log(
        action="legal.lifecycle.change_event_confirmed",
        entity_type="legal_change_event",
        entity_id=event_id,
        actor_user_id=actor,
        actor_role="admin",
        details={"reason": body.reason, "impact_case_count": len(cases)},
        request=request,
    )
    store.events[event_id] = confirmed
    store.impact_cases.update({item.id: item for item in cases})
    return {"event_id": event_id, "status": "confirmed", "impact_case_count": len(cases)}


@router.get("/impact-cases")
async def list_impact_cases(request: Request, status: str | None = None) -> list[dict[str, Any]]:
    _admin_actor(request)
    rows = list(_store().impact_cases.values())
    if status:
        rows = [item for item in rows if item.status == status]
    return [asdict(item) for item in rows]


@router.post("/impact-cases/{case_id}/decision")
async def decide_impact_case(
    case_id: str, body: ImpactDecisionRequest, request: Request
) -> dict[str, Any]:
    actor = _admin_actor(request)
    store = _store()
    case = store.impact_cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Impact case not found.")
    decided = LegalImpactEngine().decide(
        case,
        decision=body.decision,
        reviewer_user_id=actor,
        reason=body.reason,
    )
    await write_audit_log(
        action="legal.lifecycle.impact_decided",
        entity_type="legal_impact_case",
        entity_id=case_id,
        actor_user_id=actor,
        actor_role="admin",
        details={"reason": body.reason, "decision": body.decision},
        request=request,
    )
    store.impact_cases[case_id] = decided
    return asdict(decided)


@router.post("/index-jobs/preview")
async def preview_index_job(body: IndexPreviewRequest, request: Request) -> dict[str, Any]:
    _admin_actor(request)
    if body.document_id not in _store().documents:
        raise HTTPException(status_code=404, detail="Legal document not found.")
    return {
        "document_id": body.document_id,
        "target_provisions": body.provisions,
        "mode": "incremental" if body.provisions else "full",
        "state": "preview",
        "mutation_performed": False,
        "active_pointer_change": False,
    }


@router.get("/index-manifests/active")
async def active_index_manifest(request: Request) -> dict[str, Any]:
    """Return a bounded Admin projection, never local paths or raw inventory."""

    _admin_actor(request)
    try:
        payload = json.loads(VECTOR_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Vector serving manifest is unavailable.",
        ) from exc
    if not isinstance(payload, dict) or payload.get("read_only") is not True:
        raise HTTPException(
            status_code=503,
            detail="Vector serving manifest failed integrity validation.",
        )
    allowed = {
        "schema_version",
        "generated_at",
        "active_collection",
        "active_pointer_unchanged",
        "gate_passed",
        "counts",
        "expected_fingerprints",
        "manifest_fingerprint",
        "inventory_sha256",
        "reason_codes",
        "source_warnings",
        "read_only",
        "vectors_mutated",
        "corpus_mutated",
    }
    return {key: payload.get(key) for key in allowed}
