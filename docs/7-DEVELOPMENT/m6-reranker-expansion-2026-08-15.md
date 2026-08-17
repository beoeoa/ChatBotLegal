# M6 reranker and expansion — 2026-08-15

## Outcome

The M6 implementation and isolated real-model smoke matrix pass. Production
activation does not pass: even the smallest learned window is far above the
3,000 ms retrieval p95 gate, so the active learned-reranker flag and active
collection pointer remain unchanged.

## Frozen inputs

- Candidate serving manifest: `legal-serving-candidate-3000-v1.json`, 3,000
  documents and 88,209 chunks/vectors.
- Retrieval parent from M5: vector Top-K 20, lexical Top-K 20,
  `legacy_stack` fusion.
- Reranker: `BAAI/bge-reranker-v2-m3`, revision
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.
- Local model path:
  `J:\DevCache\HuggingFace\bge-reranker-v2-m3-953dc6f6f85a`.
- All six model-file SHA-256 values match the existing Feature 016 model
  manifest.
- CUDA runtime: PyTorch 2.8.0+cu126 on NVIDIA GTX 1660. Query embedding was
  placed on CPU for this isolated run so the 6 GB GPU could load the reranker
  without weakening the 2,800 MiB free-VRAM guard.

The Golden-1000 questions used in M5 contain explicit law and article
identifiers. The retrieval contract correctly sends those requests through the
deterministic exact-article packet and bypasses relevance reranking. Therefore
they cannot measure M6 learned-reranker quality. M6 uses the already approved
100-case Feature 016 hard-negative set for reranker acceptance; the one-case
run below is an implementation smoke, not a replacement for the full
100-case acceptance run.

## Implemented contract

- `rerank_top_n` is request-scoped and bounded to 1–100.
- Cross-encoder windows 20, 30, 50 and 100 are supported. If the M5 fused
  candidate pool contains fewer candidates, the actual scored count is
  reported instead of fabricating candidates.
- Parent and neighbor expansion can be enabled independently.
- Exact-article packets retain mandatory parent integrity even when ordinary
  parent expansion is disabled.
- Batch request propagation and cache keys include all M6 controls.
- Responses and benchmark rows expose reranker mode, scored count, reranker
  latency, parent hydration count and neighbor candidate count.
- Benchmark gates count both evidence outside the serving manifest and evidence
  that is not current as of the frozen legal date.

## Real-model smoke result

| Experiment | Recall@10 | MRR | Retrieval p95 | Reranker p95 |
|---|---:|---:|---:|---:|
| Control, no expansion | 1.00 | 1.00 | 1,655.6 ms | 0 ms |
| Rerank Top-N 20 | 1.00 | 1.00 | 30,836.2 ms | 29,275.4 ms |
| Rerank Top-N 30 | 1.00 | 1.00 | 28,925.1 ms | 27,559.2 ms |
| Rerank Top-N 50 | 1.00 | 1.00 | 43,106.7 ms | 41,822.1 ms |
| Rerank Top-N 100 | 1.00 | 1.00 | 39,740.1 ms | 38,464.7 ms |
| Parent only, winner Top-N 30 | 1.00 | 1.00 | 30,607.4 ms | 28,987.0 ms |
| Neighbor only, winner Top-N 30 | 1.00 | 1.00 | 28,048.6 ms | 26,550.4 ms |

The parent-only run hydrated four parent contexts and zero neighbors. The
neighbor-only run added three neighbor chunks and hydrated zero parents. Every
run returned zero expired/invalid evidence, zero out-of-manifest evidence and
zero errors. The active pointer was unchanged.

Recall and MRR from one case are not acceptance-quality statistics. Latency is
already a decisive hard failure: learned reranking is roughly 9–13 times the
3-second retrieval SLO even before concurrent production load.

## Decision

Keep `LEGAL_LEARNED_RERANKER_ENABLED` disabled. Do not activate or change the
active collection. Continue serving the M5 vector + lexical + heuristic stack.
Before any future activation, benchmark either a smaller/quantized reranker or
a stronger GPU profile on the full approved 100-case hard-negative set, then
repeat parent-only and neighbor-only measurement.

## Evidence

- `reports/m6-reranker-expansion-smoke-hard/m6_protocol.json`
- `reports/m6-reranker-expansion-smoke-hard/m6_reranker_expansion_v1.json`
- `reports/m6-reranker-expansion-smoke-hard/m6_checksums.json`
- `reports/m6-reranker-expansion-smoke-hard/m6_audit_v1.json`

The independent artifact audit passes all structural, model-mode, expansion,
safety, checksum, no-activation and pointer-integrity checks. The latency gate
is separately and explicitly false.
