# Quickstart: Feature 018 staged verification and release gates

## Safety preconditions

- Do not apply Feature 018 migration to a live/default database without a separately approved task slice, verified backup and isolated rehearsal evidence.
- Do not move/delete/re-index real vectors or switch an active vector pointer during plan/shadow slices.
- Do not switch support/FAQ to `postgres_active`, activate public release or deploy production before Go/No-Go.
- Do not disable validity, authority, role, procedure, form, citation, checksum or audit gates to make tests pass.
- Preserve the dirty worktree and unrelated user changes.

## 1. Validate artefacts and Capability Registry

```powershell
python scripts/audit_capability_registry.py --registry config/system-capabilities.json --report reports/feature018/capability-audit.json
pytest -q tests/test_capability_registry.py
```

Expected: every registered route/job has owner, server role policy, source, audit, retention, SLA, tests and rollback; disabled legacy routes are not routable.

## 2. Answer presentation and chat

```powershell
pytest -q tests/test_legal_answer_presentation.py tests/test_answer_pipeline_v3.py
Set-Location frontend
npm test -- --run src/components/search
npx tsc --noEmit
```

Expected: one renderer, exact forms/citations remain structured, coverage does not remove supported claims, chat controls remain visible in long conversations.

## 3. Isolated support/FAQ migration rehearsal

```powershell
python scripts/rehearse_feature018_postgres.py plan
pytest -q tests/test_feature018_schema_rehearsal.py tests/test_support_repository.py tests/test_support_allocator.py tests/test_faq_governance.py
```

Optional only on an explicitly isolated database:

```powershell
$env:FEATURE018_DATABASE_URL = "postgresql+psycopg2://.../feature018_isolated_20260813"
python scripts/rehearse_feature018_postgres.py up --confirm-isolated
python scripts/rehearse_feature018_postgres.py import-shadow --confirm-isolated
python scripts/rehearse_feature018_postgres.py verify --confirm-isolated
python scripts/rehearse_feature018_postgres.py down --confirm-isolated --allow-drop-fixtures
```

Expected: live/default database names are refused; import is idempotent; ownership/count/timestamp/attachment/state reconciliation passes; no active pointer changes.

## 4. Support concurrency

```powershell
pytest -q tests/test_support_api.py tests/test_support_realtime.py tests/test_support_authorization.py
python scripts/load_test_support.py --tickets 1000 --officers 30 --max-active 3 --report reports/feature018/support-load.json
```

Expected: zero duplicate/wrong-domain assignments, max 90 realtime sessions, waiting users do not open realtime sockets, queue/support SLA passes.

## 5. Lifecycle, impact and vector manifest

```powershell
pytest -q tests/test_legal_lifecycle_feature018.py tests/test_legal_impact_engine.py tests/test_vector_serving_manifest.py
python scripts/build_vector_serving_manifest.py --read-only --output reports/feature018/vector-manifest.json
```

Expected: 90/30/7/1 buckets, partial/replacement/suspension/history cases pass; vector report classifies active/historical/staging/missing/orphan/duplicate/fingerprint mismatch without mutation.

## 6. Forms, FAQ, dashboard and security

```powershell
pytest -q tests/test_feature017_*.py tests/test_faq_governance.py tests/test_activity_center.py tests/test_release_security.py
Set-Location frontend
npm test -- --run
npm run lint
npm run build
```

Expected: Feature 017 active form release remains canonical; FAQ cannot leak unreleased forms; dashboard actions and reason-gated sensitive access pass; frontend gate is green.

## 7. Legal-answer quality gates

```powershell
pytest -q tests/test_answer_pipeline_v3.py tests/test_legal_claim_validation.py tests/test_legal_exact_article_packet.py
python scripts/run_feature017_deepseek_golden1000.py --resume --report reports/feature018/deepseek-live-1000.json
```

Also run 100 regression, 294 approved Golden and Golden V3 1,000 full-answer evaluation on the same release fingerprints.

Expected: correct document/procedure ≥99%, exact forms 100%, citation support 100%, critical fact coverage ≥95%, unexpected fallback ≤3%, provider errors <0.5%, retrieval P95 ≤3s and full-answer P95 ≤25s.

## 7A. Retrieval Release V2 — inventory and evaluation suite

