# Feature 017 — Procedure and form governance

## Delivered architecture

Feature 017 introduces a PostgreSQL-canonical workflow for commune procedures and official forms. It is deliberately disabled by default: the currently released JSON catalog remains read-only and continues to serve users until a separate migration and activation approval.

The legal workflow is:

```text
Officer submit/supplement
→ Admin source review
→ legal metadata enrichment
→ checksum-bound attestation
→ immutable release candidate
→ deterministic release gate
→ atomic active pointer
→ citizen Form Router
```

Source approval never makes a form public. Legal attestation binds the procedure, form, source, checksum, bindings, conditions, aliases, revision and reviewer. Release activation is a third explicit action.

## Data ownership

- PostgreSQL tables from `scripts/feature017_migrations/` are the only writable legal source after activation. Each normalized procedure/form/binding/alias row is versioned by `release_ref`; activation materializes and verifies these projections in the same transaction that changes the active pointer.
- SurrealDB is notification/inbox projection only. Projection failure cannot revert or redefine legal state.
- Existing JSON catalogs are read-only compatibility data.
- `form_workflow_event` is append-only and hash-linked; updates/deletes are denied by trigger.
- No form/history is hard-deleted by application workflow.

## Runtime flags

- `FORM_GOVERNANCE_SOURCE=json_compat` (default): new workflow writes return `FORM_SCHEMA_UNAVAILABLE`; existing public catalog is unchanged.
- `FORM_GOVERNANCE_SOURCE=postgres_shadow`: PostgreSQL repository is available for separately rehearsed environments.
- `FORM_GOVERNANCE_ROUTER_MODE=shadow`: compare Feature 017 decision in internal trace only.
- `FORM_GOVERNANCE_ROUTER_MODE=active`: serve the active PostgreSQL release. This flag must not be enabled before separate approval.
- `FORM_RELEASE_VERIFY_REMOTE_SOURCES=true`: download/check remote official sources during the Release Gate. It is forced on whenever `FORM_GOVERNANCE_SOURCE=postgres_active`.

There is no fallback from a failed PostgreSQL write to JSON write.

## Deterministic Form Router

1. Match exact procedure code, name or approved alias.
2. If exact matching fails, deterministic BM25 may nominate a procedure only; exclude/hard-negative aliases are honored.
3. Shared short phrases or near-tied candidates return clarification without forms.
4. Final form IDs come exclusively from bindings in the active release.
5. Procedure, binding and asset must all be released, effective, audience-eligible and checksum-bound.
6. Required forms are all returned. Conditional forms keep their condition. E-forms return the official URL.
7. A missing/expired/wrong-role/bad-checksum member blocks the entire expected form set; the router never returns a misleading partial set.
8. The provider receives an Evidence Packet but cannot add/change form IDs or links.

## Migration rehearsal

`scripts/manage_feature017_form_schema.py` defaults to plan-only and refuses common live/default database names. Apply/rollback requires a uniquely named isolated database and `--confirm-isolated`. The migration is additive and does not touch legal documents, chunks or vectors.

`scripts/rehearse_feature017_postgres.py` exercises the real PostgreSQL repository after migration. It covers officer submission, Admin source approval, legal enrichment, attestation, evidence-backed verified gap, candidate creation, Release Gate, atomic activation, normalized catalog projection, notification outbox and append-only audit enforcement. Database-native dates/timestamps are normalized before manifest hashing and JSON persistence, so the in-memory and PostgreSQL paths produce the same deterministic contract.

## Golden V3

`scripts/build_feature017_golden_v3.py` builds exactly 1,000 review-ready cases only from a complete release manifest. Every case remains `proposed`; the script never produces an approved checksum. Model paraphrases, when added later, may change only wording—not IDs, conditions, sources, state or expected answer mode.

The source manifest contains both province and commune procedures: 418 total = 227 province + 191 commune. Feature 017 now applies the explicit `executing_level=commune` filter and deterministically reconciles the approved baseline:

- 191 commune procedures;
- 131 form identities;
- 229 procedure–form bindings;
- 31 runtime-released identities;
- 100 identities still requiring a legal source decision.

`reports/feature017/coverage-baseline.json` contains the read-only scoped queue. `outputs/feature017-form-review-campaign-100/feature017-form-review-campaign-100.xlsx` is a human-review workbook; it does not write PostgreSQL, attest, or release anything.

## Verification recorded on 2026-08-11

