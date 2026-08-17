# Ask progress SSE and local model fallback

Date: 2026-07-16

## Scope

This change adds an opt-in progress transport around the existing validated
`AskResponse`. It does not replace `/api/search/ask/simple`, change the legal
corpus, or expose an unvalidated model draft.

Configuration:

- `LEGAL_ASK_PROGRESS_ENABLED=false` keeps the new endpoint unavailable by
  default.
- `LEGAL_LOCAL_FALLBACK_ENABLED=true` permits the reviewed cloud-to-local
  fallback policy.
- The fallback model is fixed to `qwen2.5:3b` for this rollout.

## Progress contract

`POST /api/search/ask/progress` uses `text/event-stream` and emits:

1. `accepted`: trace ID, conversation ID and idempotency key.
2. `status`: only `retrieving`, `generating`, `validating` or `persisting`.
3. `sources`: citations already accepted by the normal grounding pipeline.
4. `final`: one complete, validated `AskResponse` after audit and conversation
   persistence have been attempted.
5. `error`: a structured safe error code, retryability and trace ID.
6. `complete`: total duration and outcome.

The transport does not accept or emit `token`, `draft` or partial answer
events. Non-admin roles have `rag_trace` removed again at the SSE boundary,
even if an internal caller accidentally returns one. Source payloads are
allow-listed and do not contain question text, answer text, credentials or
model tokens.

### Frontend transport and legacy compatibility audit

When `NEXT_PUBLIC_ASK_SSE_ENABLED=true`, the active streaming consumer
`frontend/src/lib/api/search.ts::askKnowledgeBaseStream` opens
`POST /api/search/ask/progress`. Its parser accepts lifecycle events and a
structured `final.response`; it deliberately discards `strategy`, `answer`,
`draft`, `token` and `final_answer` shapes. If the progress route is rejected
before a response body opens (for example, because a role rollout flag is
still disabled), the hook falls back once to `/api/search/ask/simple` with the
same role and idempotency key. It never retries after the stream opens.

The older raw-body helper `askKnowledgeBase` has no in-repository caller. The
legacy `/api/search/ask` route remains available for compatibility and is not
used by the Feature 3 progress client.

The public legacy URL is therefore retained, but now runs the same canonical
`_execute_ask_simple` pipeline as `/simple` and `/progress`. Its data-only SSE
envelope (`data: {"type": ...}`) remains compatible with the current frontend,
while server-side output is restricted to `accepted`, `status`, verified
`sources`, structured validated `final`, safe `error`, and `complete`. Several
older Step 8 diagnostic scripts still parse `final_answer`, so the compatibility
envelope also emits that deprecated alias **after** `final`, with
`validated=true` and exactly the already-validated final text. It is not a
model draft and cannot appear before validation. The response advertises
`X-Ask-Contract: validated-final-v1`.

The former graph-stream implementation is no longer routed and its internal
helper no longer emits strategy or per-source answer drafts. The frontend also
forces `show_rag_trace=false` for citizen/officer calls; admin requests must
explicitly opt in, with server-side authorization remaining authoritative.

If the client omits an idempotency key, the server creates one and returns it
in `accepted`. A retry should reuse that key. Client cancellation cancels the
Ask task, records a `cancelled` stage outcome and does not persist an assistant
error message.

## Fallback decision

Cloud remains the primary generator. Local fallback is allowed only after a
classified provider timeout, connection failure, rate limit, or HTTP
500/502/503/504 availability failure. Authentication, configuration, input and
other non-transient errors keep their original response.

The execution boundary catches both the application's normalized provider
exceptions and raw `httpx.HTTPError` subclasses. This is intentional: some
provider adapters pass through `ReadTimeout`, `ConnectError` or
`HTTPStatusError` directly. Every raw exception still goes through the same
allowlist classifier, so a provider 4xx cannot silently enable local fallback.

