# Legal answer pipeline V3 — implementation record

Date: 2026-08-13

## What changed

- Added a shared deterministic domain projection in `api/legal_domains.py`.
  `cu_tru`, `an_ninh` and `cu_tru_an_ninh` now resolve to the same retrieval
  scope while the original value and mapping reason remain available to the
  trace. Feature 017 form routing uses the same projection.
- Long conversational questions no longer disable BM25. Retrieval condenses
  the question to a bounded set of legal business phrases/terms before the
  existing parameterized SQL search. RRF V2 remains rank-based.
- Added a V3 full-corpus fallback after core and domain-expanded retrieval.
  It broadens only the approved active/official retrieval scope; validity,
  authority, role, exact-article and procedure/form identity gates are reused.
- Added explicit public answer fields: `answer_status`, `fallback_tier`,
  `canonical_domain`, `evidence_count`, `coverage_warning` and
  `blocked_reason`. Existing response fields and answer modes remain intact.
- Source gaps now explain what was checked and ask for the missing procedural
  detail instead of returning a terse “no information” response.
- Ask UI renders the backend status directly and forces an insufficient-
  evidence presentation when `evidence_count` is zero.
- Added a five-second in-process L1 session cache keyed only by SHA-256 token
  hash. Logout, revocation, deactivation and security-sensitive account
  updates clear the relevant cache entries. Raw tokens are never cached.
- Added a safe PostgreSQL pool timeout (`5s`) and read-only manifest fields for
  embedding/pipeline fingerprints. No live database, vector collection or
  validity snapshot was mutated.

## Verification

- Focused backend V3/domain/retrieval tests: 50 passed.
- Auth cache, manifest, conversation and batch retrieval tests: pass.
- Full frontend Vitest suite: 41 files, 178 tests passed; Ask `StreamingResponse`: 11/11 pass.
- Frontend ESLint: pass.
- `frontend`: `npx tsc --noEmit` passes (`TSC_EXIT=0`) after completing the
  unavailable-users fixture with the required typed statistics shape.
- Live DeepSeek V4 Flash citizen evaluation completed all 1,000 approved
  Golden V3 cases in
  `reports/feature017/deepseek-golden1000-live-v3.json`. End-to-end P50 was
  12.343s and P95 was 18.267s, so the 25s latency SLA passed. This run is not
  a quality pass: 746/1,000 cases passed the structured evaluator; there were
  48 HTTP 500/empty-answer cases, 84 form-set mismatches and 254 answer-mode
  mismatches. No unexpected provider fallback was observed. T086 therefore
  has latency evidence but remains open for quality remediation.
- Browser UAT T091 was not falsely marked complete. The available in-app
  browser initially could not attach to the local `localhost:3000` webview,
  but a fresh local tab later attached and authenticated successfully. Direct
  UI runs produced answer text for 19/20 citizen cases and 20/20 officer
  cases; one citizen case hit the UI selector timeout while the answer panel
  was still transitioning. The persisted evidence summary is
  `reports/feature017/web-uat-40-natural-questions.json` (40 planned, 39
  rendered, 1 timeout). This is not yet a clean T091 pass because the
  timeout must be replayed independently and each case needs final rendered
  evidence.

## Explicit non-goals in this slice

- No re-index, vector deletion, live schema migration or active-release switch.
- BGE remains disabled until its separately defined activation benchmark passes.
- Browser UAT remains a release-gate task. The live 1,000-case DeepSeek run is
  complete for latency evidence, but its quality failures remain open.

## Resumable live-evaluation evidence hardening

The live evaluator now emits V2 reports and checkpoints. A checkpoint is bound
to the evaluator version, Golden file SHA-256, selected case IDs, role, target
origin hash, concurrency and request timeout. Resume rejects legacy or
mismatched checkpoints instead of carrying stale evaluation decisions into a
new release report.

Completed checkpoint rows store only the minimum response projection required
to rerun the current evaluator: answer presence, public answer mode, grounding
and completeness status, form IDs, clarification count, quality flags and a
stable error code. Server/provider prose is not copied into default evidence.
The Feature 018 quality summary requires this identity-bound V2 envelope, so
the earlier V1 run remains useful for latency diagnosis but cannot qualify a
future GO decision. A new paid live run is still approval-gated.
