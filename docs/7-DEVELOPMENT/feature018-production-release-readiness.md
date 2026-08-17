# Feature 018 — Production release readiness

## Purpose

Feature 018 is the umbrella release program for the Hai Phong legal assistant. It does not replace the legal safety contracts in Features 009–017. It makes every runtime capability, data source, role boundary, test and rollback method inspectable before the system can be labelled production-ready.

The authoritative design is under `specs/018-production-release-readiness/`. Procedure/form behavior still follows every artefact under `specs/017-procedure-form-governance/`.

## First implementation slice: System Capability Registry

`config/system-capabilities.json` is a source-controlled, read-only manifest for:

- every Next.js page currently present under `frontend/src/app`;
- every FastAPI router mounted in `api/main.py`;
- long-running crawler, import, retention and effectivity jobs;
- removed/deprecated routes that must remain unavailable or redirect explicitly.

Each record declares:

- owner and allowed roles;
- canonical data source and sensitivity;
- audit and retention policy;
- SLA, feature flag and test evidence;
- rollback method and lifecycle status.

The manifest is not an authorization source and does not enable routes. Runtime authorization remains server-side. The registry is a release gate and inventory used to detect drift.

### Validate locally

```powershell
python scripts/validate_feature018_contracts.py
pytest -q tests/test_feature018_contracts.py tests/test_capability_registry.py
python scripts/audit_capability_registry.py --report reports/feature018/capability-audit.json
```

The audit fails when a source page or mounted API router is unregistered, a registered active route no longer exists, IDs/paths are duplicated, or required ownership/release metadata is invalid.

The Admin read-only endpoint is:

```text
GET /api/admin/capabilities
GET /api/admin/capabilities/{capability_id}
```

Both endpoints enforce the Admin role in the router; the registry exposes no credentials, local file contents or user data.

## Route-boundary fix

Role routing now accepts a page only when the pathname equals an allowed path or is a real child separated by `/`. A string such as `/admin-evil`, `/admin-control` or `/admin-dashboard` is no longer accepted merely because it starts with `/admin`.

This is both a release-hygiene and authorization defense. The legacy `/admin-dashboard` page still performs its explicit server redirect to `/admin`, but the generic role guard does not treat lookalike route names as authorized children.

## Fail-closed Go/No-Go evidence

`api/release_evidence.py` defines immutable evidence records bound to a release ID and release fingerprint. `scripts/verify_production_readiness.py` requires matching passed evidence for:

- capability registry;
- backend and frontend gates;
- legal regression, 294 Golden, Golden V3 and 1,000 live answers;
- browser UAT;
- load, security, backup, restore, rollback and smoke.

Missing, failed, malformed, stale or wrong-release evidence produces `NO-GO`. This verifier is intentionally not a replacement for executing the tests; it only ensures the candidate cannot be labelled ready without the required artefacts.

## Safety boundaries for later slices

- PostgreSQL remains canonical for legal/business state.
- SurrealDB remains notification projection for new support/FAQ architecture.
- Chroma active snapshots are immutable; no real vector move/delete/re-index occurs without the approved gated slice.
- Live support/FAQ migration starts with additive schema and isolated rehearsal.
- No production pointer, public release, browser UAT or live migration is implied by completing registry work.
- Validity, authority, role, procedure, form, citation, checksum and audit gates cannot be disabled to satisfy release metrics.

## Unified legal answer presentation slice

`api/legal_answer_presentation.py` defines the versioned `legal-answer-v1`
projection. It is a pure post-validation adapter: it performs no retrieval,
provider call, form lookup or citation extraction from generated prose. The
stable section order is short answer, actions, dossier, procedure facts,
official forms, legal bases, caveats and clarification questions.

`api/unified_chat_service.py` is the single finalization boundary for the Ask
core and conversation chat. It executes the existing generation/claim
validation core once, derives the public response mode, allow-lists release
metadata and attaches the V1 projection. Supported claims survive an unrelated
coverage gap; only rejected claim text is removed by the existing validator.

`api/legal_structured_answer.py` makes the provider trust boundary explicit:
public form and citation identities are copied only from backend-validated
collections. Provider JSON/prose cannot add or replace form IDs, URLs, law
numbers or citation URLs. A historical answer requires `legal_as_of` and a
plain historical label; otherwise the finalizer does not present the response
as historical. A verified label is absent whenever `evidence_count` is zero.

The frontend prefers `LegalAnswerCard` whenever
`presentation_version=legal-answer-v1`. Both previously structured and legacy
Ask outputs therefore use one renderer after backend projection. Stored older
messages without V1 fields retain the compatibility renderer and are not
reinterpreted as newly verified answers.

