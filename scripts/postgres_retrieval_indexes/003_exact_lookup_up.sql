-- DB-5 exact metadata lookup. No corpus row is changed by this migration.
CREATE OR REPLACE FUNCTION legal_normalize_identifier(input_text TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT REGEXP_REPLACE(
        REPLACE(
            UPPER(public.unaccent('public.unaccent', COALESCE(input_text, ''))),
            'Đ',
            'D'
        ),
        '\s+',
        '',
        'g'
    )
$$;
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_exact_document_law_number
    ON legal_documents (legal_normalize_identifier(law_number));
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_exact_article_number
    ON legal_articles (legal_normalize_text(article_number), document_id, id);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_exact_chunk_heading
    ON legal_article_chunks (legal_normalize_text(heading), article_id, id);
