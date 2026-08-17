# AGENTS.md

Guidance for AI coding agents working in `J:\ChatBotLegal`.

## Project focus

This repository is a legal assistant for ward/commune-level administrative law in Hai Phong. The highest priorities are:

1. Do not fabricate legal sources, article numbers, dates, time limits, or fees.
2. Prefer effective legal documents and metadata-grounded answers over broad model creativity.
3. Keep retrieval and ingestion fast enough for local deployment.
4. Treat Hai Phong local documents as priority context, but never above higher-validity central law.

## Working style

This project adopts the useful parts of `addyosmani/agent-skills`:

- Spec before large changes.
- Small, reviewable implementation slices.
- Verification after each risky change.
- Document architectural choices that affect legal accuracy, ingestion, or retrieval.

For any non-trivial change, follow this lightweight cycle:

1. Clarify the goal and assumptions.
2. Identify touched modules and failure risks.
3. Implement the smallest working slice.
4. Verify with commands, API calls, or browser checks.
5. Record the decision if it changes behavior or architecture.

## Legal-specific boundaries

### Always do

- Preserve source URLs and legal metadata where available.
- Prefer deterministic parsing and filtering before LLM interpretation.
- Keep fallback behavior explicit when optional parsers or models are unavailable.
- Verify whether a change affects citizen role, officer role, or admin workflows differently.

### Ask first

- Schema changes that affect imported legal records.
- Changes that delete or rewrite existing legal corpora.
- New external services that require paid APIs or background daemons.

### Never do

- Invent legal citations to fill missing data.
- Silently replace official metadata with model-generated guesses.
- Remove safety checks around effective/expired documents without a stronger replacement.

## Code hotspots

- `api/routers/legal_search.py`: import, extraction, legal search API.
- `api/legal_crawl_service.py`: VBPL crawler, candidate review queue, admin notifications.
- `scripts/legal_search_server.py`: retrieval/indexing service.
- `frontend/src/app/(dashboard)/legal-import/page.tsx`: admin ingestion UI.
- `frontend/src/app/(dashboard)/search/*`: user-facing Q&A surfaces.

## Optional dependency policy

This project now allows optional advanced parsing adapters, especially from `external/RAG-Anything`.

Rules:

- Optional integrations must fail gracefully.
- Local-first defaults should keep working without heavy parsers.
- When a heavy parser is unavailable, return a clear fallback reason instead of crashing.

## Documentation rule

If a change affects ingestion strategy, retrieval architecture, answer grounding, or background jobs, add or update a doc under `docs/7-DEVELOPMENT/`.

<!-- SPECKIT START -->
Current Spec Kit implementation context:
`specs/018-production-release-readiness/plan.md`. Read that plan, its contracts,
research, data model, quickstart and tasks before changing production readiness,
support, lifecycle, dashboard, answer presentation or deployment behavior. For
procedure/form workflow, form selection, release manifests, coverage or Golden
V3, also read all design and task artifacts under
`specs/017-procedure-form-governance/`. Preserve the answer-trust contract from
spec 016, lifecycle baseline in specs 014-015, crawler governance from spec 007,
validity from spec 009, hierarchy from spec 010, parent hydration from spec 011,
multi-issue orchestration from spec 012 and role-scoped crawl/import behavior
from spec 013. Code, additive PostgreSQL migration definitions and isolated
rehearsal are approved; do not apply a live migration, mutate live source/corpus
history, move/delete/re-index real vectors, add a worker or paid service, switch
support/FAQ/vector/public pointers, activate production, run browser UAT, or
hard-delete records without the corresponding approved task slice and gates.
<!-- SPECKIT END -->
