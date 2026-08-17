# Quickstart: Feature 017 non-browser verification

## Safety preconditions

- Do not apply migrations to `legal_chatbot`, production, release or any existing live database.
- Do not import/approve/release the 100 pending identities automatically.
- Do not switch `FORM_GOVERNANCE_SOURCE=postgres_active`, active release pointer or public feature flag.
- Browser UAT is excluded until the user separately approves it after code/API/build gates.

## 1. Validate specification and contracts

```powershell
python scripts/validate_feature017_contracts.py
```

Expected: workflow states/transitions, release manifest schema and Golden V3 schema pass; no unknown answer modes.

## 2. Plan and rehearse PostgreSQL schema

```powershell
python scripts/manage_feature017_form_schema.py plan
pytest -q tests/test_feature017_schema_rehearsal.py
```

Optional isolated PostgreSQL only:

```powershell
$env:LEGAL_DATABASE_URL = "postgresql+psycopg2://.../feature017_isolated_20260811"
python scripts/manage_feature017_form_schema.py up --confirm-isolated
python scripts/manage_feature017_form_schema.py verify --confirm-isolated
python scripts/manage_feature017_form_schema.py down --confirm-isolated --allow-drop-records
python scripts/manage_feature017_form_schema.py verify-down --confirm-isolated
```

Expected: command refuses default/live DB names and never falls back to a built-in credential.

## 3. Workflow, authorization and release

```powershell
pytest -q `
  tests/test_feature017_workflow.py `
  tests/test_feature017_api.py `
  tests/test_feature017_release.py
```

Expected: out-of-domain/cross-owner operations denied; source approval not runtime eligible; stale/tampered attestation rejected; failed gate keeps pointer; rollback retains audit.

## 4. Form Router and Ask compatibility

```powershell
pytest -q `
  tests/test_feature017_form_router.py `
  tests/test_step1_form_catalog_hard_gates.py `
  tests/test_form_recommendation_and_citations.py
```

Expected: exact procedure resolution, 100% exact form set for fixtures, ambiguity clarification, conditional/e-form labels and provider-independent form IDs.

## 5. Coverage and Golden V3

```powershell
python scripts/build_feature017_coverage.py --compat-json --output reports/feature017/coverage-baseline.json
pytest -q tests/test_feature017_coverage.py tests/test_feature017_golden_v3.py
```

If coverage is incomplete, Golden builder must stop with `FEATURE017_COVERAGE_INCOMPLETE` and output the review queue. It must not fabricate 1,000 approved cases.

After a separately approved active release reaches 100% coverage:

```powershell
python scripts/build_feature017_golden_v3.py `
  --release-manifest <approved-manifest.json> `
  --output-dir outputs/feature017-golden-v3-user-journey-1000
```

Expected: 1,000 proposed/review-ready rows, 200/domain, exact 400/300/100/100/100 composition, ≥3 phrasings/effective procedure, source manifest checksum on every case. Freeze remains blocked until workbook approval.

## 6. Frontend and final non-browser gate

```powershell
pytest -q tests/test_feature017_*.py
Set-Location frontend
npm test -- --run
npm run lint
npm run build
```

Run isolated performance benchmark only after functional tests pass. Then stop and request permission before browser UAT, live migration, source campaign decisions or production activation.

