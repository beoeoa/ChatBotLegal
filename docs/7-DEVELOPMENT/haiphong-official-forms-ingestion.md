# Hải Phòng official forms ingestion

## Scope

`scripts/crawl_haiphong_official_forms.py` collects public procedure and form
packages from official `*.haiphong.gov.vn` pages and their CDN attachments.
This source replaces commercial form catalogs as the primary source for the
Hải Phòng commune-level pilot.

## Data policy

- Downloaded files enter `official_candidates`, never the answer corpus.
- File bytes must validate as PDF, DOC, DOCX, XLS, or XLSX.
- SHA-256 is used to identify duplicate attachments.
- A package must contain deterministic commune-level markers before it is
  labelled `downloaded_pending_review`.
- The crawler never infers that a package is currently effective. It records
  `needs_admin_effectivity_review` until an admin checks the publishing
  decision, amendments, replacements, and repeal status.
- Titles or contents containing repeal markers are labelled
  `inactive_archived`.

## Run

```powershell
python -X utf8 scripts/crawl_haiphong_official_forms.py
```

Use `--limit-articles 5` for a small verification run. Results are written to
`notebook_data/forms/haiphong_official_candidates.json`.

Build the non-destructive form index after crawling:

```powershell
python -X utf8 scripts/index_haiphong_official_forms.py
```

The index points to exact official source packages and PDF pages or DOCX
paragraph locations. It does not regenerate or silently modify official forms.

Load the complete index into the runtime catalog:

```powershell
python -X utf8 scripts/promote_haiphong_official_forms.py
```

Every source reference is retained. Chatbot availability is limited to one
canonical source per duplicate group with a usable title and no repeal warning.
The download remains the official source package when the publisher did not
provide a standalone form file.

## Priority catalog for runtime search

The broad crawler catalog is not treated as a popularity ranking. Build the
curated supplement and the representative runtime catalog separately:

```powershell
python -X utf8 scripts/ingest_priority_official_forms.py
python -X utf8 scripts/build_priority_200_forms.py
```

`ingest_priority_official_forms.py` uses deterministic official source
definitions for common civil-status, residence, and complaint forms. It stores
only an original standalone file or a verbatim page slice from the official
PDF. Every slice keeps the original package, source URL, original SHA-256, and
page range.

`build_priority_200_forms.py` combines the verified supplement with canonical
Hai Phong source packages. It:

- excludes known out-of-scope science, technology, heritage, and tourism
  packages;
- reclassifies records into the five system domains;
- ranks common citizen-facing forms above specialist forms;
- caps land/construction records so one large package cannot consume the whole
  catalog;
- writes `notebook_data/forms/priority_200_forms.json`.

Runtime form search reads this priority catalog first. The broad catalog
remains available for admin review and fallback. The API exposes:

- `GET /api/procedures/forms-catalog/priority`
- `GET /api/procedures/forms-catalog/status`
- `GET /api/procedures/forms-catalog/official/{form_id}/download`

The priority catalog is a relevance shortlist, not proof that every form is
currently effective. Records retain `needs_admin_effectivity_review` unless
their legal status has been checked against the publishing authority.

## Verified URL enrichment

`enrich_missing_form_urls.py` restores source URLs for local form files only
when the same record already has a verified HTTP(S) source in the crawler
catalogue or in `notebook_data/forms/verified_form_source_urls.json`. It never
constructs a URL from a filename or asks the model to guess one:

```powershell
python -X utf8 scripts/enrich_missing_form_urls.py
python -X utf8 scripts/audit_legal_data.py
```

The 2026-07-13 run restored 74/74 previously URL-less priority records. The
audit now reports 663 official-index records, 0 missing external URLs, and 1
record with a missing local file. The missing-file record remains excluded
from public download recommendations until an administrator supplies and
reviews the file.

In Docker, `notebook_data` is mounted at `/app/data`, while verified form
files are mounted read-only at `/app/data/uploads/forms`. The API resolves the
catalog directory from either the host layout (`notebook_data/forms`) or the
container layout (`data/forms`). Keep both Compose mounts when moving the
pilot to another machine.
