---
description: "Dependency-ordered implementation tasks for Feature 017"
---

# Tasks: Quản trị thủ tục, biểu mẫu và Golden V3

**Input**: Design documents from `/specs/017-procedure-form-governance/`

**Tests**: Mandatory for state transitions, authority/effectivity, role isolation, attestation, release pointer, deterministic routing, migration and Golden ground truth.

## Phase 1: Setup and contracts

- [x] T001 Create Feature 017 spec, requirements checklist and active feature pointer in `specs/017-procedure-form-governance/` and `.specify/feature.json`
- [x] T002 Create plan, research, data model, quickstart and contracts in `specs/017-procedure-form-governance/`
- [x] T003 Add contract validator in `scripts/validate_feature017_contracts.py` and tests in `tests/test_feature017_contracts.py`
- [x] T004 Update current Spec Kit context in `AGENTS.md` without removing prior lifecycle baselines

## Phase 2: Foundational schema and domain core

- [x] T005 Write failing additive-schema/rehearsal tests in `tests/test_feature017_schema_rehearsal.py`
- [x] T006 Add PostgreSQL forward/down definitions in `scripts/feature017_migrations/`
- [x] T007 Implement guarded plan/up/verify/down runner in `scripts/manage_feature017_form_schema.py`
- [x] T008 Write failing state/hash/model tests in `tests/test_feature017_workflow.py`
- [x] T009 Implement enums, data models, transition matrix, canonical hashing and attestation fingerprint in `api/form_governance_models.py`
- [x] T010 Implement repository protocol and in-memory repository in `api/form_governance_repository.py`
- [x] T011 Implement lazy PostgreSQL repository and explicit JSON compatibility reader in `api/form_governance_repository.py`

**Checkpoint**: Schema is rehearsal-safe and the pure domain core cannot perform invalid transitions.

## Phase 3: User Story 1 — Officer proposal/supplement

- [x] T012 [US1] Write officer ownership/domain/version tests in `tests/test_feature017_workflow.py`
- [x] T013 [US1] Implement submit, list mine, detail, supplement and withdraw in `api/form_governance_service.py`
- [x] T014 [US1] Write API contract/ID-tampering tests in `tests/test_feature017_api.py`
- [x] T015 [US1] Add officer endpoints and server-derived actor scope in `api/routers/procedure_forms_catalog.py`
- [x] T016 [US1] Register router in `api/main.py`

## Phase 4: User Story 2 — Admin review/enrichment/attestation

- [x] T017 [US2] Write source-approval/not-runtime and stale/tampered attestation tests in `tests/test_feature017_workflow.py`
- [x] T018 [US2] Implement request supplement, source approve/reject and legal enrichment in `api/form_governance_service.py`
- [x] T019 [US2] Implement attestation preview and exact-fingerprint confirmation in `api/form_governance_service.py`
- [x] T020 [US2] Add Admin review/enrichment/attestation endpoints in `api/routers/procedure_forms_catalog.py`
- [x] T021 [US2] Preserve legacy candidate adapters and prove approval remains non-public in `tests/test_review_workflow.py`

## Phase 5: User Story 3 — Release and sync

- [x] T022 [US3] Write immutable manifest, failed-gate pointer and rollback tests in `tests/test_feature017_release.py`
- [x] T023 [US3] Implement manifest builder, deterministic gate and coverage summary in `api/form_governance_service.py`
- [x] T024 [US3] Implement transactional activation/rollback contract in repositories
- [x] T025 [US3] Add release preview/validate/activate/rollback/active endpoints
- [x] T026 [US3] Implement notification outbox projection boundary without adding a worker
- [x] T027 [US3] Verify Surreal projection failure does not change legal state in `tests/test_feature017_release.py`

## Phase 6: User Story 4 — Deterministic Form Router

- [x] T028 [US4] Write exact/ambiguous/conditional/e-form/gap/expired/role tests in `tests/test_feature017_form_router.py`
- [x] T029 [US4] Implement exact and lexical procedure candidate resolver in `api/form_router_v3.py`
- [x] T030 [US4] Implement active-release SQL binding selection and all hard gates in `api/form_router_v3.py`
- [x] T031 [US4] Produce Form Evidence Packet and public `recommended_forms` projection
- [x] T032 [US4] Add shadow/active compatibility hook to `api/routers/search.py` without changing legacy defaults
- [x] T033 [US4] Prove provider output cannot add/change form IDs or URLs
- [x] T034 [US4] Run existing form catalog/citation regressions

