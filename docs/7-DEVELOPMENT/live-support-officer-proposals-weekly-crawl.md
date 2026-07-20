# Live support, officer proposals, and weekly legal crawl

## Decision

- Live support is a citizen-to-officer channel. Admin may retain audited,
  read-only oversight through backend administration, but cannot join realtime
  sessions or send chat messages.
- Tickets are routed only through the five canonical staffed domains. Officers
  can see and claim only tickets in their assigned domains.
- The browser connects WebSocket traffic directly to FastAPI. Local Next.js
  rewrites remain for HTTP but are not relied on for WebSocket upgrades.
- Officers can propose a URL or upload PDF/DOCX/TXT/MD. Extraction and URL
  preview are review aids only; the result becomes a pending candidate.
- Only admin can approve a candidate for later import and embedding.
- Default VBPL sources run every 10,080 minutes (seven days). The scheduler may
  check hourly, but a source is scanned only when its own interval is due.
- The former 1,440-minute default is migrated to weekly. Custom admin intervals
  are preserved.

## Demo records

`scripts/seed_weekly_crawl_demo_candidates.py` creates exactly two idempotent,
metadata-only demo candidates. They are marked `is_demo`, remain pending, and
have `public_rag_allowed=false`. They must never be used as legal evidence.

## Review workflow UI

- `/officer-proposals` shows the submitting officer's own proposal history,
  including `changes_requested` feedback. It never exposes crawler candidates
  or proposals submitted by another officer.
- `/legal-import` lets admin filter crawler discoveries versus officer
  proposals, open the original source, inspect metadata diffs, request changes,
  reject, assess, or approve.
- Admin can inspect every configured crawler source, enable/disable it, run one
  source immediately, or force a manual scan of all enabled sources.
- A manual scan is distinct from the scheduler: manual means run now, while the
  scheduler continues to honour each source's seven-day due time.
- Source configuration changes, manual scans, and review decisions are audited.

## Verification

- Live-support integration tests cover domain privacy, realtime delivery, and
  denial of admin messaging.
- Crawler tests cover the weekly default and preservation of custom schedules.
- Officer proposal tests verify candidate-first behavior and admin-only review.
- Frontend tests/build verify the new officer page and direct WebSocket client.
