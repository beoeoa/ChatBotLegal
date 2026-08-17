DROP INDEX CONCURRENTLY IF EXISTS ix_legal_exact_chunk_heading;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_exact_article_number;
-- migrate:split
DROP INDEX CONCURRENTLY IF EXISTS ix_legal_exact_document_law_number;
-- migrate:split
DROP FUNCTION IF EXISTS legal_normalize_identifier(TEXT);
