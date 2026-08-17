# Notebook note embedding fallback (2026-08-09)

## Decision

Saving a notebook note is the primary user action. Enqueuing `embed_note` is an
optional background enhancement and must not change a successfully persisted
note into an HTTP 500 response.

`Note.save()` therefore persists the note first, then attempts to enqueue its
embedding. If the job queue or its database credentials are unavailable, the
method logs a warning and returns `None`. The notes API continues assigning the
owner and linking the note to its notebook. Embedding can be retried later.

The create-note frontend mutation explicitly disables automatic retries because
POST `/notes` is not idempotent. A user can retry manually after a visible error.

## Reason

Previously, a queue authentication failure happened after the note record was
created. The API returned 500, and the frontend mutation retry created a second
orphan note while still reporting failure to the officer.

## Verification

- Unit coverage confirms persistence succeeds when command submission raises.
- API and browser smoke tests confirm the created note is owned, linked, and
  visible in the notebook.
- Temporary smoke-test accounts, notebooks, notes, relations, and sessions are
  removed after verification.
