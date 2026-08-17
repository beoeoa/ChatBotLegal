---
description: "Dependency-ordered implementation tasks for Feature 018 production release readiness"
---

# Tasks: Hoàn thiện sản phẩm để phát hành Internet

**Input**: Design documents from `/specs/018-production-release-readiness/`

**Tests**: Mandatory for legal safety, role isolation, migrations, support concurrency, lifecycle, vector serving, release evidence and every user-visible workflow.

## Phase 1: Setup and specification

- [x] T001 Create Feature 018 specification and quality checklist in `specs/018-production-release-readiness/`
- [x] T002 Create implementation plan, research, data model, API contracts and quickstart in `specs/018-production-release-readiness/`
- [x] T003 Point current Spec Kit context to Feature 018 while preserving Feature 017 requirements in `.specify/feature.json` and `AGENTS.md`
- [x] T004 Add a Feature 018 contract validator in `scripts/validate_feature018_contracts.py` and tests in `tests/test_feature018_contracts.py`

## Phase 2: Foundational capability and release evidence

- [x] T005 Write failing registry coverage/role/owner/rollback tests in `tests/test_capability_registry.py`
- [x] T006 Add the machine-readable capability inventory in `config/system-capabilities.json`
- [x] T007 Implement registry loading, validation and route/job reconciliation in `api/capability_registry.py`
- [x] T008 Add the read-only Admin capability endpoint in `api/routers/admin_capabilities.py` and register it in `api/main.py`
- [x] T009 Add the CI/release audit command in `scripts/audit_capability_registry.py`
- [x] T010 Classify legacy routes as redirect/deprecated/disabled and add route guard tests in `frontend/src/lib/auth/route-guard.test.ts`
- [x] T011 Add release evidence types and fingerprint validation in `api/release_evidence.py` with tests in `tests/test_release_evidence.py`
- [x] T012 Add the Feature 018 readiness verifier in `scripts/verify_production_readiness.py` with fail-closed tests in `tests/test_production_readiness.py`
- [x] T013 Document capability ownership and release evidence operation in `docs/7-DEVELOPMENT/feature018-production-release-readiness.md`

**Checkpoint**: Every current route/job is inventoried or explicitly excluded, and missing release evidence cannot produce GO.

## Phase 3: User Story 1 — Grounded, consistent legal answers

- [x] T014 [US1] Write presentation adapter and invariant tests in `tests/test_legal_answer_presentation.py`
- [x] T015 [US1] Implement `LegalAnswerPresentationV1` and legacy projection in `api/legal_answer_presentation.py`
- [x] T016 [US1] Add presentation fields to Ask contracts without breaking existing clients in `api/models.py` and `api/routers/search.py`
- [x] T017 [US1] Prove per-claim validation preserves supported claims and advisory coverage in `tests/test_answer_pipeline_v3.py`
- [x] T018 [US1] Consolidate answer generation/validation into the V3 service boundary in `api/unified_chat_service.py`
- [x] T019 [US1] Add exact-article, procedure-form, general and historical route contract tests in `tests/test_answer_routes_feature018.py`
- [x] T020 [US1] Preserve backend-owned citations/forms and historical labels in `api/legal_structured_answer.py`
- [x] T021 [US1] Implement the single answer card renderer in `frontend/src/components/search/LegalAnswerCard.tsx`
- [x] T022 [US1] Add renderer/status/form/citation tests in `frontend/src/components/search/LegalAnswerCard.test.tsx`
- [x] T023 [US1] Integrate the card into structured and legacy answer paths in `frontend/src/components/search/StreamingResponse.tsx`

**Independent test**: A mixed multi-part question retains all supported claims, exact forms and citations, and renders the same ordered sections for citizen/officer.

## Phase 4: User Story 2 — Long-chat usability

- [x] T024 [US2] Write viewport/new-chat/pagination state tests in `frontend/src/components/search/ChatInterface.test.tsx`
- [x] T025 [US2] Make only the message viewport scroll and keep composer/new-chat visible in `frontend/src/components/search/ChatInterface.tsx`
- [x] T026 [US2] Add “latest message” behavior without forced scroll in `frontend/src/components/search/AskMessageHistory.tsx`
- [x] T027 [US2] Add 30-message cursor pagination contract in `api/routers/conversations.py` and `api/conversation_service.py`
- [x] T028 [US2] Reset domain/form/answer/session state for new chats in `frontend/src/app/(dashboard)/search/page.tsx`
- [x] T029 [US2] Correct citizen/officer labels and support action copy in `frontend/src/app/(dashboard)/search/page.tsx`
- [x] T030 [US2] Add responsive/keyboard Playwright cases in `frontend/e2e/chat-ux-feature018.spec.ts`

