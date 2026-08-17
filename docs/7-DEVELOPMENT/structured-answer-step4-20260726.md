# Structured answer contract — Step 4 baseline

## Scope

The structured-answer path now treats validation as a hard boundary between
model output and public rendering. A model response is parsed once for the
whole question. Invalid JSON, timeout, or a failed quality gate uses the
bounded extractive fallback and records the failure; no repair invocation is
attempted.

## Hard gates

- Evidence must belong to the current request and issue.
- The support quote must be present in the evidence content.
- Article, clause, point, law number, amounts, deadlines, and fees must be
  supported by the quote or evidence metadata.
- Form claims with an expected form must match the expected `procedure_id`
  (and `form_code` when present).
- Internal markers such as `[legal:...]`, `chunk_id`, `trace_id`, and
  `packet_id` are rejected from claims. Model guidance is not rendered as a
  legal claim; only validated claims are rendered.

Rejected claims remain visible in the request trace with a reason and are not
used to calculate displayed coverage.

## Verification

- Step 4 regression suite: 10 focused validator/renderer tests plus the
  orchestration timeout case (covering all 11 requested failure modes through
  parametrization).
- Structured pipeline/orchestration suites: 47 tests passed.
- Full backend suite: 896 passed, 11 pre-existing warnings.
- Frontend: 87 tests passed, TypeScript check passed, lint 0 errors (11
  existing unused-symbol warnings), production build passed.
- The one-shot orchestration tests assert one model provisioning/invocation
  and `repair_count=0` for both timeout and invalid JSON.

No corpus files were deleted, rewritten, or re-embedded.
