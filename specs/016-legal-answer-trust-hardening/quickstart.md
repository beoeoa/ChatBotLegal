# Quickstart: Feature 016 non-browser verification

## Preconditions

- Do not apply live schema migrations, backfill/re-index real corpus, switch active vectors or enable live flags.
- Use synthetic/isolated fixtures and existing local services only when a test explicitly needs them.
- Browser UAT is excluded until the user approves it after all checks below pass.

## Phase A

```powershell
$qaWorkbook = "<absolute-path-to-qa-log.xlsx>"
python scripts/build_feature016_regression.py --input $qaWorkbook --output notebook_data/feature016-regression-100.json
python scripts/validate_legal_golden_v2.py --dataset notebook_data/feature016-golden-v2.json
pytest -q tests/test_feature016_regression.py tests/test_feature016_golden_v2.py tests/test_feature016_qa_evaluator.py
```

Expected: 100 unique questions, 20/domain, source checksum present, schema passes, and a sourced-but-wrong fixture fails evaluation.

## Phase B

```powershell
pytest -q tests/test_feature016_validity.py tests/test_feature016_intent.py tests/test_feature016_fallback.py tests/test_legal_exact_article_packet.py tests/test_feature016_determinism.py
```

Expected: known expired/partial provisions blocked; confusing procedure pairs separated; source-only is not a legal answer; exact Article packet detects missing/duplicate units; repeated structured decisions are identical.

## Phase C

```powershell
pytest -q tests/test_feature016_reranker.py tests/test_feature016_embedding_shadow.py
python scripts/evaluate_legal_answer_trust.py --mode retrieval-shadow --dataset notebook_data/feature016-golden-v2.json
```

Expected: hard gate remains authoritative; learned reranker meets quality/latency gates or remains disabled with reason code; active collection is unchanged.

For the real-passage acceptance rerun, first collect the exact post-gate
candidate set, then score the same identities with the pinned local model:

```powershell
python scripts/benchmark_feature016_reranker_real.py `
  --dataset reports/feature016/phase-c/hard-negatives-v1.json `
  --candidate-cache reports/feature016/phase-c-real/candidate-cache.json `
  --output reports/feature016/phase-c-real/benchmark.json
```

Exit status is non-zero when any activation gate fails. A failed activation is
an expected safe result: keep the learned feature flag disabled and preserve
the active embedding collection.

## Phase D

```powershell
pytest -q tests/test_feature016_provenance.py tests/test_feature016_audit_chain.py tests/test_feature016_provider_privacy.py tests/test_feature016_migration_rehearsal.py
```

Expected: forged/mismatched citations fail; audit tampering is detected; PII never leaves unredacted; migration forward/rollback passes in isolation.

## Phase E and final non-browser gate

```powershell
pytest -q tests/test_feature016_extraction_blocks.py tests/test_feature016_bounded_multihop.py
pytest -q
```

Run isolated API/load benchmarks at concurrency 1/5/10/20/30 and scan artifacts for secrets/PII. When all applicable gates pass, stop and ask the user before any direct browser testing.

### Final isolated API load command

```powershell
python scripts/benchmark_feature016_isolated_api.py `
  --output reports/feature016/final-non-browser/isolated-api-load.json `
  --requests-per-level 60 `
  --duration-seconds 600 `
  --rps 3
```

Expected: 60 requests at each concurrency 1/5/10/20/30, followed by exactly
1,800 scheduled requests at 3 RPS for ten minutes; zero persisted questions,
answers, credentials or citation bodies. This is an isolated real-route harness
with deterministic external dependencies, not a production model/database
capacity claim.

Review `reports/feature016/final-non-browser/test-manifest.json` and
`reports/feature016/final-non-browser/fr-sc-evidence.md` before requesting
browser approval. The final run must include the complete backend and frontend
suites, lint, production build and whitespace check; the recorded 2026-08-10
run passed all of these gates.