### Verify the answer slice

```powershell
pytest -q tests/test_legal_answer_presentation.py tests/test_answer_routes_feature018.py tests/test_answer_pipeline_v3.py tests/test_step7_role_delivery.py
Set-Location frontend
npm test -- --run src/components/search/LegalAnswerCard.test.tsx src/components/search/StreamingResponse.test.tsx
npx tsc --noEmit
```

## Long-chat usability slice

- Conversation detail loads the latest 30 messages. Older messages use an
  opaque, conversation-scoped cursor through
  `GET /api/conversations/{conversation_id}/messages`.
- Prepending an older page preserves the reader's viewport. A newly arriving
  message scrolls automatically only while the reader is already near the
  bottom; otherwise the UI offers **Xuống tin mới nhất**.
- Starting a new conversation clears the prior answer, selected legal domain,
  recommended-form state, upload/voice state and pending Ask state. Citizen and
  officer surfaces use the explicit labels **Trợ lý pháp luật** and
  **Tra cứu nghiệp vụ**.
- The answer-presentation snapshot is stored in existing conversation metadata,
  so reloading history keeps the V1 renderer without a schema migration.
- Responsive and keyboard Playwright cases are registered for 360, 768 and
  1440 px. They remain gated by `FEATURE018_CHAT_UX_UAT=true`; this implementation
  slice did not run browser UAT or mutate a live environment.

## Isolated support/FAQ schema rehearsal

`scripts/feature018_migrations/001_support_faq_up.sql` is additive and defines
the canonical ticket/message/attachment, officer presence/assignment and FAQ
revision/release tables. It does not duplicate Feature 017 form bindings: an
FAQ revision stores only its confirmed procedure identity, while a FAQ release
records the Feature 017 form release it was validated against.

`scripts/rehearse_feature018_postgres.py` is fail-closed. `plan` is safe without
a database and redacts credentials. `up`, `import-shadow`, `verify` and `down`
require `FEATURE018_DATABASE_URL`, an isolated/test/rehearsal database name and
`--confirm-isolated`; `down` additionally requires `--allow-drop-fixtures`.
Imported JSON is read-only, support records without a real owner are refused,
and an approved legacy FAQ becomes `needs_review` rather than public/released.
The runner never updates an active support or FAQ runtime pointer.

This slice ran only `plan` and refusal/unit tests. No PostgreSQL connection,
schema application, JSON import, pointer switch or live data mutation occurred.

## Canonical support repository and allocator

`api/support_repository.py` centralizes the support state machine and access
rules. Citizens can read only owned tickets, officers can read content only for
an assigned ticket in one of their server-scoped domains, and Admin content
access requires a reason. Queue projections intentionally omit citizen identity
and question content. The JSON adapter applies the same read ACL and refuses
all writes.

The PostgreSQL repository uses optimistic ticket versions, append-only hashed
state events and immutable support messages. `api/support_allocator.py` claims
the next domain-scoped ticket with `FOR UPDATE SKIP LOCKED`, enforces the
three-session capacity, stores only a lease-token hash and safely requeues an
assignment when either assignment or presence lease expires. The in-memory
implementation exercises the same contract under concurrent test threads;
no local in-memory state is intended as a production source of truth.

## Scalable support API and isolated load evidence

Canonical mode derives citizen/officer identity and officer domains on the
server. Citizens poll their queue position and do not open a WebSocket while
queued. Realtime starts only after assignment; each officer has one domain
queue stream and at most three leased or active sessions. Queue events contain
only ticket ID, domain, priority, status and creation time. Admin lists are
metadata-only, while content access requires a recorded reason and audit.

The frontend adds a citizen **Yêu cầu của tôi** panel with explicit cancel
confirmation, an officer capacity/claim flow, and an admin SLA view with a
reason-gated content action. Legacy JSON remains the default runtime pointer;
canonical PostgreSQL mode requires the explicit
`FEATURE018_SUPPORT_MODE=postgres_active` setting and a database URL.

`scripts/load_test_support.py` ran against the isolated in-memory contract with
1,000 tickets and 30 officers. It activated exactly 90 sessions, left 910 in
polling wait state, and reported zero duplicate assignments, wrong-domain
assignments, capacity violations or waiting-user realtime sockets. Evidence is
written to `reports/feature018/support-load.json`. No live database, queue,
WebSocket deployment or runtime pointer was touched.

## Lifecycle and impact read-model slice