## Phase 7: User Story 5 — Coverage campaign

- [x] T035 [US5] Write 191/131/229 dimension and dedupe tests in `tests/test_feature017_coverage.py`
- [x] T036 [US5] Implement compatibility coverage builder in `scripts/build_feature017_coverage.py`
- [x] T037 [US5] Implement coverage endpoint and verified-gap decisions in service/router
- [x] T038 [US5] Emit unresolved identity review queue without auto-attestation/release
- [x] T039 [US5] Record baseline/source snapshot differences instead of hard-coding refreshed counts

## Phase 8: User Story 6 — Golden V3

- [x] T040 [US6] Write schema/distribution/coverage/freeze tests in `tests/test_feature017_golden_v3.py`
- [x] T041 [US6] Implement contract validator in `scripts/validate_feature017_contracts.py`
- [x] T042 [US6] Implement deterministic 1,000-case builder in `scripts/build_feature017_golden_v3.py`
- [x] T043 [US6] Export JSON/Excel/review manifest and block approved checksum before user review
- [x] T044 [US6] Verify model paraphrases cannot mutate manifest-derived truth
- [x] T045 [US6] Generate review queue/report from current compatibility release; do not claim final Golden while coverage incomplete

## Phase 9: Admin/Officer UI

- [x] T046 [P] Extend Form Workflow/Coverage/Release types and API methods in `frontend/src/lib/api/legal-import.ts`
- [x] T047 Write failing component tests in `frontend/src/components/legal-import/FormGovernancePanel.test.tsx`
- [x] T048 Implement three-action queue and two-step legal wizard in `frontend/src/components/legal-import/FormGovernancePanel.tsx`
- [x] T049 Integrate panel into `frontend/src/app/(dashboard)/legal-import/page.tsx`
- [x] T050 Verify no-tech copy explains source approval, attestation, release and conditional/e-form outcomes

## Phase 10: Verification and documentation

- [x] T051 Run Feature 017 backend tests and existing form regressions
- [x] T052 Run frontend tests, lint and production build (177 tests, lint and production build pass)
- [x] T053 Add isolated 1,000-resolution procedure/form benchmark and verify the 3-second retrieval gate
- [x] T054 Scan public/API artifacts for credentials, PII, reviewer notes, local paths and audit hashes
- [x] T055 Update `docs/7-DEVELOPMENT/feature017-procedure-form-governance.md`
- [x] T056 Record completed/non-completed tasks, current coverage blocker and activation prerequisites
- [x] T057 Stop and request separate approval before live migration, official source decisions, active release, browser UAT or production activation

## Phase 11: Completion audit remediation

- [x] T058 Apply the explicit `executing_level=commune` scope and reconcile 418 source rows to 191 procedures, 131 identities, 229 bindings, 31 released and 100 pending
- [x] T059 Version normalized PostgreSQL catalog rows by release and materialize them atomically with the active pointer
- [x] T060 Add Admin release preview, deterministic Release Gate and explicit publish controls without auto-publication
- [x] T061 Add Officer form proposal/supplement/status UI and synchronize released status through the notification outbox
- [x] T062 Produce the read-only 100-identity human review workbook with formulas, validation and source URLs
- [x] T063 Align the five officer seed accounts with the five canonical Feature 017 domains without mutating live accounts
- [x] T064 Carry evidence-backed verified gaps into release manifests and keep them non-downloadable/fail-closed
- [x] T065 Normalize PostgreSQL-native dates at the release boundary and prove the complete workflow with a disposable isolated-database rehearsal

## Dependencies and execution order

- Phase 2 blocks all runtime work.
- Officer/Admin stories share service/repository foundation but remain independently testable.
- Release activation requires attested cases; Form Router requires a validated active release fixture.
- Coverage must be 100% before final Golden V3 build/freeze.
- UI can begin after API contracts but activation/UI browser UAT remains separately gated.
- Tests are written before each risky implementation slice and must fail for the intended missing behavior.

