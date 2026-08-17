# Three-tier form resolution campaign

## Purpose

The campaign resolves Central, Hai Phong and Le Chan form occurrences without
changing the legal corpus, vector store or active collection. It separates
source occurrences from canonical forms and procedure bindings so progress
reports are not interpreted as a number of approved forms.

## Deterministic pipeline

1. Read the scoped inventory and create an immutable run baseline.
2. Normalize Unicode and resolve identity by form code, issuing instrument,
   appendix and jurisdiction.
3. Keep repeated generic codes in different instruments separate.
4. Exclude explicit supporting documents and issued results.
5. Record every occurrence with a reason code when identity/source/effectivity
   evidence is missing.
6. Resolve official documents and attachments through the existing candidate-only
   VBPL resolver, with validated attachment caching. Package records are
   deduplicated only after validation and only when their content SHA-256 is
   identical; different bytes remain independent ambiguity evidence.
7. Convert legacy DOC packages locally with LibreOffice when available.
8. Use the existing page-by-page `vie+eng` OCR adapter for scanned PDFs. Partial
   OCR cannot pass a hard gate.
9. Write source-attempt and effectivity sidecar artifacts. No model call creates
   metadata or a legal decision.
10. Reuse the authenticated atomic Admin attestation transaction. Only
    attested canonical records can become runtime-eligible.

### Curated Government/Công báo fallback

The current VBPL React endpoint does not return every older/current document
when searched by an exact number. For that narrowly scoped outage, the
resolver may use `notebook_data/forms/official_government_document_sources_v1.json`.
Each record is an exact document-number mapping to an allowlisted Government
legal-document page, an official current-status page, and an official download
URL. It is accepted only when the record's `verified_as_of` exactly matches the
campaign's `legal_as_of`; title matching, guessed dates and search-engine links
are never accepted.

This is discovery and provenance fallback only. The downloaded file still
passes MIME/magic/checksum/extraction checks, its exact form boundary is still
required, and the resulting record remains `candidate_pending_review` until an
authenticated legal reviewer attests it. The fallback never changes the
canonical catalog, runtime eligibility, active collection or feature flag.

### Preservation of prior human review

Source re-resolution may reproduce a candidate that an administrator has
already triaged. When the candidate ID, artifact checksum, source-package
checksum, official procedure code, procedure binding and proposed canonical ID
all agree, the queue refresh updates only reproducible source metadata and
retains the prior human disposition (`review_status`, reviewer/time/note,
catalog/legal-review status and local approved-file path). Existing hard gates
are unioned with incoming gates; they are never removed by a refresh.

Any conflict in those immutable fields is treated as
`candidate_provenance_drift` and fails closed. Retaining a prior approved
review marker does not promote serving: `approved`, `runtime_eligible` and
canonical promotion remain exactly as the human workflow set them. This avoids
both accidental declassification of reviewed records and automated runtime
promotion.

## Current live run (2026-07-29)

Run: `20260729124743-a9f2be2a`, legal date `2026-07-29`.

| Metric | Result |
|---|---:|
| Required identities | 278 |
| Runtime-approved baseline | 46 |
| Pending identities | 232 |
| Ready for human attestation | 30 identities |
| Terminal, fail-closed gaps | 202 identities |
| Unaccounted pending identities | 0 |
| Review batches | 2 (25 identities / 31 bindings, then 5 / 5) |

The immutable requirement manifest is bound by SHA-256
`349009e0e5bf12c1fcf68991c78680ae023f3e244f0b7e39a2a533bedfec02c4` and
the source snapshot by SHA-256
`2dedfc177b8cc12f972e8150616a44d9c71f14c9a2733f48b6d67ff0c26cde19`.
The campaign is `READY_FOR_HUMAN_ATTESTATION`; its feature flag remains false.

Review batching is identity-bounded, not row-bounded. A canonical form
identity can bind to several procedures, so the API returns every binding for
up to 25 identities and rejects an oversized identity batch without
truncating rows.

Each transaction is bound to the batch ID, v2 batch fingerprint, requirement
manifest checksum, source snapshot checksum and exact item set. Reviewer
identity comes from the authenticated server session; reviewer fields supplied
by a client are forbidden. The atomic transaction updates candidates,
canonical forms, bindings, official index, checksum manifest and audit log. It
is byte-idempotent when repeated and rolls all six artifacts back on a partial
replacement failure.

Official interactive eForms use a checksum-bound official route instead of a
fabricated local file. They can only become runtime-eligible after the same
human attestation. Completing the first batch leaves the second batch open;
only the last decided batch closes human review and advances to automatic
post-attestation release gates.

### Fail-closed source research queue

`reports/feature006/form-gap-research-queue.json` accounts for all 202 terminal
gap identities in the locked run. It preserves the nine prioritized reason
groups, includes only allowlisted official URLs, and records input checksums
and a deterministic queue fingerprint. The artifact contains no local paths,
question/answer text, credentials, approval or runtime-eligible record.

The initial queue has exact cached VBPL pages for 99 identities. Unicode NFC
normalization is allowed for canonically equivalent Vietnamese metadata while
mojibake and replacement characters remain rejected. Explicit structured form
codes now take precedence when a descriptive title lists multiple alternative
forms; this prevents distinct form identities from being merged by the first
code mentioned in prose.

Isolated, non-committing resolver probes recovered the exact official PDF
pages and SHA-256 values for identities `e014bccb8d0b3ea53e1df6bc`
(`TP-TSCC-01A`) and `7d45bf0709c92ba14580cb6e` (`TP-TSCC-01B`) under
instrument `06/2025/TT-BTP`. Exact form-level effectivity evidence also
recovered identity `cf291bee1e63ed1f78402228` (`TP-CC-06`) under instrument
`05/2025/TT-BTP`. The official amendment `11/2025/TT-BTP` replaces only
`TP-CC-01` through `TP-CC-04`; its exact replacement scope and the official
history URL are stored in
`notebook_data/forms/official_form_effectivity_evidence_v1.json`. The resolver
accepts this rule only when its instrument, target form code, verification
date, official URLs and replacement edges all pass deterministic checks.
The same exact replacement scope is recorded for `TP-CC-05` and `TP-CC-08`.
VBPL exposed duplicate records for identical official package bytes, which
caused the earlier bounded probes to repeat PDF extraction until timeout. A
test-first resolver correction now validates every file record, computes the
content SHA-256, and extracts each distinct package hash once. It never
deduplicates by filename, size or URL. Fresh non-committing probes then
isolated exact pages 33â€“34 for identity `389631f7223b3d32c15fce68`
(`TP-CC-05`) and pages 41â€“42 for identity
`2cd3efb27ba3505d5741042b` (`TP-CC-08`). Their extracted SHA-256 values are
respectively
`e70e10904a473826abf4e728f0116ad456b5c47f85941cfac9d17e54c3c1761b`
and `c9720345c07b7f5d850c42d251cc62b11958e5006279a1dd6014e65b6d832571`;
both local files were re-hashed successfully.

At that checkpoint, the queue recorded all five as
`TECHNICALLY_RESOLVED_PENDING_FUTURE_HUMAN_BATCH`. They have not been added to
the two locked human-review batches, approved, attested or made
runtime-eligible. The privacy-safe queue still accounts for 202/202 terminal
identities with zero unaccounted items; its SHA-256 after this checkpoint is
`9b04933f990b8b3ccc0d9535af3617b83b3a74fd119db065285c87167b247c4b`.

#### Current-form attachment evidence for Circular 53/2025/TT-BCA

