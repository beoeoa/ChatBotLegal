"""Department operating model with staged compatibility for legacy domains.

The singleton settings record remains the write-safe compatibility source in
``legacy`` and ``shadow`` modes.  The normalized tables become authoritative
only in ``hybrid``/``unit_primary`` after they have a verified projection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from loguru import logger

from api.models import OrganizationUnitConfig, OrganizationUnitDomainConfig
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_query,
    repo_update,
)

ROUTING_MODES = ("legacy", "shadow", "hybrid", "unit_primary")
# New discoveries may legitimately wait for an administrator. Only records
# already approved for import must block the cutover, not the review backlog.
READINESS_INFORMATION_KEYS = frozenset({"pending_candidates_without_assignment"})

_projection_lock = asyncio.Lock()
_projection_signature: tuple[Any, ...] | None = None


def routing_mode(value: Any) -> str:
    normalized = str(value or "legacy").strip().lower()
    return normalized if normalized in ROUTING_MODES else "legacy"


def imported_document_id(value: Any) -> str:
    """Read an import receipt or a fetched document without guessing an ID."""
    if isinstance(value, Mapping):
        return imported_document_id(value.get("document_id") or value.get("id"))
    return str(value or "").strip()


def effective_domain_assignments(
    unit: OrganizationUnitConfig,
) -> list[OrganizationUnitDomainConfig]:
    """Read both the normalized relation and the legacy domain projection.

    Older clients can still update only ``domain_codes``.  ``model_copy`` and
    partially upgraded API clients do not rerun the model's before-validator,
    so domains missing from the normalized list must remain visible as primary
    assignments during the compatibility window.
    """

    assignments = list(unit.domain_assignments)
    known = {item.domain_code for item in assignments}
    assignments.extend(
        OrganizationUnitDomainConfig(domain_code=domain, responsibility="primary")
        for domain in unit.domain_codes
        if domain and domain not in known
    )
    return assignments


def unit_signature(units: Iterable[OrganizationUnitConfig]) -> tuple[Any, ...]:
    return tuple(
        (
            unit.id,
            unit.code,
            unit.name,
            unit.short_name,
            tuple(unit.aliases),
            unit.parent_id,
            tuple(
                (assignment.domain_code, assignment.responsibility)
                for assignment in effective_domain_assignments(unit)
            ),
            unit.support_enabled,
            unit.is_active,
            unit.sort_order,
        )
        for unit in units
    )


def validate_operating_units(
    units: Iterable[OrganizationUnitConfig],
) -> list[OrganizationUnitConfig]:
    rows = list(units)
    ids = [row.id for row in rows]
    codes = [row.code for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("organization_unit_id_duplicate")
    if len(codes) != len(set(codes)):
        raise ValueError("organization_unit_code_duplicate")

    primary_owners: dict[str, str] = {}
    for unit in rows:
        seen_domains: set[str] = set()
        for assignment in effective_domain_assignments(unit):
            domain = assignment.domain_code.strip()
            if domain in seen_domains:
                raise ValueError(f"organization_unit_domain_duplicate:{unit.id}:{domain}")
            seen_domains.add(domain)
            if (
                unit.is_active
                and unit.support_enabled
                and assignment.responsibility == "primary"
            ):
                previous = primary_owners.get(domain)
                if previous and previous != unit.id:
                    raise ValueError(f"organization_domain_multiple_primary:{domain}")
                primary_owners[domain] = unit.id
    return rows


def domains_for_unit(unit: OrganizationUnitConfig) -> list[str]:
    return list(
        dict.fromkeys(
            assignment.domain_code
            for assignment in effective_domain_assignments(unit)
            if assignment.domain_code
        )
    )


def find_unit(
    units: Iterable[OrganizationUnitConfig],
    *,
    unit_id: str | None = None,
    name_or_alias: str | None = None,
) -> OrganizationUnitConfig | None:
    unit_id = str(unit_id or "").strip()
    # A stable ID must never lose to an earlier unit's stale display label.
    if unit_id:
        return next((unit for unit in units if unit.id == unit_id), None)
    needle = str(name_or_alias or "").strip().casefold()
    for unit in units:
        labels = {unit.name.casefold(), unit.code.casefold()}
        labels.update(alias.casefold() for alias in unit.aliases)
        if needle and needle in labels:
            return unit
    return None


def derive_profile_projection(
    payload: Mapping[str, Any],
    units: Iterable[OrganizationUnitConfig],
    *,
    mode: str,
    retained_unit_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the stable unit and maintain legacy fields through dual-write."""

    result = dict(payload)
    # Officer assignments always use the dynamic unit as source of truth.
    # Legacy mode is retained only for unmigrated read-only citizen profiles.
    if routing_mode(mode) == "legacy" and str(payload.get("role") or "") != "officer":
        return result
    if str(payload.get("role") or "") == "officer":
        mode = "unit_primary"
    if "organization_unit_id" in result and not result["organization_unit_id"]:
        if routing_mode(mode) != "shadow":
            result.update(organization_unit_id=None, department=None, allowed_domains=[])
        return result
    unit = find_unit(
        units,
        unit_id=str(result.get("organization_unit_id") or "").strip() or None,
        name_or_alias=str(result.get("department") or "").strip() or None,
    )
    if unit is None:
        if result.get("organization_unit_id"):
            raise ValueError("organization_unit_not_found")
        if routing_mode(mode) in {"hybrid", "unit_primary"}:
            result.update(organization_unit_id=None, department=None, allowed_domains=[])
        return result
    if not unit.is_active:
        if unit.id != retained_unit_id:
            raise ValueError("organization_unit_inactive")
        # Allow contact/password/deactivation updates without granting work to
        # an inactive department. A newly selected inactive unit is rejected.
        result.update(organization_unit_id=unit.id, department=unit.name, allowed_domains=[])
        return result
    result["organization_unit_id"] = unit.id
    result["department"] = unit.name
    # Shadow may add the unit hint, but must not silently grant the other
    # domains of that unit to an existing officer.
    if routing_mode(mode) != "shadow" or "allowed_domains" not in result:
        result["allowed_domains"] = domains_for_unit(unit)
    return result