**Independent test**: A 100-message conversation is usable at 360/768/1440 px and a new conversation starts clean without scrolling to the page top.

## Phase 5: User Story 3 — Scalable online support

- [x] T031 [US3] Write isolated additive schema and migration refusal tests in `tests/test_feature018_schema_rehearsal.py`
- [x] T032 [US3] Add support/FAQ forward and rehearsal-only down SQL in `scripts/feature018_migrations/`
- [x] T033 [US3] Implement guarded migration/import/shadow runner in `scripts/rehearse_feature018_postgres.py`
- [x] T034 [US3] Write support state/ownership/domain/capacity tests in `tests/test_support_repository.py`
- [x] T035 [US3] Implement PostgreSQL support repository and JSON read-only adapter in `api/support_repository.py`
- [x] T036 [US3] Write concurrent allocation/lease/requeue tests in `tests/test_support_allocator.py`
- [x] T037 [US3] Implement transaction-safe allocator and presence leases in `api/support_allocator.py`
- [x] T038 [US3] Refactor support API to server-derived identity/domain and polling queue status in `api/routers/live_support.py`
- [x] T039 [US3] Replace per-waiting-ticket WebSockets with assigned-only realtime and one officer queue stream in `api/routers/live_support.py`
- [x] T040 [US3] Add citizen My Requests and confirmation modal in `frontend/src/app/(dashboard)/search/page.tsx`
- [x] T041 [US3] Refactor officer queue/capacity/realtime UI in `frontend/src/app/(dashboard)/live-support/page.tsx`
- [x] T042 [US3] Add admin SLA/assignment metadata view with reason-gated content in `frontend/src/app/(dashboard)/admin/support/page.tsx`
- [x] T043 [US3] Add API/auth/realtime tests in `tests/test_support_api.py`, `tests/test_support_authorization.py` and `tests/test_support_realtime.py`
- [x] T044 [US3] Add 1,000-ticket/30-officer load harness in `scripts/load_test_support.py`

**Independent test**: 1,000 waiting users produce no waiting WebSockets; 30 officers receive no duplicate/wrong-domain assignment and never exceed three active sessions.

## Phase 6: User Story 4 — Legal lifecycle, impact and vector state

- [x] T045 [US4] Write lifecycle bucket/event/provision tests in `tests/test_legal_lifecycle_feature018.py`
- [x] T046 [US4] Add additive lifecycle/impact/index schema definitions in `scripts/feature018_migrations/`
- [x] T047 [US4] Implement lifecycle projections and 90/30/7/1 alerts in `api/legal_lifecycle_service.py`
- [x] T048 [US4] Write replacement-different-content and dependency tests in `tests/test_legal_impact_engine.py`
- [x] T049 [US4] Implement reviewable change-event/relationship/provision impact engine in `api/legal_impact_service.py`
- [x] T050 [US4] Add lifecycle/impact/filter/preview endpoints in `api/routers/legal_management.py`
- [x] T051 [US4] Write vector classification/fingerprint tests in `tests/test_vector_serving_manifest.py`
- [x] T052 [US4] Implement read-only active/staging/historical/missing/orphan manifest in `api/vector_serving_manifest.py`
- [x] T053 [US4] Add read-only reconciliation command in `scripts/build_vector_serving_manifest.py`
- [x] T054 [US4] Add lifecycle actions and filtered lists in `frontend/src/app/(dashboard)/legal-management/page.tsx`
- [x] T055 [US4] Add document timeline/impact/index readiness view in `frontend/src/app/(dashboard)/legal-documents/[id]/page.tsx`
- [x] T056 [US4] Add the end-to-end expiry→replacement→impact→index→chat regression in `tests/test_legal_change_journey.py`

**Independent test**: An expired document remains historically visible, is excluded from current answers, and a different replacement creates review tasks without copying old content or requiring full re-index.

