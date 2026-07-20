# Portable legal PostgreSQL release

The Docker release isolates its legal corpus in the `legal_postgres` service.
Only the seven tables required by retrieval are exported: documents, articles,
chunks, document relationships, fields, search scope and commune field groups.
User accounts, conversations, notebooks, API keys and audit records are never
included.

On the source machine, set `LEGAL_RELEASE_DATABASE_URL` only in the local
environment, then run:

```powershell
.\scripts\export_legal_postgres_release.ps1
```

This creates the ignored file `release-data/legal-corpus/legal-corpus.dump`.
On first Docker start, the official PostgreSQL image restores that file into an
otherwise empty database. The data volume is then persistent and the seed is
not replayed on future starts. The file must travel with the release archive,
not in Git history. Verify its SHA-256 before handing it over.

Rollback is `docker compose down -v` followed by restoring a known-good corpus
seed; legal source records are never mutated by the retrieval container.
