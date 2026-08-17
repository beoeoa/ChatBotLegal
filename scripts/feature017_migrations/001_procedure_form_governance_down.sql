BEGIN;

DROP TRIGGER IF EXISTS feature017_workflow_event_no_update ON form_workflow_event;
DROP FUNCTION IF EXISTS feature017_workflow_event_immutable();
DROP TABLE IF EXISTS form_notification_outbox;
DROP TABLE IF EXISTS form_active_release;
DROP TABLE IF EXISTS form_workflow_event;
DROP TABLE IF EXISTS form_source_gap;
DROP TABLE IF EXISTS form_review_revision;
DROP TABLE IF EXISTS form_review_case;
DROP TABLE IF EXISTS procedure_question_alias;
DROP TABLE IF EXISTS procedure_form_binding;
DROP TABLE IF EXISTS legal_form_asset;
DROP TABLE IF EXISTS legal_procedure;
DROP TABLE IF EXISTS form_release;

COMMIT;
