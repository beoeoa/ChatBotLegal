# Legal answer quality 9/10 implementation

## Scope

This change introduces the machine-enforceable foundation for the 9/10 legal
answer plan. It does not claim that the pilot has reached 9/10. Human legal
experts must still approve all 334 citizen/officer review records.

## Legal date propagation

`AskRequest` accepts `event_date` and `legal_as_of`. The effective date is
resolved in this order:

1. explicit `legal_as_of`;
2. `event_date`;
3. the current date.

The date is passed to the legal retrieval service as `as_of`, included in the
answer graph, trace, citation validation records, and response contract.

## Evidence coverage

Before exposing the final response, the backend creates deterministic coverage
states for conclusion, authority, documents, processing time, fee, penalty,
remedial measures, forms, and citations. Each item is one of `verified`,
`missing`, `conflicting`, or `not_applicable`. Current implementation reports
observable source support only; it does not ask the LLM to guess missing data.

Rejected deadline, fee, authority, and other sensitive claims from the existing
claim guard are recorded in `claim_validation`. Internal keys are translated to
Vietnamese before appearing in the answer.

## Duplicate request protection

The non-streaming Ask endpoint supports a client-generated `idempotency_key`.
The key is scoped by user and role. Duplicate in-flight calls wait for the owner
request; completed results are cached briefly. This prevents double-clicks or
retries from producing two different answers and duplicate history records.

The current store is process-local and intentionally small. A shared Redis or
database implementation is required before horizontal multi-process deployment.

## Expert review gate

Run:

```powershell
python scripts/prepare_expert_golden_review.py
python scripts/audit_expert_golden_review.py
```

The first command creates 334 pending records from 167 cases and two roles. It
keeps existing expected citations only as a machine proposal and does not
fabricate expert answers. The second command blocks release until all 334
records are approved with reviewer identity and review time.

`expert_disputed` and `needs_revalidation` are explicit non-release states.
Cases affected by changed laws must be returned to `needs_revalidation` by the
future document-relationship job.

## Remaining work before 9/10 can be claimed

- Populate authority, document relationship, and procedure matrices from
  reviewed legal data.
- Replace keyword evidence coverage with clause-level entailment validation for
  each legal claim.
- Complete expert review of all 334 records.
- Run the live benchmark per domain and role, including viewer/PDF/form health.
- Meet the release thresholds without any critical legal error.
