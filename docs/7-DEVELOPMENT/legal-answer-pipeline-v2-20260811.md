# Legal Answer Pipeline V2

Date: 2026-08-11

## Architecture decision

The citizen answer path is reduced to one deterministic pipeline:

`route -> retrieve -> hard legal gates -> Evidence Packet -> one generation -> one claim/citation validation -> response + audit`

The legacy pipeline remains the rollback path. V2 is enabled only when
`LEGAL_ANSWER_PIPELINE_V2_ENABLED=true` and the authenticated role is present
in `LEGAL_ANSWER_PIPELINE_V2_ROLES`. Checked-in release defaults remain false
and citizen-first.

## Router and issue ownership

The router selects `exact_article`, `procedure_form`, `general_legal`, or
`historical`. AI Problem Map is off in V2. One question is one issue unless the
user provides an explicit numbered or reviewed quoted list. Commas, “và” and
“hoặc” remain inside the issue. Every issue owns its law number, Article,
procedure and domain; identifiers are never broadcast between sibling issues.

Ambiguous form codes or missing facts produce `clarifying_questions` before
retrieval/generation. The existing public answer-mode enum is unchanged.

## Retrieval

General retrieval creates independent vector and BM25 rankings and fuses them
with deterministic RRF. Structural duplicates are removed, then the existing
validity, authority, scope, hierarchy, replacement and role gates apply. V2
does not run a second heuristic hard reject after those gates. BGE may rerank
at most 40 post-gate candidates, but remains disabled because the measured
activation gate failed.

Exact Article retrieval uses metadata identity, including alphanumeric
Articles 18a/37a, and hydrates every structural child in legal order. It
bypasses BGE and content relevance. Missing/duplicate units make the packet
incomplete. A terminal gazette or attached-form boundary is excluded from the
answer projection without mutating PostgreSQL source content. A bounded TTL/LRU
cache accelerates repeated exact-Article reads and exposes only aggregate
health statistics.

## Evidence, generation and validation

Each issue owns an Evidence Packet containing source identity, structure,
quote, validity, provenance and coverage. Coverage measures missing facets but
does not discard otherwise valid evidence. FAQ, procedure and approved form
evidence is inserted before generation.

V2 invokes only `final_answer_model` for legal prose. The old strategy/answer
model IDs remain accepted for request compatibility but do not create extra
legal answers. Output is parsed once and passed to one claim/citation
validator. Invalid output uses a deterministic verified fallback; there is no
LLM repair pass. A valid issue remains visible when another issue is missing
evidence.

The validator checks issue ownership, document/Article/clause/point identity,
verbatim support, money/deadline/authority facts, validity at `legal_as_of`,
and citation proof (`physical_span` or `content_quote`). Invalid claims are
removed while valid claims remain.

## Forms and providers

The Form Router runs before RAG generation. It selects one reviewed
`procedure_id`, returns every mandatory form and labels conditional forms.
Generic codes such as “Mẫu 01” require procedure context. Only released,
approved, role-valid, current, official and checksum-valid bindings can be
returned. The language model can explain a selected form but cannot invent or
choose its name/link.

Ollama and cloud requests use the same router, retrieval, Evidence Packet and
validator; only the generation adapter changes. PII redaction and egress guard
remain before cloud calls. The machine currently exposes `qwen2.5:3b` and
`nomic-embed-text:latest` through Ollama. Changing the embedding model still
requires an explicitly approved re-index; the setup UI cannot silently change
the live index fingerprint.

## Audit and data release trace

Privacy-safe Admin history now records:

- pipeline version and answer route;
- RRF/BGE decision and reason;
- data release ID;
- index collection and embedding fingerprint;
- validity snapshot SHA-256;
- reranker version.

Question text, answer text, raw chunks and queries are excluded from the
bounded trace snapshot.

## Verification on 2026-08-11

Passed:

- corrected Golden 100 retrieval;
- Golden 294 validity subset (294/294, no expired selection);
- cold live 1,000 retrieval: Recall@10 and Top-5 99.905%, coverage 100%, zero
  wrong-domain/expired selection, P95 1,999 ms;
- direct citizen API V2: 2/2 grounded, all requested issues returned, all
  citations verified;
- form campaign: 1,254 procedure-role cases with no unsafe binding;
- final focused Python regressions: 99 passed and compilation passed.

Not passed:

- 1,000 final answers: 42.5% case pass, 42.912% facet coverage, 94.932%
  citation validity and 89.8% fallback; seven API/model errors;
- backend logs identify repeated cloud `RateLimitError` and an open provider
  circuit. Grounded claims remain 100%, showing the validator fails closed
  rather than inventing missing content;
- BGE activation, citizen rollout, officer rollout and browser UAT.

## Rollout and rollback

1. Keep V2 disabled in release defaults.
2. Restore adequate cloud capacity or separately benchmark the configured
   local generator on the same full-answer dataset.
3. Require fallback <=3%, final-answer case/coverage gates, 100% citation
   validity, API P95 <=25 seconds and error rate <0.5%.
4. Enable citizen only.
5. Stop and request separate approval for browser UAT.
6. Expand to officer only after citizen telemetry passes.

Rollback only disables V2/removes a role from its allow-list. It must never
disable validity, ACL, grounding, citation, privacy or audit gates.

## Read-only reconciliation

The current manifest contains 545,625 PostgreSQL chunks and 161,077 vector IDs:
149,675 active/vectorized, 282 active/missing-vector, 53,901 historical,
260,495 duplicate, 81,272 other inactive and zero orphan vectors. The 282
missing IDs remain a review queue; this V2 slice did not backfill or re-index
them.
