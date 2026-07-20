# Docker release quickstart

## Maintainer

1. Copy `.env.release.example` to `.env` and fill deployment secrets locally.
2. Prepare a sanitized release-data bundle with `scripts/prepare_release_data.ps1`.
3. Start Docker Desktop.
4. Run:

```powershell
.\scripts\start_release.ps1 -Build
```

5. Verify `http://localhost:3000`, `http://localhost:5055/ready`, and
   `http://localhost:8765/health`.

## End user

The end user receives this repository plus the authorized release-data bundle.
They copy `.env.release.example` to `.env`, fill their own password/API key,
place the data under `release-data`, and run:

```powershell
.\scripts\start_release.ps1 -Build
```

The compose file maps the production frontend to port 3000 and keeps API,
retrieval, and SurrealDB on 5055, 8765, and 8000 respectively.

Never distribute `data/private`, account histories, support tickets, `.env`,
API keys, or unsanitized `surreal_data`.
