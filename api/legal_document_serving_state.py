"""One mutable serving contract over the existing legal-document SQL schema.

Index membership is not permission to serve a document. These projections only
restrict an already release-scoped candidate; they never add corpus membership
or assert a legal expiration date from an administrative decision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import ARRAY, Integer, bindparam, text

from api.legal_validity_models import vietnam_legal_date


HISTORICAL_STATUSES = frozenset({"archived", "expired", "historical", "replaced"})
HISTORICAL_REASONS = frozenset({
    "admin_marked_historical", "expired_status", "expired_by_date",
    "known_superseded", "replaced_by_reviewed_document_historical",
})
EXCLUDED_REASONS = frozenset({
    "admin_excluded_from_search", "admin_quarantined_from_search",
    "replaced_by_reviewed_document", "replacement_activation_failed",
})
TRANSITIONS = {
    "exclude": ("blocked", False, "admin_excluded_from_search", "excluded"),
    "quarantine": ("blocked", False, "admin_quarantined_from_search", "quarantined"),
    "historical": ("archived", False, "admin_marked_historical", "historical"),
    "restore": ("active", True, "admin_restored_to_search", "restored"),
}

_STATE_SELECT = """
    SELECT d.id AS document_id, d.title, d.law_number, d.status,
           d.effective_date, d.expired_date,
           s.included AS search_included, s.reason AS search_reason,
           s.domain AS domain, s.evaluated_as_of, s.evaluated_at
    FROM legal_documents d
    LEFT JOIN legal_search_scope s ON s.document_id = d.id
