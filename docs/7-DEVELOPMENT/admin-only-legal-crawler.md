# Admin-only legal crawler

## Decision

The legal crawler is one shared system capability operated by Admin. Officers do
not have a separate crawler, URL-preview action, weekly crawl monitor, crawler
source list, or access to candidates produced by the shared crawler.

Officers retain the manual proposal workflow. They may submit an official URL,
file, or pasted content in an assigned domain through `/api/legal/proposals` and
may view only their own proposal history through
`/api/legal/proposals/candidates`. These records remain pending and non-serving
until Admin verifies the source and completes the normal review/import process.

## API and UI boundary

- Removed Officer routes: `POST /api/legal/proposals/preview` and
  `GET /api/legal/proposals/weekly-monitor`.
- Preserved Officer routes: `POST /api/legal/proposals` and
  `GET /api/legal/proposals/candidates`.
- Preserved Admin crawler namespace: `/api/legal/crawl/*`, including the Admin
  preview, source controls, manual scans, notifications, review, and import.
- The Officer page contains no crawl button or crawler status. Its URL input is
  evidence for the proposal only and does not trigger a fetch.

## Safety and duplication behavior

Automatic discovery is deduplicated in the shared Admin pipeline. A manual
Officer proposal can still point to a document already discovered by the
crawler; this is intentional review evidence, not a second crawling job. The
existing duplicate checks must reconcile it before activation, and neither path
may approve, embed, publish, or serve a document automatically.

## Rollback and data impact

This change removes only Officer UI/API crawl surfaces. It introduces no schema
migration, deletes no source, candidate, crawl history, legal corpus, or vector,
and does not alter the Admin scheduler. Rollback is code-only restoration of the
removed Officer surfaces, although doing so would reintroduce duplicate crawler
responsibilities and requires a new product decision.
