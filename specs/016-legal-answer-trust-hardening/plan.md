# Implementation Plan: Nâng cấp độ tin cậy câu trả lời pháp luật

**Branch**: `codex/006-public-production-readiness` | **Date**: 2026-08-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/016-legal-answer-trust-hardening/spec.md`

## Summary

Triển khai theo năm giai đoạn có cổng độc lập: (A) phép đo/golden/log có cấu trúc; (B) P0 hiệu lực, intent/facet, fallback, exact-article và determinism; (C) learned reranker cùng dual-embedding shadow; (D) provenance/citation, audit chain và provider privacy; (E) OCR/layout và multi-hop giới hạn. Mỗi giai đoạn viết test trước, chỉ chuyển pha khi test và cổng của pha đạt. Browser UAT được loại khỏi thực thi tự động hiện tại và chỉ chạy sau khi xin phép người dùng.

## Technical Context

**Language/Version**: Python 3.12 cho API/retrieval; TypeScript/React/Next.js 15 cho giao diện; Node.js 20 cho artifact/report scripts

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy, PostgreSQL, SurrealDB, ChromaDB, PyTorch/Transformers, VNLegal-LAL, rank-bm25, artifact-tool; optional BGE reranker/BGE-M3

**Storage**: PostgreSQL là nguồn pháp luật; ChromaDB là active vector index; SurrealDB lưu notebook/conversation; JSON/JSONL versioned cho golden, snapshot và benchmark artifacts

**Testing**: pytest, Vitest, TypeScript/build checks, isolated API tests, deterministic benchmark scripts; browser test bị hoãn tới cổng phê duyệt riêng

**Target Platform**: Local Windows development có CUDA 6 GB; Linux Docker production hỗ trợ CPU profile và CUDA profile riêng

**Project Type**: Web application gồm FastAPI, Next.js, retrieval service và background import worker hiện có

**Performance Goals**: Retrieval P95 ≤3 giây; API P95 ≤25 giây; error/timeout <0,5%; profile mục tiêu 30 Ask đồng thời/3 RPS trong 10 phút sau tối ưu

**Constraints**: Không live migration/backfill/re-index/activation; không paid service/daemon mới; không trộn embedding; không hạ safety gate; GPU 6 GB chỉ tải model tuần tự

**Scale/Scope**: Baseline 100 câu duy nhất; golden đích 1.000 câu x hai role; active retrieval khoảng 160 nghìn vector và PostgreSQL khoảng 545 nghìn chunk; mục tiêu 5.000 tài khoản/1.000 DAU

## Constitution Check

- **Legal grounding — PASS**: Chỉ evidence chính thức/được duyệt; claim-level binding; thiếu evidence chuyển từ chối/source-only.
- **Authority/effectivity/jurisdiction — PASS**: Strict current-answer gate, provision-level partial validity, historical-only label, snapshot readiness.
- **Role and data isolation — PASS**: Giữ middleware/allowed-domain hiện có; public projection không lộ admin trace; negative tests cho ID/role/domain tampering.
- **Deterministic retrieval/fallback — PASS**: Hard gate trước reranker, tie-break cố định, exact-article packet, ba answer modes, model/resource degraded mode có reason code.
- **Privacy/security/audit — PASS**: PII redaction trước cloud; audit không giữ raw PII/secret; checkpoint key nằm ngoài database.
- **Verification — PASS WITH DEFERRED LIVE GATES**: Unit/contract/integration/API-isolated tests trong scope; browser UAT, live migration/backfill/re-index/activation cần phê duyệt riêng.
- **Performance/operability — PASS**: CPU/CUDA profiles, stage timings, benchmark concurrency; safety không phụ thuộc cấu hình phần cứng.
- **Documentation — PASS**: Cập nhật `docs/7-DEVELOPMENT/legal-answer-trust-hardening.md` và runbook liên quan.

Post-design check: Các schema mới là additive sidecar; active corpus/index không bị sửa. BGE/reranker/OCR đều optional và fail-safe. Không có vi phạm cần biện minh.

## Project Structure

### Documentation (this feature)

```text
specs/016-legal-answer-trust-hardening/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── answer-trust-contract.md
│   ├── golden-case-v2.schema.json
│   └── structured-qa-run.schema.json
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api/
├── legal_problem_map.py
├── legal_section_grounding.py
├── legal_structured_answer.py
├── legal_answer_completeness.py
├── legal_validity_registry.py
├── legal_claim_validation.py
├── routers/search.py
└── models.py

