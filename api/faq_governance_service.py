"""Canonical FAQ revision/release governance for Feature 018.

FAQ content is versioned in PostgreSQL. Form identities are deliberately absent
from FAQ writes and are projected from the release-bound Feature 017 catalog.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Protocol


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


@dataclass(frozen=True)
class FaqActor:
    user_id: str
    role: str


class FaqGovernanceRepository(Protocol):
    def create_revision(self, faq_key: str, revision: dict[str, Any]) -> dict[str, Any]: ...
    def get_revision(self, revision_id: str) -> dict[str, Any] | None: ...
    def list_revisions(self) -> list[dict[str, Any]]: ...
    def update_revision_state(self, revision_id: str, state: str, *, actor_id: str) -> dict[str, Any]: ...
    def save_release(self, release: dict[str, Any], revision_ids: list[str]) -> dict[str, Any]: ...
    def get_release(self, release_id: str) -> dict[str, Any] | None: ...
    def update_release(self, release_id: str, values: dict[str, Any]) -> dict[str, Any]: ...
    def set_active_release(self, release_id: str, *, actor_id: str) -> dict[str, Any]: ...
    def active_release(self) -> dict[str, Any] | None: ...
    def next_release_version(self) -> int: ...


class InMemoryFaqGovernanceRepository:
    def __init__(self) -> None:
        self.identities: dict[str, dict[str, Any]] = {}
        self.revisions: dict[str, dict[str, Any]] = {}
        self.releases: dict[str, dict[str, Any]] = {}
        self.active_release_id: str | None = None

    def create_revision(self, faq_key: str, revision: dict[str, Any]) -> dict[str, Any]:
        identity = self.identities.setdefault(faq_key, {
            "id": revision["faq_ref"],
            "faq_key": faq_key,
            "created_at": _utcnow(),
        })
        existing = [item for item in self.revisions.values() if item["faq_ref"] == identity["id"]]
        if any(item["source_sha256"] == revision["source_sha256"] for item in existing):
            return deepcopy(next(item for item in existing if item["source_sha256"] == revision["source_sha256"]))
        stored = {
            **deepcopy(revision),
            "faq_ref": identity["id"],
            "revision_number": len(existing) + 1,
            "created_at": _utcnow(),
        }
        self.revisions[stored["id"]] = stored
        return deepcopy(stored)

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        item = self.revisions.get(revision_id)
        return deepcopy(item) if item else None

    def list_revisions(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in sorted(
            self.revisions.values(),
            key=lambda value: (str(value.get("created_at") or ""), value["id"]),
            reverse=True,
        )]

    def update_revision_state(self, revision_id: str, state: str, *, actor_id: str) -> dict[str, Any]:
        item = self.revisions.get(revision_id)
        if not item:
            raise LookupError("FAQ_REVISION_NOT_FOUND")
        item["public_state"] = state
        item["reviewed_by"] = actor_id
        item["reviewed_at"] = _utcnow()
        return deepcopy(item)

    def save_release(self, release: dict[str, Any], revision_ids: list[str]) -> dict[str, Any]:
        stored = {**deepcopy(release), "revision_ids": list(revision_ids)}
        self.releases[stored["id"]] = stored
        return deepcopy(stored)

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        item = self.releases.get(release_id)
        return deepcopy(item) if item else None

    def update_release(self, release_id: str, values: dict[str, Any]) -> dict[str, Any]:
        item = self.releases.get(release_id)
        if not item:
            raise LookupError("FAQ_RELEASE_NOT_FOUND")
        item.update(deepcopy(values))
        return deepcopy(item)

    def set_active_release(self, release_id: str, *, actor_id: str) -> dict[str, Any]:
        release = self.releases.get(release_id)
        if not release:
            raise LookupError("FAQ_RELEASE_NOT_FOUND")
        previous = self.active_release_id
        if previous and previous != release_id:
            self.releases[previous]["status"] = "retired"
        self.active_release_id = release_id
        release["status"] = "active"
        release["activated_by"] = actor_id
        release["activated_at"] = _utcnow()
        for revision_id in release.get("revision_ids") or []:
            self.update_revision_state(revision_id, "released", actor_id=actor_id)
        return deepcopy(release)

    def active_release(self) -> dict[str, Any] | None:
        return self.get_release(self.active_release_id) if self.active_release_id else None

    def next_release_version(self) -> int:
        return max((int(item["version"]) for item in self.releases.values()), default=0) + 1


class PostgresFaqGovernanceRepository:
    """Transactional adapter for the additive Feature 018 FAQ tables."""

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(database_url, future=True, pool_pre_ping=True)

    @staticmethod
    def _row(row: Any) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        for key in ("evidence", "manifest"):
            if key in item:
                item[key] = _json_value(item[key])
        return item

    def create_revision(self, faq_key: str, revision: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO faq_identity (id, faq_key)
                VALUES (:id, :faq_key)
                ON CONFLICT (faq_key) DO NOTHING
            """), {"id": revision["faq_ref"], "faq_key": faq_key})
            identity = connection.execute(text(
                "SELECT id FROM faq_identity WHERE faq_key=:faq_key FOR UPDATE"
            ), {"faq_key": faq_key}).scalar_one()
            existing = connection.execute(text("""
                SELECT * FROM faq_revision
                WHERE faq_ref=:faq_ref AND source_sha256=:source_sha256
            """), {"faq_ref": identity, "source_sha256": revision["source_sha256"]}).mappings().first()
            if existing:
                return self._row(existing) or {}
            revision_number = int(connection.execute(text("""
                SELECT COALESCE(MAX(revision_number),0)+1
                FROM faq_revision WHERE faq_ref=:faq_ref
            """), {"faq_ref": identity}).scalar_one())
            connection.execute(text("""
                INSERT INTO faq_revision
                    (id, faq_ref, revision_number, question, answer, canonical_domain,
                     confirmed_procedure_id, evidence, public_state, source_sha256)
                VALUES
                    (:id, :faq_ref, :revision_number, :question, :answer, :canonical_domain,
                     :confirmed_procedure_id, CAST(:evidence AS JSONB), :public_state, :source_sha256)
            """), {
                **revision,
                "faq_ref": identity,
                "revision_number": revision_number,
                "evidence": _canonical_json(revision["evidence"]),
            })
            row = connection.execute(text(
                "SELECT * FROM faq_revision WHERE id=:id"
            ), {"id": revision["id"]}).mappings().one()
            return self._row(row) or {}

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT * FROM faq_revision WHERE id=:id"
            ), {"id": revision_id}).mappings().first()
        return self._row(row)

    def list_revisions(self) -> list[dict[str, Any]]:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT DISTINCT ON (faq_ref) * FROM faq_revision
                ORDER BY faq_ref, revision_number DESC
            """)).mappings().all()
        return [self._row(row) or {} for row in rows]

    def update_revision_state(self, revision_id: str, state: str, *, actor_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            row = connection.execute(text(
                "SELECT public_state FROM faq_revision WHERE id=:id FOR UPDATE"
            ), {"id": revision_id}).first()
            if not row:
                raise LookupError("FAQ_REVISION_NOT_FOUND")
            connection.execute(text("""
                UPDATE faq_revision SET public_state=:state, reviewed_by=:actor,
                    reviewed_at=CURRENT_TIMESTAMP WHERE id=:id
            """), {"id": revision_id, "state": state, "actor": actor_id})
            updated = connection.execute(text(
                "SELECT * FROM faq_revision WHERE id=:id"
            ), {"id": revision_id}).mappings().one()
        return self._row(updated) or {}

    def save_release(self, release: dict[str, Any], revision_ids: list[str]) -> dict[str, Any]:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO faq_release
                    (id, release_id, version, status, manifest, manifest_sha256,
                     previous_release_id, form_release_id, created_by)
                VALUES
                    (:id, :release_id, :version, :status, CAST(:manifest AS JSONB),
                     :manifest_sha256, :previous_release_id, :form_release_id, :created_by)
            """), {**release, "manifest": _canonical_json(release["manifest"])})
            for index, revision_id in enumerate(revision_ids):
                connection.execute(text("""
                    INSERT INTO faq_release_item
                        (id, release_ref, faq_revision_ref, display_order)
                    VALUES (:id, :release, :revision, :position)
                """), {
                    "id": f"{release['id']}:item:{index}",
                    "release": release["id"],
                    "revision": revision_id,
                    "position": index,
                })
        return self.get_release(release["id"]) or {}

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT * FROM faq_release WHERE id=:id OR release_id=:id"
            ), {"id": release_id}).mappings().first()
            if not row:
                return None
            revision_ids = list(connection.execute(text("""
                SELECT faq_revision_ref FROM faq_release_item
                WHERE release_ref=:id ORDER BY display_order, id
            """), {"id": row["id"]}).scalars())
        item = self._row(row) or {}
        item["revision_ids"] = revision_ids
        return item

    def update_release(self, release_id: str, values: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text

        allowed = {"status", "manifest", "manifest_sha256"}
        changed = {key: value for key, value in values.items() if key in allowed}
        if not changed:
            return self.get_release(release_id) or {}
        assignments = []
        params: dict[str, Any] = {"id": release_id}
        for key, value in changed.items():
            assignments.append(f"{key}=CAST(:{key} AS JSONB)" if key == "manifest" else f"{key}=:{key}")
            params[key] = _canonical_json(value) if key == "manifest" else value
        with self.engine.begin() as connection:
            if not connection.execute(text(
                "SELECT 1 FROM faq_release WHERE id=:id FOR UPDATE"
            ), {"id": release_id}).first():
                raise LookupError("FAQ_RELEASE_NOT_FOUND")
            connection.execute(text(
                f"UPDATE faq_release SET {', '.join(assignments)} WHERE id=:id"
            ), params)
        return self.get_release(release_id) or {}

    def set_active_release(self, release_id: str, *, actor_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            release = connection.execute(text(
                "SELECT * FROM faq_release WHERE id=:id FOR UPDATE"
            ), {"id": release_id}).mappings().first()
            if not release:
                raise LookupError("FAQ_RELEASE_NOT_FOUND")
            pointer = connection.execute(text("""
                SELECT release_ref, pointer_version FROM faq_active_release
                WHERE pointer_key='faq-catalog' FOR UPDATE
            """)).mappings().first()
            if pointer and pointer["release_ref"] != release_id:
                connection.execute(text(
                    "UPDATE faq_release SET status='retired' WHERE id=:id"
                ), {"id": pointer["release_ref"]})
            pointer_version = int((pointer or {}).get("pointer_version") or 0) + 1
            connection.execute(text("""
                INSERT INTO faq_active_release
                    (pointer_key, release_ref, pointer_version, updated_by)
                VALUES ('faq-catalog', :release, :version, :actor)
                ON CONFLICT (pointer_key) DO UPDATE SET
                    release_ref=EXCLUDED.release_ref,
                    pointer_version=EXCLUDED.pointer_version,
                    updated_by=EXCLUDED.updated_by,
                    updated_at=CURRENT_TIMESTAMP
            """), {"release": release_id, "version": pointer_version, "actor": actor_id})
            connection.execute(text("""
                UPDATE faq_release SET status='active', activated_by=:actor,
                    activated_at=CURRENT_TIMESTAMP WHERE id=:id
            """), {"id": release_id, "actor": actor_id})
            connection.execute(text("""
                UPDATE faq_revision SET public_state='released', reviewed_by=:actor,
                    reviewed_at=COALESCE(reviewed_at, CURRENT_TIMESTAMP)
                WHERE id IN (
                    SELECT faq_revision_ref FROM faq_release_item WHERE release_ref=:release
                )
            """), {"release": release_id, "actor": actor_id})
        return self.get_release(release_id) or {}

    def active_release(self) -> dict[str, Any] | None:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            release_id = connection.execute(text("""
                SELECT release_ref FROM faq_active_release WHERE pointer_key='faq-catalog'
            """)).scalar_one_or_none()
        return self.get_release(release_id) if release_id else None

    def next_release_version(self) -> int:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            return int(connection.execute(text(
                "SELECT COALESCE(MAX(version),0)+1 FROM faq_release"
            )).scalar_one())


class FaqGovernanceService:
    def __init__(
        self,
        repository: FaqGovernanceRepository,
        *,
        form_release_provider: Callable[[], dict[str, Any] | None],
        today: Callable[[], date] = date.today,
    ) -> None:
        self.repository = repository
        self.form_release_provider = form_release_provider
        self.today = today

    @staticmethod
    def _admin(actor: FaqActor) -> None:
        if actor.role != "admin":
            raise PermissionError("FAQ_ADMIN_REQUIRED")

    @staticmethod
    def _active_form_release(provider: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
        release = provider()
        if not release or release.get("status") != "active":
            raise ValueError("FAQ_FORM_RELEASE_NOT_ACTIVE")
        if not (release.get("manifest") or release).get("release_id"):
            raise ValueError("FAQ_FORM_RELEASE_INVALID")
        return release

    def create_revision(self, actor: FaqActor, payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        if "form_ids" in payload:
            raise ValueError("FAQ_FORM_IDS_NOT_ACCEPTED")
        required = ("question", "answer", "canonical_domain", "confirmed_procedure_id")
        if not all(str(payload.get(key) or "").strip() for key in required):
            raise ValueError("FAQ_REVISION_INCOMPLETE")
        faq_key = str(payload.get("faq_key") or uuid.uuid4().hex)
        evidence = deepcopy(payload.get("evidence") or {})
        evidence["requires_forms"] = bool(payload.get("requires_forms"))
        for key in ("submission_place", "legal_basis", "guidance_label", "steps", "ward_scope"):
            if key in payload:
                evidence[key] = deepcopy(payload[key])
        source = {
            "question": str(payload["question"]).strip(),
            "answer": str(payload["answer"]).strip(),
            "canonical_domain": str(payload["canonical_domain"]).strip(),
            "confirmed_procedure_id": str(payload["confirmed_procedure_id"]).strip(),
            "evidence": evidence,
        }
        source_sha256 = _sha256(source)
        revision = {
            "id": f"faq-revision-{source_sha256[:24]}",
            "faq_ref": f"faq-{_sha256(faq_key)[:24]}",
            **source,
            "public_state": "pending",
            "source_sha256": source_sha256,
            "reviewed_by": None,
            "reviewed_at": None,
        }
        return self.repository.create_revision(faq_key, revision)

    def confirm_revision(self, actor: FaqActor, revision_id: str) -> dict[str, Any]:
        self._admin(actor)
        revision = self.repository.get_revision(revision_id)
        if not revision:
            raise LookupError("FAQ_REVISION_NOT_FOUND")
        if revision["public_state"] not in {"pending", "needs_review", "confirmed"}:
            raise ValueError("FAQ_REVISION_NOT_CONFIRMABLE")
        return self.repository.update_revision_state(revision_id, "confirmed", actor_id=actor.user_id)

    def list_admin(self, actor: FaqActor) -> list[dict[str, Any]]:
        self._admin(actor)
        result = []
        for revision in self.repository.list_revisions():
            evidence = deepcopy(revision.get("evidence") or {})
            result.append({
                "id": revision["faq_ref"],
                "revision_id": revision["id"],
                "revision_number": revision["revision_number"],
                "question": revision["question"],
                "answer": revision["answer"],
                "domain": revision["canonical_domain"],
                "confirmed_procedure_id": revision["confirmed_procedure_id"],
                "public_state": revision["public_state"],
                "requires_forms": bool(evidence.get("requires_forms")),
                "forms": [],
                "form_ids": [],
                "forms_unavailable": False,
                "submission_place": evidence.get("submission_place") or "",
                "legal_basis": evidence.get("legal_basis") or [],
                "guidance_label": evidence.get("guidance_label") or "",
                "steps": evidence.get("steps") or [],
                "ward_scope": evidence.get("ward_scope"),
                "created_at": str(revision.get("created_at") or ""),
                "updated_at": str(revision.get("reviewed_at") or revision.get("created_at") or ""),
                "approved_by": revision.get("reviewed_by"),
            })
        return result

    def build_release(self, actor: FaqActor, revision_ids: list[str]) -> dict[str, Any]:
        self._admin(actor)
        if not revision_ids or len(set(revision_ids)) != len(revision_ids):
            raise ValueError("FAQ_RELEASE_ITEMS_INVALID")
        revisions = []
        for revision_id in revision_ids:
            revision = self.repository.get_revision(revision_id)
            if not revision:
                raise LookupError("FAQ_REVISION_NOT_FOUND")
            if revision["public_state"] != "confirmed":
                raise ValueError("FAQ_REVISION_NOT_CONFIRMED")
            revisions.append(revision)
        form_release = self._active_form_release(self.form_release_provider)
        form_manifest = form_release.get("manifest") or form_release
        active = self.repository.active_release()
        version = self.repository.next_release_version()
        release_id = f"faq-release-v{version}-{uuid.uuid4().hex[:8]}"
        manifest = {
            "schema_version": "faq-release-v1",
            "release_id": release_id,
            "version": version,
            "form_release_id": form_manifest["release_id"],
            "items": [{
                "revision_id": revision["id"],
                "faq_ref": revision["faq_ref"],
                "source_sha256": revision["source_sha256"],
                "confirmed_procedure_id": revision["confirmed_procedure_id"],
            } for revision in revisions],
        }
        release = {
            "id": release_id,
            "release_id": release_id,
            "version": version,
            "status": "candidate",
            "manifest": manifest,
            "manifest_sha256": _sha256(manifest),
            "previous_release_id": (active or {}).get("release_id"),
            "form_release_id": form_manifest["release_id"],
            "created_by": actor.user_id,
            "created_at": _utcnow(),
        }
        return self.repository.save_release(release, revision_ids)

    def _project_revision(
        self,
        revision: dict[str, Any],
        *,
        form_release: dict[str, Any],
        audience: str,
    ) -> dict[str, Any]:
        from api.form_router_v3 import resolve_forms

        evidence = deepcopy(revision.get("evidence") or {})
        requires_forms = bool(evidence.get("requires_forms"))
        forms: list[dict[str, Any]] = []
        forms_unavailable = False
        if requires_forms:
            result = resolve_forms(
                question=str(revision["confirmed_procedure_id"]),
                manifest=form_release.get("manifest") or form_release,
                audience=audience,
                legal_as_of=self.today(),
            )
            if result.get("status") != "resolved":
                forms_unavailable = True
            else:
                forms = list(result.get("recommended_forms") or [])
        return {
            "id": revision["faq_ref"],
            "revision_id": revision["id"],
            "question": revision["question"],
            "answer": revision["answer"],
            "domain": revision["canonical_domain"],
            "confirmed_procedure_id": revision["confirmed_procedure_id"],
            "public_state": "released",
            "review_status": "approved",
            "requires_forms": requires_forms,
            "forms": forms,
            "form_ids": [item["form_id"] for item in forms],
            "forms_unavailable": forms_unavailable,
            "submission_place": evidence.get("submission_place") or "",
            "legal_basis": evidence.get("legal_basis") or [],
            "guidance_label": evidence.get("guidance_label") or "",
            "steps": evidence.get("steps") or [],
            "ward_scope": evidence.get("ward_scope"),
            "created_at": str(revision.get("created_at") or ""),
            "updated_at": str(revision.get("reviewed_at") or revision.get("created_at") or ""),
            "approved_by": revision.get("reviewed_by"),
        }

    def validate_release(self, actor: FaqActor, release_id: str) -> dict[str, Any]:
        self._admin(actor)
        release = self.repository.get_release(release_id)
        if not release:
            raise LookupError("FAQ_RELEASE_NOT_FOUND")
        if release["status"] not in {"candidate", "validated"}:
            raise ValueError("FAQ_RELEASE_NOT_VALIDATABLE")
        form_release = self._active_form_release(self.form_release_provider)
        form_manifest = form_release.get("manifest") or form_release
        errors = []
        if form_manifest.get("release_id") != release["form_release_id"]:
            errors.append("FAQ_FORM_RELEASE_DRIFT")
        projections = []
        for revision_id in release.get("revision_ids") or []:
            revision = self.repository.get_revision(revision_id)
            if not revision or revision.get("public_state") != "confirmed":
                errors.append(f"FAQ_REVISION_NOT_CONFIRMED:{revision_id}")
                continue
            projection = self._project_revision(revision, form_release=form_release, audience="citizen")
            if projection["requires_forms"] and projection["forms_unavailable"]:
                errors.append(f"FAQ_FORMS_UNAVAILABLE:{revision_id}")
            projections.append({
                "revision_id": revision_id,
                "procedure_id": projection["confirmed_procedure_id"],
                "form_fingerprints": [
                    _sha256({
                        "form_id": item.get("form_id"),
                        "checksum": item.get("source_checksum"),
                        "source_url": item.get("source_url"),
                    }) for item in projection["forms"]
                ],
            })
        manifest = deepcopy(release["manifest"])
        manifest["gate_report"] = {
            "passed": not errors,
            "errors": errors,
            "form_release_id": release["form_release_id"],
            "projections": projections,
        }
        status = "validated" if not errors else "blocked"
        return self.repository.update_release(release["id"], {
            "status": status,
            "manifest": manifest,
            "manifest_sha256": _sha256(manifest),
        })

    def activate_release(self, actor: FaqActor, release_id: str) -> dict[str, Any]:
        self._admin(actor)
        release = self.repository.get_release(release_id)
        if not release:
            raise LookupError("FAQ_RELEASE_NOT_FOUND")
        if release["status"] != "validated" or not (release.get("manifest") or {}).get("gate_report", {}).get("passed"):
            raise ValueError("FAQ_RELEASE_NOT_VALIDATED")
        form_release = self._active_form_release(self.form_release_provider)
        current_form_release_id = (form_release.get("manifest") or form_release).get("release_id")
        if current_form_release_id != release["form_release_id"]:
            raise ValueError("FAQ_FORM_RELEASE_DRIFT")
        return self.repository.set_active_release(release["id"], actor_id=actor.user_id)

    def list_public(self, *, audience: str = "citizen") -> list[dict[str, Any]]:
        release = self.repository.active_release()
        if not release or release.get("status") != "active":
            return []
        form_release = self._active_form_release(self.form_release_provider)
        form_release_id = (form_release.get("manifest") or form_release).get("release_id")
        if form_release_id != release.get("form_release_id"):
            return []
        items = []
        for revision_id in release.get("revision_ids") or []:
            revision = self.repository.get_revision(revision_id)
            if revision and revision.get("public_state") == "released":
                items.append(self._project_revision(revision, form_release=form_release, audience=audience))
        return items


@lru_cache(maxsize=1)
def get_faq_governance_service() -> FaqGovernanceService:
    database_url = os.getenv("FAQ_GOVERNANCE_DATABASE_URL") or os.getenv("FEATURE018_DATABASE_URL")
    if not database_url:
        raise RuntimeError("FAQ_GOVERNANCE_DATABASE_URL_REQUIRED")
    from api.form_governance_service import get_form_governance_service

    form_repository = get_form_governance_service().repository
    return FaqGovernanceService(
        PostgresFaqGovernanceRepository(database_url),
        form_release_provider=form_repository.active_release,
    )
