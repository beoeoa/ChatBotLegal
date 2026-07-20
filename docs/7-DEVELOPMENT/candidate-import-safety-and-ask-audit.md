# Candidate import safety and Ask audit metadata

## Status
Accepted

## Date
2026-07-11

## Context
Legal crawler candidates are reviewed by an admin before they may be used in the official legal answer corpus. The review queue and Ask audit history use SurrealDB tables configured as `SCHEMAFULL`; free-form metadata must therefore be represented explicitly and cannot silently break the answer path.

## Decisions

- Candidate approval calls Legal Search `/import` first. A candidate is set to `imported` only after that service accepts the official-source payload and creates its legal index record.
- If Legal Search rejects or cannot import a candidate, the candidate remains `approved` with a review note explaining the failure. No notebook source or notebook embedding job is created first.
- The crawler review, source-configuration, scan, and candidate endpoints remain admin-only through the existing `/api/legal/crawl` authorization boundary.
- `user_ask_history.sources` stores compact JSON source snapshots so structured retrieval metadata remains compatible with the SCHEMAFULL audit table. The history API deserializes them for admin consumers.
- Upload text is PII-masked before being returned or sent to the Ask pipeline. Citizen callers cannot list Ask history; officers receive only redacted upload-metadata summaries and no full RAG trace. Admins retain the audit view.

## Verification

- An unconfirmed synthetic candidate was approved through the API. Legal Search returned `400` because the official-source flag was absent; the candidate remained `approved`, no source record was created, and the law number was absent from legal retrieval.
- Citizen and officer review attempts returned `403`.
- Upload extraction masked CCCD, Vietnamese phone, and email values and returned retention metadata.
- Final regression gates: backend health, legal search health, 3-role smoke, legal golden-set validator, candidate workflow unit tests, and frontend production build.

## Operational note

A successful real import still requires a verified official source, valid scope/metadata, and a document that passes Legal Search validation. Admins must not use an unverified candidate merely to force a corpus import.