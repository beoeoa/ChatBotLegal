# Form requirement manifest

## Release decision as of 2026-07-29

The chatbot needs **278 form-serving entries** for the current five-domain
scope in Lê Chân/Hải Phòng:

- 254 paper or downloadable-file form identities;
- 24 interactive electronic-form identities;
- 317 of 418 procedures refer to at least one official form;
- 101 procedures list no official standard form and must receive an explicit
  no-template response plus dossier/content guidance, never an invented file.

This is the fixed planning denominator for the captured official-procedure
snapshot. It is not a legal approval count. Only 46 identities are currently
runtime-approved; 232 still require official artifact/effectivity review. Only
40 procedures are fully covered by the runtime catalog, 15 are partially
covered, and 262 form-bearing procedures are not covered.

The release verdict is therefore `BLOCKED_DATA` until every required identity
has an official file or a verified interactive-form route, current legal
effectivity, an exact procedure binding, and authenticated human attestation.

## Deterministic counting rules

1. Start from all current official procedure details in the five supported
   domains and the province/commune scope.
2. Exclude processing results, existing certificates, photos, maps, product
   label samples, guidance text and other supporting evidence.
3. Classify self-authored applications without an explicit prescribed
   template as `applicant_submission`; they remain dossier requirements but do
   not inflate the official-form count.
4. Split compact multi-form components such as “Mẫu số 06, 07 và 08” into
   separate references.
5. Deduplicate a proven identity by exact form code plus issuing instrument.
   Codeless named forms and interactive e-forms remain explicit review items;
   they are never silently merged by model similarity.
6. Treat paper/file and interactive e-form delivery as different serving
   entries. Preserve every official procedure URL and procedure binding.
7. Never auto-approve an entry or mutate the runtime catalog.

## Reproduction

Run:

```powershell
python scripts/build_form_requirement_manifest.py --legal-as-of 2026-07-29
```

Use `--refresh` to retrieve a new official snapshot. The command writes the
manifest, form list, procedure-coverage list and Vietnamese summary under
`reports/feature006/`. Any changed count is drift and requires review before it
replaces the release denominator.

## Operator stop, resume and rollback

Create the checksum-bound baseline and a narrow backup before a candidate run:

```powershell
python scripts/backup_form_completion_state.py
```

Verify a created backup without restoring or mutating runtime data:

```powershell
python scripts/backup_form_completion_state.py `
  --verify backups/form_completion/<timestamp>/backup-manifest.json
```

Stop a campaign by terminating only its runner process. Its per-identity
checkpoints remain under `data/form_resolution_campaign/runs/<run-id>/`; resume
the same run with `--resume-run-id <run-id>`. Do not start a competing worker
for the same requirement-manifest checksum.

If candidate preparation fails, keep all records non-serving and resume after
repair. If an authenticated synchronization later fails, restore only the five
catalog/attestation JSON files from the verified pre-batch backup, rerun the
focused catalog/binding/audit checks and keep the feature flag false. Never
delete source attempts, official artifacts or audit history to hide a failure.

## Checksum-bound completion campaign

The Feature 006 bridge converts the 278 requirement identities into the
existing resolver contract without changing the canonical catalog. The fixed
2026-07-29 snapshot reconciles to 46 runtime-approved and 232 pending
identities. Those pending identities currently produce 403 procedure bindings
(373 paper/file bindings and 30 e-form bindings); bindings must never be
misreported as additional form identities.

Create and verify the deterministic bridge using the hashes captured in
`reports/feature006/form-completion-baseline.json`:

```powershell
python scripts/bridge_form_requirements_to_campaign.py `
  --legal-as-of 2026-07-29 `
  --manifest-sha256 349009e0e5bf12c1fcf68991c78680ae023f3e244f0b7e39a2a533bedfec02c4 `
  --source-snapshot-sha256 2dedfc177b8cc12f972e8150616a44d9c71f14c9a2733f48b6d67ff0c26cde19
```

Run the candidate-only campaign with the same hashes:

```powershell
python scripts/run_form_resolution_campaign.py `
  --legal-as-of 2026-07-29 `
  --manifest-sha256 349009e0e5bf12c1fcf68991c78680ae023f3e244f0b7e39a2a533bedfec02c4 `
  --source-snapshot-sha256 2dedfc177b8cc12f972e8150616a44d9c71f14c9a2733f48b6d67ff0c26cde19
```

Paper/file forms use exact form-code plus issuing-instrument resolution and
appendix-level effectivity evidence. Interactive e-forms use a separate gate
for official ownership, stable URL, login/CAPTCHA access, procedure ownership,
role visibility and current snapshot evidence. Both paths stop at
`READY_FOR_HUMAN_ATTESTATION`. Review batches contain at most 25 identities,
retain every procedure binding and are bound to the manifest checksum and a
preview fingerprint.

## Required chatbot behavior

For a recognized procedure, the answer layer must return:

- every applicable approved form with its official name, code, issuing
  instrument, effective status, source page and download/open action;
- the interactive DVC route when the requirement is an e-form;
- an explicit “no official standard form is listed” response for procedures in
  that state, followed by required document/content guidance;
- a limitation instead of a file whenever identity, effectivity or approval is
  pending.