def route_for_domain(
    units: Iterable[OrganizationUnitConfig], domain: str | None
) -> tuple[OrganizationUnitConfig, str] | None:
    target = str(domain or "").strip()
    if not target:
        return None
    matches: list[tuple[OrganizationUnitConfig, str]] = []
    for unit in units:
        if not unit.is_active or not unit.support_enabled:
            continue
        for assignment in effective_domain_assignments(unit):
            if assignment.domain_code == target:
                matches.append((unit, assignment.responsibility))
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda item: (
            0 if item[1] == "primary" else 1,
            item[0].sort_order,
            item[0].name.casefold(),
        ),
    )[0]


def propose_candidate_assignment(
    *,
    domain: str | None,
    source_default_unit_id: str | None,
    units: Iterable[OrganizationUnitConfig],
) -> dict[str, Any]:
    rows = list(units)
    explicit = find_unit(rows, unit_id=source_default_unit_id)
    if explicit is not None and not explicit.is_active:
        explicit = None
    routed = route_for_domain(rows, domain)
    selected = explicit or (routed[0] if routed else None)
    if selected is None:
        return {
            "assignment_state": "unassigned",
            "primary_organization_unit_id": None,
            "proposed_organization_unit_ids": [],
            "organization_assignment_confidence": 0.0,
        }
    confidence = 1.0 if explicit else (0.85 if routed and routed[1] == "primary" else 0.65)
    return {
        "assignment_state": "assigned",
        "primary_organization_unit_id": selected.id,
        "proposed_organization_unit_ids": [selected.id],
        "organization_assignment_confidence": confidence,
    }


def _unit_from_database_row(
    row: Mapping[str, Any], domain_rows: Iterable[Mapping[str, Any]]
) -> OrganizationUnitConfig:
    assignments = [
        OrganizationUnitDomainConfig(
            domain_code=str(item.get("domain_code") or ""),
            responsibility=str(item.get("responsibility") or "primary"),
        )
        for item in domain_rows
        if str(item.get("organization_unit_id") or "") == str(row.get("unit_id") or "")
        and str(item.get("domain_code") or "").strip()
    ]
    return OrganizationUnitConfig(
        id=str(row.get("unit_id") or ""),
        code=str(row.get("code") or ""),
        name=str(row.get("name") or ""),
        short_name=row.get("short_name"),
        aliases=list(row.get("aliases") or []),
        parent_id=row.get("parent_unit_id"),
        domain_assignments=assignments,
        domain_codes=[item.domain_code for item in assignments],
        support_enabled=bool(row.get("support_enabled", True)),
        is_active=bool(row.get("is_active", True)),
        sort_order=int(row.get("sort_order") or 0),
    )


async def database_units() -> list[OrganizationUnitConfig]:
    rows = await repo_query(
        "SELECT * FROM organization_unit ORDER BY sort_order ASC, name ASC;"
    )
    if not rows:
        return []
    domains = await repo_query(
        "SELECT * FROM organization_unit_domain WHERE effective_to = NONE OR effective_to > time::now();"
    )
    return validate_operating_units(
        _unit_from_database_row(row, domains) for row in rows
    )


