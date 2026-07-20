# Form recommendation and citation selection (Step 9)

## Form recommendation contract

The Ask response exposes `recommended_forms` only if the user explicitly asks to obtain or fill a form (for example: `m?u`, `t? khai`, `bi?u m?u`, or a download/request phrase). Mentioning a complaint, an application, supporting documents, or a procedure in a factual/legal question is not enough.

A recommended form must meet every condition below:

1. `official_level == "official"`;
2. `review_status == "approved"`;
3. a validated non-empty official file exists;
4. it has a local authorized download URL; and
5. it maps to one of the concrete procedures detected in the question.

Ranking is deterministic: exact procedure match, form title/type overlap, selected/detected domain, ward scope, and file availability. Results are capped at three and prefer one form per matched procedure before any duplicate procedure form. No inferred passport, complaint, or broad domain package is returned. A request with no eligible official file is marked `forms_unavailable` rather than receiving a seed/synthetic URL.

## Citation selection contract

Ask citations are limited to one through three active legal retrieval hits. Selection rejects explicitly inactive/expired documents and ranks active items by retrieval score, query overlap, and presence of an actual document/article mapping. Citation links use the canonical internal viewer (`/legal-documents/{doc_id}?article=...`) and internal PDF route; external source URLs remain provenance metadata only.

## Regression coverage

`tests/test_form_recommendation_and_citations.py` covers: T? Hi?u pavement-roof legal question (no form), birth-registration download request, complaint-form request, an inheritance/multi-procedure request, and active internal citation mapping.
