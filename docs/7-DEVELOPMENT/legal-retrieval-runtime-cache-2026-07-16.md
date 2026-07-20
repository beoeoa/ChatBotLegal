# Legal retrieval runtime and query-vector cache

Date: 2026-07-16

## Decision

VNLegal-LAL remains the embedding model. The local retrieval process selects
its device with `LEGAL_EMBED_DEVICE=auto|cuda|cpu`:

- `auto` prefers CUDA with float16 and falls back to CPU float32 when CUDA is
  unavailable. A CUDA out-of-memory error reloads the model on CPU and retries
  the failed embedding once.
- `cuda` is strict and fails readiness when the installed PyTorch runtime does
  not expose CUDA.
- `cpu` always uses float32.

The service prewarms the model through the health/readiness path. It reports
ready only after one embedding succeeds. Health exposes the selected device,
dtype, fallback reason, model fingerprint and aggregate cache counters; it does
not expose questions or cache keys.

## Cache boundary

The in-memory query-vector cache is a bounded, thread-safe TTL/LRU cache:

- default TTL: 300 seconds;
- default maximum: 1,024 vectors;
- key: SHA-256 of the normalized rewritten query and model fingerprint;
- value: a defensive float32 copy of the query vector.

Domain, legal date, retrieval tier and corpus revision are deliberately absent
from this cache key because they do not change the embedding. This allows the
expanded tier to reuse the vector already computed for the core tier. A future
full retrieval-result cache must use a separate key that includes those legal
scope fields and the corpus revision.

## Ranking changes

BM25 scores are computed once for the whole candidate set and reused by the
rerank window. Candidates are ordered by their base score before the bounded
rerank bonus is applied. Evidence compaction keeps source/article diversity,
removes exact repeated content within an article, and may retain one additional
non-duplicate chunk when there are not enough distinct articles.

These changes do not weaken effective-date, domain, authority or source URL
filters and do not modify the legal corpus or PostgreSQL schema.

## Verification

Targeted tests cover cache expiry/LRU/privacy, core-to-expanded vector reuse,
device selection, strict CUDA configuration, CUDA OOM retry, readiness, health
fields, single-pass BM25, complementary evidence, two-tier retrieval and legal
date propagation.

