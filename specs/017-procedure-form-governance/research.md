# Research: Feature 017 Procedure/Form Governance

## Decision 1 — PostgreSQL canonical, Surreal notification projection

**Decision**: Trạng thái pháp lý và active release chỉ tồn tại canonical trong PostgreSQL. SurrealDB nhận notification projection có thể retry nhưng không quyết định trạng thái.

**Rationale**: Dual-write hai nguồn chuẩn tạo race và trạng thái công khai lệch review. PostgreSQL transaction phù hợp state transition, immutable release manifest và atomic pointer.

**Alternatives rejected**: Tiếp tục JSON write-side hoặc dual-write PostgreSQL/Surreal đều không có transaction boundary thống nhất.

## Decision 2 — Additive schema separate from legal corpus

**Decision**: Tạo bảng `legal_procedure`, `legal_form_asset`, `procedure_form_binding`, `procedure_question_alias`, `form_review_case`, `form_review_revision`, `form_release`, `form_workflow_event`, `form_notification_outbox`, `form_active_release`.

**Rationale**: Tránh thay đổi corpus/vector và cho phép rehearsal/rollback riêng. Revision/outbox là chi tiết triển khai cần thiết để đáp ứng lịch sử file và projection.

## Decision 3 — Explicit state machine in domain service and DB constraint

**Decision**: Transition được kiểm tra ở service, trạng thái có CHECK constraint; event append-only và mang `from_status/to_status`.

**Rationale**: Client/API khác nhau không thể bỏ qua workflow. DB constraint chặn trạng thái vô danh, service chặn cạnh chuyển sai.

## Decision 4 — Attestation binds canonical preview

**Decision**: Fingerprint SHA-256 của canonical JSON gồm case revision, procedure, asset, binding, alias, source/legal metadata, checksum và reviewer preview.

**Rationale**: Tách nguồn review khỏi attestation nhưng phát hiện mọi sửa đổi giữa preview và confirm.

## Decision 5 — Immutable manifest and atomic pointer

**Decision**: Release manifest bất biến; activation updates singleton pointer in one transaction only after gate report passed. Rollback is another audited pointer change.

**Rationale**: Catalog half-release is unacceptable; history must remain available.

## Decision 6 — Deterministic form selection

**Decision**: Exact procedure identity/approved aliases first. Lexical/vector only nominate procedures. Final forms come from active release SQL rows and gates.

**Rationale**: Similarity and LLM reasoning are not reliable enough for form IDs reused across instruments.

**Alternatives rejected**: Learned reranker for form IDs, generative procedure mapping, or global “Mẫu 01” mapping.

## Decision 7 — Conditional forms remain conditional

**Decision**: Router returns conditional form with condition text unless user input deterministically resolves a declared condition; it never silently turns conditional into required.

**Rationale**: User facts may be incomplete and conditions are legal metadata.

## Decision 8 — Verified gap is a positive coverage decision, not a form

**Decision**: A procedure/identity can close coverage as `verified_gap` only with source checks/evidence and reason. It never produces a runtime asset.

**Rationale**: Distinguishes “officially no form” from “not researched yet” and prevents fabricated downloads.

## Decision 9 — JSON compatibility remains read-only

**Decision**: Current catalog remains active by default. PostgreSQL has shadow/active modes behind flags; import of JSON creates staging rows/proposals only.

**Rationale**: User explicitly prohibited live migration/activation in first slice and current worktree has mature fail-closed JSON gates.

## Decision 10 — Golden V3 is release-derived

**Decision**: Ground truth is constructed from manifest data. DeepSeek V4 Flash may create natural-language variants but cannot populate expected IDs, state, URL or checksum. Freeze requires 100% coverage and human workbook approval.

**Rationale**: A model-generated key would only test agreement with itself.

## Decision 11 — No new worker/service

**Decision**: Notification outbox can be projected by existing request/maintenance paths; no daemon is added in this feature slice.

**Rationale**: AGENTS requires approval for new background daemon; legal state remains correct even when notifications lag.

## Decision 12 — Scope and baseline are versioned

**Decision**: Store `legal_as_of`, source snapshot fingerprint and scope counts in coverage/release artifacts. Baseline 191/131/229 is expected, not hard-coded truth after refresh.

**Rationale**: Official catalogs change; acceptance must explain any count change rather than hide it.

