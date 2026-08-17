# Ask request persistence flag

The answer executor may remove a just-persisted user message from the
conversation context to avoid duplicating the current question in the prompt.
That behavior is controlled by `AskRequest.pre_persisted_user_message`.

The field is an internal boolean and defaults to `False`, so both
`/api/search/ask/simple` and `/api/search/ask/progress` remain compatible with
existing client payloads. Without the field, the executor raised an
`AttributeError` before retrieval and generation, which surfaced in the UI as
“Hệ thống chưa thể hoàn tất câu trả lời”.

Verification:

- `pytest tests/test_ask_request_contract.py tests/test_ask_progress.py -q` — 6 passed.
- Backend restarted and `/ready/import` reports database, retrieval and import
  worker healthy.
