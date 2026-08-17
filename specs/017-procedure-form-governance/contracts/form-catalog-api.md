# Procedure Forms Catalog API Contract

Base path: `/api/procedures/forms-catalog`

All write operations require authenticated actor identity derived server-side. Client-supplied role/domain/reviewer is never authoritative.

## Officer operations

- `POST /review-cases`: submit proposal; procedure must be in assigned domain.
- `GET /review-cases/mine`: list own/domain-visible cases.
- `GET /review-cases/{case_id}`: owner/domain scoped detail.
- `POST /review-cases/{case_id}/supplement`: allowed only from `needs_supplement`; creates immutable revision and moves to `resubmitted`.
- `POST /review-cases/{case_id}/withdraw`: owner may withdraw before attestation.

## Admin source/legal operations

- `GET /review-cases`: filter by status/domain/source gap.
- `POST /review-cases/{case_id}/request-supplement`: requires fields/reason.
- `POST /review-cases/{case_id}/approve-source`: source decision only; never runtime eligible.
- `POST /review-cases/{case_id}/reject-source`: terminal rejected with reason.
- `PUT /review-cases/{case_id}/legal-metadata`: updates enrichment working set and advances only through valid transitions.
- `GET /review-cases/{case_id}/attestation-preview`: returns canonical preview and SHA-256 fingerprint.
- `POST /review-cases/{case_id}/attest`: requires exact preview fingerprint/revision/checksum.

## Release and coverage operations

- `POST /releases/preview`: build immutable candidate preview from selected attested cases.
- `POST /releases/{release_id}/validate`: run deterministic gate, no activation.
- `POST /releases/{release_id}/activate`: explicit Admin action; passed gate required.
- `POST /releases/{release_id}/rollback`: switch to previous validated manifest.
- `GET /releases/active`: public-safe active release metadata; admin trace is role-restricted.
- `GET /coverage`: counts and unresolved IDs by procedure, identity, binding, domain and state.

## Public procedure/form lookup

- `GET /resolve?q=...&audience=citizen&legal_as_of=YYYY-MM-DD`
- Result is either:
  - `resolved`: one procedure, released required/conditional forms and evidence packet IDs;
  - `clarification_required`: candidate procedure labels/questions, no form IDs;
  - `source_gap`: no runtime form, no invented link;
  - `unsupported`: outside reviewed catalog.

## Compatibility

- `/api/search/ask`, SSE endpoints and `recommended_forms` stay unchanged.
- Existing route adapters may call the new service only in shadow/active mode.
- Public responses never expose reviewer notes, raw storage paths, audit hashes or private attachments.

## Stable error codes

`FORM_CASE_NOT_FOUND`, `FORM_CASE_FORBIDDEN`, `FORM_DOMAIN_FORBIDDEN`, `FORM_TRANSITION_INVALID`, `FORM_SOURCE_REQUIRED`, `FORM_CHECKSUM_MISMATCH`, `FORM_ATTESTATION_STALE`, `FORM_RELEASE_GATE_FAILED`, `FORM_RELEASE_POINTER_CONFLICT`, `FORM_AMBIGUOUS_PROCEDURE`, `FORM_SOURCE_GAP`, `FORM_SCHEMA_UNAVAILABLE`, `FORM_NOTIFICATION_PENDING`.