## Phase 7: User Story 5 — Procedure, form and FAQ governance UX

- [x] T057 [US5] Write explicit-step/procedure-picker/upload contract tests in `tests/test_feature018_form_workflow.py`
- [x] T058 [US5] Extend Feature 017 API with searchable procedure candidates and source proposal metadata in `api/routers/procedure_forms_catalog.py`
- [x] T059 [US5] Replace generic approve actions with workflow-specific actions/stepper in `frontend/src/components/legal-import/FormGovernancePanel.tsx`
- [x] T060 [US5] Add name/code/domain procedure picker and URL/PDF/DOCX/e-form input in `frontend/src/components/legal-import/FormGovernancePanel.tsx`
- [x] T061 [US5] Write FAQ revision/release/form-derivation tests in `tests/test_faq_governance.py`
- [x] T062 [US5] Implement PostgreSQL FAQ repository/release adapter in `api/faq_governance_service.py`
- [x] T063 [US5] Refactor FAQ API to confirmed procedure and active form release in `api/routers/faq.py`
- [x] T064 [US5] Replace “Nháp” and manual form-ID UI with public-state/procedure workflow in `frontend/src/app/(dashboard)/faq-management/page.tsx`
- [x] T065 [US5] Run Feature 017 1,000-case router/form regressions and ensure active release compatibility

**Independent test**: An admin can finish a form/FAQ flow without knowing IDs; source approval is visibly non-public; citizen only receives released, checksum-valid forms for the confirmed procedure.

## Phase 8: User Story 6 — No-tech dashboard and auditable operations

- [x] T066 [US6] Write alert-priority/copy/action/filter tests in `tests/test_admin_operations_feature018.py`
- [x] T067 [US6] Implement normalized operational alert projection in `api/admin_operations_service.py`
- [x] T068 [US6] Rebuild `/admin` information hierarchy in `frontend/src/app/(dashboard)/admin/page.tsx`
- [x] T069 [US6] Add filtered Activity Center API in `api/routers/admin_activity.py`
- [x] T070 [US6] Add role/module/result/time filters and export in `frontend/src/app/(dashboard)/admin/activity/page.tsx`
- [x] T071 [US6] Add reason-required sensitive activity access and audit tests in `tests/test_activity_center.py`
- [x] T072 [US6] Consolidate old dashboard/quality routes into redirects or disabled states in frontend route guards
- [x] T073 [US6] Add guided cloud/local provider setup and connection tests in `frontend/src/app/(dashboard)/settings/api-keys/page.tsx`

**Independent test**: A no-tech admin identifies the highest-impact issue, understands it, opens the filtered worklist and completes the next action without reading internal IDs.

## Phase 9: User Story 7 — Security, deployment and production gates

- [x] T074 [US7] Complete server ACL/session revoke/MFA/rate-limit negative tests in `tests/test_release_security.py`
- [x] T075 [US7] Add upload validation/malware/path/checksum gates in `api/upload_security.py`
- [x] T076 [US7] Add hash-chain audit checkpoint and verification in `api/legal_audit_chain.py`
- [x] T077 [US7] Add content/attachment/audit retention jobs and tests in `tests/test_retention_jobs_feature018.py`
- [x] T078 [US7] Split frontend/API production services and health/readiness checks in `docker-compose.release.yml` and `deploy/`
- [x] T079 [US7] Add Caddy TLS/rate/security headers configuration in `deploy/caddy/`
- [x] T080 [US7] Add content-free metrics/logging dashboards and alerts in `deploy/monitoring/`
- [x] T081 [US7] Add PostgreSQL/object/vector/Surreal backup and restore runbooks/scripts in `deploy/runbooks/`
- [x] T082 [US7] Run and record restore rehearsal with release/fingerprint reconciliation
- [x] T083 [US7] Complete resumable 1,000-case live DeepSeek evaluation and 25-second SLA evidence for T086
- [x] T084 [US7] Run 100 regression, 294 Golden and Golden V3 1,000 full-answer gates
- [ ] T085 [US7] Run approved browser UAT 20 citizen + 20 officer and admin journeys for T091
- [x] T086 [US7] Run 1,000-user/100-ask/30-officer load, security and secret scans
- [x] T087 [US7] Run frontend type-check/lint/test/build and backend unit/contract/integration suites
- [x] T088 [US7] Generate the signed/fingerprinted Go/No-Go report in `reports/feature018/go-no-go.json`
- [ ] T089 [US7] Deploy staging and canary citizen 10/50/100, then officer/admin, with rollback smoke and 72-hour observation

