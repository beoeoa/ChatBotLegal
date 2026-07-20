# Citizen Answer Fast Path

## Decision

The configured `DeepSeek v4 pro` model remains the default model. Citizen answers
use a smaller generation budget and skip presentation-only repair checks when the
draft is otherwise usable. Retrieval, effective-document filtering, citation
validation, and grounding remain unchanged.

Officer and admin answers keep the full completion and role-format checks because
those roles need the deeper procedural output.

## Conversation persistence

The search UI no longer replaces a completed local answer with a conversation
snapshot that contains only the user message. When the server snapshot is missing
a completed assistant message, the UI mirrors the completed answer to the
conversation before reloading it. If that write fails, the completed answer stays
visible locally instead of disappearing.

## Verification

- `python -m py_compile open_notebook/graphs/ask.py`
- `npx tsc --noEmit --pretty false` from `frontend/`
- Ask API smoke test with the configured DeepSeek model
- Conversation detail must contain both user and completed assistant messages
