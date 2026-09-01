# Unified router v1 (2026-09-01)

Rule-first serving router for Ask. One `RouterDecision` is computed before retrieval.

## Flags

| Flag | Default | Role |
| --- | --- | --- |
| `CHAT_UNIFIED_ROUTER_V1_ENABLED` | `true` | citizen, officer (admin stays off) |
| `CHAT_UNIFIED_ROUTER_V1_SHADOW` | `false` | compute + log only when unified is off |
| `CHAT_LLM_ROUTER_V2_ENABLED` | `false` | optional small-router refine |
| `CHAT_LLM_ROUTER_MODEL_ID` | `ollama:qwen2.5:0.5b` | reused; do not invent `CHAT_ROUTER_LLM_MODEL` |
| `CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ENABLED` | `false` | unchanged; unified serving does **not** require it |

When unified is on, Ask uses `RouterDecision` even if orchestrator v1 / LLM router v2 stay false.

## Gate order (must beat LLM)

1. History recall / chat_meta / capability
2. Out of scope, including smalltalk (`viết thơ`, thời tiết, `kể chuyện cười`, `cảm ơn nhiều nhé`)
3. Standalone law number / article / form code → `legal_query` (never `document_followup`)
4. Document deixis + `active_document` → `document_followup`
5. Insufficient facts → `legal_query` + `legal_route=clarification`, no retrieval
6. Optional V2 LLM only if rules fail-open **and** the remainder is short/ambiguous **and** `CHAT_LLM_ROUTER_V2_ENABLED=true`. Parse/confidence failure fail-opens to `legal_query`.

## Routes

- `chat_meta` / `out_of_scope`: stop. No retrieval, no legal planner.
- `document_followup` without active document: `ACTIVE_DOCUMENT_REQUIRED`, no retrieval.
- `document_followup` with active document: hydrate that document only; do not run the facet/issue planner.
- `legal_query`: M4 plan (issues, domain, facets, standalone queries, confidence), then retrieval/answer LLM as today.

## ACL after classify

Officer `allowed_domains` is a **backend** check after classification. `route_legal_answer` / `build_legal_query_decision` must not overwrite `canonical_domain` from `account_domain` (compatibility flag `apply_account_domain_acl=False` by default). If the classified domain is outside `allowed_domains` → HTTP 403 Vietnamese. Shared/cross-cutting documents are not a router concern.
