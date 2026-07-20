-- Explicit rollback for objects owned by 001_retrieval_indexes_up.sql.
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_articles_active_document_id;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_chunks_article_id;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_documents_law_number_trgm;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_documents_title_trgm;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_articles_title_trgm;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_chunks_heading_trgm;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_retrieval_chunks_content_trgm;