- Feature 017 focused suite: 34 passed, 0 failed after the final PostgreSQL date-normalization fix.
- Existing form/citation/role regressions: 40 passed, 0 failed.
- Contract, coverage, Golden-builder and 1,000-resolution performance checks: passed. Synthetic pre-release router benchmark resolved the exact procedure and exact form set in 1,000/1,000 cases; P50 3.519 ms, P95 6.762 ms and maximum 28.352 ms.
- Isolated PostgreSQL 18.4 rehearsal: migration up/verify, officer→Admin→attestation→candidate→gate→active, one released procedure, one verified-gap procedure, normalized SQL projections, two outbox messages, seven immutable workflow events and down/verify-down passed. The exact disposable database was removed after verification; no live database was changed.
- Rehearsal evidence: `reports/feature017/postgres-isolated-rehearsal.json`.
- Frontend lint: passed.
- Frontend production build/TypeScript: passed.
- Full frontend test run: 177 passed, 0 failed.

## Post-activation answer guard refinements (2026-08-12)

- A released form is allowed to reconcile a missing form facet only when the
  deterministic router sets `procedure_identity_confirmed=true`; approved and
  official URLs alone are insufficient.
- The same identity gate now applies to the public form section and catalog
  citations, preventing a valid asset from certifying or hiding an unrelated
  procedure answer.
- The DeepSeek Golden evaluator supports `--role citizen|officer`, atomic
  `--checkpoint` writes every 25 completed cases and `--resume`. Checkpoints
  are bound to the role and golden input and contain no credentials. Live
  reports store question/answer SHA-256 hashes, bounded reason codes and
  allow-listed timings by default; plaintext requires the explicit
  `--include-content` opt-in for synthetic ignored data only.
- Focused Feature 017/form/structured-answer regressions: 111 passed.
- The optimized latency profile is disabled by default and canary-only. When
  explicitly enabled for citizen/officer, it bounds core/expanded retrieval to
  4/6 and 2/3 seconds, generation to 12/14 seconds, total orchestration to
  20/24 seconds, context to the full 6,000/12,000 characters and output to
  1,536/2,048 tokens. The 4,500/9,000 context is benchmark-only and requires
  `LEGAL_ANSWER_OPTIMIZED_PROFILE_CONTEXT_BENCHMARK=true` after the grounding
  and coverage gates pass. The public `timing_summary` contains only numeric
  allow-listed stage timings.
- When `LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED=true`, the persisted canary
  state is authoritative: Stage 0 enables no optimized role, Stage 1 enables
  citizen, and Stage 2 enables citizen plus officer. A missing or malformed
  state fails closed with a service/configuration error.
- State changes are managed only through
  `scripts/manage_optimized_profile_rollout.py`; `--advance` requires a JSON
  evidence file. The gate requires quality and performance pass flags, full
  answer P95 at or below 25 seconds, fallback at or below 3%, provider errors
  below 0.5%, and, before opening the officer stage, at least 100 citizen
  samples observed over 24 hours. No live canary state file is created by
  development or test commands.
- Runtime smoke evidence (local-only, 2026-08-12): the same citizen form-only
  question measured 26.3s on the baseline and 12.0s with the optimized profile
  in two separate process configurations. The optimized run returned one
  identity-confirmed official form with `answer_mode=normal` and no provider
  fallback after the deterministic form-only correction. This is a smoke
  result, not a P95/concurrency gate; the profile was disabled again after the
  probe.
- The previous 1,000-case report remains historical evidence only: it recorded
  427/1,000 evaluator passes before the optimized profile and deterministic
  form-only correction. Its 507 answer-mode mismatches must not be treated as
  a release pass; the live 1,000-case evaluation remains open under T086.
- Citizen canary probe after connection and mode fixes (local-only,
  `reports/feature017/deepseek-optimized-citizen-100-v2.json`): 100/100 HTTP
  responses succeeded, exact form set was 100/100, full-answer P50/P95/max was
  4.58/6.87/8.20 seconds, and unexpected provider fallback was 1/100. The
  evaluator pass rate was 95/100; the five remaining mode mismatches include
  owner-deferred cases whose approved Golden expectation is `source_view_only`
  and one released mixed dossier with provider invalid output. This is not a
  quality gate pass and does not authorize Stage 1 or officer rollout. The
  profile was disabled after the probe.

## Activation checklist

Before any live activation:

