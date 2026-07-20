"""Safely copy only the active cloud chat model configuration to a fresh pilot.

The source credential is stored encrypted in SurrealDB.  This tool never
prints it, exports it to disk, or writes it to an evidence artifact.  The
pilot must use the same encryption key as the source application so its normal
credential layer can decrypt the copied value at runtime.

The command is dry-run by default.  ``--apply`` copies precisely three records
to the isolated pilot database: the selected chat model, its linked credential
and the ``default_models`` record containing only ``default_chat_model``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from surrealdb import AsyncSurreal, RecordID


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT / "specs" / "003-stream-ask-experience" / "evidence" / "pilot-cloud-config.json"
)
DEFAULTS_RECORD = "open_notebook:default_models"


def record_id(value: Any) -> str:
    """Normalize Surreal record identifiers without serializing record data."""
    identifier = str(value or "").strip()
    if not identifier or ":" not in identifier:
        raise ValueError("Expected a Surreal record identifier")
    return identifier


def first_row(result: Any) -> dict[str, Any] | None:
    """Accept the result variants returned by supported SurrealDB clients."""
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        if not result:
            return None
        first = result[0]
        if isinstance(first, dict):
            return first
        if isinstance(first, list) and first and isinstance(first[0], dict):
            return first[0]
    return None


def without_id(row: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare a CONTENT payload while retaining encrypted credential fields."""
    return {key: value for key, value in row.items() if key != "id"}


def safe_fingerprint(*identifiers: str) -> str:
    return hashlib.sha256("|".join(identifiers).encode("utf-8")).hexdigest()


async def connect(url: str, username: str, password: str, namespace: str, database: str):
    client = AsyncSurreal(url)
    await client.signin({"username": username, "password": password})
    await client.use(namespace, database)
    return client


async def select_only(client: Any, identifier: str) -> dict[str, Any] | None:
    result = await client.query(
        "SELECT * FROM ONLY $record_id;",
        {"record_id": RecordID.parse(identifier)},
    )
    return first_row(result)


async def upsert_content(client: Any, identifier: str, payload: Mapping[str, Any]) -> None:
    await client.query(
        "UPSERT $record_id CONTENT $payload;",
        {
            "record_id": RecordID.parse(identifier),
            "payload": dict(payload),
        },
    )


async def copy_active_chat_model(
    *,
    source: Any,
    target: Any,
    apply: bool,
) -> dict[str, Any]:
    defaults = await select_only(source, DEFAULTS_RECORD)
    if not defaults:
        raise RuntimeError("Source default_models record is missing")
    model_identifier = record_id(defaults.get("default_chat_model"))
    model = await select_only(source, model_identifier)
    if not model:
        raise RuntimeError("Source default_chat_model record is missing")
    if str(model.get("type") or "") != "language":
        raise RuntimeError("Source default_chat_model is not a language model")
    credential_identifier = record_id(model.get("credential"))
    credential = await select_only(source, credential_identifier)
    if not credential:
        raise RuntimeError("Source model credential is missing")

    summary = {
        "schema_version": 1,
        "mode": "apply" if apply else "dry_run",
        "source_model_id": model_identifier,
        "source_credential_id": credential_identifier,
        "provider": str(model.get("provider") or "unknown"),
        "model_name": str(model.get("name") or "unknown"),
        "config_fingerprint": safe_fingerprint(model_identifier, credential_identifier),
        "secret_exported": False,
        "target_records": [credential_identifier, model_identifier, DEFAULTS_RECORD],
    }
    if not apply:
        return summary

    await upsert_content(target, credential_identifier, without_id(credential))
    await upsert_content(target, model_identifier, without_id(model))
    await upsert_content(
        target,
        DEFAULTS_RECORD,
        {"default_chat_model": RecordID.parse(model_identifier)},
    )

    copied_default = await select_only(target, DEFAULTS_RECORD)
    copied_model = await select_only(target, model_identifier)
    copied_credential = await select_only(target, credential_identifier)
    if not copied_default or not copied_model or not copied_credential:
        raise RuntimeError("Pilot verification could not read the copied configuration")
    if record_id(copied_default.get("default_chat_model")) != model_identifier:
        raise RuntimeError("Pilot default_chat_model verification failed")
    summary["verified"] = True
    return summary


def write_evidence(path: Path, summary: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    password = os.environ.get("SURREAL_PASSWORD", "root")
    source = await connect(
        args.surreal_url,
        args.surreal_user,
        password,
        args.source_namespace,
        args.source_database,
    )
    target = await connect(
        args.surreal_url,
        args.surreal_user,
        password,
        args.target_namespace,
        args.target_database,
    )
    try:
        return await copy_active_chat_model(source=source, target=target, apply=args.apply)
    finally:
        await source.close()
        await target.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Copy the three records to pilot")
    parser.add_argument("--surreal-url", default="ws://127.0.0.1:8000/rpc")
    parser.add_argument("--surreal-user", default=os.environ.get("SURREAL_USER", "root"))
    parser.add_argument("--source-namespace", default="open_notebook")
    parser.add_argument("--source-database", default="open_notebook")
    parser.add_argument("--target-namespace", default="open_notebook_pilot")
    parser.add_argument("--target-database", default="open_notebook_pilot")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = asyncio.run(run(args))
    write_evidence(args.evidence, summary)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