async def _upsert_unit_row(unit: OrganizationUnitConfig) -> None:
    rows = await repo_query(
        "SELECT id FROM organization_unit WHERE unit_id = $unit_id LIMIT 1;",
        {"unit_id": unit.id},
    )
    payload = {
        "unit_id": unit.id,
        "code": unit.code,
        "name": unit.name,
        "short_name": unit.short_name,
        "aliases": unit.aliases,
        "parent_unit_id": unit.parent_id,
        "support_enabled": unit.support_enabled,
        "is_active": unit.is_active,
        "sort_order": unit.sort_order,
    }
    if rows:
        await repo_update("organization_unit", str(rows[0]["id"]), payload)
    else:
        await repo_create("organization_unit", payload)

    await repo_query(
        "DELETE organization_unit_domain WHERE organization_unit_id = $unit_id;",
        {"unit_id": unit.id},
    )
    for assignment in effective_domain_assignments(unit):
        await repo_create(
            "organization_unit_domain",
            {
                "organization_unit_id": unit.id,
                "domain_code": assignment.domain_code,
                "responsibility": assignment.responsibility,
                "effective_from": datetime.now(timezone.utc),
                "effective_to": None,
            },
        )


async def sync_unit_projection(units: Iterable[OrganizationUnitConfig]) -> None:
    """Project compatibility settings into normalized tables, idempotently."""

    global _projection_signature
    normalized = validate_operating_units(units)
    signature = unit_signature(normalized)
    if signature == _projection_signature:
        return
    async with _projection_lock:
        if signature == _projection_signature:
            return
        existing = await database_units()
        incoming_ids = {item.id for item in normalized}
        for unit in normalized:
            await _upsert_unit_row(unit)
        for stale in existing:
            if stale.id not in incoming_ids and stale.is_active:
                await _upsert_unit_row(stale.model_copy(update={"is_active": False}))
        _projection_signature = signature


async def effective_units(
    settings_units: Iterable[OrganizationUnitConfig], *, mode: str
) -> list[OrganizationUnitConfig]:
    configured = validate_operating_units(settings_units)
    selected_mode = routing_mode(mode)
    if selected_mode == "legacy":
        return configured
    try:
        if selected_mode == "shadow":
            await sync_unit_projection(configured)
            return configured
        # In hybrid mode the settings projection is the request-time source of
        # truth. Reading the normalized compatibility tables here caused every
        # page load to perform two remote Surreal queries (and could block for
        # 10–15 seconds when the connection was cold). Writes and the recovery
        # loop keep that projection synchronized; reads must stay local and
        # deterministic.
        if selected_mode == "hybrid":
            return configured
        persisted = await database_units()
        if persisted:
            return persisted
        await sync_unit_projection(configured)
        persisted = await database_units()
        if persisted:
            return persisted
    except Exception as exc:
        logger.warning("Organization projection unavailable in {} mode: {}", selected_mode, exc)
        if selected_mode != "unit_primary":
            return configured
        raise RuntimeError("organization_unit_database_unavailable") from exc
    if selected_mode == "unit_primary":
        raise RuntimeError("organization_unit_database_empty")
    return configured