The next test-first research slice resolved all three requirement identities
under `53/2025/TT-BCA`: `598b65a57ff99f0054654cd9` (`CT01`, nine procedure
bindings), `f3a150b0a873f1484276c119` (`CT02`, one binding), and
`01925ca7e4ac2a6d6adca5f3` (`DC01`, five bindings). The authoritative
requirement manifest and fresh resolver grouping proved that the middle
identity is `CT02`; the locked occurrence registry had retained `CT01` from
an older first-code parse. The research queue now preserves that baseline
value and records the deterministic correction reason
`STRUCTURED_PROBE_FORM_CODE_PRECEDENCE` instead of silently merging the two
forms.

The official full text at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=179354` explicitly replaces
the prior `CT01`, `CT02`, and `DC01` forms with the forms attached to Circular
53. The official status page at
`https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=179354&Keyword=` records
the circular as current from `2025-07-01`, while the official attachment page
`https://vbpl.vn/TW/Pages/vbpq-van-ban-goc.aspx?ItemID=179354` publishes one
standalone file for each exact code. The resolver therefore supports the
narrow evidence basis `official_current_form_attachment`. It accepts a rule
only when the exact form code, current-status URL, attachment-listing URL,
standalone filename, stable official download URL, verification date and
allowlist checks all agree. Missing fields, a non-official URL, or any filename
with a different/additional form code remains fail-closed.

Fresh non-committing probes produced the following locally re-hashed official
artifacts:

| Form | Group | File SHA-256 | Size | Bindings |
|---|---|---|---:|---:|
| `CT01` | `8b3269ed3ec4e70eebf33b84` | `1ec8d86fc8bc9a7cfbc6fbfe6e629330d0c5a5c4244a6d5967a2c7ecd075021c` | 67,584 | 9 |
| `CT02` | `fc7495e7ef79f43fac9f8f60` | `82aa5506e76710225aff13ff2f98cdc6784bcc58f9bf43db9ff7faafda961ec6` | 67,584 | 1 |
| `DC01` | `b7745b88ee12a9e9a42c28cf` | `a4a34d628bfb5f7caf799eac40d6b7ab281d5da5cc346e765819a5c3005c5fce` | 122,368 | 5 |

The queue builder now aggregates identical artifact evidence across all
procedure bindings of one requirement identity while retaining every
candidate/procedure ID. It still rejects a different group, code, instrument,
source, checksum or effectivity result as
`FORM_GAP_RESEARCH_PROBE_EVIDENCE_CONFLICT`.

The current privacy-safe queue remains 202/202 identities with zero
unaccounted items and zero runtime-eligible items. It now contains eight
technically resolved future candidates; `VERIFY_EXACT_APPENDIX_EFFECTIVITY`
dropped from 71 to 68. Its deterministic fingerprint is
`c6a7fd395a5f457f332dbbf831a11c45b160d47918408e89989da33fa9d04d7b`
and file SHA-256 is
`1e7d6b7358e68ee6d3b7655a9edb98a939b777bd051df65c16a3e3839ae67b0f`.
None of these three identities was added to the two locked review batches,
approved, attested or made runtime-eligible.

Verification for this slice: 71 focused source/effectivity/registry/e-form
tests passed; the complete form/e-form suite passed 177 tests with one
pre-existing Pydantic deprecation warning; three selected frontend files
passed 11 tests; TypeScript, ESLint and Ruff passed; the restore manifest
passed with five records. All seven locked manifest/runtime hashes, the 25/5
review batches, active collection and false feature flag remained unchanged.

#### Exact provision-scope evidence for Circular 09/2025/TT-BNV

The next test-first partial-effectivity slice recovered six more requirement
identities under `09/2025/TT-BNV`: forms `03` through `08`. The official parent
text at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=178434` ties forms `03` and
`04` to Article 11, forms `05`, `06`, and `07` to Article 12, and form `08` to
Articles 12 and 13. Article 11(2) of the modifying Circular `15/2025/TT-BNV`,
published at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=180280`, makes only Articles
3, 4, and 5 of Circular 09 ineffective from `2025-08-05`. The target-provision
sets are therefore disjoint from the exact partial-expiry scope. The resolver
records this narrow basis as `explicit_partial_provision_scope`; it does not
infer form effectivity from the parent status or title.

The official history page is
`https://vbpl.vn/bonoivu/Pages/vbpq-lichsu.aspx?ItemID=178434&Keyword=&dvid=320`.
The official DOCX package `09.2025.TT-NV.docx` is 128,426 bytes with SHA-256
`1eabca2e12d27dce2fe7391201baf916c268c4c69890cdd32af1245ed402e380`;
its stable official download is
`https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/buckets/vbpl/178434/09.2025.TT-NV.docx/download`.
The package contains the exact headings and official titles for forms `03`
through `08`. Extraction used the official scanned source PDF, 7,337,218 bytes
with SHA-256
`8a3ce801391f8b2b6b7c9cb5b0081c164f5e096d27256bdf55fef96b78523284`,
from the official attachment gateway.

| Identity | Form | Group | Target provisions | Source pages (zero-based) | Extracted SHA-256 | Size | Bindings |
|---|---|---|---|---|---|---:|---:|
| `7d3d23f5a17e9f5c4ecef2ae` | `03` | `0516e1516b72521fbd553382` | `11` | `23,24` | `c0afc1a468f531553e6d4201019bed9fe3b384fe074f2df4acb65b2eed06faa7` | 363,954 | 3 |
| `67e851e4c4043588b67441e3` | `04` | `2730e9c898d1016b32753cb4` | `11` | `25,26,27` | `77aeb08584b9b76491dce3634fff96bd638410977265484270a878c479e35e8f` | 472,882 | 3 |
| `0b33f4e6041cc16122b1a753` | `05` | `13e9507e38c289226b369b24` | `12` | `28` | `49e3b5b9d4677c3aae751ab4c85b47c9cca3d789f31bb5053d410f07ac32c2c8` | 115,905 | 1 |
| `f457cc473640d3cf6eaaae2f` | `06` | `ffb36d1d7f7bbeee7cf235c4` | `12` | `29` | `fc035c0839067709d26476ddafca026a7857de5d441042d3fe2bd88f980c0418` | 127,148 | 1 |
| `d1bea8c656b53510095f168e` | `07` | `81bec0687b86a9528296f5d9` | `12` | `30` | `1214b5c1956aed35eb4aa3ccf4a05b020dd35c81fcbba9cf89f57312f3391918` | 127,288 | 1 |
| `6a57d38ce4613f205683571c` | `08` | `c5e0ee340960996693db307b` | `12,13` | `31` | `6156b2cd6420784da8d5908958f4878223cde363ebdc68ec143d549cc3b0e97b` | 152,743 | 2 |

The scanned headings for forms `04` and `08` do not repeat an attachment
caption. A regression test exposed that the older PDF locator confused earlier
legal citations with the real form boundary. The locator now accepts only a
strict line beginning with the exact `Mẫu` heading, optionally preceded by a
printed page-number line. Earlier prose citations still fail the boundary
test, and incomplete extraction remains fail-closed.

The locked occurrence registry does not carry requirement identity IDs in the
legacy group metadata for this instrument, and one procedure can require more
than one form. The queue builder therefore gained a test-first exact fallback
keyed by `(procedure_id, normalized instrument, normalized form code)`. The
checksum-bound 278-item requirement manifest is authoritative for those exact
keys and overrides a legacy occurrence key when an older multi-code parser
collapsed several forms to the first code. Direct identity metadata remains
first priority; procedure-only fallback is still allowed only when it has
exactly one identity. A manifest checksum mismatch, missing identity, or
ambiguous exact key remains fail-closed.

