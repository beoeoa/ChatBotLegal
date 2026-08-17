# Legal answer trust hardening — feature 016

## Purpose

Feature 016 strengthens the existing legal-answer pipeline without replacing its public API or mutating the active corpus. The order is deliberate: measurement, current-law correctness, retrieval quality, proof/governance, then complex-source coverage.

## Baseline

The source workbook contains 1,540 executions but only 100 unique legal questions, split evenly across five domains. `has_sources` is only transport/UI metadata and is not evidence of correctness. The new baseline records the workbook checksum, unique question hash, roles, run counts, fallback/error counts, answer variants and latency observations.

The baseline is not legal ground truth. Golden v2 requires reviewed expected/forbidden provisions, ordered claims, `legal_as_of`, proof where available and expected refusal/fallback behavior.

## Safety architecture

1. Normalize legal intent and requested facets.
2. Retrieve broadly but apply authority, jurisdiction, current validity and request/issue scope gates before reranking.
3. Require procedure/facet eligibility before evidence can bind a claim.
4. Hydrate complete Article packets for exact-provision questions.
5. Validate every public claim; rejected claims are removed.
6. Return a grounded answer, verified condensed answer or source-only limitation.

Missing/stale validity, ambiguous intent and insufficient evidence fail closed. Expired material remains available only in explicitly historical views.

## Measurement contract

Structured QA artifacts contain answer body, citations, answer mode, grounding/completeness/validity/claim decisions, stage timings and version fingerprints. They do not use a DOM accessibility dump as answer text and do not declare success from `has_sources`.

## Retrieval evolution

VNLegal-LAL remains the active embedding. The learned reranker is optional and runs after hard gates on a bounded window. BGE-M3 is shadow-only in a separate collection. Neither component can activate unless quality, safety, latency and resource gates pass.

Phase C implements a local-only cross-encoder adapter over at most 40 already
eligible candidates. `LEGAL_LEARNED_RERANKER_ENABLED=false` is the default. If
the configured local model directory, dependency, device or inference is
unavailable, the adapter preserves deterministic BM25/RRF ordering and emits a
stable degraded reason code; it never downloads a model. The reviewed Golden
v2 expected/forbidden source pairs generate the hard-negative dataset without
inventing cross-case negatives.

The BGE-M3 runner writes only an isolated shadow manifest/collection name. It
does not create or mutate the active pointer, does not re-index the real corpus
and cannot request activation. On the current machine neither optional model
is installed, so Gate C passes in explicit degraded mode: the fallback and
isolation contracts are verified, but no learned-quality improvement is
claimed.

CPU and CUDA release profiles are separate. Weak hardware may disable shadow or defer OCR/background work, but never disables effectivity, grounding, role isolation or citation validation.

## Citation and audit

Citation verification levels are `physical_span`, `content_quote` and `metadata_only`. Metadata-only sources cannot support a legal conclusion. Provenance is stored in an additive sidecar keyed by chunk/span and source/text hashes.

Legally significant mutations append to a canonical hash chain. Signed checkpoints use a private key outside the database. Chain verification detects modification, deletion, insertion or reordering.

Phase D makes the verification boundary explicit. A `physical_span` is valid
only when the server can independently observe the original asset hash and the
text of the claimed page, then reproduce the quote from page-local offsets.
Wrong file, page, offset or quote is rejected. Repeated content without stable
coordinates is not promoted to physical proof. Until the sidecar is activated,
`LEGAL_PHYSICAL_CITATIONS_ENABLED=false` downgrades otherwise valid physical
proof to a verified `content_quote`; legacy metadata remains reference-only.

The additive migration defines `legal_chunk_provenance`, `legal_audit_chain`
and `legal_audit_checkpoint`. It was rehearsed forward and backward only in
`feature016_trust_rehearsal_20260810_2`; the live database was not touched.
When `LEGAL_AUDIT_CHAIN_ENABLED=true`, critical candidate review/import,
effectivity, version activation and corpus events must append to a valid chain
before the ordinary audit row is written. The chain stores only a canonical
detail hash. Checkpoints are signed with an Ed25519 private key loaded from
`LEGAL_AUDIT_CHECKPOINT_PRIVATE_KEY_FILE`, never from the database.

## Provider boundary

Multiple providers remain supported. Public responses identify local/cloud mode and safe provider/model labels. PII must be redacted before cloud egress; raw prompts, secrets and PII are prohibited from audit artifacts. If safe redaction is impossible, the cloud call is blocked and the system uses a local or source-only path.

The Ask router applies this guard to both the current question and persisted
conversation context whenever any participating strategy/answer/final model is
cloud-hosted. Audit evidence records input/output hashes, categories and reason
codes rather than prompt text. `LEGAL_PROVIDER_DISCLOSURE_ENABLED=true` is the
default; disabling it keeps the local/cloud mode visible but replaces specific
provider/model labels with a generic public label.