The release scope is the complete PostgreSQL inventory of 12,236 documents,
not only the 7,245-document M2 baseline or the 3,000-document candidate.
Current and historical serving are separate; unresolved metadata/effectivity
is quarantined.  The observed classification is not legal approval.

```powershell
python scripts/audit_retrieval_source_inventory_v2.py --output reports/retrieval-release-v2/source-inventory-reconciliation.json
python scripts/assess_retrieval_quality_policy_v2.py --output reports/retrieval-release-v2/quality-policy-v2-assessment.json --tokenizer <approved-local-vnlegal-tokenizer>
python scripts/build_retrieval_metadata_review_queue_v2.py
# Only after a human reviewer supplies a separate correction attestation:
python scripts/apply_retrieval_metadata_attestation_v2.py --queue reports/retrieval-release-v2/metadata-review-queue-v2.json --inventory reports/retrieval-release-v2/source-inventory-reconciliation.json --attestation <metadata-attestation-v1.json> --output-audit reports/retrieval-release-v2/metadata-attestation-report-v1.json --output-inventory reports/retrieval-release-v2/source-inventory-metadata-overlay.json
python scripts/reconcile_retrieval_source_gaps_v2.py
python scripts/record_retrieval_official_source_observations_v2.py --output reports/retrieval-release-v2/official-source-observations-v2-fetch2.json
python scripts/build_retrieval_source_gap_review_manifest_v2.py --reconciliation reports/retrieval-release-v2/source-gap-reconciliation-full-inventory-v2-fullchroma-r1.json --observations reports/retrieval-release-v2/official-source-observations-v2-fetch2.json --output reports/retrieval-release-v2/source-gap-review-manifest-v2-20260816-fetch2-rerun-fullchroma-r1.json
python scripts/prepare_retrieval_review_attestation_template_v2.py --manifest reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-draft-passage-v3.json --output reports/retrieval-release-v2/legal-review-attestation-v2-passage-v3-template.json
set LEGAL_EMBED_DEVICE=cuda
.venv-retrieval-cu126\\Scripts\\python.exe scripts/capture_retrieval_runtime_fingerprint.py --prewarm --output reports/retrieval-release-v2/runtime-fingerprint-cuda.json
python scripts/prepare_retrieval_eval_suite_v1.py
python scripts/validate_retrieval_eval_suite_v1.py reports/retrieval-release-v2/retrieval-eval-suite-v1.json --development-only --report reports/retrieval-release-v2/eval-suite-validation.json
python scripts/validate_production_holdout_artifact_v1.py --envelope reports/retrieval-release-v2/production-holdout-envelope-v1.json
python scripts/audit_retrieval_eval_source_availability_v2.py --suite reports/retrieval-release-v2/retrieval-eval-suite-v1.json --manifest reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-approved-passage-v3.json --output reports/retrieval-release-v2/retrieval-eval-source-availability-v2.json
python scripts/verify_retrieval_chunk_manifest_v2.py reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-draft-passage-v3.json --output reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-verification-passage-v3.json
python scripts/build_retrieval_quality_policy_release_report_v2.py
python scripts/check_retrieval_release_v2_gates.py
```

Before the gate, verify the real baseline write barrier. The probes are
transactional and rolled back:

```powershell
python scripts/manage_chroma_collection_lock.py verify --chroma-path release-data/legal/chroma_store --collection legal_chunks_vnlegal_lal_haiphong_unified_v1 --active-pointer-file release-data/legal/chroma_store/active_core_collection.txt --output reports/retrieval-release-v2/baseline-write-lock-verification.json
```

The acceptance population is Golden regression 1,000, Hard-negative 500 and
sealed Production holdout 500 (2,000 total), balanced across five domains.
Only the 1,500 development cases may be present in the workspace. The public
holdout artifact is a checksum envelope; its questions, labels and expected
sources stay in a legal-QA custody bundle outside this repository. A missing
or incomplete split is a release blocker and must not be filled with synthetic
legal cases.

Approval is a separate, reviewer-supplied input.  The command below requires
one official-source/effectivity/jurisdiction review row for every document and
writes a new approved manifest; it never edits the draft, database, vectors or
active pointer:

```powershell
python scripts/approve_retrieval_chunk_manifest_v2.py --manifest reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-draft-passage-v3.json --inventory reports/retrieval-release-v2/source-inventory-reconciliation.json --attestation <legal-review-attestation-v2.json> --output reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-approved-passage-v3.json
python scripts/build_retrieval_release_v2_lexical_index.py --manifest reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-approved-passage-v3.json --output reports/retrieval-release-v2/legal-retrieval-v2-exact-lexical.sqlite3
set LEGAL_EMBED_DEVICE=cuda
.venv-retrieval-cu126\\Scripts\\python.exe scripts/run_retrieval_release_v2_benchmark.py --mode m5 --manifest reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-approved-passage-v3.json --serving-manifest reports/retrieval-release-v2/legal-serving-manifest-v3.json --lexical-index reports/retrieval-release-v2/legal-retrieval-v2-exact-lexical.sqlite3 --suite reports/retrieval-release-v2/retrieval-eval-suite-v1.json --holdout-envelope reports/retrieval-release-v2/production-holdout-envelope-v1.json --chroma-path release-data/legal/chroma_store --current-collection <v2-current-collection> --temporal-collection <v2-temporal-collection> --output reports/retrieval-release-v2/m5-v2-acceptance.json
.venv-retrieval-cu126\\Scripts\\python.exe scripts/run_retrieval_release_v2_benchmark.py --mode m6 --manifest reports/retrieval-release-v2/legal-retrieval-chunk-manifest-v2-approved-passage-v3.json --serving-manifest reports/retrieval-release-v2/legal-serving-manifest-v3.json --lexical-index reports/retrieval-release-v2/legal-retrieval-v2-exact-lexical.sqlite3 --suite reports/retrieval-release-v2/retrieval-eval-suite-v1.json --holdout-envelope reports/retrieval-release-v2/production-holdout-envelope-v1.json --chroma-path release-data/legal/chroma_store --current-collection <v2-current-collection> --temporal-collection <v2-temporal-collection> --reranker-model <pinned-reranker> --reranker-manifest <reranker-manifest.json> --reranker-custom-code <pinned-custom-code-if-declared> --output reports/retrieval-release-v2/m6-v2-acceptance.json
```

Only the independent legal-QA custodian runs the final holdout command. The
custody bundle path must resolve outside `J:\\ChatBotLegal`; the returned file
contains aggregate metrics only:

```powershell
.venv-retrieval-cu126\\Scripts\\python.exe scripts/run_retrieval_release_v2_holdout_acceptance.py --mode m6 --benchmark-report reports/retrieval-release-v2/m6-v2-acceptance.json --suite X:\\legal-qa-custody\\holdout-v1.json --holdout-envelope reports/retrieval-release-v2/production-holdout-envelope-v1.json --current-collection <v2-current-collection> --temporal-collection <v2-temporal-collection> --output reports/retrieval-release-v2/production-holdout-acceptance-v1.json
python scripts/validate_production_holdout_artifact_v1.py --envelope reports/retrieval-release-v2/production-holdout-envelope-v1.json --receipt reports/retrieval-release-v2/production-holdout-acceptance-v1.json
```

After legal review creates an approved V2 chunk manifest, build only in shadow:

```powershell
python scripts/build_retrieval_release_v2_shadow.py --manifest <approved-v2-chunk-manifest.json> --target <v2-current-collection> --document-state current_retrievable --output reports/retrieval-release-v2/legal-retrieval-v2-current-shadow.report.json --apply
python scripts/build_retrieval_release_v2_shadow.py --manifest <approved-v2-chunk-manifest.json> --target <v2-temporal-collection> --document-state all --output reports/retrieval-release-v2/legal-retrieval-v2-temporal-shadow.report.json --apply
python scripts/verify_retrieval_release_v2_shadow.py --manifest <approved-v2-chunk-manifest.json> --chroma-path release-data/legal/chroma_store --collection <v2-current-collection> --document-state current_retrievable --output reports/retrieval-release-v2/current-shadow-verification.json
python scripts/verify_retrieval_release_v2_shadow.py --manifest <approved-v2-chunk-manifest.json> --chroma-path release-data/legal/chroma_store --collection <v2-temporal-collection> --document-state all --output reports/retrieval-release-v2/temporal-shadow-verification.json
python scripts/build_retrieval_release_v2_lexical_index.py --manifest <approved-v2-chunk-manifest.json> --output reports/retrieval-release-v2/legal-retrieval-v2-exact-lexical.sqlite3
python scripts/build_retrieval_serving_manifest_v3.py --manifest <approved-v2-chunk-manifest.json>
.venv-retrieval-cu126\Scripts\python.exe scripts/verify_retrieval_release_v2_vectors.py --manifest <approved-v2-chunk-manifest.json> --vector-report reports/retrieval-release-v2/legal-retrieval-v2-current-shadow.report.json --collection <v2-current-collection> --state current_retrievable --sample-size 500 --run-cpu-gpu-parity --output reports/retrieval-release-v2/current-vector-verification.json
python scripts/rehearse_retrieval_release_v2_pointer.py --active-pointer release-data/legal/chroma_store/active_core_collection.txt --candidate-manifest reports/retrieval-release-v2/legal-serving-manifest-v3.json --output reports/retrieval-release-v2/pointer-rollback-rehearsal.json
```