The rebuilt privacy-safe queue still accounts for 202/202 terminal identities,
with zero unaccounted and zero runtime-eligible items. It now contains 14
technically resolved future candidates; `VERIFY_EXACT_APPENDIX_EFFECTIVITY`
dropped from 68 to 62. Its deterministic fingerprint is
`c347890bdc9a8130206c65654215b39fc60e788181b2664f453f02ddb8f79cf6`
and file SHA-256 is
`461384b222c2e43a705009a65466a9cc45195729b9766d0a668b640b5ccfabba`.
All six new records remain candidate-only with `approved=false` and
`runtime_eligible=false`. They were not inserted into the two locked review
batches, and no attestation or human decision was created.

#### Hierarchical provision-scope evidence for Decree 20/2021/ND-CP

The next test-first partial-effectivity slice recovered eight additional
requirement identities under `20/2021/ND-CP`: forms `1A`, `1B`, `1C`, `1Đ`,
`2A`, `2B`, `03`, and `07`. The official parent text is at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=148955`; its stable official
PDF is 2,552,919 bytes with SHA-256
`627922a72141c70f021d234fb616b42c7418baff391f4f01af3ed8823f469de1`.
The source is scanned, so OCR was used only to locate candidate page
boundaries. Each extracted package was then checked against the rendered
official pages and exact form heading.

The effectivity gate now understands hierarchical provision paths such as
`5.5.b` and `27.2.a`. A modifying scope overlaps a target when either is a
prefix of the other; only a proven disjoint scope may use the
`amends_other_provisions_only` or `expires_other_provisions_only` relation.
The gate can also record an official modifying provision that explicitly
confirms the target form binding. This prevents a broad `5.5` target from
being treated as unaffected when only `5.5.b` and `5.5.c` are known to have
expired.

The official modifying texts used for this decision are Decree
`104/2022/ND-CP` at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=167220`, Decree
`76/2024/ND-CP` at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=173641`, Decree
`147/2025/ND-CP` at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=178293`, and Decree
`176/2025/ND-CP` at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=184035`. Their exact scopes
support the eight candidates below. They do not safely resolve form `1D`
(`288d1f748d33f15db9885ffe`), whose target `5.5` overlaps the partial expiry
of `5.5.b` and `5.5.c`, or form `04`
(`ded796a675cb10596479155d`), whose broad appendix title and newer narrow
replacement path require a human legal decision. Both remain fail-closed.

| Identity | Form | Group | Source pages (zero-based) | Extracted SHA-256 | Size | Bindings |
|---|---|---|---|---|---:|---:|
| `359e7fb45a46f7e2765854e6` | `1A` | `518c45e91d90ac878c0beb29` | `30,31` | `232017b31524113a77bdb2a59aa397952eda1aa4ee8d54a5d061cd5e816fb70f` | 58,967 | 1 |
| `b2cfa2752e8a417f9cf14cb8` | `1B` | `a38a0afcb6f944e1e6e06f2d` | `32,33` | `f119333011bd3533bb57c4d8c03603f67065d4b5c8bdbfbffdd55f0ad625ec7c` | 50,693 | 1 |
| `6de23379d4bec7f59ef5dfb1` | `1C` | `393997082b810838503e1c0a` | `34` | `e707aa581188349b1096e9ae5d50d063ba6d29a74dcb8cf33d58cac9633e4589` | 45,415 | 1 |
| `7a9fea90c099987a0b6eea1a` | `1Đ` | `abbbc0f661b08d61d101a19a` | `37,38` | `18e411290050c23a058478c5fafd3aebb8294dac2f4dc9dd5b8e3f433d52d8cb` | 56,263 | 1 |
| `5023b5a1f755111930348fc5` | `2A` | `81feeb3b9b1b60598334fe70` | `39` | `a04c16cab3cf60829ae89423b0201e875c84851b353b67946770024e881e695e` | 43,015 | 1 |
| `04a14bd720075dec6ff5532f` | `2B` | `f208108ac7547ed9c77f646e` | `40,41` | `b4622b8e9e5209a44f7c4fc60f3262ae152f6f55352f05946c5bf9c3f5adb6ad` | 72,861 | 1 |
| `72c6de7e8899331fcb53b8c3` | `03` | `489e624bec381a63c2f21b76` | `42,43` | `75d8a69c25f07baa336cb5358e8affc60f6a3520265dec9f57d9ff0a4f2116ab` | 50,367 | 1 |
| `7d01a5808453b84e8409eb6f` | `07` | `36751fac5d9d0e56a4924355` | `50` | `da89cf0a53329ab2ac6e0d232c62117654db5b373c4d4133fd91a5a483585d20` | 41,189 | 2 |

The PDF locator also gained two OCR-safe corrections: it accepts `Mau so` as
an accent-loss variant of `Mẫu số`, and an exact heading line is no longer
rejected because unrelated body OCR resembles another form code. Separately,
the research queue now displays the checksum-bound manifest form code as the
authority even when a technical probe is absent, while preserving legacy
registry codes in `baseline_form_codes` for audit.

This checkpoint supersedes the prior 14-candidate queue snapshot. The rebuilt
privacy-safe queue still accounts for all 202/202 terminal identities, with
zero unaccounted and zero runtime-eligible items. It contains 22 technically
resolved future candidates; `VERIFY_EXACT_APPENDIX_EFFECTIVITY` is now 54.
Its deterministic fingerprint is
`6ff707d0c7039553a84d8fcc3a172f03d501964bd84246d63dfa3e7db92e82c2`
and file SHA-256 is
`c795d61c6f32e3f93c8780d246fb477c098de094df3076eb830a6a237e97f595`.
The future-review batch remains below the 25-item cap. All 22 records remain
candidate-only with `approved=false` and `runtime_eligible=false`; T035, the
locked 25/5 review batches, and the release flag were not changed.

The live official form index has an independent workspace drift and was not
overwritten: it currently has 756 rows and SHA-256
`6f3df5fe09e753708f1ab6eb79075f98d69399be62847599671f6622be63de03`,
versus the locked 754-row SHA-256
`561c8795dc7a31866153537aa1ef39518883055ccc2b5d05b0e52ad92ba5225b`.
That difference is kept visible for the real reviewer instead of being folded
into this research-only slice. The other six locked manifest/source/runtime
hashes still match their baseline values. The two locked review batches remain
at 25 and 5 identities, with no approved, runtime-eligible, or automated record.

Verification for this slice: the complete form/e-form suite passed 183 tests
with one pre-existing Pydantic deprecation warning; Ruff passed on every
changed Python module and test; the five-record restore manifest passed; the
queue JSON reports 202 identities, 22 technically resolved candidates, 54
exact-effectivity research actions, zero unaccounted identities, and zero
runtime-eligible identities. `.specify/extensions.yml` is absent, so no
extension-defined completion step applies.

#### Exact appendix evidence for Decree 96/2023/ND-CP

The next test-first partial-effectivity slice recovered six additional exact
appendix identities under `96/2023/ND-CP`. The official Government metadata is
at `https://vanban.chinhphu.vn/?classid=1&docid=209491&pageid=27160&typegroupid=4`;
the signed 287-page package at
`https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/01/96-nd.signed.pdf` is
10,680,628 bytes with SHA-256
`48773833e84ec6804b367a5f9e2f0393b698b75a40bc5c064dba52f20ab730a6`.
Every extracted boundary was checked against the rendered signed pages.

