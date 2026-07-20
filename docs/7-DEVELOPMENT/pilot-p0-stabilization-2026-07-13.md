# Pilot P0 stabilization - 2026-07-13

## Scope

This note records the P0 stabilization slice for the Hai Phong ward/commune legal assistant pilot. The slice focused on user-visible stability issues rather than changing the legal corpus:

- public auth/session behavior;
- conversation history persistence;
- UTF-8/mojibake on legal/admin screens;
- approved form discovery/download safety;
- citation viewer and PDF download messaging;
- crawler candidate review and AI-assessment error handling.

No legal corpus or vector store was deleted or rewritten in this slice.

## Decisions

1. Public login no longer trusts a role selected by the browser. The backend resolves the role from the account. Public registration creates `citizen` accounts only.
2. Citizen/officer/admin conversations are stored through the conversation API and reloaded by owner. The frontend refreshes conversation state after an answer is persisted.
3. Form recommendation must use approved records with a valid file/link. Candidate, rejected, synthetic, or missing-file forms must not show a fake download button.
4. Ask citations prefer the internal route `/legal-documents/{doc_id}?article={article}`. Original external URLs stay as provenance metadata.
5. PDF download prefers approved source assets and otherwise falls back to the indexed legal-search export. Missing/unavailable PDF paths must show a clear error instead of hanging silently.
6. Crawler AI assessment is only a review hint. If the model/API is unavailable, the UI now warns that AI assessment did not run and keeps rule-based review metadata visible.
7. Admin candidate review errors return structured JSON with `code`, `message`, `suggestion`, and `trace_id` where possible. The frontend displays backend/network failures with an actionable explanation instead of a bare `Network Error`.

## Files touched

- `api/routers/auth.py`
- `api/auth.py`
- `api/main.py`
- `api/user_service.py`
- `api/conversation_service.py`
- `api/routers/legal_search.py`
- `api/routers/ward_procedures.py`
- `frontend/src/components/auth/LoginForm.tsx`
- `frontend/src/lib/stores/auth-store.ts`
- `frontend/src/lib/hooks/use-auth.ts`
- `frontend/src/app/(dashboard)/search/page.tsx`
- `frontend/src/components/search/ConversationSidebar.tsx`
- `frontend/src/components/search/StreamingResponse.tsx`
- `frontend/src/app/(dashboard)/legal-documents/[id]/page.tsx`
- `frontend/src/app/(dashboard)/legal-import/page.tsx`
- `frontend/src/lib/api/legal-import.ts`
- `tests/test_public_auth_flow.py`
- `tests/test_user_pilot.py`

## Verification

Passed:

```powershell
python -m pytest tests/test_public_auth_flow.py tests/test_user_pilot.py tests/test_officer_seed_and_password.py -q
python -m pytest tests/test_conversation_service.py tests/test_ask_history.py -q
python -m pytest tests/test_official_form_runtime.py tests/test_forms_metadata.py tests/test_form_recommendation_and_citations.py tests/test_procedure_rendering.py -q
python -m pytest tests/test_legal_viewer.py tests/test_legal_viewer_smoke.py tests/test_legal_document_viewer.py tests/test_legal_pdf_pipeline.py -q
python -m pytest tests/test_crawl_route_registration.py tests/test_candidate_pdf_review.py tests/test_approved_import_guard.py -q
python -m py_compile api/routers/legal_search.py api/legal_crawl_service.py
cd frontend; npx eslint 'src/app/(dashboard)/legal-documents/[id]/page.tsx' 'src/app/(dashboard)/legal-import/page.tsx' 'src/components/search/StreamingResponse.tsx' 'src/lib/api/legal-import.ts'
cd frontend; npx tsc --noEmit --pretty false
```

Notes:

- Some Python test runs still emit the existing `multiprocess.resource_tracker` shutdown warning. The tests pass.
- `tests/test_legal_viewer.py::test_download_pdf_generated` emits an AsyncMock warning from the test double. It does not fail the PDF pipeline tests.

## Remaining work

- Run browser E2E on `localhost:3000` for public/citizen/officer/admin flows.
- Continue cleaning older admin/legal screens not covered by this P0 slice.
- Extend crawler diff summary to article-level comparison for old/new documents.
- Complete the golden legal benchmark set and quality dashboard acceptance thresholds.
