"""Read-only System Capability Registry for release-readiness audits.

The registry is source-controlled.  This module never changes runtime routes;
it only validates ownership/policy metadata and reconciles the manifest with
the current FastAPI/Next.js source tree.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = ROOT / "config" / "system-capabilities.json"

CapabilityKind = Literal["frontend_route", "api_route", "background_job"]
CapabilityStatus = Literal["active", "internal", "deprecated", "disabled"]
CapabilityRole = Literal["public", "citizen", "officer", "admin", "system"]
Sensitivity = Literal["public", "internal", "personal", "restricted"]


class CapabilityRegistryError(ValueError):
    """Stable fail-closed registry validation error."""


class CapabilityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    kind: CapabilityKind
    path: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    roles: tuple[CapabilityRole, ...] = Field(min_length=1)
    data_source: str = Field(min_length=1)
    sensitivity: Sensitivity
    audit: str = Field(min_length=1)
    retention: str = Field(min_length=1)
    sla: str = Field(min_length=1)
    feature_flag: str | None = None
    tests: tuple[str, ...] = Field(min_length=1)
    rollback: str = Field(min_length=1)
    status: CapabilityStatus

    @field_validator(
        "path",
        "owner",
        "data_source",
        "audit",
        "retention",
        "sla",
        "rollback",
    )
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("roles", "tests")
    @classmethod
    def _must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("items must be unique")
        if any(not str(item).strip() for item in value):
            raise ValueError("items must not be blank")
        return value


@dataclass(frozen=True)
class RegistryReconciliation:
    missing_frontend_routes: list[str]
    missing_api_routers: list[str]
    duplicate_paths: list[str]
    stale_active_frontend_routes: list[str]
    stale_active_api_routers: list[str]

    @property
    def passed(self) -> bool:
        return not any(
            (
                self.missing_frontend_routes,
                self.missing_api_routers,
                self.duplicate_paths,
                self.stale_active_frontend_routes,
                self.stale_active_api_routers,
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "passed" if self.passed else "failed",
            "missing_frontend_routes": self.missing_frontend_routes,
            "missing_api_routers": self.missing_api_routers,
            "duplicate_paths": self.duplicate_paths,
            "stale_active_frontend_routes": self.stale_active_frontend_routes,
            "stale_active_api_routers": self.stale_active_api_routers,
        }


def validate_registry_payload(payload: Mapping[str, Any]) -> list[CapabilityRecord]:
    if payload.get("schema_version") != "capability-registry-v1":
        raise CapabilityRegistryError("CAPABILITY_REGISTRY_SCHEMA_UNSUPPORTED")
    raw_records = payload.get("capabilities")
    if not isinstance(raw_records, list) or not raw_records:
        raise CapabilityRegistryError("CAPABILITY_REGISTRY_EMPTY")
    try:
        records = [CapabilityRecord.model_validate(item) for item in raw_records]
    except ValidationError as exc:
        raise CapabilityRegistryError("CAPABILITY_REGISTRY_INVALID") from exc

    ids = [record.capability_id for record in records]
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        raise CapabilityRegistryError(
            "CAPABILITY_REGISTRY_DUPLICATE_ID:" + ",".join(duplicate_ids)
        )

    for record in records:
        if record.status == "disabled" and "public" in record.roles:
            raise CapabilityRegistryError(
                f"CAPABILITY_DISABLED_PUBLIC:{record.capability_id}"
            )
        if record.sensitivity in {"personal", "restricted"} and record.audit == "none":
            raise CapabilityRegistryError(
                f"CAPABILITY_SENSITIVE_AUDIT_REQUIRED:{record.capability_id}"
            )
    return records


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> list[CapabilityRecord]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityRegistryError("CAPABILITY_REGISTRY_UNREADABLE") from exc
    if not isinstance(payload, dict):
        raise CapabilityRegistryError("CAPABILITY_REGISTRY_ROOT_INVALID")
    return validate_registry_payload(payload)


def _frontend_route_from_page(app_root: Path, page: Path) -> str:
    relative = page.relative_to(app_root)
    parts = [
        part
        for part in relative.parts[:-1]
        if not (part.startswith("(") and part.endswith(")"))
    ]
    return "/" + "/".join(parts) if parts else "/"


def discover_frontend_routes(root: Path = ROOT) -> set[str]:
    app_root = root / "frontend" / "src" / "app"
    if not app_root.is_dir():
        return set()
    return {
        _frontend_route_from_page(app_root, path)
        for path in app_root.rglob("page.tsx")
        if path.is_file()
    }


def discover_api_routers(root: Path = ROOT) -> set[str]:
    main_path = root / "api" / "main.py"
    if not main_path.is_file():
        return set()
    text = main_path.read_text(encoding="utf-8")
    aliases = {"commands_router": "commands"}
    names = set(
        re.findall(
            r"app\.include_router\(\s*([A-Za-z_][A-Za-z0-9_]*)\.router\b",
            text,
            flags=re.MULTILINE,
        )
    )
    return {aliases.get(name, name) for name in names}


def _duplicate_paths(records: Iterable[CapabilityRecord]) -> list[str]:
    keys = [(record.kind, record.path) for record in records]
    return sorted(
        f"{kind}:{path}"
        for (kind, path), count in Counter(keys).items()
        if count > 1
    )


def reconcile_registry(
    records: Iterable[CapabilityRecord], *, root: Path = ROOT
) -> RegistryReconciliation:
    materialized = list(records)
    discovered_frontend = discover_frontend_routes(root)
    discovered_api = discover_api_routers(root)

    registered_frontend = {
        record.path
        for record in materialized
        if record.kind == "frontend_route"
    }
    registered_api = {
        record.path.removeprefix("api:")
        for record in materialized
        if record.kind == "api_route" and record.status != "disabled"
    }
    stale_frontend = sorted(
        record.path
        for record in materialized
        if record.kind == "frontend_route"
        and record.status in {"active", "internal", "deprecated"}
        and record.path not in discovered_frontend
    )
    stale_api = sorted(
        record.path.removeprefix("api:")
        for record in materialized
        if record.kind == "api_route"
        and record.status in {"active", "internal", "deprecated"}
        and record.path.removeprefix("api:") not in discovered_api
    )

    return RegistryReconciliation(
        missing_frontend_routes=sorted(discovered_frontend - registered_frontend),
        missing_api_routers=sorted(discovered_api - registered_api),
        duplicate_paths=_duplicate_paths(materialized),
        stale_active_frontend_routes=stale_frontend,
        stale_active_api_routers=stale_api,
    )


def registry_summary(
    records: Iterable[CapabilityRecord], *, root: Path = ROOT
) -> dict[str, object]:
    materialized = list(records)
    reconciliation = reconcile_registry(materialized, root=root)
    return {
        "schema_version": "capability-registry-v1",
        "count": len(materialized),
        "by_kind": dict(sorted(Counter(item.kind for item in materialized).items())),
        "by_status": dict(sorted(Counter(item.status for item in materialized).items())),
        "reconciliation": reconciliation.as_dict(),
    }
