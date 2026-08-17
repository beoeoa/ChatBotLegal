CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS legal_chunk_quality (
    chunk_id BIGINT NOT NULL REFERENCES legal_article_chunks(id),
    quality_version TEXT NOT NULL,
    eligible BOOLEAN NOT NULL,
    canonical_chunk_id BIGINT NULL REFERENCES legal_article_chunks(id),
    content_hash CHAR(64) NOT NULL,
    cleaned_article_title TEXT NOT NULL,
    quality_reasons TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    assessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chunk_id, quality_version),
    CHECK (canonical_chunk_id IS NULL OR canonical_chunk_id <> chunk_id)
);
