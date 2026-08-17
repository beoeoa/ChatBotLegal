# M2 serving manifest

## Outcome

The runtime serving boundary is now the immutable, checksum-bound
`legal-serving-manifest-v2`. The active M2 dataset is
`legal-baseline-7245-m2-v1`, backed by the unchanged M1 collection
`legal_chunks_vnlegal_lal_haiphong_unified_v1`.

The manifest contains 7,245 documents and 168,155 chunk IDs. Its vector-content
embedding fingerprint is
`91147c770b9dc012c6dd45726698fcea4418aaef88660c337b11a5582489b675`.

## Truth and versioning contract

- A versioned manifest file is never rewritten. The builder accepts an
  idempotent rerun only when every semantic field is unchanged; any other
  change requires a new `dataset_version` and file.
- `active_serving_manifest.json` is the only mutable selector. It names a
  relative manifest path and binds both the file SHA-256 and canonical manifest
  SHA-256.
- Release retrieval starts with `LEGAL_SERVING_MANIFEST_REQUIRED=true`. A
  missing, malformed, mutated, collection-mismatched or count-mismatched
  manifest fails startup/health rather than widening retrieval.
- Unknown `source_id` and `validity_confidence` remain JSON `null`; the builder
  does not manufacture provenance or legal confidence.

The schema is in
`specs/018-production-release-readiness/contracts/legal-serving-manifest-v2.schema.json`.

## Enforcement points

- Vector core and expanded tiers use the manifest collection. Expanded serving
  cannot query the source-wide collection when a runtime manifest is active.
- Exact and lexical SQL, hydration, parent context and fallback SQL add the
  manifest document set inside the query, before result limits.
- Vector IDs, neighbor IDs and hydrated rows are filtered by manifest chunk IDs.
  Reranking receives only already-filtered rows.
- Document lookup, citation detail, content hydration and generated/cached PDF
  access enforce document visibility and chunk membership.
- The public API derives `audience` from authenticated request state and sends
  it server-to-server. Search, document and PDF cache namespaces cannot cross
  audience or manifest versions.
- Visibility levels are `public`, `officer`, `admin` and `internal`. Citizen,
  officer, admin and system audiences are evaluated in the backend loader.

Management inventory endpoints intentionally remain full-corpus admin tools;
they are protected by the existing backend admin gate and are not serving or
citation paths.

## Build and audit

```powershell
python scripts/build_m2_serving_manifest.py
python scripts/audit_m2_serving_manifest.py
pytest -q tests/test_m2_serving_manifest.py tests/test_legal_serving_scope.py tests/test_retrieval_serving_scope.py
```

Evidence is written to `reports/m2-serving-manifest/`:

- `m2_report.json`: build and immutable-pointer gates;
- `m2_acceptance_report.json`: requirements 25–28 plus vector, hydration,
  rerank, citation, exact database/vector set and live runtime checks;
- `m2_checksums.json`: SHA-256 values for the manifest, pointer and reports.

The local launcher and release compose both require the pointer. The active
Chroma pointer remains the M1 baseline; M2 did not activate the candidate or
rewrite any legal record/vector.
