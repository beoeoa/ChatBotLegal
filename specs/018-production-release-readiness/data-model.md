# Data Model: Feature 018 Production Release Readiness

## Enumerations

### CapabilityStatus

`active`, `internal`, `deprecated`, `disabled`.

### AnswerRoute / AnswerStatus

- Route: `exact_article`, `procedure_form`, `general_legal`, `historical`.
- Status: `grounded`, `partial_grounded`, `broad_grounded`, `clarifying`, `source_gap`, `provider_error`.

### SupportTicketStatus

`queued`, `assigned`, `active`, `waiting_citizen`, `waiting_officer`, `resolved`, `closed`, `cancelled`, `expired`.

### OfficerPresenceStatus

`offline`, `available`, `busy`, `away`, `stale`.

### LegalLifecycleBucket

`active`, `future`, `expiring_90`, `expiring_30`, `expiring_7`, `expiring_1`, `expired`, `partially_expired`, `replaced`, `repealed`, `suspended`, `corrected`, `consolidated`, `unknown`.

### VectorServingState

`active`, `historical_only`, `staging`, `missing`, `orphan`, `duplicate`, `fingerprint_mismatch`, `quarantined`.

### LegalImpactStatus

`pending`, `machine_candidate`, `needs_review`, `confirmed`, `released`, `dismissed`, `blocked`.

## Entities

### CapabilityRecord

- Stable `capability_id`, kind (`frontend_route`, `api_route`, `job`), path/name and owner.
- Allowed roles/domains, canonical data source, sensitivity, audit and retention policy.
- SLA, feature flag, test IDs, rollback method and status.
- Rule: public/active entries require owner, auth policy, tests and rollback; disabled entries must not be routable.

### LegalAnswerPresentationV1

- `presentation_version`, `answer_status`, `answer_route`, `legal_as_of`, release/fingerprint fields.
- Sections: short answer, actions, dossier, procedure facts, forms, legal bases, caveats/clarification.
- Evidence/citation IDs remain structured; prose cannot add IDs/URLs.
- Rule: verified label requires `evidence_count > 0`; historical answer includes applicable date and label.

### SupportTicket

- Opaque ID, citizen owner, canonical domain, question summary, status, priority, SLA timestamps.
- Queue sequence/created time, assigned officer, assignment generation, active/closed timestamps.
- Retention deadline and content/attachment references stored separately.
- Rule: owner sees own tickets; officer sees only assigned/domain queue metadata; admin content access needs a reason.

### SupportMessage / SupportAttachment

- Ticket, sender identity/role, sequence, content or object reference, checksum, created time.
- Rule: append-only after send; attachments pass file security gate and expire with ticket content policy.

### OfficerPresence / SupportAssignment

- Officer, canonical domains, status, heartbeat, max/active capacity.
- Assignment has ticket, officer, lease/version, assigned/released times and reason.
- Rule: unique active assignment per ticket; active count never exceeds capacity; stale lease requeues safely.

### LegalChangeEvent

- Document, event type, effective date/range, source/provenance, scope (`whole_document` or provisions), status and reviewer.
- Types include effective/expiry, amend, supplement, replace, repeal, suspend, restore, correct, extend, consolidate, effective-date/authority/jurisdiction changes and identity/source conflict.
- Rule: machine events are candidates until confirmed; source and applicable date required for serving impact.

### LegalDocumentRelationship

- From/to document, relationship type, source, effective range and confirmation state.
- Rule: `replaces` does not imply content equivalence; reverse relation is consistent.

### LegalProvisionEffectivity

- Document plus article/clause/point identity, effective range and event relation.
- Rule: current serving is allowed only when the provision is effective at `legal_as_of`.

### LegalImpactCase

- Change event, dependent object type/id, detected reason, old/new evidence, diff candidate, status, reviewer/release.
- Dependents include procedure, form, FAQ, Golden, cache, citation and index records.
- Rule: candidate cannot mutate released truth; confirmed changes produce a new revision/release.

### IndexManifest / IndexJob

