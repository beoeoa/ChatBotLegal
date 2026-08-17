"""Canonical tamper-evident audit chain with external Ed25519 checkpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


GENESIS_HASH = "0" * 64
CRITICAL_ACTION_PREFIXES = (
    "legal.candidate.review",
    "legal.candidate.import",
    "legal.import",
    "legal.lifecycle",
    "admin.legal_validity",
    "legal.validity",
    "legal.version.activate",
    "legal.corpus",
)


class AuditIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class IntegrityResult:
    valid: bool
    reason_code: str | None = None
    sequence: int = 0
    head_hash: str = GENESIS_HASH


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _entry_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get(key)
        for key in (
            "chain_id",
            "sequence",
            "event_time",
            "event_type",
            "actor_id",
            "actor_role",
            "object_type",
            "object_id",
            "detail_hash",
            "previous_hash",
        )
    }


def verify_audit_chain(entries: Sequence[Mapping[str, Any]]) -> IntegrityResult:
    previous = GENESIS_HASH
    for index, entry in enumerate(entries, 1):
        if int(entry.get("sequence") or 0) != index:
            return IntegrityResult(False, "sequence_gap", index - 1, previous)
        if str(entry.get("previous_hash") or "") != previous:
            return IntegrityResult(False, "previous_hash_mismatch", index - 1, previous)
        expected = _hash(_entry_payload(entry))
        if str(entry.get("entry_hash") or "") != expected:
            return IntegrityResult(False, "entry_hash_mismatch", index - 1, previous)
        previous = expected
    return IntegrityResult(True, None, len(entries), previous)


def append_audit_entry(
    entries: Sequence[Mapping[str, Any]],
    *,
    event_time: str,
    event_type: str,
    actor_id: str,
    actor_role: str,
    object_type: str,
    object_id: str,
    detail: Mapping[str, Any],
    chain_id: str = "legal-critical",
) -> dict[str, Any]:
    integrity = verify_audit_chain(entries)
    if not integrity.valid:
        raise AuditIntegrityError(
            f"existing_chain_invalid:{integrity.reason_code or 'unknown'}"
        )
    entry = {
        "chain_id": str(chain_id),
        "sequence": len(entries) + 1,
        "event_time": str(event_time),
        "event_type": str(event_type),
        "actor_id": str(actor_id),
        "actor_role": str(actor_role),
        "object_type": str(object_type),
        "object_id": str(object_id),
        "detail_hash": _hash(dict(detail)),
        "previous_hash": integrity.head_hash,
    }
    entry["entry_hash"] = _hash(_entry_payload(entry))
    return entry


def public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _checkpoint_payload(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: checkpoint.get(key)
        for key in (
            "checkpoint_id",
            "chain_id",
            "created_at",
            "sequence",
            "head_hash",
            "public_key_fingerprint",
        )
    }


def create_checkpoint(
    entries: Sequence[Mapping[str, Any]],
    *,
    chain_id: str,
    checkpoint_id: str,
    created_at: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    integrity = verify_audit_chain(entries)
    if not integrity.valid:
        raise AuditIntegrityError(
            f"checkpoint_chain_invalid:{integrity.reason_code or 'unknown'}"
        )
    checkpoint = {
        "checkpoint_id": str(checkpoint_id),
        "chain_id": str(chain_id),
        "created_at": str(created_at),
        "sequence": integrity.sequence,
        "head_hash": integrity.head_hash,
        "public_key_fingerprint": public_key_fingerprint(private_key.public_key()),
        "algorithm": "Ed25519",
    }
    checkpoint["signature"] = base64.b64encode(
        private_key.sign(_canonical(_checkpoint_payload(checkpoint)))
    ).decode("ascii")
    return checkpoint


def verify_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    entries: Sequence[Mapping[str, Any]],
    public_key: Ed25519PublicKey,
) -> IntegrityResult:
    integrity = verify_audit_chain(entries)
    if not integrity.valid:
        return integrity
    checkpoint_chain_id = str(checkpoint.get("chain_id") or "")
    if not checkpoint_chain_id or any(
        str(entry.get("chain_id") or "") != checkpoint_chain_id for entry in entries
    ):
        return IntegrityResult(False, "checkpoint_chain_mismatch", integrity.sequence, integrity.head_hash)
    if str(checkpoint.get("algorithm") or "") != "Ed25519":
        return IntegrityResult(False, "checkpoint_algorithm_invalid", integrity.sequence, integrity.head_hash)
    if (
        int(checkpoint.get("sequence") or 0) != integrity.sequence
        or str(checkpoint.get("head_hash") or "") != integrity.head_hash
    ):
        return IntegrityResult(False, "checkpoint_head_mismatch", integrity.sequence, integrity.head_hash)
    if str(checkpoint.get("public_key_fingerprint") or "") != public_key_fingerprint(public_key):
        return IntegrityResult(False, "checkpoint_public_key_mismatch", integrity.sequence, integrity.head_hash)
    try:
        signature = base64.b64decode(str(checkpoint.get("signature") or ""), validate=True)
        public_key.verify(signature, _canonical(_checkpoint_payload(checkpoint)))
    except (ValueError, InvalidSignature):
        return IntegrityResult(False, "checkpoint_signature_invalid", integrity.sequence, integrity.head_hash)
    return integrity


def load_private_key_from_file(path: str, password: bytes | None = None) -> Ed25519PrivateKey:
    with open(path, "rb") as stream:
        key = serialization.load_pem_private_key(stream.read(), password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("audit_checkpoint_key_must_be_ed25519")
    return key


def load_public_key_from_file(path: str) -> Ed25519PublicKey:
    with open(path, "rb") as stream:
        key = serialization.load_pem_public_key(stream.read())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("audit_checkpoint_key_must_be_ed25519")
    return key


def audit_chain_enabled() -> bool:
    """Return the explicit rollout flag; disabled remains the safe pre-migration default."""

    return str(os.getenv("LEGAL_AUDIT_CHAIN_ENABLED") or "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_critical_legal_action(action: str) -> bool:
    normalized = str(action or "").strip().casefold()
    return any(normalized.startswith(prefix) for prefix in CRITICAL_ACTION_PREFIXES)


async def append_critical_audit_event(
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    actor_user_id: str | None,
    actor_role: str | None,
    details: Mapping[str, Any] | None,
    chain_id: str = "legal-critical",
    query=None,
    create=None,
) -> dict[str, Any] | None:
    """Append one chain-required event, failing closed when the feature is enabled.

    The sidecar is dormant until its separately approved migration is applied and
    ``LEGAL_AUDIT_CHAIN_ENABLED`` is set.  Once enabled, an invalid/missing head or
    a uniqueness race raises instead of allowing an untraceable legal mutation.
    """

    if not audit_chain_enabled() or not is_critical_legal_action(action):
        return None
    if query is None or create is None:
        from open_notebook.database.repository import repo_create, repo_query

        query = query or repo_query
        create = create or repo_create
    rows = await query(
        "SELECT * FROM legal_audit_chain WHERE chain_id = $chain_id ORDER BY sequence ASC;",
        {"chain_id": chain_id},
    )
    entries = list(rows or [])
    entry = append_audit_entry(
        entries,
        event_time=datetime.now(timezone.utc).isoformat(),
        event_type=action,
        actor_id=str(actor_user_id or "system"),
        actor_role=str(actor_role or "system"),
        object_type=entity_type,
        object_id=str(entity_id or "unknown"),
        detail=dict(details or {}),
        chain_id=chain_id,
    )
    created = await create("legal_audit_chain", entry)
    if isinstance(created, list):
        created = created[0] if created else None
    if not isinstance(created, Mapping):
        raise AuditIntegrityError("audit_chain_append_not_confirmed")
    return dict(created)


async def create_persisted_checkpoint(
    *,
    checkpoint_id: str,
    chain_id: str = "legal-critical",
    private_key_path: str | None = None,
    query=None,
    create=None,
) -> dict[str, Any]:
    """Sign and persist a chain head using a private key loaded outside the DB."""

    if query is None or create is None:
        from open_notebook.database.repository import repo_create, repo_query

        query = query or repo_query
        create = create or repo_create
    key_path = str(
        private_key_path or os.getenv("LEGAL_AUDIT_CHECKPOINT_PRIVATE_KEY_FILE") or ""
    ).strip()
    if not key_path:
        raise AuditIntegrityError("checkpoint_private_key_file_missing")
    rows = await query(
        "SELECT * FROM legal_audit_chain WHERE chain_id = $chain_id ORDER BY sequence ASC;",
        {"chain_id": chain_id},
    )
    checkpoint = create_checkpoint(
        list(rows or []),
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        private_key=load_private_key_from_file(key_path),
    )
    created = await create("legal_audit_checkpoint", checkpoint)
    if isinstance(created, list):
        created = created[0] if created else None
    if not isinstance(created, Mapping):
        raise AuditIntegrityError("audit_checkpoint_not_confirmed")
    return dict(created)


async def verify_persisted_checkpoint(
    *,
    checkpoint_id: str,
    public_key_path: str | None = None,
    query=None,
) -> IntegrityResult:
    """Verify the database chain and one externally anchored checkpoint.

    Later entries are allowed, but the whole current chain must first be valid;
    the checkpoint then verifies the exact historical prefix it signed.
    """

    if query is None:
        from open_notebook.database.repository import repo_query

        query = repo_query
    key_path = str(
        public_key_path or os.getenv("LEGAL_AUDIT_CHECKPOINT_PUBLIC_KEY_FILE") or ""
    ).strip()
    if not key_path:
        raise AuditIntegrityError("checkpoint_public_key_file_missing")
    checkpoints = await query(
        "SELECT * FROM legal_audit_checkpoint WHERE checkpoint_id = $checkpoint_id LIMIT 1;",
        {"checkpoint_id": checkpoint_id},
    )
    if not checkpoints:
        return IntegrityResult(False, "checkpoint_not_found")
    checkpoint = dict(checkpoints[0])
    chain_id = str(checkpoint.get("chain_id") or "")
    entries = list(
        await query(
            "SELECT * FROM legal_audit_chain WHERE chain_id = $chain_id ORDER BY sequence ASC;",
            {"chain_id": chain_id},
        )
        or []
    )
    full_integrity = verify_audit_chain(entries)
    if not full_integrity.valid:
        return full_integrity
    sequence = int(checkpoint.get("sequence") or 0)
    if sequence < 0 or len(entries) < sequence:
        return IntegrityResult(False, "checkpoint_head_missing", len(entries), full_integrity.head_hash)
    return verify_checkpoint(
        checkpoint,
        entries=entries[:sequence],
        public_key=load_public_key_from_file(key_path),
    )
