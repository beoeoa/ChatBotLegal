# PostgreSQL retrieval indexes: migration and operating guide

## Decision and scope

This change adds derived PostgreSQL indexes for the existing legal lexical
retrieval query. It does not change ranking, grounding, source metadata, API
behavior, frontend behavior, or any legal-corpus row.

The migration mirrors the current expressions of the retrieval query:

| Table | Indexed value | Type | Purpose |
|---|---|---|---|
| `legal_article_chunks` | `LOWER(COALESCE(content, ''))` | GIN trigram | Leading-wildcard content match |
| `legal_article_chunks` | `LOWER(COALESCE(heading, ''))` | GIN trigram | Leading-wildcard heading match |
| `legal_articles` | `LOWER(COALESCE(title, ''))` | GIN trigram | Article-title match |
| `legal_documents` | `LOWER(COALESCE(title, ''))` | GIN trigram | Document-title match |
| `legal_documents` | `LOWER(COALESCE(law_number, ''))` | GIN trigram | Law-number match |
| `legal_article_chunks` | `(article_id, id)` | B-tree | Chunk-to-article join |
| `legal_articles` | `(document_id, id) WHERE status='active'` | Partial B-tree | Active article-to-document join |

`pg_trgm` supports indexed `LIKE`/`ILIKE` searches. The migration leaves that
extension installed during rollback because an extension is database-wide and
may be used by objects outside this migration.

## Safety properties

- `--plan-only` is a true offline mode. It validates and prints the migration
  without resolving a database URL or opening a connection.
- With no action flag, the runner is a dry run. It performs preflight checks
  and prints the planned up migration, but executes no migration statement.
- Apply and rollback require the explicit `--apply` or `--rollback` flag.
- The connection uses `AUTOCOMMIT`; every `CREATE/DROP INDEX CONCURRENTLY`
  statement is sent separately, outside a transaction block.
- The runner validates PostgreSQL, autocommit, required tables/columns,
  `pg_trgm` availability, recovery state, target index validity, and existing
  index definitions before mutation.
- A failed apply never triggers an automatic rollback. Concurrent builds are
  not atomic as a group, so the operator must inspect the failure first.
- The down migration only drops the seven indexes named by the up migration.
  It does not drop tables, the `pg_trgm` extension, or legal data.
- A name collision with an unexpected index definition blocks both apply and
  rollback so the runner cannot silently keep or remove an unrelated object.

PostgreSQL documents both the autocommit restriction and the fact that
`IF NOT EXISTS` alone does not prove an existing index has the intended
definition. See the official [`CREATE INDEX` documentation](https://www.postgresql.org/docs/current/sql-createindex.html)
and [`pg_trgm` documentation](https://www.postgresql.org/docs/current/pgtrgm.html).

## Before applying

1. Confirm a current database backup/snapshot and normal restore procedure.
   The migration is data-preserving, but this remains a production change.
2. Check free disk space. GIN indexes increase storage and ongoing write cost.
3. Choose a low-traffic window. `CONCURRENTLY` keeps normal reads/writes
   available, but building seven indexes still consumes CPU, I/O, and time.
4. Set the same PostgreSQL URL used by Legal Search. Prefer the environment
   variable so credentials are not saved in shell history:

   ```powershell
   $env:LEGAL_DATABASE_URL = "postgresql+psycopg2://<user>:<password>@<host>:<port>/<database>"
   ```

## Offline plan

Use this mode for static validation on a developer machine or CI worker that
must not reach PostgreSQL:

```powershell
python scripts/manage_postgres_retrieval_indexes.py `
  --plan-only `
  --report reports/postgres-retrieval-index-offline-plan.json
```

Expected result: `mode` is `plan-only`, `database_connection_opened` and
`executed` are `false`, and `planned_statements` is `8`. The output also shows
that the owned down migration passed static validation. This validates the
allowed SQL operations and symmetric owned-object set, but it is not evidence
that the target schema or database permissions are ready.

## Connected dry run (default)

After setting `LEGAL_DATABASE_URL`, run without an action flag:

```powershell
python scripts/manage_postgres_retrieval_indexes.py
```

Expected result: `mode` is `dry-run`, `executed` is `false`, preflight is
`ok`, and `planned_statements` is `8` (one extension check/create statement
plus seven concurrent index statements). No `EXPLAIN ANALYZE` is run in this
mode, because it executes the benchmark `SELECT` and may be expensive.

## Apply and measure

Use a stable, representative term for a comparable before/after plan:

```powershell
python scripts/manage_postgres_retrieval_indexes.py `
  --apply `
  --benchmark-term "khai sinh" `
  --report reports/postgres-retrieval-index-apply.json
```

The apply workflow is:

1. Preflight.
2. `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` before the migration.
3. Execute each up statement separately in autocommit mode.
4. Run the identical `EXPLAIN ANALYZE` after the migration.
5. Print the result and optionally write the JSON report.

The report includes planning time, execution time, indexes selected by the
planner, and the complete plan tree. `EXPLAIN ANALYZE` executes only a bounded,
read-only `SELECT`; it never changes corpus rows.

Compare repeated runs under similar load. A planner may choose a sequential
scan for small tables or a joined `OR` query even when valid indexes exist, so
the presence of an index is not itself proof of a latency improvement. A query
rewrite, cache, or retrieval-server change must be handled as a separate,
specified feature.

## Failure handling

If a concurrent build fails, PostgreSQL can leave an invalid index. The runner
will stop future dry-run/apply attempts and list the invalid target. It will not
remove anything automatically.

After reviewing the database error and confirming that cleanup is intended,
the explicit rollback path is allowed to remove an invalid target only when
its definition still matches an object owned by this migration:

```powershell
python scripts/manage_postgres_retrieval_indexes.py `
  --rollback `
  --benchmark-term "khai sinh" `
  --report reports/postgres-retrieval-index-rollback.json
```

Rollback also records before/after plans. It leaves `pg_trgm` installed and
does not restore or rewrite corpus data because the up migration never changed
that data.

## Contract verification

These tests are fully mocked/static and do not connect to PostgreSQL:

```powershell
python -m pytest -q `
  tests/test_postgres_retrieval_index_migration.py `
  tests/test_postgres_retrieval_index_runner.py
```

They lock the concurrent-index syntax, owned rollback set, autocommit engine,
dry-run default, explicit mutation modes, preflight collision handling,
before/after benchmark order, and absence of automatic rollback.
