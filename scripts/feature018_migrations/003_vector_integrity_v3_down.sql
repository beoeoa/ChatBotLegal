BEGIN;

DROP TRIGGER IF EXISTS feature018_completed_vector_release_no_update ON legal_chunk_release;
DROP FUNCTION IF EXISTS feature018_completed_vector_release_immutable();
DROP TRIGGER IF EXISTS feature018_chunk_revision_no_update ON legal_chunk_revision;
DROP FUNCTION IF EXISTS feature018_vector_release_immutable();
DROP TABLE IF EXISTS legal_metadata_review_case;
DROP TABLE IF EXISTS legal_chunk_revision;
DROP TABLE IF EXISTS legal_chunk_release;

COMMIT;
