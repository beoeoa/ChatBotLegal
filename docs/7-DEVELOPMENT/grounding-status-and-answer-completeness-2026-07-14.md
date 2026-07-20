# Grounding status and answer completeness

## Decision

`grounding_status` measures whether legal claims and citations in the answer
match the retrieved, approved, active legal chunks. It does not measure
whether a procedural answer contains every requested section.

Procedural completeness is reported separately through `source_gap` and the
answer's explicit `Thông tin chưa xác minh được từ nguồn hiện có` notice.

## Why

A response can cite a retrieved provision correctly while the current corpus
does not contain a verified fee, deadline, or form. Downgrading that response
to `partially_grounded` incorrectly suggests that its existing citations are
unreliable. Conversely, treating an answer with a fabricated or invalid
citation as grounded would weaken the legal safety boundary.

The verification order is therefore:

1. Validate every `legal:<chunk_id>` citation against retrieval results.
2. Validate mentioned law numbers and article numbers against those results.
3. Keep `fully_grounded` when these checks pass.
4. Compute missing procedural sections independently and expose them in
   `source_gap`.

This applies to both the local fallback path and the cloud answer path.

## Verification

The focused grounding, question-policy, and form citation tests pass after
this change. The full repository command should exclude `external/crawl4ai`
tests, because that dependency contains standalone adversarial tests that call
`SystemExit` during pytest collection.
