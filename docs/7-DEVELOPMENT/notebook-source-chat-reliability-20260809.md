# Notebook source and chat reliability (2026-08-09)

## Decision

Notebook-scoped source imports perform extraction in the active API request and
do not enable embedding automatically. Extracted `full_text` is sufficient for
notebook context and question answering; vector embedding remains an explicit,
optional action.

The synchronous source path calls `process_source_command` directly. It must not
submit a new Surreal Commands queue item and wait for an external worker that is
not part of the local runtime. The retry endpoint follows the same rule and has
a five-minute upper bound.

VBPL pages use the Playwright crawler because direct HTTP requests are commonly
rejected with HTTP 403. Playwright is therefore a runtime dependency. Local and
deployment setup must also install its Chromium browser binary.

Notebook chat sessions are owned by every authenticated account, including
admins. Legacy ownerless sessions are accessible only when their `refers_to`
notebook is resolved and owned by the same account.

Chat model provisioning and invocation run asynchronously on the API event
loop. If the default model is temporarily unavailable, configured language
models are tried deterministically with another provider preferred. If all
configured models fail, the response is a deterministic excerpt selected from
the notebook context; it does not invent legal material.

## Failure modes addressed

- Admin-created chat sessions were ownerless and immediately failed access
  checks; the router converted the intended 403 into an Axios 500 overlay.
- Notebook sources were queued without a local worker, leaving `full_text`
  empty indefinitely.
- The former “sync” path also queued a command and waited 300 seconds.
- Missing Python Playwright caused VBPL extraction to fall back to a 403 error
  string that was incorrectly saved as document content.
- The synchronous chat graph provisioned a model on a second event loop while
  reusing a database connection owned by the API loop.
- A shared free-model rate limit made the entire notebook chat fail even though
  other configured models were available.

## Verification

- Unit tests cover session ownership, legacy-session isolation, direct source
  retry, async chat invocation, provider fallback, and grounded excerpt fallback.
- The affected VBPL URL was reprocessed without embedding: 20,863 characters of
  legal text were extracted instead of the former HTTP 403 error string.
- A temporary owned chat session built 21,220 characters of notebook context
  and answered the live question with `Nghị định số 310/2026/NĐ-CP`; the session
  was deleted after verification.
