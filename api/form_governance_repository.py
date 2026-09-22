"""Persistence boundary for Feature 017.

PostgreSQL is the only writable legal source.  The memory implementation is
limited to isolated tests; JSON compatibility is read-only by design.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

from api.form_governance_models import (
    canonical_sha256,
    FormReviewCase,
    FormWorkflowStatus,
    WorkflowEvent,
    utc_now,
)


class FormGovernanceRepository(Protocol):
    def create_case(self, case: FormReviewCase) -> FormReviewCase: ...
    def get_case(self, case_id: str) -> FormReviewCase | None: ...
    def save_case(self, case: FormReviewCase, *, expected_version: int) -> FormReviewCase: ...
    def list_cases(self) -> list[FormReviewCase]: ...
    def append_event(self, event: WorkflowEvent) -> None: ...
    def events(self, object_id: str) -> list[WorkflowEvent]: ...
    def save_release(self, release: dict[str, Any]) -> dict[str, Any]: ...
    def update_release(self, release: dict[str, Any]) -> dict[str, Any]: ...
    def get_release(self, release_id: str) -> dict[str, Any] | None: ...
    def set_active_release(self, release_id: str, *, actor_id: str) -> dict[str, Any]: ...
    def active_release(self) -> dict[str, Any] | None: ...
    def latest_pending_release(self) -> dict[str, Any] | None: ...
    def latest_release_version(self) -> int: ...
    def enqueue_notification(self, notification: dict[str, Any]) -> None: ...
    def save_verified_gap(self, gap: dict[str, Any]) -> dict[str, Any]: ...
    def list_verified_gaps(self) -> list[dict[str, Any]]: ...


class InMemoryFormGovernanceRepository:
    """Deterministic repository for tests and isolated rehearsals only."""

    def __init__(self) -> None:
        self._cases: dict[str, FormReviewCase] = {}
        self._events: list[WorkflowEvent] = []
        self._releases: dict[str, dict[str, Any]] = {}
        self._active_release_id: str | None = None
        self.notifications: list[dict[str, Any]] = []
        self._gaps: list[dict[str, Any]] = []

    def create_case(self, case: FormReviewCase) -> FormReviewCase:
        if case.case_id in self._cases:
            raise ValueError("FORM_CASE_CONFLICT")
        self._cases[case.case_id] = case.model_copy(deep=True)
        return case.model_copy(deep=True)

    def get_case(self, case_id: str) -> FormReviewCase | None:
        value = self._cases.get(case_id)
        return value.model_copy(deep=True) if value else None

    def save_case(self, case: FormReviewCase, *, expected_version: int) -> FormReviewCase:
        current = self._cases.get(case.case_id)
        if not current:
            raise ValueError("FORM_CASE_NOT_FOUND")
        if current.version != expected_version:
            raise ValueError("FORM_CASE_VERSION_CONFLICT")
        saved = case.model_copy(update={"version": expected_version + 1}, deep=True)
        self._cases[case.case_id] = saved
        return saved.model_copy(deep=True)

    def list_cases(self) -> list[FormReviewCase]:
        return [item.model_copy(deep=True) for item in self._cases.values()]

    def append_event(self, event: WorkflowEvent) -> None:
        self._events.append(event.model_copy(deep=True))

    def events(self, object_id: str) -> list[WorkflowEvent]:
        return [item.model_copy(deep=True) for item in self._events if item.object_id == object_id]

    def save_release(self, release: dict[str, Any]) -> dict[str, Any]:
        release_id = str(release["release_id"])
        if release_id in self._releases:
            raise ValueError("FORM_RELEASE_CONFLICT")
        self._releases[release_id] = deepcopy(release)
        return deepcopy(release)

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        value = self._releases.get(release_id)
        return deepcopy(value) if value else None

    def update_release(self, release: dict[str, Any]) -> dict[str, Any]:
        release_id = str(release["release_id"])
        if release_id not in self._releases: raise ValueError("FORM_RELEASE_NOT_FOUND")
        self._releases[release_id] = deepcopy(release); return deepcopy(release)

    def set_active_release(self, release_id: str, *, actor_id: str) -> dict[str, Any]:
        release = self._releases.get(release_id)
        if not release:
            raise ValueError("FORM_RELEASE_NOT_FOUND")
        if release.get("status") != "validated":
            raise ValueError("FORM_RELEASE_GATE_FAILED")
        previous = self._active_release_id
        if previous and previous in self._releases:
            self._releases[previous]["status"] = "retired"
        release["status"] = "active"
        self._active_release_id = release_id
        case_ids = list((release.get("manifest") or {}).get("build", {}).get("case_ids") or [])
        for case_id in case_ids:
            case = self._cases.get(str(case_id))
            if case and case.status.value == "release_candidate":
                self._cases[str(case_id)] = case.model_copy(
                    update={
                        "status": FormWorkflowStatus.RELEASED,
                        "updated_at": utc_now(),
                        "version": case.version + 1,
                    }
                )
        return {"release_id": release_id, "previous_release_id": previous, "updated_by": actor_id}

    def active_release(self) -> dict[str, Any] | None:
        return self.get_release(self._active_release_id) if self._active_release_id else None

    def latest_pending_release(self) -> dict[str, Any] | None:
        pending = [
            item for item in self._releases.values()
            if item.get("status") in {"candidate", "validated", "blocked"}
        ]
        return deepcopy(max(pending, key=lambda item: int(item.get("version") or 0))) if pending else None

    def latest_release_version(self) -> int:
        return max((int(item.get("version") or 0) for item in self._releases.values()), default=0)

    def enqueue_notification(self, notification: dict[str, Any]) -> None:
        self.notifications.append(deepcopy(notification))

    def save_verified_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        if any(x["target_type"] == gap["target_type"] and x["target_id"] == gap["target_id"] and x["legal_as_of"] == gap["legal_as_of"] for x in self._gaps):
            raise ValueError("FORM_VERIFIED_GAP_CONFLICT")
        self._gaps.append(deepcopy(gap)); return deepcopy(gap)

    def list_verified_gaps(self) -> list[dict[str, Any]]: return deepcopy(self._gaps)


class ReadOnlyJsonCompatibilityRepository:
    """Read-only marker used until PostgreSQL migration/activation is approved."""

    def _blocked(self, *_args, **_kwargs):
        raise RuntimeError("FORM_SCHEMA_UNAVAILABLE: JSON compatibility is read-only")

    create_case = save_case = append_event = save_release = update_release = set_active_release = enqueue_notification = save_verified_gap = _blocked

    def get_case(self, _case_id: str):
        return None

    def list_cases(self):
        return []

    def events(self, _object_id: str):
        return []

    def get_release(self, _release_id: str):
        return None

    def active_release(self):
        return None

    def latest_pending_release(self):
        return None

    def latest_release_version(self) -> int:
        return 0

    def list_verified_gaps(self): return []


class PostgresFormGovernanceRepository:
    """Lazy SQLAlchemy repository for the separately approved PostgreSQL schema."""

    def __init__(self, database_url: str | None = None) -> None:
        url = str(
            database_url
            or os.getenv("LEGAL_DATABASE_URL")
            or os.getenv("LEGAL_RELEASE_DATABASE_URL")
            or ""
        ).strip()
        if not url:
            raise RuntimeError(
                "FORM_SCHEMA_UNAVAILABLE: LEGAL_DATABASE_URL/LEGAL_RELEASE_DATABASE_URL missing"
            )
        from sqlalchemy import create_engine

        self.engine = create_engine(url, future=True, pool_pre_ping=True)

    def _require_shared_faq_release_database(self) -> None:
        """Fail closed when production FAQ and form pointers cannot be atomic."""

        if str(os.getenv("FAQ_GOVERNANCE_MODE") or "disabled").casefold() != "postgres_active":
            return
        faq_url = str(
            os.getenv("FAQ_GOVERNANCE_DATABASE_URL")
            or os.getenv("FEATURE018_DATABASE_URL")
            or ""
        ).strip()
        if not faq_url:
            raise RuntimeError("CATALOG_RELEASE_DATABASE_NOT_SHARED")
        from sqlalchemy.engine import make_url

        def identity(value) -> tuple[Any, ...]:
            url = make_url(str(value))
            return (
                url.host or "",
                int(url.port or 5432),
                url.database or "",
                url.username or "",
            )

        if identity(self.engine.url) != identity(faq_url):
            raise RuntimeError("CATALOG_RELEASE_DATABASE_NOT_SHARED")

    @staticmethod
    def _case_params(case: FormReviewCase) -> dict[str, Any]:
        import json

        data = case.model_dump(mode="json")
        return {**data, "current_submission": json.dumps(data["current_submission"], ensure_ascii=False), "legal_metadata": json.dumps(data["legal_metadata"], ensure_ascii=False)}

    def create_case(self, case: FormReviewCase) -> FormReviewCase:
        from sqlalchemy import text

        p = self._case_params(case)
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO form_review_case
                (id,case_id,officer_id,domain,proposed_procedure_id,title,status,current_revision,legal_metadata,version,created_at,updated_at)
                VALUES (:case_id,:case_id,:officer_id,:domain,:procedure_id,:title,:status,:revision,CAST(:legal_metadata AS JSONB),:version,:created_at,:updated_at)"""), p)
            c.execute(text("""INSERT INTO form_review_revision
                (id,review_case_ref,revision_number,source_url,source_sha256,submission,submitted_by,submitted_at)
                VALUES (:rid,:case_id,:revision,:source_url,:source_checksum,CAST(:submission AS JSONB),:officer_id,:created_at)"""), {
                "rid": f"{case.case_id}:r{case.revision}", "case_id": case.case_id, "revision": case.revision,
                "source_url": case.current_submission.source_url, "source_checksum": case.current_submission.source_checksum,
                "submission": p["current_submission"], "officer_id": case.officer_id, "created_at": case.created_at,
            })
        return case

    def _row_case(self, row: Any) -> FormReviewCase:
        value = dict(row._mapping if hasattr(row, "_mapping") else row)
        return FormReviewCase(
            case_id=value["case_id"], officer_id=value["officer_id"], domain=value["domain"],
            procedure_id=value["proposed_procedure_id"], title=value["title"], status=value["status"],
            revision=value["current_revision"], current_submission=value.get("submission") or {},
            source_reviewed_by=value.get("source_reviewed_by"), source_reviewed_at=value.get("source_reviewed_at"),
            legal_metadata=value.get("legal_metadata") or {}, attestation_fingerprint=value.get("attestation_sha256"),
            attested_by=value.get("attested_by"), attested_at=value.get("attested_at"),
            created_at=value["created_at"], updated_at=value["updated_at"], version=value["version"],
        )

    def get_case(self, case_id: str) -> FormReviewCase | None:
        from sqlalchemy import text
        with self.engine.connect() as c:
            row = c.execute(text("""SELECT c.*, r.submission FROM form_review_case c JOIN form_review_revision r
              ON r.review_case_ref=c.id AND r.revision_number=c.current_revision WHERE c.case_id=:id"""), {"id": case_id}).first()
        return self._row_case(row) if row else None

    def list_cases(self) -> list[FormReviewCase]:
        from sqlalchemy import text
        with self.engine.connect() as c:
            rows = c.execute(text("""SELECT c.*, r.submission FROM form_review_case c JOIN form_review_revision r
              ON r.review_case_ref=c.id AND r.revision_number=c.current_revision ORDER BY c.updated_at DESC""")).all()
        return [self._row_case(row) for row in rows]

    def save_case(self, case: FormReviewCase, *, expected_version: int) -> FormReviewCase:
        import json
        from sqlalchemy import text
        with self.engine.begin() as c:
            result = c.execute(text("""UPDATE form_review_case SET status=:status,current_revision=:revision,
              title=:title,domain=:domain,proposed_procedure_id=:procedure_id,
              source_reviewed_by=:source_reviewed_by,source_reviewed_at=:source_reviewed_at,
              legal_metadata=CAST(:legal_metadata AS JSONB),attestation_sha256=:attestation,
              attested_by=:attested_by,attested_at=:attested_at,
              updated_at=:updated_at,version=version+1
              WHERE case_id=:case_id AND version=:expected"""), {
                  "status": case.status.value,
                  "title": case.title, "domain": case.domain, "procedure_id": case.procedure_id,
                  "revision": case.revision,
                  "source_reviewed_by": case.source_reviewed_by,
                  "source_reviewed_at": case.source_reviewed_at,
                  "legal_metadata": json.dumps(case.legal_metadata, ensure_ascii=False),
                  "attestation": case.attestation_fingerprint,
                  "attested_by": case.attested_by,
                  "attested_at": case.attested_at,
                  "updated_at": case.updated_at,
                  "case_id": case.case_id,
                  "expected": expected_version,
              })
            if result.rowcount != 1: raise ValueError("FORM_CASE_VERSION_CONFLICT")
            existing = c.execute(text("SELECT 1 FROM form_review_revision WHERE review_case_ref=:id AND revision_number=:rev"), {"id":case.case_id,"rev":case.revision}).first()
            if not existing:
                submission = case.current_submission.model_dump(mode="json")
                c.execute(text("""INSERT INTO form_review_revision
                  (id,review_case_ref,revision_number,source_url,source_sha256,submission,submitted_by,submitted_at)
                  VALUES (:rid,:id,:rev,:url,:sha,CAST(:submission AS JSONB),:by,:at)"""), {"rid":f"{case.case_id}:r{case.revision}","id":case.case_id,"rev":case.revision,"url":case.current_submission.source_url,"sha":case.current_submission.source_checksum,"submission":json.dumps(submission,ensure_ascii=False),"by":case.officer_id,"at":case.updated_at})
        return case.model_copy(update={"version": expected_version + 1})

    def append_event(self, event: WorkflowEvent) -> None:
        from sqlalchemy import text
        from api.form_governance_models import canonical_sha256
        with self.engine.begin() as c:
            # Locking the table also serializes the empty-chain case; selecting
            # the last row alone cannot lock a row when the table is empty.
            c.execute(text("LOCK TABLE form_workflow_event IN EXCLUSIVE MODE"))
            previous = c.execute(text("SELECT entry_hash FROM form_workflow_event ORDER BY event_sequence DESC LIMIT 1 FOR UPDATE")).scalar()
            entry_hash = canonical_sha256({"previous_hash":previous,"event":event.model_dump(mode="json")})
            c.execute(text("""INSERT INTO form_workflow_event
              (id,object_type,object_id,actor_id,actor_role,action,from_status,to_status,reason_code,detail_sha256,previous_hash,entry_hash,occurred_at)
              VALUES (:id,:ot,:oid,:aid,:role,:action,:before,:after,:reason,:detail,:previous,:entry,:at)"""), {"id":event.event_id,"ot":event.object_type,"oid":event.object_id,"aid":event.actor_id,"role":event.actor_role,"action":event.action,"before":event.from_status.value if event.from_status else None,"after":event.to_status.value if event.to_status else None,"reason":event.reason_code,"detail":event.detail_hash,"previous":previous,"entry":entry_hash,"at":event.occurred_at})

    def events(self, object_id: str) -> list[WorkflowEvent]:
        from sqlalchemy import text
        with self.engine.connect() as c: rows=c.execute(text("SELECT * FROM form_workflow_event WHERE object_id=:id ORDER BY event_sequence"),{"id":object_id}).mappings().all()
        return [WorkflowEvent(event_id=x["id"],object_type=x["object_type"],object_id=x["object_id"],actor_id=x["actor_id"],actor_role=x["actor_role"],action=x["action"],from_status=x["from_status"],to_status=x["to_status"],reason_code=x["reason_code"],detail_hash=x["detail_sha256"],occurred_at=x["occurred_at"]) for x in rows]

    def save_release(self, release: dict[str, Any]) -> dict[str, Any]:
        import json
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO form_release
              (id,release_id,version,legal_as_of,status,source_snapshot_sha256,manifest,manifest_sha256,previous_release_id,created_by)
              VALUES (:id,:id,:version,:asof,:status,:source,CAST(:manifest AS JSONB),:hash,:previous,:by)"""), {"id":release["release_id"],"version":release["version"],"asof":release["legal_as_of"],"status":release["status"],"source":release["source_snapshot_sha256"],"manifest":json.dumps(release["manifest"],ensure_ascii=False),"hash":release["manifest_sha256"],"previous":release.get("previous_release_id"),"by":release["created_by"]})
        return deepcopy(release)

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text
        with self.engine.connect() as c: row=c.execute(text("SELECT * FROM form_release WHERE release_id=:id"),{"id":release_id}).mappings().first()
        if not row: return None
        return {**dict(row), **dict(row["manifest"]), "manifest":dict(row["manifest"])}

    def update_release(self, release: dict[str, Any]) -> dict[str, Any]:
        import json
        from sqlalchemy import text
        with self.engine.begin() as c:
            result=c.execute(text("""UPDATE form_release SET status=:status,gate_report=CAST(:report AS JSONB),
              gate_report_sha256=:hash WHERE release_id=:id AND status IN ('candidate','blocked','validated','retired')"""),{"status":release["status"],"report":json.dumps(release.get("gate_report") or {},ensure_ascii=False),"hash":__import__('hashlib').sha256(json.dumps(release.get("gate_report") or {},sort_keys=True).encode()).hexdigest(),"id":release["release_id"]})
            if result.rowcount != 1: raise ValueError("FORM_RELEASE_STATE_CONFLICT")
        return deepcopy(release)

    @staticmethod
    def _materialize_release_catalog(connection, release_id: str, manifest: dict[str, Any], actor_id: str) -> None:
        """Create immutable, release-versioned SQL projections before pointer swap."""
        import json
        from sqlalchemy import text
        from api.legal_form_catalog import fold_text

        procedure_refs: dict[str, str] = {}
        for item in manifest.get("procedures") or []:
            procedure_id = str(item["procedure_id"])
            row_id = f"{release_id}:procedure:{canonical_sha256(procedure_id)[:24]}"
            procedure_refs[procedure_id] = row_id
            connection.execute(text("""INSERT INTO legal_procedure
              (id,release_ref,procedure_id,procedure_code,canonical_name,domain,authority,jurisdiction,
               official_source_url,official_source_sha256,effective_from,effective_to,legal_as_of,
               coverage_status,coverage_reason,primary_organization_unit_id,
              supporting_organization_unit_ids)
              VALUES (:id,:release,:procedure_id,:code,:name,:domain,:authority,:jurisdiction,
               :url,:checksum,:effective_from,:effective_to,:legal_as_of,:coverage,:reason,
               :primary_unit,CAST(:supporting_units AS JSONB))
              ON CONFLICT (release_ref,procedure_id) DO NOTHING"""), {
                "id": row_id,
                "release": release_id,
                "procedure_id": procedure_id,
                "code": item.get("procedure_code"),
                "name": item.get("name") or item.get("canonical_name") or procedure_id,
                "domain": item.get("domain") or "unknown",
                "authority": item.get("authority") or "Chưa xác định",
                "jurisdiction": item.get("jurisdiction") or "Hai Phong",
                "url": item.get("official_source_url"),
                "checksum": item.get("official_source_checksum"),
                "effective_from": item.get("effective_from"),
                "effective_to": item.get("effective_to"),
                "legal_as_of": manifest["legal_as_of"],
                "coverage": item.get("coverage_status") or "unresolved",
                "reason": item.get("coverage_reason"),
                "primary_unit": item.get("primary_organization_unit_id"),
                "supporting_units": json.dumps(
                    item.get("supporting_organization_unit_ids") or [],
                    ensure_ascii=False,
                ),
            })

        asset_refs: dict[str, str] = {}
        for item in manifest.get("assets") or []:
            form_id = str(item["form_id"])
            row_id = f"{release_id}:asset:{canonical_sha256(form_id)[:24]}"
            asset_refs[form_id] = row_id
            identity_sha256 = canonical_sha256({
                "form_code": item.get("form_code"),
                "canonical_name": item.get("canonical_name"),
                "issuing_instrument": item.get("issuing_instrument"),
                "source_checksum": item.get("source_checksum"),
            })
            connection.execute(text("""INSERT INTO legal_form_asset
              (id,release_ref,form_id,form_code,canonical_name,asset_kind,source_url,
               source_classification,source_sha256,identity_sha256,issuing_instrument,
               effective_from,effective_to,audiences,coverage_status)
              VALUES (:id,:release,:form_id,:code,:name,:kind,:url,:classification,:checksum,
               :identity,:instrument,:effective_from,:effective_to,CAST(:audiences AS JSONB),:coverage)
              ON CONFLICT (release_ref,form_id) DO NOTHING"""), {
                "id": row_id,
                "release": release_id,
                "form_id": form_id,
                "code": item.get("form_code"),
                "name": item.get("canonical_name") or form_id,
                "kind": item.get("asset_kind"),
                "url": item.get("source_url"),
                "classification": item.get("source_classification") or "official",
                "checksum": item.get("source_checksum"),
                "identity": identity_sha256,
                "instrument": item.get("issuing_instrument"),
                "effective_from": item.get("effective_from"),
                "effective_to": item.get("effective_to"),
                "audiences": json.dumps(item.get("audiences") or [], ensure_ascii=False),
                "coverage": item.get("coverage_status") or "unresolved",
            })

        for item in manifest.get("bindings") or []:
            binding_id = str(item["binding_id"])
            procedure_id = str(item.get("procedure_id") or "")
            form_id = str(item.get("form_id") or "")
            connection.execute(text("""INSERT INTO procedure_form_binding
              (id,release_ref,procedure_ref,form_asset_ref,requirement,condition_text,
               condition_sha256,audience,display_order,legal_basis,coverage_status,
               effective_from,effective_to)
              VALUES (:id,:release,:procedure,:asset,:requirement,:condition,:condition_hash,
               :audience,:display_order,:legal_basis,:coverage,:effective_from,:effective_to)
              ON CONFLICT (id) DO NOTHING"""), {
                "id": f"{release_id}:binding:{binding_id}",
                "release": release_id,
                "procedure": procedure_refs.get(procedure_id),
                "asset": asset_refs.get(form_id) if form_id else None,
                "requirement": item.get("requirement"),
                "condition": item.get("condition"),
                "condition_hash": canonical_sha256(str(item.get("condition") or "")),
                "audience": item.get("audience"),
                "display_order": int(item.get("display_order") or 0),
                "legal_basis": item.get("legal_basis"),
                "coverage": item.get("coverage_status") or "unresolved",
                "effective_from": item.get("effective_from"),
                "effective_to": item.get("effective_to"),
            })

        for index, item in enumerate(manifest.get("aliases") or []):
            alias = str(item.get("alias") or "").strip()
            procedure_id = str(item.get("procedure_id") or "")
            alias_kind = str(item.get("alias_kind") or "natural")
            alias_sha256 = canonical_sha256(alias)
            connection.execute(text("""INSERT INTO procedure_question_alias
              (id,release_ref,procedure_ref,alias_text,alias_folded,alias_sha256,
               alias_kind,review_status,reviewed_by)
              VALUES (:id,:release,:procedure,:alias,:folded,:hash,:kind,'approved',:reviewer)
              ON CONFLICT (id) DO NOTHING"""), {
                "id": f"{release_id}:alias:{index}:{alias_sha256[:16]}",
                "release": release_id,
                "procedure": procedure_refs.get(procedure_id),
                "alias": alias,
                "folded": fold_text(alias),
                "hash": alias_sha256,
                "kind": alias_kind,
                "reviewer": actor_id,
            })

        expected = {
            "legal_procedure": len(manifest.get("procedures") or []),
            "legal_form_asset": len(manifest.get("assets") or []),
            "procedure_form_binding": len(manifest.get("bindings") or []),
            "procedure_question_alias": len(manifest.get("aliases") or []),
        }
        for table, count in expected.items():
            observed = int(connection.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE release_ref=:release"),
                {"release": release_id},
            ).scalar_one())
            if observed != count:
                raise ValueError(f"FORM_RELEASE_PROJECTION_MISMATCH:{table}")

    @staticmethod
    def _republish_active_faq_for_form_release(
        connection,
        *,
        form_release_id: str,
        form_manifest: dict[str, Any],
        actor_id: str,
    ) -> str | None:
        """Rebind the public FAQ catalog inside the form pointer transaction.

        FAQ content remains immutable. This creates a new FAQ release carrying
        the active revisions, validates them against the candidate form
        manifest and swaps both pointers in the same PostgreSQL transaction.
        """

        import hashlib
        import json
        import uuid
        from datetime import date

        from sqlalchemy import text

        required_tables = (
            "faq_release",
            "faq_release_item",
            "faq_active_release",
            "faq_revision",
        )
        available = all(
            connection.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": table}
            ).scalar_one()
            for table in required_tables
        )
        if not available:
            if str(os.getenv("FAQ_GOVERNANCE_MODE") or "disabled").casefold() == "postgres_active":
                raise RuntimeError("CATALOG_RELEASE_DATABASE_NOT_SHARED")
            return None

        pointer = connection.execute(text("""
            SELECT release_ref, pointer_version FROM faq_active_release
            WHERE pointer_key='faq-catalog' FOR UPDATE
        """)).mappings().first()
        if not pointer:
            return None
        current = connection.execute(
            text("SELECT * FROM faq_release WHERE id=:id FOR UPDATE"),
            {"id": pointer["release_ref"]},
        ).mappings().one()
        if str(current.get("form_release_id") or "") == form_release_id:
            return str(current["release_id"])

        revisions = connection.execute(text("""
            SELECT revision.* FROM faq_release_item item
            JOIN faq_revision revision ON revision.id=item.faq_revision_ref
            WHERE item.release_ref=:release
            ORDER BY item.display_order, item.id
            FOR UPDATE OF revision
        """), {"release": current["id"]}).mappings().all()
        procedures = {
            str(item.get("procedure_id") or ""): item
            for item in form_manifest.get("procedures") or []
        }
        errors: list[str] = []
        projections: list[dict[str, Any]] = []
        eligible_revisions: list[Any] = []
        excluded_revisions: list[dict[str, str]] = []
        legal_as_of = date.fromisoformat(str(form_manifest["legal_as_of"])[:10])
        from api.form_router_v3 import resolve_forms

        for revision in revisions:
            revision_id = str(revision["id"])
            procedure_id = str(revision.get("confirmed_procedure_id") or "")
            if revision.get("public_state") not in {"confirmed", "released"}:
                errors.append(f"FAQ_REVISION_NOT_CONFIRMED:{revision_id}")
                continue
            if procedure_id not in procedures:
                excluded_revisions.append({
                    "revision_id": revision_id,
                    "confirmed_procedure_id": procedure_id,
                    "reason_code": "FAQ_PROCEDURE_NOT_IN_FORM_RELEASE",
                })
                continue
            evidence = revision.get("evidence") or {}
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            form_fingerprints: list[str] = []
            if bool(evidence.get("requires_forms")):
                projected = resolve_forms(
                    question=procedure_id,
                    manifest=form_manifest,
                    audience="citizen",
                    legal_as_of=legal_as_of,
                )
                if projected.get("status") != "resolved":
                    errors.append(f"FAQ_FORMS_UNAVAILABLE:{revision_id}")
                form_fingerprints = [
                    canonical_sha256({
                        "form_id": item.get("form_id"),
                        "checksum": item.get("source_checksum"),
                        "source_url": item.get("source_url"),
                    })
                    for item in projected.get("recommended_forms") or []
                ]
            projections.append({
                "revision_id": revision_id,
                "procedure_id": procedure_id,
                "primary_organization_unit_id": procedures[procedure_id].get(
                    "primary_organization_unit_id"
                ),
                "form_fingerprints": form_fingerprints,
            })
            eligible_revisions.append(revision)
        if errors:
            raise ValueError("CATALOG_RELEASE_FAQ_GATE_FAILED:" + ",".join(errors))
        if revisions and not eligible_revisions:
            raise ValueError(
                "CATALOG_RELEASE_FAQ_GATE_FAILED:FAQ_NO_ELIGIBLE_REVISION"
            )

        version = int(connection.execute(
            text("SELECT COALESCE(MAX(version),0)+1 FROM faq_release")
        ).scalar_one())
        faq_release_id = f"faq-release-v{version}-{uuid.uuid4().hex[:8]}"
        manifest = {
            "schema_version": "faq-release-v1",
            "release_id": faq_release_id,
            "version": version,
            "form_release_id": form_release_id,
            "items": [{
                "revision_id": str(revision["id"]),
                "faq_ref": str(revision["faq_ref"]),
                "source_sha256": str(revision["source_sha256"]),
                "confirmed_procedure_id": str(
                    revision["confirmed_procedure_id"]
                ),
            } for revision in eligible_revisions],
            "gate_report": {
                "passed": True,
                "errors": [],
                "form_release_id": form_release_id,
                "projections": projections,
                "excluded_revisions": excluded_revisions,
                "excluded_revision_count": len(excluded_revisions),
                "activation_mode": "atomic_form_republish",
            },
        }
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        manifest_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        connection.execute(text("""
            INSERT INTO faq_release
                (id,release_id,version,status,manifest,manifest_sha256,
                 previous_release_id,form_release_id,created_by,activated_by,
                 activated_at)
            VALUES (:id,:id,:version,'active',CAST(:manifest AS JSONB),:sha,
                    :previous,:form_release,:actor,:actor,CURRENT_TIMESTAMP)
        """), {
            "id": faq_release_id,
            "version": version,
            "manifest": canonical,
            "sha": manifest_sha256,
            "previous": current["release_id"],
            "form_release": form_release_id,
            "actor": actor_id,
        })
        for index, revision in enumerate(eligible_revisions):
            connection.execute(text("""
                INSERT INTO faq_release_item
                    (id,release_ref,faq_revision_ref,display_order)
                VALUES (:id,:release,:revision,:position)
            """), {
                "id": f"{faq_release_id}:item:{index}",
                "release": faq_release_id,
                "revision": revision["id"],
                "position": index,
            })
        connection.execute(
            text("UPDATE faq_release SET status='retired' WHERE id=:id"),
            {"id": current["id"]},
        )
        connection.execute(text("""
            UPDATE faq_active_release SET release_ref=:release,
                pointer_version=:version,updated_by=:actor,
                updated_at=CURRENT_TIMESTAMP
            WHERE pointer_key='faq-catalog'
        """), {
            "release": faq_release_id,
            "version": int(pointer["pointer_version"]) + 1,
            "actor": actor_id,
        })
        if eligible_revisions:
            connection.execute(text("""
                UPDATE faq_revision SET public_state='released',reviewed_by=:actor,
                    reviewed_at=COALESCE(reviewed_at,CURRENT_TIMESTAMP)
                WHERE id = ANY(:revision_ids)
            """), {
                "actor": actor_id,
                "revision_ids": [str(row["id"]) for row in eligible_revisions],
            })
        if excluded_revisions:
            connection.execute(text("""
                UPDATE faq_revision SET public_state='needs_review',
                    reviewed_by=:actor,reviewed_at=CURRENT_TIMESTAMP
                WHERE id = ANY(:revision_ids)
            """), {
                "actor": actor_id,
                "revision_ids": [
                    row["revision_id"] for row in excluded_revisions
                ],
            })
        return faq_release_id

    def set_active_release(self, release_id: str, *, actor_id: str) -> dict[str, Any]:
        from sqlalchemy import text
        self._require_shared_faq_release_database()
        with self.engine.begin() as c:
            release=c.execute(text("SELECT id,status,manifest FROM form_release WHERE release_id=:id FOR UPDATE"),{"id":release_id}).mappings().first()
            if not release: raise ValueError("FORM_RELEASE_NOT_FOUND")
            if release["status"] != "validated": raise ValueError("FORM_RELEASE_GATE_FAILED")
            # Serialize publication before checking its base and case versions.
            # Service checks alone cannot prevent two concurrent admins publishing.
            c.execute(text("SELECT pg_advisory_xact_lock(17092026)"))
            current_row = c.execute(text("""
                SELECT r.release_id, r.manifest
                FROM form_active_release p
                JOIN form_release r ON r.id=p.release_ref
                WHERE p.pointer_key='forms-catalog'
                FOR UPDATE OF p
            """)).mappings().first()
            current_id = current_row["release_id"] if current_row else None
            manifest = dict(release["manifest"])
            # Normal publication advances from the active pointer.  A rollback
            # is allowed only to that pointer's immediate previous release;
            # this mirrors the in-memory repository and prevents an older,
            # non-adjacent release from bypassing the release chain.
            rolling_back = bool(
                current_row
                and (current_row.get("manifest") or {}).get("previous_release_id") == release_id
            )
            if manifest.get("previous_release_id") != current_id and not rolling_back:
                raise ValueError("FORM_RELEASE_BASE_STALE")
            expected_versions = manifest.get("build", {}).get("case_versions", {})
            for case_id in manifest.get("build", {}).get("case_ids") or []:
                case = c.execute(text("SELECT status,version,attestation_sha256 FROM form_review_case WHERE case_id=:id FOR UPDATE"), {"id": case_id}).mappings().first()
                if not case or case["status"] != "release_candidate" or not case["attestation_sha256"] or (case_id in expected_versions and case["version"] != expected_versions[case_id]):
                    raise ValueError("FORM_ATTESTATION_STALE")
            self._materialize_release_catalog(c, release_id, dict(release["manifest"]), actor_id)
            faq_release_id = self._republish_active_faq_for_form_release(
                c,
                form_release_id=release_id,
                form_manifest=dict(release["manifest"]),
                actor_id=actor_id,
            )
            pointer=c.execute(text("SELECT release_ref,pointer_version FROM form_active_release WHERE pointer_key='forms-catalog' FOR UPDATE")).mappings().first()
            previous=pointer["release_ref"] if pointer else None; version=int(pointer["pointer_version"])+1 if pointer else 1
            if previous: c.execute(text("UPDATE form_release SET status='retired' WHERE id=:id"),{"id":previous})
            c.execute(text("UPDATE form_release SET status='active',activated_by=:by,activated_at=CURRENT_TIMESTAMP WHERE id=:id"),{"by":actor_id,"id":release["id"]})
            case_ids = [str(value) for value in (release["manifest"].get("build", {}).get("case_ids") or [])]
            if case_ids:
                c.execute(text("""UPDATE form_review_case
                  SET status='released',updated_at=CURRENT_TIMESTAMP,version=version+1
                  WHERE case_id = ANY(:case_ids) AND status='release_candidate'"""), {"case_ids": case_ids})
            c.execute(text("""INSERT INTO form_active_release(pointer_key,release_ref,pointer_version,updated_by)
              VALUES ('forms-catalog',:release,:version,:by) ON CONFLICT(pointer_key) DO UPDATE SET
              release_ref=EXCLUDED.release_ref,pointer_version=EXCLUDED.pointer_version,updated_by=EXCLUDED.updated_by,updated_at=CURRENT_TIMESTAMP"""),{"release":release["id"],"version":version,"by":actor_id})
        return {"release_id":release_id,"previous_release_id":previous,"updated_by":actor_id,"pointer_version":version,"faq_release_id":faq_release_id,"activation_mode":"atomic_catalog_bundle" if faq_release_id else "forms_only"}

    def active_release(self) -> dict[str, Any] | None:
        from sqlalchemy import text
        with self.engine.connect() as c: row=c.execute(text("""SELECT r.* FROM form_active_release p JOIN form_release r ON r.id=p.release_ref WHERE p.pointer_key='forms-catalog'""")).mappings().first()
        if not row: return None
        return {**dict(row), **dict(row["manifest"]), "manifest":dict(row["manifest"])}

    def latest_pending_release(self) -> dict[str, Any] | None:
        from sqlalchemy import text
        with self.engine.connect() as c:
            row = c.execute(text("""
                SELECT * FROM form_release
                WHERE status IN ('candidate', 'validated', 'blocked')
                ORDER BY version DESC LIMIT 1
            """)).mappings().first()
        if not row:
            return None
        return {**dict(row), **dict(row["manifest"]), "manifest": dict(row["manifest"])}

    def latest_release_version(self) -> int:
        from sqlalchemy import text
        with self.engine.connect() as c:
            value = c.execute(text("SELECT COALESCE(MAX(version), 0) FROM form_release")).scalar_one()
        return int(value or 0)

    def enqueue_notification(self, notification: dict[str, Any]) -> None:
        import json, uuid
        from sqlalchemy import text
        event_id=str(notification.get("workflow_event_id") or "")
        if not event_id:
            # Notification projections without an event reference remain
            # optional; the workflow event is the recoverable source.
            return
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO form_notification_outbox
              (id,workflow_event_ref,recipient_id,recipient_role,public_payload)
              VALUES (:id,:event,:recipient,'officer',CAST(:payload AS JSONB))"""),{"id":f"out-{uuid.uuid4().hex}","event":event_id,"recipient":notification["recipient_id"],"payload":json.dumps(notification,ensure_ascii=False)})

    def save_verified_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO form_source_gap
              (id,target_type,target_id,reason_code,evidence_source_url,evidence_sha256,metadata,legal_as_of,verified_by)
              VALUES (:gap_id,:target_type,:target_id,:reason_code,:evidence_source_url,:evidence_sha256,
               CAST(:metadata AS JSONB),:legal_as_of,:verified_by)"""),{
                  **gap,
                  "metadata": __import__("json").dumps(gap.get("metadata") or {}, ensure_ascii=False),
              })
        return deepcopy(gap)

    def list_verified_gaps(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self.engine.connect() as c: return [dict(x) for x in c.execute(text("SELECT * FROM form_source_gap ORDER BY target_type,target_id")).mappings().all()]


def configured_repository() -> FormGovernanceRepository:
    source = str(os.getenv("FORM_GOVERNANCE_SOURCE") or "json_compat").casefold()
    if source in {"postgres_shadow", "postgres_active"}:
        return PostgresFormGovernanceRepository()
    return ReadOnlyJsonCompatibilityRepository()