**Independent test**: The candidate release reaches GO only with matching legal, functional, load, security, restore and UAT evidence; a failed gate blocks activation and rollback preserves safety/audit.

## Final Phase: Documentation and completion audit

- [x] T090 Update operator/user runbooks and architecture decisions under `docs/7-DEVELOPMENT/` and `deploy/runbooks/`
- [x] T091 Reconcile every Feature 018 requirement and task with authoritative evidence in `reports/feature018/completion-audit.json`
- [ ] T092 Verify no P0/P1, route ghost, default password, draft leakage, failed build or stale release fingerprint remains
- [x] T093 Mark production-ready only after all tasks and Go gates pass; otherwise preserve active goal and report exact blockers

## Phase 10: M2 — Immutable serving-manifest boundary

- [x] T094 Define and validate `legal-serving-manifest-v2` with version, dataset, visibility, provenance, procedure and chunk fields
- [x] T095 Build the immutable baseline M2 manifest and checksum-bound atomic active pointer
- [x] T096 Bind core and expanded vector retrieval to the manifest collection and chunk set
- [x] T097 Enforce manifest document/chunk scope in exact, lexical, hydration, parent, neighbor and fallback paths
- [x] T098 Defensively filter reranker inputs, relationships and citation/document/PDF lookup by the manifest
- [x] T099 Derive audience from backend authentication and isolate search/document caches by audience and manifest version
- [x] T100 Require the serving manifest in local/release retrieval startup and expose its attestation in health
- [x] T101 Add mutation, visibility, fail-closed, retrieval-order, release and builder tests
- [x] T102 Produce exact PostgreSQL/Chroma/runtime acceptance evidence and document the M2 operating contract

**Independent test**: The acceptance audit proves the manifest, active PostgreSQL rows and active Chroma collection contain exactly 7,245 documents and 168,155 chunks/vectors; all M2 gates and live manifest-bound health pass without changing the M1 active collection.

## Phase 11: M3 — Candidate retrieval baseline and full stage trace

- [x] T103 Define the machine-readable M3 trace schema/validator and failing contract tests
- [x] T104 Instrument raw/normalized/classification, vector, lexical, fusion, reranker, expansion, final evidence and stage latency without changing ranking
- [x] T105 Add an isolated immutable candidate-3000 trace runner with frozen baseline configuration
- [x] T106 Run and independently audit three complete traces against the 3,000-document/88,209-vector candidate while preserving the active baseline pointer
- [x] T107 Link the original 1,000-query candidate baseline, publish checksums and document M3 operation/rollback

**Independent test**: Every captured candidate query validates against `legal-retrieval-trace-m3-v1`, has non-empty vector/lexical/fusion/reranker/expanded/final stages with all latency stages, and the live service remains healthy on the unchanged M2 baseline.

## Phase 12: M4 — Query normalization and structured classification

- [x] T108 Define `legal-query-classification-m4-v1` and failing contract/intent/temporal tests
- [x] T109 Implement deterministic Unicode normalization and structured domain/intent/scope/answer-type classification
- [x] T110 Cover all 16 required primary intents while retaining additional facets as secondary intents
- [x] T111 Enforce current/historical/unknown before exact, vector and lexical retrieval, including conflict and invalid-date refusal
- [x] T112 Propagate explicit batch `as_of`, expose classification in response/M3 trace, publish audit/checksums and document rollback

**Independent test**: All 16 intents validate against the M4 schema; exact historical dates become the effective retrieval date, while year-only, ambiguous, invalid, future and conflicting temporal requests return no evidence before embedding/SQL. The M2 active collection pointer remains unchanged.

## Phase 13: M5 — Manifest-bound hybrid retrieval experiments

- [x] T113 Define the M5 summary schema, parameterized fusion contract and failing unit tests
- [x] T114 Implement backward-compatible RRF and weighted fusion without mixing incompatible raw score scales
- [x] T115 Add a resumable checksum-bound runner that freezes Golden/model/reranker/expansion and preserves the active pointer
- [x] T116 Run vector Top-K `10/20/30/50` and lexical Top-K `10/20/30/50`, changing only one variable per experiment
- [x] T117 Run RRF and weighted `0.7/0.3`, `0.6/0.4`, `0.5/0.5` on the selected parent configuration
- [x] T118 Independently audit all 13 full Golden-1,000 runs, manifest containment, checksums, winner selection and rollback posture

