# Calibrated grounded answers

## Decision

Feature 005 changes multi-issue Ask behavior from one answer-wide grounding decision to request-bound, issue-level grounding. It does not loosen the requirements for legal conclusions.

## Rules

- Retrieval uses only the current user question or a current-question issue span. Conversation history can inform generation but is never a retrieval query.
- Evidence is request-bound and issue-bound. A source cannot support another issue or a later request without independent retrieval and eligibility validation.
- Eligibility checks effective status, official metadata, jurisdiction, issue relevance and legal hierarchy before generation.
- Applicable central law is selected before compatible effective Hai Phong implementation material. Local material never overrides higher-validity central law.
- A sufficient issue may state a legal conclusion with eligible citations.
- A partial issue may show only “Hướng dẫn tham khảo” plus a limitation; it never states a legal conclusion.
- An insufficient issue shows a localized limitation and, when useful, one clarifying question.
- Internal identifiers and citation-marker syntax are never public output.

## Compatibility and rollback

The existing flat Ask response remains available. `answer_sections` is optional and protected by `LEGAL_SECTION_GROUNDING_ENABLED=false` by default. Rollback is disabling the flag and restarting the API. No legal corpus, conversation, audit or source data is rewritten or deleted.

## Observability and privacy

Measure issue count, eligibility outcome, section status count, retrieval/generation/validation/end-to-end timing and repair rate. Do not record raw questions, answers, citation content, credentials, attachments or raw exception messages in feature telemetry.

## Implemented orchestration

When the flag is enabled, the router creates a bounded deterministic issue plan
and invokes the existing Ask graph sequentially once for each issue. Each graph
input contains only the current issue span as both generation and retrieval
query, plus the same server-generated request and issue IDs. The router accepts
a generated legal conclusion only if the legal-reference validator matches it
against that issue's request-local evidence packet. A failed issue is rendered
as a local limitation and cannot replace a verified issue with a global fallback.

The aggregate response remains backward compatible: `answer` and `citations`
are assembled deterministically from validated sections, while the optional
`answer_sections` exposes the verified/partial/insufficient state. Section
citations use only public document metadata and official links. Chunk, trace,
packet and legacy marker values are excluded at both API and UI layers.

## Legacy-path reliability safeguards

The flag-off path is a real rollback of section orchestration, not a rollback of
request isolation.  The router always passes the current question to retrieval;
conversation history is retained only for answer generation.  This prevents a
previous land-related turn from contaminating a later household-registration
query while keeping the existing `AskResponse` contract.

Domain detection is deterministic and is used only when the caller has not
selected a domain.  Reviewed compatibility aliases (for example,
`ho_tich_chung_thuc` and `tu_phap_ho_tich`) are expanded by retrieval without
changing source records.  A clearly selected domain remains authoritative for
authorization and scope checks.

If a draft contains both verified and unsupported legal fragments, the answer
pipeline removes only the unsupported sentence/line and keeps the verified
fragments.  It does not spend an editorial model call on Markdown-only defects,
and an insufficient fallback has no citations.  Missing-section checks are
intent-specific: a question asking only for documents does not require an
unasked fee, deadline, form or procedure section.

The legacy no-basis detector is also scoped to the whole answer.  A localized
warning such as “Nguồn hiện có chưa nêu lệ phí” cannot discard a different
section that has a current-request citation.  The warning is preserved and the
normal strict citation/claim validator still runs; only an answer with no
grounded fragment is converted to the insufficient-evidence fallback.

The same intent-specific rule is rendered into the model contract: headings and
instructions are built from `required_sections`, so a documents-only request does
not prompt the model to invent authority, deadline or fee content.  If an
optional audit write stalls, it is bounded by
`LEGAL_AUDIT_WRITE_TIMEOUT_SECONDS` (default five seconds) and the validated
answer is still delivered.  The audit trace is reduced to statuses, counters and
timings before persistence.

## Local release evidence

Use the local-only launcher `scripts/start_all.ps1`. It starts SurrealDB,
retrieval, API and frontend on ports 8000, 8765, 5055 and 3000 respectively;
it does not start Docker or ngrok. The launcher now isolates the retrieval
sub-launcher so an already-running retrieval service cannot prevent API and
frontend from starting.

Generate performance evidence only after local readiness is green:

```powershell
python scripts/benchmark_section_grounding.py --concurrency 20 --mode warm --case simple_supported --case land_multi_issue --artifact reports/feature005/warm-c20.json
python scripts/scan_section_grounding_artifacts.py reports/feature005/warm-c20.json
python scripts/check_section_grounding_quality_gate.py reports/feature005/warm-c20.json
```

Run cold and warm samples for concurrency 1, 5, 10 and 20. Benchmark files are
aggregate-only and must pass the scanner before sharing. The runner reports
timing and status counts; it does not sign a legal-quality gate or store raw
question, answer, citation, credential or exception content.

The technical gate can return only `blocked` or `ready_for_legal_review`.
It never supplies legal-review approval itself, and blocks if the artifact is
not privacy-safe, requests failed, repair rate reaches 10%, or a warm p95
exceeds 15 seconds.

The benchmark asks for the existing admin-only RAG trace so it can aggregate
retrieval, generation, validation and end-to-end timings. Per-stage summaries
are present only when `LEGAL_BENCHMARK_TOKEN` belongs to an authorized admin;
the artifact still excludes the trace, prompts, answers and citation bodies.

Browser journeys are in `frontend/e2e/section-grounding.spec.ts`. They require
an explicitly supplied local test-account storage state (`E2E_AUTH_STORAGE`)
and a flag-on isolated local runtime for structured cases. No credential or
storage state is committed. The legacy flag-off journey remains the rollback
check.

## Role acceptance run (2026-07-22)

The implementation includes role-specific rendering, form provenance,
round-robin evidence packing, adjacent-article retrieval, runtime-domain alias
mapping and a source-excerpt fallback for structured-model timeout. The local
role matrix is `tests/fixtures/feature005_role_matrix.json`; its private live
report is written under the ignored `reports/feature005/` directory.

The live API run used three isolated Feature 005 accounts and completed all
nine requests. One citizen case and one officer case reached grounded source
coverage, and one admin case reached full coverage. The remaining cases were
blocked by DeepSeek structured-generation timeout and/or unresolved
procedure/form coverage; the matrix summary recorded model, retrieval/issue
planner, validator, form-catalog and UI causes. Successful requests were above
the 30-second release gate, so the feature flag is intentionally `false` after
the run. This evidence is not legal-review approval.

