# Answer quality contract

## Scope

The answer engine now classifies each question deterministically before the
LLM runs. The classifier controls the answer shape only; legal conclusions
still require approved, effective retrieval sources.

Supported categories are `procedure`, `form_request`, `legal_explanation`,
`land_construction`, `complaint_sanction`, `residence_security`,
`social_support`, and `out_of_scope`.

Domain-specific questions can also have procedural intent. For example, a
land question asking for documents still receives the procedure checklist.

## Response contract

`AskResponse` exposes `question_type`, `detected_domain`,
`required_sections`, `forms_unavailable`, and `source_gap`. The same
classification is stored in `rag_trace.question_classification`.

Procedural questions check conclusion, submission place, documents, time
limit, fee, steps, official forms, and legal source links. If retrieval does
not establish a section, the answer reports that it was not verified instead
of filling it from model memory. Explanatory questions are not forced to
contain administrative hồ sơ, thời hạn, lệ phí, or biểu mẫu sections.

## Quality endpoints

Admin quality checks are available at:

- `GET /api/faq/quality`
- `GET /api/forms/quality`
- `GET /api/admin/legal-quality`
- `POST /api/quality/run`
- `GET /api/quality/runs/{run_id}`

The checks report FAQ domain coverage, stale form references, mojibake
records, form inventory status, and existing retrieval/role evaluation
artifacts. These checks do not approve crawler candidates.

## Verification

The focused regression suite includes the deterministic classifier, FAQ form
gating, citation handling, procedure rendering, and legal data audit. The
Step 13 golden set contains 167 cases, with at least 30 cases in each of the
five canonical ward/commune domains.

## Citizen answer pipeline

The citizen path is now:

`retrieval -> first grounded draft -> LLM editorial/completion pass -> citation and form validation -> final display`

The editorial pass receives only filtered legal evidence and approved-form
context. If that second model call fails or times out, the first draft is
retained after invalid citation removal; the repair call must never erase a
usable answer.
