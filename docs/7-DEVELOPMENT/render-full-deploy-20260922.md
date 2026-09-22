# Render full-system deployment preparation (2026-09-22)

## Goal and scope

Deploy the frontend, account and admin workflows, API, and legal search to Render in
Singapore. Use OpenRouter with `OPENROUTER_API_KEY` configured by the owner in
Render. The first
legal retrieval data set is the reviewed `commune-core-288-20260919` release;
expanding to the R28 national corpus is a separate release decision. Preserve the
source URLs, legal metadata, manifest checks, and document effectivity gates.

This is a deployment plan, not authorization to enable full public production.
The existing `docker-compose.release.yml` explicitly sets
`PUBLIC_QUICK_CHAT_ONLY_MODE=true` and its handoff README calls it a demo, not a
full production sign-off. Full-role traffic needs its own readiness and legal
answer verification before the public URL is shared.

## Current evidence

- Git remote: `https://github.com/beoeoa/ChatBotLegal.git`.
- The working tree has approximately 4,180 changed or untracked paths. The
  checked-out branch has one commit not yet pushed. A Render build from GitHub
  would therefore differ materially from the running local code.
- `.env.release` is ignored. `release-data/` is untracked and excluded by
  `.dockerignore`; Render's Git-backed Docker builds cannot see this data.
- The local release data totals about 6.3 GB, including backups and live
  PostgreSQL files that must not be copied into an image. The sanitized Render
  disk seed contains about 0.59 GiB of Chroma data and 1.12 GiB of model files.
  The legal-only PostgreSQL SQL is about 75 MiB.
- `python scripts/verify_core_288_release.py --release-root release-data`
  passed locally: 288 baseline documents, 11,040 baseline chunks, and a
  checksum-verified serving manifest.
- Current idle container memory was approximately 2.5 GiB for retrieval,
  0.2 GiB for API, 0.12 GiB for frontend, and 0.07 GiB for SurrealDB. These are
  observations, not load-test sizing guarantees.
- Render Dashboard is signed in to Phúc's Hobby workspace. GitHub repository
  `beoeoa/ChatBotLegal` is available to connect. The `chatbot` project has no
  active services, there are no Blueprint instances, and no payment card is on
  file. No Render CLI or Render MCP connection is available here.

## Proposed topology

All services and the database should use Render's Singapore region so private
traffic stays in one region and workspace. Only the frontend receives public
traffic; Next.js forwards API requests over the private network.

| Component | Render resource | Initial compute | Persistent data |
| --- | --- | --- | --- |
| Next.js frontend | Web service, Docker | 0.5 CPU / 512 MiB | None |
| FastAPI | Private service, Docker | 1 CPU / 2 GiB | 2 GB account/runtime disk |
| Legal retrieval | Private service, Docker | 2 CPU / 4 GiB, review under load | 10 GB reviewed release and writable Chroma disk |
| SurrealDB 2.6.5 | Private service, Docker | 0.5 CPU / 512 MiB, review under load | 2 GB RocksDB disk |
| PostgreSQL 18 | Render Postgres | 0.5 CPU / 1 GiB | 5 GB managed database storage |

The published Render prices for those compute sizes are roughly USD 143 per
month before persistent disks, bandwidth, build overages, tax, and model API
usage. The 14 GB of attached disks adds roughly USD 3.50 per month, giving a
baseline of about USD 146.50 plus database storage, bandwidth, tax, and
OpenRouter usage. The retrieval service may need an 8 GiB plan after load
testing, adding roughly USD 50 per month. A full deployment cannot rely on free
services because private services and persistent disks require paid compute.

## Data and secret handling

1. Keep `.env.release`, user data, corpus files, model weights, and database
   backups outside Git and Docker build contexts. Do not push them to a public
   container registry.
2. The original `legal-corpus.dump` contains local `users`, `conversations`,
   and `chat_histories` data (roughly 61 MB of chat-history SQL). **Never upload
   or restore that archive to Render.** The local staging script reads it and
   writes only an empty public schema plus 12 allowlisted legal data tables.
   An isolated PostgreSQL 18 restore of that output gave 290 legal documents,
   11,204 chunks, and zero users/conversations/chat-history rows.
