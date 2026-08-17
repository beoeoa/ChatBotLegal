# Feature 006 — Runtime form/procedure bridge and grounded form answers

Date: 2026-07-30  
Scope: public Ask, form catalog, release package, five-domain release benchmark

## Decision

The public runtime may recommend a form only when all of these checks pass:

1. the form identity is `approved`;
2. the source is classified as official;
3. the form is marked runtime eligible;
4. the procedure-to-form binding is approved;
5. an official source URL, legal basis and downloadable asset are present.

The 278-identity inventory is not itself a runtime allow-list. Identities still
in `PENDING_LEGAL_REVIEW` remain fail-closed and cannot affect form ranking,
citations or download recommendations.

## Failure found

The public Ask resolver previously relied mainly on local textual procedure
aliases. Official numeric procedure codes in the three-tier procedure catalog
were not consistently bridged into the runtime form catalog. A repeated,
generic form number such as `Mẫu 01` could therefore outrank an explicit
official procedure code and return unrelated forms.

The model-generated answer could also mention form names extracted from nearby
legal chunks even after the deterministic `recommended_forms` list had selected
the correct form. This created a disagreement between answer text and the
download card.

## Implementation

- Added an approved official numeric procedure bridge from the three-tier
  procedure catalog into both runtime resolution and release packaging.
- Made an explicitly cued official procedure code (`mã thủ tục`, `thủ tục mã`)
  rank above repeated generic form codes.
- Rejected pending identities, runtime-ineligible identities and unapproved
  procedure bindings before ranking.
- For a form-only question, replaced model prose with a deterministic answer
  assembled only from the approved catalog fields: form name/code, effective
  date, legal basis, official source and download URL.
- Marked form-catalog citations with an internal attestation source so the
  quality validator can verify them without requiring retrieval chunk IDs.
- Added focused retrieval expansion for first-instance administrative
  complaints. The expansion is query-only; returned claims must still match an
  active official source quote.
- Normalized Vietnamese `đ` during structured query folding, recognized
  bulleted conclusion labels, removed repeated claims across rendered sections
  and limited each issue facet to one displayed proposition while retaining
  source diversity in citations.

No schema or legal-corpus rewrite was performed.

## Release artifacts

- Runtime package manifest:
  `release-data/notebook_data/forms/runtime_form_package_manifest.json`
- Exact procedure/form lookup dataset:
  `reports/feature006/form-lookup-release-dataset.json`
- Lookup quality report:
  `reports/feature006/form-lookup-quality.json`
- Five-domain chatbot report:
  `reports/feature006/five-domain-chatbot-quality.json`

At the time of this decision the runtime package contains 51 approved forms,
73 approved bindings, 59 procedures and 51 verified assets. The complete
inventory remains 278 identities, with 227 still pending legal review.

## Verification

- Exact lookup dataset: 418 procedures × 3 roles = 1,254 cases.
- Exact form recall: 1.0.
- Wrong, missing, pending, expired and role-leak counts: 0.
- Five-domain live Ask benchmark: 5/5 cases pass, minimum and average score
  10.0, median 8.496 seconds, p95 25.671 seconds, zero critical errors.
- Focused form/grounding/structured-answer regression suites pass.

The five-domain score is a deterministic release preview, not a substitute for
legal review of the 227 pending identities.

## Post-attestation campaign reconciliation before supplemental probe rebind

The campaign was rebuilt from the same 418-procedure DVC snapshot after the
five approved identities were promoted. The current checksum-bound state is:

- 278 target identities;
- 51 runtime-approved identities;
- 227 non-serving identities;
- 68 identities that pass technical gates and are partitioned into immutable
  human-review batches of 25, 22 and 21;
- 159 identities retained as explicit non-serving research gaps;
- zero unaccounted identities and zero automated approvals.

The reconciliation fixed four campaign defects:

1. the bridge and backup baseline no longer hard-code the historical 46/232
   counts after an authenticated approval delta;
2. a resolver hit that later fails a technical hard gate remains an explicit
   gap instead of disappearing from both the shortlist and gap inventory;
3. review candidates are validated for Unicode corruption before batching;
4. decomposed Vietnamese source metadata is normalized deterministically to
   NFC in the review projection while the immutable resolver source record is
   preserved.

Current campaign evidence:

- run ID: `20260730102031-9de39f6a`;
- requirement manifest SHA-256:
  `c610277239a3294f3ad1d78e2aa1ae16756575981b07ef7ab973b86e75fd9ed9`;
- source snapshot SHA-256:
  `2dedfc177b8cc12f972e8150616a44d9c71f14c9a2733f48b6d67ff0c26cde19`;
- review-batch file SHA-256:
  `4e9a65c8f269a0f3170033b6a93182cae1d75fd6dd376bec9791d76f08add469`.

