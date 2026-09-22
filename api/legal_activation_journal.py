"""Durable idempotency boundary for approved draft activation (PostgreSQL)."""
import hashlib
import json
from sqlalchemy import text


def run_activation(engine, key, payload, execute, recover):
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('legal_activation_operation_schema'))"))
        connection.execute(text("""CREATE TABLE IF NOT EXISTS legal_activation_operation (
            version_key TEXT PRIMARY KEY, request_fingerprint TEXT NOT NULL,
            document_id BIGINT, result JSONB, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""))
        connection.execute(text('ALTER TABLE legal_activation_operation ADD COLUMN IF NOT EXISTS recovery JSONB'))
        connection.commit()
        connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
        connection.commit()
        try:
            row = connection.execute(text("SELECT * FROM legal_activation_operation WHERE version_key=:key"), {"key": key}).mappings().first()
            if row and row["request_fingerprint"] != fingerprint:
                raise ValueError("activation_idempotency_conflict")
            if row and row["result"]:
                return dict(row["result"])
            if not row:
                connection.execute(text("INSERT INTO legal_activation_operation(version_key,request_fingerprint) VALUES (:key,:fp)"), {"key":key,"fp":fingerprint})
            connection.commit()
            result = recover(int(row["document_id"])) if row and row["document_id"] else execute()
            connection.execute(text("UPDATE legal_activation_operation SET result=CAST(:result AS jsonb),updated_at=now() WHERE version_key=:key"), {"key":key,"result":json.dumps(result, default=str)})
            connection.commit()
            return result
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id":lock_id})
            connection.commit()


def bind_activation_document(connection, key, document_id):
    if key:
        result = connection.execute(text("UPDATE legal_activation_operation SET document_id=:doc,updated_at=now() WHERE version_key=:key AND document_id IS NULL"), {"key":key,"doc":document_id})
        if result.rowcount != 1:
            raise ValueError("activation_journal_not_reserved")


def record_activation_recovery_state(engine, key, document_id, revision):
    with engine.begin() as connection:
        result = connection.execute(text('UPDATE legal_activation_operation SET recovery=CAST(:recovery AS jsonb),updated_at=now() WHERE version_key=:key AND document_id=:doc AND result IS NULL'),
            {'key': key, 'doc': document_id, 'recovery': json.dumps({'excluded_revision': revision})})
        if result.rowcount != 1:
            raise ValueError('activation_recovery_journal_mismatch')


def activation_recovery_revision(engine, key, document_id):
    with engine.connect() as connection:
        recovery = connection.execute(text('SELECT recovery FROM legal_activation_operation WHERE version_key=:key AND document_id=:doc AND result IS NULL'),
            {'key': key, 'doc': document_id}).scalar_one_or_none()
    return (recovery or {}).get('excluded_revision')
