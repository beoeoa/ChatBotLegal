# Conversation Persistence Repair

## Problem

Some completed Ask requests saved the user message but not the assistant
message. The answer payload included evolving nested citation/form metadata;
older strict Surreal schemas rejected one nested field and rolled back the
entire `conversation_message` record.

## Decision

`conversation_service.add_message` now retries an assistant/user message with
an answer-first payload containing only stable fields when the full snapshot is
rejected. The current response still retains its citations and RAG trace, while
the durable conversation at minimum retains the complete answer text.

When an authenticated conversation is opened, the service also checks the
owner-scoped `user_ask_history` and restores every matching missing assistant
answer. No model call is made during recovery and no other user's history is
consulted.

## Verification

- Regression test covers nested citation schema rejection and answer recovery.
- Existing conversation and ask-history tests must remain green.
- The browser should show both user and assistant messages after refresh or
  reopening the same conversation.

