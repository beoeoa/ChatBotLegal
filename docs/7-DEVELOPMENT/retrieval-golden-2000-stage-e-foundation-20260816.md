# Retrieval Golden 2,000 — Stage E foundation

Date: 2026-08-16

## Implemented slice

Stage E now has a fail-closed custody boundary. The 1,000 Golden regression
and 500 Hard-negative cases are the only case-level data allowed in the
workspace. The independent 500-case production holdout is represented here
only by `production-holdout-envelope-v1`; its questions, expected sources,
issue labels and reviewer answers remain in an external Legal-QA custody
bundle.

The envelope proves the exact case/domain counts and binds the hidden dataset
to its source snapshot, approved serving manifest, quota policy, quota
attestation, cross-split leakage audit and official-source approval manifest.
These hashes are evidence, not substitutes for the underlying Legal-QA review.

Every visible evaluation case now requires `case_sha256`. The validator also
rejects duplicate IDs, normalized duplicate questions, invalid refusal/source
contracts, missing issue/source-group links and incorrect domain/quota counts.

The M5/M6 selection runner validates only the 1,500 visible cases and the
public holdout envelope. It cannot use holdout results to tune configuration.
The final holdout runner rejects a custody bundle located anywhere under the
repository and emits an aggregate-only receipt. It strips per-case misses,
dataset errors and records. Any run consumes that holdout version; a failed
candidate requires a new independently authored holdout version.

## Zero-cost candidate generation result

The candidate authoring pass is complete and incurred USD 0 of API cost. It
used the installed local Ollama runtime only:

- Ollama `0.32.3` at `127.0.0.1:11434`;
- generation model `qwen2.5:3b`, digest
  `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`;
- embedding model `nomic-embed-text:latest`, digest
  `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`;
- pinned Ragas `0.4.3` and DeepEval `3.9.9` in the isolated
  `.venv-eval-generation` environment.

Both framework adapters passed a local-only smoke. Their initial 3B-generated
proposals were not trusted as legal cases: deterministic QA found source-type
hallucination, retained the proposals in the rejection evidence and replaced
them with source-bound deterministic transforms. The review pack therefore
contains 1,966 deterministic grounded transforms and 34 retained approved
legacy seeds; it contains no framework-generated legal question promoted by
automation.

The resulting pool contains exactly 2,000 pending candidates:

- Golden regression: 1,000;
- Hard-negative: 500;
- production holdout: 500 under external custody only;
- exactly 200/100/100 cases per domain for the three splits;
- scenario quotas 65/15/5/5/10 per 100 cases;
- 100% source seeds bound to owner-attested retrievable inventory records;
- 0 normalized duplicates;
- 0 semantic pairs at cosine `>= 0.9995` across all splits.

The audit is recorded in
`reports/retrieval-release-v2/retrieval-eval-candidate-audit-v1.json`. All 2,000
rows remain `pending_final_review`; synthetic candidates are not legal ground
truth and no reviewer identity or approval was fabricated. No database row,
source history, vector collection or active pointer was changed.

## Next gated work

1. The project owner reviews all rows in the external custody workbook and
   records `APPROVE`, `REJECT` or `REVISE`, reviewer ID and review timestamp.
2. Import the owner decisions, replace rejected/revised rows without breaking
   quota or custody boundaries, and rerun the deterministic and semantic gates.
3. Publish the public holdout envelope only after all 500 holdout rows have a
   real owner approval; do not place holdout questions in the repository.
4. Freeze the approved candidate before the single holdout run and validate
   only its aggregate receipt before final acceptance.
