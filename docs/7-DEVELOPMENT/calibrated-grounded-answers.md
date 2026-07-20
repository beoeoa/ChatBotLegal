# Calibrated grounded answers

## Decision

Feature 005 changes multi-issue Ask behavior from one answer-wide grounding decision to request-bound, issue-level grounding. It does not loosen the requirements for legal conclusions.

## Rules

- Retrieval uses only the current user question or a current-question issue span. Conversation history can inform generation but is never a retrieval query.
- Evidence is request-bound and issue-bound. A source cannot support another issue or a later request without independent retrieval and eligibility validation.
- Eligibility checks effective status, official metadata, jurisdiction, issue relevance and legal hierarchy before generation.
- Applicable central law is selected before compatible effective Hai Phong implementation material. Local material never overrides higher-validity central law.
- A sufficient issue may state a legal conclusion with eligible citations.
- A partial issue may show only “Hướng dẫn tham khảo” plus a limitation; it never states a legal conclusion.
- An insufficient issue shows a localized limitation and, when useful, one clarifying question.
- Internal identifiers and citation-marker syntax are never public output.

## Compatibility and rollback

The existing flat Ask response remains available. `answer_sections` is optional and protected by `LEGAL_SECTION_GROUNDING_ENABLED=false` by default. Rollback is disabling the flag and restarting the API. No legal corpus, conversation, audit or source data is rewritten or deleted.

## Observability and privacy

Measure issue count, eligibility outcome, section status count, retrieval/generation/validation/end-to-end timing and repair rate. Do not record raw questions, answers, citation content, credentials, attachments or raw exception messages in feature telemetry.

## Implemented orchestration

When the flag is enabled, the router creates a bounded deterministic issue plan
and invokes the existing Ask graph sequentially once for each issue. Each graph
input contains only the current issue span as both generation and retrieval
query, plus the same server-generated request and issue IDs. The router accepts
a generated legal conclusion only if the legal-reference validator matches it
against that issue's request-local evidence packet. A failed issue is rendered
as a local limitation and cannot replace a verified issue with a global fallback.

The aggregate response remains backward compatible: `answer` and `citations`
are assembled deterministically from validated sections, while the optional
`answer_sections` exposes the verified/partial/insufficient state. Section
citations use only public document metadata and official links. Chunk, trace,
packet and legacy marker values are excluded at both API and UI layers.

## Legacy-path reliability safeguards

The flag-off path is a real rollback of section orchestration, not a rollback of
request isolation.  The router always passes the current question to retrieval;
conversation history is retained only for answer generation.  This prevents a
previous land-related turn from contaminating a later household-registration
query while keeping the existing `AskResponse` contract.

Domain detection is deterministic and is used only when the caller has not
selected a domain.  Reviewed compatibility aliases (for example,
`ho_tich_chung_thuc` and `tu_phap_ho_tich`) are expanded by retrieval without
changing source records.  A clearly selected domain remains authoritative for
authorization and scope checks.

If a draft contains both verified and unsupported legal fragments, the answer
pipeline removes only the unsupported sentence/line and keeps the verified
fragments.  It does not spend an editorial model call on Markdown-only defects,
and an insufficient fallback has no citations.  Missing-section checks are
intent-specific: a question asking only for documents does not require an
unasked fee, deadline, form or procedure section.

The legacy no-basis detector is also scoped to the whole answer.  A localized
warning such as “Nguồn hiện có chưa nêu lệ phí” cannot discard a different
section that has a current-request citation.  The warning is preserved and the
normal strict citation/claim validator still runs; only an answer with no
grounded fragment is converted to the insufficient-evidence fallback.

The same intent-specific rule is rendered into the model contract: headings and
instructions are built from `required_sections`, so a documents-only request does
not prompt the model to invent authority, deadline or fee content.  If an
optional audit write stalls, it is bounded by
`LEGAL_AUDIT_WRITE_TIMEOUT_SECONDS` (default five seconds) and the validated
answer is still delivered.  The audit trace is reduced to statuses, counters and
timings before persistence.

## Local release evidence

Use the local-only launcher `scripts/start_all.ps1`. It starts SurrealDB,
retrieval, API and frontend on ports 8000, 8765, 5055 and 3000 respectively;
it does not start Docker or ngrok. The launcher now isolates the retrieval
sub-launcher so an already-running retrieval service cannot prevent API and
frontend from starting.

Generate performance evidence only after local readiness is green:

```powershell
python scripts/benchmark_section_grounding.py --concurrency 20 --mode warm --case simple_supported --case land_multi_issue --artifact reports/feature005/warm-c20.json
python scripts/scan_section_grounding_artifacts.py reports/feature005/warm-c20.json
python scripts/check_section_grounding_quality_gate.py reports/feature005/warm-c20.json
```

Run cold and warm samples for concurrency 1, 5, 10 and 20. Benchmark files are
aggregate-only and must pass the scanner before sharing. The runner reports
timing and status counts; it does not sign a legal-quality gate or store raw
question, answer, citation, credential or exception content.

The technical gate can return only `blocked` or `ready_for_legal_review`.
It never supplies legal-review approval itself, and blocks if the artifact is
not privacy-safe, requests failed, repair rate reaches 10%, or a warm p95
exceeds 15 seconds.

Browser journeys are in `frontend/e2e/section-grounding.spec.ts`. They require
an explicitly supplied local test-account storage state (`E2E_AUTH_STORAGE`)
and a flag-on isolated local runtime for structured cases. No credential or
storage state is committed. The legacy flag-off journey remains the rollback
check.
