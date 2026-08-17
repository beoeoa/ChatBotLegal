# Frontend API candidate fallback

## Context

The local frontend can retain an `api-url-override` from an earlier test
session. If that override points to a retired port, the connection guard used
to stop immediately even when the local Next.js proxy and FastAPI backend were
healthy.

## Decision

`frontend/src/lib/config.ts` keeps the configured priority, but treats each API
URL as a candidate. It tries the override first, then runtime configuration,
the build-time environment value, and finally the relative Next.js rewrite.
An unreachable candidate is logged and skipped. The first successful `/api/config`
response becomes the active URL for the session.

This preserves explicit deployments while allowing stale local overrides to
recover through the local proxy (`/api/*` → `127.0.0.1:5055`).

## Verification

- `npm exec vitest run src/lib/config.test.ts` — 4 tests passed.
- `npm exec eslint src/lib/config.ts` — passed.
- Local browser verification: stale `127.0.0.1:5056` failed, then `/api/config`
  succeeded and the login screen rendered.