**Independent test**: `m5_acceptance_report.json` passes all gates for exactly 13 experiments; every run has 1,000 cases, zero execution errors and zero documents outside the candidate manifest. The active pointer remains on the M2 baseline and M6 is not started.

## Phase 14: M6 — Reranker and isolated evidence expansion

- [x] T119 Define the M6 experiment schema and failing tests for rerank Top-N, parent/neighbor toggles and evidence safety
- [x] T120 Add request-scoped `rerank_top_n` 20/30/50/100 while preserving the M5 fusion parent and exact-article ordering
- [x] T121 Add independently measurable parent and neighbor expansion controls constrained by the serving manifest
- [x] T122 Add invalid/expired and outside-manifest evidence counters to the frozen Golden benchmark contract
- [x] T123 Prepare a fingerprinted local-only reranker artifact and a resumable M6 runner without enabling it in live serving
- [ ] T124 Run the control, four rerank Top-N and isolated parent/neighbor experiments on the approved 100-case M6 hard-negative set; retain Golden 1,000 as exact-article bypass safety evidence
- [ ] T125 Audit recall gain versus latency, all validity/scope gates, checksums, pointer preservation and rollback posture
- [x] T126 Publish M6 smoke evidence and select No-Go without activating candidate or starting the next stage

**Independent test**: Every M6 run uses the M5 quality parent (`vector=20`, `lexical=20`, `legacy_stack`), changes only its declared variable, has zero errors, zero expired/invalid evidence and zero documents outside the candidate manifest. The active M2 pointer remains unchanged.

## Phase 15: M6.1 — Local reranker model comparison

- [x] T127 Define failing tests and a frozen M6.1 schema for control, BGE Top-N 10, GTE Top-N 10 and conditional GTE Top-N 20
- [x] T128 Download `Alibaba-NLP/gte-multilingual-reranker-base` to a separate pinned local snapshot and publish per-file SHA-256 without changing the BGE snapshot
- [x] T129 Implement a resumable model-selectable runner with `max_length=512`, batch 8, warm-up, separate cold/warm timings and unchanged M5 retrieval parent
- [x] T130 Run control, BGE Top-N 10 and GTE Top-N 10 on the approved Golden-100 hard-negative set; run GTE Top-N 20 only when its latency precondition passes
- [x] T131 Audit safety, Recall@10, MRR, Top-5, p95, OOM/errors, checksums and active-pointer preservation
- [x] T132 Publish the M6.1 Go/No-Go decision without activating a model or deleting either local snapshot

**Independent test**: Every model experiment uses candidate 3,000, vector/lexical Top-K 20, `legacy_stack`, final evidence 10, `max_length=512`, batch 8 and the same host. A winner requires zero safety errors, p95 retrieval plus reranking at most 15 seconds, non-decreasing Recall@10 and either relative MRR gain at least 5% or Top-5 gain at least one percentage point. The active M2 pointer and live reranker configuration remain unchanged.

## Phase 16: M5/M6 quality remediation — metric, temporal and source gaps

- [x] T133 Define retrieval metric v2 and source-gap contracts with denominator, per-domain, multi-issue and non-mutation tests
- [x] T134 Correct date-scoped effectivity classification while retaining fail-closed absolute-current conflicts
- [x] T135 Add per-case `legal_as_of`, refusal-aware Recall/MRR and multi-issue coverage to the candidate benchmark
- [x] T136 Build the checksum-bound Golden-1,000 + Hard-negative-100 source-gap manifest and enrich it from PostgreSQL read-only inventory
- [x] T137 Reconcile all 12,236 inventory documents and obtain legal review for every unresolved source/effectivity/article gap before building candidate-vNext in a separate staging collection
- [ ] T138 Run and pass source/exact/vector/lexical/fusion candidate-stage diagnostics before learned reranking, with current/history/quarantine filters
- [ ] T139 Run the corrected control on Golden-1,000 and Hard-negative-500 and require Recall@10/MRR/per-domain/safety/latency gates
- [ ] T140 Run BGE/GTE Top-N and isolated parent/neighbor experiments only after the candidate Recall@50 gate passes
- [ ] T141 Freeze the complete 2,000-case suite including sealed Production holdout-500, run final independent acceptance and request separate pointer activation approval
- [ ] T142 Publish final operation/evidence documentation and close only the tasks supported by immutable artifacts

