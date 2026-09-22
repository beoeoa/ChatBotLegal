"""Read-only loader for the remediation procedure catalog proposal.

This module intentionally cannot be imported by serving code to resolve a
procedure.  It exists for review/validation and makes every un-attested record
fail closed until an owner supplies official source metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "release-data"
    / "legal"
    / "staging"
    / "uat-blocker-v1-procedure-catalog.json"
)
_REQUIRED_RECORD_KEYS = {
    "record_id",
    "procedure_id",
    "identity_status",
    "domain",
    "audience",
    "aliases",
    "hard_negatives",
    "source",
    "owner_review_status",
}


def load_staging_catalog(path: str | Path = CATALOG_PATH) -> dict[str, Any]:
    """Load and validate the proposal without making it runtime evidence."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("staging_catalog_must_be_object")
    if payload.get("serving") is not False:
        raise ValueError("staging_catalog_must_not_serve")
    if payload.get("activation_status") != "staging_review_required":
        raise ValueError("staging_catalog_requires_review")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("staging_catalog_records_missing")
    checked: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping) or not _REQUIRED_RECORD_KEYS <= set(record):
            raise ValueError("staging_catalog_record_contract_invalid")
        source = record.get("source")
        if not isinstance(source, Mapping):
            raise ValueError("staging_catalog_source_missing")
        # A proposal with no official URL/checksum is review material only.
        record_copy = dict(record)
        record_copy["serving"] = False
        record_copy["runtime_eligible"] = False
        record_copy["source_verification_status"] = (
            "verified" if source.get("url") and source.get("sha256") else "pending"
        )
        if record_copy["source_verification_status"] != "verified":
            record_copy["runtime_eligible"] = False
        checked.append(record_copy)
    return {**dict(payload), "records": checked, "runtime_eligible": False}


def runtime_records(*_: Any, **__: Any) -> list[dict[str, Any]]:
    """Always return no serving records; activation is a separate approval."""

    return []
