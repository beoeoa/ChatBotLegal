BEGIN;

CREATE TABLE IF NOT EXISTS feature018_import_batch (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('support_json','faq_json')),
    source_snapshot_sha256 CHAR(64) NOT NULL,
    source_record_count INTEGER NOT NULL CHECK (source_record_count >= 0),
    imported_record_count INTEGER NOT NULL DEFAULT 0 CHECK (imported_record_count >= 0),
    reconciliation JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('running','reconciled','failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    UNIQUE (source_kind, source_snapshot_sha256)
);

CREATE TABLE IF NOT EXISTS feature018_source_record (
    id TEXT PRIMARY KEY,
    import_batch_ref TEXT NOT NULL REFERENCES feature018_import_batch(id) ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('support_ticket','faq')),
    source_id TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_kind, source_id, source_sha256)
);

CREATE TABLE IF NOT EXISTS support_ticket (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    canonical_domain TEXT NOT NULL,
    question_summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','assigned','active','waiting_citizen','waiting_officer','resolved','closed','cancelled','expired')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','urgent')),
    queue_sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    assigned_officer_id TEXT,
    assignment_generation INTEGER NOT NULL DEFAULT 0 CHECK (assignment_generation >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    first_response_due_at TIMESTAMPTZ,
    resolution_due_at TIMESTAMPTZ,
    assigned_at TIMESTAMPTZ,
    active_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    retention_expires_at TIMESTAMPTZ NOT NULL,
    source_created_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (closed_at IS NULL OR resolved_at IS NULL OR closed_at >= resolved_at)
);

