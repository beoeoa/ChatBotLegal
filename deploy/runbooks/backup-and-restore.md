# Feature 018 backup and restore

## Required backup set

One timestamped, immutable backup set must contain all of the following and a
SHA-256 inventory:

1. PostgreSQL custom-format dump (`pg_dump --format=custom`) for the legal
   corpus and Feature 017/018 governance tables.
2. Object/file data: approved form assets, support attachments and application
   data, preserving relative paths but never logging their contents.
3. Chroma/vector store copied only while retrieval is stopped or from a
   storage snapshot. Record collection/pipeline/embedding/validity fingerprints.
4. SurrealDB export or cold RocksDB snapshot. A raw RocksDB copy is permitted
   only after the Surreal service is stopped and the target is verified.
5. `release-data/manifest.sha256.json`, active release IDs and the Go/No-Go
   candidate fingerprint.

Use `scripts/backup_legal_retrieval.py` for PostgreSQL + Chroma after stopping
retrieval. Use `scripts/verify_surreal_restore_drill.py` to open a cold Surreal
copy on a separate port and compare schema/table counts. Object data should be
copied with `scripts/rehearse_feature018_restore.py --source ...` or an approved
snapshot tool that preserves relative paths and timestamps.

## Restore order

1. Create a new isolated target; never restore over an active database or
   vector directory.
2. Verify the backup inventory before opening any service.
3. Restore PostgreSQL with `pg_restore` into a new database and compare schema,
   row counts and governance pointer rows.
4. Restore Surreal into a new directory/port and run the existing inventory
   verifier.
5. Restore objects and vectors into new paths. Rebuild the read-only vector
   serving manifest and require the recorded fingerprints to match.
6. Start API/retrieval/frontend only on the isolated network. Run `/health`,
   `/ready`, answer/form smoke tests and pointer reconciliation.
7. Record checksums, counts, RPO/RTO and reviewer in a release-evidence report.

No restore rehearsal authorizes a production pointer switch. A mismatch is a
No-Go and the original stores remain untouched.
