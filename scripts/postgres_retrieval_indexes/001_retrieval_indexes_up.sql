-- PostgreSQL lexical retrieval indexes. Each statement is committed separately.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_chunks_content_trgm
    ON legal_article_chunks
    USING gin ((LOWER(COALESCE(content, ''))) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_chunks_heading_trgm
    ON legal_article_chunks
    USING gin ((LOWER(COALESCE(heading, ''))) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_articles_title_trgm
    ON legal_articles
    USING gin ((LOWER(COALESCE(title, ''))) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_documents_title_trgm
    ON legal_documents
    USING gin ((LOWER(COALESCE(title, ''))) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_documents_law_number_trgm
    ON legal_documents
    USING gin ((LOWER(COALESCE(law_number, ''))) gin_trgm_ops);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_chunks_article_id
    ON legal_article_chunks (article_id, id);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_retrieval_articles_active_document_id
    ON legal_articles (document_id, id)
    WHERE status = 'active';