1. Approve and run migration on a backed-up staging clone, then verify rollback.
2. Have officers/Admin review the 100 rows in the campaign workbook; do not import workbook decisions automatically.
3. Complete every identity/binding as released or evidence-backed verified gap and retain official URL/checksum/effectivity evidence.
4. Run download/e-form/checksum/effectivity/role gates.
5. Build active release and Golden V3 workbook; obtain user approval.
6. Run full-answer and performance gates.
7. Request separate approval for citizen browser UAT and public activation.

## Approved 100-source campaign checkpoint (2026-08-11)

The user reviewed all 100 pending identities. The decision is recorded at the
source-review boundary only: 73 rows are source-approved and 27 rows remain
`needs_supplement` because their code, issuing instrument or stable identity is
not yet complete. No row was automatically attested or released.

`scripts/collect_feature017_approved_sources.py` now performs a candidate-only
collection from the fixed National Public Service Portal endpoints. It warms
the exact official procedure page to obtain the portal's WAF cookie, refreshes
only the 123 procedures in this campaign, validates attachment IDs, rejects
redirects, checks file magic/macro/malware constraints, computes SHA-256 and
stores files only under the Feature 017 staging cache. E-form provenance uses
a canonical metadata snapshot checksum rather than a fabricated file.

Current source checkpoint:

- official snapshot legal date: `2026-08-11`;
- 123/123 procedure details refreshed, zero fetch errors;
- 60 identities ready for legal enrichment: 37 file identities and 23 e-form
  identities;
- 13 identities require exact appendix/instrument package evidence because no
  suitable standalone DVC attachment is available;
- 27 identities remain in the explicit supplement path;
- runtime catalog mutation, legal attestation, release creation and public
  activation: zero.

The collector deliberately keeps identical files referenced by more than one
procedure as one canonical asset checksum with multiple attachment/binding
provenance entries. A filename that explicitly names a different form code is
rejected even when the portal attached it to the otherwise matching component.
Four identities had multiple DOCX binary checksums from different procedures;
the normalized full document text was byte-for-byte equivalent after Unicode
and whitespace normalization. They are accepted as one semantic form identity,
while every binary checksum remains in provenance. This equivalence gate is
DOCX-only and any text difference remains fail-closed.
The refreshed official snapshot and checksum-bound result are stored under
`data/source_cache/feature017_approved_sources_20260811/` and are staging
evidence only.

`supplement-queue.json` now materializes the 27-row resubmission queue from the
same snapshot. It preserves the user's review decision, lists the exact DVC
procedure/component evidence, exposes official attachment identifiers and all
official legal-basis candidates, and names each missing field. Six rows already
have one official attachment available; 21 have no official file/e-form yet.
The queue never selects a form code or issuing instrument and cannot create an
approval, attestation or release.

The instrument-package pass remains fail-closed for 13 rows: 3 official exact
document identities were not found by the bounded resolver, 5 instruments have
partial effectivity requiring exact-form review, 3 forms reference an issuing
instrument reported expired by the current VBPL API, 1 exact form file is not
present in the located package and 1 official PDF package is ambiguous. In
particular, the current VBPL API reports `175/2024/NĐ-CP` as wholly expired
from 2026-07-01, while older rendered pages/search indexes expose stale or
partial status. The campaign therefore blocks those forms instead of weakening
the effectivity gate; only exact current form/replacement evidence may move
them forward.

Two false source gaps were removed without weakening exact matching. VBPL can
prefix `docNum` with `Số:`, and exact-number searches can return more than 100
relationship hits. Document-number normalization now extracts one unambiguous
number token from that label, and the resolver searches at most five 100-row
pages while still requiring exact number equality. This recovered both forms
01 and 02 from the official `154/2024/NĐ-CP` package; each extracted form has
its own checksum and remains only a legal-enrichment candidate.

`instrument-package-review-queue.json` projects the remaining 13 rows into
explicit Admin actions. Expired instruments are suggested only for replacement
or historical verified-gap review; partial-effectivity cases require exact-form
scope evidence; missing/ambiguous packages require identity or page-range
reconciliation. The projection cannot automatically create a verified gap,
attestation or release.

The curated Hải Phòng status check is now separated from the earlier package
page-range verification date. The official 52/2026/QĐ-UBND package was fetched
again at 15,210,449 bytes with unchanged SHA-256, while current Hải Phòng pages
still publish the decision with effective date 2026-07-10. Exact binding 21Đ,
Appendix 04, procedure 1.115644 was therefore extracted from source page 241
into its own checksum-bound candidate. This refresh does not re-approve the
binding or change the runtime catalog.