When structured generation times out, the API now returns only eligible source
excerpts with public citations and an explicit not-yet-synthesized notice; it
never fabricates a legal conclusion or invokes a second repair generation. The
runtime launchers load authentication and feature-flag values from local
`.env`, fixing the previous state where login succeeded but Ask calls returned
401.

Verification after the final implementation slice:

- The isolated grounding/role/planner/runtime/timeout suite is green (28 focused
  cases, including the hard-deadline regression).
- The backend environment is isolated from the developer `.env`; the last full
  run after the SurrealDB test fixes reached 698 passed (the later timeout-only
  change has its own focused green suite).
- The frontend unit suite previously reached 82 passed across 20 files and the
  TypeScript check passed. A missing `type-check` npm script is a tooling gap,
  not evidence of a type failure.
- Retrieval index migration was backed up and applied to the active release
  database; EXPLAIN time improved from 7,969 ms to 3,554 ms (2.243x), and the
  direct one-issue service sample was about 2.6 s.
- The latest live smoke still exceeded 45 s before an access log was emitted.
  Stage tracing then isolated the delay: model lookup was about 0.14 s,
  conversation context was 0 ms, retrieval was 3--5 s, model provisioning was
  about 9--12 s on a cold process, and DeepSeek generation reached the 24 s
  budget without producing valid structured JSON. A worker-thread call and a
  shared adapter cache now prevent event-loop blocking and repeated provisioning.
  Warm end-to-end smoke time fell to 27--29 s, but the answer was still the
  verified-source fallback because DeepSeek missed the generation gate.
- Batch retrieval now has a 60-second non-Admin cache with request/issue
  provenance rebound on cache hits; Admin trace requests bypass this cache.
- Reducing the structured output budget to 768 tokens did not make the model
  finish within 24 s. This is a model/provider latency limitation, not a reason
  to lower grounding requirements. The full nine-case matrix remains blocked
  until DeepSeek can produce the structured answer inside the gate.
- A decision-only override benchmark of the already configured
  `deepseek-v4-flash` completed generation in about 15.8--18.9 s and warm
  end-to-end in about 19.5 s, while `deepseek-v4-pro` timed out at 24 s. The
  first Flash case scored 8.6/10 but missed authority and fee coverage. The
  attempted nine-case Flash run then encountered retrieval read timeouts and
  fell back to the legacy path for later cases, so it is not an acceptance run.
  No default model was changed; a model decision and a clean, isolated rerun
  are still required.
- The flag-on path now fails closed when batch retrieval is unavailable; it no
  longer falls through to the legacy Ask graph. Acceptance results therefore
  remain attributable to Feature 005 and cannot become false positives when
  the retrieval service is degraded.
- The deterministic issue planner now recognizes both “nơi nộp” and “nộp ở
  đâu” as authority facets. The birth-registration regression previously
  planned only documents/deadline/fee; it now plans authority/deadline/fee.
- Fine-grained retrieval timing showed that broad lexical SQL, not ANN or
  relationship hydration, consumed 27--35 s for long land/construction
  questions. Relationship source/target indexes were added, lexical terms were
  bounded, and broad lexical scans are now skipped for long questions without
  an explicit legal identifier; the 300-vector ANN candidates and deterministic
  reranking remain active. The representative land query fell from about
  36.5 s to 0.73 s.
- The post-fix nine-case Flash decision run completed with 1/9 passing and four
  critical failures (down from five). Admin foreign marriage reached 100%
  coverage; officer foreign birth reached 75%; construction and meritorious
  person cases returned inside 30 s but lacked evidence coverage. Three citizen
  cases and the Admin building-permit case still exceeded the 45 s client
  budget. This remains decision evidence, not rollout approval.
- `LEGAL_SECTION_GROUNDING_ENABLED=false` is restored after every probe. No
  rollout or legal-review approval is authorized while the live latency and
  coverage gates remain red.

## Step 0 environment gate (2026-07-23)

The runbook Step 0 was completed with the grounding flag still
`LEGAL_SECTION_GROUNDING_ENABLED=false`.

- Preflight confirmed isolated test configuration: tests do not load the
  developer `.env` unless an explicit isolated `.env.test` opt-in is present;
  runtime passwords are cleared from the test process.
- API, PostgreSQL-backed retrieval, Chroma, SurrealDB and the frontend all
  returned healthy/HTTP 200 readiness responses. DeepSeek is configured. Ollama
  remained a non-required optional timeout and did not affect API readiness.
- The full backend suite passed: 708 tests, 11 non-blocking warnings.
- The frontend suite passed: 82 tests across 20 files.
- TypeScript `tsc --noEmit` passed.
- The focused rollback/flag/migration suite passed: 58 tests.
- No legal data, active Chroma collection or feature flag was changed.

Two environment-test regressions found during the first run were fixed and
rerun: CPU launcher preflight now uses a lightweight module-presence check
instead of importing heavyweight CUDA/Transformers modules, and the
plan-only migration assertion now matches the current 12-statement index plan.

## Step 0 rerun (2026-07-26)

The environment gate was rerun after the local API launcher was exercised
against the current service state. The persisted runtime flag remains
`LEGAL_SECTION_GROUNDING_ENABLED=false`, and the test process remains isolated
from the repository runtime `.env` unless `CHATBOTLEGAL_TEST_LOAD_DOTENV` is
explicitly enabled with a separate `.env.test`.

The rerun found and fixed two launcher-only defects: a stale backend PID in
`logs/local-services.json` could prevent a restart even when the local API was
healthy, and the restart readiness probe used a three-second timeout while the
model-provider readiness probe takes about five seconds. The launcher now
validates the listening process command line and uses a 15-second readiness
timeout. Regression coverage is in
`tests/test_local_runtime_preflight.py`.

The current read-only baseline and readiness evidence are recorded in
`reports/feature005/step0-baseline-20260726.json`. No imported legal row,
Chroma vector, active collection or embedding was changed by this rerun.

## DB-1 backup gate (2026-07-23)

The read-only database backup gate passed with
`LEGAL_SECTION_GROUNDING_ENABLED=false`. Runtime target verification resolved
the repository release URL to PostgreSQL `127.0.0.1:5432/legal_chatbot`; an
unrelated shell `LEGAL_DATABASE_URL` pointing at a staging database was not
used.

- PostgreSQL dump: `reports/feature005/backup-db1-20260723/artifact/postgres.dump`
  (224,974,395 bytes; SHA-256 is recorded in the manifest).
