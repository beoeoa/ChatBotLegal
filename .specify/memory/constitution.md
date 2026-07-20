<!--
Sync Impact Report
- Version change: template (unratified) -> 1.0.0
- Added principles:
  - I. Legal Accuracy and Source Traceability
  - II. Authority, Effectivity, and Jurisdiction
  - III. Account and Role Data Isolation
  - IV. Deterministic Retrieval and Explicit Fallbacks
  - V. Privacy, Security, and Auditability
  - VI. Specification, Small Changes, and Verification
  - VII. Local-First Performance and Observability
- Added sections: Legal and Technical Constraints; Development Workflow and Quality Gates
- Removed sections: none (template placeholders were replaced)
- Templates synchronized:
  - ✅ .specify/templates/plan-template.md
  - ✅ .specify/templates/spec-template.md
  - ✅ .specify/templates/tasks-template.md
- Runtime guidance reviewed:
  - ✅ AGENTS.md (already aligned; no change required)
  - ✅ README.md (no constitution-specific change required)
- Command templates: none present under .specify/templates/commands/
- Deferred items: none
-->
# ChatBotLegal Constitution

## Core Principles

### I. Legal Accuracy and Source Traceability (NON-NEGOTIABLE)

Every legal answer MUST be grounded in retrieved official or otherwise verified source material.
The system MUST NOT invent document names, issuing authorities, article numbers, dates, deadlines,
fees, procedures, or URLs. Answers MUST preserve available source URLs and legal metadata and MUST
clearly state when evidence is missing, conflicting, or insufficient. Model fluency MUST never be
treated as evidence. This principle exists because an unsupported legal answer can directly harm a
citizen's rights or administrative procedure.

### II. Authority, Effectivity, and Jurisdiction (NON-NEGOTIABLE)

Retrieval and answer generation MUST evaluate document authority, effective status, applicable date,
territorial scope, and subject matter before relying on a source. Higher-validity central law MUST
prevail over conflicting local material. Hai Phong documents MUST be prioritized only when applicable
and MUST NOT override superior law. Expired, superseded, draft, or unverified documents MUST be
excluded or explicitly labelled; safeguards enforcing these rules MUST NOT be removed without a
documented, stronger replacement.

### III. Account and Role Data Isolation (NON-NEGOTIABLE)

Citizen, officer, and administrator capabilities MUST be explicitly separated and verified. Every
private legal dossier, conversation, attachment, and derived record MUST have an owner or authorized
scope enforced on the server, not only hidden in the interface. One ordinary account MUST NOT read,
modify, delete, or otherwise affect another account's private records. Shared access MUST be explicit,
auditable, and required by a documented workflow. Authorization tests MUST cover direct API access,
identifier tampering, and each affected role.

### IV. Deterministic Retrieval and Explicit Fallbacks

The system MUST prefer deterministic parsing, metadata filtering, authority/effectivity checks, and
ranked retrieval before model interpretation. Retrieval context MUST remain inspectable enough to
explain why a source was selected. Optional parsers, embedding services, and models MUST fail
gracefully: the user MUST receive a clear fallback or limitation message instead of a crash or an
unsupported answer. Changing retrieval, ingestion, ranking, or grounding behavior requires a written
decision under `docs/7-DEVELOPMENT/`.

### V. Privacy, Security, and Auditability

Credentials, tokens, personal data, and dossier contents MUST NOT be committed, logged in plaintext,
or exposed to unauthorized users. Inputs and uploaded files MUST be validated at trust boundaries.
Sensitive operations MUST enforce authentication and authorization server-side and MUST create
useful audit records without leaking protected content. Test data SHOULD be synthetic or sanitized.
Any new paid external service, data transfer, or background daemon requires explicit approval before
adoption.

### VI. Specification, Small Changes, and Verification

Every non-trivial change MUST begin with a clear goal, assumptions, affected roles, failure risks, and
testable acceptance criteria. Implementation MUST be divided into the smallest reviewable slice that
delivers value. Risky behavior changes MUST be verified with the narrowest relevant automated tests
plus an API or browser journey when user-visible. Schema changes affecting imported legal records,
and deletion or rewriting of an existing legal corpus, MUST receive explicit approval before work
starts. Existing unrelated user changes MUST be preserved.

### VII. Local-First Performance and Observability

Core search and question-answering MUST remain usable in the supported local deployment. Plans that
affect retrieval or generation MUST define measurable latency targets and separate retrieval time,
model-generation time, and end-to-end time where practical. Slow or failed operations MUST expose
diagnostic timing and structured errors without sensitive data. Heavy optional dependencies MUST NOT
break the local-first default and MUST document their resource and fallback behavior.

## Legal and Technical Constraints

- Official metadata and source URLs MUST be preserved throughout ingestion, indexing, retrieval, and
  answer rendering.
- A feature specification affecting legal answers MUST identify source, authority, effective-status,
  jurisdiction, citation, and insufficient-evidence behavior.
- A feature involving private or role-scoped data MUST define ownership, access matrix, isolation
  boundaries, and negative authorization scenarios.
- Database schema or legal-corpus migrations MUST include backup, compatibility, validation, and
  rollback considerations and require approval when covered by Principle VI.
- Optional integrations MUST declare availability checks and a clear local fallback.
- User-visible legal limitations MUST be expressed in plain Vietnamese and MUST not imply certainty
  beyond the retrieved evidence.

## Development Workflow and Quality Gates

1. Specify the user outcome, roles, scope, assumptions, legal risks, and measurable acceptance tests.
2. Identify touched modules, data boundaries, source-grounding impact, and rollback needs.
3. Pass the Constitution Check before design and repeat it after design.
4. Implement one small, reviewable slice while preserving unrelated work.
5. Verify applicable unit, contract, integration, authorization, retrieval, and browser scenarios.
6. Record architectural decisions affecting legal accuracy, ingestion, retrieval, grounding, or
   background jobs under `docs/7-DEVELOPMENT/`.
7. Do not declare completion while a mandatory gate fails. Any justified exception MUST be documented
   in the plan's Complexity Tracking table with its owner and follow-up action.

## Governance

This constitution governs all specifications, plans, task lists, implementation, and review in this
repository. When guidance conflicts, the stricter legal-accuracy, authorization, privacy, or safety
rule prevails. `AGENTS.md` supplies operational repository guidance and MUST remain consistent with
this constitution.

Amendments MUST include a rationale, affected principles and templates, migration or compatibility
impact, and verification evidence. Removing or materially redefining a principle requires a MAJOR
version bump; adding a principle or materially expanding obligations requires a MINOR bump;
clarifications without semantic change require a PATCH bump. The initial adoption is version 1.0.0.

Every non-trivial plan and review MUST explicitly evaluate compliance with all applicable principles.
Reviewers MUST block changes that fabricate legal claims, bypass authority/effectivity safeguards,
weaken server-side isolation, leak protected data, or lack verification proportional to risk.

**Version**: 1.0.0 | **Ratified**: 2026-07-16 | **Last Amended**: 2026-07-16
