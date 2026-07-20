# Pilot runtime stability - 2026-07-15

## Scope

This slice stabilizes the pilot runtime without rewriting the legal corpus or
rebuilding embeddings.

## Decisions

- Ask requests use a 60-second client timeout. Failed turns are persisted as
  assistant error messages so reloads do not leave an orphan user question.
- Conversation writes are retried once and assistant error messages are
  deduplicated by turn/idempotency metadata.
- Session `last_seen_at` is debounced (default 10 minutes) so ordinary API
  requests do not serialize on a database write. A failed telemetry touch does
  not invalidate an otherwise valid session.
- Runtime JSON data is resolved through `api.data_paths.notebook_data_dir()`.
  The Docker runtime prefers `/app/data`, while source checkouts use
  `notebook_data`. This keeps FAQ, forms, quality reports and procedures on the
  mounted persistent volume instead of a stale image copy.
- Internal PDF generation repairs legacy display text only at the read
  boundary. It does not rewrite legal metadata or source content.
- Admin user mutations require `X-Business-Reason`. The user-management UI now
  supplies this reason for update, deactivate/reactivate and password reset;
  the audit event stores the reason.
- The crawler remains candidate-first. Official sources run on their stored
  seven-day schedule, officer proposals stay pending, and admin approval is
  still required before import or RAG use.

## Verification

- Conversation/idempotency/auth tests pass.
- FAQ/form runtime and data-path tests pass.
- Citation viewer and generated PDF tests pass.
- Officer proposal, weekly crawler and realtime support tests pass.
- Frontend TypeScript check passes.
- Direct retrieval PDF smoke for document `402311`, article `16`, returns a
  valid PDF response.

## Operational note

The Docker service bakes application code into the image. Source changes only
become visible on port `8502` after rebuilding and recreating the
`open_notebook` service. The mounted `/app/data` directory is preserved.
