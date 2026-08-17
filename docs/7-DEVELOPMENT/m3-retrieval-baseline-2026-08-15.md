# M3 retrieval baseline and criterion-29 trace

## Result

Criterion 29 is PASS for the isolated 3,000-document candidate collection.
The trace contract is `legal-retrieval-trace-m3-v1` and every traced query
contains raw/normalized query, deterministic classification, vector and
lexical candidates, fusion candidates, reranker input/output, expanded
evidence, final evidence and per-stage latency.

This is an observation-only change. Candidate scores, ranking, result limits,
document/chunk scope and active pointers are not modified by trace capture.

## Frozen baseline configuration

- collection: `legal_chunks_candidate_3000_v1`;
- documents: 3,000;
- chunks/vectors: 88,209;
- ranking: `legacy_stack`;
- candidate windows: vector 150, lexical 60, final 10;
- learned reranker: disabled;
- exact-flat backend: disabled;
- hydration cache: disabled;
- broad fallback: enabled;
- retrieval tier: core.

The previously completed 1,000-query candidate baseline remains the aggregate
quality/latency reference: Recall@10 94.6%, MRR 93.256%, p50 168.181 ms and
p95 9,660.568 ms. The M3 trace artifact links and checksums that unchanged
historical report rather than relabelling later optimized runs as the baseline.

## Trace capture evidence

Three deterministic natural-language queries were captured across civil
status, land and residence domains. All stages are non-empty:

| Query | Vector | Lexical | Fusion | Reranker input/output | Expanded | Final | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| Civil status | 150 | 60 | 213 | 84 / 84 | 26 | 2 | 4,311.1 ms |
| Land | 150 | 59 | 210 | 210 / 210 | 8 | 7 | 6,408.1 ms |
| Residence | 150 | 60 | 198 | 218 / 218 | 31 | 10 | 3,944.4 ms |

Candidate-stage projections contain IDs, provenance and scores but no chunk
content. Raw query is retained because criterion 29 explicitly requires it;
production API routing continues to control whether trace is requested.

## Commands

```powershell
python scripts/capture_m3_candidate_trace.py --device auto
python scripts/audit_m3_retrieval_baseline.py
pytest -q tests/test_m3_retrieval_trace.py tests/test_legal_retrieval_runtime.py
```

The trace artifact is immutable and versioned. Re-running the same output path
is rejected; a changed benchmark must use a new version.

## Safety and rollback

The candidate benchmark uses explicit benchmark mode and its checksum-bound M1
manifest. Before and after capture, the active Chroma pointer remained
`legal_chunks_vnlegal_lal_haiphong_unified_v1`. The live baseline service was
restarted with the new trace code and passed the same M2 manifest health check
at 168,155 vectors.
