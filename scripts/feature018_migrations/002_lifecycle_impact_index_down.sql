BEGIN;

DROP TRIGGER IF EXISTS feature018_legal_change_event_no_update ON legal_change_event;
DROP FUNCTION IF EXISTS feature018_legal_append_only_record();
DROP TABLE IF EXISTS legal_index_job;
DROP TABLE IF EXISTS legal_index_manifest;
DROP TABLE IF EXISTS legal_impact_case;
DROP TABLE IF EXISTS legal_provision_effectivity;
DROP TABLE IF EXISTS legal_document_relation_candidate;
DROP TABLE IF EXISTS legal_change_event;

COMMIT;
