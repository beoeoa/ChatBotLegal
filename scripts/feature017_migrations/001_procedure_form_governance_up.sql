BEGIN;

CREATE TABLE IF NOT EXISTS form_release (
    id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL UNIQUE CHECK (version > 0),
    legal_as_of DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate','validated','active','retired','blocked')),
    source_snapshot_sha256 CHAR(64) NOT NULL,
    manifest JSONB NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL UNIQUE,
    gate_report JSONB,
    gate_report_sha256 CHAR(64),
    previous_release_id TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_by TEXT,
    activated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS legal_procedure (
    id TEXT PRIMARY KEY,
    release_ref TEXT NOT NULL REFERENCES form_release(id) ON DELETE RESTRICT,
    procedure_id TEXT NOT NULL,
    procedure_code TEXT,
    canonical_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    authority TEXT NOT NULL,
    jurisdiction TEXT NOT NULL DEFAULT 'Hai Phong',
    official_source_url TEXT NOT NULL,
    official_source_sha256 CHAR(64),
    effective_from DATE,
    effective_to DATE,
    legal_as_of DATE NOT NULL,
    coverage_status TEXT NOT NULL DEFAULT 'unresolved'
        CHECK (coverage_status IN ('unresolved','released','verified_gap','owner_deferred','not_applicable','superseded','expired')),
    coverage_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (release_ref, procedure_id),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS legal_form_asset (
    id TEXT PRIMARY KEY,
    release_ref TEXT NOT NULL REFERENCES form_release(id) ON DELETE RESTRICT,
    form_id TEXT NOT NULL,
    form_code TEXT,
    canonical_name TEXT NOT NULL,
    asset_kind TEXT NOT NULL CHECK (asset_kind IN ('file','eform')),
    source_url TEXT NOT NULL,
    source_classification TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    identity_sha256 CHAR(64) NOT NULL,
    issuing_instrument TEXT,
    effective_from DATE,
    effective_to DATE,
    audiences JSONB NOT NULL,
    coverage_status TEXT NOT NULL DEFAULT 'unresolved'
        CHECK (coverage_status IN ('unresolved','released','verified_gap','owner_deferred','not_applicable','superseded','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (release_ref, form_id),
    UNIQUE (release_ref, identity_sha256),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (identity_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS procedure_form_binding (
    id TEXT PRIMARY KEY,
    release_ref TEXT NOT NULL REFERENCES form_release(id) ON DELETE RESTRICT,
    procedure_ref TEXT NOT NULL REFERENCES legal_procedure(id) ON DELETE RESTRICT,
    form_asset_ref TEXT REFERENCES legal_form_asset(id) ON DELETE RESTRICT,
    requirement TEXT NOT NULL CHECK (requirement IN ('required','conditional')),
    condition_text TEXT,
    condition_sha256 CHAR(64) NOT NULL,
    audience TEXT NOT NULL CHECK (audience IN ('citizen','officer','both')),
    display_order INTEGER NOT NULL DEFAULT 0 CHECK (display_order >= 0),
    legal_basis TEXT,
    coverage_status TEXT NOT NULL DEFAULT 'unresolved'
        CHECK (coverage_status IN ('unresolved','released','verified_gap','owner_deferred','not_applicable','superseded','expired')),
    effective_from DATE,
    effective_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (release_ref, procedure_ref, form_asset_ref, condition_sha256, audience),
    CHECK (requirement <> 'conditional' OR length(trim(condition_text)) > 0)
);

CREATE TABLE IF NOT EXISTS procedure_question_alias (
    id TEXT PRIMARY KEY,
    release_ref TEXT NOT NULL REFERENCES form_release(id) ON DELETE RESTRICT,
    procedure_ref TEXT NOT NULL REFERENCES legal_procedure(id) ON DELETE RESTRICT,
    alias_text TEXT NOT NULL,
    alias_folded TEXT NOT NULL,
    alias_sha256 CHAR(64) NOT NULL,
    alias_kind TEXT NOT NULL CHECK (alias_kind IN ('exact','natural','exclude','hard_negative')),
    review_status TEXT NOT NULL DEFAULT 'proposed' CHECK (review_status IN ('proposed','approved','rejected')),
    reviewed_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (release_ref, procedure_ref, alias_sha256, alias_kind)
);

CREATE TABLE IF NOT EXISTS form_review_case (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE,
    officer_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    proposed_procedure_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','submitted','needs_supplement','resubmitted','source_approved','legal_enrichment','ready_for_attestation','attested','release_candidate','released','rejected','withdrawn','superseded','expired','quarantined')),
    current_revision INTEGER NOT NULL DEFAULT 1 CHECK (current_revision > 0),
    source_reviewed_by TEXT,
    source_reviewed_at TIMESTAMPTZ,
    legal_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    attestation_sha256 CHAR(64),
    attested_by TEXT,
    attested_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS form_review_revision (
    id TEXT PRIMARY KEY,
    review_case_ref TEXT NOT NULL REFERENCES form_review_case(id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    source_url TEXT NOT NULL,
    source_sha256 CHAR(64),
    attachment_ref TEXT,
    submission JSONB NOT NULL,
    submitted_by TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (review_case_ref, revision_number)
);

CREATE TABLE IF NOT EXISTS form_source_gap (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('procedure','identity','binding')),
    target_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_source_url TEXT NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    legal_as_of DATE NOT NULL,
    verified_by TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'verified_gap' CHECK (status = 'verified_gap'),
    UNIQUE (target_type, target_id, legal_as_of)
);

CREATE TABLE IF NOT EXISTS form_active_release (
    pointer_key TEXT PRIMARY KEY CHECK (pointer_key = 'forms-catalog'),
    release_ref TEXT NOT NULL REFERENCES form_release(id) ON DELETE RESTRICT,
    pointer_version INTEGER NOT NULL CHECK (pointer_version > 0),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS form_workflow_event (
    event_sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('citizen','officer','admin')),
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    reason_code TEXT,
    detail_sha256 CHAR(64) NOT NULL,
    previous_hash CHAR(64),
    entry_hash CHAR(64) NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS form_notification_outbox (
    id TEXT PRIMARY KEY,
    workflow_event_ref TEXT NOT NULL REFERENCES form_workflow_event(id) ON DELETE RESTRICT,
    recipient_id TEXT NOT NULL,
    recipient_role TEXT NOT NULL CHECK (recipient_role IN ('officer','admin')),
    public_payload JSONB NOT NULL,
    projection_status TEXT NOT NULL DEFAULT 'pending' CHECK (projection_status IN ('pending','projected','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    surreal_notification_id TEXT,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    projected_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_legal_procedure_domain ON legal_procedure(domain, coverage_status);
CREATE INDEX IF NOT EXISTS idx_form_review_case_queue ON form_review_case(status, domain, updated_at);
CREATE INDEX IF NOT EXISTS idx_form_review_case_owner ON form_review_case(officer_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_binding_procedure ON procedure_form_binding(procedure_ref, coverage_status, audience, display_order);
CREATE INDEX IF NOT EXISTS idx_alias_folded ON procedure_question_alias(alias_folded, review_status, alias_kind);
CREATE INDEX IF NOT EXISTS idx_workflow_object ON form_workflow_event(object_type, object_id, event_sequence);
CREATE INDEX IF NOT EXISTS idx_form_source_gap_target ON form_source_gap(target_type, target_id, legal_as_of);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON form_notification_outbox(projection_status, created_at);

CREATE OR REPLACE FUNCTION feature017_workflow_event_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'FORM_WORKFLOW_EVENT_IMMUTABLE';
END;
$$;

DROP TRIGGER IF EXISTS feature017_workflow_event_no_update ON form_workflow_event;
CREATE TRIGGER feature017_workflow_event_no_update
BEFORE UPDATE OR DELETE ON form_workflow_event
FOR EACH ROW EXECUTE FUNCTION feature017_workflow_event_immutable();

COMMIT;
