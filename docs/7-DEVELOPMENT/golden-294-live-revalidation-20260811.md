# Golden 294 live revalidation — approved production slice

## Authorization and outcome

On 2026-08-11 the user explicitly approved: `Duyệt cập nhật dữ liệu sống và
tái kiểm định 294 ca`. This opens only the targets listed below. It does not
authorize schema migration, whole-corpus re-index, hard deletion, a paid
service, or an Internet deployment without a named target.

## Fixed target set

- Import and embed the official current instrument `116/2026/TT-BCA`.
- Retain `55/2021/TT-BCA` and `66/2023/TT-BCA` for history while excluding
  them from current-law answers after 2026-07-01.
- Resolve provision scope for `31/2024/QH15` and `73/2025/QH15` only from
  exact official evidence; otherwise retain `partial_scope_unresolved`.
- Repair only proven missing structural units for `88/2025/QH15`.
- Keep the complete-Article contract for the oversized Article 1 of
  `62/2020/QH14`; use structured sub-packets only after whole-Article coverage
  has been verified.
- Rebuild and re-evaluate exactly the 294 Golden cases reported by
  `approved-validity-audit.json`.

## Mutation order

1. Capture SQL/document/article/chunk counts, metadata checksum, validity
   snapshot hash, vector collection counts and service versions.
2. Stop retrieval cleanly and make PostgreSQL plus Chroma backups. Export the
   operational SurrealDB database and verify output hashes.
3. Fetch official evidence, preview every mutation and persist a source hash.
4. Use the existing candidate → approval → import/embed pipeline for the new
   instrument. Activation occurs only after SQL and both vector collections
   verify.
5. Apply any old-document state change in one bounded transaction with before
   values in the rollback manifest. Do not delete rows or vectors.
6. Update/rebuild only exact structural IDs covered by a manifest.
7. Rebuild the affected Golden cases and issue a delta review packet. Human
   approval of the old version remains in the audit trail but does not approve
   changed legal claims automatically.
8. Run validity, retrieval, role, ingestion and stable-deployment gates.

## Rollback

- Restore PostgreSQL from the custom dump when SQL parity cannot be recovered.
- Restore the stopped Chroma directory from the checksum-bound copy, or remove
  only new exact IDs listed in the activation manifest when that is sufficient.
- Restore SurrealDB from its export when candidate/import/validity operational
  state must be rolled back.
- Never clear an old serving pointer before the replacement is completely
  verified. Strict validity remains fail-closed throughout rollback.
