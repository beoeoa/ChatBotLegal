"""Persistence and fast snapshot projection for legal validity sync.

Operational observations live outside the imported legal corpus.  The compact
snapshot is atomically replaced only after an observation batch is persisted,
so retrieval never needs a database or network call on its critical path.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import get_ident
from typing import Any, Awaitable, Callable, Iterable, Mapping

from api.data_paths import notebook_data_dir
from api.legal_validity_models import (
    EvidenceStatus,
    IdentityStatus,
    LegalValidityObservation,
    NormalizedValidityStatus,
    normalize_provisions,
    serving_decision,
    vietnam_legal_date,
)
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_query,
    repo_update,
)

SNAPSHOT_PATH = notebook_data_dir() / "operations" / "legal-validity-serving.json"
SNAPSHOT_SCHEMA = "legal-validity-serving-v1"

QueryFn = Callable[..., Awaitable[list[dict[str, Any]]]]
CreateFn = Callable[[str, dict[str, Any]], Awaitable[Any]]
UpdateFn = Callable[[str, Any, dict[str, Any]], Awaitable[Any]]


def _row(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return dict(value) if isinstance(value, Mapping) else None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_identifier(value: Any) -> str | None:
    """Return a stable JSON-safe identifier for SurrealDB records or strings."""
    if value is None:
        return None
    table_name = getattr(value, "table_name", None)
    record_id = getattr(value, "id", None)
    if table_name and record_id is not None:
        return f"{table_name}:{record_id}"
    text = str(value).strip()
    return text or None


def _event_type(previous: Mapping[str, Any] | None, current: LegalValidityObservation) -> str:
    if current.identity_status is not IdentityStatus.EXACT:
        return "identity_conflict"
    if previous is None:
        return "status_changed"
    if previous.get("normalized_status") != current.normalized_status.value:
        return "status_changed"
    if previous.get("effective_from") != current.effective_from or previous.get("effective_to") != current.effective_to:
        return "dates_changed"
    return "partial_scope_changed"


def _severity(observation: LegalValidityObservation) -> str:
    if observation.normalized_status in {
        NormalizedValidityStatus.EXPIRED,
        NormalizedValidityStatus.SUSPENDED,
        NormalizedValidityStatus.REPLACED,
        NormalizedValidityStatus.REPEALED,
    }:
        return "critical"
    if observation.normalized_status in {
        NormalizedValidityStatus.EXPIRED_PARTIAL,
        NormalizedValidityStatus.SUSPENDED_PARTIAL,
        NormalizedValidityStatus.AMENDED,
    }:
        return "high"
    if observation.identity_status is not IdentityStatus.EXACT or observation.evidence_status is not EvidenceStatus.SUFFICIENT:
        return "medium"
    return "low"


def _serving_action(observation: LegalValidityObservation, as_of: date, mode: str) -> str:
    partial = observation.normalized_status in {
        NormalizedValidityStatus.EXPIRED_PARTIAL,
        NormalizedValidityStatus.SUSPENDED_PARTIAL,
        NormalizedValidityStatus.AMENDED,
    }
    if partial and observation.affected_provisions:
        return "block_provisions" if mode != "observe" else "allow"
    return serving_decision(observation, as_of=as_of, mode=mode).serving_action


class ValiditySnapshotCache:
    """mtime-aware cache that keeps the last valid snapshot on file corruption."""

    def __init__(self, path: Path = SNAPSHOT_PATH) -> None:
        self.path = Path(path)
        self._mtime_ns: int | None = None
        self._snapshot: dict[str, Any] | None = None

    def load(self) -> dict[str, Any] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return self._snapshot
        if self._snapshot is not None and stat.st_mtime_ns == self._mtime_ns:
            return self._snapshot
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != SNAPSHOT_SCHEMA:
                raise ValueError("invalid validity snapshot schema")
            if not isinstance(payload.get("documents"), dict):
                raise ValueError("invalid validity snapshot documents")
        except (OSError, ValueError, json.JSONDecodeError):
            return self._snapshot
        self._snapshot = payload
        self._mtime_ns = stat.st_mtime_ns
        return self._snapshot


class LegalValidityRegistry:
    def __init__(
        self,
        *,
        query: QueryFn = repo_query,
        create: CreateFn = repo_create,
        update: UpdateFn = repo_update,
        snapshot_path: Path = SNAPSHOT_PATH,
    ) -> None:
        self.query = query
        self.create = create
        self.update = update
        self.snapshot_path = Path(snapshot_path)

    async def record_observation(
        self,
        observation: LegalValidityObservation,
        *,
        scope: str | None = None,
        document_title: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.query(
            "SELECT * FROM legal_validity_observation WHERE observation_key = $observation_key LIMIT 1;",
            {"observation_key": observation.observation_key},
        )
        if existing:
            return {"created": False, "observation": existing[0], "event": None}

        previous_rows = []
        if observation.document_id:
            previous_rows = await self.query(
                "SELECT * FROM legal_validity_observation WHERE document_id = $document_id ORDER BY observed_at DESC LIMIT 1;",
                {"document_id": observation.document_id},
            )
        previous = previous_rows[0] if previous_rows else None
        payload = observation.to_dict()
        payload.update(
            {
                "scope": str(scope or "").strip().casefold() or None,
                "document_title": str(document_title or "").strip() or None,
            }
        )
        try:
            created = _row(await self.create("legal_validity_observation", payload))
        except Exception:
            # A concurrent worker may have inserted the unique observation.
            raced = await self.query(
                "SELECT * FROM legal_validity_observation WHERE observation_key = $observation_key LIMIT 1;",
                {"observation_key": observation.observation_key},
            )
            if raced:
                return {"created": False, "observation": raced[0], "event": None}
            raise
        if created is None:
            raise RuntimeError("validity_observation_not_persisted")

        needs_event = bool(
            previous
            or observation.normalized_status is not NormalizedValidityStatus.ACTIVE
            or observation.identity_status is not IdentityStatus.EXACT
            or observation.evidence_status is not EvidenceStatus.SUFFICIENT
        )
        event = None
        if needs_event:
            previous_fingerprint = str((previous or {}).get("fingerprint") or "none")
            event_key = hashlib.sha256(
                f"{observation.document_id}|{previous_fingerprint}|{observation.fingerprint}".encode("utf-8")
            ).hexdigest()
            prior_event = await self.query(
                "SELECT * FROM legal_validity_event WHERE event_key = $event_key LIMIT 1;",
                {"event_key": event_key},
            )
            if prior_event:
                event = prior_event[0]
            else:
                event_payload = {
                    "event_key": event_key,
                    "document_id": observation.document_id,
                    "law_number": observation.law_number,
                    "document_title": payload.get("document_title"),
                    "issuing_agency": observation.issuing_agency,
                    "issued_date": observation.issued_date,
                    "scope": payload.get("scope"),
                    "previous_observation": (previous or {}).get("id"),
                    "current_observation": created.get("id"),
                    "previous_fingerprint": previous_fingerprint,
                    "current_fingerprint": observation.fingerprint,
                    "event_type": _event_type(previous, observation),
                    "severity": _severity(observation),
                    "review_status": "open",
                    "serving_action": _serving_action(
                        observation, observation.observed_at.date(), "protect"
                    ),
                    "source_url": observation.source_url,
                    "raw_status": observation.raw_status,
                    "normalized_status": observation.normalized_status.value,
                    "effective_from": observation.effective_from,
                    "effective_to": observation.effective_to,
                    "affected_provisions": [
                        item.to_dict() for item in observation.affected_provisions
                    ],
                    "created_at": observation.observed_at,
                    "updated_at": observation.observed_at,
                }
                event = _row(await self.create("legal_validity_event", event_payload))
        return {"created": True, "observation": created, "event": event}

    async def latest_observations(
        self,
        *,
        page_size: int = 2000,
        max_records: int = 100_000,
        limit: int | None = None,
    ) -> list[LegalValidityObservation]:
        """Read the complete persisted observation history in bounded pages.

        ``limit`` is retained as a backwards-compatible total cap.  Snapshot
        rebuilds intentionally use the much larger ``max_records`` default so
        an older observation cannot silently disappear merely because it fell
        outside the newest database page.
        """

        bounded_page_size = max(1, min(int(page_size), 5000))
        bounded_max_records = max(1, int(max_records))
        if limit is not None:
            bounded_max_records = min(bounded_max_records, max(1, int(limit)))

        rows: list[dict[str, Any]] = []
        offset = 0
        previous_page_signature: tuple[str, str, int] | None = None
        while offset < bounded_max_records:
            requested = min(bounded_page_size, bounded_max_records - offset)
            page = await self.query(
                "SELECT * FROM legal_validity_observation "
                "ORDER BY observed_at DESC LIMIT $limit START $offset;",
                {"limit": requested, "offset": offset},
            )
            page = [dict(row) for row in page or [] if isinstance(row, Mapping)]
            if not page:
                break

            # A small number of legacy query adapters ignored START.  Detect a
            # repeated page so a degraded adapter cannot create an endless loop.
            first_key = str(page[0].get("id") or page[0].get("observation_key") or "")
            last_key = str(page[-1].get("id") or page[-1].get("observation_key") or "")
            page_signature = (first_key, last_key, len(page))
            if page_signature == previous_page_signature:
                break
            previous_page_signature = page_signature

            rows.extend(page)
            offset += len(page)
            if len(page) < requested:
                break

        # A law number is not a stable document identity: the official corpus
        # may contain two instruments with the same number (for example a
        # Law and a National Assembly Resolution), and imported source rows
        # may be duplicated under different internal ids.  Keep the latest
        # observation per (law_number, document_id) so one row cannot
        # silently overwrite another in the serving projection.
        seen: set[tuple[str, str]] = set()
        result: list[LegalValidityObservation] = []
        for row in rows:
            law_number = str(row.get("law_number") or "")
            document_id = str(row.get("document_id") or "")
            identity = (law_number, document_id)
            if identity in seen:
                continue
            try:
                observation = LegalValidityObservation.from_dict(row)
            except (TypeError, ValueError):
                continue
            seen.add(identity)
            result.append(observation)
        return result

    async def rejected_fingerprints(self) -> set[str]:
        rows = await self.query(
            "SELECT current_fingerprint FROM legal_validity_event WHERE review_status = 'rejected_match';"
        )
        return {
            str(row.get("current_fingerprint") or "")
            for row in rows or []
            if str(row.get("current_fingerprint") or "")
        }

    async def latest_serving_observations(
        self,
        *,
        page_size: int = 2000,
        max_records: int = 100_000,
        limit: int | None = None,
        document_ids: Iterable[str] | None = None,
    ) -> list[LegalValidityObservation]:
        observations = await self.latest_observations(
            page_size=page_size,
            max_records=max_records,
            limit=limit,
        )
        rejected = await self.rejected_fingerprints()
        allowed_ids = {str(value).strip() for value in (document_ids or ()) if str(value).strip()}
        return [
            item for item in observations
            if item.fingerprint not in rejected
            and (not allowed_ids or str(item.document_id or "").strip() in allowed_ids)
        ]

    async def serving_action_overrides(self) -> dict[str, str]:
        """Return the latest audited Admin serving choice per legal instrument."""

        rows = await self.query(
            "SELECT document_id, review_status, updated_at, created_at "
            "FROM legal_validity_event WHERE review_status IN "
            "['historical_release_pending', 'quarantine_release_pending'] "
            "ORDER BY updated_at DESC, created_at DESC;"
        )
        overrides: dict[str, str] = {}
        for row in rows or []:
            document_id = str(row.get("document_id") or "").strip()
            if not document_id or document_id in overrides:
                continue
            overrides[document_id] = (
                "block"
                if row.get("review_status") == "quarantine_release_pending"
                else "historical_only"
            )
        return overrides

    def write_snapshot(
        self,
        *,
        observations: Iterable[LegalValidityObservation],
        last_success_at: datetime,
        eligible_count: int,
        mode: str,
        eligible_document_ids: Iterable[str] | None = None,
        release_id: str | None = None,
        manifest_sha256: str | None = None,
        serving_action_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        mode = mode if mode in {"observe", "protect", "strict"} else "protect"
        allowed_ids = {
            str(value).strip()
            for value in (eligible_document_ids or ())
            if str(value).strip()
        }
        latest: dict[str, LegalValidityObservation] = {}
        for observation in observations:
            document_id = str(observation.document_id or "").strip()
            if allowed_ids and document_id not in allowed_ids:
                continue
            identity = document_id or f"unmapped:{observation.fingerprint}"
            current = latest.get(identity)
            if current is None or observation.observed_at > current.observed_at:
                latest[identity] = observation

        documents: dict[str, dict[str, Any]] = {}
        document_ids: dict[str, str] = {}
        legacy_number_keys: dict[str, list[str]] = {}
        for _identity, observation in sorted(latest.items()):
            document_id = str(observation.document_id or "").strip()
            entry_key = (
                f"document:{document_id}"
                if document_id
                else f"unmapped:{observation.fingerprint[:16]}"
            )
            action = _serving_action(
                observation, vietnam_legal_date(last_success_at), mode
            )
            override = (serving_action_overrides or {}).get(document_id)
            if override in {"historical_only", "block"}:
                action = override
            base_decision = serving_decision(
                observation, as_of=vietnam_legal_date(last_success_at), mode=mode
            )
            documents[entry_key] = {
                "document_id": observation.document_id,
                "law_number": observation.law_number,
                    "normalized_status": observation.normalized_status.value,
                    "serving_action": action,
                    "source_url": observation.source_url,
                    "verified_at": (
                        observation.verified_at or observation.observed_at
                    ).isoformat(),
                    "verified_by": observation.verified_by,
                    "source_kind": observation.source_kind,
                    "raw_status": observation.raw_status,
                    "effective_from": observation.effective_from,
                    "effective_to": observation.effective_to,
                    "affected_provisions": [
                        item.to_dict() for item in observation.affected_provisions
                    ],
                    "identity_status": observation.identity_status.value,
                    "evidence_status": observation.evidence_status.value,
                    "warning_code": base_decision.warning_code,
                    "reason_code": base_decision.reason_code,
                    "fingerprint": observation.fingerprint,
            }
            if document_id:
                document_ids[document_id] = entry_key
            legacy_number_keys.setdefault(observation.law_number, []).append(entry_key)
        # Read compatibility only. Canonical projections use document_ids;
        # ambiguous numbers intentionally receive no alias.
        for law_number, keys in legacy_number_keys.items():
            if law_number and len(keys) == 1:
                documents[law_number] = {
                    **documents[keys[0]],
                    "compatibility_alias": True,
                }
        observed_count = len(latest)
        payload = {
            "schema_version": SNAPSHOT_SCHEMA,
            "generated_at": now.isoformat(),
            "last_success_at": last_success_at.astimezone(timezone.utc).isoformat(),
            "mode": mode,
            "release_id": release_id,
            "manifest_sha256": manifest_sha256,
            "eligible_document_ids": sorted(allowed_ids) if allowed_ids else None,
            "coverage": {
                "eligible": max(0, int(eligible_count)),
                "observed": observed_count,
                "fresh": observed_count,
            },
            "documents": documents,
            "document_ids": document_ids,
        }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.{os.getpid()}.{get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Windows can briefly deny a replace while another process is
            # reading the previous snapshot.  Keep the atomic same-directory
            # replace, but tolerate that short sharing-window contention.
            for attempt in range(4):
                try:
                    os.replace(temporary, self.snapshot_path)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(0.025 * (attempt + 1))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return payload

    async def refresh_snapshot_projection(
        self,
        *,
        additional_document_ids: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """Rebuild the serving projection after an Admin mapping decision."""

        try:
            snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(snapshot, Mapping):
            return None
        last_success_at = _as_datetime(snapshot.get("last_success_at"))
        if last_success_at is None:
            return None
        coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), Mapping) else {}
        eligible_document_ids = {
            str(value or "").strip()
            for value in (snapshot.get("eligible_document_ids") or ())
            if str(value or "").strip()
        }
        eligible_document_ids.update(
            str(value or "").strip()
            for value in (additional_document_ids or ())
            if str(value or "").strip()
        )
        observations = await self.latest_serving_observations(
            document_ids=sorted(eligible_document_ids) or None
        )
        overrides = await self.serving_action_overrides()
        return self.write_snapshot(
            observations=observations,
            last_success_at=last_success_at,
            eligible_count=(
                len(eligible_document_ids)
                if eligible_document_ids
                else int(coverage.get("eligible") or len(observations))
            ),
            mode=str(snapshot.get("mode") or "protect"),
            eligible_document_ids=sorted(eligible_document_ids) or None,
            release_id=str(snapshot.get("release_id") or "") or None,
            manifest_sha256=str(snapshot.get("manifest_sha256") or "") or None,
            serving_action_overrides=overrides,
        )

    @staticmethod
    def snapshot_health(
        snapshot: Mapping[str, Any] | None,
        *,
        now: datetime,
        stale_after_seconds: float,
    ) -> dict[str, Any]:
        if not snapshot:
            return {"status": "missing", "reason_code": "validity_snapshot_unavailable"}
        last_success = _as_datetime(snapshot.get("last_success_at"))
        if last_success is None:
            return {"status": "degraded", "reason_code": "validity_snapshot_invalid_time"}
        age_seconds = max(0.0, (now.astimezone(timezone.utc) - last_success).total_seconds())
        if age_seconds > max(60.0, float(stale_after_seconds)):
            return {
                "status": "stale",
                "reason_code": "validity_snapshot_stale",
                "age_seconds": age_seconds,
            }
        return {"status": "healthy", "reason_code": None, "age_seconds": age_seconds}

    async def acquire_lease(
        self,
        *,
        owner: str,
        now: datetime,
        ttl_seconds: float,
        name: str = "global",
    ) -> bool:
        expires_at = now + timedelta(seconds=max(30.0, float(ttl_seconds)))
        rows = await self.query(
            "SELECT * FROM legal_validity_sync_lease WHERE name = $name LIMIT 1;",
            {"name": name},
        )
        if rows:
            lease = rows[0]
            current_expiry = _as_datetime(lease.get("expires_at"))
            if str(lease.get("owner") or "") != owner and current_expiry and current_expiry > now:
                return False
            updated = await self.update(
                "legal_validity_sync_lease",
                lease["id"],
                {"owner": owner, "acquired_at": now, "expires_at": expires_at},
            )
            return bool(updated)
        try:
            created = await self.create(
                "legal_validity_sync_lease",
                {"name": name, "owner": owner, "acquired_at": now, "expires_at": expires_at},
            )
        except Exception:
            return False
        return _row(created) is not None

    async def release_lease(
        self, *, owner: str, now: datetime, name: str = "global"
    ) -> None:
        rows = await self.query(
            "SELECT * FROM legal_validity_sync_lease WHERE name = $name LIMIT 1;",
            {"name": name},
        )
        if rows and str(rows[0].get("owner") or "") == owner:
            await self.update(
                "legal_validity_sync_lease",
                rows[0]["id"],
                {"expires_at": now, "released_at": now},
            )

    async def record_decision(
        self,
        *,
        event_id: str,
        action: str,
        reason: str,
        actor_user_id: str,
        serving_confirmation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        status_by_action = {
            "confirm_mapping": "confirmed",
            "reject_match": "rejected_match",
            "request_recheck": "recheck_requested",
            # Legacy callers without a SQL receipt only request a future
            # serving decision. The active API supplies confirmation below.
            "mark_historical": "historical_release_pending",
            "quarantine": "quarantine_release_pending",
        }
        if action not in status_by_action:
            raise ValueError("invalid_validity_decision")
        clean_reason = str(reason or "").strip()
        if not 10 <= len(clean_reason) <= 2000:
            raise ValueError("validity_decision_reason_required")
        event_ref = ensure_record_id(event_id)
        events = await self.query(
            "SELECT * FROM legal_validity_event WHERE id = $id LIMIT 1;",
            {"id": event_ref},
        )
        if not events and str(event_ref) != event_id:
            # Test doubles and older repository adapters may retain the raw
            # ``table:id`` string instead of the client's RecordID object.
            events = await self.query(
                "SELECT * FROM legal_validity_event WHERE id = $id LIMIT 1;",
                {"id": event_id},
            )
        if not events:
            raise LookupError("legal_validity_event_not_found")
        event = events[0]
        event_record_id = event.get("id") or event_ref
        confirmed_action = None
        if serving_confirmation is not None:
            from api.legal_document_serving_state import document_id
            confirmed_action = {"mark_historical": "historical_only", "quarantine": "block"}.get(action)
            if (
                confirmed_action is None
                or serving_confirmation.get("serving_action") != confirmed_action
                or serving_confirmation.get("search_included") is not False
                or document_id(serving_confirmation.get("document_id")) != document_id(event.get("document_id"))
            ):
                raise ValueError("invalid_serving_confirmation")
            # Reuse the existing confirmed status/schema. It means the SQL
            # serving choice was applied, not a new assertion of legal expiry.
            status_by_action[action] = "confirmed"
        replay_key = hashlib.sha256(
            f"{event_id}|{action}|{clean_reason}|{actor_user_id}".encode("utf-8")
        ).hexdigest()
        replay = await self.query(
            "SELECT * FROM legal_validity_decision WHERE replay_key = $replay_key LIMIT 1;",
            {"replay_key": replay_key},
        )
        if replay:
            if confirmed_action:
                await self.update("legal_validity_event", event_record_id, {
                    "review_status": "confirmed", "serving_action": confirmed_action,
                    "updated_at": datetime.now(timezone.utc),
                })
            if action in {"confirm_mapping", "reject_match", "mark_historical", "quarantine"}:
                await self.refresh_snapshot_projection()
            return self._decision_projection(replay[0])
        now = datetime.now(timezone.utc)
        new_status = status_by_action[action]
        decision = _row(
            await self.create(
                "legal_validity_decision",
                {
                    "replay_key": replay_key,
                    "event": ensure_record_id(str(event_record_id)),
                    "action": action,
                    "reason": clean_reason,
                    "actor_user_id": actor_user_id,
                    "previous_review_status": event.get("review_status") or "open",
                    "new_review_status": new_status,
                    "created_at": now,
                },
            )
        )
        if decision is None:
            raise RuntimeError("validity_decision_not_persisted")
        await self.update(
            "legal_validity_event",
            event_record_id,
            {"review_status": new_status, "updated_at": now,
             **({"serving_action": confirmed_action} if confirmed_action else {})},
        )
        if action == "confirm_mapping" and event.get("current_observation"):
            await self.update(
                "legal_validity_observation",
                event["current_observation"],
                {"verified_by": actor_user_id, "verified_at": now},
            )
        if action in {"confirm_mapping", "reject_match", "mark_historical", "quarantine"}:
            await self.refresh_snapshot_projection()
        return self._decision_projection(decision)

    async def start_sync_run(
        self,
        *,
        trigger: str,
        requested_by: str | None,
        reason: str | None,
        scopes: tuple[str, ...],
        started_at: datetime,
    ) -> dict[str, Any]:
        created = _row(
            await self.create(
                "legal_validity_sync_run",
                {
                    "trigger": trigger,
                    "requested_by": requested_by,
                    "reason": reason,
                    "status": "running",
                    "scope": list(scopes),
                    "started_at": started_at,
                    "completed_at": None,
                    "documents_scanned": 0,
                    "observations_created": 0,
                    "events_created": 0,
                    "failures": {},
                    "duration_ms": None,
                },
            )
        )
        if created is None:
            raise RuntimeError("validity_sync_run_not_persisted")
        return created

    async def finish_sync_run(
        self, run_id: Any, *, completed_at: datetime, payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        updated = await self.update(
            "legal_validity_sync_run",
            run_id,
            {
                "status": payload.get("status"),
                "completed_at": completed_at,
                "documents_scanned": payload.get("scanned", {}).get("documents", 0),
                "observations_created": payload.get("observations_created", 0),
                "events_created": payload.get("events_created", 0),
                "failures": dict(payload.get("failures") or {}),
                "duration_ms": payload.get("duration_ms"),
            },
        )
        return _row(updated)

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        record_id = event_id if ":" in event_id else f"legal_validity_event:{event_id}"
        rows = await self.query(
            "SELECT * FROM legal_validity_event WHERE id = $id LIMIT 1;",
            {"id": ensure_record_id(record_id)},
        )
        if not rows:
            rows = await self.query(
                "SELECT * FROM legal_validity_event WHERE id = $id LIMIT 1;",
                {"id": record_id},
            )
        return self._event_projection(rows[0]) if rows else None

    @staticmethod
    def _event_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        allowed = (
            "id",
            "document_id",
            "document_title",
            "law_number",
            "issuing_agency",
            "issued_date",
            "scope",
            "previous_observation",
            "current_observation",
            "event_type",
            "severity",
            "review_status",
            "serving_action",
            "source_url",
            "raw_status",
            "normalized_status",
            "effective_from",
            "effective_to",
            "affected_provisions",
            "created_at",
            "updated_at",
        )
        projection = {key: row.get(key) for key in allowed}
        projection["affected_provisions"] = list(
            projection.get("affected_provisions") or []
        )
        for key in (
            "id",
            "document_id",
            "previous_observation",
            "current_observation",
        ):
            projection[key] = _record_identifier(projection.get(key))
        return projection

    @staticmethod
    def _decision_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        allowed = (
            "id",
            "event",
            "action",
            "reason",
            "actor_user_id",
            "previous_review_status",
            "new_review_status",
            "created_at",
        )
        projection = {key: row.get(key) for key in allowed}
        for key in ("id", "event", "actor_user_id"):
            projection[key] = _record_identifier(projection.get(key))
        return projection

    async def list_events(
        self,
        *,
        review_status: str | None = None,
        severity: str | None = None,
        scope: str | None = None,
        expired_within_days: int | None = None,
        document_ids: Iterable[str] | None = None,
        latest_per_document: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        predicates: list[str] = []
        page_limit = min(max(int(limit), 1), 100)
        params: dict[str, Any] = {"limit": page_limit + 1}
        if review_status:
            predicates.append("review_status = $review_status")
            params["review_status"] = review_status
        if severity:
            predicates.append("severity = $severity")
            params["severity"] = severity
        if scope:
            predicates.append("scope = $scope")
            params["scope"] = scope
        if expired_within_days is not None:
            bounded_days = min(max(int(expired_within_days), 1), 365)
            expired_to = vietnam_legal_date()
            expired_from = expired_to - timedelta(days=bounded_days)
            predicates.extend(
                (
                    "normalized_status IN ['expired', 'expired_partial']",
                    "effective_to >= $expired_from",
                    "effective_to <= $expired_to",
                )
            )
            params["expired_from"] = expired_from.isoformat()
            params["expired_to"] = expired_to.isoformat()
        normalized_document_ids = sorted(
            {
                str(value or "").strip()
                for value in (document_ids or ())
                if str(value or "").strip()
            }
        )
        if document_ids is not None:
            predicates.append("document_id IN $document_ids")
            params["document_ids"] = normalized_document_ids or ["__none__"]
        if cursor:
            parsed_cursor = _as_datetime(cursor)
            if parsed_cursor is None:
                raise ValueError("invalid_validity_event_cursor")
            predicates.append("created_at < $cursor")
            params["cursor"] = parsed_cursor
        where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
        if latest_per_document:
            # The canonical worklist normally contains only a handful of
            # current adverse documents. Read enough matching history to
            # collapse repeated observations before applying the UI page size.
            params["limit"] = min(max(page_limit * 20, 200), 2000) + 1
        rows = await self.query(
            f"SELECT * FROM legal_validity_event{where} ORDER BY created_at DESC LIMIT $limit;",
            params,
        )
        candidates = list(rows or [])
        if latest_per_document:
            seen_documents: set[str] = set()
            unique_rows: list[Mapping[str, Any]] = []
            for row in candidates:
                document_id = _record_identifier(row.get("document_id"))
                if not document_id or document_id in seen_documents:
                    continue
                seen_documents.add(document_id)
                unique_rows.append(row)
            candidates = unique_rows
        has_more = len(candidates) > page_limit
        page = candidates[:page_limit]
        next_cursor = None
        if has_more and page:
            created_at = page[-1].get("created_at")
            next_cursor = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or "") or None
        return {
            "items": [self._event_projection(row) for row in page],
            "next_cursor": next_cursor,
        }

    async def document_timeline(self, document_id: str) -> dict[str, Any] | None:
        observations = await self.query(
            "SELECT * FROM legal_validity_observation WHERE document_id = $document_id ORDER BY observed_at DESC LIMIT 200;",
            {"document_id": document_id},
        )
        events = await self.query(
            "SELECT * FROM legal_validity_event WHERE document_id = $document_id ORDER BY created_at DESC LIMIT 200;",
            {"document_id": document_id},
        )
        if not observations and not events:
            return None
        event_ids = [event.get("id") for event in events or [] if event.get("id")]
        decisions = []
        if event_ids:
            decisions = await self.query(
                "SELECT * FROM legal_validity_decision WHERE event IN $event_ids ORDER BY created_at DESC LIMIT 500;",
                {"event_ids": event_ids},
            )
        observation_fields = (
            "id",
            "document_id",
            "document_title",
            "law_number",
            "issuing_agency",
            "issued_date",
            "scope",
            "source_url",
            "source_kind",
            "raw_status",
            "normalized_status",
            "effective_from",
            "effective_to",
            "affecting_document_number",
            "affected_provisions",
            "identity_status",
            "evidence_status",
            "observed_at",
            "source_updated_at",
        )
        first = (observations or events)[0]
        return {
            "document_id": document_id,
            "law_number": first.get("law_number"),
            "document_title": first.get("document_title"),
            "observations": [
                {
                    **{key: row.get(key) for key in observation_fields},
                    "id": _record_identifier(row.get("id")),
                    "document_id": _record_identifier(row.get("document_id")),
                }
                for row in observations or []
            ],
            "events": [self._event_projection(row) for row in events or []],
            "decisions": [self._decision_projection(row) for row in decisions or []],
        }

    async def count_open_events(self) -> int:
        rows = await self.query(
            "SELECT count() AS total FROM legal_validity_event WHERE review_status = 'open' GROUP ALL;"
        )
        return int((rows[0] if rows else {}).get("total") or 0)


def _snapshot_observation(
    law_number: str, entry: Mapping[str, Any]
) -> LegalValidityObservation | None:
    try:
        return LegalValidityObservation(
            document_id=str(entry.get("document_id") or "") or None,
            law_number=law_number,
            issuing_agency=None,
            issued_date=None,
            source_url=str(entry.get("source_url") or ""),
            source_kind=str(entry.get("source_kind") or "vbpl"),
            raw_status=str(entry.get("normalized_status") or "") or None,
            normalized_status=NormalizedValidityStatus(
                str(entry.get("normalized_status") or "unknown")
            ),
            effective_from=str(entry.get("effective_from") or "") or None,
            effective_to=str(entry.get("effective_to") or "") or None,
            affecting_document_number=None,
            affected_provisions=normalize_provisions(entry.get("affected_provisions") or []),
            identity_status=IdentityStatus(str(entry.get("identity_status") or "missing")),
            evidence_status=EvidenceStatus(str(entry.get("evidence_status") or "malformed")),
            observed_at=_as_datetime(entry.get("verified_at"))
            or datetime.now(timezone.utc),
            source_updated_at=None,
        )
    except (TypeError, ValueError):
        return None


def _public_validity_metadata(
    *,
    entry: Mapping[str, Any] | None,
    status: str,
    action: str,
    warning_code: str | None,
    reason_code: str | None,
    would_block: bool,
    current_answer_eligible: bool,
    historical_lookup_allowed: bool = True,
    display_label: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "serving_action": action,
        "verified_at": (entry or {}).get("verified_at"),
        "verified_by": (entry or {}).get("verified_by"),
        "source_kind": (entry or {}).get("source_kind"),
        "source_url": (entry or {}).get("source_url"),
        "effective_from": (entry or {}).get("effective_from"),
        "effective_to": (entry or {}).get("effective_to"),
        "warning_code": warning_code,
        "reason_code": reason_code,
        "would_block": bool(would_block),
        "current_answer_eligible": bool(current_answer_eligible),
        "historical_lookup_allowed": bool(historical_lookup_allowed),
        "display_label": display_label,
    }


def _validity_display_label(
    *,
    status: str,
    current_answer_eligible: bool,
    warning_code: str | None,
) -> str:
    if warning_code == "historical_validity" and current_answer_eligible:
        return (
            "V\u0103n b\u1ea3n l\u1ecbch s\u1eed \u2013 c\u00f3 hi\u1ec7u l\u1ef1c "
            "t\u1ea1i th\u1eddi \u0111i\u1ec3m \u0111\u01b0\u1ee3c h\u1ecfi"
        )
    labels = {
        "active": "\u0110ang c\u00f3 hi\u1ec7u l\u1ef1c",
        "not_yet_effective": "Ch\u01b0a c\u00f3 hi\u1ec7u l\u1ef1c",
        "expired": (
            "H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 "
            "tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
        ),
        "replaced": (
            "\u0110\u00e3 b\u1ecb thay th\u1ebf \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 "
            "tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
        ),
        "repealed": (
            "\u0110\u00e3 b\u1ecb b\u00e3i b\u1ecf \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 "
            "tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
        ),
        "suspended": (
            "\u0110ang ng\u01b0ng hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 "
            "tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
        ),
        "expired_partial": "H\u1ebft hi\u1ec7u l\u1ef1c m\u1ed9t ph\u1ea7n",
        "suspended_partial": "Ng\u01b0ng hi\u1ec7u l\u1ef1c m\u1ed9t ph\u1ea7n",
        "amended": "\u0110\u00e3 \u0111\u01b0\u1ee3c s\u1eeda \u0111\u1ed5i, b\u1ed5 sung",
        "unknown": "Ch\u01b0a x\u00e1c minh hi\u1ec7u l\u1ef1c",
    }
    label = labels.get(status, labels["unknown"])
    if not current_answer_eligible and status not in {
        "expired",
        "replaced",
        "repealed",
        "suspended",
    }:
        return f"{label} \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
    return label


def project_validity_for_row(
    row: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Project one canonical serving status without hiding historical data."""

    effective_mode = str(
        mode
        or (snapshot or {}).get("mode")
        or os.getenv("LEGAL_VALIDITY_SYNC_MODE", "protect")
    ).casefold()
    if effective_mode not in {"observe", "protect", "strict"}:
        effective_mode = "protect"
    legal_as_of = as_of or vietnam_legal_date()
    stored_status = str(
        row.get("stored_status") or row.get("document_status") or row.get("status") or ""
    ).strip().casefold()
    if stored_status == "blocked":
        # Admin search exclusion is independent from the effectivity snapshot.
        # An older snapshot must never project an excluded document as current.
        return _public_validity_metadata(
            entry=None,
            status="blocked",
            action="block_document",
            warning_code=None,
            reason_code="admin_excluded_from_search",
            would_block=True,
            current_answer_eligible=False,
            historical_lookup_allowed=False,
            display_label="Đã loại khỏi tìm kiếm hiện hành",
        )
    documents = (
        snapshot.get("documents")
        if isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("documents"), Mapping)
        else {}
    )
    document_ids = (
        snapshot.get("document_ids")
        if isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("document_ids"), Mapping)
        else {}
    )
    law_number = str(row.get("law_number") or "").strip()
    document_id = str(row.get("document_id") or row.get("doc_id") or "").strip()
    # Exact document-id mapping wins over the legacy law-number lookup.  This
    # prevents a same-number Law/Resolution pair (or duplicate import) from
    # inheriting the other instrument's validity status.
    entry_key = str(document_ids.get(document_id) or "") if document_id else ""
    if document_id and not entry_key:
        # Older snapshots did not yet materialize the ``document_ids`` index,
        # but their entries already carried canonical document_id values.
        # Recover by exact identity only; never transfer validity by number.
        entry_key = next(
            (
                str(key)
                for key, candidate in documents.items()
                if isinstance(candidate, Mapping)
                and str(candidate.get("document_id") or "") == document_id
            ),
            "",
        )
    # Rows with a canonical document ID never inherit a same-number record.
    # Law-number lookup remains read-only compatibility for legacy rows that
    # genuinely have no identity mapping.
    entry = (
        documents.get(entry_key)
        if document_id
        else documents.get(law_number) if law_number else None
    )
    if (
        isinstance(entry, Mapping)
        and entry.get("source_kind") == "admin_confirmed_stored_metadata"
        and (not document_id or str(entry.get("document_id") or "") != document_id)
    ):
        # A manual record confirmation must not transfer to another document
        # merely because its number matches.
        entry = None

    if not isinstance(entry, Mapping):
        warning = (
            "validity_snapshot_unavailable"
            if snapshot is None
            else "validity_not_observed"
        )
        would_block = effective_mode == "strict"
        return _public_validity_metadata(
            entry=None,
            status="unknown",
            action="block_document" if would_block else "allow",
            warning_code=warning,
            reason_code=warning if would_block else None,
            would_block=would_block,
            current_answer_eligible=not would_block,
            display_label=_validity_display_label(
                status="unknown",
                current_answer_eligible=not would_block,
                warning_code=warning,
            ),
        )

    observation = _snapshot_observation(law_number, entry)
    if observation is None:
        warning = "validity_snapshot_entry_invalid"
        would_block = effective_mode == "strict"
        return _public_validity_metadata(
            entry=entry,
            status="unknown",
            action="block_document" if would_block else "allow",
            warning_code=warning,
            reason_code=warning if would_block else None,
            would_block=would_block,
            current_answer_eligible=not would_block,
            display_label=_validity_display_label(
                status="unknown",
                current_answer_eligible=not would_block,
                warning_code=warning,
            ),
        )

    decision = serving_decision(
        observation,
        as_of=legal_as_of,
        mode=effective_mode,
        article_number=row.get("article_number"),
        clause_number=row.get("clause_number"),
        point_number=row.get("point_number"),
    )
    status = observation.normalized_status.value
    current_answer_eligible = not decision.would_block
    return _public_validity_metadata(
        entry=entry,
        status=status,
        action=decision.serving_action,
        warning_code=decision.warning_code,
        reason_code=decision.reason_code,
        would_block=decision.would_block,
        current_answer_eligible=current_answer_eligible,
        display_label=_validity_display_label(
            status=status,
            current_answer_eligible=current_answer_eligible,
            warning_code=decision.warning_code,
        ),
    )