CREATE TABLE IF NOT EXISTS support_state_event (
    event_sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    id TEXT PRIMARY KEY,
    ticket_ref TEXT NOT NULL REFERENCES support_ticket(id) ON DELETE RESTRICT,
    actor_user_id TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('citizen','officer','admin','system')),
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason_code TEXT,
    detail_sha256 CHAR(64) NOT NULL,
    previous_hash CHAR(64),
    entry_hash CHAR(64) NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (detail_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (entry_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS support_message (
    id TEXT PRIMARY KEY,
    ticket_ref TEXT NOT NULL REFERENCES support_ticket(id) ON DELETE RESTRICT,
    sender_user_id TEXT NOT NULL,
    sender_role TEXT NOT NULL CHECK (sender_role IN ('citizen','officer','admin','system')),
    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
    content TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retention_expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (ticket_ref, sequence_number),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS support_attachment (
    id TEXT PRIMARY KEY,
    ticket_ref TEXT NOT NULL REFERENCES support_ticket(id) ON DELETE RESTRICT,
    message_ref TEXT REFERENCES support_message(id) ON DELETE RESTRICT,
    object_ref TEXT NOT NULL,
    original_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    content_sha256 CHAR(64) NOT NULL,
    security_status TEXT NOT NULL CHECK (security_status IN ('pending','passed','blocked','quarantined')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retention_expires_at TIMESTAMPTZ NOT NULL,
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS officer_presence (
    officer_user_id TEXT PRIMARY KEY,
    canonical_domains JSONB NOT NULL,
    presence_status TEXT NOT NULL CHECK (presence_status IN ('offline','available','busy','away')),
    max_capacity INTEGER NOT NULL DEFAULT 3 CHECK (max_capacity BETWEEN 1 AND 3),
    active_count INTEGER NOT NULL DEFAULT 0 CHECK (active_count >= 0 AND active_count <= 3),
    heartbeat_at TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (active_count <= max_capacity)
);

CREATE TABLE IF NOT EXISTS support_assignment (
    id TEXT PRIMARY KEY,
    ticket_ref TEXT NOT NULL REFERENCES support_ticket(id) ON DELETE RESTRICT,
    officer_user_id TEXT NOT NULL,
    canonical_domain TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    lease_token_sha256 CHAR(64) NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('leased','active','released','expired','cancelled')),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    release_reason TEXT,
    UNIQUE (ticket_ref, generation),
    CHECK (lease_token_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_support_assignment_active_ticket
ON support_assignment(ticket_ref) WHERE status IN ('leased','active');

CREATE TABLE IF NOT EXISTS support_content_access (
    id TEXT PRIMARY KEY,
    ticket_ref TEXT NOT NULL REFERENCES support_ticket(id) ON DELETE RESTRICT,
    admin_user_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 8),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    audit_event_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS faq_identity (
    id TEXT PRIMARY KEY,
    faq_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS faq_revision (
    id TEXT PRIMARY KEY,
    faq_ref TEXT NOT NULL REFERENCES faq_identity(id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    canonical_domain TEXT NOT NULL,
    confirmed_procedure_id TEXT NOT NULL,
    evidence JSONB NOT NULL,
    public_state TEXT NOT NULL CHECK (public_state IN ('pending','machine_candidate','needs_review','confirmed','released','dismissed','blocked')),
    source_sha256 CHAR(64) NOT NULL,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (faq_ref, revision_number),
    UNIQUE (faq_ref, source_sha256),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS faq_release (
    id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL UNIQUE CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('candidate','validated','active','retired','blocked')),
    manifest JSONB NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL UNIQUE,
    previous_release_id TEXT,
    form_release_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_by TEXT,
    activated_at TIMESTAMPTZ,
    CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS faq_release_item (
    id TEXT PRIMARY KEY,
    release_ref TEXT NOT NULL REFERENCES faq_release(id) ON DELETE RESTRICT,
    faq_revision_ref TEXT NOT NULL REFERENCES faq_revision(id) ON DELETE RESTRICT,
    display_order INTEGER NOT NULL DEFAULT 0 CHECK (display_order >= 0),
    UNIQUE (release_ref, faq_revision_ref)
);

CREATE TABLE IF NOT EXISTS faq_active_release (
    pointer_key TEXT PRIMARY KEY CHECK (pointer_key = 'faq-catalog'),
    release_ref TEXT NOT NULL REFERENCES faq_release(id) ON DELETE RESTRICT,
    pointer_version INTEGER NOT NULL CHECK (pointer_version > 0),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_support_ticket_queue
ON support_ticket(status, canonical_domain, priority, queue_sequence);
CREATE INDEX IF NOT EXISTS idx_support_ticket_owner
ON support_ticket(owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_assignment_officer
ON support_assignment(officer_user_id, status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_support_message_ticket
ON support_message(ticket_ref, sequence_number);
CREATE INDEX IF NOT EXISTS idx_support_state_event_ticket
ON support_state_event(ticket_ref, event_sequence);
CREATE INDEX IF NOT EXISTS idx_faq_revision_public
ON faq_revision(public_state, canonical_domain, confirmed_procedure_id);
CREATE INDEX IF NOT EXISTS idx_source_record_target
ON feature018_source_record(target_table, target_id);

CREATE OR REPLACE FUNCTION feature018_append_only_record()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'FEATURE018_APPEND_ONLY_RECORD';
END;
$$;

DROP TRIGGER IF EXISTS feature018_support_message_no_update ON support_message;
CREATE TRIGGER feature018_support_message_no_update
BEFORE UPDATE OR DELETE ON support_message
FOR EACH ROW EXECUTE FUNCTION feature018_append_only_record();

DROP TRIGGER IF EXISTS feature018_support_state_event_no_update ON support_state_event;
CREATE TRIGGER feature018_support_state_event_no_update
BEFORE UPDATE OR DELETE ON support_state_event
FOR EACH ROW EXECUTE FUNCTION feature018_append_only_record();

DROP TRIGGER IF EXISTS feature018_source_record_no_update ON feature018_source_record;
CREATE TRIGGER feature018_source_record_no_update
BEFORE UPDATE OR DELETE ON feature018_source_record
FOR EACH ROW EXECUTE FUNCTION feature018_append_only_record();

COMMIT;
