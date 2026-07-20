# PostgreSQL staging retrieval rehearsal

## Scope

The local PostgreSQL 18 service has an isolated database named
`chatbotlegal_retrieval_staging` and a dedicated non-superuser login role. Its
connection URL is stored only in the current Windows user's
`LEGAL_DATABASE_URL` environment variable. It must never be copied into source,
evidence, chat logs, Docker images, or production configuration.

## Safety boundary

This database is intentionally empty. No legal corpus, user data, conversation,
or credential was copied into it. A connected dry-run of
`scripts/manage_postgres_retrieval_indexes.py` therefore blocks at schema
preflight. This is the expected safe result: a concurrent index migration must
not be applied to a target that is not production-equivalent.

The privacy-safe blocked-preflight evidence is at
`specs/002-accelerate-legal-retrieval/evidence/postgres-staging-preflight-2026-07-17.json`.
The offline migration and rollback validation is at
`specs/002-accelerate-legal-retrieval/evidence/postgres-index-plan-2026-07-17.json`.

## Future rehearsal requirements

Before an index rehearsal can proceed, an operator must provide an explicitly
approved, non-production PostgreSQL dataset with the legal metadata schema and
representative non-sensitive data. Then run in this order:

1. Connected dry-run and `EXPLAIN ANALYZE` preflight.
2. Separate operator approval checkpoint.
3. Concurrent apply, post-check, and verified rollback rehearsal.

Do not use the staging database to create, rewrite, or copy the legal corpus.