"""


def current_document_lookup_clause() -> str:
    """Additional current-only gate for exact lookups (never grants release access).

    These endpoints have no historical date contract. Administrative history is
    served elsewhere; an archived version must not substitute for a withdrawn one.
    """
    reasons = sorted(EXCLUDED_REASONS | HISTORICAL_REASONS)
    literals = ", ".join("'" + reason + "'" for reason in reasons)
    return f"""
        AND d.status = 'active'
        AND (d.effective_date IS NULL OR d.effective_date <= :lookup_as_of)
        AND (d.expired_date IS NULL OR d.expired_date > :lookup_as_of)
        AND EXISTS (
            SELECT 1 FROM legal_search_scope lookup_scope
            WHERE lookup_scope.document_id = d.id
              AND lookup_scope.included IS TRUE
              AND COALESCE(lookup_scope.reason, '') NOT IN ({literals})
        )
    """


def document_id(value: Any) -> int:
    raw = str(value or "").strip()
    if ":" in raw:
        prefix, raw = raw.split(":", 1)
        if prefix not in {"legal_document", "legal_documents"}:
            raise ValueError("invalid_document_id")
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError("invalid_document_id")
    return int(raw)


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def state_revision(row: Mapping[str, Any]) -> str:
    values = {key: row.get(key) for key in (
        "document_id", "status", "effective_date", "expired_date",
        "search_included", "search_reason", "domain", "evaluated_as_of", "evaluated_at",
    )}
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def rollback_action_for_transition(receipt: Mapping[str, Any]) -> str:
    """Map a transition receipt back to its exact previous serving class."""

    previous_status = str(receipt.get("previous_status") or "").strip().casefold()
    previous_reason = str(receipt.get("previous_search_reason") or "").strip().casefold()
    previous_included = receipt.get("previous_search_included")
    if previous_reason == "admin_quarantined_from_search":
        return "quarantine"
    if previous_status == "blocked" or previous_reason in EXCLUDED_REASONS:
        return "exclude"
    if previous_status in HISTORICAL_STATUSES or previous_reason in HISTORICAL_REASONS:
        return "historical"
    if previous_included is False:
        return "exclude"
    return "restore"


def serving_projection(
    row: Mapping[str, Any] | None, *, temporal_scope: str, as_of: date,
) -> dict[str, Any]:
    """Determine serving eligibility independently of login role or vector tags."""

    def result(allowed: bool, state: str, reason: str | None) -> dict[str, Any]:
        return {"allowed": allowed, "serving_state": state, "reason_code": reason}

    if not row:
        return result(False, "unavailable", "document_state_missing")
    status = str(row.get("status") or "").strip().casefold()
    reason = str(row.get("search_reason") or "").strip().casefold()
    if status == "blocked" or reason in EXCLUDED_REASONS:
        return result(False, "excluded", "admin_excluded_from_search")
    if status not in {"active", *HISTORICAL_STATUSES}:
        return result(False, "quarantined", "document_not_activated")
    historical = status in HISTORICAL_STATUSES or reason in HISTORICAL_REASONS
    if not bool(row.get("search_included")) and not historical:
        return result(False, "excluded", "document_outside_serving_scope")
    if historical and temporal_scope != "historical":
        return result(False, "historical_only", "historical_document_not_current")
    try:
        effective = _date(row.get("effective_date"))
        expired = _date(row.get("expired_date"))
    except (TypeError, ValueError):
        return result(False, "quarantined", "invalid_legal_dates")
    if effective and effective > as_of:
        return result(False, "not_yet_effective", "not_effective_at_query_date")
    if expired and expired <= as_of:
        return result(False, "historical_only", "expired_at_query_date")
    return result(True, "historical_only" if historical else "current_retrievable", None)


def inventory_projection(
    row: Mapping[str, Any] | None, *, release_state: str, as_of: date,
) -> dict[str, Any]:
    """Admin visibility of serving state, without promoting release membership.

    Historical eligibility means lookup *at a valid past date*, not permission
    to apply an expired document to a current question.
    """
    current = serving_projection(row, temporal_scope="current", as_of=as_of)
    state = str(current["serving_state"])
    current_allowed = release_state == "current_retrievable" and bool(current["allowed"])
    historical_allowed = False
    if row and release_state in {"current_retrievable", "historical_only"}:
        try:
            expired = _date(row.get("expired_date"))
            anchor = min(as_of, expired - timedelta(days=1)) if expired else as_of
            historical_allowed = bool(serving_projection(row, temporal_scope="historical", as_of=anchor)["allowed"])
        except (TypeError, ValueError, OverflowError):
            historical_allowed = False
    if current_allowed:
        state = "current_retrievable"
    elif historical_allowed:
        state = "historical_only"
    elif state == "current_retrievable":
        state = release_state if release_state else "quarantined"
    return {
        "serving_state": state, "serving_status": state,
        "current_answer_eligible": current_allowed,
        "historical_lookup_allowed": historical_allowed,
        "search_included": bool(row and row.get("search_included")),
        "search_reason": row.get("search_reason") if row else "document_state_missing",
        "state_revision": state_revision(row) if row else None,
    }


def historical_anchor(row: Mapping[str, Any], *, as_of: date) -> date:
    """Choose a date inside the known legal interval for a history check."""

    effective = _date(row.get("effective_date"))
    expired = _date(row.get("expired_date"))
    anchor = as_of
    if expired is not None and expired <= anchor:
        anchor = expired - timedelta(days=1)
    if effective is not None and anchor < effective:
        anchor = effective
    return anchor


def transition_verification(
    row: Mapping[str, Any], *, action: str, as_of: date
) -> dict[str, Any]:
    """Prove the SQL serving postcondition before committing a transition."""

    history_as_of = historical_anchor(row, as_of=as_of)
    current = serving_projection(row, temporal_scope="current", as_of=as_of)
    historical = serving_projection(
        row, temporal_scope="historical", as_of=history_as_of
    )
    expected = {
        "exclude": (False, False),
        "quarantine": (False, False),
        "historical": (False, True),
        "restore": (True, None),
    }[action]
    current_matches = bool(current["allowed"]) is expected[0]
    historical_matches = (
        True
        if expected[1] is None
        else bool(historical["allowed"]) is expected[1]
    )
    return {
        "status": "passed" if current_matches and historical_matches else "failed",
        "passed": current_matches and historical_matches,
        "expected_current": expected[0],
        "expected_historical": expected[1],
        "current_allowed": bool(current["allowed"]),
        "current_state": current["serving_state"],
        "current_reason_code": current["reason_code"],
        "historical_allowed": bool(historical["allowed"]),
        "historical_state": historical["serving_state"],
        "historical_reason_code": historical["reason_code"],
        "historical_as_of": history_as_of.isoformat(),
    }


class DocumentServingStateStore:
    """Transactional state/scope updates; no schema creation and no vector writes."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def check_availability(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text(_STATE_SELECT + " WHERE 1 = 0")).first()

    def read_many(self, values: Iterable[Any]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(document_id(value) for value in values))
        if not ids:
            return {}
        if (
            getattr(getattr(self.engine, "dialect", None), "name", "")
            == "postgresql"
        ):
            # Management projections routinely read the full 12k-document
            # serving inventory. One typed array keeps the prepared statement
            # compact; expanding 12k placeholders caused cold Admin requests
            # to miss their metadata deadline.
            query = text(
                _STATE_SELECT + " WHERE d.id = ANY(:document_ids)"
            ).bindparams(bindparam("document_ids", type_=ARRAY(Integer)))
        else:
            query = text(
                _STATE_SELECT + " WHERE d.id IN :document_ids"
            ).bindparams(bindparam("document_ids", expanding=True))
        with self.engine.connect() as connection:
            rows = connection.execute(query, {"document_ids": ids}).mappings().all()
        return {str(row["document_id"]): dict(row) for row in rows}

    def read(self, value: Any) -> dict[str, Any]:
        identity = document_id(value)
        row = self.read_many([identity]).get(str(identity))
        if row is None:
            raise LookupError("legal_document_not_found")
        return row

    def transition(
        self, value: Any, *, action: str, requested_by: str, reason: str,
        expected_revision: str | None = None,
        required_active_document_id: Any | None = None,
    ) -> dict[str, Any]:
        identity = document_id(value)
        action = str(action or "").strip().casefold()
        if action not in TRANSITIONS:
            raise ValueError("invalid_search_state_action")
        if not str(requested_by or "").strip() or not 10 <= len(str(reason or "").strip()) <= 2000:
            raise ValueError("document_state_audit_reason_required")
        status, included, scope_reason, label = TRANSITIONS[action]
        partner_id = document_id(required_active_document_id) if required_active_document_id is not None else None
        if partner_id == identity:
            raise ValueError("replacement_document_must_differ")
        now = datetime.now(timezone.utc)
        today = vietnam_legal_date()
        with self.engine.begin() as connection:
            lock = " FOR UPDATE OF d" if connection.dialect.name == "postgresql" else ""
            # Lock both sides in ID order. A concurrent admin action cannot
            # exclude the replacement between validation and the old-state
            # commit; concurrent replacements cannot silently overwrite one
            # another's decision.
            ids = sorted({identity, partner_id} if partner_id is not None else {identity})
            statement = text(_STATE_SELECT + " WHERE d.id IN :ids ORDER BY d.id" + lock).bindparams(
                bindparam("ids", expanding=True)
            )
            rows = {int(item["document_id"]): item for item in connection.execute(
                statement, {"ids": ids}
            ).mappings()}
            row = rows.get(identity)
            if row is None:
                raise LookupError("legal_document_not_found")
            if partner_id is not None:
                partner = rows.get(partner_id)
                if not serving_projection(partner, temporal_scope="current", as_of=today)["allowed"]:
                    raise ValueError("replacement_document_not_current")
            previous = dict(row)
            if expected_revision and state_revision(previous) != expected_revision:
                raise ValueError("document_serving_state_changed")
            previous_status = str(previous.get("status") or "").casefold()
            if action in {"restore", "historical"} and previous_status not in {
                "active", "blocked", *HISTORICAL_STATUSES,
            }:
                raise ValueError("unactivated_document_cannot_enter_search")
            if action == "restore":
                effective, expired = _date(row.get("effective_date")), _date(row.get("expired_date"))
                if previous_status in {"expired", "replaced"} or (expired and expired <= today):
                    raise ValueError("expired_document_cannot_be_restored_to_search")
                if effective is None or effective > today:
                    raise ValueError("document_not_effective_for_current_search")
            replay = (
                previous_status == status and previous.get("search_included") == included
                and previous.get("search_reason") == scope_reason
            )
            if not replay:
                connection.execute(
                    text("UPDATE legal_documents SET status = :status WHERE id = :document_id"),
                    {"status": status, "document_id": identity},
                )
                # Existing rows retain their reviewed domain. A missing scope
                # row must not turn a successful UPDATE(0 rows) into success.
                connection.execute(text("""
                    INSERT INTO legal_search_scope
                        (document_id, included, reason, domain, evaluated_as_of, evaluated_at)
                    VALUES (:document_id, :included, :reason, :domain, :as_of, :now)
                    ON CONFLICT (document_id) DO UPDATE SET
                        included = excluded.included, reason = excluded.reason,
                        evaluated_as_of = excluded.evaluated_as_of, evaluated_at = excluded.evaluated_at
                """), {"document_id": identity, "included": included, "reason": scope_reason,
                         "domain": previous.get("domain"), "as_of": today, "now": now})
            current = dict(connection.execute(
                text(_STATE_SELECT + " WHERE d.id = :document_id"), {"document_id": identity}
            ).mappings().one())
            verification = transition_verification(
                current, action=action, as_of=today
            )
            if not verification["passed"]:
                raise RuntimeError("document_state_postcondition_failed")
        return {
            "status": label, "action": action, "document_id": str(identity),
            "law_number": str(previous.get("law_number") or ""),
            "title": str(previous.get("title") or ""), "stored_status": status,
            "previous_status": previous_status,
            "previous_search_included": bool(previous.get("search_included")),
            "previous_search_reason": previous.get("search_reason"),
            "search_included": included,
            "serving_action": "historical_only" if action == "historical" else "allow" if included else "block",
            "current_answer_eligible": verification["current_allowed"],
            "historical_lookup_allowed": verification["historical_allowed"],
            "state_revision": state_revision(current), "previous_state_revision": state_revision(previous),
            "idempotent_replay": replay, "vectors_preserved_for_audit": True,
            "requested_by": requested_by, "reason": reason,
            "verification": verification,
        }


class ReplacementActivationError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        document_id: Any,
        compensation: Mapping[str, Any],
        verification: Mapping[str, Any] | None = None,
    ):
        self.detail = {
            "code": "replacement_activation_failed", "stage": stage,
            "new_document_id": str(document_id), "compensation": dict(compensation),
            "verification": dict(verification or {}),
            "message": "Không hoàn tất thay thế. Kiểm tra trạng thái hoàn tác trước khi thử lại.",
        }
        super().__init__("replacement_activation_failed")
