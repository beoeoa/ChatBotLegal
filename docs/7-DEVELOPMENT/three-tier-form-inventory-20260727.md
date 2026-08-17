# Three-tier official form inventory

## Scope

The inventory covers the five configured legal domains at two execution
levels: provincial and commune/ward.  It consumes the public national
administrative-procedure API and classifies current procedures as:

- `central`: a standard procedure promulgated by a central authority;
- `hai_phong_override`: a specific procedure promulgated by Hải Phòng;
- `le_chan_local`: a unique procedure/form promulgated by phường Lê Chân.

A provincial or ward website mirroring a centrally prescribed template is
provenance, not by itself an override.

## Safety model

`scripts/build_three_tier_form_inventory.py` is candidate-only.  It does not:

- change `LEGAL_SECTION_GROUNDING_ENABLED`;
- mutate the canonical runtime catalog or approval state;
- delete or re-embed corpus data;
- change the active vector collection;
- call a language model to infer metadata.

The pipeline first filters procedure state, five-domain scope and executing
level.  It then classifies profile components deterministically.  Official
results and supporting documents are counted as removed and are not emitted as
form candidates.

## Canonicalization

`api/three_tier_form_inventory.py` applies the hard gates and deduplication.
An identical checksum is not sufficient to merge two forms when a package file
contains multiple distinct templates.  The form code, or the normalized form
name when no code is published, must also agree.  Where a downloadable file is
not available, code plus issuing instrument may identify the same form.

A review candidate must have an exact procedure ID, official source page,
validated downloaded file and checksum, issuing instrument, explicit
effectivity, provenance and a verified source tier.  Missing facts are recorded
as `VERIFIED_DATA_GAP`; dates are never inferred from an issuance date or from
model output.

Already reviewed records may be included to calculate the current unique
canonical count, but are omitted from the new human-attestation shortlist.

## Artifacts

- `notebook_data/forms/three_tier_procedure_catalog_v1.json`: full scoped
  procedure catalog with official IDs, codes, level, domain and tier.
- `notebook_data/forms/three_tier_form_inventory_v1.json`: private detailed
  candidates, hard-gate decisions, gaps and exclusions.
- `notebook_data/forms/three_tier_form_review_shortlist_v1.json`: records that
  pass automated gates and still require a logged-in human reviewer.
- `reports/feature005/three-tier-form-inventory-latest.json`: privacy-safe
  aggregate evidence.
- `notebook_data/forms/three_tier_form_groups_v1.json`: exact groups keyed by
  strict form code and explicit issuing-instrument number.
- `notebook_data/forms/three_tier_form_source_resolution_v1.json`: detailed
  official-source resolution, effectivity decision, artifact and gap.
- `notebook_data/forms/three_tier_form_review_shortlist_v2.json`: technically
  eligible records waiting for the authenticated Admin attestation.
- `reports/feature005/three-tier-form-resolution-latest.json`: privacy-safe
  aggregate source-resolution evidence.

The report contains counts and reason codes only.  It does not contain user
questions, generated legal answers or credentials.

## Reproduction

Run:

```powershell
python scripts/build_three_tier_form_inventory.py `
  --legal-as-of 2026-07-27 `
  --workers 8

python scripts/resolve_three_tier_form_sources.py `
  --legal-as-of 2026-07-27 `
  --workers 6 `
  --commit-candidates
```

Run focused verification:

```powershell
python -m pytest `
  tests/test_three_tier_form_inventory.py `
  tests/test_form_source_resolution.py `
  tests/test_form_review_sync.py `
  tests/test_official_procedure_source_discovery.py `
  tests/test_canonical_form_crawler.py `
  tests/test_step1_form_catalog_hard_gates.py -q
```

## Official-source resolution

The resolver uses the Ministry of Justice VBPL portal as the
legal-document identity and attachment source. Search results are accepted
only when the complete normalized document number matches. The file list is
retrieved for that exact document ID and downloads use the official Ministry
gateway.

The strict identity parser requires a numeric form code and rejects natural
language false positives such as `mẫu hộ`, `mẫu quy định`, `mẫu tại` and
`biểu mẫu điện tử`. Document numbers containing sub-numbers, for example
`66.18/2026/NQ-CP`, retain the full identity.

Effectivity is checked at `legal_as_of`. Expired instruments are excluded.
Partially expired instruments are not assumed to keep a particular form in
force and receive `PARTIAL_EFFECTIVITY_REQUIRES_REVIEW`.

Standalone attachments are preferred. For a PDF or DOCX package, extraction
starts only at an element/page containing exactly the requested form code and
ends before the next distinct form code. Multiple different extracted
artifacts, same-page mixed forms, legacy DOC packages without a deterministic
splitter and scanned packages without reliable text remain fail-closed.

The reproducible 2026-07-27 run produced:

- 715 official-procedure form occurrences;
- 144 exact `form_code + issuing_instrument` groups;
- 406 occurrences with unresolved identity;
- 49 distinct issuing instruments checked;
- 3 groups and 3 exact procedure bindings ready for human attestation;
- 547 fail-closed gap records;
- zero automatic legal approvals and zero external-call blockers;
- one stale candidate quarantined after a regression caught a truncated dotted
  document number.

The second full run reproduced all three queue records as `unchanged`. The
bulk preview shows three eligible proposed canonical forms. Before the Admin
decision they remain `candidate_pending_review`, `approved=false` and
`runtime_eligible=false`.

The attestation transaction supports a new technically verified form that is
not in the original 93-form catalog. The logged-in Admin's single decision
creates or updates the canonical form and exact procedure binding in the same
transaction as the official index, checksum manifest and audit attestation.
A failed transaction remains fail-closed.