`LegalRetrievalUnavailableError` and `insufficient_evidence` are hard
exclusions. The local model never substitutes for missing legal evidence. The
local path repeats the same retrieval-sufficiency, citation and sensitive-claim
validation and returns the normal safe `insufficient_evidence` response before
calling Ollama when evidence is inadequate.

A successful fallback is marked with the non-fatal
`error.code=LOCAL_MODEL_FALLBACK` and `quality_flags=local_model_fallback` for
operator visibility without exposing provider exception text.

### Windows local runtime binding (2026-07-30)

`scripts/start_local.ps1` binds `OLLAMA_URL` to
`http://127.0.0.1:11434` for its host-process API.  The application default
`host.docker.internal` remains appropriate for container deployments, but is
not reachable from every direct Windows host process.  This launcher-only
binding keeps the reviewed `qwen2.5:3b` fallback available after a transient
cloud-provider failure; it does not alter the primary provider, corpus or
persisted rollout flags.

### Default Ask model decision (2026-07-30)

The release-quality run showed repeated 60-second V4-Pro generation timeouts
while retrieval was healthy.  The existing, authenticated DeepSeek V4-Flash
language-model record was therefore assigned to **`default_chat_model` only**.
`default_transformation_model` and `large_context_model` remain V4-Pro.  This
is a reversible provider-performance configuration change, not a legal-data
or approval change: restore `default_chat_model` to
`model:xsgzui5t2e2tftag48m1` to revert.  It must be accepted only after a clean
five-domain quality run; otherwise the release remains blocked.

### Citation provenance repair (2026-07-30)

The five-domain run exposed two corpus citations whose law number and official
VBPL URL did not identify the same document.  `scripts/repair_verified_legal_source_urls.py`
uses exact expected prior URLs, active-status checks and an audit report before
changing only those URLs.  It corrected the source records for Law
60/2014/QH13 (Hộ tịch) and Law 02/2011/QH13 (Khiếu nại) after direct official
VBPL verification.  It does not change legal text, effectivity, embeddings,
approval state or forms.  A changed prior URL fails closed and requires a new
human verification rather than an automatic overwrite.

### Exact-provision answer fallback (2026-07-30)

For an explicit legal provision, retrieval now retains up to two bounded chunks
of that requested article before unrelated articles use the response budget.
This preserves split provisions such as the dossier paragraph and subsequent
processing paragraph of one article. The per-document cap remains three and
the ordinary active-source, scope and effectivity filters still apply.

If a generator omits the direct outcome even though those retrieved chunks
contain it, Ask can return at most two source-verbatim passages for the
requested authority, submission-place or dossier facet. It never synthesizes
an additional legal fact. A generic mention of an agency cannot displace a
direct competence passage, and an absent source still produces the normal
insufficient-evidence behavior. This is intentionally a response-layer safety
fallback; it does not mutate corpus content, source metadata or form approval.

## Compatibility and rollback

- `/api/search/ask/simple` retains its request and response contract.
- `/api/search/ask` retains its URL and SSE transport but intentionally removes
  the unsafe pre-validation event shapes. The current frontend requires the
  replacement structured `final`; old diagnostic scripts remain functional
  through the post-validation `final_answer` alias.
- Disable progress immediately with `LEGAL_ASK_PROGRESS_ENABLED=false`.
- Disable local fallback independently with
  `LEGAL_LOCAL_FALLBACK_ENABLED=false`.
- Removing the new route/helper requires no data migration or corpus rewrite.
  Existing idempotency and conversation records remain readable.

## Verification

- Contract tests cover event order, no draft/token events, structured errors,
  trace authorization, cancellation, and an invalid internal executor result.
  Even the invalid-result case terminates with safe `error` plus `complete`
  instead of breaking the stream or exposing the unvalidated object.
- Policy tests cover accepted and rejected provider failures, retrieval
  exclusion, fixed local model selection, raw `httpx` timeout handling and the
  fallback feature flag.
- Existing Ask grounding, authorization, idempotency, telemetry and runtime
  error tests are included in the regression set.
