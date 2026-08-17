# Legal document lifecycle management: Phases 0–1

## Outcome and boundary

Feature 014 adds an Admin-only, read-only management projection for the existing
legal stores. It intentionally stops before lifecycle schema, draft/version
mutation, approval, activation, archive, restore, vector cleanup or deletion.

The serving and legal-safety order remains unchanged:

```text
stored SQL/file identity
-> validity overlay
-> hierarchy and relationship filtering
-> final evidence ranking
-> parent hydration
-> model context
```

The new console observes this state; it does not become a new source of truth.

## Implemented architecture

- `scripts/legal_search_server.py` owns deterministic PostgreSQL projections for
  summary, metadata-only inventory and document detail. List responses use an
  allowlist and never transfer legal body or chunk text.
- `api/routers/legal_search.py` enforces Admin authorization before any store
  read, normalizes required-read failures, composes validity/crawl/audit
  evidence and redacts optional-store exceptions.
- `frontend/src/lib/api/legal-management.ts` exposes typed GET-only contracts.
- `/legal-management` provides summary, server filters, stable pagination and
  explicit unavailable states.
- `/legal-management/[id]` provides metadata, structure, validity/relationship,
  exact vector membership, FAQ availability, version availability and redacted
  audit tabs.
- Citizen and Officer navigation/direct API access remain denied. Existing
  public legal viewers keep their prior applicability behavior.

No lifecycle table or write endpoint was added. The relationship, immutable
version and FAQ-impact sections fail explicitly when their future schema is not
available.

## Deterministic quality and performance

Quality flags cover missing identity/source metadata, zero chunks, unknown
stored status and impossible date ordering. A century-scale issue-to-effect
gap is flagged for human review but never auto-corrected. This caught a live
record whose 1984 issue date was paired with a 2984 effective date.

The isolated 100,000-row PostgreSQL metadata benchmark uses a temporary table,
rolls its transaction back and does not read or write corpus rows. The final
run produced p50 32.48 ms, p95 47.17 ms and max 58.05 ms against the 1,000 ms
target. Search semantics are bounded: exact normalized law number and prefix
matching for title, agency and document type. A broad unindexed `%term%` scan
is deliberately not presented as the 100k target.

## Browser and authorization evidence

The live journey used `http://localhost:3000` with the supplied Admin account.
It initially observed 25,439 documents, opened
document `2099`, verified its official VBPL URL and inspected validity, vector
and FAQ tabs. The page showed no mutation/re-index controls and no console
errors. An isolated browser journey additionally proved every management API
request was GET, Citizen/Officer responses are denied, and unavailable
vector/FAQ sections are visible.

## Non-mutation evidence and concurrency limitation

The first PostgreSQL probe was later found to have inherited a staging database
override. The release gate was therefore re-baselined against the actual
`legal_chatbot` runtime. Before/after counts stayed at 25,440 documents,
232,968 articles, 545,376 chunks, 25,440 scope rows, 2,205 fields and 2,205
commune-field groups. The canonical metadata SHA-256 stayed
`3939ed57e08b924de8dbc6530302067f47da86f6a61558aed6c58c6ac4983b42`.

Pre-existing crawler/import/validity jobs continued to append operational
history independently of the GET-only feature. One pre-existing import
completed before the corrected runtime baseline, moving the live document count
from 25,439 to 25,440. The API was restarted with
`restart_local_api.ps1 -ReadOnlyGate`, which disables those workers only in that
process; the persistent `.env` was not changed. Across the corrected release
window, corpus/source/run/candidate/import/sync-run/decision counts stayed
fixed. A validity sync already running elsewhere appended observations and
events; no history was deleted or rewritten.

The already-open retrieval process reported stable vector totals of 160,828
fast/core and 431,863 expanded. The live detail projection performed exact
read-only membership checks; no vector was inserted, moved, deleted or
re-indexed.

## Rollout and rollback

Rollout is additive: deploy the retrieval routes, API routes, typed client,
Admin navigation and pages. Optional stores may remain unavailable without
breaking the core metadata view.

Rollback removes the navigation/pages and management GET routes, then restarts
the affected services. There is no down migration, corpus restore or vector
rebuild because none was created or mutated in Phases 0–1.

## Remaining gates

Explicit approval is required before any of the following:

- lifecycle/immutable-version schema and up/down migration;
- real corpus backfill or mutation;
- draft/review/approval/activation write workflow;
- historical vector architecture or real re-index/cleanup;
- Editor/Reviewer/Super Admin role mapping and two-person approval;
- new worker/daemon or paid external service;
- archive/restore and hard-delete policy.

The repository still reports npm audit findings (16 total at install time,
including one critical); no broad dependency remediation was attempted in this
feature because it is a separate release/security slice.