## Owner-scoped closeout for the 100-row campaign (2026-08-12)

The owner subsequently directed the campaign to skip both unresolved groups:
27 supplement rows and 13 instrument-package rows. These 40 rows are recorded
as owner-deferred scope decisions, not as evidence-backed verified gaps. They
remain visible for audit/future review but are ineligible for public routing,
runtime bindings and positive Golden cases.

The fail-closed artifacts are:

- `supplement-scope-decision.json`: 27
  `OWNER_DEFERRED_MISSING_EVIDENCE` rows;
- `instrument-package-scope-decision.json`: 13
  `OWNER_DEFERRED_PACKAGE_REVIEW` rows;
- `scoped-legal-enrichment-candidate.json`: the remaining 60 identities,
  99 procedures and 119 bindings;
- `scoped-attestation-preview.json`: the tamper-evident batch preview.

All 60 retained identities have an official URL, a source checksum and either
a checksum-verified staged file or an official current e-form metadata hash.
The 119 bindings are all present in the approved commune requirement manifest
as `required_form_identity_ids`; therefore no LLM or portal `required` boolean
is used to invent binding semantics. All 60 assets are ready to preview for
attestation. They are not attested and remain runtime-ineligible.

Current batch attestation fingerprint:

`f1973a4866f57f7cd58b4267d2bc06dbd0a6aacd0a5ff6d7df2d5bd2e4c6a3f2`

The confirmation command requires this exact fingerprint and an explicit
actor. A mismatch, modified preview, missing actor or unreviewed binding fails
closed. Even a confirmed isolated attestation can create only a release delta;
it cannot change the active pointer. The delta must later be merged with the
existing active release and pass the complete 191/131/229 Release Gate before
Golden V3 or public activation.

Verification after this scope change: 151 Feature 017/source/form-evidence
tests passed, plus the scoped candidate tests and Python compilation. No live
PostgreSQL row, Surreal notification, canonical catalog, vector or public
release pointer was changed.

The final non-browser gate was rerun after the scope closeout:

- Feature 017 contracts: passed;
- schema/workflow/API/release tests: 24 passed;
- frontend: 41 files / 177 tests passed;
- frontend lint and Next.js production build: passed;
- migration command: plan-only, `live_apply=false`;
- Golden V3 negative probe: the unattested candidate is rejected with
  `FEATURE017_RELEASE_INVALID` and no workbook/dataset is created.

A read-only query of the configured local `legal_chatbot` PostgreSQL database
found no `form_*` tables and therefore no active form release to merge. A new
isolated database rehearsal could not be created because the configured
release database role has no `CREATEDB` privilege; the existing successful
isolated rehearsal remains at
`reports/feature017/postgres-isolated-rehearsal.json`. The failed create did
not modify the live database. Live migration remains a separately approved
operation.

## Final packaged candidate after Admin fingerprint confirmation (2026-08-12)

The owner confirmed fingerprint
`f1973a4866f57f7cd58b4267d2bc06dbd0a6aacd0a5ff6d7df2d5bd2e4c6a3f2`
with the `admin` account. This created a staging attestation and a
non-activatable release delta for the 60 retained identities. It did not
change PostgreSQL `legal_chatbot`, SurrealDB, a vector index or the public
release pointer.

The complete candidate now makes an explicit decision for all scoped objects:

- 191/191 commune procedures;
- 131/131 form identities: 91 released-candidate assets and 40
  `owner_deferred` identities;
- 229/229 binding decisions: 141 released-candidate bindings and 88
  fail-closed binding exclusions;
- 40 procedures with no official form listed are evidence-backed
  `verified_gap`; owner-deferred procedures remain distinct and are never
  described as verified gaps.

The initial remote check exposed that 31 legacy URLs pointed to an entire
legal instrument while their checksums described exact extracted form files.
The gate was not weakened. Feature 017 now packages every exact file under the
release asset root, exposes it only through the active-release checksum-bound
download endpoint and keeps the official source page/package as provenance.
For DVC attachments, verification warms the exact procedure page and posts the
attested attachment UUID to the fixed official attachment endpoint. For
forms extracted from a legal package, the exact runtime file checksum and the
official source-package checksum are verified separately.

Final package evidence:

- packaged release manifest SHA-256:
  `a54a014dcc6eaec4834fa8fc0234919f5a8e3fcbdf98fccc66d8b30888473f96`;