The form-to-provision links were checked in the official full text at
`https://vbpl.vn/bovanhoathethao/Pages/vbpq-print.aspx?ItemID=168128`. Decree
`207/2025/ND-CP`, effective 2025-10-01, is recorded at
`https://vbpl.vn/thanhtrachinhphu/Pages/vbpq-toanvan.aspx?ItemID=179714&Keyword=`
and makes only paragraph 9 of Article 40 ineffective. The six target scopes in
Articles 26, 68, 83, 130, 132, 134 and 136 do not overlap that provision.

| Identity | Form | Appendix | Group | Source pages (zero-based) | Extracted SHA-256 | Size | Bindings |
|---|---|---|---|---|---|---:|---:|
| `bb48a1b8c2bb2015453e177b` | `03` | `IV` | `3fb0924b86bb279c07081e73` | `229,230` | `e7cb9460643e665b28ee683af0627b53e0a35e066c945be959fcfaefed96211d` | 33,857 | 1 |
| `1e205e95d81f59ca4f806dc5` | `04` | `II` | `b54e8a7c79785f5fbb99eb8f` | `215` | `8ae1e7719676e2b73a42da15afa2c7b98c12d7f92ef44354aff5585f291f5ef0` | 21,010 | 1 |
| `2a8d1d6f26a42a296f223e84` | `05` | `II` | `771c82bc3f6dd4c9c45a75a9` | `216` | `b0d41e9ca291033256880a1c4441c94724e32e7ceb387d9000f8058d20d15675` | 19,038 | 1 |
| `9cd7fc8194132a0fc850b4f5` | `07` | `I` | `752a0466a0c24b77f35feb7b` | `202` | `8c7da418a944f9a9ae3804cb0beab848b1f58d57c3677a476d0eee04bd090441` | 24,360 | 3 |
| `b9f20ae0c336f1ab00eef111` | `08` | `I` | `36346544ff6a4d78efabeb8e` | `203,204` | `4b065bcce5a2a8006be6fa35d5bd04288f4a3f02f15bc6f1da213204228c18d7` | 41,632 | 5 |
| `87d0e47572929c08694334d9` | `09` | `I` | `322f537290a412d9150fa56c` | `205,206` | `1bd49fc4fb8f5c600fe9b89321161935a8a454efab71a8d7c17f2216a7c268a5` | 47,025 | 2 |

Forms `01` (`93ad259f8dfda52c13feb76a`) and `02`
(`2e30adcc1e59fa97c95491db`) were deliberately not recovered: the requirement
manifest collapses same-code forms from multiple appendices, so neither code
alone identifies one legal form. Both remain
`VERIFY_EXACT_APPENDIX_EFFECTIVITY`, `FAIL_CLOSED`.

The resolver now accepts an explicitly supplied direct official package only
after validating the requested and final redirect domains, size, MIME/magic,
file openability, malware scan and checksum. Appendix is part of the grouping,
effectivity and extraction key. The research handoff also retains the exact
appendix and partitions technical candidates into immutable candidate-only
batches of at most 25; the current future handoff is 25 and 3.

This checkpoint supersedes the 22-candidate queue snapshot. The rebuilt queue
still accounts for 202/202 identities with zero unaccounted and zero
runtime-eligible items. It contains 28 technically resolved future candidates;
`VERIFY_EXACT_APPENDIX_EFFECTIVITY` is now 48. The queue fingerprint is
`0c40a81427d7fce414cd7b2dea5fd4199de58a87275798f219e843f4d4bcf7f3`
and its file SHA-256 is
`53cde48ad07b3684a55fcec0dca387e203af8a1cc2bb55d13570d0f3a29dfe10`.
All six new forms remain `approved=false`, `runtime_eligible=false` and require
human attestation. T035, the locked 25/5 review batches, active collection and
false release flag are unchanged.

Verification: the complete form/e-form suite passed 190 tests; the frontend
passed 101 tests, TypeScript, ESLint and the production build; Ruff passed on
all changed Python modules and tests; and the five-record restore manifest
passed. The known independent official-index drift remains visible and was not
overwritten. `.specify/extensions.yml` remains absent.

### Partial-effectivity slice: `08/2023/TT-BLĐTBXH`

Five additional identities were recovered from the official document record at
`https://vbpl.vn/van-ban/chi-tiet/thong-tu-so-08-2023-tt-bldtbxh-sua-doi-bo-sung-bai-bo-mot-so-dieu-cua-cac-thong-tu-thong-tu-lien-tich-do-bo-truong-bo-lao-dong-thuong-binh-va-xa-hoi-ban-hanh-lien-tich-ban-hanh--167031`.
The stable official PDF package is
`https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/buckets/vbpl/167031/VanBanGoc_Th%C3%B4ng%20t%C6%B0-08-2023-TT-BL%C4%90TBXH.pdf/download`.
It is 930,044 bytes with SHA-256
`e341fcdbfd0e10d57fd30bfcf04fdf44f1bd5aa68173deea51e75f6bbf7af7dc`.

The official history page is
`https://vbpl.vn/TW/Pages/vbpq-lichsu.aspx?ItemID=167031`. The exact modifying
text at `https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=178434` makes only
forms `05` through `15` issued with `08/2023/TT-BLĐTBXH` ineffective from
2025-07-01. That form-code scope is disjoint from all five targets below. No
appendix identifier was inferred: the source package labels the collection only
as an unnumbered appendix, so the evidence remains keyed by exact instrument and
form code.

| Identity | Form | Group | Source pages (zero-based) | Extracted SHA-256 | Size | Bindings |
|---|---|---|---|---|---:|---:|
| `ff7cefec27ae47e0c8218e8e` | `18` | `5a66a120b05e50b1054136ce` | `27` | `ef6bbc0094ec0e7691ffd52538d7856034b1fefcf17451f4c6d16353dedf6c96` | 219,209 | 1 |
| `b410b7bfb648cce384c05971` | `19` | `eaa203d5ee3a4bf7ff9827e6` | `28,29` | `12687509538cb32ea22897d37e6471402ed4c416e73eb85f2c857b8eef08fe84` | 244,415 | 1 |
| `04c256ce0c38805288ada61b` | `21` | `00507d203198cda5e2b63488` | `31` | `d64a97c7ab7458fa8978d062c8be2f77730d92f179956c0b53821d3bc638a44a` | 218,461 | 1 |
| `549f40bf94ecec6c2d74ea28` | `23` | `2a650dfa4de238c1f47bb33c` | `33` | `ce125148fb041369bceb9ef1fe92cc7e4b2018cda5f03b6b18e248f3334b6c77` | 218,406 | 1 |
| `ed68d7c7832413000f8aad00` | `24` | `022f7c0e1bc8eb5a5915f115` | `34` | `7caa02553a502c1030b492292b4de7d8b3843899782d7b6db456ed18a1a95d06` | 218,825 | 1 |

PDF text extraction split the headings for forms `18` and `19` across several
glyph lines. The locator now accepts that pattern only in the exact first
heading block and still stops at the next exact form. Rendered physical pages
28, 29, 30, 31, 32, 33, 34 and 35 were inspected: page 30 is the shared note
for forms `18` and `19`, while pages 31 and 33 start forms `20` and `22`, which
confirms the selected boundaries. When the explicitly supplied official package
duplicates a package-discovery attachment, the resolver now retains its direct
evidence precedence without treating a different official format as the target.

