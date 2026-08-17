"""Server-authoritative commune procedure/domain scope for Feature 017.

The compatibility manifest is read-only staging evidence.  Once PostgreSQL is
active, only procedures in the active release are accepted for new officer
submissions; JSON is never used as a second writable legal truth.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
COMPATIBILITY_MANIFEST = (
    ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
)
ProcedureScopeLookup = Callable[[str], str | None]


@lru_cache(maxsize=4)
def load_commune_procedure_domains(path_text: str = str(COMPATIBILITY_MANIFEST)) -> dict[str, str]:
    path = Path(path_text)
    if not path.is_file():
        raise RuntimeError("FORM_PROCEDURE_SCOPE_UNAVAILABLE")
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for item in payload.get("procedures") or []:
        if str(item.get("executing_level") or "").casefold() != "commune":
            continue
        procedure_id = str(item.get("procedure_id") or "").strip()
        domain = str(item.get("domain") or "").strip()
        if not procedure_id or not domain:
            raise RuntimeError("FORM_PROCEDURE_SCOPE_INVALID")
        previous = result.setdefault(procedure_id, domain)
        if previous != domain:
            raise RuntimeError("FORM_PROCEDURE_SCOPE_CONFLICT")
    if len(result) != 191:
        raise RuntimeError("FORM_PROCEDURE_SCOPE_INCOMPLETE")
    return result


def lookup_from_manifest(manifest: Mapping[str, Any] | None) -> ProcedureScopeLookup:
    by_id = {
        str(item.get("procedure_id") or ""): str(item.get("domain") or "")
        for item in (manifest or {}).get("procedures") or []
        if item.get("procedure_id") and item.get("domain")
    }
    return lambda procedure_id: by_id.get(str(procedure_id or "").strip())


def configured_procedure_scope_lookup(
    *,
    repository: Any,
    governance_source: str,
) -> ProcedureScopeLookup:
    source = str(governance_source or "json_compat").casefold()
    if source == "postgres_active":
        def active_lookup(procedure_id: str) -> str | None:
            release = repository.active_release()
            manifest = (release or {}).get("manifest") or release or {}
            return lookup_from_manifest(manifest)(procedure_id)

        return active_lookup
    domains = load_commune_procedure_domains()
    return lambda procedure_id: domains.get(str(procedure_id or "").strip())


def fixed_procedure_scope_lookup(mapping: Mapping[str, str]) -> ProcedureScopeLookup:
    frozen = {str(key): str(value) for key, value in mapping.items()}
    return lambda procedure_id: frozen.get(str(procedure_id or "").strip())