- 68 checksum-bound runtime files and 23 official e-forms;
- 19 unique official source packages;
- remote/runtime source gate: 91/91 passed, zero checksum or source errors;
- public activation allowed: false; active pointer changed: false.

A fresh isolated PostgreSQL database was created successfully after correcting
the connection URL rendering used by the earlier rehearsal attempt. It holds
one `validated` packaged release, zero `form_active_release` rows and zero
materialized runtime catalog rows. The live `legal_chatbot` database remains
untouched.

Golden V3 was regenerated from this exact packaged manifest. The builder now
adds the procedure code when two official procedure names normalize to the
same text, preventing false ambiguity between duplicated land-procedure names.
The resulting proposed workbook contains 1,000 rows, 200 per domain and the
fixed 400/300/100/100/100 category distribution. Evaluation results:

- deterministic Form Router: 1,000/1,000 passed, P95 54.2 ms;
- public resolve API contract: 1,000/1,000 passed, P95 56.0 ms;
- exact form set, ambiguity clarification, forbidden-form exclusion and
  provider identity immutability: 100%;
- the workbook remains `proposed`; no approved checksum was fabricated.

Final non-browser regression after packaging:

- Feature 017 contracts: passed;
- Feature 017 tests: 80 passed;
- broader form/source/role regressions: 265 passed;
- frontend: 41 files / 177 tests passed;
- frontend lint and Next.js production build: passed;
- completion audit: complete with no missing requirement for the approved
  non-production slice.

Live migration, active-pointer activation, Golden approval/freeze and browser
UAT remain separate actions requiring explicit approval. This final checkpoint
supersedes the earlier statement that a new isolated database could not be
created.

## Golden V3 owner approval and checksum freeze (2026-08-12)

The owner explicitly approved all 1,000 Golden V3 cases. The proposed JSON and
review workbook remain unchanged as the pre-approval audit record. A separate
approved dataset and workbook were created; the only per-case change is
`review_status: proposed -> approved`. A field-by-field projection check proves
that questions, procedures, expected/forbidden forms, legal dates, source
checksums, answer modes and release provenance were not changed.

Frozen approved checksum:

`609a1fff8839379080307778e65ffe7990d0f5ce543e5f2a3c9e867473a98263`

The approved set remains bound to packaged release manifest SHA-256
`a54a014dcc6eaec4834fa8fc0234919f5a8e3fcbdf98fccc66d8b30888473f96`.
Approval closes only the Golden review gate. It does not apply the Feature 017
schema to live PostgreSQL, activate a public release pointer, write SurrealDB,
change vectors, authorize browser UAT or enable production rollout.

## Owner-approved local-live activation and UAT (2026-08-12)

The owner subsequently gave separate approval for the previously deferred live
migration, candidate import, active pointer, Surreal notification projection,
citizen/officer rollout and direct browser UAT. This approval applies to the
configured local-live deployment. It does not claim that the service has been
hosted on the public Internet.

Before mutation, the deployment captured a PostgreSQL custom dump and a
SurrealDB backup under `backups/feature017-live-20260812-100050/`. The
PostgreSQL dump is 225,183,193 bytes with SHA-256
`c7365cc3147f4138bbf7db455294ce3e238a74e2d47f1b7a7b810150beaf4722`.
The guarded command then applied the additive migration, imported the exact
approved candidate, reran the 1,000-case direct/API shadow gate and switched
the active pointer atomically.

Active release checkpoint:

- release: `forms-2026-08-11-feature017-attested`;
- manifest SHA-256:
  `a54a014dcc6eaec4834fa8fc0234919f5a8e3fcbdf98fccc66d8b30888473f96`;
- approved Golden SHA-256:
  `609a1fff8839379080307778e65ffe7990d0f5ce543e5f2a3c9e867473a98263`;
- runtime catalog: 191 procedures, 91 assets, 141 bindings and 382 aliases;
- notification outbox: five projected events;
- vector index mutations: zero.

PostgreSQL remains the canonical legal/catalog state. SurrealDB stores only
the projected inbox records. The initial projection used the five officer
usernames while the authenticated endpoint queried internal user IDs. The API
now authorizes by the verified session and searches both its internal ID and
username alias; all five officer accounts return exactly one unread release
notification for the active release.