The builder never copies legacy vectors and has no activation option.  It must
stop on missing legal attestation, mixed fingerprints, empty/oversized chunks,
non-finite vectors, ID mismatch or pointer change.

## 7B. Retrieval Quality V2

```powershell
pytest -q tests/test_m4_query_understanding.py tests/test_retrieval_evaluation_v2.py
python scripts/build_retrieval_source_gap_manifest.py
python scripts/build_retrieval_dataset_integrity_report.py
python scripts/build_source_article_integrity_report.py
python scripts/run_retrieval_quality_v2.py --output reports/retrieval-quality-v2/control-full-v2.json
python scripts/run_retrieval_stage_diagnostics_v2.py --output reports/retrieval-quality-v2/stage_diagnostics_hard100_v2.json
python scripts/merge_retrieval_control_replay.py
```

Expected: Golden-1.000, Hard-negative-500 và Production holdout-500 được chấm
riêng; expected refusal
không nằm trong mẫu số source Recall/MRR; temporal date-scoped không bị chặn;
source gaps có checksum; safety count bằng 0 và active pointer không đổi.
Learned reranking chỉ được chạy sau khi candidate Recall@50 gate đạt.
Runner M6/M6.1 cũng phải dừng trước khi load model nếu source-law hoặc
source-article gap chưa được phê duyệt và xử lý.

## 7C. Vector Integrity V3 foundation

```powershell
pytest -q tests/test_vector_integrity_v3.py tests/test_feature018_contracts.py
python scripts/validate_feature018_contracts.py
python scripts/manage_vector_integrity_v3_schema.py plan
```

Optional only on a disposable database whose name starts with
`feature018_vector_` or `feature018_isolated_` and already contains fixture
definitions for `legal_documents` and `legal_articles`:

```powershell
$env:FEATURE018_VECTOR_DATABASE_URL = "postgresql+psycopg2://.../feature018_vector_isolated_<id>"
python scripts/manage_vector_integrity_v3_schema.py up --confirm-isolated
python scripts/manage_vector_integrity_v3_schema.py verify --confirm-isolated
python scripts/manage_vector_integrity_v3_schema.py down --confirm-isolated --allow-drop-fixtures
```

Expected: Manifest V3 requires separate model/tokenizer/embedding/passage/
splitter/source/dependency/vector fingerprints; release counts fail closed;
approved metadata needs official evidence plus reviewer; rehearsal creates only
the three additive V3 tables and performs no vector or pointer mutation.

## 8. Browser UAT

After code/API gates pass and browser UAT is approved:

- 20 new citizen questions and 20 new officer questions covering document/article/form/dossier/procedure/history/combined/easy/hard/natural language.
- Citizen support: create, wait, leave/return, assigned realtime, close.
- Officer: presence, queue, capacity, claim/requeue/resolve.
- Admin: lifecycle alert → replacement impact → index status → FAQ/form release; activity access reason; model setup.
- Viewport 360/768/1440, keyboard/focus/labels, no console error/HTTP 500.

## 9. Backup/restore, security, load and Go/No-Go

```powershell
python scripts/verify_production_readiness.py --release <release-id> --evidence-dir reports/feature018 --output reports/feature018/go-no-go.json
```

Required evidence: migration rehearsal, PostgreSQL/object/vector/Surreal restore rehearsal, secret/upload/auth/ACL/CORS/rate tests, 1,000-user/100-ask/30-officer load, build, Golden/live/UAT and rollback smoke.

Only `GO` when no P0/P1 remains and every evidence fingerprint matches the candidate release. Observe citizen canary 10% → 50% → 100%, then officer/admin, for at least 72 hours before `stable`.
