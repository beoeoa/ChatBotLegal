# Answer Trust Contract

## Request contract

Every legal Ask request carries or derives:

- `question`, authenticated role/user scope.
- `legal_as_of` (default current server date, always returned).
- optional domain; officer domain remains server-authoritative.
- `trace_id`/request scope generated server-side.

## Internal intent contract

```json
{
  "domain": "cu_tru",
  "procedure_family": "dang_ky_thuong_tru_nha_thue",
  "action": "register",
  "target_object": "permanent_residence",
  "requested_facets": ["conditions", "dossier"],
  "legal_identifiers": [],
  "jurisdiction": "central_or_haiphong",
  "legal_as_of": "2026-08-10",
  "confidence": 0.97,
  "ambiguity_reasons": []
}
```

Ambiguous intent must return clarification/unsupported outcome before evidence binding.

## Evidence/claim contract

- Candidate may be retrieved broadly but becomes answer-eligible only after authority, jurisdiction, validity, request/issue scope and procedure/facet gates.
- Every public legal claim has at least one eligible evidence ID and an exact support quote.
- `metadata_only` citation cannot support a public legal conclusion.
- Claim validator rejects conflicting law/article, numbers, deadlines, fees, authority or issue scope.

## Answer modes

- `grounded_answer`: all public claims verified and completeness gate satisfied.
- `verified_source_condensed`: every retained claim verified; public label explains condensed provider fallback.
- `source_view_only`: source cards and limitation only; no legal conclusion and no “đã xác minh” answer label.

## Additive AskResponse fields

```json
{
  "generation_provenance": {
    "mode": "cloud",
    "provider_label": "configured-provider",
    "model_label": "configured-model",
    "redaction_applied": true
  },
  "citation_verification_summary": {
    "physical_span": 1,
    "content_quote": 2,
    "metadata_only": 0,
    "rejected": 0
  },
  "retrieval_decision_summary": {
    "intent_confidence": 0.97,
    "strict_validity": true,
    "fallback_reason": null
  },
  "intent": {},
  "validity_decision": {},
  "claim_validation": {}
}
```

Admin trace may contain internal IDs/offsets but public responses must not expose raw prompt, PII, provider errors, audit payload or private storage paths.

## Exact article contract

- Exact document/version and Article must match.
- Packet includes every known clause/point in structural order.
- Completeness reports expected, present, missing and duplicate units.
- Missing units prevent a complete-answer label.

## Error/fallback contract

All optional model/parser/provider failures return a stable reason code and safe mode. Overload, quota, timeout, stale validity, unsupported intent and missing evidence are distinct. Safety gates are never bypassed to recover availability.
