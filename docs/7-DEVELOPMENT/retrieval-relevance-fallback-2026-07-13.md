# Retrieval relevance fallback

## Problem

The domain filter could return non-empty results that were unrelated to the
question. The API treated those results as a successful retrieval, so the
global search fallback did not run and the answer model received bad evidence.

## Decision

Domain selection is a soft preference. Before evidence is passed to the answer
engine, the first results are checked for deterministic overlap with meaningful
question terms. Empty or clearly unrelated domain hits trigger one broader
search without the domain filter. The fallback is recorded in `rag_trace`.

This does not invent a legal source and does not bypass effective-status or
candidate filtering performed by the retrieval service.
