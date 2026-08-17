# Multi-Issue Legal Answer Orchestration

## Architectural decision

Feature 012 consolidates multi-issue legal Q&A in the structured pipeline in
`api/routers/search.py`. The legacy graph consumes the same cleaned evidence
projection and remains a compatibility adapter; it does not own a separate
retrieval policy.

The mandatory order is:

```text
authenticated role
-> bounded Problem Map
-> batch child retrieval
-> validity/scope/hierarchy gates
-> issue-aware content relevance
-> final selection
-> adaptive parent capsule
-> complete coverage matrix
-> at most one supplemental retrieval round
-> claim validation
-> role rendering
```

Content relevance cannot change `validity_status`, authority rank, or promote a
local document above a controlling central document. Parent context is hydrated
only after the child has passed those gates.

## Contracts

- Problem Map: at most 8 issues, 4 queries per issue, and 16 queries per request.
- A simple question uses the deterministic planner. A complex question may use
  one model call at temperature 0 with a 4-second timeout; every error falls
  back to deterministic planning.
- The role is taken from the authenticated request. The model cannot elevate it.
- `/search/batch` still accepts legacy `query`; new callers may add `queries[]`.
- Results retain `request_id`, `issue_id`, and `query_id`; duplicate chunks are
  merged within the issue while retaining all `query_ids`.
- Coverage never drops `not_available` from the denominator. Every facet is
  represented as `covered`, `partially_covered`, `missing_evidence`, or
  `requires_user_fact`.
- A claim is valid only when its exact support quote exists in the capsule of
  evidence belonging to the same request and issue.
- `requires_user_fact` is bound deterministically to the affected issue. For
  the land regression this includes signatures, the existence of a formal
  dispute, and a recovery notice/decision. Direct evidence may still support a
  conditional explanation, but that issue does not count as fully covered.
- Public citations are built only from claims that the renderer actually
  displays. Additional validated candidates remain available to the admin
  trace and cannot appear as unexplained public citations.

## Cleaner and evidence capsule

`api/legal_text_cleaning.py` creates a ranking/prompt projection without
overwriting raw content, child content, parent content, source URL, or official
metadata. It removes only known Gazette headers/footers, known footer URLs, and
an isolated page number at a document edge or next to recognized noise. Unknown
or uncertain content remains untouched.

The default evidence budget is 6,000 characters for simple questions and
12,000 for complex questions. Round-robin allocation gives every issue an
opportunity to receive evidence before a second source is added.

## Observability and privacy

The admin-only trace records planner mode/fallback reason, issue/query/candidate
counts, content decision reasons, coverage, supplemental round, context size,
and sanitization totals. In-memory telemetry stores only bounded counts and
timings. It does not retain questions, legal body text, user IDs, cookies, or
credentials. Citizen and Officer responses do not expose internal trace IDs.

## Local rollout and rollback

After unit, contract, role, privacy, API, and browser gates pass, local runtime
uses:

```dotenv
LEGAL_SECTION_GROUNDING_ENABLED=true
LEGAL_PROBLEM_MAP_LLM_ENABLED=true
LEGAL_STRUCTURED_CONTEXT_MAX_CHARS=6000
LEGAL_STRUCTURED_HARD_CONTEXT_MAX_CHARS=12000
```

`.env.example`, release compose, and production startup remain fail-closed with
`LEGAL_SECTION_GROUNDING_ENABLED=false`. Local rollback changes the feature flag
to `false` and restarts the API; it does not migrate, re-index, or restore data.

## Data scope

This feature adds no schema migration, does not rewrite the corpus, and does not
change the active retrieval collection. Missing direct evidence remains visibly
missing; orchestration cannot manufacture an absent legal source.

## Local verification (2026-08-08)

- Core orchestration/cleaner/retrieval/role suite: 140 tests passed.
- Validity, hierarchy, privacy, authentication, scope, and retrieval suite:
  153 tests passed.
- Frontend search/session/auth suite: 31 tests passed; TypeScript validation
  passed.
- Warm local `/search/batch` run with eight issues: p95 120 ms, with issue and
  query provenance present.
- Browser acceptance at `http://localhost:3000/search`: Citizen login, complex
  eight-issue answer, citations, missing-evidence rendering, role retention,
  refresh, and history reload passed.
- Officer API acceptance used an authenticated Officer session while the body
  attempted to request the Citizen role; the response retained Officer titles,
  returned eight sections, and did not expose admin trace.

The configured planner provider timed out during one browser acceptance run.
The deterministic eight-issue fallback completed the request as designed.
One selected legacy land instrument is still marked `validity_sync.status=unknown`
in the local index while the official source reports partial effectivity. The
structured pipeline preserved the existing fail-closed authority/effectivity
gates and did not hard-code a guessed provision status; this remains a data
sync/legal-review item before production approval.
This is not a production rollout approval; production remains disabled pending
the separately defined data, legal-review, provider, and 1,000-question gates.