The rebuilt research queue still accounts for 202/202 identities with zero
unaccounted and zero runtime-eligible items. It now contains 33 technically
resolved future candidates; `VERIFY_EXACT_APPENDIX_EFFECTIVITY` is 43. Future
human handoff remains candidate-only in batches of 25 and 8. The queue
fingerprint is
`e1e9b44c681b06d857d9f02aa2d4f6d9fc762ac4ace13ee69db8436a9e039442`
and its file SHA-256 is
`8577482ad482f897a2a28031215ea25d419bc6b0897f69ec6d4afd0b4edbf98c`.
All five new records remain `approved=false`, `runtime_eligible=false`; T035,
the locked 25/5 first-review batches, active collection and false release flag
are unchanged.

Verification: 66 focused source-resolution/research-queue tests and the broader
321-test form/e-form selection passed. The frontend passed 101 tests, TypeScript,
ESLint and an isolated production build. Ruff passed on all touched Python files,
and the five-record backup manifest verified successfully. Manifest/source hashes
remain `349009e0e5bf12c1fcf68991c78680ae023f3e244f0b7e39a2a533bedfec02c4`
and `2dedfc177b8cc12f972e8150616a44d9c71f14c9a2733f48b6d67ff0c26cde19`.
The known independent 756-row official-index drift remains visible and was not
overwritten. `.specify/extensions.yml` remains absent.

### Partial-effectivity slice: `11/2025/TT-BTP`

Two additional identities were recovered from official document `178734` at
`https://vbpl.vn/van-ban/chi-tiet/thong-tu-so-11-2025-tt-btp-sua-doi-bo-sung-bai-bo-mot-so-dieu-cua-cac-thong-tu-thuoc-linh-vuc-quan-ly-nha-nuoc-cua-bo-tu-phap--178734`.
The exact official appendix package is
`https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/buckets/vbpl/178734/VanBanGoc_PH%E1%BB%A4%20L%E1%BB%A4C%20TH%C3%94NG%20T%C6%AF%2011.pdf/download`.
It is 2,489,172 bytes with SHA-256
`9ad6442ba0ad14e2584ecd8c99facfcb7a92bea101d37edc7646148e7aae7b4b`.

The official history at
`https://vbpl.vn/TW/Pages/vbpq-lichsu.aspx?ItemID=178734` records one amendment,
`24/2025/TT-BTP`, effective 2025-12-01. Its official text at
`https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=184060` changes only the
number and title of Article 10 to Article 11 and Article 11 to Article 12.
Both target forms are expressly bound by paragraphs 1 and 2 of Article 7, so
the target scope does not overlap the technical renumbering.

| Identity | Form | Group | Source pages (zero-based) | Extracted SHA-256 | Size | Bindings |
|---|---|---|---|---|---:|---:|
| `f67d38161edf945af67cc0e0` | `TP-CC-02` | `251d31dbad31825c5c1ac7c9` | `49,50,51` | `ff1fac1d5ec5d476e9a11d4e63a7c399daa9622321cc9a6a9b873fe36809a8af` | 451,654 | 1 |
| `db50cd8100009c38313b9d97` | `TP-CC-04` | `234b75e0b6b88910929abcf3` | `54,55` | `362fd2649039f65dbe3da2cf60df400dbb8327fdb28fc4fccaff1f694bdbfb2a` | 335,999 | 1 |

Rendered physical pages 50 through 57 were inspected. Pages 50–52 contain the
complete `TP-CC-02` form and notes; page 53 begins `TP-CC-03`. Pages 55–56
contain the complete `TP-CC-04` form and notes; page 57 begins a different
`01-TP-TGPL` form. These boundaries agree with the deterministic PDF locator.

This checkpoint supersedes the 33-candidate queue snapshot. The rebuilt queue
still accounts for 202/202 identities with zero unaccounted and zero
runtime-eligible items. It now contains 35 technically resolved future
candidates and 41 `VERIFY_EXACT_APPENDIX_EFFECTIVITY` items. Candidate-only
future batches are 25 and 10. The queue fingerprint is
`a1aa4a755b4218f6d2305d1f789c400bb0378ae6bff38ad5840abff801fd06dc`
and its file SHA-256 is
`6a9a54f0c16b8ceb3e29e6cb5098d5d35bb3ff61b0d9d82e17bcdf7eb8383d74`.
Both new records remain `approved=false`, `runtime_eligible=false`; T035, the
locked first-review batches, active collection and false release flag are
unchanged.

### Partial-effectivity slice: `95/2024/NĐ-CP`

One additional identity was recovered for procedure `1.012890`: Appendix I
Form `01`, “Đơn đề nghị gia hạn thời hạn sở hữu nhà ở tại Việt Nam”, identity
`a7e608e0e2052ac6e4faca15`. Article 6 paragraph 1 point a of the official
decree binds this exact form to the procedure.

The official attachment is the seventh scanned segment:
`https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/buckets/vbpl/169709/VanBanGoc_95_2024_ND-CP_24072024-signed-trang-7.pdf/download`.
It is 7,994,117 bytes with SHA-256
`0e8a9dc8bc00dbcd5bffd6bcfcdd1872b0e17850b349b911d6107e3ece4a2ec2`.
Complete OCR covered 18/18 pages with no failed page. The resolver extracted
source-package page 7 (zero-based page `6`) as a 417,488-byte PDF with SHA-256
`0897f0af9e24d769c17a48727d07da38639ba1ff42785042dd0f531df3b6b076`.

The official current-history evidence records `178/2025/NĐ-CP` and
`54/2026/NĐ-CP`. Their exact amendment scopes cover other
articles/appendices, not Article 6 or Appendix I. Visual inspection confirmed
that source-package page 6 is the Appendix I index, page 7 contains the
complete Form `01`, and page 8 starts Form `02`. The clean probe reports one
resolved group, zero verified gap, zero effectivity-evidence rejection and zero
automated approval.

This checkpoint supersedes the 35-candidate queue snapshot. The rebuilt queue
still accounts for 202/202 identities with zero unaccounted and zero
runtime-eligible items. It now contains 36 technically resolved future
candidates and 40 `VERIFY_EXACT_APPENDIX_EFFECTIVITY` items. Candidate-only
future batches are 25 and 11. The queue fingerprint is
`628e103b087d1846d882adfd13174fe4500798804ef6091d016c7f352fa87cc4`
and its file SHA-256 is
`9ba6bb6172b2215b0e533ade54f937d00c94542f5d20e897a560345d5abe1be1`.
The new record remains `approved=false`, `runtime_eligible=false`; T035, the
locked first-review batches, active collection and false release flag are
unchanged.

Verification: 67 focused source-resolution/research-queue tests and the broader
269-test form/e-form selection passed. The frontend passed 101 tests,
TypeScript, ESLint and the production build. Ruff passed on the touched Python
files, and the five-record rollback backup verified successfully. Catalog,
binding, attestation and canonical-checksum files still match the locked
baseline. The known independent 756-row official-index drift remains at
SHA-256
`6f3df5fe09e753708f1ab6eb79075f98d69399be62847599671f6622be63de03`
and was not overwritten. Manifest/source hashes remain
`349009e0e5bf12c1fcf68991c78680ae023f3e244f0b7e39a2a533bedfec02c4`
and `2dedfc177b8cc12f972e8150616a44d9c71f14c9a2733f48b6d67ff0c26cde19`.

### Partial-effectivity slice: `17/2020/NĐ-CP`

Five additional identities were recovered from official document `140944`:
forms `01A`, `01B`, `02A`, `02B` and `04` in Section I of the appendix to
`17/2020/NĐ-CP`. The official detail page is
`https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-17-2020-nd-cp-sua-doi-bo-sung-mot-so-dieu-cua-cac-nghi-dinh-lien-quan-den-dieu-kien-dau-tu-kinh-doanh-thuoc-linh-vuc-quan-ly-nha-nuoc-cua-bo-cong-thuong--140944`.
The signed official PDF is
`https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/buckets/vbpl/140944/VanBanGoc_17.signed.pdf/download`.
It is 3,326,190 bytes with SHA-256
`1951c84d57d374dfe7db5375378150103427d75b5210c9b9aff3a2bc8491c92c`.
Complete OCR covered 75/75 scanned pages with no failed page.

