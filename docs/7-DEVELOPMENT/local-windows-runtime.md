# Local Windows runtime

Use the local runtime while developing and testing. It keeps the frontend,
backend, legal retrieval and database on the same source revision and does not
start Docker or ngrok.

## Start

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_all.ps1
```

Services:

- Frontend: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:5055`
- Legal retrieval: `http://127.0.0.1:8765`
- SurrealDB: `http://127.0.0.1:8000`

The script uses SurrealDB `2.6.5`, matching the existing database files. Do not
open `surreal_data` with a different major version.

`scripts/start_all.ps1`, `scripts/start_local.ps1` and the older
`start-services.ps1` all use this same local-only runtime. Do not run
`docker compose up` or start a second frontend/API while testing.

## Stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop_local.ps1
```

The stop script does not delete database, legal vector or notebook files.
