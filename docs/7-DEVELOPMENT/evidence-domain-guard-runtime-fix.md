# Evidence domain guard runtime fix

## Failure

Structured answers could retrieve valid candidates and then fail before
rendering because `rank_issue_evidence()` read a non-existent `source` variable
instead of the current candidate `row`. The user-facing symptom was the generic
“Hệ thống chưa thể hoàn tất câu trả lời” message after retrieval had succeeded.

## Correction

- Read `domain`/`legal_domain` from the current candidate row.
- Apply taxonomy hard-negative matching only to values defined by
  `LegalTopic`. Legacy issue labels such as `planning` and
  `construction_authority` continue through their dedicated deterministic
  checks instead of being rejected as unrelated taxonomy topics.

## Verification

`pytest tests/test_legal_evidence_relevance.py tests/test_ask_request_contract.py tests/test_ask_progress.py -q`
passes 55 tests. The local backend was restarted and reports the database,
retrieval and import worker healthy with the unified 168,155-vector collection.
