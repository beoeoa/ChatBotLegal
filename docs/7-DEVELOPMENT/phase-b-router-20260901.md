# Phase B — Router + legal query plan (2026-09-01)

Rule-first conversation router, frozen pipeline contracts, Qwen 0.5B
constraints, and a private labeled dataset. Retrieval vNext, answer-model
swap, unit_primary, and LoRA/SFT are **not** in this change.

## What landed

- `api/pipeline_contracts.py`
  - Frozen `PipelineDecisionV1`: `conversation_route` ∈
    `{legal_query, document_followup, chat_meta, out_of_scope}`, plus
    `legal_issues`, `canonical_domain`, `facets`, `temporal_scope`,
    `active_document_id`, `confidence`, `source` (`rule|llm|fail_open`),
    `independent_queries`. **No** ACL / department / effectivity fields.
  - Frozen `AuthorizationScopeV1`: `role`, `primary_unit_id`,
    `grants_checksum`, `allowed_domains`. Built from a backend snapshot when
    present; otherwise from request role + `allowed_domains`. Conversation
    memory must not mutate it (`frozen=True` + tuple copy).
  - `decide_pipeline` / `pipeline_decision_from_router` map
    `unified_router.RouterDecision` → `PipelineDecisionV1`. For
    `legal_query`, the legal plan comes from `route_legal_answer` /
    `LegalQueryDecisionV1` **without** `account_domain` overwrite.
- Qwen 2.5 0.5B runtime (prompt/runtime only; **no LoRA/SFT**):
  - `prewarm_search_runtime` warms `CHAT_LLM_ROUTER_MODEL_ID`
    (`ollama:qwen2.5:0.5b`) with a tiny JSON classify ping
    (`num_ctx=1024`, `num_predict<=64`) **before** ready.
  - Readiness `warmup` now includes `router_llm: bool` next to
    `retrieval` / `ollama` / `completed`.
  - Router invoke: context 1024 tokens, output short JSON
    (`conversation_route` + `confidence`), timeout
    `CHAT_LLM_ROUTER_V2_TIMEOUT_SECONDS` (3s). Fail-open `legal_query`.
    Rule gates 1–5 still beat the LLM (`should_invoke_router_llm` only on
    fail-open remainders).
  - Schema forbids department / ACL / effectivity. If the model returns
    those keys, they are stripped.
- Dataset (private, already generated; holdout is eval-only):
  - `tests/fixtures/router_dataset_v1/{train,dev,holdout}.jsonl`
  - Generator: `scripts/generate_router_dataset_v1.py` (do not re-run just
    to retune prompts against holdout).
- Eval: `tests/test_router_dataset_v1.py` (rule + fail-open; no Ollama by
  default).

## Flags

| Flag | Default | Role in Phase B |
| --- | --- | --- |
| `CHAT_UNIFIED_ROUTER_V1_ENABLED` | `true` | serving uses `RouterDecision` |
| `CHAT_LLM_ROUTER_V2_ENABLED` | `false` | optional 0.5B refine after rule fail-open |
| `CHAT_LLM_ROUTER_MODEL_ID` | `ollama:qwen2.5:0.5b` | dedicated router model |
| `CHAT_LLM_ROUTER_V2_TIMEOUT_SECONDS` | `3` | router hard timeout |
| `CHAT_LLM_ROUTER_MAX_TOKENS` | `64` (capped at 64) | short JSON only |
| `CHAT_QWEN_WARMUP_ENABLED` | `true` | warms recommended local model **and** router 0.5B |
| `ROUTER_DATASET_RUN_LLM` | unset | eval-only; `1` may hit Ollama 0.5B |

## Dataset sizes

Source: `tests/fixtures/router_dataset_v1/summary.json` (generated 2026-09-01).

| Split | Rows |
| --- | ---: |
| train | 670 |
| dev | 161 |
| holdout | 177 |
| **total** | **1008** |

Routes: `legal_query` 684, `document_followup` 150, `chat_meta` 94,
`out_of_scope` 80. `needs_llm` 170. Tags include typo, abbrev, followup,
multi_domain, stale_memory, citizen, officer, exact, meta, oos. Real
identifiers used: 60/2014/QH13, 31/2024/QH15, 101/2024/NĐ-CP,
123/2015/NĐ-CP, 154/2024/NĐ-CP, 20/2021/NĐ-CP; domains
`ho_tich_chung_thuc` / `dat_dai_xay_dung` / `an_sinh_y_te_giao_duc`;
procedures khai sinh / kết hôn / sang tên đất / tạm trú.

Question md5 is split-private (no question leakage across splits). Holdout
was **not** used to tune the router prompt in this change.

## How to eval

Rule + fail-open (default, no Ollama):

```
.venv\Scripts\python.exe -m pytest tests/test_router_gold.py tests/test_unified_router.py tests/test_router_dataset_v1.py tests/test_conversational_orchestrator_v1.py tests/test_legal_answer_router_v2.py -q --tb=line
```

Optional 0.5B pass (local Ollama, after warmup):

```
set ROUTER_DATASET_RUN_LLM=1
.venv\Scripts\python.exe -m pytest tests/test_router_dataset_v1.py -q --tb=line -s
```

The harness prints accuracy, fallback rate, rule-path p95, and the count of
`legal_query` predicted as `chat_meta`/`out_of_scope`. It **fails** only on
the four hard gates above (plus rule-path p95 < 50ms). It does **not** fail
the suite when overall accuracy is below 99.5%.

## What is NOT done / not claimed

- No LoRA, no SFT, no fine-tune of qwen2.5:0.5b.
- **99.5% accuracy, fallback ≤ 1%, router P95 ≤ 1.5s warm are GO gates still
  to be measured on the private holdout with warm Ollama.** This change does
  not invent those numbers.
- No retrieval vNext, no answer-model swap, no `unit_primary` work.
- Phase A was skipped; this is Phase B only.
