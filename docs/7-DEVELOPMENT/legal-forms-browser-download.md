# Legal forms browser download

## Decision

Form detail pages may expose a download through JavaScript instead of a direct
document URL. The forms crawler therefore supports a user-authorized persistent
Crawl4AI browser profile and performs the download click inside that browser.

## Safety and legal-data rules

- The crawler does not bypass Cloudflare, authentication, or subscription checks.
- A user completes any challenge and login in the visible browser.
- Only files whose bytes validate as PDF, DOC, or DOCX enter the official store.
- HTML pages, login responses, and malformed files are rejected.
- Failed records remain visible in `forms_download_status.json` for admin review.
- Interactive waiting applies only to the first page. Later pages reuse the same
  browser session and use a short rendering delay.

## Usage

Run a one-item verification first:

```powershell
python -X utf8 scripts/crawl_legal_forms.py `
  --engine crawl4ai `
  --profile-dir notebook_data/browser_profiles/tvpl `
  --interactive `
  --interactive-wait 180 `
  --limit 1
```

After one file reaches `downloaded_verified`, run the full catalog using the
same visible browser session and profile.
