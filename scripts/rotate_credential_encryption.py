"""Rotate encrypted provider credentials without printing secret material.

The source and target encryption keys are read from environment variables so
they do not appear in shell history or process arguments. The command is a dry
run unless ``--execute`` is supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_KEY_ENV = "CHATBOTLEGAL_CREDENTIAL_SOURCE_KEY"
TARGET_KEY_ENV = "OPEN_NOTEBOOK_ENCRYPTION_KEY"


class RotationError(RuntimeError):
    """Raised when credential rotation cannot complete fail-closed."""


@dataclass(frozen=True)
class TokenRotation:
    token: str
    status: str


def derive_fernet(key: str) -> Fernet:
    """Use the same SHA-256 key derivation as the application runtime."""
    if not key:
        raise RotationError("Encryption key must not be empty")
    derived = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _looks_encrypted(value: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        return False
    if len(decoded) < 73:
        return False
    ciphertext_length = len(decoded) - 1 - 8 - 16 - 32
    return ciphertext_length > 0 and ciphertext_length % 16 == 0


def rotate_token(token: str, *, source_key: str, target_key: str) -> TokenRotation:
    """Return an idempotently target-key-encrypted token."""
    if not token:
        return TokenRotation(token=token, status="empty")

    target = derive_fernet(target_key)
    try:
        target.decrypt(token.encode("utf-8"))
        return TokenRotation(token=token, status="already_current")
    except InvalidToken:
        pass

    if not _looks_encrypted(token):
        return TokenRotation(
            token=target.encrypt(token.encode("utf-8")).decode("utf-8"),
            status="rotated_plaintext",
        )

    source = derive_fernet(source_key)
    try:
        plaintext = source.decrypt(token.encode("utf-8"))
    except InvalidToken as exc:
        raise RotationError(
            "Credential token cannot be decrypted with the source or target key"
        ) from exc

    return TokenRotation(
        token=target.encrypt(plaintext).decode("utf-8"),
        status="rotated",
    )


def _load_dotenv_key(path: Path, key_name: str) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key_name:
            return value.strip().strip('"').strip("'") or None
    return None


async def _query_credentials() -> list[dict[str, Any]]:
    from open_notebook.database.repository import repo_query

    return await repo_query(
        "SELECT id, name, provider, api_key FROM credential ORDER BY id;",
        {},
    )


async def _set_token(record_id: str, token: str) -> None:
    from open_notebook.database.repository import ensure_record_id, repo_query

    result = await repo_query(
        "UPDATE $record_id SET api_key = $api_key, updated = time::now() RETURN AFTER;",
        {
            "record_id": ensure_record_id(record_id),
            "api_key": token,
        },
    )
    if len(result) != 1:
        raise RotationError(f"Credential update did not affect exactly one record: {record_id}")


async def _rotate(execute: bool, backup_dir: Path) -> dict[str, Any]:
    source_key = os.environ.get(SOURCE_KEY_ENV)
    target_key = os.environ.get(TARGET_KEY_ENV) or _load_dotenv_key(
        PROJECT_ROOT / ".env",
        TARGET_KEY_ENV,
    )
    if not source_key:
        raise RotationError(f"{SOURCE_KEY_ENV} is required")
    if not target_key:
        raise RotationError(f"{TARGET_KEY_ENV} is required")
    if source_key == target_key:
        raise RotationError("Source and target encryption keys must differ")

    records = await _query_credentials()
    planned: list[tuple[dict[str, Any], TokenRotation]] = []
    counts: dict[str, int] = {}
    for record in records:
        api_key = record.get("api_key")
        if not isinstance(api_key, str):
            result = TokenRotation(token="", status="empty")
        else:
            result = rotate_token(
                api_key,
                source_key=source_key,
                target_key=target_key,
            )
        counts[result.status] = counts.get(result.status, 0) + 1
        planned.append((record, result))

    changed = [
        (record, result)
        for record, result in planned
        if result.status in {"rotated", "rotated_plaintext"}
    ]
    if not execute:
        return {
            "status": "dry_run",
            "credential_count": len(records),
            "change_count": len(changed),
            "counts": counts,
        }

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        "credential-ciphertext-before-rotation-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + ".json"
    )
    backup_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "records": [
                    {
                        "id": str(record.get("id")),
                        "provider": record.get("provider"),
                        "api_key": record.get("api_key"),
                    }
                    for record, _ in changed
                ],
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    updated: list[tuple[str, str]] = []
    try:
        for record, result in changed:
            record_id = str(record["id"])
            original = str(record["api_key"])
            await _set_token(record_id, result.token)
            updated.append((record_id, original))

        verification = await _query_credentials()
        target = derive_fernet(target_key)
        for record in verification:
            token = record.get("api_key")
            if token:
                target.decrypt(str(token).encode("utf-8"))
    except Exception as exc:
        rollback_errors: list[str] = []
        for record_id, original in reversed(updated):
            try:
                await _set_token(record_id, original)
            except Exception:
                rollback_errors.append(record_id)
        if rollback_errors:
            raise RotationError(
                "Credential rotation failed and ciphertext rollback was incomplete"
            ) from exc
        raise RotationError(
            "Credential rotation failed; original ciphertext was restored"
        ) from exc

    return {
        "status": "rotated",
        "credential_count": len(records),
        "change_count": len(changed),
        "counts": counts,
        "backup_file": str(backup_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("data/release_backups/credential-rotation"),
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_rotate(args.execute, args.backup_dir))
    except RotationError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
