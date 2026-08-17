# M5 hybrid retrieval experiments

## Result

M5 criteria 30–33 are PASS on the isolated 3,000-document candidate corpus.
The run completed all 13 checksum-bound experiments over the same Golden 1,000,
embedding model, query-understanding code, reranker setting and expansion
behavior. Every run completed with zero execution errors and zero result from
outside the candidate serving manifest.

No serving activation was performed. The active pointer remained
`legal_chunks_vnlegal_lal_haiphong_unified_v1` before and after the run.

## Frozen protocol

- candidate collection: `legal_chunks_candidate_3000_v1`;
- corpus: 3,000 documents and 88,209 chunks/vectors;
- Golden cases: 1,000;
- legal as-of date: `2026-08-14`;
- Golden SHA-256: `b3e0835ef5d11f3438366a6161b20745d964677218e1c3ac8fa267588e642113`;
- candidate file SHA-256: `7681c9b097711131316530da583f373dd8fc5ab65e755309c9947dfa0e2c9326`;
- candidate manifest SHA-256: `fc85bd36fae35b509b341fe384b38bf75dd5f8a9ef71dc1e0d2e684f0ba47da3`;
- embedding fingerprint: `29020cd8c81d5439420992fc7e8c08eb16fa2883f4fefadb1e6bb4918116b9aa`;
- protocol SHA-256: `f49a660a1d1b807fa60c421bf324cf2b3f8bd1cbd98025a83a74ee40a29440c9`;
- learned reranker: disabled;
- parent/neighbor expansion: unchanged baseline behavior;
- final result limit: 10.

The query embedding cache was prewarmed once and reused by every experiment.
The exact-row cache was cleared before each experiment. Runs were sequential so
their latency measurements were not distorted by another benchmark process.

## Results

| Stage | Winning configuration | Recall@10 | MRR | Direct source top 5 | p95 retrieval |
|---|---|---:|---:|---:|---:|
| Control | vector 150, lexical 60, legacy | 0.474 | 0.46651 | 0.475 | 4,798.8 ms |
| Vector Top-K | vector 20, lexical 60, legacy | 0.477 | 0.46757 | 0.478 | 4,667.1 ms |
| Lexical Top-K | vector 20, lexical 20, legacy | 0.478 | 0.46923 | 0.479 | 4,616.5 ms |
| New fusion | vector 20, lexical 20, weighted 0.7/0.3 | 0.478 | 0.46873 | 0.479 | 3,027.8 ms |

All required vector K values `10/20/30/50`, lexical K values `10/20/30/50`,
RRF and weighted configurations `0.7/0.3`, `0.6/0.4`, `0.5/0.5` were measured.
The independent audit recomputed every stage winner and verified that each
experiment changed only its declared variable.

The best new fusion configuration is weighted `0.7/0.3`. It reduces p95 by
1,588.7 ms relative to the lexical-stage parent, but its MRR is lower by
0.00050 while Recall@10 and direct-source Top-5 are equal. Therefore M5 records
it as the fusion-stage winner but does not authorize rollout. The current
legacy fusion with vector K=20 and lexical K=20 remains the conservative global
quality choice pending an explicit latency/quality acceptance decision.

The lower absolute recall compared with the historical M3 report is expected
from the M4 temporal fail-closed contract: ambiguous/year-only historical
questions are now blocked before vector or lexical lookup. This behavior was
identical and frozen in all M5 experiments, so it does not invalidate the M5
one-variable comparisons.

## Evidence and verification

Primary artifacts are under `reports/m5-hybrid-retrieval/`:

- `m5_protocol.json`;
- `m5_hybrid_experiments_v1.json` (read-only);
- `m5_checksums.json`;
- `m5_acceptance_report.json`;
- `m5_audit_checksums.json`;
- 13 full per-experiment files under `experiments/`.

Commands:

```powershell
python scripts/run_m5_hybrid_experiments.py
python scripts/audit_m5_hybrid_experiments.py
pytest -q tests/test_m5_hybrid_retrieval.py tests/test_legal_retrieval_quality.py
```

The first aborted control artifact is retained separately at
`reports/m5-hybrid-retrieval-aborted-domain-gate/`. It proved that the old
`wrong_scope_result_count` metric measures cross-domain label overlap, not a
serving-manifest escape. The official M5 gate uses
`outside_manifest_result_count`; the domain count remains diagnostic only.

## Rollback and next stage

M5 is code/config experimentation only. Rollback is to keep the existing
default `legacy_stack` settings; no database, vector corpus, candidate manifest
or active pointer needs restoration. M6 reranker and expansion experiments have
not started.
