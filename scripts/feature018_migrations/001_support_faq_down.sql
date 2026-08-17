BEGIN;

DROP TRIGGER IF EXISTS feature018_source_record_no_update ON feature018_source_record;
DROP TRIGGER IF EXISTS feature018_support_message_no_update ON support_message;
DROP TRIGGER IF EXISTS feature018_support_state_event_no_update ON support_state_event;
DROP FUNCTION IF EXISTS feature018_append_only_record();
DROP TABLE IF EXISTS faq_active_release;
DROP TABLE IF EXISTS faq_release_item;
DROP TABLE IF EXISTS faq_release;
DROP TABLE IF EXISTS faq_revision;
DROP TABLE IF EXISTS faq_identity;
DROP TABLE IF EXISTS support_content_access;
DROP TABLE IF EXISTS support_assignment;
DROP TABLE IF EXISTS officer_presence;
DROP TABLE IF EXISTS support_attachment;
DROP TABLE IF EXISTS support_message;
DROP TABLE IF EXISTS support_state_event;
DROP TABLE IF EXISTS support_ticket;
DROP TABLE IF EXISTS feature018_source_record;
DROP TABLE IF EXISTS feature018_import_batch;

COMMIT;
