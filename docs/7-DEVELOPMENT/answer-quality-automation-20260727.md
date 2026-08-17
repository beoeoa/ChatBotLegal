# Answer quality automation - 2026-07-27

## Safety boundaries

- `LEGAL_SECTION_GROUNDING_ENABLED` stays false during implementation.
- Queue approval is not legal approval.
- No source-gap workflow approves, indexes, embeds, deletes, or rewrites corpus
  data.
- The active retrieval collection is not changed.
- TLS certificate and hostname checks are mandatory.

## Legal form attestation

Queue approval is fail-closed. A form can become runtime eligible only after an
authenticated legal reviewer submits one atomic attestation that synchronizes
the candidate, canonical form, procedure binding, official index, checksum
manifest, and audit record. Validation happens before writes; partial writes
are rolled back.

## Large-dataset evaluator

`scripts/evaluate_retrieval_dataset.py` writes schema
`retrieval-dataset-v2`. Cache identity is SHA-256 of normalized question,
`legal_as_of`, dataset version, and retrieval version. Runs also record an
opaque run ID, dataset checksum, and retrieval/generation versions.

Model sampling is optional and never used during retrieval. Responses are
scored without a model judge. The privacy-safe report contains aggregate case
pass, available-facet coverage, grounded-claim rate, citation validity, form
gate, internal-marker, provider error, fallback, latency, request, and cost
metrics. It contains no question, answer, prompt, credential, or chunk content.

Provider errors remain failed cases. A provider failure affecting at least half
the selected model cohort produces `BLOCKED_EXTERNAL`.

## SourceGapJob

`api/source_gap_jobs.py` handles explicit `MISSING_FORM_SOURCE` and
`MISSING_LEGAL_SOURCE` findings. `FOUND_NOT_RETRIEVED` remains a retrieval
regression and never starts a crawl.

The existing crawler scheduler runs a due job at most once per day. Only
official-government hosts are accepted. Redirects, MIME, file magic, size,
checksum, and basic active-content signatures are validated. A successful
download remains `candidate_pending_review`.

After seven checks on distinct days, an official page with no public file
becomes `verified_gap`; persistent external failure becomes
`blocked_external`. A weekly monitor may reopen a verified gap.

## Four priority form groups

`scripts/prepare_priority_gap_candidates.py --apply` prepares four pending
records:

1. Land registration change: current Mẫu 11/ĐK is proposed, while requested
   legacy Mẫu 09/ĐK is quarantined as superseded.
2. Foreign-element birth registration: procedure code `2.000528`.
3. Foreign-element marriage registration: procedure code `2.000806`.
4. Individual-house construction permit: Mẫu 01, procedure code `1.009122`.

Every candidate has an official source page/download, a real local asset,
SHA-256, and provenance. Every candidate remains `runtime_eligible=false` until
legal review.

## Rollback

Rollback is data-safe:

1. Keep `LEGAL_SECTION_GROUNDING_ENABLED=false`.
2. Stop the source-gap scheduler with `LEGAL_SOURCE_GAP_ENABLED=false` if
   external sources are unstable.
3. Pending candidates remain non-runtime and can be rejected through the
   normal review path.
4. Do not change the active collection and do not re-embed the corpus.
