# Grounded retrieval dataset 2026-07-28

`scripts/build_grounded_retrieval_dataset.py` creates exact-law/article cases
from identities present in the primary serving collection. The generator is
read-only and applies the authoritative serving gates before emitting an
expected contract:

- domain comes from `legal_commune_field_groups/legal_search_scope` in
  PostgreSQL, not potentially stale vector metadata;
- the chunk must have `legal_chunk_quality.eligible=true`;
- document and article must be active and effective at `legal_as_of`;
- documents whose `Văn bản căn cứ` relationship points to an expired source
  are excluded;
- the law number must be recognized by the deterministic exact parser;
- every case is a unique document/article identity, not a duplicated question.

Each case emits a direct-document observation and an exact-provision
observation. This allows Recall@10 and direct-source top-5 to be measured
independently. The dataset contains benchmark questions, while the release
report contains only opaque case IDs and public source metadata.

The 2026-07-28 regression run found two kinds of drift that serving correctly
rejected: stale vector domains and vectors for chunks marked
`eligible=false`. The generator now fails closed on both. It also treats
`lao_dong` as a reviewed child scope of the UI domain
`an_sinh_y_te_giao_duc` consistently in retrieval, evidence validation and
evaluation; unrelated domains remain excluded.