- Chroma copy: `reports/feature005/backup-db1-20260723/artifact/chroma_store`
  (40 files; approximately 7.18 GB).
- Core collection: `legal_chunks_vnlegal_lal_haiphong`, 159,530 vectors.
- Source collection: `legal_chunks_vnlegal_lal`, 430,565 vectors.
- PostgreSQL: 25,426 documents, 232,869 articles, 544,078 chunks.
- ID checksums for documents, articles, chunks and both Chroma collections are
  recorded in `db1-manifest.json`.
- `pg_restore --list` completed with exit code 0.
- Copy verification and post-restart pre/post snapshots both report
  `all_match=true` for counts, ID checksums, scope, quality metadata, database
  target and active collection.
- Retrieval was stopped only while copying Chroma, then restarted on the same
  collection and returned `healthy`, `ready=true`, 159,530 indexed vectors and
  544,078 database chunks.

The manifest is
`reports/feature005/backup-db1-20260723/db1-manifest.json`. It contains no
database password, API key, question text or answer text. No imported legal
record, scope row, active collection pointer or vector was changed.

## DB-2 Le Chan shadow serving scope (2026-07-23)

DB-2 was generated in read-only shadow mode by
`scripts/build_lechan_shadow_scope.py`. No sidecar table was created because
schema approval is still pending. The active collection and imported legal
rows were not changed.

The verified shadow manifest is
`reports/feature005/db2-shadow-20260723/manifest.json`:

- all 25,426 documents were classified: 878 `primary`, 746 `support` and
  23,802 `historical_quarantine`;
- all 544,078 chunks were classified: 24,445 `primary`, 29,961 `support` and
  489,672 `historical_quarantine`;
- document and chunk unknown counts are zero;
- primary/support hard-gate violations are zero;
- only one-hop amendment, supplement, replacement and
  guidance/detailing relationships are eligible for expansion;
- `Van ban can cu` expansion count is zero;
- all 25 golden/role source locators that resolve to documents in the corpus
  remain in primary/support;
- five golden expectation rows, representing four unique legal documents, are
  recorded as absent from the corpus, not inferred or silently substituted;
- DB-2 used the stricter legacy `is_approved=true` subset for its initial form
  seed audit. DB-3 below audits all records whose catalog
  `review_status=approved`.

Independent verification is stored in
`reports/feature005/db2-shadow-20260723/verification.json`. It recomputes both
ledger checksums and ID checksums, compares the live PostgreSQL IDs with the
post-run snapshot, confirms the active pointer is unchanged, verifies that no
sidecar schema was created, and confirms
`LEGAL_SECTION_GROUNDING_ENABLED=false`.

## DB-3 coverage and legal-review gate (2026-07-23)

DB-3 compared the verified shadow scope with all 167 golden cases, 202 priority
form requirements, nine role cases and all 662 catalog records marked
`review_status=approved`. The comparison was technically verified and was
subsequently approved by the legal reviewer. The signed verification state is
`approved`.

- 35 expected document/provision rows available in the corpus pass
  effectivity, scope, hierarchy, article/chunk and `primary`-tier checks.
- No available expected source is excluded or assigned to the wrong tier.
- Five expectation rows are `VERIFIED_DATA_GAP`, representing four unique
  absent instruments: `68/2020/QH14`, `60/2021` (the approved fixture does not
  provide the suffix), `23/2008/QH12` and `06/2021/NĐ-CP`. Independent
  verification reproduces zero corpus matches for each identifier.
- The officer foreign-birth role case explicitly identifies the Civil Status
  Law and Articles 13/35; all three locators are present and correctly tiered.
  The other eight role cases do not yet contain legally approved
  `expected_sources` contracts and remain review gaps.
- All 202 priority form rows receive a deterministic result and exact reason.
  None currently has a strict identity + procedure + domain + provenance +
  effectivity match in the approved catalog, so all 202 remain
  `VERIFIED_DATA_GAP`.
- Of 662 catalog-approved records, 19 pass the standalone form hard gate and
  643 fail at least one explicit gate. The dominant issue is unapproved
  effectivity metadata (621 records); other reasons include unknown domain,
  title review flags and non-official source hosts.
- No source is excluded because it is rarely asked or has low query
  frequency.
- PostgreSQL IDs and the active collection pointer are unchanged, the feature
  flag remains false, and no model was used to infer metadata.

The DB-3 scope gate is complete. The approval receipt validates the reviewed
expected/forbidden-source scope and the immutable artifact checksums. This
approval is limited to corpus scope: it does not replace the separate Step 6
legal review of the nine final generated answers.

Artifacts:

- `reports/feature005/db3-coverage-20260723/manifest.json`
- `reports/feature005/db3-coverage-20260723/verification.json`
- `reports/feature005/db3-coverage-20260723/expected-sources.jsonl`
- `reports/feature005/db3-coverage-20260723/procedure-coverage.jsonl`
- `reports/feature005/db3-coverage-20260723/approved-form-audit.jsonl`
- `reports/feature005/db3-coverage-20260723/legal-review-packet.json`
- `reports/feature005/db3-coverage-20260723/verification-approved.json`

## Feature 005 Step 1 retrieval gate (2026-07-24)

Step 1 passed on the complete 167-case golden set using live retrieval only.
The run did not call the answer-generation model, did not fall back to a
fixture, did not re-embed the corpus, and left both the active collection
pointer and `LEGAL_SECTION_GROUNDING_ENABLED=false` unchanged.

The serving retrieval path now:

- parses all exact legal numbers in a query instead of keeping only the first;
- performs normalized exact metadata lookup before ANN;
- queries `primary` first and calls `support` at most once for a missing facet;
- applies deterministic cross-cutting domain aliases for administrative
  penalties, complaints, urban order, land and civil-status cases;
- permits the verified Law `15/2012/QH13` quality exception only when the sole
  exclusion reason is `missing_domain`; noisy-title chunks remain excluded;
- skips the expensive broad lexical content scan for long queries while
  retaining exact lookup and ANN;
- records intent, expected-source assessment, source rank, effectivity, domain,
  hierarchy, tier and selection/exclusion reasons for every issue.

Final serving metrics:

