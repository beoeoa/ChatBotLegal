# Step 19 regression runner

`scripts/run_step19_regression.py` groups the Step 19 pilot checks into 14
numbered acceptance items and writes repeatable JSON/Markdown reports.

## Run

Fast local/unit pass without runtime services or production build:

```powershell
python scripts\run_step19_regression.py --skip-runtime --skip-build
```

Full pilot pass after backend, legal search, database, and frontend dependencies
are ready:

```powershell
python scripts\run_step19_regression.py
```

Set a custom API base if needed:

```powershell
$env:STEP19_API_URL = "http://127.0.0.1:5055"
python scripts\run_step19_regression.py
```

## Outputs

- Latest JSON: `notebook_data/regression/step19_regression_latest.json`
- Latest Markdown: `notebook_data/regression/step19_regression_latest.md`
- Per-command logs: `notebook_data/regression/step19_logs_*`

Statuses:

- `PASS`: all checks for the item passed.
- `FAIL`: at least one check ran and failed.
- `TIMEOUT`: a command exceeded its timeout.
- `ENV_FAIL`: the local runtime required for that item was not reachable.

`ENV_FAIL` is not a release pass. It is intentionally reported as a failing
condition so pilot sign-off cannot happen without a real runtime smoke run.

