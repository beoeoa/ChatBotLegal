# Data Model: Feature 017 Procedure/Form Governance

## Enumerations

### FormWorkflowStatus

Active path: `draft`, `submitted`, `needs_supplement`, `resubmitted`, `source_approved`, `legal_enrichment`, `ready_for_attestation`, `attested`, `release_candidate`, `released`.

Terminal/side path: `rejected`, `withdrawn`, `superseded`, `expired`, `quarantined`.

### FormCoverageStatus

`unresolved`, `released`, `verified_gap`, `not_applicable`, `superseded`, `expired`.

### AssetKind

`file`, `eform`.

### BindingRequirement

`required`, `conditional`.

## Entities

### legal_procedure

- `id` UUID primary key; `procedure_id` stable public identity unique within jurisdiction.
- `procedure_code`, canonical name, domain, authority level/name, jurisdiction.
- official source URL/hash, `effective_from/effective_to`, `legal_as_of`.
- coverage status/reason/evidence, created/updated timestamps.
- Rule: citizen eligibility requires active release plus effective date; procedure metadata is never model-generated.

### legal_form_asset

- `id` UUID; stable `form_id`; normalized form code/name; kind file/eform.
- official source page/download/e-form URL, source classification, issuing instrument.
- content SHA-256 for file; metadata snapshot SHA-256 for e-form.
- effectivity, allowed audiences, review/coverage status.
- Unique/dedupe: stable form ID; canonical identity fingerprint; content checksum where present.

### procedure_form_binding

- `id` UUID; procedure FK; optional asset FK for verified gap/not-applicable decisions.
- requirement type, condition text/code, audience, order.
- legal basis/source/effectivity and coverage decision.
- Unique `(procedure_id, form_asset_id, condition_fingerprint, audience)`.

### procedure_question_alias

- `id` UUID; procedure FK; alias text/folded/hash.
- alias kind: `exact`, `natural`, `exclude`, `hard_negative`.
- status/reviewer/source/release eligibility.
- Unique `(procedure_id, alias_hash, alias_kind)`.

### form_review_case

- `id` UUID; public opaque `case_id`; officer ID/domain; proposed procedure/asset identity.
- current status/revision, source decision metadata, attestation fingerprint/ref.
- ownership, timestamps, optimistic version.
- Rule: status changes only through service transition; source approval never sets runtime eligible.

### form_review_revision

- `id`; case FK; revision number; submitted URL/file identity/checksum; metadata snapshot.
- submitter, created timestamp; immutable after insertion.

### form_release

- `id` UUID; stable `release_id`; version; `legal_as_of`; status (`candidate`, `validated`, `active`, `retired`, `blocked`).
- canonical manifest JSON and SHA-256, gate report/hash, previous release ID, creator/activator/timestamps.
- Rule: manifest is immutable after candidate creation; only a passed gate can activate.

### form_active_release

- singleton key `forms-catalog`; active release FK; version; updated_by/at.
- Updated with row lock/transaction; rollback writes workflow event.

### form_workflow_event

- Monotonic sequence/UUID; case/release/object identity.
- actor/role, action, from/to status, reason code/detail hash, event time.
- `previous_hash/entry_hash` for tamper evidence; append-only trigger denies update/delete.

### form_notification_outbox

- Event FK, recipient ID/role, payload with no legal authority, projection status/attempts/error code.
- Surreal notification ID after success.
- Rule: failure never rolls back or changes legal state.

## State transition table

| From | Allowed to | Actor |
|------|------------|-------|
| draft | submitted, withdrawn | Officer |
| submitted | needs_supplement, source_approved, rejected, withdrawn | Admin (withdrawn: owner) |
| needs_supplement | resubmitted, withdrawn | Officer owner |
| resubmitted | needs_supplement, source_approved, rejected | Admin |
| source_approved | legal_enrichment, rejected, superseded | Admin |
| legal_enrichment | ready_for_attestation, needs_supplement, rejected | Admin |
| ready_for_attestation | attested, legal_enrichment, rejected | Admin |
| attested | release_candidate, superseded, expired, quarantined | Admin/release builder |
| release_candidate | released, attested, quarantined | Admin/release gate |
| released | superseded, expired, quarantined | Admin/release lifecycle |

Terminal states do not reopen; correction creates a new case/revision/release relation.

## Release manifest canonical shape

- release identity/version/legal_as_of/source fingerprint/previous release.
- sorted procedures, assets, bindings and aliases.
- coverage summary and unresolved IDs.
- each file/e-form provenance/checksum.
- gate decisions and build versions.
- canonical JSON uses UTF-8, sorted keys and stable item ordering; SHA-256 excludes mutable activation timestamps.

## GoldenCaseV3

- Identity: case/schema/domain/category/sequence.
- Input: citizen question, audience, legal_as_of.
- Ground truth: procedure ID, expected/forbidden form IDs, requirement/condition, state, source URL/checksum, clarification flag, answer mode.
- Provenance: release ID/manifest checksum, generation type (`template` or `model_paraphrase`), review status.
- Rule: proposed model paraphrases cannot change ground truth or become approved automatically.

## Compatibility

- Existing `recommended_forms` fields are projected from released asset/binding records.
- Existing JSON adapter maps approval/release gates to the same output type but is read-only.
- PostgreSQL schema is additive and independent from legal article/chunk tables.
- SurrealDB stores only notifications; legal state is always queried from PostgreSQL when feature source is active.