| Gate | Result |
|---|---:|
| Golden cases / classified issues | 167 / 167 |
| `FOUND_AND_RETRIEVED` issues | 165 |
| `VERIFIED_DATA_GAP` issues | 2 |
| Unresolved `FOUND_NOT_RETRIEVED` | 0 |
| Recall@10 | 100% |
| Direct source in top 5 | 100% |
| Wrong-field or expired selections | 0 |
| Retrieval P95 | 372.329 ms |
| Live retrieval coverage | 100% |
| Generation-model calls | 0 |

Eight expected-source rows are recorded in the verified data-gap manifest.
Five refer to identifiers absent from the corpus. Three additional rows were
verified as legal-number collisions or identifiers resolving to an unrelated
legal subject, so they are not treated as retrieval failures and are never
silently substituted.

Evidence:

- `reports/feature005/step1-retrieval-20260724/manifest.json`
- `reports/feature005/step1-retrieval-20260724/verified-data-gaps.json`
- `reports/feature005/step1-retrieval-20260724/final-run.out.log`
- `tests/test_feature005_step1_retrieval.py`
- `tests/test_db5_exact_tiered_retrieval.py`

The focused retrieval and grounding regression suite passed 45 tests. Step 2
must use the verified data-gap manifest as its only missing-source input and
must not invent or substitute documents with colliding legal numbers.

## Feature 005 Step 2 data-gap completion (2026-07-24)

Step 2 is `PASS` with `LEGAL_SECTION_GROUNDING_ENABLED=false`. The operation
did not delete imported rows, re-embed the full corpus, or change the active
collection pointer.

Official current sources added incrementally through the existing ingestion
path:

- Law `68/2020/QH14`;
- Decree `154/2024/NĐ-CP`;
- amending Decree `58/2026/NĐ-CP`.

Existing official sources admitted to the shadow serving scope by deterministic
sidecar correction and reuse of their stored vectors:

- Circular `17/2024/TT-BCA`;
- Law `36/2024/QH15`;
- Decree `168/2024/NĐ-CP`.

The shadow primary collection contains 25,396 vectors. Validation found zero
missing, orphan, stale, empty, duplicate, or embedding-less records among the
951 checked chunks; the 430,968-vector source collection and active pointer
were unchanged.

The final live retrieval-only gate covered 167 issues:

| Gate | Result |
|---|---:|
| `FOUND_AND_RETRIEVED` | 164 |
| `VERIFIED_DATA_GAP` / fail-closed | 3 |
| unresolved `FOUND_NOT_RETRIEVED` | 0 |
| Recall@10 | 100% |
| Direct source in top 5 | 100% |
| Wrong-field or expired selections | 0 |
| Coverage | 98.802% |
| Retrieval P95 | 445.857 ms |

Four partially effective instruments remain excluded until provision-level
legal review. Two deliberately broad pilot questions are fail-closed because
they do not identify a concrete procedure; the system must request that fact
instead of selecting an arbitrary source. Seed-only forms and records without
an approved downloadable file/URL remain unavailable at runtime.

Completion evidence:

- `reports/feature005/step2-data-gap-20260724/completion.json`
- `reports/feature005/step2-data-gap-20260724/manifest.json`
- `reports/feature005/step2-data-gap-20260724/retrieval-gate.json`
- `reports/feature005/step2-data-gap-20260724/post-step2-gaps.json`
- `reports/feature005/step2-data-gap-20260724/runtime-validation.json`
- `reports/feature005/step2-data-gap-20260724/expected-sources.jsonl`

## Feature 005 Step 3 structured generation and claim validation (2026-07-24)

Step 3 is `PASS` with `LEGAL_SECTION_GROUNDING_ENABLED=false`.

The section path now builds a deterministic per-issue coverage matrix before
generation and makes exactly one model invocation for the complete question.
The prompt exposes only the requested facets and issue-bound evidence IDs. The
Pydantic JSON contract rejects unknown fields and unsupported claim types.

Every model claim is validated independently before rendering:

- the evidence belongs to the current request and issue;
- the support quote exists verbatim in the selected evidence;
- document, article, clause and point references agree with metadata;
- material numbers occur in the quote;
- non-numeric deadline and fee claims require matching source language.

Rejected claims are not rendered. Timeout and invalid JSON return only bounded
source excerpts with citations and never trigger a second model repair. Admin
trace records coverage per issue plus a deterministic gate requiring 100%
grounding for displayed legal claims and at least 90% coverage of requested
facets for which evidence is available.

Verification:

| Gate | Result |
|---|---:|
| Focused structured/claim/orchestration tests | 27 passed |
| Feature 005 quality subset | 109 passed |
| Full backend suite | 765 passed |
| Frontend tests | 82 passed |
| Frontend type-check | passed |
| Frontend lint | 0 errors |
| Successful multi-issue model invocations | 1 total |
| Model repair invocations | 0 |
| Displayed claim grounding in acceptance case | 100% |
| Available-facet coverage in acceptance case | 100% |

The quality gate also has negative regressions proving that 89.99% coverage or
any displayed claim without valid evidence is blocked. The nine live role
journeys and latency/concurrency gates remain separate Step 4–5 work and are
not grounds for enabling the feature in Step 3.

## Feature 005 Step 4 live performance result (2026-07-24)

Step 4 is `BLOCKED_EXTERNAL / DECISION_REQUIRED`; the feature remains disabled.
No model, provider, paid service, corpus row or active collection pointer was
changed.

The runtime now measures retrieval, model provisioning, generation, validation
and real HTTP end-to-end time independently. The structured context is capped
at 4,000 characters, output at 256 tokens, duplicate evidence is removed,
retrieval results use a privacy-safe ten-minute cache, and support retrieval is
expanded only once for missing facets. The configured generation deadline
remains 24 seconds. The invocation budget is 23.75 seconds so timeout can return
the existing fail-closed extractive fallback without a repair call.

| Warm gate | Concurrency 1 | Concurrency 5 |
|---|---:|---:|
| Completed requests | 6/6 | 6/6 |
| HTTP request failures | 0 | 0 |
| Retrieval P95 | 52 ms | 897 ms |
| Provisioning P95 | 0 ms | 0 ms |
| Generation P95 | 23,778 ms | 26,816 ms |
| HTTP latency P95 | 27,706 ms | 31,381 ms |
| Repair calls | 0 | 0 |
| Live quality gate | 3 pass / 3 fail | 3 pass / 3 fail |

