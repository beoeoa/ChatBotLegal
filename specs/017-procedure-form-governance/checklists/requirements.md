# Requirements Quality Checklist: Feature 017

**Purpose**: Xác nhận đặc tả đủ rõ để thiết kế và triển khai an toàn
**Created**: 2026-08-11
**Feature**: [spec.md](../spec.md)

## Scope and roles

- [x] CHK001 Phạm vi 191 thủ tục/5 lĩnh vực và baseline 131/229 được nêu rõ.
- [x] CHK002 Quyền Citizen/Officer/Admin và các negative path được quy định server-side.
- [x] CHK003 Source approval, attestation và release là các thao tác tách biệt.
- [x] CHK004 Live migration, auto approval, browser UAT và production activation được đặt ngoài lát tự động.

## Legal and deterministic behavior

- [x] CHK005 Nguồn chính thức, hiệu lực, jurisdiction, checksum và insufficient-evidence behavior được chỉ rõ.
- [x] CHK006 Form Router không giao `form_id` cho LLM/vector/reranker.
- [x] CHK007 Ambiguity, conditional form, e-form, no-form và source-gap có hành vi xác định.
- [x] CHK008 Expired/superseded data được giữ lịch sử nhưng bị chặn ở current answers.

## Data and release

- [x] CHK009 PostgreSQL là legal source of truth; SurrealDB chỉ là notification projection.
- [x] CHK010 State machine, version history, active pointer và rollback semantics được mô tả.
- [x] CHK011 Coverage decisions có số đo riêng cho procedure/identity/binding.
- [x] CHK012 Golden V3 ground truth chỉ lấy từ manifest và cần người dùng duyệt trước khi freeze.

## Acceptance quality

- [x] CHK013 Mỗi user story có independent test và acceptance scenarios.
- [x] CHK014 Performance, fallback, exact-form, ambiguity và release safety có ngưỡng định lượng.
- [x] CHK015 Không còn placeholder hoặc NEEDS CLARIFICATION.

