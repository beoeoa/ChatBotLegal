BEGIN;

CREATE TABLE IF NOT EXISTS legal_change_event (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'effective','expiry','amend','supplement','replace','repeal','suspend',
        'restore','correct','extend','consolidate','effective_date_change',
        'authority_change','jurisdiction_change','identity_conflict','source_conflict'
    )),
    effective_from DATE NOT NULL,
    effective_to DATE,
    source_url TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    scope TEXT NOT NULL CHECK (scope IN ('whole_document','provisions')),
    provision_identities JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('candidate','confirmed','rejected','superseded')),
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (status <> 'confirmed' OR (reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_legal_change_event_document_effective
    ON legal_change_event (document_id, effective_from, status);

CREATE TABLE IF NOT EXISTS legal_document_relation_candidate (
    id TEXT PRIMARY KEY,
    from_document_id TEXT NOT NULL,
    to_document_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL CHECK (relationship_type IN (
        'amends','supplements','replaces','repeals','suspends','restores',
        'corrects','extends','consolidates'
    )),
    source_url TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    confirmation_state TEXT NOT NULL CHECK (confirmation_state IN ('candidate','confirmed','rejected','superseded')),
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (from_document_id <> to_document_id),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE (from_document_id, to_document_id, relationship_type, effective_from)
);

CREATE TABLE IF NOT EXISTS legal_provision_effectivity (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    article_identity TEXT NOT NULL,
    clause_identity TEXT NOT NULL DEFAULT '',
    point_identity TEXT NOT NULL DEFAULT '',
    effective_from DATE NOT NULL,
    effective_to DATE,
    source_event_ref TEXT REFERENCES legal_change_event(id) ON DELETE RESTRICT,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate','confirmed','rejected','superseded')),
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE (document_id, article_identity, clause_identity, point_identity, effective_from)
);

CREATE INDEX IF NOT EXISTS ix_legal_provision_effectivity_document_range
    ON legal_provision_effectivity (document_id, effective_from, effective_to, status);

CREATE TABLE IF NOT EXISTS legal_impact_case (
    id TEXT PRIMARY KEY,
    change_event_ref TEXT NOT NULL REFERENCES legal_change_event(id) ON DELETE RESTRICT,
    dependent_type TEXT NOT NULL CHECK (dependent_type IN (
        'procedure','form','faq','golden','cache','citation','index_record'
    )),
    dependent_id TEXT NOT NULL,
    detected_reason TEXT NOT NULL,
    old_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    new_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    diff_candidate JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_sha256 CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('needs_review','confirmed','rejected','released','superseded')),
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    release_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (change_event_ref, dependent_type, dependent_id, evidence_sha256)
);

CREATE INDEX IF NOT EXISTS ix_legal_impact_case_review
    ON legal_impact_case (status, dependent_type, created_at);

CREATE TABLE IF NOT EXISTS legal_index_manifest (
    id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    serving_state TEXT NOT NULL CHECK (serving_state IN (
        'active','staging','historical','missing','orphan','duplicate','fingerprint_mismatch'
    )),
    embedding_fingerprint TEXT NOT NULL,
    splitter_fingerprint TEXT NOT NULL,
    pipeline_fingerprint TEXT NOT NULL,
    validity_snapshot_sha256 CHAR(64) NOT NULL,
    inventory JSONB NOT NULL DEFAULT '[]'::jsonb,
    inventory_sha256 CHAR(64) NOT NULL,
    verification_state TEXT NOT NULL CHECK (verification_state IN ('pending','passed','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (release_id, collection_name)
);

CREATE TABLE IF NOT EXISTS legal_index_job (
    id TEXT PRIMARY KEY,
    target_document_id TEXT NOT NULL,
    target_provisions JSONB NOT NULL DEFAULT '[]'::jsonb,
    mode TEXT NOT NULL CHECK (mode IN ('incremental','full')),
    staging_collection TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('preview','queued','running','verifying','passed','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code TEXT,
    verification_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_manifest_ref TEXT REFERENCES legal_index_manifest(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE FUNCTION feature018_legal_append_only_record()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'FEATURE018_LEGAL_APPEND_ONLY_RECORD';
END;
$$;

DROP TRIGGER IF EXISTS feature018_legal_change_event_no_update ON legal_change_event;
CREATE TRIGGER feature018_legal_change_event_no_update
    BEFORE UPDATE OR DELETE ON legal_change_event
    FOR EACH ROW EXECUTE FUNCTION feature018_legal_append_only_record();

COMMIT;
