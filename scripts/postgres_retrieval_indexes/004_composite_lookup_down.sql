DROP INDEX IF EXISTS ix_legal_composite_chunks_doc_article;
-- migrate:split
DROP INDEX IF EXISTS ix_legal_composite_articles_doc_artnum;
-- migrate:split
DROP INDEX IF EXISTS ix_legal_composite_documents_scope_status;
