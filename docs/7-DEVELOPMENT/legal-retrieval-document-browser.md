# Legal retrieval document browser

## Purpose

The `/sources` screen is the public browser for the same PostgreSQL legal
corpus used by VNLegal-LAL retrieval. It no longer reads the generic Open
Notebook `source` table, which may legitimately be empty on a legal-only
deployment.

## Data flow

```text
frontend /sources
  -> GET /api/legal/documents (20 rows per page)
  -> GET /api/legal/documents/export.xlsx (all filtered rows, max 10,000)
  -> backend legal-search proxy
  -> GET /documents or /documents/export.xlsx on scripts/legal_search_server.py
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
- `issued_from` and `issued_to`: inclusive issue-date range.
- `effective_from` and `effective_to`: inclusive effective-date range.
- `expired_from` and `expired_to`: inclusive expiry-date range.
- `sort_by`: `effective_date`, `issued_date`, `title`, or `law_number`.
- `sort_order`: `asc` or `desc`.

The response contains `doc_id`, metadata, retrieval tier, and active article
count. Candidate, staging, expired, and not-yet-effective documents are not
shown.

`GET /api/legal/documents/export.xlsx` accepts the same search, domain, tier,
date and sorting filters. It exports all matching rows from the authenticated
serving-manifest scope, not only the visible page. Exports above 10,000 rows are
rejected with HTTP 422 so the operator must narrow the filters. Text cells are
escaped against Excel formula injection.

## Verification

1. Check `GET http://127.0.0.1:8765/documents?limit=3`.
2. Check the authenticated proxy at
   `GET http://127.0.0.1:5055/api/legal/documents?limit=3&tier=core`.
3. Open `http://127.0.0.1:3000/sources` and confirm the total is non-zero.
4. Confirm each page contains at most 20 rows and page 2 has no IDs from page 1.
5. Search by law number, filter by tier and an inclusive date range, then open a row.
6. Export Excel and confirm its data-row count equals the filtered API `total`.
7. Confirm the viewer URL contains the selected `doc_id` and loads its article
   index.
