# Official data completion runbook (2026-07-28)

This runbook operationalizes
`specs/005-calibrated-grounded-answers/plan.md` for the five-domain Central →
Hai Phong → Le Chan serving scope.

## Current checkpoint

- Resume existing form campaign `20260728030530-d52039a7`; do not create a
  competing run.
- The resumed report has 715/715 terminal occurrence records, 203 identity
  groups, 319 procedure bindings, 10 candidate bindings ready for attestation,
  675 gap occurrences, no blocked-external occurrence in the offline cached
  pass and 355 unresolved identities. This is not a legal approval.
- Current catalog baseline is 99 canonical forms with 16 approved. The active
  collection is `legal_chunks_lechan_primary_v20260723`.
- Keep `LEGAL_SECTION_GROUNDING_ENABLED=false` until one release manifest is
  green and the required human decisions are recorded.

## Safe execution order

1. Snapshot database/catalog/audit/source files and write a run manifest.
2. Reconcile expected legal sources against current PostgreSQL/SurrealDB and
   retrieval metadata. Do not crawl a source that is already present; fix a
   `FOUND_NOT_RETRIEVED` case in retrieval instead.
3. Resume deterministic official-source resolution. Final URLs must be on the
   government allowlist; a search engine result is not provenance.
4. Download, checksum and extract one artifact at a time. OCR scanned pages
   locally with page-level resume. Partial extraction is not review-ready.
5. Resolve instrument/form identity, procedure mapping and provision/appendix
   effectivity from official evidence only.
6. Show only all-green hard-gate candidates in the Admin shortlist.
7. Let the authenticated legal reviewer attest a batch once. The server writes
   catalog, binding, official index, checksum, attestation and audit records
   atomically and idempotently.
8. Import only the approved legal-document delta into shadow/support scope.
   Forms remain exact catalog assets and are not embedded.
9. Run form validation, retrieval-only evaluation, role/API/UI, privacy,
   authorization, rollback and performance gates on the same manifest.

When a completed shadow resolver result must replace a partial checkpoint,
first back up the campaign and queue, then sync the shortlist with:

```powershell
python scripts/sync_shadow_form_candidates.py `
  --shortlist reports/feature005/live-20260728/resolver-regression/shortlist.json `
  --queue notebook_data/forms/official_forms_candidates_classified.json
```

The command is idempotent, preserves approved decisions, quarantines stale
generated candidates and never promotes a pending record. Copy only the
completed resolver artifacts into the campaign run, then resume the campaign
with `--no-network`; this reuses the durable checkpoint rather than replacing
it with an empty offline result.

## Failure handling

- Missing official file, unresolved effectivity or ambiguous mapping becomes
  `VERIFIED_DATA_GAP` with a reason code.
- CAPTCHA, login, persistent 429 or unavailable official infrastructure becomes
  `BLOCKED_EXTERNAL` with retry history.
- Extraction or transaction failure becomes `sync_failed`; it never becomes a
  runtime candidate.
- A checksum change invalidates the prior preview and requires a fresh
  attestation.
- A repeated attestation request with the same idempotency key returns the
  original result and creates no duplicate decision.

## Release stop conditions

Keep the feature flag false and `rollout_stage=0` for any claim without valid
evidence, expired/wrong-field source, form mapping error, privacy or
authorization leak, skipped role case, generation timeout storm, retrieval
threshold failure, concurrency failure, restore mismatch or unreviewed source.

Artifacts shared outside the operator workspace contain only opaque IDs,
counts, statuses, reason codes, timings, URLs and checksums. They contain no
raw questions, answers, OCR, credentials or attachment bodies.