The live DeepSeek check exposed a separate answer-mode problem. Form Router
selected the correct released form set, but an invalid provider rendering
could still downgrade the whole form-only answer to `source_view_only`.
Verified form identity, source, effectivity and checksum are deterministic and
must not depend on LLM formatting. Exact verified form-only answers therefore
remain normal/grounded with the catalog-rendered result; mixed dossier/form
questions and `verified_gap` cases continue to fail closed for every missing
facet. The five-domain live check passed 5/5 HTTP responses and 5/5 exact form
sets; all four released cases returned without provider fallback, while the
one verified gap returned no form. Maximum observed response time was 3.258
seconds.

Direct browser UAT passed for all three roles:

- citizen: NA17 appeared with its official VBPL source and checksum-gated PDF
  download;
- officer: the V2 exact-article route rendered Article 18a with a verified
  direct source in the officer-scoped interface;
- admin: `/legal-import` displayed the live review dashboard and coverage of
  191/191 procedures, 91/91 form assets and 141/141 bindings.

Final regression evidence: 129 Feature 017/form/structured tests passed; the
frontend passed 41 test files and 177 tests, lint and the Next.js production
build. The final guarded active verification is in
`reports/feature017/live-final-verify-20260812.json`; the DeepSeek evidence is
in `reports/feature017/deepseek-exact-form-e2e-v2.json`; the consolidated UAT
checkpoint is in `reports/feature017/live-uat-summary-20260812.json`.

The 1,000-case deterministic router/public catalog API result must not be
misreported as 1,000 live DeepSeek generations. A separately approved live
DeepSeek batch was started with six concurrent citizen requests, but the first
multi-facet cases remained inside the current 90-second normal / 180-second
hard total budgets. That would require hours and is already outside the target
API P95 of 25 seconds. The batch was stopped before 100 completed cases to
avoid non-SLA provider traffic. The reusable evaluator is
`scripts/run_feature017_deepseek_golden1000.py`; completing it remains T086
after latency budgets and fallback behavior are tuned without weakening
grounding. No partial result is claimed as a pass.

Additional balanced-domain citizen probe (hash-only) is recorded at
`reports/feature017/deepseek-optimized-citizen-balanced-100.json`: 100/100 HTTP
200 and 100/100 exact form sets, with P95 end-to-end 10.3 seconds and P95
retrieval 6.0 seconds. Only 78/100 cases passed the evaluator because 22
released dossier cases entered the explicit `invalid_output` provider
fallback. The fallback still exposed verified forms and source metadata, but
the provider-error threshold is not met; Stage 1 remains closed.

The structured-provider circuit default is now two consecutive failures before
opening. This prevents one transient invalid JSON response from cascading into
`provider_circuit_open` for the rest of a concurrent canary window; explicit
failure, citation and grounding gates remain unchanged.

The follow-up provider-concurrency benchmark is recorded at
`reports/feature017/deepseek-optimized-citizen-balanced-100-concurrency6.json`:
100/100 HTTP 200, 100/100 exact form sets, P95 end-to-end 12.5 seconds,
evaluator pass 80/100 and 13 unexpected provider fallbacks. It improves the
short canary but still fails the provider-error threshold and is therefore not
the rollout default. The runtime was returned to the disabled optimized
profile and default provider concurrency after the benchmark.

The optimized profile now performs a deterministic preflight after retrieval.
It may skip provider provisioning only when the extractive renderer reports
full facet coverage, fully grounded aggregate status, complete issue facets
and a passing claim-evidence quality gate. This preserves the existing
effectivity, citation and form checksum gates while removing avoidable provider
timeouts for already-proven answers. Partial or gap cases still use the
existing `verified_source_condensed` or `source_view_only` fallback modes.

A 20-case balanced citizen probe after the preflight change is recorded at
`reports/feature017/deepseek-optimized-citizen-preflight-20-v3.json`: 20/20
HTTP 200, 20/20 exact form sets, P95 end-to-end 12.6 seconds, and one
unexpected provider fallback. The evaluator remains below release acceptance
because Golden answer-mode mismatches are still present; this evidence does
not open Stage 1 or T086.

The subsequent user-facing fallback suppression probe is recorded at
`reports/feature017/deepseek-optimized-citizen-no-fallback-20.json`: 20/20
HTTP 200, 20/20 exact form sets, P95 end-to-end 11.8 seconds and zero
`unexpected_provider_fallback` errors. The API no longer exposes
`AI_PROVIDER_FALLBACK` for deterministic condensed/source-view responses. The
internal deterministic safety renderer remains active so unsupported claims
are never invented; this is presentation/error suppression, not a relaxation
of legal grounding gates. Golden answer-mode mismatches still keep T086 open.