`api/legal_lifecycle_service.py` now has a date-bound, read-only projection for
active/future/90/30/7/1-day/expired/partial/replaced/repealed/suspended states.
Only confirmed events with a source and applicable date affect current serving;
candidate or incomplete events remain visible for review but are ignored by the
serving projection. Provision effectivity is evaluated independently, so a
partially expired document can retain only its currently effective provisions.

`api/legal_impact_service.py` maps a confirmed event to immutable review cases
for procedures, forms, FAQs, Golden cases, caches, citations and index records.
A replacement with a different content hash does not copy content or imply
equivalence. It records hashes and a review candidate only. The new
`/api/legal-management` router exposes Admin-only summary, filtered metadata,
timeline, candidate confirmation, impact review and index preview contracts.
Its runtime store is disabled by default, and index preview explicitly reports
that no mutation or active-pointer change occurred.

The additive `002_lifecycle_impact_index_*` rehearsal SQL defines event,
relationship-candidate, provision, impact and index manifest/job tables without
altering `legal_documents` or the live corpus. Only the plan command and unit
tests ran; no schema was applied.

## Read-only vector serving manifest

`api/vector_serving_manifest.py` deterministically classifies canonical chunks
as active, historical, staging, missing, orphan, duplicate or fingerprint
mismatch. The active gate binds embedding, splitter, pipeline and validity
snapshot fingerprints, and fails if non-current vectors remain in the active
collection or the active pointer changes.

`scripts/build_vector_serving_manifest.py --read-only` reads PostgreSQL in a
`SET TRANSACTION READ ONLY` transaction. Chroma is inspected through SQLite
`mode=ro&immutable=1` with `PRAGMA query_only=ON`; `PersistentClient` is not
used because even read-looking client calls may perform housekeeping writes.
The command fingerprints the complete Chroma file inventory before and after
the scan and writes only the requested report.

The current evidence at `reports/feature018/vector-manifest.json` observed
545,625 canonical chunks and 703,510 vectors across seven legal collections.
It reports 124,046 historical, 64,801 staging, 260,495 duplicate/non-canonical,
96,283 fingerprint-mismatch and six orphan chunk identities. The gate is
therefore correctly `needs_review`; it was not bypassed. All 70 Chroma files
(9,162,998,576 bytes) had the same pre/post store signature, the active pointer
remained `legal_chunks_vnlegal_lal_haiphong`, and zero write operations were
invoked. No re-index, vector cleanup or pointer switch occurred.

## Lifecycle Admin UX and legal-change journey

The legal-management list now has explicit lifecycle/vector filters and a
source-backed two-step change workflow. The first action creates a candidate
only. Confirmation requires the candidate's evidence fingerprint and a new
reason, after which dependent records remain `needs_review`. The document view
loads timeline, impact and index readiness only for Admin; other roles neither
render nor request this operational projection. Indexing remains a preview that
reports `mutation_performed=false` and `active_pointer_change=false`.

`tests/test_legal_change_journey.py` covers expiry/replacement through impact,
manifest and chat serving. A different replacement creates review work for all
dependency classes, the new missing vector fails the manifest gate, and the old
document stays excluded from current answers even if its vector is still
physically present. Historical `legal_as_of` access remains available with the
historical-validity warning.

## Procedure, form and FAQ convergence

The Feature 017 workflow remains the only form truth. The workflow API now
provides a read-only, role/domain-scoped procedure picker over the active form
release, with the 191-procedure compatibility manifest used only when that
release is not configured. Source-proposal metadata names all six gates from
procedure selection through publication. PDF/DOCX selection computes SHA-256
in the browser but does not create an unscanned server upload path; the secure
upload gate remains a separate production-readiness task. Source verification,
legal attestation, Release Gate and publication are visibly distinct actions.

FAQ writes now use immutable revisions and candidate/validated/active releases
through `api/faq_governance_service.py`. The PostgreSQL adapter uses the
additive `faq_identity`, `faq_revision`, `faq_release`, `faq_release_item` and
`faq_active_release` tables. FAQ write requests accept one confirmed
`procedure_id` and reject manual `form_ids`. Public forms are recomputed from
the FAQ release-bound Feature 017 manifest and pass its procedure, role,
effectivity, source and checksum gates. A Feature 017 pointer drift blocks FAQ
activation. When FAQ shadow or active mode is enabled, legacy JSON writes and
hard delete are disabled; JSON remains read-only compatibility data.