**Independent test**: Golden-1,000 and Hard-negative-500 are scored separately under `legal-retrieval-metrics-v2`; expected refusals are not source-recall misses, answer-required temporal blocks remain misses, each domain reaches Recall@10 ≥95%, overall MRR@10 ≥0.90, safety counts are zero and the active pointer is unchanged. Hard-negative-100 remains legacy diagnostic evidence only.

## Phase 17: Vector Integrity V3 — chunk release, provenance and clean index

- [x] T143 Write failing Manifest V3, provenance separation, immutable chunk-release and quality fail-closed tests
- [x] T144 Define `legal-serving-manifest-v3` and extend the Feature 018 contract validator without weakening Manifest V2 rollback compatibility
- [x] T145 Add isolated additive `legal_chunk_release`, `legal_chunk_revision` and `legal_metadata_review_case` migration definitions plus guarded rehearsal tests
- [x] T146 Implement typed immutable release/revision/review models, canonical hashing and count/inventory invariants
- [x] T147 Write and implement Quality Policy V2 assessment so every one of the 12,236 inventory documents is exactly one of `current_retrievable`, `historical_only`, `future_effective` or `quarantined`, with zero fail-open assessments
- [x] T148 Write OCR/Unicode/table/appendix/token-budget tests for the canonical structural splitter
- [x] T149 Implement additive chunking V2 with full-passage 512-token limit, 64-token intra-child overlap and source-offset provenance
- [ ] T150 Build the official-metadata review queue and apply only separately attested corrections with before/after audit
- [x] T151 Write builder tests that reject copied legacy vectors, mixed fingerprints, silent truncation, orphan IDs and unsafe resume
- [x] T152 Implement resumable clean VNLegal-LAL staging build with fresh vectors, exact ID/count checks and deterministic vector-content fingerprint
- [x] T153 Bind runtime hydration and ANN metadata filters to Manifest V3 while retaining Manifest V2 rollback and exact/lexical degraded fallback
- [x] T154 Route future imports to staging chunk releases/index jobs and disable direct active-collection mutation by default
- [ ] T155 Build the full 12,236-document inventory release (current and temporal collections) and shadow collection without changing the M2 active pointer
  - [x] T155A Implement checksum-bound Kaggle input export, private worker/orchestrator, output verifier and isolated shadow importer without pointer mutation
  - [x] T155R Audit all 4,994 quarantined records and their stored official URLs in a read-only evidence artifact; no legal state, vector collection or active pointer was changed
  - [ ] T155B Run the private Kaggle GPU job, verify the full approved V2 output and import current/temporal shadows after credentials and legal-data gates pass
- [ ] T156 Run vector integrity, 500-passage replay/parity, restore and pointer-preservation acceptance
- [ ] T157 Run Golden-1,000, Hard-negative-500, sealed Production holdout-500, 294 exact and final acceptance; require per-domain Recall@10 ≥95%, MRR@10 ≥0.90 and zero safety errors
- [ ] T158 Rehearse atomic activation/rollback and request separate live migration plus pointer-switch approval
- [ ] T159 After approval, run citizen 10/50/100 canary, officer/admin rollout and 72-hour observation with automatic rollback thresholds
- [ ] T160 Publish immutable evidence, operation/rollback documentation and close only tasks supported by matching release fingerprints

**Independent test**: Inventory totals exactly 12,236 documents; every document has exactly one assessed current/history/quarantine state; the staging Chroma collections contain only eligible V2 chunks freshly embedded under one provenance tuple; Manifest V3 counts/checksums match PostgreSQL and Chroma; Golden-1,000, Hard-negative-500 and sealed Holdout-500 pass all retrieval/safety/latency gates while the M2 pointer remains unchanged until separately approved activation.

## Phase 18: Stage E — Retrieval Golden 2,000