The latency release thresholds pass at warm concurrency 1 and concurrency 5
has no transport failure. Privacy scanning passes. However, all three
evidence-backed cases exhaust the default DeepSeek invocation budget before a
valid JSON answer is returned. The three no-source cases correctly fail closed.
A single supported diagnostic request still timed out at 23,771 ms after the
4,000-character/256-token reduction. Because fallback coverage for available
facets is then zero, the Step 3 quality gate correctly blocks release.

The remaining dependency is an explicit product decision to approve a
model/provider that satisfies the unchanged 24-second contract, or to approve a
different latency contract. Feature 005 must remain disabled until that
decision is made and the complete live quality and performance gates are
rerun.

Evidence:

- `reports/feature005/step4-performance-20260724/final-warm-c1.json`
- `reports/feature005/step4-performance-20260724/final-warm-c5.json`
- `reports/feature005/step4-performance-20260724/diagnostic-simple-256.json`
- `reports/feature005/step4-performance-20260724/decision.json`

## Step 5 role-aware delivery result (2026-07-24)

The same validated evidence packet now drives simple API, compatibility SSE,
persisted history and the browser renderer. Role presentation changes labels
and operational emphasis only; it cannot introduce a new legal claim.

- Live API role matrix: 9/9 pass, zero critical failures.
- In-app browser role matrix: 9/9 pass across citizen, officer and admin.
- Citizen/officer receive no Admin diagnostics; Admin diagnostics remain
  opt-in. Public sections and SSE source events use allow-listed citation
  metadata only.
- Explicit requested facets are converted into bounded retrieval issues, so an
  unavailable facet is rendered fail-closed instead of disappearing behind a
  sufficient section.
- Jurisdiction and procedure-topic gates prevent foreign-representation-only
  material from supporting domestic procedures and prevent same-domain,
  wrong-procedure passages from supporting a claim.
- Persisted history round-trips `answer_sections` and `forms_unavailable`
  through SurrealDB migration 38.
- Multi-domain officers remain in auto-detection unless their profile maps to
  one unambiguous department/domain.

Verification: backend 791 passed; frontend 86 passed; TypeScript passed; lint
had zero errors; rollback/privacy subset 46 passed. Privacy-safe artifacts are
`reports/feature005/step5-api-summary-final3.json` and
`reports/feature005/step5-browser-summary-final.json`.

The persisted flag remains `LEGAL_SECTION_GROUNDING_ENABLED=false`. Step 5 is
complete, but rollout remains blocked by the independent Step 4
latency/default-model decision.

## Feature 005 Step 6 release gate (2026-07-24)

Overall status is `BLOCKED_EXTERNAL`. `T038`, `T039` and `T042` remain
unchecked, and the persisted feature flag remains
`LEGAL_SECTION_GROUNDING_ENABLED=false`.

The former Step 4 quality failure was fixed in the deterministic extractive
fallback. A facet-relevant sentence that embeds a legal reference conflicting
with the hydrated provision metadata is now skipped. The next relevant
sentence must pass the unchanged `validate_structured_claims` path before it
can be rendered. No second model repair call was added.

Technical rerun:

- backend: 794 passed;
- frontend: 86 passed; TypeScript and production build passed; lint had zero
  errors and 11 non-blocking warnings;
- live API role matrix: 9/9 passed with no critical failure;
- in-app browser flag-on role matrix: 9/9 passed, with no public internal
  marker and no Admin trace leakage to Citizen or Officer;
- warm concurrency 1: retrieval p95 128 ms, generation p95 23,771 ms, HTTP p95
  27,773 ms, quality 6/6;
- warm concurrency 5: 6/6 completed without request failure, quality 6/6;
- cold/warm concurrency 1/5/10/20: 48/48 requests and quality checks passed,
  and every benchmark artifact passed the privacy scanner;
- the standard runtime was restored to
  `legal_chunks_vnlegal_lal_haiphong` with the flag false.

Two external gates still block release:

1. The live flag-off browser journey reached the legacy model path but the
   configured DeepSeek provider returned `402 Insufficient Balance`. The
   project constraints prohibit silently changing the model or adding a paid
   service. The configured provider balance/credential must be restored and
   the flag-off journey plus the complete Step 6 gate rerun.
2. No substantive legal approval exists for the nine Step 6 answers. The DB-3
   receipt approves expected/forbidden source scope only; it does not approve
   the newly generated conclusions, provisions, procedures, deadlines, fees
   and forms. The pending review request is
   `reports/feature005/step6-release-20260724/legal-review-request.json`.

Because these gates are red, role rollout Admin → Officer → Citizen was not
started.

## Serving-corpus integrity remediation (2026-07-24)

The post-audit remediation is technically complete while the feature remains
disabled. It fixes two integrity problems not represented by the original
DB-4 checksum: imported test documents could acquire vectors in a mutable
shadow collection, and hydrated metadata could use a runtime field label or
`unknown` instead of the reviewed DB-2 domain.

The builder is now fail-closed and immutable. Every eligible DB-2 document
must pass every hard gate. Metadata uses the reviewed manifest domain.
Official HTTPS provenance, current effectivity and non-empty content are
revalidated before copy. An existing target is never deleted or overwritten,
and `--resume` rejects any unexpected vector. Step-2 additions are restricted
to the six reviewed official document IDs and eligible chunks. No model
inference or corpus-wide embedding occurs.

New immutable collections:

- `legal_chunks_lechan_primary_v20260724_r2`: 25,003 vectors;
- `legal_chunks_lechan_support_v20260724_r2`: 29,961 vectors.

Both ID checksums match their expected values. Missing, stale, empty, orphan,
duplicate, missing-embedding and unknown-domain counts are zero. ANN/hydration
probes pass for every represented domain. Test chunk IDs `750091` through
`750094` occur in neither collection. The 430,968-vector source collection and
active pointer are unchanged.

The full 167-case live retrieval-only rerun against r2 passed:

| Gate | Result |
|---|---:|
| `FOUND_AND_RETRIEVED` | 164 |
| `VERIFIED_DATA_GAP` | 3 |
| unresolved `FOUND_NOT_RETRIEVED` | 0 |
| Recall@10 | 100% |
| Direct source in top 5 | 100% |
| Wrong-field/expired selection | 0 |
| Coverage | 99.401% |
| Retrieval P95 | 394.033 ms |

A generic domestic-marriage regression was fixed deterministically: marriage
registration routes to Civil Status Law `60/2014/QH13` and Decree
`123/2015/ND-CP`, with domestic Articles 17/18 and foreign-element Articles
35/38 kept distinct.