The no-tech FAQ UI now shows `Chờ xác nhận`, `Đã xác nhận · chưa công khai`,
release validation and `Đã phát hành` instead of treating “Nháp/Đã duyệt” as a
publication switch. It searches procedures by name/code/domain, never asks for
a form ID, and separates confirmation from release activation.

Verification evidence:

- 19 focused Feature 017/018 form and FAQ API tests passed.
- all 104 `test_feature017_*` regressions passed.
- the approved Feature 017 Golden V3 run passed 1,000/1,000 direct router cases
  and 1,000/1,000 public API cases, with exact form set, clarification,
  forbidden-form and provider-identity gates all at 100%; direct/API P95 were
  54.1/57.8 ms. The report is
  `reports/feature018/feature017-golden-v3-1000-regression.json`.
- the affected frontend tests, TypeScript and ESLint passed.

No FAQ or form migration was applied, no source/corpus history was rewritten,
and no form/FAQ public pointer was switched by this slice.

## No-tech operations and Activity Center

The Admin landing page now projects operational signals into business-facing
alerts with an impact statement, module, state and exactly one filtered next
action. Internal identifiers and diagnostic payloads stay collapsed. Missing
metrics render as unavailable instead of a misleading zero.

`/api/admin/activity` provides Admin-only, cursor-paginated metadata filters by
role, module, result, actor and time. CSV, XLSX and PDF exports use the same
content-free projection. Sensitive metadata access requires a reason of at
least eight characters, recursively redacts secrets and emits its own audit
event. The matching `/admin/activity` UI exposes those filters and exports, and
the role navigation makes the page discoverable only to Admin. Legacy quality
and dashboard routes redirect to the consolidated `/admin` surface.

Cloud/local model setup now distinguishes same-machine and Docker Ollama URLs
and blocks activation until credentials, a successful connection test, a chat
model and an embedding model are all present. Phase 8 verification passed 12
focused backend tests and 29 frontend tests; affected TypeScript and ESLint
checks also passed.

## Security and deployment foundation

Release security now has negative tests for server-side Admin ACL, spoofed role
headers, session revocation and L1-cache eviction, production MFA and auth-rate
limits. Uploads share `api/upload_security.py`: it rejects unsafe client/storage
paths, disallowed size/type, magic/extension mismatch, executable payloads,
EICAR, unsafe archive paths, active Office content and checksum mismatch before
exclusive storage. Source, support, officer proposal, form and Ask media paths
use the gate. Support attachments retain a verified SHA-256.

The audit chain can sign Ed25519 checkpoints outside the database and verify a
persisted historical prefix only after validating the entire current chain.
Checkpoint algorithm, chain identity, public-key fingerprint, head and
signature mismatches fail closed. Retention now follows the Feature 018
contract: support content and attachments expire 180 days after close, open
tickets do not expire, and critical legal audit history is excluded from the
730-day operational-audit purge.

Production compose now runs independent non-root `frontend` and `api` images,
each with its own health/readiness probe and no published port. Caddy is the
only TLS entry point. It applies security headers, header/body/time bounds and
uses stock `forward_auth` to a private shared-token API limiter, avoiding an
unreviewed community Caddy plugin. The limiter stores only hashed client/rate
class keys. Monitoring assets provide content-free private blackbox probes,
alerts and a Grafana dashboard, but the compose file does not start additional
monitoring daemons without operator approval.

Backup/restore and incident/rollback runbooks cover PostgreSQL, object files,
Chroma/vector fingerprints and Surreal. After release-owner approval, a new
quiesced four-layer backup was captured and reconciled against release
fingerprint `8da24463ed4c4317f7ae5ed926d7f368c7193826a737d798528dae6039a6a607`.
The real isolated restore contains 3,146 files with matching source/restored
inventory SHA-256. PostgreSQL restored into the guarded database
`feature018_restore_20260813` with 29 tables and 1,694,129 rows and no count
mismatch. The isolated Chroma store opened both expected collections with
161,077 and 432,112 vectors. The cold Surreal snapshot opened on a separate
port with 40 tables and 31,714 records, matching schema, table counts and raw
copy checksum. A separate comparison against the resumed live source disclosed
one later `legal_validity_sync_run` row; snapshot-to-snapshot restore still
matched exactly. The report is `reports/feature018/restore-rehearsal.json`, and
it explicitly does not authorize a production pointer switch.

