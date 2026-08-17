from __future__ import annotations

from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from api.legal_audit_chain import (
    AuditIntegrityError,
    append_audit_entry,
    append_critical_audit_event,
    create_checkpoint,
    verify_audit_chain,
    verify_checkpoint,
    verify_persisted_checkpoint,
)


def _chain() -> list[dict]:
    entries: list[dict] = []
    for index, event in enumerate(
        ("candidate_reviewed", "document_approved", "index_activated"), 1
    ):
        entries.append(
            append_audit_entry(
                entries,
                event_time=f"2026-08-10T10:0{index}:00Z",
                event_type=event,
                actor_id="admin-1",
                actor_role="admin",
                object_type="legal_document",
                object_id="doc-1",
                detail={"reason_code": "reviewed", "revision": index},
            )
        )
    return entries


def test_chain_detects_edit_delete_insert_and_reorder():
    entries = _chain()
    assert verify_audit_chain(entries).valid is True

    edited = deepcopy(entries)
    edited[0]["detail_hash"] = "f" * 64
    deleted = [entries[0], entries[2]]
    inserted = deepcopy(entries)
    inserted.insert(1, deepcopy(entries[0]))
    reordered = [entries[1], entries[0], entries[2]]

    assert verify_audit_chain(edited).reason_code == "entry_hash_mismatch"
    assert verify_audit_chain(deleted).reason_code == "sequence_gap"
    assert verify_audit_chain(inserted).reason_code == "sequence_gap"
    assert verify_audit_chain(reordered).reason_code == "sequence_gap"


def test_checkpoint_detects_tail_deletion_and_wrong_public_key():
    entries = _chain()
    private_key = Ed25519PrivateKey.generate()
    checkpoint = create_checkpoint(
        entries,
        chain_id="legal-critical",
        checkpoint_id="cp-1",
        created_at="2026-08-10T10:10:00Z",
        private_key=private_key,
    )

    assert verify_checkpoint(
        checkpoint, entries=entries, public_key=private_key.public_key()
    ).valid is True
    assert verify_checkpoint(
        checkpoint, entries=entries[:-1], public_key=private_key.public_key()
    ).reason_code == "checkpoint_head_mismatch"
    assert verify_checkpoint(
        checkpoint,
        entries=entries,
        public_key=Ed25519PrivateKey.generate().public_key(),
    ).reason_code == "checkpoint_public_key_mismatch"


def test_chain_required_append_fails_closed_on_tampered_head():
    entries = _chain()
    entries[-1]["entry_hash"] = "0" * 64
    with pytest.raises(AuditIntegrityError, match="existing_chain_invalid"):
        append_audit_entry(
            entries,
            event_time="2026-08-10T10:20:00Z",
            event_type="document_published",
            actor_id="admin-1",
            actor_role="admin",
            object_type="legal_document",
            object_id="doc-1",
            detail={"reason_code": "approved"},
        )


def test_entry_stores_detail_hash_not_raw_sensitive_payload():
    entry = append_audit_entry(
        [],
        event_time="2026-08-10T10:00:00Z",
        event_type="provider_egress",
        actor_id="citizen-1",
        actor_role="citizen",
        object_type="ask",
        object_id="trace-1",
        detail={"question": "CCCD 012345678901", "reason_code": "redacted"},
    )

    assert "detail" not in entry
    assert "012345678901" not in str(entry)
    assert len(entry["detail_hash"]) == 64


@pytest.mark.asyncio
async def test_enabled_critical_write_appends_sidecar_before_legacy_log(monkeypatch):
    monkeypatch.setenv("LEGAL_AUDIT_CHAIN_ENABLED", "true")
    stored: list[dict] = []

    async def query(_sql, _params):
        return list(stored)

    async def create(table, payload):
        assert table == "legal_audit_chain"
        stored.append(dict(payload))
        return dict(payload)

    result = await append_critical_audit_event(
        action="legal.candidate.review",
        entity_type="legal_crawl_candidate",
        entity_id="candidate-1",
        actor_user_id="admin-1",
        actor_role="admin",
        details={"decision": "approved", "raw_note": "khong luu truc tiep"},
        query=query,
        create=create,
    )

    assert result and result["sequence"] == 1
    assert "raw_note" not in str(result)
    assert verify_audit_chain(stored).valid is True


@pytest.mark.asyncio
async def test_enabled_critical_write_fails_closed_on_tampered_database_head(monkeypatch):
    monkeypatch.setenv("LEGAL_AUDIT_CHAIN_ENABLED", "true")
    stored = _chain()
    stored[-1]["entry_hash"] = "0" * 64

    async def query(_sql, _params):
        return stored

    async def create(_table, _payload):
        raise AssertionError("must not write after failed integrity verification")

    with pytest.raises(AuditIntegrityError, match="existing_chain_invalid"):
        await append_critical_audit_event(
            action="admin.legal_validity.decision",
            entity_type="legal_validity_event",
            entity_id="event-1",
            actor_user_id="admin-1",
            actor_role="admin",
            details={"action": "confirmed"},
            query=query,
            create=create,
        )


@pytest.mark.asyncio
async def test_persisted_checkpoint_verifies_historical_prefix_and_current_chain(tmp_path):
    entries = _chain()
    key = Ed25519PrivateKey.generate()
    checkpoint = create_checkpoint(
        entries[:2],
        chain_id="legal-critical",
        checkpoint_id="cp-persisted",
        created_at="2026-08-10T10:10:00Z",
        private_key=key,
    )
    public_key_path = tmp_path / "audit-public.pem"
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    async def query(sql, _params):
        return [checkpoint] if "legal_audit_checkpoint" in sql else entries

    result = await verify_persisted_checkpoint(
        checkpoint_id="cp-persisted",
        public_key_path=str(public_key_path),
        query=query,
    )
    assert result.valid is True
    assert result.sequence == 2

    entries[-1]["entry_hash"] = "0" * 64
    tampered = await verify_persisted_checkpoint(
        checkpoint_id="cp-persisted",
        public_key_path=str(public_key_path),
        query=query,
    )
    assert tampered.valid is False
    assert tampered.reason_code == "entry_hash_mismatch"
