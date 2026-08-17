# M4 query normalization and structured classification

## Result

M4 is PASS. Every retrieval request is normalized and classified into the
machine-readable `legal-query-classification-m4-v1` contract before exact,
vector or lexical retrieval starts. Classification is deterministic and does
not generate legal facts.

The object contains `domain`, primary `intent`, `secondary_intents`, `scope`,
`temporal_scope`, `temporal_reference`, `temporal_precision`, `answer_type`,
`retrieval_allowed`, `retrieval_as_of` and a stable temporal error code. The
same object is returned at response level and embedded in an M3 trace.

## Intent coverage

The classifier has executable acceptance cases for all required intents:

`PROCEDURE`, `ELIGIBILITY`, `REQUIRED_DOCUMENTS`, `AUTHORITY`, `PROCESS`,
`DEADLINE`, `FEE`, `FORM`, `LEGAL_BASIS`, `VALIDITY`, `SPECIFIC_DOCUMENT`,
`HISTORICAL`, `COMPARISON`, `COMPLAINT`, `OUT_OF_SCOPE` and `UNKNOWN`.

One primary intent drives the answer shape. Other detected facets remain in
`secondary_intents`; for example, a dated historical dossier question has
primary intent `HISTORICAL` and secondary intent `REQUIRED_DOCUMENTS`.

## Temporal safety contract

| Input | Classification | Retrieval decision |
|---|---|---|
| No past marker / “hiện nay” | `current` | Use the current request date |
| Exact past day such as `01/06/2020` | `historical` | Force all retrieval validity gates to `2020-06-01` |
| Past year only such as `năm 2020` | `historical` | Block until a day is supplied; an explicit matching `as_of` resolves it |
| “trước đây”, “hồi đó”, “thời điểm đó” | `unknown` | Block until an applicability date is supplied |
| Invalid/future date | `unknown` | Block with a stable error code |
| “hiện nay” with a past `as_of` | conflict | Block before retrieval |

Blocked requests return `clarification_required`, no evidence and no vector or
SQL lookup. Exact historical dates replace the default date in exact lookup,
lexical filtering, candidate validity checks and validity overlay. This is the
enforcement that prevents currently effective material from being represented
as the law at an unspecified past date.

Stable codes are `HISTORICAL_AS_OF_REQUIRED`,
`TEMPORAL_SCOPE_CLARIFICATION_REQUIRED`, `TEMPORAL_DATE_INVALID`,
`TEMPORAL_DATE_IN_FUTURE` and `TEMPORAL_AS_OF_CONFLICT`.

## Verification

```powershell
pytest -q tests/test_m4_query_understanding.py
python scripts/audit_m4_query_understanding.py
```

The audit validates all 16 intents against JSON Schema, verifies current,
historical and unknown temporal branches, and confirms the active collection
pointer remains `legal_chunks_vnlegal_lal_haiphong_unified_v1`.

## Compatibility and rollback

The request `as_of` field remains backward compatible. Batch requests preserve
whether `as_of` was explicitly supplied, so their default current date cannot
silently authorize or conflict with a historical query. The Ask API propagates
this explicitness through core, expanded and full-corpus retrieval paths.
Rollback is code-only: revert the M4
classifier and retrieval gate; no database, manifest, corpus or vector change
was made.
