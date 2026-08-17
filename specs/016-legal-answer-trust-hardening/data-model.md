# Data Model: Legal Answer Trust Hardening

## Logical entities

### GoldenCaseV2

- `case_id`: unique stable identifier.
- `schema_version`, `domain`, `procedure_family`, `legal_as_of`.
- `questions`: citizen/officer wording.
- `expected_sources[]`: law number, article/clause/point, applicability and optional exact proof.
- `forbidden_sources[]`: expired, superseded, wrong procedure or wrong facet references.
- `required_claims[]`: ordered fact/facet requirements.
- `expected_answer_mode`, `expected_refusal`, `risk_tags[]`.
- `review_status`: proposed → legally_reviewed → approved; automation must not promote status.

Validation: case IDs and question hashes unique; current-answer cases require `legal_as_of`; every approved case has at least one expected source or an explicit expected refusal.

### StructuredQARun

- Identity: `run_id`, `case_id`, `question_hash`, `trace_id`.
- Context: role, domain, `legal_as_of`, question.
- Output: answer body only, answer mode, grounding, completeness, citations, claim validation.
- Decisions: intent, validity, fallback reason, error.
- Timings: retrieval, provisioning, generation, validation, end-to-end.
- Versions: app, index, snapshot, embedding, reranker, provider/model.
- Evaluation: per-rubric booleans/reason codes and aggregate pass.

Validation: no DOM as answer; public artifact must omit raw admin trace, token, secret and raw PII.

### LegalIntent

- `domain`, `procedure_family`, `action`, `target_object`.
- `requested_facets[]`, `legal_identifiers[]`, `jurisdiction`, `legal_as_of`.
- `confidence`, `ambiguity_reasons[]`, `router_version`.

State: resolved; ambiguous_needs_clarification; unsupported.

### EvidenceCandidate

- Source identity and legal metadata.
- `procedure_family`, `supported_facets[]`, action tags.
- authority/jurisdiction/effectivity decision and reason codes.
- vector/lexical/reranker scores and deterministic rank fields.
- provenance reference and request/issue/query scope IDs.

Eligibility: only effective applicable evidence with explicit or deterministically verified procedure/facet support may bind a claim.

### AnswerClaim

- `claim_id`, `issue_id`, `facet`, `text`.
- `evidence_ids[]`, `support_quote`, `verification_level`.
- `validation_status`, reason codes and ordered position.

State: proposed → verified | rejected. Rejected claims are removed from the public answer.

## Additive persistence sidecars

### legal_chunk_provenance

- `id`, `chunk_id`, `span_index`.
- `source_asset_sha256`, extractor/version.
- page, char offsets, bounding box, block type, table path.
- `text_hash`, verification status, created/updated timestamps.
- Unique `(chunk_id, span_index)`; no cascade deletion of legal corpus in this phase.

### legal_audit_chain

- sequence/id, event time/type, actor/role, object type/id.
- canonical detail hash, `previous_hash`, `entry_hash`.
- checkpoint ID/signature/public-key fingerprint.
- integrity status and verification timestamp.

State: appended → checkpointed → verified | broken. Chain-required writes fail if the previous head cannot be verified.

## Validity decision

Canonical states: `effective`, `partially_effective`, `expired_or_repealed`, `unknown_or_stale`.

- Current-answer eligibility: effective; partial only when requested provision is not blocked.
- Historical mode: expired allowed only with explicit label and excluded from current conclusions.
- Unknown/stale: fail closed for legal conclusions.

## Compatibility and migration

- `AskResponse` additions are optional/additive for old clients.
- Legacy citations map to `metadata_only` until quote/provenance is verified.
- Sidecar migration and isolated rehearsal are allowed; live apply/backfill require approval.
- Vector collections remain immutable and model-specific; activation uses a validated pointer switch only after approval.
