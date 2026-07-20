# Legal retrieval document browser

## Purpose

The `/sources` screen is the public browser for the same PostgreSQL legal
corpus used by VNLegal-LAL retrieval. It no longer reads the generic Open
Notebook `source` table, which may legitimately be empty on a legal-only
deployment.

## Data flow

```text
frontend /sources
  -> GET /api/legal/documents
  -> backend legal-search proxy
  -> GET /documents on scripts/legal_search_server.py
  -> PostgreSQL legal_documents + legal_search_scope
```

Only documents that are active and effective on the requested `as_of` date are
returned. Documents included in `legal_search_scope` are labelled `core`; the
remaining active source-corpus documents are labelled `expanded`.

Selecting a row opens `/legal-documents/{doc_id}`. The viewer and PDF endpoints
therefore use the exact internal document identifier that retrieval citations
use, rather than reconstructing links from a law number or title.

## API

`GET /api/legal/documents` supports:

- `q`: title, law number, issuing agency, or document type.
- `domain`: reviewed legal domain slug.
- `tier`: `all`, `core`, or `expanded`.
- `as_of`: effective-date boundary, defaulting to today.
- `limit` and `offset`: pagination.
- `sort_by`: `effective_date`, `issued_date`, `title`, or `law_number`.
- `sort_order`: `asc` or `desc`.

The response contains `doc_id`, metadata, retrieval tier, and active article
count. Candidate, staging, expired, and not-yet-effective documents are not
shown.

## Verification

1. Check `GET http://127.0.0.1:8765/documents?limit=3`.
2. Check the authenticated proxy at
   `GET http://127.0.0.1:5055/api/legal/documents?limit=3&tier=core`.
3. Open `http://127.0.0.1:3000/sources` and confirm the total is non-zero.
4. Search by law number, filter by tier, and open a row.
5. Confirm the viewer URL contains the selected `doc_id` and loads its article
   index.