## Explicitly deferred external actions

- Applying SQL to live `legal_chatbot` PostgreSQL.
- Converting current JSON records into legally attested/released rows.
- Deciding the 100 unresolved identities or refreshing official sources as if human-reviewed.
- Switching `FORM_GOVERNANCE_SOURCE` to `postgres_active`.
- Freezing an approved Golden V3 checksum without workbook approval.
- Direct browser UAT or production activation.

## Phase 12: Approved source campaign and completion gates

- [x] T066 Close the completion-audit authorization, procedure-scope, coverage-dedupe, release-identity and typed-contract gaps with focused regression tests
- [x] T067 Route Feature 017 procedure/form decisions into the Ask Evidence Packet even when the legacy resolver has no `procedure_id`; preserve clarification and fail-closed behavior
- [x] T068 Record the user's review of all 100 identities as 73 source approvals and 27 supplement requests without attestation, release or live-database mutation
- [x] T069 Implement and run the candidate-only official DVC collector; refresh all 123 related procedure details as of 2026-08-11, verify official attachments/checksums and retain e-form metadata checksums
- [x] T070 Close the 100-row source campaign according to the owner's revised scope: retain 60 checksum-bound identities for legal enrichment; fail-close and defer 27 supplement rows plus 13 instrument-package rows without calling them verified gaps. The isolated scope manifests make all 40 deferred identities ineligible for public routing and positive Golden cases. Exact normalization of VBPL's `Số:` label plus bounded pagination recovered both `154/2024/NĐ-CP` forms; separately refreshed status evidence and a checksum-pinned page binding recovered Hải Phòng form 21Đ from `52/2026/QĐ-UBND`. The 60 retained identities now cover 99 procedures and 119 required bindings, and all 60 pass the tamper-evident attestation-preview gate. No attestation or release was created.
- [x] T071 Build a complete isolated PostgreSQL release candidate from the resolved 191/131/229 scope and pass the full deterministic Release Gate without activating the public pointer. Evidence: packaged manifest `a54a014dcc6eaec4834fa8fc0234919f5a8e3fcbdf98fccc66d8b30888473f96`; 91/91 remote/runtime checksum checks passed; isolated PostgreSQL contains one `validated` release and zero active pointers/catalog projections.
- [x] T072 Regenerate Golden V3 1,000 cases from that complete candidate, export the review workbook and run full-answer/API/performance evaluation. Evidence: deterministic router and public API contract both passed 1,000/1,000; exact form set, clarification, forbidden-form and provider-identity gates are 100%; direct P95 54.2 ms and API P95 56.0 ms; workbook remains `proposed` for user review and has no approved checksum.
- [x] T073 Run final backend/frontend/schema rehearsal and documentation audit, then stop for separate live migration, browser UAT and public activation approval. Current evidence: 151 Feature 017/source/form tests, 24 schema/workflow/release tests, 177 frontend tests, frontend lint and production build all pass; contract validation passes; migration remains plan-only; the live `legal_chatbot` database has no `form_*` tables or active form release; Golden V3 correctly rejects the unattested scoped candidate.

## Phase 13: User approval and Golden V3 freeze

- [x] T074 Record the owner's explicit approval of all 1,000 Golden V3 cases and freeze a deterministic approved checksum without changing any ground-truth field.
- [x] T075 Export a separate approved workbook, preserve the proposed workbook for audit, and verify 1,000 approved rows, summary formulas and zero formula errors.
- [x] T076 Re-run Golden V3 API/router evaluation and completion audit against the approved dataset. Live PostgreSQL migration, active-pointer activation, browser UAT and public rollout remain unapproved and unchanged.

## Phase 14: Owner-approved local-live activation and UAT

