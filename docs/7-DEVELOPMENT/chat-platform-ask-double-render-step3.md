# Bước 3 — Fix Ask double-render

**Date:** 2026-07-11  
**Scope:** Frontend Ask history rendering + ask-session message snapshot fields.  
**Status:** Implemented and verified with unit/UI tests.

## Problem

After each Ask request completed, the same answer was rendered twice:

1. A raw assistant bubble from `chatHistory` (`<p>{msg.content}</p>`).
2. A polished `StreamingResponse` card driven by ephemeral `useAsk().finalAnswer`.

When the user asked a second question, `useAsk` reset `finalAnswer`, so the polished card of the first answer disappeared. Only the raw history text remained.

## Fix

### Single renderer
- New component: `frontend/src/components/search/AskMessageHistory.tsx`
- `search/page.tsx` no longer renders a standalone `StreamingResponse` from `ask.finalAnswer`.
- History is the only answer list:
  - user bubble
  - pending assistant skeleton
  - completed assistant → one polished `StreamingResponse` card
  - error assistant → error text in the same message slot

### Stable final state per message
- `AskMessage` now stores a full snapshot:
  - `status`
  - `citations`
  - `recommended_forms`
  - `faqs`
  - `procedure_detail`
  - `rag_trace`
  - `grounding_status`
- On success, `handleAsk` updates the **same pending message** via `completeAskTurn()`.
- Then `ask.reset()` clears ephemeral state so it cannot create a second card.
- Sending a new question appends a new pending turn; completed turns stay intact.

### Session persistence
- `api/routers/ask_sessions.py` accepts and stores:
  - `status`
  - `faqs`
  - `procedure_detail`
  - `rag_trace`
- Ask UI persists the polished snapshot fields with assistant messages.
- Local polished card remains even if persistence fails.

## Verification

```powershell
python -m py_compile api/routers/ask_sessions.py
Set-Location frontend
npx tsc --noEmit
npm test -- --run src/components/search/AskMessageHistory.test.tsx src/lib/utils/ask-turn-history.test.ts
```

Observed:

- TypeScript compile: pass
- Backend compile: pass
- Tests: 2 files / 3 tests pass
  - two consecutive completed answers → exactly two polished cards
  - first polished answer remains after second question
  - pending turn shows skeleton only

## Acceptance checklist

| Requirement | Evidence |
|---|---|
| No simultaneous raw + polished answer | History renderer only; raw assistant bubble removed |
| One stable renderer per assistant message | pending → complete in-place via `completeAskTurn` |
| Final state not reset by next question | history owns snapshot; `useAsk` reset after commit |
| Citations/forms/FAQ/procedure/ragTrace preserved | snapshot fields stored on `AskMessage` and passed into `StreamingResponse` |
| UI test for two consecutive questions | `AskMessageHistory.test.tsx` |

## Non-goals of this step

- Does not migrate ask sessions from JSON to Surreal tables.
- Does not change answer generation, retrieval, or citation policy.
- Does not redesign the overall chat layout (later steps).
