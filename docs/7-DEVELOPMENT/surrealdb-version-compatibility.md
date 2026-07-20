# SurrealDB version compatibility

The Docker pilot currently pins the database major line to `surrealdb:v2`.
SurrealDB 2.x uses `type::thing(table, id)` when constructing a record ID from
separate table and ID values. The two migration-version queries therefore use
`type::thing`.

Do not replace these calls with the SurrealDB 3.x
`type::record(table, id)` form while the Compose database remains on v2.
Single-argument `type::record(value)` calls elsewhere are type coercions and
are not covered by this compatibility rule.

Before changing the database major version:

1. Back up `surreal_data`.
2. Run all migrations against a copy of the database.
3. Audit every `type::thing` and `type::record` query.
4. Verify login, user ownership, ask history, and legal candidate review.
