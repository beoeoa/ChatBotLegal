# Legal Grounding Runtime Notes

## Status
Accepted

## Date
2026-06-18

## Context
The Hai Phong legal assistant must not fabricate legal sources, article numbers, deadlines, fees, agencies, or required documents. During runtime smoke testing, the offline Ollama answer path could answer from weak retrieval or turn missing information into a stronger claim.

## Decisions
- `/api/search/ask/simple` supports `offline_mode=true` without requiring cloud model IDs.
- The offline legal answer path checks whether retrieved legal evidence is strong enough before calling the local LLM.
- If retrieval is empty or too weak, the API returns an explicit fallback instead of asking the LLM to infer an answer.
- The local prompt tells the model not to convert "source does not state this" into claims such as "no dossier required", "no fee", or "no deadline applies".
- `legal-quality` reads evaluation files from the runtime data directory first (`/app/data` in Docker), then falls back to repository-local data paths.
- Chroma only discovers vector candidates. PostgreSQL `legal_commune_field_groups.group_slug` plus the reviewed `legal_search_scope` is the authoritative domain filter.
- A domain must never be mapped to one hard-coded `field_id`; each commune domain contains many legal fields and old Chroma metadata may be stale.
- Ask citations accept structured `source_metadata`; response serialization must not fail after an answer has already been generated.

## Verification
- Docker image rebuild passed with frontend production build.
- Runtime smoke after rebuild passed for:
  - `/health`
  - `/api/auth/status`
  - `/api/auth/login` with password `gfi`
  - `/api/users/me`
  - `/api/legal/health`
  - `/api/legal/quality/summary`
  - `/api/legal/crawl/summary`
  - `/api/settings`
  - `/api/search/local-models`
  - `/api/search/ask/simple` in offline mode using `qwen2.5:3b`
- `python scripts/validate_legal_golden_set.py` passed before rebuild and should remain a required gate.

## Follow-up
- Run full retrieval and role-answer evaluations after any substantial corpus/index change.
- Browser-smoke the dashboard pages before release.
- Rebuild Docker after source changes; avoid relying on container hot patches for release state.