async def assignment_readiness(*, settings: Any | None = None) -> dict[str, Any]:
    """Return cutover gates without mutating any account or business record."""

    queries = {
        "active_officers_without_unit": (
            "SELECT count() AS count FROM user_account WHERE role = 'officer' "
            "AND is_active = true AND id NOT IN "
            "(SELECT VALUE user FROM user_profile WHERE organization_unit_id IN "
            "(SELECT VALUE unit_id FROM organization_unit WHERE is_active = true)) GROUP ALL;"
        ),
        "open_support_without_unit": (
            "SELECT count() AS count FROM support_session WHERE queue_status IN "
            "['queued','assigned','active','waiting_citizen','waiting_officer'] "
            "AND primary_organization_unit_id = NONE GROUP ALL;"
        ),
        "pending_candidates_without_assignment": (
            "SELECT count() AS count FROM legal_crawl_candidate WHERE status IN "
            "['pending','changes_requested'] AND (assignment_state = NONE OR assignment_state = 'unassigned') GROUP ALL;"
        ),
        "approved_candidates_without_assignment": (
            "SELECT count() AS count FROM legal_crawl_candidate WHERE status IN "
            "['approved','import_queued','import_failed'] AND (assignment_state = NONE OR assignment_state = 'unassigned') GROUP ALL;"
        ),
        "procedures_without_unit": (
            "SELECT count() AS count FROM ward_procedure WHERE "
            "primary_organization_unit_id = NONE OR primary_organization_unit_id = '' GROUP ALL;"
        ),
        "document_assignments_pending_sync": (
            "SELECT count() AS count FROM document_organization_assignment WHERE "
            "confirmation_status = 'projection_pending' GROUP ALL;"
        ),
        "document_assignments_needing_confirmation": (
            "SELECT count() AS count FROM document_organization_assignment WHERE "
            "confirmation_status = 'needs_confirmation' GROUP ALL;"
        ),
    }
    result: dict[str, int] = {}
    for key, query in queries.items():
        rows = await repo_query(query)
        result[key] = int((rows[0] if rows else {}).get("count") or 0)
    # The deployed queue can live in PostgreSQL, not support_session. Never
    # report a green cutover gate by inspecting only the compatibility store.
    sql_support_missing = await asyncio.to_thread(_active_support_unit_readiness)
    result["open_support_without_unit"] = max(result["open_support_without_unit"], sql_support_missing)
    imported_rows = await repo_query(
        "SELECT imported_document FROM legal_crawl_candidate WHERE "
        "status = 'imported' AND imported_document != NONE;"
    )
    assignment_rows = await repo_query(
        "SELECT document_id, assignment_state, primary_organization_unit_id, confirmation_status FROM document_organization_assignment;"
    )
    assigned_document_ids = {
        str(row.get("document_id") if isinstance(row, Mapping) else row or "").strip()
        for row in assignment_rows
    }
    imported_document_ids = {
        imported_document_id(row.get("imported_document"))
        for row in imported_rows
    }
    result["imported_documents_without_assignment"] = len(
        {value for value in imported_document_ids if value}
        - {value for value in assigned_document_ids if value}
    )
    result["imported_documents_unassigned"] = len({
        str(row.get("document_id") or "").strip()
        for row in assignment_rows
        if isinstance(row, Mapping)
        and row.get("assignment_state") not in {"assigned", "shared"}
        and str(row.get("document_id") or "").strip() in imported_document_ids
    })
    release_readiness = await asyncio.to_thread(_active_form_release_unit_readiness)
    result.update(release_readiness)
    relation_rows = await repo_query("SELECT document_id, organization_unit_id, relationship FROM document_organization_unit;")
    result.update(await asyncio.to_thread(_legal_corpus_unit_readiness, authority=assignment_rows, relations=relation_rows))
    data_issue_count = sum(value for key, value in result.items() if key not in READINESS_INFORMATION_KEYS)
    data_ready = data_issue_count == 0
    return {
        **result,
        "data_ready_for_unit_primary": data_ready,
        "blocking_issue_count": data_issue_count,
        "issue_count_basis": "conditions_not_unique_records",
        "ready_for_unit_primary": data_ready,
    }


def projection_disagreement_ids(sql_rows: Iterable[Mapping[str, Any]], authority: Iterable[Mapping[str, Any]], relations: Iterable[Mapping[str, Any]]) -> set[str]:
    """Compare all stores, including confirmed SQL rows with no authority."""
    authoritative = {str(row.get("document_id")): row for row in authority if row.get("document_id")}
    related: dict[str, set[str]] = {}
    primary_relations: dict[str, set[str]] = {}
    for row in relations:
        if not row.get("document_id"):
            continue
        doc = str(row["document_id"])
        related.setdefault(doc, set()).add(str(row.get("organization_unit_id") or ""))
        if row.get("relationship") == "reviewing":
            primary_relations.setdefault(doc, set()).add(str(row.get("organization_unit_id") or ""))
    mismatches: set[str] = set()
    known = set()
    for row in sql_rows:
        doc = str(row["document_id"])
        known.add(doc)
        source = authoritative.get(doc)
        if source is None or any(row.get(key) != source.get(key) for key in ("assignment_state", "primary_organization_unit_id", "confirmation_status")):
            mismatches.add(doc)
            continue
        units = related.get(doc, set())
        if set(row.get("organization_unit_ids") or []) != units:
            mismatches.add(doc)
        if source.get("assignment_state") == "assigned":
            if not source.get("primary_organization_unit_id") or primary_relations.get(doc, set()) != {source["primary_organization_unit_id"]}:
                mismatches.add(doc)
        elif units or source.get("primary_organization_unit_id"):
            mismatches.add(doc)
    return mismatches | ((set(authoritative) | set(related)) - known)