Release startup now enables an opt-in live model-provider probe. A provider
402 response makes `/ready` return HTTP 503 with
`provider_payment_required`; no raw provider error, credential, token or
prompt is returned. `/health` remains a liveness probe.

Evidence:

- `reports/feature005/db4-serving-20260724-r2/manifest.json`
- `reports/feature005/db5-shadow-20260724-r2/retrieval-167.json`
- `reports/feature005/db5-shadow-20260724-r2/verified-data-gaps.json`
- `tests/test_db4_serving_builder.py`
- `tests/test_section_retrieval_policy.py`
- `tests/test_db5_exact_tiered_retrieval.py`
- `tests/test_readiness.py`

This does not authorize activation. The active runtime still uses the rollback
collection and `LEGAL_SECTION_GROUNDING_ENABLED=false`. Release remains
`BLOCKED_EXTERNAL` until DeepSeek is usable and the separate nine-answer
Step-6 legal review is approved.

## r2 answer and concurrency remediation (2026-07-24)

The r2 API acceptance rerun found one false quality failure in the
`labour_after_land_history` isolation case. The legal answer and citations were
grounded, but the question policy treated an authority-only land question as a
broad explanation and invented condition/exception coverage requirements.
Vietnamese `đ` was also not normalized by the policy fold function.

The deterministic policy now recognizes land-boundary and authority phrases,
normalizes `đ` to `d`, and requests only conclusion plus authority for that
question shape. A regression protects the exact failure. No legal conclusion,
source, corpus row or embedding was changed.

Concurrent provider timeouts also exposed event-loop deadline drift. The safe
extractive answer is now built and validated before model invocation, at most
two DeepSeek invocations run concurrently, and excess requests fail closed
immediately instead of queueing. The 24-second configured deadline and
single-call/no-repair contract remain unchanged.

Final r2 technical evidence:

| Gate | Concurrency 1 | Concurrency 5 |
|---|---:|---:|
| Completed / quality pass | 6/6 | 6/6 |
| Retrieval P95 | 91 ms | 1,485 ms |
| Generation P95 | 23,783 ms | 23,765 ms |
| Validation P95 | 0 ms | 32 ms |
| Internal end-to-end P95 | 23,838 ms | 25,054 ms |
| HTTP P95 | 28,482 ms | 28,765 ms |
| Repair calls | 0 | 0 |

The live r2 API role matrix remains 9/9 with zero critical failures. The full
backend suite is 802 passed; the previously completed frontend gate remains 86
passed with TypeScript/build passing and zero lint errors. New aggregate
artifacts pass the privacy scanner.

Evidence:

- `reports/feature005/r2-performance-20260724/warm-c1-all-fixed.json`
- `reports/feature005/r2-performance-20260724/warm-c5-all-precomputed.json`
- `reports/feature005/r2-role-acceptance-20260724/api-summary.json`

DeepSeek readiness is currently healthy, so the earlier payment dependency is
not an active blocker in this rerun. Release is still not authorized: the
current in-app browser webview could not attach for a fresh r2 browser rerun,
and the separate substantive legal approval for the nine final answers has not
been recorded. The persisted flag and standard runtime must therefore remain
on rollback settings.

## Release remediation rerun (2026-07-27)

This rerun replaces the conflicting technical evidence above for the current
working tree. It does not replace or create a legal-review decision.

### Source-gap and catalog decisions

- SurrealDB migration 39 stores nested candidate metadata, uploaded-file,
  extraction, validity and review-recommendation objects as `FLEXIBLE`.
- Runtime and test credential encryption are isolated. Local launchers load the
  runtime key from `.env` and refuse the known development default.
- Source-gap legal documents and source-gap forms use separate idempotent
  bridges. A form extracted from a multi-procedure official PDF is copied to
  the form candidate area only; it is never inserted into the legal-document
  review queue, canonical catalog, procedure binding or runtime index.
- Page-sliced form extraction preserves both source-file and extracted-file
  checksums and the declared source pages. The foreign-element marriage form
  candidate remains `candidate_pending_review`, `is_approved=false` and
  `runtime_eligible=false`.
- The accidentally routed legacy form record was quarantined rather than
  deleted. The Nghị định 16/2022/NĐ-CP source candidate remains pending human
  review after deterministic OCR of all 94 pages.
- Release form packaging copies only the ten canonical records that pass every
  runtime hard gate. The other 83 canonical records and all pending,
  seed/demo, quarantined, expired, superseded or incomplete records remain
  fail-closed.

### Retrieval and answer evidence

The final 167-case retrieval-only rerun uses exact indexed lookup before ANN,
does not call the answer model and does not use fixture fallback:

| Gate | Current result |
|---|---:|
| Recall@10 | 100% |
| Direct source in top 5 | 100% |
| Available-facet coverage | 98.404% |
| Wrong-field/expired selection | 0 |
| Unresolved `FOUND_NOT_RETRIEVED` | 0 |
| Retrieval P95 | 268.334 ms |

The urban-order exact-law regression now accepts the reviewed
`cu_tru_an_ninh` metadata for Nghị định 167/2013/NĐ-CP only when the normalized
query contains that exact instrument identity. It does not broaden the
`trat_tu_do_thi` domain to unrelated security documents.

The isolated API role matrix is 8/9. The unlicensed-construction case remains
blocked because Nghị định 16 is not legally approved and therefore cannot be
indexed or served. The UI delivery matrix is 9/9 and cross-account identifier
tampering is denied in all 8/8 directions. UI success does not override the
API legal-source failure.

DeepSeek structured generation timed out for all three model-eligible requests
in each cold/warm concurrency 1 and 5 cohort at the configured approximately
24-second boundary. The three evidence-gap requests in each cohort failed
closed without a model call. Repair calls remained zero and every aggregate
artifact passed the privacy scanner. The provider/model result is therefore
`BLOCKED_EXTERNAL`, even though transport-level fallback completed.

### Operational state

- The full backend and frontend gates are rerun from the current working tree.
- The normal local API and frontend are restored after isolated benchmarks.
- The persisted setting is `LEGAL_SECTION_GROUNDING_ENABLED=false`.
- The active serving collection remains
  `legal_chunks_lechan_primary_v20260723`; no corpus-wide embedding or pointer
  change was performed.
- Release status remains `BLOCKED_RELEASE`, rollout stage remains zero, and no
  automated test is treated as legal approval.

Current blockers are the two pending legal-review decisions, repeated
provider/model timeout, the absence of a legally contracted dataset of at least
1,000 distinct questions, and the production-equivalent restore/24-hour pilot
gates. These must be cleared and all gates rerun against one immutable release
manifest before Admin, Officer or Citizen rollout.

