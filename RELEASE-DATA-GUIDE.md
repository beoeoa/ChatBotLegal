# Release data bundle

The release compose file deliberately keeps runtime data outside Git and outside
the application image. Populate these directories from a reviewed, sanitized
data bundle before starting:

```text
release-data/legal/chroma_store       # Chroma collections and metadata
release-data/legal/legal_search.db    # retrieval metadata database, if used
release-data/legal-corpus/legal-corpus.dump # reviewed PostgreSQL legal corpus
release-data/postgres/                # created automatically on first run
release-data/surreal_data/             # seeded SurrealDB data
release-data/notebook_data/            # clean application data only
release-data/forms/                    # forms approved for distribution
```

Do not copy `data/private`, `data/ask_sessions`, `data/support_tickets`, API
keys, `.env` files, browser profiles, logs, or user uploads into this bundle.
Create a SHA-256 manifest for every archive before distributing it.

The corpus and forms must be legally distributable. If they cannot be shared,
distribute the application image and provide a private retrieval-data download
to authorized users instead.
