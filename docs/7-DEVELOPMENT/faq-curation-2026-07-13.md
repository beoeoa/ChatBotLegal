# FAQ curation 2026-07-13

The FAQ store was curated from common ward/commune questions for Hai Phong.
The additions cover civil status, residence/security, complaints/denunciations
and sanctions, land/construction, and social support. The entries are practical
guidance, not a substitute for a case-specific decision by the competent
authority.

## Form safety

Before writing the store, `scripts/curate_hai_phong_faq.py` loads the official
forms index and removes every form reference that is not an approved official
record with a local file. The public FAQ API applies the same check again and
returns a download button only for a file that exists. Missing forms are shown
as unavailable rather than replaced with a guessed or synthetic file.

## Current result

- FAQ records: 67 (50 existing + 17 curated additions)
- Repaired stale form references: 8
- Curated entries with verified downloadable forms: civil status, residence,
  and complaint form examples use the local approved IDs.
- New canonical domains: `cu_tru_an_ninh` and
  `khieu_nai_to_cao_xu_phat`

Regenerate the deterministic curation after restoring a backup or changing the
official form index:

```powershell
python -X utf8 scripts/curate_hai_phong_faq.py
```

The script is idempotent: existing curated IDs are not duplicated.