def _legal_corpus_unit_readiness(*, authority: list[dict] | None = None, relations: list[dict] | None = None) -> dict[str, int]:
    """Fail closed if the entire SQL corpus, including legacy, is not assigned."""
    import os
    from sqlalchemy import create_engine, text

    unavailable = {
        'legacy_documents_unassigned': 0,
        'corpus_assignments_pending_sync': 0,
        'corpus_assignments_needing_confirmation': 0,
        'corpus_unit_readiness_unavailable': 1,
    }
    url = str(os.getenv('LEGAL_DATABASE_URL') or os.getenv('LEGAL_RELEASE_DATABASE_URL') or '').strip()
    if not url:
        return unavailable
    engine = None
    try:
        engine = create_engine(url, future=True, pool_pre_ping=True)
        with engine.connect() as connection:
            row = connection.execute(text("""
                SELECT count(*) FILTER (WHERE a.document_id IS NULL OR a.assignment_state='unassigned') AS unassigned,
                       count(*) FILTER (WHERE a.document_id IS NOT NULL AND a.confirmation_status='projection_pending') AS pending,
                       count(*) FILTER (WHERE a.document_id IS NOT NULL AND a.confirmation_status='needs_confirmation') AS needs_confirmation
                FROM legal_documents d
                LEFT JOIN legal_document_organization_assignment a ON a.document_id=d.id
            """)).mappings().one()
            mismatches = set()
            if authority is not None:
                projections = connection.execute(text("SELECT d.id AS document_id, a.assignment_state, a.primary_organization_unit_id, a.organization_unit_ids, a.confirmation_status FROM legal_documents d LEFT JOIN legal_document_organization_assignment a ON a.document_id=d.id")).mappings().all()
                mismatches = projection_disagreement_ids(projections, authority, relations or [])
        return {
            'legacy_documents_unassigned': int(row['unassigned']),
            'corpus_assignments_pending_sync': int(row['pending']),
            'corpus_assignments_needing_confirmation': int(row['needs_confirmation']),
            'corpus_unit_readiness_unavailable': 0,
            'corpus_assignment_disagreements': len(mismatches),
        }
    except Exception as exc:
        logger.warning('Could not inspect full-corpus department readiness: {}', type(exc).__name__)
        return unavailable
    finally:
        if engine is not None:
            engine.dispose()


def _active_support_unit_readiness() -> int:
    import os
    if str(os.getenv("FEATURE018_SUPPORT_MODE") or "legacy_json").strip() != "postgres_active":
        return 0
    database_url = str(os.getenv("FEATURE018_DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("Chưa cấu hình nơi lưu yêu cầu hỗ trợ; không thể xác nhận chuyển đổi phòng ban.")
    from sqlalchemy import create_engine, text
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(
                "SELECT count(*) FROM support_ticket "
                "WHERE status NOT IN ('closed','resolved','cancelled','expired') "
                "AND NULLIF(trim(primary_organization_unit_id), '') IS NULL"
            )).scalar_one())
    finally:
        engine.dispose()


def _active_form_release_unit_readiness() -> dict[str, int]:
    """Inspect the immutable form catalog only when PostgreSQL serves it."""

    import os

    if str(os.getenv("FORM_GOVERNANCE_SOURCE") or "json_compat").casefold() != "postgres_active":
        return {
            "active_form_release_without_unit": 0,
            "form_release_readiness_unavailable": 0,
        }
    database_url = str(
        os.getenv("LEGAL_DATABASE_URL")
        or os.getenv("LEGAL_RELEASE_DATABASE_URL")
        or ""
    ).strip()
    if not database_url:
        return {
            "active_form_release_without_unit": 0,
            "form_release_readiness_unavailable": 1,
        }
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url, future=True, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                manifest = connection.execute(text("""
                    SELECT release.manifest FROM form_active_release pointer
                    JOIN form_release release ON release.id=pointer.release_ref
                    WHERE pointer.pointer_key='forms-catalog'
                """)).scalar_one_or_none()
        finally:
            engine.dispose()
        if not manifest:
            return {
                "active_form_release_without_unit": 0,
                "form_release_readiness_unavailable": 1,
            }
        if isinstance(manifest, str):
            import json

            manifest = json.loads(manifest)
        missing = sum(
            not str(item.get("primary_organization_unit_id") or "").strip()
            for item in manifest.get("procedures") or []
        )
        return {
            "active_form_release_without_unit": int(missing),
            "form_release_readiness_unavailable": 0,
        }
    except Exception as exc:
        logger.warning("Could not inspect active form release department readiness: {}", exc)
        return {
            "active_form_release_without_unit": 0,
            "form_release_readiness_unavailable": 1,
        }


