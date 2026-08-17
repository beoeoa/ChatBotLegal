# Vector Integrity V3 foundation — 2026-08-16

## Outcome

Feature 018 now has the contract and isolated schema foundation required to
replace the non-replayable legacy vector candidate without changing the M2
active collection. This slice implements T143–T146 only; it does not assess the
live corpus, re-chunk, embed, build Chroma or change a serving pointer.

## Decisions

- `legal-serving-manifest-v3` separates model artifact, tokenizer, embedding
  recipe, passage recipe, splitter, source snapshot, dependency lock and
  persisted vector-content fingerprints. The ambiguous V2
  `embedding_fingerprint` remains readable only for rollback compatibility.
- `legal_chunk_release` and `legal_chunk_revision` are additive. V1
  `legal_article_chunks` rows are not rewritten, and a completed release or
  chunk revision is immutable.
- A release inventory is the sum of retrievable and quarantined documents.
  Vector count must exactly equal eligible chunk count.
- A chunk is never release-eligible without an explicit quality assessment.
  Empty passages, passages above 512 tokens and inconsistent serving states are
  rejected by both typed contracts and SQL checks.
- Approved legal metadata corrections require an official evidence URL,
  evidence checksum and reviewer. Machine proposals cannot become legal truth.

## Migration safety

`scripts/manage_vector_integrity_v3_schema.py` accepts only explicitly confirmed
disposable database names beginning `feature018_vector_` or
`feature018_isolated_`. It has no Chroma dependency or pointer write path. The
rehearsal completed `up`, `verify` and `down` against a disposable PostgreSQL
database with minimal canonical-table fixtures, after which that database was
removed.

The older Feature 018 rehearsal runner was also tightened to accept only
`feature018_isolated*`; previously an arbitrary database name such as
`customer_data` could pass its name check.

## Verification

- 24 focused contract/model/migration/M2 tests passed in the main environment.
- 5 read-only vector-manifest tests passed in the retrieval environment.
- PostgreSQL rehearsal created and verified exactly
  `legal_chunk_release`, `legal_chunk_revision` and
  `legal_metadata_review_case`, then removed them and the disposable database.

## Next gate

T147 must produce Quality Policy V2 and a read-only inventory assessment. No
V3 chunk or vector build starts until all 7,245 documents have an explicit
`retrievable` or `quarantined` decision and all Golden-required sources are
reviewed.