## Goal rerun: deterministic latency and facet coverage (2026-07-28)

This section supersedes the provider-timeout result above for the current
working tree. It is technical evidence only and does not create a legal-review
decision.

- The configured model remains `deepseek-v4-pro`. Its official
  OpenAI-compatible adapter is invoked with thinking disabled for the
  structured extraction pass; there is still exactly one model call and no
  repair call.
- Normal and hard questions are classified without a model. Their provider
  budgets remain inside the 30/60-second public SLA.
- Structured context is capped at 4,000 characters and contains only selected
  issue-bound evidence. The JSON output ceiling is 1,024 tokens so up to six
  planned issues are not silently truncated.
- A conclusion/legal-basis facet no longer creates a duplicate issue for a
  documents-only question. Required facets bind to the exact issue before
  broader compatibility rules.
- A documents question triggers the single allowed support-retrieval pass when
  core evidence only describes post-receipt processing and does not contain a
  dossier-composition/submission clause.
- Provider capacity remains two concurrent invocations. Additional requests
  wait only within their original deadline; waiting never renews the budget or
  creates an unbounded queue.
- Structured `source_gap` now exposes only missing public facet names. It
  contains no chunk IDs, request IDs or internal trace markers.
- The search UI keeps a synchronous active-session reference so the first
  submit after “New” cannot be lost during React state propagation.

The current privacy-safe benchmark artifacts are under
`reports/feature005/goal-20260728/`. Cold/warm concurrency 1 and 5 all complete
6/6 requests with 6/6 quality gates, no repair and no provider timeout.
Concurrency-5 P95 end-to-end is 17.6 seconds cold and 16.9 seconds warm.
Three negative cases correctly return an evidence-gap fallback without calling
the model; this is not used to hide a provider failure.

## Goal rerun: exact retrieval and claim-bound source diversity (2026-07-28)

This section supersedes the retrieval-latency and generation figures above for
the current working tree.

- The live retrieval-only dataset contains 1,000 unique exact
  law-and-provision cases, stratified across the six reviewed runtime domains.
  It calls neither the answer model nor a fixture fallback.
- Explicit law-and-article queries use the indexed exact tier without paying
  for a redundant embedding and ANN pass. Natural questions still use ANN;
  query rewriting must not accidentally activate the exact-query shortcut.
- At concurrency 5 the current 1,000-case artifact records Recall@10 and
  direct-source top-5 of 100%, coverage of 100%, zero wrong-field/expired
  selections, zero external errors, P50 919 ms and P95 1,813 ms.
- Cold/warm generation at concurrency 1 and 5 completes every request. The
  current worst P95 is 12,818 ms for generation and 12,904 ms end-to-end.
  The three negative evidence cases remain deliberate fail-closed paths, not
  provider timeouts; repair count remains zero.
- A successful single model call can omit one of several independently
  selected primary legal instruments. Public citations remain claim-bound:
  the renderer never appends an unreferenced citation. For an available
  legal-basis facet, the deterministic extractive path may therefore add at
  most one preflighted rule excerpt per otherwise unrepresented document, up
  to three documents. Every added excerpt passes the same request, issue,
  quote, provision and value validator before rendering. This is not a second
  model call and it does not infer metadata or legal content.
- After a completed Ask response is persisted, the current browser keeps that
  exact local response snapshot and only marks it durable. It does not
  immediately replace it with a separately fetched, privacy-reduced history
  record. This prevents an Admin RAG panel from being remounted and closed
  while it is opened; a later page/session load still reads server history as
  the source of truth.
- Release backup selection ignores a generic inherited
  `LEGAL_DATABASE_URL`, because local test sessions may point it at an empty
  staging database. The backup accepts `LEGAL_CORPUS_DATABASE_URL` as the only
  explicit override, otherwise it uses `LEGAL_RELEASE_DATABASE_URL` before
  the legacy runtime fallback. The current verified backup targets
  `legal_chatbot` (25,429 documents, 232,938 articles and 544,481 chunks) and
  records both the 25,396-vector primary and 29,961-vector support
  collections. A first staging-target backup was quarantined and is not
  restore evidence.

The authoritative artifacts for these measurements are:

- `reports/feature005/goal-20260728/dataset-1000-report-v4-full-c5.json`;
- `reports/feature005/goal-20260728/generation-cold-c1-v6.json`;
- `reports/feature005/goal-20260728/generation-warm-c1-v6.json`;
- `reports/feature005/goal-20260728/generation-cold-c5-v6.json`;
- `reports/feature005/goal-20260728/generation-warm-c5-v6.json`;
- `reports/feature005/goal-20260728/api-role-9-v6-summary.json`;
- `reports/feature005/goal-20260728/ui-role-9-v6-playwright.json`.

## Goal rerun: restore and production-session hardening (2026-07-29)

The one-node pilot now has a reproducible SurrealDB raw-backup verifier in
`scripts/verify_surreal_restore_drill.py`. It copies an already quiesced
RocksDB backup into a new isolated directory, starts the pinned SurrealDB
2.6.5 binary on an isolated port, and compares the complete `INFO FOR DB`
schema plus every table count. The report contains aggregate counts and
checksums only. The current drill passed for 38 tables and 2,667 records; the
isolated listener was stopped afterwards. PostgreSQL and Chroma had already
passed their fresh restore/count/checksum drill.

Release packaging no longer copies a development SurrealDB RocksDB directory.
An existing RocksDB contains its original root identity, so a compose
`SURREAL_PASSWORD` cannot safely replace that credential and the files may
also contain private sessions or audit records. The release compose uses a
fresh named SurrealDB volume; application migrations recreate the schema and
the dedicated `OPEN_NOTEBOOK_ADMIN_PASSWORD` bootstraps the first Admin.
`OPEN_NOTEBOOK_PASSWORD` is not present in the production compose. The
release environment file is excluded from both Git and the Docker build
context.

Production browser authentication uses:

- an `HttpOnly`, `Secure`, `SameSite=Strict` session cookie;
- a session-bound double-submit CSRF token for unsafe HTTP methods;
- server-side session revocation plus cookie deletion on logout;
- bounded one-node authentication rate limits keyed by a one-way
  client/route digest, with no username or password retention; and
- a target-bound, one-use WebSocket ticket with a 60-second lifetime.

