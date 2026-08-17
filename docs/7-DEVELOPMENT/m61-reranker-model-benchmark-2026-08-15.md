# M6.1 local reranker model benchmark — 2026-08-15

## Decision

`BAAI/bge-reranker-v2-m3` with Top-N 10 and `max_length=512` is the only
learned reranker configuration that passes the frozen M6.1 acceptance gate.
It is **eligible for controlled review**, but it was not activated. The active
collection pointer, live reranker configuration and both local model snapshots
remain unchanged.

GTE is faster, but neither GTE Top-N 10 nor Top-N 20 meets the required quality
gain. Do not replace or delete BGE based on this run.

## Frozen protocol

- Corpus: candidate manifest 3,000, exactly 88,209 chunks/vectors.
- Evaluation: approved Golden-100 hard-negative set.
- M5 parent: vector Top-K 20, lexical Top-K 20, `legacy_stack` fusion.
- Evidence: final 10, parent and neighbor expansion enabled and manifest-bound.
- Reranker: batch 8, `max_length=512`, GTX 1660 CUDA, same local host.
- Experiments: no learned reranker, BGE Top-N 10, GTE Top-N 10 and conditional
  GTE Top-N 20.
- Gate: p95 retrieval plus reranking at most 15 seconds, Recall@10 no lower
  than control, relative MRR gain at least 5% or Top-5 gain at least 1 point,
  zero errors/OOM and zero invalid/out-of-manifest evidence.

Each experiment changed only its declared reranker variable. No live pointer,
configuration or corpus write was allowed.

## Results

| Experiment | Recall@10 | MRR | MRR gain | Top-5 | Warm p50 total | Warm p95 total | Warm p95 rerank | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Control M5 | 50.0% | 0.39117 | — | 70.0% | 2.619 s | 7.986 s | 0 s | Control |
| BGE Top-N 10 | 50.0% | 0.41867 | +7.0302% | 70.0% | 6.011 s | 8.453 s | 5.487 s | PASS |
| GTE Top-N 10 | 50.0% | 0.39283 | +0.4244% | 70.0% | 3.534 s | 5.515 s | 2.175 s | FAIL quality |
| GTE Top-N 20 | 50.0% | 0.39283 | +0.4244% | 70.0% | 5.054 s | 8.499 s | 3.968 s | FAIL quality |

Cold-start was measured separately before the warm benchmark. BGE took 9.560
seconds and GTE took 17.425 seconds to load and score the fixed calibration
packet on CUDA. The immediate second warm-up call took 1.403 seconds for BGE
and 0.516 seconds for GTE. The fixed calibration query produced four eligible
manifest results; warm p50/p95 above are computed over all 100 cases and are
the acceptance values.

Two Golden cases (`web-023`, `web-089`) returned no evidence in every control
and learned run, so no reranker stage was invoked for those cases. The audit
classifies this as a consistent retrieval miss, not a learned-model fallback.
All other learned cases report `mode=learned`, `device=cuda`, batch 8,
`max_length=512` and no degradation.

## Safety and artifact evidence

- Errors/OOM: 0 in all four experiments.
- Evidence outside serving manifest: 0.
- Expired/invalid evidence: 0.
- Candidate model/code artifacts: pinned and SHA-256 verified before loading.
- GTE model revision: `8215cf04918ba6f7b6a62bb44238ce2953d8831c`.
- GTE custom-code revision: `40ced75c3017eb27626c9d4ea981bde21a2662f4`.
- Active pointer before/after:
  `legal_chunks_vnlegal_lal_haiphong_unified_v1`.
- Independent audit: PASS, with no failed checks.

Canonical artifacts:

- `reports/m61-reranker-model-comparison/gte-model-manifest.json`
- `reports/m61-reranker-model-comparison/m61_protocol.json`
- `reports/m61-reranker-model-comparison/m61_reranker_model_benchmark.json`
- `reports/m61-reranker-model-comparison/m61_audit.json`
- `reports/m61-reranker-model-comparison/m61_checksums.json`

## Operational consequence

The M6.1 comparison selects BGE Top-N 10 for the next approval/rehearsal slice.
It does not authorize activation. Keep live serving on its existing M5 posture
until a separate approval includes a rollback rehearsal and controlled rollout.
The absolute Recall@10 remains 50%, so this pass only establishes that BGE
improves ranking under the user-defined gate; it does not establish that the
overall retrieval quality is production-ready.