The refreshed 1,254-case lookup matrix still has exact recall 1.0, zero wrong,
missing, pending, expired or role-leaking forms, and lookup p95 128.691 ms.
The warm five-domain live benchmark passes 5/5 with minimum/average 10.0,
median 9.721 seconds, p95 27.098 seconds and zero critical errors.

## Final release-runner hardening

The direct Windows entry point now inserts the repository root into `sys.path`
before late campaign handoff imports. Benchmark case discovery also supports
both package import and direct-script execution. This prevents the retrieval
virtual environment's `PYTHONPATH` from shadowing the repository `scripts` or
`api` packages.

Unhandled runner reports persist only the exception class, never the exception
message, command output or credential values. The final run
`20260730-121132` reached stage `complete` with 15 PASS, 3 FAIL and 0 BLOCKED;
all disposable accounts were cleaned and the private credential file was
removed. Its failures are explicit release blockers: golden-167 p95 latency,
six role cases missing approved runtime forms, and an attestation/campaign
provenance mismatch.

## Rollback

Revert the runtime bridge, deterministic form-answer helper and focused query
expansion changes together. Rebuild
`runtime_form_package_manifest.json` from the prior approved catalog snapshot
and rerun the exact lookup plus five-domain benchmark before serving.

## Feature flag

Persisted `LEGAL_SECTION_GROUNDING_ENABLED` remains `false`. Section grounding
was enabled only in the isolated benchmark runtime; this decision does not
change the persisted production default.

## Final restore and checksum-bound release manifests

The release backup was restored into a new, non-runtime directory and all five
files reproduced the recorded bytes and SHA-256 values. The drill did not
mutate the runtime catalog and did not copy or mutate the legal corpus or vector
collection. Evidence is stored in:

- `backups/form_completion/goal-20260730-release-drill-51/backup-manifest.json`;
- `backups/form_completion/goal-20260730-release-drill-51-restored/restore-verification.json`.

The official-form review index changed after the earlier reconciliation
backup: it now contains 845 references instead of 766, with 79 added and none
removed. This is review-state drift, not a runtime promotion: the canonical
runtime count remains 51 and the exact runtime delta is zero. The earlier file
was not restored because doing so would erase later review decisions. The
current index checksum is bound into the release manifest instead.

The data-readiness manifest binds 67 form, procedure, legal-source, dataset,
index, code, attestation and release-evidence artifacts. Verification reports
no missing file or checksum mismatch. The unified decision remains fail-closed:

- data status: `BLOCKED_DATA`;
- release verdict: `NO_GO`;
- rollout stage: `0`;
- feature flag: `false`;
- legal reviewer decision: `PENDING`;
- release owner decision: `PENDING`.

The authoritative files are
`reports/feature006/data-readiness-manifest.json` and
`reports/feature006/unified-release-manifest.json`.

The explicit form-role authorization matrix covers 418 procedures for each of
the citizen, officer and admin roles (1,254 cases). All 1,254 pass with zero
wrong, missing, pending, expired or role-leaking forms; overall lookup p95 is
30.299 ms. The five sanitized trap families for wrong instrument, repeated
code, expired form, multi-form procedure and official e-form also pass.

## Supplemental verified-probe campaign

Fifteen identities that remained in the 159-item research queue still matched
checksum-bound official probe evidence from 2026-07-29. They cover 22 exact
procedure bindings. A new verifier confirmed all 15 local artifacts reproduce
their recorded SHA-256 values and open successfully: 14 PDF files and one
legacy DOC opened through LibreOffice.

The compatible candidates were imported into a new candidate-only campaign,
`20260730065330-c94b9e4f`. The candidate queue was backed up before the merge:
20 missing candidate rows were created and two existing rows were preserved
without rewriting their prior review state. The runtime catalog, approved
bindings, attestation ledger and official runtime index retained their exact
pre-merge checksums.

The current checksum-bound completion state is therefore:

- 278 target identities;
- 51 runtime-approved identities;
- 83 identities ready for authenticated human legal attestation;
- 144 explicit research/effectivity gaps;
- four immutable review batches of 25, 25, 25 and 8 identities;
- zero unaccounted identities and zero automated approvals.

The feature flag remains false and the unified release verdict remains
`NO_GO`. Evidence is recorded in
`reports/feature006/form-completion-final.json`,
`reports/feature006/probe-campaign-integration.json` and
`reports/feature006/reconciled-probe-candidate-verification.json`.

## Retrieval latency experiments and rollback

Two cross-request embedding-batching variants and one PostgreSQL lexical
statement-timeout variant were tested. They did not reduce the golden-167 p95
below the 3-second gate and either increased latency or caused one critical
five-domain answer defect. All experimental runtime changes and their tests
were fully reverted.

After rollback, the five-domain live benchmark again passes 5/5 with minimum
and average scores of 10.0, median latency 10.930 seconds, p95 latency 30.872
seconds and zero critical errors. The golden-167 p95 blocker remains open and
is not represented as fixed.
