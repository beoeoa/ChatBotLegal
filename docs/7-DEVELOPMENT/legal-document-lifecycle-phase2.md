# Legal Document Lifecycle Phase 2: Drafts and Immutable Versions

## Outcome

Feature 015 adds an isolated-ready workflow for legal-document drafts, proposed
immutable versions, distinct review and activation preview. The normal runtime
is intentionally fail-closed: Admin role alone grants no editor/reviewer
capability, lifecycle writes are disabled by default, and no live activation
adapter is configured.

## Architecture

- `api/legal_lifecycle_service.py` owns deterministic validation, canonical
  fingerprints, ETag revision checks, state transitions, idempotency,
  separation of duties and activation gates.
- `api/routers/legal_search.py` exposes `/api/legal/lifecycle/*`, normalizes
  errors and requires `Idempotency-Key`, `If-Match` and a bounded reason.
- Additive SurrealDB tables store operational draft/version/approval/manifest
  state. They are not read by any Citizen/Officer retrieval path.
- PostgreSQL and files remain corpus truth. Chroma remains a derived index.
- Approval creates an immutable proposed version and exact manifest projection;
  it never changes the active corpus in the default runtime.

## Capability model

The following process-local settings are required in a separately approved
environment:

```text
LEGAL_LIFECYCLE_WRITES_ENABLED=true
LEGAL_LIFECYCLE_EDITORS=user:<editor-id>
LEGAL_LIFECYCLE_REVIEWERS=user:<different-reviewer-id>
```

Admin membership does not imply either capability. A user appearing in both
lists still cannot review or activate a draft they submitted. Real IDs must not
be configured until the owner approves the organizational mapping.

`LEGAL_LIFECYCLE_ACTIVATION_ENABLED=true` is insufficient by itself: an
approved activation adapter, exact base fingerprint, ready manifest, SQL check,
every required collection count and a successful audit intent are also needed.

## State and failure rules

```text
draft / changes_requested
-> validate
-> draft | duplicate_review
-> submitted
-> approved | rejected | changes_requested
```

- Draft updates accept only the current revision.
- Submit accepts only validation produced for that exact revision.
- Duplicate number/source/hash evidence remains a candidate, never an automatic
  merge or legal relationship.
- Approved version snapshots have a content-addressed key and no update API.
- Base-document drift, incomplete manifest, audit failure, adapter failure or
  collection mismatch leaves the existing serving version unchanged.

## Migration and rollback

`scripts/manage_legal_lifecycle_schema.py` defaults to a non-mutating plan. Up
and down files live in `scripts/legal_lifecycle_migrations/`. The runner rejects
known live/default database names and requires a unique database plus
`--confirm-isolated`. Down refuses records unless the caller also supplies
`--allow-drop-records`; that flag is for isolated rehearsal only.

The 2026-08-10 rehearsal used a temporary SurrealDB memory process. Up/verify/
down passed for five empty tables. An adapter smoke then created one draft and
two idempotency records, validated it, explicitly dropped only that isolated
schema and stopped the temporary process.

Live rollback for this slice is simply disabling the two feature flags and
removing the additive routes/navigation. No live migration or activation was
performed, so PostgreSQL/files/vectors require no restore.

## Verification and limitations

- Backend regression: 77 passed.
- Frontend focused suite: 26 passed.
- Ruff, ESLint and TypeScript checks passed.
- Active counts/vectors match Feature 014: 25,440 / 232,968 / 545,376 and
  160,828 / 431,863.
- Browser E2E is authored. Direct acceptance also passed from a user-opened
  `localhost:3000` tab: Admin saw the closed write/account-mapping gates with
  no activation control, while a citizen and an officer were redirected to
  search without lifecycle record access.

Live migration, account mapping, real corpus activation, historical vectors,
new workers, backfill and deletion remain separately gated.
