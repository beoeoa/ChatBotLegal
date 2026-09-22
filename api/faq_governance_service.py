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
from pathlib import Path
from typing import Any, Callable, Protocol
from api.faq_public_safety import is_qa_faq


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


def _faq_governance_snapshot_path() -> Path:
    """Return the local recovery snapshot path for the published FAQ catalog."""
    configured = str(os.getenv("FAQ_GOVERNANCE_SNAPSHOT_FILE") or "").strip()
    if configured:
        return Path(configured)
    from api.data_paths import notebook_data_dir

    return notebook_data_dir() / "faq_governance_snapshot.json"


def _snapshot_items_without_stale_forms(items: Any) -> list[dict[str, Any]]:
    """Keep published procedure metadata, but never resurrect stale form assets."""
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("question") or "").strip() or is_qa_faq(item):
            continue
        row = deepcopy(item)
        if row.get("requires_forms"):
            row["forms"] = []
            row["form_ids"] = []
            row["forms_unavailable"] = True
        result.append(row)
    return result


def _load_faq_governance_snapshot() -> list[dict[str, Any]]:
    path = _faq_governance_snapshot_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    items = _snapshot_items_without_stale_forms(payload.get("items"))
    try:
        for receipt_path in path.with_name(path.name + '.deletions').glob('*.json'):
            removed = set(json.loads(receipt_path.read_text(encoding='utf-8'))['revision_ids'])
            items = [item for item in items if item.get('revision_id') not in removed]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    # Old in-flight readers may rewrite an older snapshot after a withdrawal.
    # Append-only release receipts keep those copies from reviving the item.
    try:
        version = int(payload.get("release_version") or 0)
        for receipt_path in path.with_name(path.name + ".withdrawals").glob("*.json"):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if version < int(receipt["release_version"]):
                withdrawn = set(receipt["revision_ids"])
                items = [item for item in items if item.get("revision_id") not in withdrawn]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return items


def _record_snapshot_withdrawal(release: dict[str, Any]) -> None:
    """Fail closed before SQL activation if a durable revocation cannot be saved."""
    withdrawal = (release.get("manifest") or {}).get("withdrawal") or {}
    if not withdrawal.get("revision_ids"):
        return
    path = _faq_governance_snapshot_path()
    directory = path.with_name(path.name + ".withdrawals")
    # Hash internal IDs rather than interpolating them into a filesystem path.
    receipt = directory / (_sha256(release["id"]) + ".json")
    temporary = receipt.with_suffix(".tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(_canonical_json({
            "release_id": release["id"], "release_version": release["version"],
            "revision_ids": withdrawal["revision_ids"], "reason": withdrawal["reason"],
        }), encoding="utf-8")
        temporary.replace(receipt)
    except OSError as exc:
        raise RuntimeError("FAQ_WITHDRAWAL_RECOVERY_UNAVAILABLE") from exc