Windows long paths are handled explicitly: the restore runner uses `robocopy`
for the copy and a `\\?\` scan root for checksum inventory. Chroma may update
local metadata when an isolated store is opened, so the open/count report is
stored separately and the final byte-for-byte inventory is captured again from
a pristine restored copy. This distinction prevents a successful functional
open from being reported as an unchanged snapshot.

Docker release images and stock Caddy validation now pass. Trivy reports zero
remediable HIGH/CRITICAL findings and zero image/source secrets. The remaining
253 unfixed OS findings were accepted by the release owner in an Ed25519-signed
artifact bound to the current release fingerprint, Trivy artifact SHA-256 and
finding count. The security gate passes only while that signature and scope
continue to verify.

Go/No-Go reports support an embedded Ed25519 release-owner attestation. The
signature binds the canonical report digest, decision, release ID, release
fingerprint, signer ID, signing time and public-key fingerprint. Verification
fails on payload edits, the wrong public key, invalid base64 or a missing
signature. `scripts/verify_production_readiness.py` writes atomically and accepts
`--signing-key` together with `--signer-id`; without both, it records
`signature_status=unsigned` and T088 remains open. The current NO-GO report is
signed and verifies with `reports/feature018/release-owner-public.pem`; private
key material remains outside the repository and is never written to a report.

The 1,000-case DeepSeek run at concurrency 100 completed but failed the release
gate: 734/1,000 evaluator passes, 927 HTTP 200 responses and client P95
184.9618 seconds. Browser UAT was approved and started; the first journey found
a conflicting compose runtime whose Surreal container could not lock the active
RocksDB store. After the host runtime was recovered and all four health checks
returned 200, the in-app Browser URL policy blocked continuation of the local
page. This is recorded as a failed, incomplete UAT gate rather than inferred as
a pass. Staging/canary, rollback smoke and observation remain prohibited while
the signed decision is NO-GO.

## Replica topology and release-confirmed procedure evidence

The release compose candidate now declares two independent frontend replicas,
two API replicas and two read-only retrieval replicas. Caddy load-balances the
frontend/API pairs with active health checks; each API uses a round-robin,
read-only client over both retrieval endpoints and fails over only on transport
or 5xx errors. `/ready` probes every configured retrieval replica concurrently
and fails closed when a replica is unavailable or when model, collection or
index counts differ. The topology is validated by the CPU and CUDA compose
config checks, Caddy validation and the focused client/readiness/monitoring
tests; no production stack was started from this candidate.

The Ask procedure path now binds official-procedure evidence only after the
active Feature 017 release returns a confirmed procedure identity, release ID
and checksum-valid form packet. The frozen National Public Service Portal
snapshot is matched by the exact released procedure code; it is never selected
from a fuzzy question match. Reviewed current-law overlays (including the
effective Decree 217/2026/ND-CP building-permit passages) take precedence over
the snapshot for the same facet. A `rule` facet is rendered only from the
snapshot's published `legalBasisesDetails`; missing condition/fee metadata
remains a visible source gap. Long procedure names are collapsed to one bounded
issue after identity confirmation, preventing comma-based planner artifacts
and HTTP 500s.

Offline canary evidence over all 373 `released` Golden V3 cases currently
confirms the release identity in 373/373 cases and projects enough official
evidence/forms for 360/373 cases. The remaining 13 cases are fail-closed gaps:
8 condition, 2 fee and 3 legal-basis projections are absent from the captured
official snapshot. This canary is a quality diagnostic, not a replacement for
the required live 1,000-case DeepSeek gate; the signed Go/No-Go decision stays
NO-GO until the live, browser, load, rollback and smoke gates pass.

A bounded live DeepSeek canary (20 cases, concurrency 5) was also run against
the read-only host API. It returned 20/20 HTTP 200 responses and exact form
sets, with P95 latency 20.57855 seconds and 16/20 evaluator passes. The four
failures were `ANSWER_MODE_MISMATCH` cases where the backend correctly exposed
an incomplete/condensed evidence state; they do not justify relabelling the
answers as fully grounded. This remains diagnostic evidence only and does not
replace the failed 1,000-case gate.

The browser follow-up on 2026-08-14 rendered four admin surfaces
(`/legal-import`, `/admin/activity`, `/admin/support`, `/faq-management`) in
the in-app browser. It did not complete the required 20 citizen plus 20
officer/admin journeys: the current session is admin-scoped and no approved
synthetic citizen/officer browser credentials were available. Evidence is in
`reports/feature018/browser-uat-followup-20260814.json`; the authoritative UAT
gate therefore remains incomplete.

## Rollback

The first slice is additive. To rollback runtime exposure, remove the `admin_capabilities` router registration while retaining the registry and audit report for review. Revert the role-boundary helper only by a reviewed change with equivalent exact-boundary protection. No data migration or corpus/vector mutation is involved.