- [x] T161 Write fail-closed tests for per-case checksum, normalized-question duplicates, sealed-holdout leakage, single-run consumption and aggregate-only receipts
- [x] T162 Define strict `production-holdout-envelope-v1` and `production-holdout-aggregate-receipt-v1` contracts and register them in Feature 018 validation
- [x] T163 Implement read-only envelope/receipt validation bound to dataset, source snapshot, approved manifest, quota attestation and cross-split leakage audit fingerprints
- [x] T164 Separate configuration selection from holdout custody: development runner accepts only 1,500 visible cases; final runner rejects custody bundles inside the repository and emits no per-case result
- [ ] T165 Reconcile and re-approve all 1,000 Golden cases under `retrieval-eval-suite-v1`, including official URL, legal validity, issue/source groups, reviewer evidence and `case_sha256`
- [ ] T166 Author, independently review and freeze Hard-negative-500 at exactly 100 cases per domain without deriving unverifiable legal labels
- [ ] T167 Have independent Legal QA author Holdout-500, complete quota/source/leakage attestations and publish only the sealed public envelope
- [ ] T168 Freeze the M5/M6 candidate, let the custodian run Holdout exactly once and reject any receipt containing case IDs, questions, expected sources or miss rows
- [ ] T169 Run final 2,000-case acceptance, require Recall@10 ≥95%, MRR@10 ≥0.90, correct refusal ≥99%, zero safety errors and a new holdout version after any failed candidate
- [x] T170 Add isolated pinned Ragas/DeepEval dependencies and prove both frameworks use only the local Ollama endpoint
- [x] T171 Write fail-closed generation-plan tests for exact 2,000 split/domain/scenario quotas, provenance and `pending_final_review` state
- [x] T172 Implement deterministic seed conversion from approved Golden/source-manifest evidence without inventing URLs, validity or article identities
- [x] T173 Implement resumable Ragas scenario generation over canonical V2 passages and retain framework/model/prompt fingerprints
- [ ] T174 Implement DeepEval context-bound evolution with `include_expected_output=false`, local Ollama only and no automatic reviewer approval
- [x] T175 Add normalized/semantic duplicate and cross-split leakage rejection plus quota-preserving replacement selection
- [x] T176 Generate the 1,500 visible development candidate pack and final-review workbook without touching corpus, vectors or active pointer
- [ ] T177 Generate the 500-case holdout candidate bundle outside the repository, publish only its checksum envelope and do not inspect case-level output in implementation reports
- [ ] T178 Import the owner's final decisions into immutable approved suite/holdout artifacts and run Gate E

**Independent test**: The workspace contains exactly 1,500 reviewable development cases and no holdout content. The public envelope attests exactly 500 hidden cases with 100 per domain and matching source/manifest/quota/leakage hashes. The final receipt has only aggregate metrics, binds one frozen candidate, is non-reusable after any run and leaves PostgreSQL, Chroma and the active pointer unchanged.

## Dependencies and execution order

- Phase 2 capability/evidence foundation blocks production GO but does not block isolated UI/backend slices.
- User Stories 1 and 2 can proceed after T004 and are independently testable.
- Support schema/repository (T031–T037) blocks support API/UI and load work.
- Lifecycle event/impact models block lifecycle UI and end-to-end legal change journey.
- Feature 017 active release remains canonical; Feature 018 must not duplicate form truth.
- Security, backup, restore and evidence verifier must pass before any live migration/pointer/canary action.
- Browser UAT starts only after code/API/build gates and the approved task slice.
- Canary order is citizen → officer → admin; each step requires observation and rollback readiness.

## Parallel opportunities

- After Phase 2, US1 answer presentation and US2 chat UX may proceed in parallel because they touch separate service/component boundaries.
- Support migration/repository and lifecycle read-only manifest tests may proceed in parallel on different files, but live state changes remain gated.
- Dashboard UI may begin from contract fixtures while backend projections are implemented, then converge through contract tests.
- Deployment/observability files may proceed alongside isolated functional tests once capability ownership and privacy contracts are fixed.

## MVP implementation strategy

1. Complete T004–T013 (registry + fail-closed release evidence).
2. Complete US1 and US2 to immediately improve answer consistency and chat usability without data migration.
3. Rehearse support/FAQ schema in isolation, then implement scalable support.
4. Add lifecycle/impact/vector read-only visibility before any indexing mutation.
5. Converge governance/admin UX and only then run full production gates.
