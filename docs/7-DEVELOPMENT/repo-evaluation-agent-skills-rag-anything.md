# Repo Evaluation: agent-skills and RAG-Anything

Date: 2026-06-17

## Summary

Two external repositories were evaluated for this project:

- `addyosmani/agent-skills`
- `HKUDS/RAG-Anything`

The conclusion is:

- `agent-skills` is useful as an engineering workflow reference, not as runtime product code.
- `RAG-Anything` is useful as an optional advanced parser path for difficult PDF/Office ingestion, but too heavy to replace the current legal RAG stack wholesale.

## 1. addyosmani/agent-skills

### What it is good at

- Enforcing spec/plan/build/test/review discipline.
- Capturing engineering rules in reusable instruction files.
- Keeping large changes more auditable.

### Why we are not embedding it as product runtime

- It is not an end-user feature for legal Q&A.
- It does not improve legal retrieval or answer correctness by itself.
- Most value comes from how maintainers and agents work, not from serving user requests.

### What we adopted

- Root `AGENTS.md` now encodes a lighter workflow inspired by that repository.
- The repo now explicitly documents legal-specific engineering boundaries:
  - no fabricated legal citations
  - deterministic metadata preservation
  - graceful fallback for optional parsers

## 2. HKUDS/RAG-Anything

### What it is good at

- Better document decomposition for complex PDFs and Office files.
- Preservation of tables, image captions, and equations as structured blocks.
- Optional multimodal parsing path via MinerU/LibreOffice.

### Why we are not replacing the current stack with it

- This project is primarily a legal text RAG system, not a multimodal research assistant.
- `RAG-Anything` adds heavy dependencies:
  - `mineru[core]`
  - optional image/table/equation processors
  - LibreOffice for Office conversion
- Full adoption would increase operational complexity and slow local deployment.
- The current legal retrieval logic already depends on custom metadata filtering and legal validity checks that would still need to stay in place.

### What we adopted

We added an optional extraction adapter:

- File: `api/rag_anything_adapter.py`
- Integrated into: `api/routers/legal_search.py`
- Surface in UI: `frontend/src/app/(dashboard)/legal-import/page.tsx`

Behavior:

- For PDF/DOCX import, admin can choose:
  - `auto`
  - `basic`
  - `rag_anything`
- `auto` tries `RAG-Anything` first for PDF/DOCX and falls back to the existing extractor when MinerU/LibreOffice is not available.
- The API returns:
  - `extractor_used`
  - `extractor_fallback_reason`
  - `content_blocks` when `RAG-Anything` succeeds

## Decision

Accepted.

We keep the current legal-search architecture and use selective adoption:

1. `agent-skills` for development discipline.
2. `RAG-Anything` only as an optional advanced ingestion path.

This gives us practical value without making the legal chatbot heavier than it needs to be.
