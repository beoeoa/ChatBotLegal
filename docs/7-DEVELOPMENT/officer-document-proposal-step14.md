# Step 14 - Officer Document Proposal

Date: 2026-07-11

## Scope

Officers can propose documents, forms, procedure references, or supporting links for their assigned legal domains. A proposal never enters the official RAG corpus directly.

## API Flow

- Officer submits through `POST /api/legal/proposals` as multipart form data.
- Required fields: `domain`, `source_type`, `title`, `reason`.
- Optional fields: `source_url`, `law_number`, legal metadata dates, `metadata_json`, `content`, and `file`.
- Supported upload extraction: TXT, MD, DOCX, PDF through the existing basic extractors.

## Authorization

- Only role `officer` can submit proposals.
- The submitted `domain` must match the officer `user_profile.allowed_domains`.
- Domain aliases are accepted, for example `ho_tich` or `chung_thuc` can satisfy `ho_tich_chung_thuc`.
- Wrong-domain proposals fail with `403` before any candidate is created.

## Candidate Queue

Approved storage target is the existing `legal_crawl_candidate` queue, enriched by migration 30:

- `submitted_by`
- `domain`
- `source_type`
- `proposal_reason`
- `uploaded_file`
- `extraction_result`
- `duplicate_candidates`
- `legal_validity_flags`
- `requested_changes_note`

New proposals are written as:

- `status = pending`
- `review_status = pending`
- `suggested_action = officer_proposal_review`
- `raw_metadata.candidate_origin = officer_document_proposal`
- `raw_metadata.confirmed_official_source = false`

This means pending proposals are excluded from Ask/RAG because the official legal-search import is not called.

## Admin Review

Admins use the existing candidate review flow:

- `approved`: calls `LegalCrawlService.import_candidate`, which imports into Legal Search first, then optionally creates notebook source/embed work.
- `rejected`: stores the review note and does not import.
- `changes_requested`: stores `requested_changes_note` and does not import.

Candidate detail returns `review_preview` with metadata, extraction result, duplicate information, legal validity flags, and content preview for admin inspection.

All proposal creation and admin review actions write audit logs through `write_audit_log`.

## Verification

Regression tests:

```powershell
pytest -q tests\test_officer_document_proposals.py tests\test_review_workflow.py
```

Covered cases:

- officer wrong-domain proposal is denied;
- pending proposal does not call import and stays outside official RAG;
- admin approval triggers the import job path.
