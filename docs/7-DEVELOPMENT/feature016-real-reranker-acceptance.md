# Feature 016 real learned-reranker acceptance

The legal retrieval path keeps deterministic eligibility first:

1. retrieve with VNLegal-LAL vector search and PostgreSQL BM25;
2. fuse with RRF and existing lexical/metadata heuristics;
3. apply domain, current-validity and relationship hard gates;
4. optionally score at most 40 surviving passages with a local cross encoder;
5. reapply legal hierarchy ordering and preserve stable candidate identity.

`BAAI/bge-reranker-v2-m3` is loaded only from a pinned local directory. Runtime
downloads and remote code are disabled. The loader supports CPU/CUDA, FP16 on
CUDA, bounded batch/window/token length, an explicit VRAM admission check and a
deterministic heuristic fallback for missing model, dependency, resource, load,
inference or OOM failure.

The 2026-08-10 real-passage run used 100 approved hard-negative cases and 1,388
live candidates. It improved Top-1 from 7% to 9% and MRR by 5.97% relative while
holding Recall@10 and all safety counters. It was not activated because reranker
P95 was 25.38 seconds on the co-resident GTX 1660, above the 3-second retrieval
SLO, with less than 1 GiB VRAM headroom at the observed peak.

The authoritative evidence is in
`reports/feature016/phase-c-real/gate-c-real-evidence.md`; the machine-readable
comparison is `reports/feature016/phase-c-real/benchmark.json`.

Do not enable `LEGAL_LEARNED_RERANKER_ENABLED` on another host merely because
the model loads. Repeat the same-candidate benchmark and require all of:

- Recall@10 does not decrease;
- MRR improves by at least 5% relative or Top-5 improves by 1 percentage point;
- forbidden/ineligible counters do not increase;
- candidate identities are preserved and OOM count is zero;
- retrieval P95 remains at most 3 seconds under the approved concurrency profile.

This acceptance does not authorize BGE-M3 shadow collection creation, corpus
re-indexing, active-pointer changes or any live legal-data migration.