- Manifest: release ID, collection, embedding/splitter/pipeline fingerprints, validity snapshot, sorted document/chunk inventory and checksum.
- Job: target document/provisions, mode (`incremental`, `full`), staging collection, state, attempts, error code and verification result.
- Rule: one staging writer; active pointer moves only after passed manifest gate; expired data becomes `historical_only` rather than deleted.

### LegalChunkRelease / LegalChunkRevision

- Release has stable ID, source manifest/snapshot checksums, model/tokenizer/embedding/passage/splitter fingerprints, quality-policy version, dependency-lock fingerprint, build state and inventory/retrievable/quarantine/chunk/vector counts.
- Revision has release, document/article identity, deterministic chunk index, structural path/kind, heading/content, source offsets/checksum, passage checksum, token count, eligibility, serving state and quality reasons.
- Rule: release rows are immutable after `complete`; `(release_id, article_id, chunk_index)` is unique; V1 chunks are never rewritten; active selection remains the checksum-bound manifest pointer rather than a second database pointer.

### LegalMetadataReviewCase

- One document field correction proposal records old/proposed values, official evidence URL/checksum, state, reviewer and decision timestamps.
- Rule: machine extraction creates proposals only; unresolved metadata leaves the document `quarantined`; approved changes retain before/after audit and never fill legal truth with generated guesses.

### FAQRevision / FAQRelease

- FAQ identity, revision, question/answer, canonical domain, confirmed procedure, evidence and public state.
- Release manifest contains approved revisions and previous pointer.
- Rule: forms are resolved at runtime from procedure active release; no manual form IDs as source of truth.

### ActivityEvent / AuditEntry

- Opaque event ID, actor/role, capability, action, target, result/reason, release/fingerprint, timestamp.
- Audit includes previous/entry hash and checkpoint; sensitive content is referenced, not copied to telemetry.
- Rule: updates/deletes are denied or tamper-evident; sensitive detail access creates another event with reason.

### ReleaseEvidence

- Release candidate, artefact type, command/scenario, environment, checksum, result, metrics, timestamp and reviewer.
- Types: unit, contract, integration, Golden, live-answer, UAT, load, security, backup, restore, build and smoke.
- Rule: Go/No-Go consumes evidence by required gate and rejects stale/mismatched release fingerprints.

### RetrievalEvaluation / RetrievalSourceGap

- Evaluation binds metric-contract version, dataset/version/checksum, per-case legal date, search configuration, stage timings, overall/domain/intent/temporal metrics and one primary miss reason.
- Answer-required cases are the Recall/MRR denominator; expected refusals use a separate correct-refusal metric; false temporal blocks remain answer misses.
- Source gap binds dataset/case/domain/law/article, official-source metadata when known, effectivity review state, required action and candidate-manifest fingerprint.
- Rule: source discovery or PostgreSQL inventory lookup is not legal approval; candidate-vNext and active pointer remain unchanged until review/release gates pass.

## Core Relationships

```text
CapabilityRecord 1 → many ActivityEvent
SupportTicket 1 → many SupportMessage/Attachment/Assignment
OfficerPresence 1 → many SupportAssignment
LegalChangeEvent → LegalDocumentRelationship/ProvisionEffectivity → LegalImpactCase
IndexManifest 1 → many document/chunk serving decisions
LegalChunkRelease 1 → many LegalChunkRevision; document metadata gaps → LegalMetadataReviewCase
FAQRevision → confirmed procedure → Feature017 active form bindings
Release candidate 1 → many ReleaseEvidence → one Go/No-Go decision
Dataset + CandidateManifest → RetrievalEvaluation → RetrievalSourceGap/StageDiagnosis
```

## Retention

- Support content and attachments: purge 180 days after close.
- Support/audit metadata: retain 730 days.
- Legal source, release, lifecycle and audit history: no hard delete; supersede/version instead.
- Operational telemetry: retain only durations/status/fingerprint/opaque identifiers according to environment policy; no question/answer content.

## Migration Rules

- New schema is additive and independently rehearsed.
- JSON support/FAQ import is idempotent by stable ID + source checksum.
- Pointer switch and source-mode transition are audited transactions.
- Down path for rehearsal may drop only isolated empty/fixture tables; production rollback uses pointers/flags and preserves records.