The exact bindings are Article 12 paragraph 1 point a for `01A`; Article 12
paragraph 2 points a–d for `01B`; Article 12 paragraph 1 point c for `02A`
and `02B`; and Article 13 paragraph 1 point d for `04`. The official
partial-effectivity history is disjoint from those target provisions:
`33/2024/NĐ-CP` replaces Articles 6 and 7, `61/2025/NĐ-CP` repeals the
base-decree scope amended by Article 3, and `26/2026/NĐ-CP` repeals Articles
8 and 9.

| Identity | Form | Procedures | Source pages (zero-based) | Extracted SHA-256 | Size |
|---|---|---|---|---|---:|
| `5a1b26b47576702c1617911b` | `01A` | `2.000591` | `34,35,36` | `608667f10ee8095462453406de3241f5be9f07571789f1947184de7c8ceb2d5c` | 84,665 |
| `a8d6c667b2a4b601c12f74fa` | `01B` | `2.000115` | `37` | `e31b7149b226fd969e25d39934f55e9a0f0bac4193890e20f65d1ba6c00c43e9` | 17,717 |
| `f76ca6a1064ea67d823788db` | `02A` | `2.000591` | `38,39` | `190307e9f4ee37278d007074fe81e96882eb2230d08a94d3844eae7140dc8296` | 69,989 |
| `fb1a223d66baa22abd159d6b` | `02B` | `2.000115`, `2.000117`, `2.000591` | `40,41` | `1b368764709b27d16fc92a8cf57ed2cc42cae909619f222ff5f20a9a7c1895bf` | 66,181 |
| `7c3edb205e2e806ef1d493ba` | `04` | `2.000115`, `2.000117`, `2.000591` | `49` | `29304761c197ca5fa048f16ead39f7b3fb040e280218df3a4c18b5b8ab0c3903` | 20,347 |

Rendered source pages 35 through 43 and 50 through 51, plus every extracted
candidate page, were inspected. `01A` legitimately spans three pages including
its establishment and product-group lists; the following page begins `01B`.
`01B`, `02A`, `02B` and `04` end before the next exact forms and contain no
extra or clipped pages.

The clean probe reports five resolved groups, nine candidate bindings, zero
verified gap, zero effectivity-evidence rejection and zero automated approval.
This checkpoint supersedes the 36-candidate queue snapshot. The rebuilt queue
still accounts for 202/202 identities with zero unaccounted and zero
runtime-eligible items. It now contains 41 technically resolved future
candidates and 35 `VERIFY_EXACT_APPENDIX_EFFECTIVITY` items. Candidate-only
future batches are 25 and 16. The queue fingerprint is
`1db251686ac967b555bcd81c75530ee6fc1c3166db9e20872d06db3f2f57b820`
and its file SHA-256 is
`0519e0f6b86a9b1149aefa8755bc40f168ab55f41efff1a1b452e71a29599fa0`.
All five records remain `approved=false`, `runtime_eligible=false`; T035, the
locked first-review batches, active collection and false release flag are
unchanged.

Verification: 67 focused source-resolution/research-queue tests and 266
form/e-form tests passed. The three tests in
`test_step4_performance_runtime.py` were not collectable in this environment
because the optional `chromadb` package is absent; no dependency or runtime
service was changed to hide that limitation. The frontend passed 101 tests,
TypeScript, ESLint and the production build. Ruff and whitespace checks
passed. The five-record rollback backup verified successfully. Manifest,
source snapshot, catalog, binding, attestation and canonical-checksum hashes
still match the locked baseline. The known independent 756-row official-index
drift remains at SHA-256
`6f3df5fe09e753708f1ab6eb79075f98d69399be62847599671f6622be63de03`
and was not overwritten. `.specify/extensions.yml` remains absent.

### Partial-effectivity slice: `148/2025/NĐ-CP`

Five additional non-colliding identities were recovered from official
document `178254`: Appendix IV Form `01` and Appendix V Forms `03`, `07`, `08`
and `12`. The official detail page is
`https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-148-2025-nd-cp-quy-dinh-ve-phan-quyen-phan-cap-trong-linh-vuc-y-te--178254`.
The relevant signed VBPL PDF segments are:

| Segment | SHA-256 | Size |
|---|---|---:|
| `VanBanGoc_148-2025-nd-cp-trang-4.pdf` | `ed3ca8cb8ea0a12fa1b84ccdac9807fb74586f6636ebb8b0827d61b6424c54d1` | 7,209,590 |
| `VanBanGoc_148-2025-nd-cp-trang-5.pdf` | `6ef20f025c1dd6fd37ced4d38e4cdbf01354cf19aa1d666e0860fa57fe8d73bc` | 8,955,768 |
| `VanBanGoc_148-2025-nd-cp-trang-6.pdf` | `e108e9bb889cb64debfd287b49e27d3072fa8b59cc2c643a86d451d9e466949a` | 6,565,530 |

Complete OCR covered every page of all three segments without a failed page.
The exact extracted candidates are:

| Identity | Form | Procedures | Segment pages (zero-based) | Extracted SHA-256 | Size |
|---|---|---|---|---|---:|
| `e8ebd84da20204f993e0e284` | `01` | `3.000447` | `4:8,9` | `1e1b11fabea3a16f8095e5c6f16c110cef25a44a7711a7a360ac2919dbd04d4b` | 521,728 |
| `858a81887692315bf31526b6` | `03` | `1.013858` | `5:18,19` | `e67f6b46f15a680c5f9c3328166b11c539b350f33fe4509bd0df60ee369c1d44` | 553,407 |
| `037013955ab4246494bfc9a7` | `07` | `1.013844`, `1.013850`, `1.013857` | `6:3` | `37884297aebdaaf4bf40c1b32f3df70d2a5e0951cc80566fc05c8424fde845e1` | 287,965 |
| `b3a30c3037c81f027cac654c` | `08` | `1.013844`, `1.013850`, `1.013854`, `1.013857` | `6:4,5` | `b5d730ce24e36fd157099f197b0b3c75e310322814e7b59776477f4b6480e636` | 592,130 |
| `d12a0660119160e66c9da4eb` | `12` | `1.013829` | `6:10,11` | `af07645194a1e6abaddcf760a27cbf98a2affb2a0b0860fa89599d922c7c9a2b` | 603,473 |

Article 20 binds Appendix IV Form `01`; Article 29 binds Forms `07` and `08`.
Those provisions are disjoint from the repeal scope published with
`46/2026/NĐ-CP`. Article 28 paragraph 1 binds Form `03`, and Article 30 binds
Form `12`; both overlap that repeal scope. The current-law chain is handled
explicitly rather than inferred from the stale parent status:

- the official publication of `46/2026/NĐ-CP` is
  `https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-46-2026-nd-cp-468886.htm`;
- `15/2026/NQ-CP`, effective `2026-04-06`, suspends
  `46/2026/NĐ-CP` until the replacement food-safety legislation and its
  implementing decree take effect:
  `https://vanban.chinhphu.vn/?classid=1&docid=217667&pageid=27160`;
- the signed Resolution 15 PDF is 271,626 bytes with SHA-256
  `0d66f6e503c561bf3093ecf70fc686554c28f74c64c7a2ed40226b0d9827b186`.