- [x] T077 Create and verify a recoverable pre-mutation backup of live PostgreSQL and SurrealDB; record the PostgreSQL dump checksum and leave vectors unchanged.
- [x] T078 Apply the additive Feature 017 migration to the approved local-live `legal_chatbot` PostgreSQL database with the guarded deployment command.
- [x] T079 Import the exact approved release candidate, pass the 1,000-case shadow router/API gate and atomically activate manifest `a54a014dcc6eaec4834fa8fc0234919f5a8e3fcbdf98fccc66d8b30888473f96`.
- [x] T080 Switch the runtime catalog to `postgres_active`, materialize 191 procedures, 91 assets, 141 bindings and 382 aliases, and re-verify the active pointer without modifying the vector index.
- [x] T081 Project five release notifications to SurrealDB and fix authenticated user-ID/username alias lookup so all five scoped officer accounts receive their notification.
- [x] T082 Keep form identity deterministic when DeepSeek output is invalid: an exact verified form-only result remains grounded, while mixed or verified-gap cases remain fail-closed.
- [x] T083 Roll out the V2 answer/form pipeline to citizen first, pass the five-domain API gate, then enable officer and verify exact-article behavior.
- [x] T084 Run direct browser UAT for citizen, officer and admin; verify the checksum-gated NA17 download surface, officer source-grounded answer and Admin coverage/review dashboard.
- [x] T085 Run 129 Feature 017/form regressions, 177 frontend tests, lint and production build; publish the local-live activation/UAT evidence. Internet hosting remains a separate deployment operation.
- [ ] T086 Bring normal/hard full-answer wall-clock budgets within the 25-second API SLA without weakening grounding, then complete the resumable 1,000-case live DeepSeek evaluation. Implemented the disabled-by-default role-scoped optimized profile (20/24-second total budgets, 4/6-second core retrieval, 2/3-second expanded retrieval, 12/14-second generation, 6,000/12,000-character default context and 1,536/2,048-token output); 4,500/9,000-character context is benchmark-only and requires explicit opt-in. The evaluator supports atomic checkpoint/resume and balanced-domain canaries. Live quality/fallback and 1,000-case evidence remain outstanding; no SLA pass is claimed.

## Phase 15: Intent-first form binding and non-destructive answer coverage

- [x] T087 Write regressions proving an explicit unknown/mismatched form code cannot nominate an unrelated procedure, and every runtime form projection carries a confirmed `procedure_id` identity decision.
- [x] T088 Make coverage/completeness advisory after claim validation: preserve individually grounded claims and issue answers even when another requested facet is missing; malformed or wholly unsupported output must still fail closed.
- [x] T089 Remove the form-only `fully_grounded` override. A released/checksum-valid form may contribute verified evidence only after procedure identity confirmation and cannot by itself certify the whole answer.
- [x] T090 Run focused Feature 017, structured-answer, form recommendation and role regressions; retain effectivity, authority, role, checksum and citation hard gates.
- [ ] T091 With the owner's separate approval in this task slice, run browser UAT for 20 new citizen and 20 new officer questions covering documents, articles, forms, easy/hard, combined and natural-language styles; publish raw evidence and a before/after summary.

## Phase 16: Answer pipeline V3 — intent-first retrieval and useful grounded fallback

- [x] T092 [P] Write regressions for shared canonical-domain aliases, public answer-status contracts and the residence `cu_tru` → `cu_tru_an_ninh` path.
- [x] T093 Implement one shared canonical-domain decision across API, retrieval and form routing; retain original/canonical values and mapping reason in trace.
- [x] T094 [P] Write regressions for long natural-language questions retaining a bounded BM25 query and RRF V2 remaining rank-based; implement bounded lexical query condensation and telemetry.
- [x] T095 Implement the V3 core → expanded-domain → full active/official corpus retrieval ladder without weakening validity, authority, role, exact-article or procedure-identity gates.
- [x] T096 Add the backward-compatible `answer_status`, `fallback_tier`, `canonical_domain`, evidence and coverage fields; preserve supported claims and replace terse source-gap wording with clarification/next-step guidance.
- [x] T097 Update the Ask frontend to render explicit backend answer statuses and never label a response verified when `evidence_count=0`.
- [x] T098 [P] Add a five-second hashed-token L1 session cache with revocation/version invalidation, safe pool timeout configuration and focused security/connection tests.
- [x] T099 [P] Extend vector reconciliation and retrieval telemetry for active/staging/orphan/fingerprint decisions without moving, deleting or re-indexing vectors.
- [x] T100 Run focused backend/frontend regressions, document the V3 architecture and publish the automated release-gate evidence before citizen browser UAT.