async def replace_document_unit_assignments(
    *,
    document_id: str | int,
    primary_organization_unit_id: str | None,
    organization_unit_ids: Iterable[str] = (),
    assignment_source: str = "admin",
    confidence: float = 1.0,
    confirmation_status: str = "confirmed",
    assignment_state: str | None = None,
    only_if_absent: bool = False,
) -> list[dict[str, Any]]:
    """Atomically replace one document's assignment state and unit relations."""

    target_document_id = str(document_id).strip()
    if not target_document_id:
        raise ValueError("document_id_required")
    unit_ids = list(
        dict.fromkeys(
            str(item).strip()
            for item in [primary_organization_unit_id, *organization_unit_ids]
            if str(item or "").strip()
        )
    )
    state = str(assignment_state or ("assigned" if primary_organization_unit_id else "unassigned")).strip()
    if state not in {"assigned", "shared", "unassigned"}:
        raise ValueError("document_assignment_state_invalid")
    if state == "assigned" and not str(primary_organization_unit_id or "").strip():
        raise ValueError("document_primary_organization_unit_required")
    if state != "assigned" and unit_ids:
        raise ValueError("document_nonassigned_state_has_unit_relations")
    now = datetime.now(timezone.utc)
    bounded_confidence = min(1.0, max(0.0, float(confidence)))
    relations: list[dict[str, Any]] = []
    for unit_id in unit_ids:
        relations.append(
            {
                "document_id": target_document_id,
                "organization_unit_id": unit_id,
                "relationship": (
                    "reviewing"
                    if unit_id == str(primary_organization_unit_id or "")
                    else "reference"
                ),
                "assignment_source": assignment_source,
                "confidence": bounded_confidence,
                "confirmation_status": confirmation_status,
                "created": now,
                "updated": now,
            }
        )
    await repo_query(
        "BEGIN TRANSACTION; "
        "IF !$only_if_absent OR array::len((SELECT VALUE id FROM document_organization_assignment WHERE document_id = $document_id LIMIT 1)) = 0 { "
        "DELETE document_organization_unit WHERE document_id = $document_id; "
        "DELETE document_organization_assignment WHERE document_id = $document_id; "
        "CREATE document_organization_assignment CONTENT $assignment; "
        "FOR $relation IN $relations { CREATE document_organization_unit CONTENT $relation; }; "
        "}; "
        "COMMIT TRANSACTION;",
        {
            "only_if_absent": only_if_absent,
            "document_id": target_document_id,
            "assignment": {
                "document_id": target_document_id,
                "assignment_state": state,
                "primary_organization_unit_id": (
                    str(primary_organization_unit_id).strip()
                    if primary_organization_unit_id
                    else None
                ),
                "assignment_source": assignment_source,
                "confidence": bounded_confidence,
                "confirmation_status": confirmation_status,
                "created": now,
                "updated": now,
            },
            "relations": relations,
        },
    )
    return relations


async def copy_document_unit_assignments(
    *,
    old_document_id: str | int,
    new_document_id: str | int,
    confirmation_status: str | None = None,
) -> list[dict[str, Any]]:
    rows = await repo_query(
        "SELECT * FROM document_organization_unit WHERE document_id = $document_id;",
        {"document_id": str(old_document_id)},
    )
    assignment_rows = await repo_query(
        "SELECT * FROM document_organization_assignment WHERE document_id = $document_id LIMIT 1;",
        {"document_id": str(old_document_id)},
    )
    primary = next(
        (
            str(item.get("organization_unit_id") or "")
            for item in rows
            if item.get("relationship") == "reviewing"
        ),
        None,
    )
    source_confirmation_status = (
        str(assignment_rows[0].get("confirmation_status") or "confirmed").strip()
        if assignment_rows
        else "confirmed"
    )
    effective_confirmation_status = confirmation_status or (
        "confirmed"
        if source_confirmation_status == "confirmed"
        else "needs_confirmation"
    )
    return await replace_document_unit_assignments(
        document_id=new_document_id,
        primary_organization_unit_id=primary,
        organization_unit_ids=[
            str(item.get("organization_unit_id") or "") for item in rows
        ],
        assignment_source="inherited_replacement",
        confidence=min(
            [float(item.get("confidence") or 1.0) for item in rows] or [1.0]
        ),
        confirmation_status=effective_confirmation_status,
        assignment_state=(
            str(assignment_rows[0].get("assignment_state") or "").strip()
            if assignment_rows
            else ("assigned" if primary else "unassigned")
        ),
    )


async def delete_document_unit_assignments(document_id: str | int) -> None:
    await repo_query(
        "BEGIN TRANSACTION; "
        "DELETE document_organization_unit WHERE document_id = $document_id; "
        "DELETE document_organization_assignment WHERE document_id = $document_id; "
        "COMMIT TRANSACTION;",
        {"document_id": str(document_id)},
    )