The legacy Bearer path remains available for local and non-browser
compatibility, but production browser login does not return or persist the
raw session token. Production WebSocket authentication rejects a session
token in the query string.

These are technical security controls, not legal approval. The current form
campaign still requires authenticated human attestation before runtime
promotion, and the persisted grounding flag remains false.

### Elevated-account MFA and reproducible build gates

Production Officer and Admin password login now requires RFC 6238 TOTP. An
unenrolled elevated account receives only a signed, short-lived setup ticket;
it does not receive a user session. The confirmation flow stores the TOTP
secret encrypted inside the existing flexible user-profile preferences,
rejects replay after enrollment, and issues the same secure cookie session
only after a valid code. Citizen login is unchanged. Setup and confirmation
endpoints use the bounded authentication rate limiter.

The post-attestation gate now invokes the repository `.venv` instead of a
machine-global Python installation. This prevents undeclared host packages
from changing test collection or shutdown behavior. Source and podcast command
modules also defer optional graph/model imports until execution; importing a
lightweight request schema or path helper no longer initializes podcast,
sentence-transformer or multiprocessing stacks.

Docker release builds exclude release data, backups, reports, local build
caches and bundled tools from the build context. Frontend dependency layers
are created before the full backend source copy, so backend-only edits do not
invalidate `npm ci` or the Next.js build cache. The application and retrieval
images build successfully with zero npm-audit findings. Docker Scout did not
return an image CVE database result after repeated bounded attempts, so the
image-CVE sub-gate remains `BLOCKED_EXTERNAL`; it is not counted as PASS.

The current isolated flag-on checks after these changes are API roles 9/9 and
UI roles 9/9. Full backend is 1,165/1,165 and frontend is 98/98, with
type-check, lint and production build green. Campaign
`20260728124624-09e91e70` still waits for one authenticated human attestation
of 43 canonical forms (59 procedure bindings, zero invalid shortlist rows).
Runtime remains 6/99 forms and `LEGAL_SECTION_GROUNDING_ENABLED=false` until
that attestation and the subsequent release gate complete.

### Fail-closed post-attestation orchestration

The post-attestation runner is self-contained and cannot use the persisted
flag-off API as release evidence. API role and generation gates target the
isolated flag-on API on port 5056; UI role gates target the isolated UI on
port 3001. On Windows, the runner starts either isolated runtime through the
checked-in launchers when it is not already ready. The API launcher uses the
repository `.venv`, not a host-global Python installation.

When caller-supplied isolated role tokens are unavailable, the runner creates
exactly one disposable citizen/officer/admin set. The private credential
manifest accepts only allowlisted `FEATURE005_*` keys, is never written into a
shareable report, and is removed after use. Each generated account is marked
with `feature005_test_account=true`; cleanup verifies both that marker and the
`feature005_` identifier prefix before deactivating the account and revoking
its sessions. Partial account creation is rolled back.

The Playwright command selects only the nine flag-on role cases and sets the
flag explicitly in the test process. This prevents the rollback case or nine
runtime skips from producing a false successful exit. If the isolated API,
UI, credential set, benchmark token or cleanup is unavailable, the affected
gate is `BLOCKED`/`FAIL`, never `PASS`.

### Campaign attestation and release-gate handoff

The canonical form-resolution API exposes the contract paths
`form-resolution/current`, `form-resolution/{run_id}` and
`form-resolution/{run_id}/review-shortlist`. The previous
`status/current`, `{run_id}/status` and `{run_id}/shortlist` paths remain as
legacy aliases. The Admin UI uses the canonical paths.

One authenticated campaign attestation now performs the complete handoff:

1. validate the exact current shortlist fingerprint and every submitted item;
2. apply the existing atomic, idempotent catalog transaction;
3. start the isolated post-attestation release gate;
4. mark the current campaign as `ATTESTED_PENDING_RELEASE_GATES`; and
5. bind the terminal `PASS` or `BLOCKED_RELEASE` result back to the same
   attestation ID.

The handoff never creates a legal decision and never turns on the grounding
feature. A missing or mismatched attestation ID makes the handoff gate fail.
Re-running discovery for the same legal date cannot replace an already
attested campaign, including one whose technical release gate is blocked.

Release-data packaging also invokes the repository `.venv` explicitly. It
does not use a machine-global Python interpreter whose dependency set could
change the runtime form package.

### Reproducible container vulnerability gate

Trivy 0.72.0 provides the local, free image-CVE evidence that Docker Scout did
not return. The scanner always includes HIGH and CRITICAL findings with
`detection-priority=precise`; unfixed findings are not hidden.

The release dependency slice now:

- excludes the uninstalled vendored `external/crawl4ai` metadata from the
  image context;
- removes both unused npm trees from the application runtime while preserving
  Node for the standalone frontend;
- pins ChromaDB 1.5.9 in the retrieval image;
- upgrades the affected application Python packages; and
- runs Pillow 12.3 with an exact, reviewed MoviePy 2.2.1 metadata patch. The
  patch changes only MoviePy's Pillow upper bound, updates wheel `RECORD`, is
  idempotent and fails on an unexpected wheel. Image smoke tests perform a
  real resize and MP4 write/read round trip.

The retrieval service uses `chromadb.PersistentClient`, does not import the
Chroma server and exposes no `/api/v2` route. The exact ChromaDB
CVE-2026-45829 product is therefore covered by the reviewed OpenVEX statement
at `deploy/security/chromadb-persistent-client.openvex.json`; this is not a
blanket package suppression.

The current dependency-image scan evidence is:

- application image: 122 unique OS HIGH/CRITICAL findings, zero remediable
  findings and zero unclassified language-package findings;
- retrieval image: 38 unique OS HIGH/CRITICAL findings, zero remediable
  findings and zero unclassified language-package findings after the exact
  Chroma VEX statement; and
- aggregate: 160 unfixed OS findings, including vendor states `affected`,
  `fix_deferred`, `will_not_fix` and `end_of_life`.

`scripts/evaluate_trivy_release_scan.py` turns those reports into one
privacy-safe gate. A remediable HIGH/CRITICAL or any unclassified
language-package finding is `BLOCKED_RELEASE`. Unfixed OS findings are
`DECISION_REQUIRED` until the release owner makes an explicit risk decision;
the evaluator cannot auto-accept them. `PASS` is reserved for a report with
no remaining HIGH/CRITICAL finding. The application must be rebuilt and
rescanned after the final source state is fixed, so these dependency-image
digests are not yet the final release manifest.