scripts/
├── legal_search_server.py
├── build_feature016_regression.py
├── validate_legal_golden_v2.py
├── evaluate_legal_answer_trust.py
└── export_qa_log_excel.mjs

frontend/src/
├── lib/types/search.ts
└── components/search/

tests/
├── fixtures/feature016/
└── test_feature016_*.py
```

**Structure Decision**: Mở rộng pipeline và `AskResponse` hiện tại; không tạo retrieval/answer API song song. Dữ liệu provenance/audit là sidecar additive. Mọi artifact benchmark được version hóa và privacy-scanned.

## Implementation Phases and Gates

### Phase A — Measurement foundation

Tạo regression 100 câu từ workbook bằng builder deterministic; golden v2 và structured run schemas; evaluator rule-based; logger/exporter chỉ nhận structured payload. Gate A yêu cầu checksum đúng, 100 câu/20 mỗi lĩnh vực, schema validation và test chứng minh `has_sources` không quyết định pass.

### Phase B — P0 answer correctness

Hoàn thiện snapshot coverage/readiness và strict mode; LegalIntent/facet eligibility; claim-level fallback; exact-article missing detector; semantic claim dedupe; temperature/tie-break/version trace; source-gap queue qua admin. Gate B: 0 expired/wrong-procedure/wrong-facet conclusions, fail-closed 100%, ≥95% regression pass, unexpected fallback ≤3%, structured decision deterministic 100%.

### Phase C — Retrieval quality

Optional `BAAI/bge-reranker-v2-m3` trên tối đa 40 candidates sau hard gate, batch 8/FP16 khi khả dụng; fallback heuristic. BGE-M3 chỉ shadow và collection riêng. Kích hoạt chỉ khi không safety regression, Recall@10 không giảm, MRR hard case +5% hoặc Top-5 +1pp và latency/OOM đạt.

### Phase D — Proof and governance

Provenance sidecar cho nguồn mới; citation verification levels/viewer; hash-chain audit với Ed25519 checkpoint; provider label và PII egress policy. Chỉ viết migration/rehearsal cô lập, không apply/backfill corpus thật.

### Phase E — Complex sources and bounded hops

ExtractionBlock giữ trang/bbox/table path; optional OCR fallback. Multi-hop chỉ trong graph đã duyệt, tối đa hai vòng/mười sáu query, tái hard-gate và cycle detection. Chỉ bật sau Gate B/C.

### Final non-browser gate

Chạy toàn bộ test kỹ thuật, isolated API benchmark, privacy/security/migration rehearsal và quickstart. Không chạy browser UAT. Khi đạt, dừng và xin phép người dùng trước 2.000 lượt web.

## Rollout and Rollback

- Feature flags độc lập cho intent v2, strict validity, fallback v2, learned reranker, dual-embed shadow, physical citations, audit chain, adaptive hop và provider disclosure.
- Safe rollback: reranker→heuristic; dual embed→VNLegal-LAL; multi-hop→single-hop; physical citation→content quote; provider→local/source-only.
- Strict validity không rollback về unsafe behavior; dùng snapshot đã xác minh hoặc từ chối.
- CPU profile giữ toàn bộ safety; chỉ shadow/OCR nền giảm ưu tiên. CUDA release dùng image riêng với device reservation và model fingerprint.
- Mọi live schema apply, real backfill/re-index, active pointer switch và browser UAT cần phê duyệt riêng.

### Approved live correction slice — 2026-08-11

Người dùng đã phê duyệt riêng việc cập nhật dữ liệu sống và tái kiểm định 294
Golden case. Quyền này chỉ mở lát cắt Phase 9 trong `tasks.md`: nhập và embedding
`116/2026/TT-BCA`; đồng bộ hai thông tư cư trú đã hết hiệu lực nhưng không xóa
lịch sử; xác minh phạm vi một phần của `31/2024/QH15` và `73/2025/QH15`; sửa
đúng structural unit thiếu của `88/2025/QH15`; và tái kiểm định đúng 294 ca.

Trước mọi ghi phải có baseline và backup PostgreSQL/SurrealDB/Chroma. Mọi vector
mới hoặc thay thế phải có exact-ID manifest. Không suy đoán affected provision,
không hạ validity gate, không re-index toàn kho, không hard-delete và không mở
rộng sang schema/worker/dịch vụ trả phí. Nếu nguồn chính thức không đủ phạm vi,
ca liên quan tiếp tục fail-closed và giữ `needs_revalidation`.

## Complexity Tracking

Không có vi phạm constitution. Sidecar schema, optional models và feature flags là mức phức tạp tối thiểu để giữ compatibility, rollback và bằng chứng pháp lý.
