# Automated Pilot Testing

## Run

From `J:\ChatBotLegal`:

```powershell
python scripts/run_pilot_tests.py
```

The runner checks backend health, the legal pilot test suites, frontend unit
tests, and the frontend TypeScript build. It writes a JSON report under
`reports/pilot-test-*.json` and exits with code `0` only when every check passes.

For the full backend suite and a production frontend build:

```powershell
python scripts/run_pilot_tests.py --full --build
```

## What it covers

- authentication and role authorization;
- conversation persistence and ask history;
- retrieval, grounding, and citation guards;
- official form metadata and recommendations;
- legal document viewer and candidate review logic;
- frontend unit tests and TypeScript type checking;
- optional full backend regression suite and production build.

The runner does not call a paid LLM or crawl external websites. Live model and
crawler checks remain separate smoke tests so the normal regression run is
deterministic and safe to repeat.