def _save_faq_governance_snapshot(
    *,
    release: dict[str, Any],
    form_release: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    """Persist the last valid published catalog for database-reset recovery."""
    path = _faq_governance_snapshot_path()
    payload = {
        "schema_version": "faq-governance-snapshot-v1",
        "release_id": release.get("release_id") or release.get("id"),
        "release_version": release.get("version") or 0,
        "form_release_id": (form_release.get("manifest") or form_release).get("release_id"),
        "generated_at": _utcnow().isoformat(),
        "items": deepcopy(items),
    }
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        # Recovery data must never make a successful FAQ read fail.
        return


@dataclass(frozen=True)
class FaqActor:
    user_id: str
    role: str
    managed_procedure_ids: tuple[str, ...] = ()


class FaqGovernanceRepository(Protocol):
    def create_revision(self, faq_key: str, revision: dict[str, Any]) -> dict[str, Any]: ...
    def get_revision(self, revision_id: str) -> dict[str, Any] | None: ...
    def list_revisions(self) -> list[dict[str, Any]]: ...
    def delete_revision(self, revision_id: str) -> None: ...
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
        # The edit form submits the stable identity returned by list_admin.
        faq_key = next((key for key, row in self.identities.items() if row['id'] == faq_key), faq_key)
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
            "id": f"faq-revision-{_sha256([identity['id'], revision['source_sha256']])[:24]}",
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

    def delete_revision(self, revision_id: str) -> None:
        item = self.revisions.get(revision_id)
        if not item:
            raise LookupError("FAQ_REVISION_NOT_FOUND")
        removed = {key for key, row in self.revisions.items() if row['faq_ref'] == item['faq_ref']}
        for key in removed:
            self.revisions.pop(key)
        self.identities = {key: row for key, row in self.identities.items() if row['id'] != item['faq_ref']}
        for release in self.releases.values():
            release['revision_ids'] = [key for key in release.get('revision_ids', []) if key not in removed]
            manifest = release.get('manifest') or {}
            manifest['items'] = [row for row in manifest.get('items', []) if row.get('revision_id') not in removed]
            release['manifest_sha256'] = _sha256(manifest)

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
        if previous != release_id and previous != release.get("previous_release_id"):
            raise ValueError("FAQ_ACTIVE_RELEASE_CHANGED")
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
            existing_key = connection.execute(text(
                "SELECT faq_key FROM faq_identity WHERE id=:key"
            ), {"key": faq_key}).scalar_one_or_none()
            faq_key = existing_key or faq_key
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
            revision = {**revision, "id": f"faq-revision-{_sha256([identity, revision['source_sha256']])[:24]}"}
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

    def delete_revision(self, revision_id: str) -> None:
        from sqlalchemy import text
        with self.engine.begin() as connection:
            connection.execute(text("LOCK TABLE faq_active_release IN EXCLUSIVE MODE"))
            row = connection.execute(text("SELECT faq_ref FROM faq_revision WHERE id=:id FOR UPDATE"), {"id": revision_id}).first()
            if not row:
                raise LookupError("FAQ_REVISION_NOT_FOUND")
            removed = set(connection.execute(text('SELECT id FROM faq_revision WHERE faq_ref=:ref'), {'ref': row[0]}).scalars())
            # Permanent ID-only receipts prevent recovery snapshots from reviving
            # deleted content. No question/answer is retained in the receipt.
            directory = _faq_governance_snapshot_path().with_name(_faq_governance_snapshot_path().name + '.deletions')
            directory.mkdir(parents=True, exist_ok=True)
            receipt = directory / (_sha256(row[0]) + '.json')
            receipt.write_text(_canonical_json({'revision_ids': sorted(removed)}), encoding='utf-8')
            releases = connection.execute(text('SELECT id,manifest FROM faq_release FOR UPDATE')).mappings().all()
            for release in releases:
                manifest = dict(release['manifest'])
                retained = [item for item in manifest.get('items', []) if item.get('revision_id') not in removed]
                if len(retained) != len(manifest.get('items', [])):
                    manifest['items'] = retained
                    connection.execute(text('UPDATE faq_release SET manifest=CAST(:manifest AS JSONB), manifest_sha256=:sha WHERE id=:id'), {'id': release['id'], 'manifest': _canonical_json(manifest), 'sha': _sha256(manifest)})
            connection.execute(text('DELETE FROM faq_release_item WHERE faq_revision_ref IN (SELECT id FROM faq_revision WHERE faq_ref=:ref)'), {'ref': row[0]})
            connection.execute(text('DELETE FROM faq_revision WHERE faq_ref=:ref'), {'ref': row[0]})
            connection.execute(text('DELETE FROM faq_identity WHERE id=:ref'), {'ref': row[0]})

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
            current_id = (pointer or {}).get("release_ref")
            if current_id != release_id and current_id != release.get("previous_release_id"):
                raise ValueError("FAQ_ACTIVE_RELEASE_CHANGED")
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
    def _draft_scope(actor: FaqActor, procedure_id: str) -> None:
        """Allow officers to prepare drafts only for procedures they manage."""
        if actor.role == "admin":
            return
        if actor.role == "officer" and procedure_id in set(actor.managed_procedure_ids):
            return
        raise PermissionError("FAQ_DRAFT_SCOPE_REQUIRED")

    @staticmethod
    def _active_form_release(provider: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
        release = provider()
        if not release or release.get("status") != "active":
            raise ValueError("FAQ_FORM_RELEASE_NOT_ACTIVE")
        if not (release.get("manifest") or release).get("release_id"):
            raise ValueError("FAQ_FORM_RELEASE_INVALID")
        return release

    def create_revision(self, actor: FaqActor, payload: dict[str, Any]) -> dict[str, Any]:
        if "form_ids" in payload:
            raise ValueError("FAQ_FORM_IDS_NOT_ACCEPTED")
        required = ("question", "answer", "canonical_domain", "confirmed_procedure_id")
        if not all(str(payload.get(key) or "").strip() for key in required):
            raise ValueError("FAQ_REVISION_INCOMPLETE")
        confirmed_procedure_id = str(
            payload.get("confirmed_procedure_id") or ""
        ).strip()
        self._draft_scope(actor, confirmed_procedure_id)
        if confirmed_procedure_id.casefold() in {
            "unresolved", "unknown", "none", "not_found"
        }:
            raise ValueError("FAQ_PROCEDURE_NOT_CONFIRMED")
        form_release = self._active_form_release(self.form_release_provider)
        procedure_ids = {
            str(item.get("procedure_id") or "").strip()
            for item in (form_release.get("manifest") or form_release).get(
                "procedures"
            )
            or []
        }
        if confirmed_procedure_id not in procedure_ids:
            raise ValueError("FAQ_PROCEDURE_NOT_IN_FORM_RELEASE")
        procedure = next(item for item in (form_release.get("manifest") or form_release).get("procedures", [])
                         if str(item.get("procedure_id") or "").strip() == confirmed_procedure_id)
        procedure_domain = str(procedure.get("canonical_domain") or procedure.get("domain") or "").strip()
        if procedure_domain and str(payload.get("canonical_domain") or "").strip() != procedure_domain:
            raise ValueError("FAQ_PROCEDURE_DOMAIN_MISMATCH")
        # Department membership is checked against live organization settings
        # by the write API. Historical release ownership must not override the
        # user's current department/domain assignments. Procedure/domain
        # consistency remains enforced above.
        faq_key = str(payload.get("faq_key") or uuid.uuid4().hex)
        evidence = deepcopy(payload.get("evidence") or {})
        evidence["requires_forms"] = bool(payload.get("requires_forms"))
        for key in (
            "submission_place",
            "legal_basis",
            "guidance_label",
            "steps",
            "documents_required",
            "duration",
            "fee",
            "ward_scope",
        ):
            if key in payload:
                evidence[key] = deepcopy(payload[key])
        if payload.get("organization_unit_id"):
            evidence["organization_unit_id"] = str(payload["organization_unit_id"])
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
        form_release = self._active_form_release(self.form_release_provider)
        manifest = form_release.get("manifest") or form_release
        if not any(str(item.get("procedure_id") or "") == str(revision.get("confirmed_procedure_id") or "")
                   for item in manifest.get("procedures") or []):
            raise ValueError("FAQ_PROCEDURE_NOT_IN_FORM_RELEASE")
        return self.repository.update_revision_state(revision_id, "confirmed", actor_id=actor.user_id)

    def delete_revision(self, actor: FaqActor, revision_id: str) -> None:
        self._admin(actor)
        self.repository.delete_revision(revision_id)

    def list_admin(self, actor: FaqActor) -> list[dict[str, Any]]:
        if actor.role not in {"admin", "officer"}:
            raise PermissionError("FAQ_ADMIN_REQUIRED")
        managed = set(actor.managed_procedure_ids) if actor.role == "officer" else None
        result = []
        for revision in self.repository.list_revisions():
            if managed is not None and str(revision.get("confirmed_procedure_id") or "") not in managed:
                continue
            evidence = deepcopy(revision.get("evidence") or {})
            result.append({
                "id": revision["faq_ref"],
                "revision_id": revision["id"],
                "revision_number": revision["revision_number"],
                "question": revision["question"],
                "answer": revision["answer"],
                "domain": revision["canonical_domain"],
                "primary_organization_unit_id": evidence.get("organization_unit_id"),
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
                "documents_required": evidence.get("documents_required") or [],
                "duration": evidence.get("duration") or "",
                "fee": evidence.get("fee") or "",
                "ward_scope": evidence.get("ward_scope"),
                "created_at": str(revision.get("created_at") or ""),
                "updated_at": str(revision.get("reviewed_at") or revision.get("created_at") or ""),
                "approved_by": revision.get("reviewed_by"),
            })
        return result

    def build_release(
        self, actor: FaqActor, revision_ids: list[str], *,
        withdraw_revision_ids: list[str] | None = None,
        withdrawal_reason: str = "",
        expected_active_release_id: str | None = None,
    ) -> dict[str, Any]:
        self._admin(actor)
        withdrawn = list(withdraw_revision_ids or [])
        if (not revision_ids and not withdrawn) or len(set(revision_ids)) != len(revision_ids):
            raise ValueError("FAQ_RELEASE_ITEMS_INVALID")
        if len(set(withdrawn)) != len(withdrawn) or set(withdrawn) & set(revision_ids):
            raise ValueError("FAQ_WITHDRAWAL_ITEMS_INVALID")
        if withdrawn and (len(withdrawal_reason.strip()) < 10 or not expected_active_release_id):
            raise ValueError("FAQ_WITHDRAWAL_REASON_AND_RELEASE_REQUIRED")
        # Releases are additive. Keep the currently active catalog in the next
        # candidate so an admin does not have to re-select every FAQ on each
        # publication. A previously released revision may also be explicitly
        # re-selected to restore it to the active catalog; it still goes
        # through the current release gate and is never auto-published.
        active = self.repository.active_release()
        if expected_active_release_id and expected_active_release_id != (active or {}).get("release_id"):
            raise ValueError("FAQ_ACTIVE_RELEASE_CHANGED")
        inherited_ids = list((active or {}).get("revision_ids") or [])
        if not set(withdrawn).issubset(inherited_ids):
            raise ValueError("FAQ_WITHDRAWAL_NOT_ACTIVE")
        ordered_ids = [value for value in inherited_ids if value not in withdrawn] + [
            revision_id for revision_id in revision_ids if revision_id not in inherited_ids
        ]
        revisions = []
        for revision_id in ordered_ids:
            revision = self.repository.get_revision(revision_id)
            if not revision:
                raise LookupError("FAQ_REVISION_NOT_FOUND")
            if revision["public_state"] not in {"released", "confirmed"}:
                raise ValueError("FAQ_REVISION_NOT_CONFIRMED")
            revisions.append(revision)
        form_release = self._active_form_release(self.form_release_provider)
        form_manifest = form_release.get("manifest") or form_release
        version = self.repository.next_release_version()
        release_id = f"faq-release-v{version}-{uuid.uuid4().hex[:8]}"
        manifest = {
            "schema_version": "faq-release-v1",
            "release_id": release_id,
            "version": version,
            "form_release_id": form_manifest["release_id"],
            "withdrawal": {
                "revision_ids": withdrawn,
                "reason": withdrawal_reason.strip() if withdrawn else "",
                "actor_id": actor.user_id,
                "from_release_id": (active or {}).get("release_id"),
            },
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
        return self.repository.save_release(release, ordered_ids)

    def _project_revision(
        self,
        revision: dict[str, Any],
        *,
        form_release: dict[str, Any],
        audience: str,
    ) -> dict[str, Any]:
        from api.form_router_v3 import resolve_forms

        evidence = deepcopy(revision.get("evidence") or {})
        form_manifest = form_release.get("manifest") or form_release
        procedure = next(
            (
                item
                for item in form_manifest.get("procedures") or []
                if str(item.get("procedure_id") or "")
                == str(revision["confirmed_procedure_id"])
            ),
            {},
        )
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
            # Inherited from the release-bound procedure. FAQ has no separate
            # department writer, which prevents release drift.
            "primary_organization_unit_id": procedure.get(
                "primary_organization_unit_id"
            ) or evidence.get("organization_unit_id"),
            "organization_unit_id": evidence.get("organization_unit_id"),
            "supporting_organization_unit_ids": list(
                procedure.get("supporting_organization_unit_ids") or []
            ),
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
            "documents_required": evidence.get("documents_required") or [],
            "duration": evidence.get("duration") or "",
            "fee": evidence.get("fee") or "",
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
        active_release = self.repository.active_release()
        inherited_ids = set((active_release or {}).get("revision_ids") or [])
        for revision_id in release.get("revision_ids") or []:
            revision = self.repository.get_revision(revision_id)
            allowed_states = {"confirmed", "released"}
            if not revision or revision.get("public_state") not in allowed_states:
                errors.append(f"FAQ_REVISION_NOT_CONFIRMED:{revision_id}")
                continue
            procedure_id = str(
                revision.get("confirmed_procedure_id") or ""
            ).strip()
            if not any(
                str(item.get("procedure_id") or "").strip() == procedure_id
                for item in form_manifest.get("procedures") or []
            ):
                errors.append(
                    f"FAQ_PROCEDURE_NOT_IN_FORM_RELEASE:{revision_id}"
                )
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
        if isinstance(self.repository, PostgresFaqGovernanceRepository):
            _record_snapshot_withdrawal(release)
        result = self.repository.set_active_release(release["id"], actor_id=actor.user_id)
        if isinstance(self.repository, PostgresFaqGovernanceRepository):
            self.list_public()
        return result

    def list_public(self, *, audience: str = "citizen") -> list[dict[str, Any]]:
        try:
            release = self.repository.active_release()
        except Exception:
            if isinstance(self.repository, PostgresFaqGovernanceRepository):
                return _load_faq_governance_snapshot()
            raise
        if not release or release.get("status") != "active":
            if isinstance(self.repository, PostgresFaqGovernanceRepository):
                return _load_faq_governance_snapshot()
            return []
        try:
            form_release = self._active_form_release(self.form_release_provider)
        except (LookupError, RuntimeError, ValueError):
            if isinstance(self.repository, PostgresFaqGovernanceRepository):
                return _load_faq_governance_snapshot()
            return []
        form_release_id = (form_release.get("manifest") or form_release).get("release_id")
        if form_release_id != release.get("form_release_id"):
            if isinstance(self.repository, PostgresFaqGovernanceRepository):
                return _load_faq_governance_snapshot()
            return []
        items = []
        for revision_id in release.get("revision_ids") or []:
            revision = self.repository.get_revision(revision_id)
            if revision and revision.get("public_state") == "released" and not is_qa_faq(revision):
                items.append(self._project_revision(revision, form_release=form_release, audience=audience))
        if isinstance(self.repository, PostgresFaqGovernanceRepository):
            _save_faq_governance_snapshot(
                release=release,
                form_release=form_release,
                items=items,
            )
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
