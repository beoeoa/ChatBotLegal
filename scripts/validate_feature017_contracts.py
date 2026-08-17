"""Validate Feature 017 JSON contracts without network or database access."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "specs" / "017-procedure-form-governance" / "contracts"


def validate() -> dict[str, object]:
    files = ("golden-case-v3.schema.json", "form-release-manifest.schema.json")
    payloads = {}
    for name in files:
        payload = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
        if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError(f"FEATURE017_SCHEMA_DRAFT_INVALID:{name}")
        if payload.get("additionalProperties") is not False:
            raise ValueError(f"FEATURE017_SCHEMA_NOT_STRICT:{name}")
        payloads[name] = payload.get("title")
    return {"status": "passed", "contracts": payloads}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))

