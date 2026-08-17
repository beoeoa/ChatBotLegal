BEGIN;

CREATE TABLE IF NOT EXISTS legal_chunk_release (
    release_id TEXT PRIMARY KEY,
    source_manifest_sha256 CHAR(64) NOT NULL,
    source_snapshot_sha256 CHAR(64) NOT NULL,
    model_artifact_fingerprint CHAR(64) NOT NULL,
    tokenizer_fingerprint CHAR(64) NOT NULL,
    embedding_recipe_fingerprint CHAR(64) NOT NULL,
    passage_recipe_fingerprint CHAR(64) NOT NULL,
    splitter_fingerprint CHAR(64) NOT NULL,
    quality_policy_version TEXT NOT NULL,
    dependency_lock_fingerprint CHAR(64) NOT NULL,
    build_state TEXT NOT NULL CHECK (
        build_state IN ('building','complete','validated','blocked')
    ),
    inventory_document_count INTEGER NOT NULL CHECK (inventory_document_count > 0),
    retrievable_document_count INTEGER NOT NULL CHECK (retrievable_document_count >= 0),
    quarantined_document_count INTEGER NOT NULL CHECK (quarantined_document_count >= 0),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
    vector_count INTEGER NOT NULL CHECK (vector_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CHECK (quality_policy_version = 'legal-chunk-quality-v2'),
    CHECK (inventory_document_count = retrievable_document_count + quarantined_document_count),
    CHECK (chunk_count = vector_count),
    CHECK (retrievable_document_count = 0 OR chunk_count > 0),
    CHECK (source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (source_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (model_artifact_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (tokenizer_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (embedding_recipe_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (passage_recipe_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (splitter_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (dependency_lock_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS legal_chunk_revision (
    id BIGSERIAL PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES legal_chunk_release(release_id) ON DELETE RESTRICT,
    document_id INTEGER NOT NULL REFERENCES legal_documents(id) ON DELETE RESTRICT,
    article_id INTEGER NOT NULL REFERENCES legal_articles(id) ON DELETE RESTRICT,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    structural_path TEXT NOT NULL,
    child_kind TEXT NOT NULL CHECK (
        child_kind IN ('article','appendix','clause','point','fallback')
    ),
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    source_start_offset INTEGER CHECK (source_start_offset IS NULL OR source_start_offset >= 0),
    source_end_offset INTEGER CHECK (source_end_offset IS NULL OR source_end_offset >= 0),
    source_content_sha256 CHAR(64) NOT NULL,
    passage_sha256 CHAR(64) NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count BETWEEN 1 AND 512),
    quality_policy_version TEXT NOT NULL CHECK (
        quality_policy_version = 'legal-chunk-quality-v2'
    ),
    quality_assessed BOOLEAN NOT NULL DEFAULT FALSE,
    eligible BOOLEAN NOT NULL DEFAULT FALSE,
    serving_state TEXT NOT NULL CHECK (serving_state IN ('retrievable','quarantined')),
    quality_reasons TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (BTRIM(content) <> ''),
    CHECK (source_content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (passage_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (source_end_offset IS NULL OR source_start_offset IS NULL OR source_end_offset >= source_start_offset),
    CHECK (quality_assessed = TRUE),
    CHECK (
        (eligible = TRUE AND serving_state = 'retrievable' AND CARDINALITY(quality_reasons) = 0)
        OR
        (eligible = FALSE AND serving_state = 'quarantined' AND CARDINALITY(quality_reasons) > 0)
    ),
    UNIQUE (release_id, article_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS ix_legal_chunk_revision_release_document
    ON legal_chunk_revision (release_id, document_id, article_id, chunk_index);

CREATE INDEX IF NOT EXISTS ix_legal_chunk_revision_release_serving
    ON legal_chunk_revision (release_id, serving_state, eligible);

CREATE TABLE IF NOT EXISTS legal_metadata_review_case (
    case_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES legal_documents(id) ON DELETE RESTRICT,
    field_name TEXT NOT NULL CHECK (field_name IN (
        'title','law_number','issuing_agency','scope','source_url','domain',
        'effective_date','expired_date','status'
    )),
    old_value JSONB,
    proposed_value JSONB,
    evidence_url TEXT,
    evidence_sha256 CHAR(64),
    state TEXT NOT NULL CHECK (state IN ('proposed','approved','rejected','superseded')),
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
        state <> 'approved'
        OR (
            BTRIM(COALESCE(evidence_url, '')) <> ''
            AND evidence_sha256 IS NOT NULL
            AND BTRIM(COALESCE(reviewer_user_id, '')) <> ''
            AND reviewed_at IS NOT NULL
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_legal_metadata_review_case_open_field
    ON legal_metadata_review_case (document_id, field_name)
    WHERE state IN ('proposed','approved');

CREATE OR REPLACE FUNCTION feature018_vector_release_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'FEATURE018_VECTOR_RELEASE_IMMUTABLE';
END;
$$;

DROP TRIGGER IF EXISTS feature018_chunk_revision_no_update ON legal_chunk_revision;
CREATE TRIGGER feature018_chunk_revision_no_update
    BEFORE UPDATE OR DELETE ON legal_chunk_revision
    FOR EACH ROW EXECUTE FUNCTION feature018_vector_release_immutable();

CREATE OR REPLACE FUNCTION feature018_completed_vector_release_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.build_state IN ('complete','validated','blocked') THEN
        RAISE EXCEPTION 'FEATURE018_VECTOR_RELEASE_IMMUTABLE';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS feature018_completed_vector_release_no_update ON legal_chunk_release;
CREATE TRIGGER feature018_completed_vector_release_no_update
    BEFORE UPDATE OR DELETE ON legal_chunk_release
    FOR EACH ROW EXECUTE FUNCTION feature018_completed_vector_release_immutable();

COMMIT;