The deterministic effectivity validator now accepts
`explicit_suspended_repeal_scope` only when every target-overlapping repealer
has a matching active suspension edge, both document numbers match exactly,
all dates are on or before `legal_as_of`, and every source and form package is
official and checksum-bound. Missing, future, inactive, unofficial or
mismatched edges fail closed. Ordinary disjoint repeal evidence is recorded as
`repeals_other_provisions_only`.

Forms `05` and `06` were deliberately not resolved. The checksum-bound
manifest currently collapses two different official artifacts under each
`form_code + issuing_instrument` identity: Appendix IV medical-device Forms
`05`/`06` and Appendix V food-testing Forms `05`/`06`. Binding either file to
the collapsed identity would serve the wrong form to at least one procedure.
The research queue therefore marks them
`SPLIT_COLLIDING_FORM_IDENTITY`, without changing the locked 278-identity
manifest. A manifest-wide deterministic audit found 10 such cross-appendix
collisions; they remain fail-closed pending an authorized appendix-aware
identity-model change.

Probe run `20260729143555` reports five resolved groups, ten procedure
bindings, zero verified gap, zero effectivity-evidence rejection and zero
automated approval. All nine rendered candidate pages were visually inspected;
each starts at the exact form header, contains the complete form and stops
before the next form.

The rebuilt queue still accounts for 202/202 terminal identities with zero
unaccounted and zero runtime-eligible items. It now contains 46 technically
resolved future candidates, 26 `VERIFY_EXACT_APPENDIX_EFFECTIVITY` actions and
10 `SPLIT_COLLIDING_FORM_IDENTITY` actions. Candidate-only future batches are
25 and 21. The queue fingerprint is
`d23d9bd30dc21c7ffcef23dff8309e297f8878cd80ec3027ce19e4d8545e9c34`;
its file SHA-256 is
`b44649353a7d59e564124d75811c654570987ad9f54fe25c178a96362c080492`.
All new records remain `approved=false`, `runtime_eligible=false`. T035, the
locked initial review batches, active collection and false release flag remain
unchanged.

Verification: 71 focused source-resolution/research-queue tests and 270
form/e-form tests passed. The frontend passed 101 tests, TypeScript, ESLint and
the production build. Ruff passed on all touched Python modules and tests. The
five-record rollback backup verified successfully. Manifest, source snapshot,
catalog, binding, attestation and canonical-checksum hashes still match the
locked baseline. The known independent 756-row official-index drift remains
`6f3df5fe09e753708f1ab6eb79075f98d69399be62847599671f6622be63de03`
and was not overwritten. The active collection remains
`legal_chunks_lechan_primary_v20260723`, and
`LEGAL_SECTION_GROUNDING_ENABLED=false`.

### Current DVC attachment slice: `91/2016/NĐ-CP` (2026-07-29)

The next bounded research slice resolved four identity groups and eight
procedure bindings from the current official National Public Service Portal
profiles:

| Requirement identity | Form | Procedures | Canonical source artifact SHA-256 | Evidence |
|---|---:|---|---|---|
| `098a1beb329231cb5f69f107` | `05` | `1.013868`, `1.013895` | `cf27fa6d9e1f4bdd3a5697b8a838a7be1d63ea18eaba1f9db48768c843d39052` | [procedure profile](https://dichvucong.gov.vn/thu-tuc-hanh-chinh/019d2bff-2d36-7533-81f7-2f3380a96f1b) |
| `52a2c04c6307ae820cbff5b1` | `06` | `1.013874` | `67db7417c571c9ac780d83c63967f1001a7f604c2896d5daafbd0fbd3d96dff8` | [procedure profile](https://dichvucong.gov.vn/thu-tuc-hanh-chinh/019d2bff-2d44-77d9-8f40-3ab356b0629c) |
| `6177c8ce5c0604a921478bb4` | `07` | `1.013870` | `f34501160e7e4c404a8c78b98c8b0eaa9520ac0eaf8b03b28db7ce5761b46354` | [Decision 1684/QĐ-BYT package](https://dichvucong.gov.vn/quyet-dinh-cong-bo/019eb146-cf41-72c8-9f55-1d3df27dd74a) |
| `b0ac03e0880bf1b6c23ba8e6` | `09` | `1.013875`, `1.013880`, `1.013881`, `1.013883` | `48c4255d0853024ff6307afcc5b8ccb7153790c043145d08911c42966dd8709f` | [procedure profile](https://dichvucong.gov.vn/thu-tuc-hanh-chinh/019d2bff-2d46-756a-9f2e-be8ecac0752a) |

The effectivity evidence is tied to Decision `1684/QĐ-BYT` (10 June 2026;
published on the official portal in the 1 July 2026 publication window), but
the parent decree is still only partially effective. This is therefore
form-level technical evidence, not a legal approval or an assertion that the
parent decree is wholly current. The four evidence rules use the strict
`official_current_dvc_attachment` basis in
`notebook_data/forms/official_form_effectivity_evidence_v1.json`; they bind
each procedure code, form code, Appendix I, instrument and exact attachment
UUID/SHA-256/size.

The resolver downloads these attachments only with the fixed official
`POST /api/v1/submitting/preview-attachment` request and JSON body
`{"fileId": "<UUID>"}`, with the official profile as `Origin`/`Referer`.
The endpoint, UUID, content type, magic bytes, size and checksum are all
validated. A checksum or binding drift is a hard failure and cannot fall back
to the older VBPL package. Generic portal filenames are not treated as
identity evidence. DOCX extraction stops at the next `Mã thủ tục hành chính:`
boundary; the Decision DOCX is used structurally for Form 07 so a preceding
table-row fragment is not included.

The final repeat probe run `20260729174312` completed all four groups with
eight candidates, zero unresolved identities, zero verified data gaps, zero
external blocks and zero automated approvals. Canonicalizing generated DOCX
ZIP timestamps and core created/modified metadata makes the four extracted
artifact checksums stable across repeated runs without changing document body,
relationships or media. The final artifacts were re-rendered and visually
inspected after this normalization. Every candidate has
`approved=false`, `runtime_eligible=false` and
`HUMAN_LEGAL_REVIEW_REQUIRED`; no attestation was created and the release flag
was not changed. The rebuilt privacy-safe queue remains 202/202 accounted,
with 50 technically resolved future candidates, 22
`VERIFY_EXACT_APPENDIX_EFFECTIVITY` actions and future batches of 25 and 25.
For DVC candidates it retains only an allowlisted provenance chain: attachment
UUID, official POST endpoint, source-package SHA-256/size, publication
decision, extracted-artifact size and structural form boundary. It excludes
local paths, filenames, request headers/bodies and document/OCR text. The
queue fingerprint is
`475249671cb136bdb71eb8e781d1f16815140aa86fbd590817065c16dbcfb0e5`;
its file SHA-256 is
`a927bac20783ea2b5097fcc699644cab3adbced7e7a075450d82d52fa3584e52`.
The 46 previously approved/live identities, the locked manifest and active
collection remain unchanged.

### Historical 2026-07-28 run

Run: `20260728005308-f7b1b1ce`

| Metric | Result |
|---|---:|
| Occurrences | 715 |
| Terminal occurrence states | 715 |
| Identity groups | 203 |
| Procedure bindings | 319 |
| Identity/source gaps | 657 verified-data-gap occurrences; 355 unresolved identity observations |
| External-blocked groups | 0 (all 144 resolver groups completed) |
| Ready for human attestation | 7 procedure bindings across 5 canonical form groups |
| Runtime-approved baseline | 3 |

The previous run had a false aggregate classification: the resolver searched
with only the first two slash-separated components of an instrument (for
example `02/2024`), so exact VBPL documents were missed. The resolver now uses
the complete instrument (`02/2024/TT-BYT`) and records granular DNS/TLS,
timeout, HTTP, CAPTCHA and endpoint-shape reasons. A probe now finds the
official document, while correctly keeping it out of the shortlist when exact
appendix effectivity or file extraction is not complete.

The resumable resolver completed all 144 identity groups. Heavy legacy-artifact
work is isolated in a bounded worker; groups that exceed the local processing
budget are recorded as `OFFICIAL_ARTIFACT_PROCESSING_TIMEOUT` and remain
fail-closed. Seven bindings passed the deterministic hard gate and are in the
candidate-only shortlist. They are not approved and are not runtime-served.
One shortlist item uses an attachment whose filename differs from the exact
2026 instrument label; this is explicitly retained as a legal-review attention
item rather than being auto-approved.

The executable code gates are green: backend `1091/1091`, frontend `91/91`,
type-check, lint and production build. Focused authorization, privacy, legacy
compatibility, review-sync and rollback tests are `38/38`.

The release gate remains blocked. Live retrieval-only quality is above target
(Recall@10 `97.06%`, direct-source top-5 `96.15%`, coverage `91.62%`,
wrong-field/expired `0`), but a fresh concurrency-5 run has retrieval P95
`8,359 ms` (target `3,000 ms`). The live generation role matrix returned
HTTP 200 for all nine requests, but quality passed `0/9` because available
facet coverage and generation latency remain below the release contract
(29.3–46.4 seconds end-to-end). No rollout flag was changed.

## Artifacts

Each run is written under:

```text
data/form_resolution_campaign/runs/<run_id>/
```

The run contains an aggregate baseline, checksum-protected occurrence registry,
source-attempt records, effectivity graph, gap records, review shortlist and
privacy-safe report. The Admin API exposes only aggregate status, opaque
occurrence IDs/reason codes and hard-gate shortlist metadata; internal paths,
OCR text, credentials and legal-answer content are omitted.

## Safety gates

- `LEGAL_SECTION_GROUNDING_ENABLED=false`.
- `active_collection=legal_chunks_lechan_primary_v20260723`.
- No corpus deletion, rewrite, full re-embedding or active-pointer switch.
- Candidate generation is never legal approval.
- `BLOCKED_EXTERNAL`, `VERIFIED_DATA_GAP`, expired, superseded, seed/demo,
  quarantined and incomplete-extraction records are fail-closed.
- Runtime release still requires authenticated legal attestation and the full
  backend/frontend, authorization, privacy, rollback and role gates.

## Local tools

LibreOffice headless is installed for legacy DOC conversion. Tesseract
Vietnamese/English OCR is available through the existing local OCR adapter.
The adapters report structured unavailability and cannot promote incomplete
content.

## Resume corrections for the 2026-07-28 campaign revision

The active campaign remains `20260728124624-09e91e70`; no competing campaign
or legal date was created.

- Retryable artifact/network failures are no longer treated as terminal
  processed groups. Resume removes the stale technical gap and retries it,
  while verified source/effectivity decisions remain durable.
- Legacy DOC extraction distinguishes an appendix header such as “Mẫu số 02”
  from an earlier legal citation such as “theo Mẫu số 02”. This correction
  extracted the official form from Nghị định 103/2016/NĐ-CP in about two
  seconds instead of entering an unnecessary OCR timeout.
- Scanned PDF OCR is attempted only after PDF text, DOCX and bounded
  LibreOffice conversion. Complete OCR is cached by the official package
  SHA-256 and reused by every form code in that package.
- The form-boundary OCR pass uses one Tesseract text pass per page at 150 DPI;
  it skips the separate confidence pass but still requires complete page
  coverage before the cache can be used. Partial OCR remains fail-closed.
- The first complete scan has a finite per-group deadline; subsequent groups
  reuse its durable cache rather than repeating a timeout from page one.

These changes affect candidate preparation only. They do not approve a form,
serve an unreviewed record, modify the legal corpus, re-embed the collection or
change the active collection pointer.

### Curated scanned-package page ranges (2026-07-30)

Some official provincial decisions are published only as scanned PDF packages,
so a text/OCR boundary detector cannot safely identify an individual form.  A
curated government-source record may now contain an exact page-range binding
only when it pins the issuing document, the form code, appendix identifier,
complete procedure-ID set, official landing page, allowlisted download URL,
package SHA-256, byte size and the contiguous zero-based source pages.

The resolver validates the full package after download and fails closed on any
checksum, size, format, page-range or binding mismatch.  It copies only the
pre-verified pages and records the source package and range in provenance; it
does not use OCR or infer a boundary.  The resulting record remains
`approved=false`, `runtime_eligible=false` and requires authenticated legal
attestation.  Finite document effectivity is carried into the candidate, and a
later legal date requires fresh current-status verification.

### Nghị định 142/2025/NĐ-CP: Công báo range slice (2026-07-30)

The next candidate-only slice adds six exact form identities and 26 procedure
bindings from the official Government Gazette packages for Nghị định
`142/2025/NĐ-CP`.  The source landing page is the [Government Gazette
record](https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-142-2025-nd-cp-45154/56904.htm);
the legal-status evidence is the Central VBPL record
`https://vbpl.vn/TW/Pages/ivbpq-thuoctinh.aspx?ItemID=178292&Keyword=`.  Both
the official status record and the Gazette metadata identify the same decree,
with effect from 2025-07-01 and current status checked on 2026-07-30.

| Requirement identity | Form / Appendix | Procedure bindings | Gazette package | Zero-based source pages |
|---|---|---:|---|---|
| `9bcfb06a60dab4309f2beb39` | `02` / `II` | 7 | 811 + 812 (`f6a7…b25c`) | `23–25` |
| `6b90254fdd5adb1cff4186b6` | `04` / `II` | 5 | 811 + 812 (`f6a7…b25c`) | `27–28` |
| `b3c3e88d255ff13008c29585` | `06` / `II` | 5 | 811 + 812 (`f6a7…b25c`) | `34–35` |
| `82e6f489c051e367ead8295d` | `07` / `II` | 3 | 811 + 812 (`f6a7…b25c`) | `36–37` |
| `17838da948507afc175b9780` | `08` / `II` | 1 | 811 + 812 (`f6a7…b25c`) | `38–39` |
| `77b1a3481bad7ca0114828c9` | `05` / `VII` | 5 | 813 + 814 (`aad3…ac0e`) | `9–16` |

Each page range was rendered and visually checked: it begins at the matching
form header and ends immediately before the next form.  The exact full
SHA-256 and byte length are stored in
`official_government_document_sources_v1.json`, so a changed Gazette package
is rejected rather than reinterpreted.

Article 44 includes a conditional future sunset and allows earlier
form/procedure-specific displacement by later legislation.  The registry
therefore does not forecast an `effective_to` date: it records only the
official current status for the campaign's legal date.  Every resulting record
is still candidate-only (`approved=false`, `runtime_eligible=false`) and must
receive authenticated legal attestation before it can be served.  A rerun for
a later legal date must obtain fresh effectivity evidence.
# Preview route repair (2026-07-31)

The admin review UI now reads the checksum-bound form-resolution campaign through
`/api/procedures/forms-catalog/form-resolution/current` and its shortlist route.
The compatibility preview route delegates to that same read-only shortlist.
These routes expose no local file paths or extracted document text and never
change candidate, catalog, or legal-review state.
