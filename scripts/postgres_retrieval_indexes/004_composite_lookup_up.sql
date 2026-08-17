-- DB-6 Composite lookup indexes for high-frequency legal retrieval queries.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_composite_chunks_doc_article
    ON legal_article_chunks (document_id, article_id, id);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_composite_articles_doc_artnum
    ON legal_articles (document_id, legal_normalize_text(article_number));
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_legal_composite_documents_scope_status
    ON legal_documents (scope, status);
