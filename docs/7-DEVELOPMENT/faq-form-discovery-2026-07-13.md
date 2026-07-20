# FAQ Form Discovery

## Behavior

`scripts/discover_missing_faq_forms.py` checks approved FAQ entries that require
forms but have no valid downloadable form. It searches by the form name on
official-domain results and falls back to known National Public Service Portal
procedure pages when search results are blocked or incomplete.

Discovered pages/files are written to
`notebook_data/forms/official_forms_candidates_classified.json` with
`candidate_pending_review`. Direct files, when available, are stored under
`data/uploads/forms/official_candidates/web_discovery/`. Candidates are never
added to the public official index or exposed as downloadable until an admin
reviews and approves them.

## Current run

The first run checked 4 FAQ entries and created 4 candidates. The official
portal returned procedure pages but no direct downloadable files for these
entries, so they remain pending instead of being presented as fake downloads.
The khai sinh FAQ was linked to the existing approved official form already in
the local index.

## Run

```text
python scripts/discover_missing_faq_forms.py --limit 50
```