async def replace_procedure_unit_assignments(
    *,
    procedure_id: str,
    primary_organization_unit_id: str,
    supporting_organization_unit_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    target = str(procedure_id).strip()
    primary = str(primary_organization_unit_id).strip()
    if not target or not primary:
        raise ValueError("procedure_primary_organization_unit_required")
    unit_ids = list(
        dict.fromkeys(
            [
                primary,
                *(
                    str(item).strip()
                    for item in supporting_organization_unit_ids
                    if str(item).strip()
                ),
            ]
        )
    )
    await repo_query(
        "DELETE procedure_organization_unit WHERE procedure_id = $procedure_id;",
        {"procedure_id": target},
    )
    created: list[dict[str, Any]] = []
    for unit_id in unit_ids:
        rows = await repo_create(
            "procedure_organization_unit",
            {
                "procedure_id": target,
                "organization_unit_id": unit_id,
                "responsibility": "primary" if unit_id == primary else "support",
                "created": datetime.now(timezone.utc),
                "updated": datetime.now(timezone.utc),
            },
        )
        if isinstance(rows, list):
            created.extend(item for item in rows if isinstance(item, dict))
        elif isinstance(rows, dict):
            created.append(rows)
    return created


def _user_record_id(value: str) -> Any:
    text = str(value or "").strip()
    if text.startswith("user_account:") and text.count(":") == 1:
        return ensure_record_id(text)
    if text.startswith("user:") and text.count(":") == 1:
        text = text.split(":", 1)[1]
    if ":" in text:
        raise ValueError("user_record_id_invalid")
    return ensure_record_id(f"user_account:{text}")


async def active_officer_grants(user_id: str) -> list[dict[str, Any]]:
    return await repo_query(
        "SELECT * FROM officer_unit_grant WHERE officer_user = $officer "
        "AND revoked_at = NONE AND expires_at > time::now() ORDER BY expires_at ASC;",
        {"officer": _user_record_id(user_id)},
    )


async def effective_officer_domains(
    *, user_id: str, profile: Mapping[str, Any], units: Iterable[OrganizationUnitConfig]
) -> list[str]:
    scope = await resolve_officer_scope(user_id=user_id, profile=profile, units=units, mode="unit_primary")
    return list(scope.domains)


@dataclass(frozen=True)
class OfficerOperatingScope:
    mode: str
    primary_organization_unit_id: str | None = None
    organization_unit_ids: tuple[str, ...] = ()
    organization_unit_domain_grants: tuple[tuple[str, str], ...] = ()
    domains: tuple[str, ...] = ()
    proposal_unit_domains: tuple[tuple[str, str], ...] = ()

    def proposal_units(self, domain: str) -> list[str]:
        from api.legal_domains import canonicalize_legal_domain

        target = canonicalize_legal_domain(domain) or domain
        return list(dict.fromkeys(unit_id for unit_id, value in self.proposal_unit_domains
                                  if (canonicalize_legal_domain(value) or value) == target))

    def permits(self, unit_ids: Iterable[str], domain: str | None) -> bool:
        from api.legal_domains import canonicalize_legal_domain

        target = canonicalize_legal_domain(domain) or str(domain or "")
        if self.mode in {"legacy", "shadow"}:
            return bool(target) and any(
                (canonicalize_legal_domain(value) or value) == target for value in self.domains
            )
        targets = set(unit_ids)
        return any(
            unit_id in targets and (canonicalize_legal_domain(value) or value) == target
            for unit_id, value in (*self.proposal_unit_domains, *self.organization_unit_domain_grants)
        )


def officer_scope_from_records(
    *, profile: Mapping[str, Any], units: Iterable[OrganizationUnitConfig],
    grants: Iterable[Mapping[str, Any]], mode: str,
    support_only: bool = False, now: datetime | None = None,
) -> OfficerOperatingScope:
    """Request-time write scope; stale profile projections never grant rights."""
    mode = routing_mode(mode)
    nested = profile.get("profile")
    merged = {**(nested if isinstance(nested, dict) else {}), **profile}
    if merged.get("organization_unit_id"):
        mode = "unit_primary"
    if mode in {"legacy", "shadow"}:
        return OfficerOperatingScope(mode=mode, domains=tuple(merged.get("allowed_domains") or ()))
    active = {u.id: u for u in units if u.is_active and (not support_only or u.support_enabled)}
    primary = active.get(str(merged.get("organization_unit_id") or ""))
    unit_ids = [primary.id] if primary else []
    domains = list(domains_for_unit(primary)) if primary else []
    proposal_pairs = [(primary.id, domain) for domain in domains] if primary else []
    limited: list[tuple[str, str]] = []
    now = now or datetime.now(timezone.utc)
    for grant in grants:
        if grant.get("revoked_at"):
            continue
        expires = grant.get("expires_at")
        try:
            expiry = expires if isinstance(expires, datetime) else datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= now:
                continue
        except (ValueError, TypeError):
            continue
        unit = active.get(str(grant.get("organization_unit_id") or ""))
        if unit is None:
            continue
        unit_domains = domains_for_unit(unit)
        requested = grant.get("domain_codes") or []
        allowed = [d for d in unit_domains if d in requested] if requested else unit_domains
        if requested:
            limited.extend((unit.id, domain) for domain in allowed)
        else:
            unit_ids.append(unit.id)
        domains.extend(allowed)
        proposal_pairs.extend((unit.id, domain) for domain in allowed)
    return OfficerOperatingScope(
        mode=mode, primary_organization_unit_id=primary.id if primary else None,
        organization_unit_ids=tuple(dict.fromkeys(unit_ids)),
        organization_unit_domain_grants=tuple(dict.fromkeys(limited)),
        domains=tuple(dict.fromkeys(domains)),
        proposal_unit_domains=tuple(dict.fromkeys(proposal_pairs)),
    )


async def resolve_officer_scope(
    *, user_id: str, profile: Mapping[str, Any], units: Iterable[OrganizationUnitConfig],
    mode: str, support_only: bool = False,
) -> OfficerOperatingScope:
    try:
        grants = await active_officer_grants(user_id) if routing_mode(mode) in {"hybrid", "unit_primary"} else []
    except Exception as exc:
        logger.warning("Could not load cross-unit grants for {}: {}", user_id, exc)
        grants = []
    return officer_scope_from_records(profile=profile, units=units, grants=grants, mode=mode, support_only=support_only)


async def current_officer_scope(user_id: str, profile: Mapping[str, Any]) -> OfficerOperatingScope:
    from api.system_settings import active_organization_units, active_settings

    settings = await active_settings()
    mode = routing_mode(getattr(settings, "organization_routing_mode", "legacy"))
    units = await active_organization_units(settings)
    nested = profile.get("profile") if isinstance(profile.get("profile"), Mapping) else {}
    if str(profile.get("organization_unit_id") or nested.get("organization_unit_id") or ""):
        mode = "unit_primary"
    return await resolve_officer_scope(user_id=user_id, profile=profile, units=units, mode=mode)


async def create_officer_grant(
    *,
    officer_user_id: str,
    organization_unit_id: str,
    domain_codes: Iterable[str],
    reason: str,
    expires_at: datetime,
    granted_by_user_id: str | None,
    units: Iterable[OrganizationUnitConfig],
) -> dict[str, Any]:
    unit = find_unit(units, unit_id=organization_unit_id)
    if unit is None or not unit.is_active:
        raise ValueError("organization_unit_not_found")
    if len(reason.strip()) < 5:
        raise ValueError("officer_unit_grant_reason_required")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("officer_unit_grant_expiry_invalid")
    allowed = set(domains_for_unit(unit))
    requested = list(
        dict.fromkeys(str(item).strip() for item in domain_codes if str(item).strip())
    )
    if requested and not set(requested).issubset(allowed):
        raise ValueError("officer_unit_grant_domain_outside_unit")
    created = await repo_create(
        "officer_unit_grant",
        {
            "officer_user": _user_record_id(officer_user_id),
            "organization_unit_id": unit.id,
            "domain_codes": requested,
            "reason": reason.strip(),
            "granted_by": _user_record_id(granted_by_user_id) if granted_by_user_id else None,
            "expires_at": expires_at,
            "revoked_at": None,
        },
    )
    return created[0] if isinstance(created, list) else created


async def revoke_officer_grant(grant_id: str, *, officer_user_id: str) -> dict[str, Any] | None:
    rows = await repo_query(
        "SELECT * FROM officer_unit_grant WHERE id = $id LIMIT 1;",
        {"id": ensure_record_id(grant_id)},
    )
    if not rows:
        return None
    owner = rows[0].get("officer_user")
    if isinstance(owner, Mapping):
        owner = owner.get("id")
    if str(owner) != str(_user_record_id(officer_user_id)):
        return None
    revoked_at = datetime.now(timezone.utc)
    updated = await repo_update(
        "officer_unit_grant", grant_id, {"revoked_at": revoked_at}
    )
    return updated[0] if updated else {**rows[0], "revoked_at": revoked_at}