## Layout-aware extraction and bounded graph hops

Phase E replaces flatten-only extraction metadata with deterministic
`ExtractionBlock` records. HTML and advanced-parser tables keep individual cell
paths. PDF text keeps page-local offsets and source hashes without claiming a
bbox it does not have. OCR retains page/bbox/confidence when Tesseract returns
layout data; missing binaries, languages, pages or coordinates produce explicit
fallback status. TXT/DOCX remain content-quote-only until a stable page mapper
exists. Layout blocks pass through the same upload PII redaction boundary as the
flattened content.

`api/legal_adaptive_hop.py` implements a local deterministic graph traversal,
not an LLM search loop. It follows only explicit verified amendment,
replacement, reference or parent–child edges, uses results of hop N to seed hop
N+1, stops after two hops or sixteen queries, detects cycles and reapplies
validity, hierarchy, jurisdiction and facet gates before and after every call.
`LEGAL_ADAPTIVE_HOP_ENABLED=false` remains the default. The current live
relationship inventory does not expose the reviewed-verification field required
to activate this safely, so Phase E proves the policy on isolated fixtures only
and leaves active single-hop retrieval unchanged.

## Rollout constraints

- Code, additive migration definitions, tests and isolated rehearsal are in scope.
- Live migration/backfill, corpus/history mutation, vector re-index or pointer switch, paid services/new daemons and live activation require separate approval.
- Browser UAT is intentionally excluded from automatic execution. After the final non-browser gate passes, implementation stops and requests user approval before the 2,000-case web run.

## Verification evidence

Evidence is written under `reports/feature016/<phase>/` and must be privacy-scanned. Every task and gate is tracked in `specs/016-legal-answer-trust-hardening/tasks.md`.

## Final non-browser verification

The Feature 016-specific suite passes 95/95 tests, the focused
security/privacy/release suite passes 76/76, frontend lint passes, and the
production build completes TypeScript checking and all 21 routes. The isolated
Ask API harness completed 2,100/2,100 requests with zero errors: a concurrency
matrix of 1/5/10/20/30 plus 1,800 requests at 3.002 RPS for 599.692 seconds.
The sustained API P95 was 28.137 ms and the isolated trust-stage retrieval P95
was 0.13 ms.

This harness mounts the real FastAPI Ask route, request validation and response
serialization, while replacing external model/database work with deterministic
local trust primitives. Its result proves route/guard stability, not production
capacity with real PostgreSQL, Chroma and model inference. The complete figures,
scope label and checksums are in
`reports/feature016/final-non-browser/isolated-api-load.json` and
`reports/feature016/final-non-browser/test-manifest.json`.

The repository-wide non-browser gate is green: 1,882/1,882 backend tests and
169/169 frontend tests pass, frontend lint passes, and the production build
completes all 21 routes. Tests for intentionally retired podcast/transformation
surfaces now assert their absence; locale parity retains historical strings
without treating retired namespaces as active UI. Legacy provider-fallback
tests explicitly disable the opt-in structured legal-answer path so they test
only their intended provider-to-local contract; the structured path keeps its
independent fail-closed coverage.

During the final gate, exact form lookup was restricted to caller-reviewed
procedure IDs instead of rescanning all 418 procedures. The form lookup group
dropped from more than five minutes to about thirteen seconds without relaxing
any approval, source, role, checksum or effectivity gate. The reviewed official
procedure snapshot is cached by path/mtime/size and processed off the event
loop; a timed-out retrieval skips that optional enrichment. Timeout and
concurrent fallback tests therefore remain bounded under the full-suite load.

Browser UAT remains intentionally unexecuted and now waits only for explicit
user approval, as required by T055.

## Importing a legal-review workbook

`scripts/import_feature016_golden_review.mjs` is the only supported bridge from
the human-facing workbook to Golden v2. It imports the workbook with the
bundled spreadsheet runtime, matches every case back to the frozen baseline,
normalizes source labels and ordered claims, and writes a separate candidate
dataset plus an audit report. It never overwrites the canonical Golden file.

The import is fail-closed. Any unresolved source identity, expected source
known to have been replaced by `legal_as_of`, baseline mismatch, duplicate
case, or incompatible answer-mode/refusal combination demotes that case to
`proposed`. A replacement relationship only blocks the old source; the tool
does not guess the replacement Article or silently rewrite the reviewer’s
legal conclusion. The reviewed workbook must be corrected and approved again
after any material source or claim change. By default the importer writes a
separate candidate; the canonical Golden file may be selected explicitly only
after the audit reports 100 safe approvals, zero errors and zero warnings.
