# Step 5 latency optimization — 2026-07-26

## Scope and safety

The production `.env` remains
`LEGAL_SECTION_GROUNDING_ENABLED=false`. Measurements used an isolated API
process with the flag enabled only in that process; the existing API, model
default, credentials, active collection and corpus were not changed.

No repair call, paid service, model replacement, collection switch or
embedding operation was performed.

## Sequential changes

1. Existing 7,000-character context was measured as the baseline.
2. Context cap was reduced to 5,000 characters.
3. Context cap was reduced to 4,000 characters.
4. Evidence context now trims breadcrumb document titles and skips duplicate
   rows with the same issue/source chunk.
5. Structured JSON prompt was shortened while retaining the same schema,
   facet coverage contract and exact-quote rules.
6. Existing embedding/query and retrieval-result caches were retained.
7. Existing orchestration already performs expanded retrieval only for issues
   with missing/insufficient facets; no broad expansion was added.

The final context cap is 4,000 characters.

## Measured stage timing

Privacy-safe artifacts are under
`reports/feature005/step5-latency-20260726/`.

| Variant | Retrieval P95 | Provisioning P95 | Generation P95 | Validation P95 | End-to-end P95 |
|---|---:|---:|---:|---:|---:|
| 7,000 chars | 20 ms | 0 ms | 23,780 ms | 23 ms | 23,857 ms |
| 5,000 chars | 21 ms | 0 ms | 23,764 ms | 14 ms | 23,807 ms |
| 4,000 chars | 19 ms | 0 ms | 23,761 ms | 31 ms | 23,858 ms |
| final warm c1 (6 cases) | 358 ms | 0 ms | 23,770 ms | 18 ms | 23,865 ms |
| final warm c5 (6 cases) | 361 ms | 0 ms | 0–514 ms* | 16 ms | 896 ms |

\* Warm concurrency 5 mostly hit the existing retrieval/embedding cache; one
request still recorded the provider timeout. Cold-mode artifacts are retained
as separate runs and are not interpreted as cache-cleared because the runner
does not flush the live service cache.

All final benchmark requests completed at HTTP level, quality gates passed,
and `repair_count=0`. The provider path still emitted generation timeout
signals at approximately the 24-second budget for warm concurrency 1.

## Quality and role checks

- Structured/latency/backend regression suite: 897 passed.
- 9 role cases: 2 passed, 7 failed quality scoring because form catalog
  availability and expected citations were missing in the benchmark runtime.
  No automatic form approval or legal-source substitution was performed.
- Benchmark artifacts passed the privacy scanner.

## Decision

`BLOCKED_EXTERNAL/DECISION_REQUIRED`: the configured provider/model continues
to hit the 24-second generation budget on warm concurrency-1 requests despite
the bounded-context and prompt reductions. A decision is required on provider
latency or timeout policy. Keep the feature flag `false` until that decision;
do not change the model or active collection automatically.