def apply_validity_overlay(
    payload: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Return a copied search/batch payload with deterministic validity guards.

    ``partial_scope_unresolved`` is deliberately a warning/pass-through
    condition.  A snapshot that says a document changed in part but does not
    identify the affected provisions cannot prove that the matched provision
    is invalid, so it must not remove the retrieval hit.  Fully adverse
    statuses (expired, replaced, repealed and suspended) and explicitly
    matched partial provisions remain hard blocks.
    """

    result = deepcopy(dict(payload))
    effective_mode = str(
        mode
        or (snapshot or {}).get("mode")
        or os.getenv("LEGAL_VALIDITY_SYNC_MODE", "protect")
    ).casefold()
    if effective_mode not in {"observe", "protect", "strict"}:
        effective_mode = "protect"
    legal_as_of = as_of or vietnam_legal_date()
    def overlay_rows(rows: Iterable[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        filtered_reasons: dict[str, int] = {}
        warning_count = 0
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            # ``result`` is already a deep copy of the caller payload. A
            # shallow row copy is sufficient because the overlay only adds one
            # top-level projection and never mutates nested retrieval data.
            item = dict(raw)
            projection = project_validity_for_row(
                item,
                snapshot=snapshot,
                as_of=legal_as_of,
                mode=effective_mode,
            )
            if (
                effective_mode != "observe"
                and not projection["current_answer_eligible"]
            ):
                reason = projection.get("reason_code") or "validity_blocked"
                filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1
                continue
            if projection.get("warning_code"):
                warning_count += 1
            item["validity_sync"] = projection
            kept.append(item)
        return kept, {
            "filtered_count": sum(filtered_reasons.values()),
            "filtered_reasons": filtered_reasons,
            "warning_count": warning_count,
        }

    aggregate = {"filtered_count": 0, "filtered_reasons": {}, "warning_count": 0}
    if isinstance(result.get("issues"), list):
        issues = []
        for issue in result["issues"]:
            if not isinstance(issue, Mapping):
                continue
            copied_issue = dict(issue)
            kept, trace = overlay_rows(copied_issue.get("results") or [])
            copied_issue["results"] = kept
            copied_issue["validity_sync"] = {"mode": effective_mode, **trace}
            issues.append(copied_issue)
            aggregate["filtered_count"] += trace["filtered_count"]
            aggregate["warning_count"] += trace["warning_count"]
            for reason, count in trace["filtered_reasons"].items():
                aggregate["filtered_reasons"][reason] = aggregate["filtered_reasons"].get(reason, 0) + count
        result["issues"] = issues
        if isinstance(result.get("context_results"), list):
            context_rows, context_trace = overlay_rows(result["context_results"])
            result["context_results"] = context_rows
            # Context results are the model-facing aggregate and may duplicate
            # issue rows. Keep their trace separately to avoid inflating the
            # issue-level filtered count while still proving that the guard ran.
            result["validity_sync_context"] = {
                "mode": effective_mode,
                **context_trace,
            }
    else:
        kept, aggregate = overlay_rows(result.get("results") or [])
        result["results"] = kept
    result["validity_sync"] = {"mode": effective_mode, **aggregate}
    return result


def current_answer_validity_readiness(
    snapshot: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float = 24 * 60 * 60,
) -> dict[str, Any]:
    """Prove whether a snapshot is safe for strict current-law answers.

    Readiness is deliberately stronger than file/schema health: every eligible
    document must be observed and fresh, all identities/evidence must be exact,
    and a partial status must name the affected provision scope.  The result is
    public-safe and contains counts/reason codes only.
    """

    reasons: list[str] = []
    if not isinstance(snapshot, Mapping):
        return {
            "ready": False,
            "coverage_ratio": 0.0,
            "freshness_ratio": 0.0,
            "reason_codes": ["validity_snapshot_unavailable"],
        }
    health = LegalValidityRegistry.snapshot_health(
        snapshot,
        now=now or datetime.now(timezone.utc),
        stale_after_seconds=stale_after_seconds,
    )
    if health.get("status") != "healthy":
        reasons.append(str(health.get("reason_code") or "validity_snapshot_unhealthy"))
    coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), Mapping) else {}
    eligible = max(0, int(coverage.get("eligible") or 0))
    observed = max(0, int(coverage.get("observed") or 0))
    fresh = max(0, int(coverage.get("fresh") or 0))
    coverage_ratio = min(1.0, observed / eligible) if eligible else 0.0
    freshness_ratio = min(1.0, fresh / eligible) if eligible else 0.0
    if eligible <= 0 or observed < eligible:
        reasons.append("validity_coverage_incomplete")
    if eligible <= 0 or fresh < eligible:
        reasons.append("validity_freshness_incomplete")

    documents = snapshot.get("documents") if isinstance(snapshot.get("documents"), Mapping) else {}
    for entry in documents.values():
        if not isinstance(entry, Mapping):
            reasons.append("validity_snapshot_entry_invalid")
            continue
        if str(entry.get("identity_status") or "") != "exact" or str(
            entry.get("evidence_status") or ""
        ) != "sufficient":
            reasons.append("validity_identity_or_evidence_unverified")
        if str(entry.get("normalized_status") or "") in {
            "expired_partial",
            "suspended_partial",
            "amended",
        } and not list(entry.get("affected_provisions") or []):
            reasons.append("partial_scope_unresolved")
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "ready": not unique_reasons,
        "coverage_ratio": round(coverage_ratio, 4),
        "freshness_ratio": round(freshness_ratio, 4),
        "reason_codes": unique_reasons,
    }


default_registry = LegalValidityRegistry()
default_snapshot_cache = ValiditySnapshotCache()


__all__ = [
    "LegalValidityRegistry",
    "SNAPSHOT_PATH",
    "ValiditySnapshotCache",
    "apply_validity_overlay",
    "current_answer_validity_readiness",
    "default_registry",
    "default_snapshot_cache",
    "project_validity_for_row",
]
