# Pilot completion status

## Implemented safeguards

- The public Ask path uses the non-streaming `/api/search/ask/simple` contract.
- The browser sends a stable idempotency key derived from the conversation and normalized question.
- The backend caches an idempotent response for retries and waits for an in-flight request.
- Conversation persistence rejects an identical completed assistant snapshot, covering API and legacy browser retries.
- The legal retrieval path keeps the two-tier core/expanded policy, effective-date filtering, hybrid vector/lexical reranking, and retrieval trace.
- Ask responses expose evidence coverage, claim validation, citations, forms, legal date, trace ID, latency, and quality flags.
- Citation links are built from retrieved `doc_id`/`chunk_id` metadata and point to the internal document viewer/PDF route.
- Retrieval normalization preserves article, clause, point, effective status, and document metadata through answer generation. If an answer names a law/article that is not present in retrieval, no nearby unrelated citation is substituted.
- Official forms are filtered by approval, official source, matching procedure, and downloadable file/link before public display.
- Approved forms remain visible in Ask even when the response also contains matched FAQ references.

## Verification

The focused backend suite currently passes 24 tests covering:

- request quality contract and legal date handling;
- request idempotency;
- conversation ownership, persistence, follow-up context, and duplicate assistant prevention;
- legal grounding and claim validation;
- citation/viewer/PDF mapping;
- official form recommendation and filtering.

Run the checks with:

```powershell
python -m pytest -q tests/test_ask_idempotency.py tests/test_conversation_service.py tests/test_ask_quality_contract.py tests/test_legal_answer_quality.py tests/test_legal_document_viewer.py tests/test_form_recommendation_and_citations.py
npm test
npm run build
```

## Remaining acceptance work

The implementation is not a legal certification. The following require real data/runtime validation before calling the pilot production-ready:

- run the full 334-case expert-reviewed benchmark;
- verify the live retrieval service and both collections on the target machine;
- health-check every citation/PDF/form URL and reconcile missing files;
- run the crawler schedule and approve candidates through the admin UI;
- run browser E2E for citizen, officer, and admin sessions;
- review the remaining data records marked candidate, inactive, missing metadata, or broken source.
