# Step 2 issue planner and coverage matrix

The deterministic issue planner is bounded to six retrieval issues per
question. Explicit punctuation, `và`, `nhưng`, `hoặc`, conditional clauses and
cross-topic clauses are split without a model call. Empty, duplicate and
single-keyword issues are rejected.

The bounded facets are:

1. condition;
2. authority;
3. documents;
4. procedure;
5. deadline;
6. fee/form.

Fee and form share the sixth planner slot when all other facets are present.
They remain separate requested facets in the coverage matrix, so neither is
silently treated as answered.

Every `LegalIssue` carries the request subject, location, facts, applicable
date, domain, intent, expected sources and expected official form. Context is
copied when `ensure_required_facet_issues` fills a missing explicit facet.

Regression coverage is in:

- `tests/test_step2_issue_planner_regressions.py`;
- `tests/test_step2_issue_planner_matrix.py`;
- `reports/feature005/step2-issue-planner-20260726.json`.

The offline evaluator covers all 167 golden cases and nine role cases. It does
not call a model, change the legal corpus, change a collection pointer or
create embeddings.
