# Fresh pilot migration compatibility with SurrealDB 2.x

## Decision

The first application migration keeps the existing full-text indexes but declares
them with `SEARCH ANALYZER` syntax. The deployed database uses SurrealDB 2.x,
where this is the accepted syntax; `FULLTEXT ANALYZER` is the 3.x form.

## Evidence

The isolated Feature 3 pilot database ran migrations 1 through 37 successfully.
No production database, legal corpus, vector store, or migration version state
was modified during this check.

## Rollback and future upgrade

This source correction affects only fresh databases. Existing installations at
migration 37 do not rerun migration 1. A future SurrealDB 3.x upgrade must add
a reviewed migration for any index-definition change; it must not rewrite legal
records or alter the corpus as an incidental deployment step.
