# Dulieuphapluat form crawl

## Purpose

`scripts/crawl_dulieuphapluat_forms.py` crawls common form templates from
`dulieuphapluat.vn` into the form candidate review queue.

This source is treated as a reference form source, not as an authoritative legal
source. Crawled records must stay in `candidate_pending_review` until an admin
checks the template, source page, relevance, and current applicability.

## Default scope

The built-in seed list covers:

- Real estate transactions: mapped to `dat_dai_xay_dung`.
- Marriage/family/inheritance/domestic violence: mapped to `ho_tich_chung_thuc`.
- Business/personnel templates: optional low-priority records, mapped to
  `unknown` with `ward_scope=false`.

Use business/personnel templates only as supplementary references. They should
not be promoted into the ward/commune official form surface unless an admin has
a concrete reason.

## Commands

Core ward/commune-adjacent categories only:

```powershell
$env:PYTHONIOENCODING='utf-8'
python scripts\crawl_dulieuphapluat_forms.py --target 220 --max-pages 35 --delay 0.08
```

All provided categories, including low-priority business templates:

```powershell
$env:PYTHONIOENCODING='utf-8'
python scripts\crawl_dulieuphapluat_forms.py --target 260 --max-pages 35 --delay 0.05 --include-low-priority
```

## Outputs

- Candidate queue:
  `notebook_data/forms/official_forms_candidates_classified.json`
- Crawl report:
  `notebook_data/forms/dulieuphapluat_forms_crawl_report.json`
- Downloaded candidate files:
  `data/uploads/forms/official_candidates/dulieuphapluat/`

The crawler validates downloaded bytes before saving a file as usable. HTML
responses, access pages, broken links, and server-local paths such as
`/C:/Users/...` are not treated as real documents.

## Review policy

- Do not expose pending records through the public official form endpoint.
- Do not import these forms into the legal RAG corpus as legal authority.
- Approve only after checking source, file integrity, relevance to the pilot
  domain, and whether the form is still applicable.
- Prefer Hai Phong official forms and central official forms over these
  reference templates.

## Latest crawl result

Run on 2026-07-12:

- Detail pages discovered: 238
- Detail pages processed: 238
- Candidate records added/updated in review queue: 238
- Verified downloadable files from this run: 101
- Invalid download responses: 134
- No public download link: 3

## Latest promotion result

Run on 2026-07-12:

- Verified files promoted into the downloadable form catalog: 101
- Core ward/commune-adjacent forms promoted: 31
- Low-priority non-core reference forms promoted: 70
- Broken or missing-download records left pending: 137
- Total downloadable records in `priority_official_forms.json` after promotion: 190

Promotion keeps `official_level=reference` for this source. These forms can be
searched and downloaded, but they must not be used as legal authority in RAG
answers.
