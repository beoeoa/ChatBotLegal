-- PostgreSQL lexical retrieval indexes. Each statement is committed separately.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- migrate:split
CREATE EXTENSION IF NOT EXISTS unaccent;
-- migrate:split
CREATE OR REPLACE FUNCTION legal_normalize_text(input_text TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT LOWER(public.unaccent('public.unaccent', COALESCE(input_text, '')))
$$;
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_chunks_content_trgm
    ON legal_article_chunks
    USING gin ((legal_normalize_text(content)) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_chunks_heading_trgm
    ON legal_article_chunks
    USING gin ((legal_normalize_text(heading)) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_articles_title_trgm
    ON legal_articles
    USING gin ((legal_normalize_text(title)) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_documents_title_trgm
    ON legal_documents
    USING gin ((legal_normalize_text(title)) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_documents_law_number_trgm
    ON legal_documents
    USING gin ((legal_normalize_text(law_number)) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_chunks_article_id
    ON legal_article_chunks (article_id, id);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_articles_active_document_id
    ON legal_articles (document_id, id)
    WHERE status = 'active';
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_relationships_source_document_id
    ON legal_document_relationships (source_document_id);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_relationships_target_document_id
    ON legal_document_relationships (target_document_id);