3. Run `python -m scripts.stage_render_core_seed --output scratch/render-core-seed`
   and `python -m scripts.stage_render_postgres_seed --output
   scratch/render-pg-legal-only` from the repo root. The first output contains
   the manifest, baseline Chroma files, and embedding model, but no original
   PostgreSQL dump. Both outputs are local, ignored by Git, and must be checked
   against their receipts before transfer.
4. After provisioning and adding the operator's SSH public key to Render, run
   `powershell -File scripts/seed_render_retrieval.ps1 -RetrievalServiceId
   srv-...`. The script verifies the local seed, transfers only the four
   allowlisted release members with SCP, verifies them again on the private
   service, and creates `/data/legal/.render-seed-complete` last. It refuses to
   overwrite an activated corpus or an existing writable Chroma copy. The
   bootstrap service returns HTTP 503 until it verifies the reviewed manifest,
   Chroma checksum/counts, and model presence, then prepares the writable
   Chroma copy. The retrieval runtime independently checks the model
   fingerprint specified in the Blueprint.
5. Install and authenticate the official Render CLI, then run `powershell -File
   scripts/seed_render_postgres.ps1 -DatabaseId dpg-...`. It checks all three
   staged SQL hashes, restores only `extensions.sql`, `schema.sql`, then
   `legal-data.sql` through `render psql`, and prints legal and account counts.
   The Blueprint keeps Postgres external access closed throughout the restore.
6. Store API runtime files on its own persistent disk. SurrealDB uses a
   separate disk. The owner chose a fresh account database; import only the
   reviewed legal release and create a new admin during setup. Do not copy
   local user accounts, conversations, or sessions.
7. Create deployment secrets in Render, not in `render.yaml` or Git. The owner
   enters `OPENROUTER_API_KEY` in Render and selects an approved OpenRouter model
   in Admin after provisioning. The owner also sets the initial admin password
   in Render. Firebase is disabled for the initial deployment, so local citizen,
   officer, and admin accounts remain available without another external
   service. Enabling Google login later requires a reviewed Firebase client and
   Admin configuration at build and runtime.
8. Use database-native exports for PostgreSQL and SurrealDB recovery. Render
   disk snapshots alone are insufficient as a consistent database backup.

## Deployment slices and gates

1. Build the Render Docker images with internal service URLs, `PORT`, disk
   paths, and fail-closed private data bootstrap. The Blueprint draft is at
   `deploy/render/render.yaml` and initially keeps public maintenance mode on
   with automatic deploys disabled.
2. Validate the Blueprint against Render's current JSON Schema and run local
   build/start checks for each container. Validate the sanitized PostgreSQL
   restore in an isolated PostgreSQL 18 container.
3. Curate and review the exact Git commit to deploy; do not stage all current
   untracked files or include private release data. Push the deployment branch.
4. Connect the owner's Render account and review the monthly charge before
   creating the Blueprint resources.
5. Provision, transfer data privately, restore PostgreSQL, configure the
   OpenRouter model as default, and check `/ready` plus each service endpoint.
6. Test citizen, officer, and admin flows separately. Verify legal citations,
   source URLs, effectivity filtering, and safe fallback behavior before
   disabling public maintenance mode. Capture a rollback version and backup
   receipt. Turn on automatic deploys only after CI and role UAT pass.

Local preparation evidence on 2026-09-22:

- Next.js 16.3.3 frontend production and Docker builds passed with Firebase
  disabled and the private API host injected at build time.
- `npm audit --omit=dev` reported zero production vulnerabilities after moving
  Next.js to 16.3.3 and Sharp to 0.35.4.
- Seven focused account/login tests passed.
- API and SurrealDB Render images built and passed their startup checks.
- The retrieval image verified the staged release, loaded the expected model
  fingerprint, reported 11,040 indexed records, and returned healthy in full
  search mode. The legal-only PostgreSQL seed restored to 290 documents and
  11,204 chunks with zero user, conversation, and chat-history rows.

## Open decisions

- Exact paid plan sizes and monthly budget approval before provisioning.
- Whether a custom domain is required for the public frontend URL.
