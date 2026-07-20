# Migration 27 — Chat Platform Schema Baseline

**Date:** 2026-07-11  
**Scope:** Additive SurrealDB schema for persistent chat, live support, legal cases, sensitive-access audit, officer document proposals, and user profile assignment fields.  
**Safety:** No existing tables/data are rewritten or deleted by the migration itself. Legacy JSON storage remains the runtime source until a later application cutover.

## Files

- Up: [`open_notebook/database/migrations/27.surrealql`](../../open_notebook/database/migrations/27.surrealql)
- Down: [`open_notebook/database/migrations/27_down.surrealql`](../../open_notebook/database/migrations/27_down.surrealql)
- Registry: [`open_notebook/database/async_migrate.py`](../../open_notebook/database/async_migrate.py)
- Backfill: [`scripts/backfill_chat_platform_ownership.py`](../../scripts/backfill_chat_platform_ownership.py)
- Profile payload support: [`api/user_service.py`](../../api/user_service.py)

## Schema added

### `user_profile` extensions
- `ward`
- `ward_scope`
- `allowed_domains`
- `must_change_password` (default `false`)

`api/user_service.py` now reads/writes these fields so create/update account paths do not drop them.

### `conversation`
- `owner_user` (optional only for unresolved backfill)
- `role_context`
- `domain`
- `title`
- `status`
- `ownership_status` (`resolved` | `needs_admin_review`)
- `legacy_source_path`
- `created_at`
- `last_message_at`
- `expires_at` (chat retention baseline: 12 months)

### `conversation_message`
- `conversation`
- `sender_user`
- `sender_role`
- `content`
- `attachments`
- `citations_snapshot`
- `created_at`

### `support_session`
- `citizen`
- `officer`
- `conversation`
- `domain`
- `queue_status`
- `ownership_status`
- `legacy_source_path`
- `assigned_at`
- `closed_at`
- `resolution_note`

### `legal_case`
- `owner_user`
- `assigned_officer` (nullable)
- `status`
- `ownership_status`
- `legacy_source_path`
- `closed_at`
- `expires_at` (case attachment retention baseline: 6 months after close)

There is intentionally no shared default legal-case workspace. Cases are account-scoped.

### `sensitive_access_audit`
- `actor`
- `resource_type`
- `resource_id`
- `reason`
- `action`
- `request_metadata`
- `created_at`

Application code in later steps must create an audit row before opening sensitive cross-account content. Migration 27 only creates the table/indexes.

### `document_candidate`
- `submitted_by`
- `domain`
- `source_type`
- `source_url`
- `title`
- `review_status`
- `ownership_status`
- `legacy_source_path`
- `ocr_score`
- `ai_relevance_score`
- `ai_compatibility_score`
- `ai_assessment`
- `rejection_reason`
- `reviewed_by`
- `reviewed_at`

This table is separate from `legal_crawl_candidate` so the existing crawler review queue is never rewritten by this step.

## What is intentionally not changed

- Existing `data/ask_sessions/**` JSON remains readable by the current Ask UI.
- Existing `data/support_tickets/**` JSON remains readable by the current live-support router.
- Existing notebook/source/chat_session ownership fields from migration 16 are left intact.
- Existing `legal_crawl_candidate` approval workflow remains intact.
- No passwords are generated or forced-changed by this migration.

## Apply migration

Migration runs automatically on API startup via `AsyncMigrationManager.run_migration_up()`.

Manual apply:

```powershell
python -c "import asyncio; from open_notebook.database.async_migrate import AsyncMigrationManager; asyncio.run(AsyncMigrationManager().run_migration_up())"
```

Expected after apply:

```text
Current version before migration: 26
Running migration 27
Migration successful. New version: 27
```

## Safe ownership backfill

Backfill is separate from schema migration and defaults to dry-run.

```powershell
# Report only
python scripts/backfill_chat_platform_ownership.py --dry-run

# Write rows after reviewing the report
python scripts/backfill_chat_platform_ownership.py --apply
```

Rules:

1. If owner resolves to an existing `user_account` record, create a normal `resolved` row.
2. If owner is missing, `legacy:*`, `user:*`, or does not exist, create `ownership_status=needs_admin_review` and **do not invent an owner**.
3. Original JSON files are never deleted, rewritten, or moved.
4. Re-running is idempotent through `legacy_source_path`.

Report path:

```text
notebook_data/chat_platform_ownership_backfill_report.json
```

## Rollback

### Schema rollback

Only after exporting any new tables that must be retained:

```powershell
python -c "import asyncio; from open_notebook.database.async_migrate import AsyncMigrationManager; m=AsyncMigrationManager(); asyncio.run(m.runner.run_one_down())"
```

`27_down.surrealql` removes only the tables/fields introduced in migration 27:

- drops `document_candidate`, `sensitive_access_audit`, `legal_case`, `support_session`, `conversation_message`, `conversation`
- removes `user_profile.must_change_password`, `allowed_domains`, `ward_scope`, `ward`

It does **not** touch:

- JSON ask sessions
- JSON support tickets
- crawl candidates
- notebooks/sources/notes
- user accounts/sessions

### Backfill rollback

If `--apply` was used and the generated rows must be removed without full schema rollback:

```surql
DELETE conversation WHERE legacy_source_path IS NOT NONE;
DELETE conversation_message WHERE conversation.legacy_source_path IS NOT NONE;
DELETE support_session WHERE legacy_source_path IS NOT NONE;
DELETE legal_case WHERE legacy_source_path IS NOT NONE;
```

Prefer exporting those rows first if admin still needs the review queue.

## Validation checklist

1. Migration version becomes 27.
2. `INFO FOR DB;` / table queries show the seven new tables/fields.
3. Dry-run backfill reports legacy sessions/tickets as `needs_admin_review` when owners are `legacy:*`.
4. No original JSON file is modified.
5. Creating/updating a user profile still returns `ward_scope`, `allowed_domains`, and `must_change_password`.

## Follow-up work (not this step)

- Application cutover from JSON ask-sessions/support tickets to Surreal tables.
- Enforce mandatory business reason + immutable audit before admin opens another account's chat/case.
- Retention jobs for 12-month chat, 6-month closed-case attachments, 24-month audit logs.
- First-login force password change using `must_change_password`.
