# Release Readiness API Contract

All actor identity, role and domain values are derived server-side. Public responses never expose raw storage paths, tokens, audit hashes, reviewer notes or support content not owned/assigned.

## Ask compatibility

Existing `/api/search/ask`, `/api/search/ask/progress` and `/api/search/ask/simple` remain compatible. Responses add or normalize:

- `presentation_version = "legal-answer-v1"`
- `answer_status`, `answer_route`, `fallback_tier`
- `canonical_domain`, `legal_as_of`, `evidence_count`, `coverage_warning`
- `pipeline_version`, `data_release_id`, `index_fingerprint`, `validity_snapshot`
- `query_classification` using `legal-query-classification-m4-v1`
- `sections.short_answer`, `actions`, `dossier`, `procedure`, `recommended_forms`, `legal_bases`, `caveats`

Rule: form IDs/URLs and citation IDs/quotes are server projections, never accepted from provider prose.

Before retrieval, M4 emits structured domain, intent, scope, temporal scope and
answer type. Historical queries with an exact date use that date as the legal
validity boundary. Year-only or ambiguous historical wording without an
explicit precise `as_of` fails closed with no vector/lexical retrieval.

## Capability Registry

- `GET /api/admin/capabilities`: Admin-only filtered registry and readiness findings.
- `GET /api/admin/capabilities/{capability_id}`: Admin detail, tests, audit, rollback and route status.
- Registry is source-controlled; runtime endpoints are read-only.

Stable errors: `CAPABILITY_NOT_FOUND`, `CAPABILITY_FORBIDDEN`, `CAPABILITY_REGISTRY_INVALID`.

## Support

- `POST /api/live-support/tickets`: Citizen creates ticket after confirming canonical domain.
- `GET /api/live-support/tickets/mine`: Citizen lists owned tickets.
- `GET /api/live-support/tickets/{ticket_id}`: Owner, assigned officer or reason-authorized admin detail.
- `GET /api/live-support/tickets/{ticket_id}/queue-status`: Pollable position/estimate; no content.
- `POST /api/live-support/tickets/{ticket_id}/cancel`: Owner cancellation while queued/allowed state.
- `POST /api/live-support/tickets/{ticket_id}/messages`: Owner/assigned officer sends message after active assignment.
- `POST /api/live-support/officer/presence`: Officer heartbeat/capacity bounded by policy.
- `GET /api/live-support/officer/queue`: One scoped queue stream/poll endpoint.
- `POST /api/live-support/officer/claim-next`: Transactional allocator; no arbitrary ticket ID claim.
- `POST /api/live-support/tickets/{ticket_id}/resolve`: Assigned officer resolution.
- `POST /api/live-support/admin/tickets/{ticket_id}/view-content`: Admin reason-required content grant and audit.

Stable errors include `SUPPORT_DOMAIN_REQUIRED`, `SUPPORT_TICKET_FORBIDDEN`, `SUPPORT_TICKET_NOT_ASSIGNED`, `SUPPORT_CAPACITY_REACHED`, `SUPPORT_ASSIGNMENT_CONFLICT`, `SUPPORT_LEASE_EXPIRED`, `SUPPORT_CONTENT_REASON_REQUIRED`.

## Legal lifecycle and impact

- `GET /api/legal-management/lifecycle/summary?legal_as_of=...`
- `GET /api/legal-management/documents?bucket=expiring_30&vector_state=missing`
- `GET /api/legal-management/documents/{document_id}/timeline`
- `POST /api/legal-management/change-events/candidates`: Admin creates source-backed candidate only.
- `POST /api/legal-management/change-events/{event_id}/confirm`: Explicit confirmation with evidence fingerprint.
- `GET /api/legal-management/impact-cases?status=needs_review`
- `POST /api/legal-management/impact-cases/{case_id}/decision`
- `GET /api/legal-management/index-manifests/active`
- `POST /api/legal-management/index-jobs/preview`: Preview only unless a separately approved execution flag exists.

Current retrieval excludes expired/replaced provisions even when the replacement index is incomplete. Historical route may include them with label/date.

## FAQ governance

- `POST /api/faq/revisions`: Draft revision with question/answer/domain/procedure candidate.
- `POST /api/faq/revisions/{revision_id}/submit`
- `POST /api/faq/revisions/{revision_id}/approve`
- `POST /api/faq/releases/preview`
- `POST /api/faq/releases/{release_id}/activate`
- `GET /api/faq/releases/active`

Forms are absent from write requests. Public FAQ projection resolves forms from the confirmed procedure through the active Feature 017 release.

## Activity Center

- `GET /api/admin/activity?role=&module=&result=&actor=&from=&to=&cursor=`
- `POST /api/admin/activity/{event_id}/sensitive-view`: requires reason and creates audit event.
- `GET /api/admin/activity/export?format=csv|xlsx|pdf&...`: server-enforced filter and redaction.

## Compatibility and activation

- Legacy support/FAQ routes remain adapters during shadow mode.
- Source mode values: `json_compat`, `postgres_shadow`, `postgres_active`.
- Default remains compatibility/shadow until migration/reconciliation/release approval.
- Public activation and live mutation are never implied by schema creation or source approval.
