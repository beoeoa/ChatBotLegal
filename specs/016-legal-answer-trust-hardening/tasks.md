# Tasks: Nâng cấp độ tin cậy câu trả lời pháp luật

**Input**: Design documents from `/specs/016-legal-answer-trust-hardening/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: TDD bắt buộc. Kiểm thử trực tiếp trên web bị hoãn; sau final non-browser gate phải dừng và xin phép người dùng.

## Phase 1: Setup and contracts

- [x] T001 Tạo spec, checklist, plan, research, data model, contracts và quickstart trong `specs/016-legal-answer-trust-hardening/`
- [x] T002 Cập nhật Spec Kit context trong `AGENTS.md` và `.specify/feature.json`
- [x] T003 Kiểm tra ignore/config portability và chỉ bổ sung pattern thiết yếu nếu thiếu trong `.gitignore`, `.dockerignore`, `frontend/eslint.config.mjs`
- [x] T004 Ghi quyết định kiến trúc feature 016 trong `docs/7-DEVELOPMENT/legal-answer-trust-hardening.md`

---

## Phase 2: User Story 1 — Measurement foundation (Priority: P1)

**Goal**: Baseline 100 câu, golden v2, structured log/evaluator có thể tái lập.

**Independent Test**: Builder tạo đúng 100 câu/20 mỗi lĩnh vực và checksum; validator chặn schema/duplicate/review sai; sourced-but-wrong fixture thất bại; exporter không dùng DOM/`has_sources` làm pass.

- [x] T005 [P] [US1] Viết test builder workbook/regression trong `tests/test_feature016_regression.py`
- [x] T006 [US1] Cài builder deterministic trong `scripts/build_feature016_regression.py`
- [x] T007 [US1] Sinh baseline đã version hóa tại `notebook_data/feature016-regression-100.json`
- [x] T008 [P] [US1] Viết test golden schema/semantic rules trong `tests/test_feature016_golden_v2.py`
- [x] T009 [US1] Cài validator golden v2 trong `scripts/validate_legal_golden_v2.py`
- [x] T010 [US1] Tạo bộ golden v2 proposed từ baseline tại `notebook_data/feature016-golden-v2.json`
- [x] T011 [P] [US1] Viết test evaluator theo rubric trong `tests/test_feature016_qa_evaluator.py`
- [x] T012 [US1] Cài evaluator structured run trong `api/legal_answer_trust_evaluator.py`
- [x] T013 [P] [US1] Viết test structured log projection trong `tests/test_feature016_structured_log.py`
- [x] T014 [US1] Mở rộng contract model/log projection trong `api/models.py` và `api/routers/search.py`
- [x] T015 [P] [US1] Viết test exporter không coi `has_sources` là pass trong `tests/test_feature016_excel_export.py`
- [x] T016 [US1] Nâng exporter structured QA tại `scripts/export_qa_log_excel.mjs`
- [x] T017 [US1] Chạy Gate A và lưu evidence không chứa PII trong `reports/feature016/phase-a/`

---

## Phase 3: User Story 2 — P0 answer correctness (Priority: P1)

**Goal**: Chỉ trả lời đúng thủ tục/phần văn bản còn hiệu lực, đủ claim/facet hoặc fail closed.

**Independent Test**: Các fixtures expired/partial, confusion pairs, fallback modes, exact Article packets và repeated runs đạt cổng B mà không gọi browser.

- [x] T018 [P] [US2] Viết regression `88/2001/NĐ-CP` và Điều 13/14 `70/2015/NĐ-CP` trong `tests/test_feature016_validity.py`
- [x] T019 [US2] Bổ sung coverage/freshness/readiness và strict current-answer gate trong `api/legal_validity_registry.py` và `api/legal_search_client.py`
- [x] T020 [P] [US2] Viết confusion-pair/ambiguity tests trong `tests/test_feature016_intent.py`
- [x] T021 [US2] Mở rộng LegalIntent router và facet eligibility trong `api/legal_problem_map.py` và `api/legal_section_grounding.py`
- [x] T022 [P] [US2] Viết claim-level fallback tests trong `tests/test_feature016_fallback.py`
- [x] T023 [US2] Thay first-source extractive fallback bằng verified claim binding trong `api/legal_structured_answer.py` và `api/legal_provider_fallback.py`
- [x] T024 [P] [US2] Mở rộng exact-article missing/duplicate/order tests trong `tests/test_legal_exact_article_packet.py`
- [x] T025 [US2] Hoàn thiện exact Article packet/completeness trong `api/legal_exact_article.py` và `api/legal_answer_completeness.py`
- [x] T026 [P] [US2] Viết determinism/dedup/version-trace tests trong `tests/test_feature016_determinism.py`
- [x] T027 [US2] Chuẩn hóa temperature, stable tie-break, semantic claim dedupe và version trace trong `api/routers/search.py`, `api/legal_structured_answer.py`, `scripts/legal_search_server.py`
- [x] T028 [P] [US2] Viết source-gap admin-only tests trong `tests/test_feature016_source_gap.py`
- [x] T029 [US2] Nối missing evidence vào hàng đợi admin hiện có trong `api/source_gap_jobs.py`
- [x] T030 [US2] Chạy Gate B trên 100 regression và lưu evidence trong `reports/feature016/phase-b/`

---

## Phase 4: User Story 3 — Retrieval and reranking (Priority: P2)

**Goal**: Rerank hard-negative chính xác hơn trong giới hạn local-first, dual embedding chỉ shadow.

**Independent Test**: Learned reranker đạt gate hoặc giữ disabled với reason code; không OOM/safety regression; active collection không đổi.

- [x] T031 [P] [US3] Viết learned-reranker/degraded-mode tests trong `tests/test_feature016_reranker.py`
- [x] T032 [US3] Cài optional cross-encoder adapter và stable scoring trong `scripts/legal_search_server.py`
- [x] T033 [US3] Tạo hard-negative dataset từ golden v2 trong `scripts/build_feature016_hard_negatives.py`
- [x] T034 [P] [US3] Viết dual-embedding isolation tests trong `tests/test_feature016_embedding_shadow.py`
- [x] T035 [US3] Cài BGE-M3 shadow runner/manifest không activation trong `scripts/benchmark_feature016_embedding_shadow.py`
- [x] T036 [US3] Chạy Gate C và ghi quality/latency/resource evidence trong `reports/feature016/phase-c/`

---

## Phase 5: User Story 4 — Proof, audit and provider governance (Priority: P2)

**Goal**: Citation kiểm chứng vật lý, audit chống sửa và cloud egress an toàn.

**Independent Test**: Forged/mismatched citation fail; audit tamper detected; PII redacted; additive migration rehearsal forward/rollback pass.

- [x] T037 [P] [US4] Viết provenance/citation tests trong `tests/test_feature016_provenance.py`
- [x] T038 [US4] Cài provenance model/service và public citation levels trong `api/legal_citation_provenance.py` và `api/models.py`
- [x] T039 [US4] Viết additive provenance migration/rehearsal trong `scripts/legal_lifecycle_migrations/` và không apply live
- [x] T040 [P] [US4] Viết audit tamper tests trong `tests/test_feature016_audit_chain.py`
- [x] T041 [US4] Cài canonical hash chain/Ed25519 checkpoint trong `api/legal_audit_chain.py`
- [x] T042 [P] [US4] Viết provider privacy/provenance tests trong `tests/test_feature016_provider_privacy.py`
- [x] T043 [US4] Cài PII redaction, egress guard và public provider labels trong `api/legal_provider_privacy.py` và `api/routers/search.py`
- [x] T044 [US4] Chạy Gate D và isolated migration rehearsal trong `reports/feature016/phase-d/`

---

## Phase 6: User Story 5 — OCR/layout and bounded multi-hop (Priority: P3)

**Goal**: Giữ cấu trúc bảng/trang và chỉ hop trong graph pháp luật đã duyệt.

**Independent Test**: Fixtures text/scan/table giữ provenance; graph tests chặn cycle, budget overflow và expired hop.

- [x] T045 [P] [US5] Viết ExtractionBlock/layout tests trong `tests/test_feature016_extraction_blocks.py`
- [x] T046 [US5] Chuẩn hóa ExtractionBlock và fallback trong `api/crawlers/legal_document_pipeline.py` và `api/crawlers/ocr_extractor.py`
- [x] T047 [P] [US5] Viết bounded-hop tests trong `tests/test_feature016_bounded_multihop.py`
- [x] T048 [US5] Cài verified-graph hop budget/cycle/hard-gate logic trong `api/legal_adaptive_hop.py`
- [x] T049 [US5] Chạy Gate E trên fixtures cô lập và lưu evidence trong `reports/feature016/phase-e/`

---

## Phase 7: Polish and final non-browser gate

- [x] T050 [P] Bổ sung CPU/CUDA retrieval image profiles và model fingerprint tests trong `Dockerfile.retrieval`, `docker-compose.release.yml`, `tests/test_feature016_runtime_profiles.py`
- [x] T051 Loại hard-coded embedding path và validate provider output modality trong `open_notebook/ai/hf_embedding.py`, `api/routers/models.py`, `frontend/src/app/(dashboard)/settings/api-keys/page.tsx`
- [x] T052 Chạy backend/frontend/type/build/security/privacy tests áp dụng và lưu manifest trong `reports/feature016/final-non-browser/`
- [x] T053 Chạy isolated API load matrix concurrency 1/5/10/20/30, kiểm tra 3 RPS trong 10 phút và lưu stage timings trong `reports/feature016/final-non-browser/`
- [x] T054 Đối chiếu mọi FR/SC với evidence, cập nhật `docs/7-DEVELOPMENT/legal-answer-trust-hardening.md` và quickstart
- [x] T055 Dừng trước browser UAT và xin phép người dùng; không chạy 2.000 lượt web nếu chưa được xác nhận

---

## Phase 8: Real learned-reranker acceptance slice

**Goal**: Đánh giá `BAAI/bge-reranker-v2-m3` bằng passage pháp luật thật trên cùng candidate set với BM25/RRF và chỉ bật runtime khi đạt Gate C đầy đủ.

**Independent Test**: Baseline và learned reranker dùng cùng identity/candidate window; báo cáo có Top-1/MRR/Recall@k, latency, RAM/VRAM, OOM và safety regressions; cấu hình active chỉ thay đổi khi gate đạt.

- [x] T056 Viết contract tests cho benchmark passage thật, metric so sánh và activation decision
- [x] T057 Tải model local theo revision cố định, ghi manifest/checksum và không commit model binary
- [x] T058 Hoàn thiện loader tài nguyên CPU/CUDA và benchmark cùng BM25/RRF trên candidate window đã qua hard gate
- [x] T059 Chạy baseline-versus-BGE benchmark, lưu evidence trong `reports/feature016/phase-c-real/`
- [x] T060 Chỉ bật learned reranker trong runtime config nếu quality/safety/latency gate đạt; nếu không giữ disabled với reason code
- [x] T061 Chạy regression áp dụng, cập nhật tài liệu và manifest kết quả

---

## Phase 9: Approved live-corpus correction and Golden-294 revalidation

**Authorization**: Người dùng phê duyệt rõ ngày 11/08/2026 bằng câu
`Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca`.

**Scope**: Chỉ các văn bản `31/2024/QH15`, `73/2025/QH15`,
`55/2021/TT-BCA`, `66/2023/TT-BCA`, `88/2025/QH15`, văn bản hiện hành
`116/2026/TT-BCA` và đúng 294 Golden case bị hậu kiểm chặn. Không hard-delete,
không đổi schema, không re-index toàn kho và không tự mở một điều có hiệu lực
một phần khi nguồn chính thức chưa nêu rõ phạm vi.

- [x] T062 Ghi baseline, dừng có kiểm soát retrieval, sao lưu PostgreSQL,
  SurrealDB và hai collection Chroma; xác minh checksum/row count trước khi ghi.
- [x] T063 Thu thập toàn văn và metadata chính thức của `116/2026/TT-BCA`,
  chạy preview và lưu manifest nguồn/hash; không nhập nếu identity/hiệu lực sai.
- [x] T064 Đưa `116/2026/TT-BCA` qua đúng hàng đợi candidate → Admin approval →
  import/embed; xác minh document/article/chunk/vector và active scope.
- [x] T065 Đồng bộ trạng thái hết hiệu lực của `55/2021/TT-BCA` và
  `66/2023/TT-BCA` bằng thao tác có manifest/rollback; giữ dữ liệu lịch sử và
  chứng minh current retrieval không dùng chúng.
- [x] T066 Đối chiếu phạm vi hiệu lực một phần của `31/2024/QH15` và
  `73/2025/QH15`; chỉ ghi affected provision khi có bằng chứng chính thức, nếu
  thiếu thì giữ `partial_scope_unresolved` và fail-closed.
- [x] T067 Kiểm tra/làm lại đúng các structural unit thiếu của
  `88/2025/QH15` và cơ chế phục vụ Điều cực dài của `62/2020/QH14`; mọi thay
  đổi chunk/vector phải có exact-ID manifest và rollback.
- [x] T068 Tái sinh đúng 294 ca, thay nguồn cư trú cũ bằng claim/Điều thực sự
  tương ứng trong `116/2026/TT-BCA`, giữ các ca partial chưa đủ bằng chứng ở
  `needs_revalidation`, và xuất gói review sai khác.
- [ ] T069 Chạy lại schema, validity audit, benchmark 100 ca, regression 1.000
  ca, kiểm thử core/role và health; chỉ gắn stable khi mọi gate bắt buộc đạt.
- [ ] T070 Triển khai bản stable trên runtime đã xác định, kiểm tra health,
  rollback manifest và ghi báo cáo; triển khai Internet cần chỉ rõ máy đích và
  quyền truy cập nếu không phải runtime local hiện có.

## Phase 10: Admin operations dashboard and no-tech model setup

**Scope**: Thay bề mặt hỏi đáp riêng của Admin bằng trung tâm vận hành;
không làm thay đổi luồng hỏi đáp của người dân/cán bộ. Chuyển mọi
cấu hình local/Ollama/model/API key khỏi từng câu hỏi sang một wizard cấu hình
tập trung, có kiểm tra kết nối, che bí mật và có mặc định an toàn.

- [x] T071 Kiểm kê route/quyền/API quản trị hiện có; chốt contract dashboard
  gồm tình trạng dịch vụ, kho văn bản/vector, hàng đợi duyệt, crawler,
  người dùng, model/provider, audit và cảnh báo.
- [x] T072 Xây API tổng hợp dashboard chỉ cho Admin, fail-closed với auth và
  không làm lộ API key/mật khẩu/nội dung audit nhạy cảm.
- [x] T073 Thay trang hỏi đáp Admin bằng dashboard tác vụ một chạm; giữ
  citizen/officer Q&A, deep-link và route guard hiện có.
- [x] T074 Gỡ checkbox `Chạy local bằng Ollama` khỏi giao diện hỏi đáp;
  runtime hỏi đáp chỉ đọc cấu hình model đang hoạt động do Admin quản lý.
- [x] T075 Hoàn thiện wizard Model & API key cho người không chuyên:
  local/cloud, Ollama URL, model gợi ý, API key, test kết nối, kích hoạt và
  hướng dẫn lỗi bằng ngôn ngữ nghiệp vụ.
- [x] T076 Chạy test backend/frontend/auth/role cho dashboard và model setup; chứng
  minh bí mật được che, Admin-only và citizen/officer không bị hồi quy.
- [ ] T077 UAT trực tiếp dashboard Admin và luồng cấu hình Ollama/cloud,
  kiểm tra rollback và ghi báo cáo stable cùng Phase 9.

## Phase 11: Answer-path simplification and direct-chat root-cause closure

**Goal**: Stop planner, exact lookup, and facet gates from discarding one
another's valid evidence. Preserve validity, authority, role, and grounding
gates. The canonical order is `split issue -> bind identity per issue ->
retrieve -> validate -> answer`.

- [x] T078 Add regressions for alphanumeric Articles, two numbered issues,
  two quoted Golden issues, reviewed title aliases, and gazette footers.
- [x] T079 Replace blind comma/conjunction splitting with a splitter that
  understands quotes, parentheses, and numbered issues. Keep one explicit
  Article as one structural issue unless the user explicitly names multiple
  issues.
- [x] T080 Bind law/Article identifiers per issue and stop broadcasting one
  identifier across the whole request. Add only reviewed exact-title aliases
  backed by approved corpus metadata.
- [x] T081 Fix exact SQL for Articles such as 18a/37a and remove known gazette
  headers/footers from the answer projection without mutating source data.
- [x] T082 Run focused unit/contract/retrieval tests, restart local services,
  and retest the same two citizen questions with answer/source/timing evidence.
- [x] T083 Document the architecture decision and rerun the applicable Golden
  gate. Do not mark stable while any requested issue is silently omitted.

## Phase 12: Legal Answer Pipeline V2

**Goal**: Reduce the citizen answer path to `deterministic route -> retrieval ->
hard legal gates -> issue-owned evidence packet -> one generation -> one
claim/citation validator -> response and audit`, while preserving the legacy
path as rollback.

- [x] T084 Add the four-route deterministic router, explicit-only issue
  splitting, per-issue identity binding and pre-retrieval clarification.
- [x] T085 Add independent vector/BM25 RRF ranking, deterministic tie-breaking,
  exact-Article bypass and an explicit disabled BGE decision.
- [x] T086 Materialize one evidence packet per issue, put approved form evidence
  before generation, skip V2 repair generations and preserve valid sibling
  issue claims through the single validator.
- [x] T087 Keep local/cloud on the same evidence and validation path, preserve
  provider egress controls and retain public API/SSE/answer-mode compatibility.
- [x] T088 Add the read-only PostgreSQL/vector/validity reconciliation manifest
  and admin trace fingerprints without migration, backfill or re-index.
- [x] T089 Add router, retrieval, citation, form, manifest, privacy and stale
  benchmark-checksum regressions; verify two citizen API cases without browser
  UAT.
- [ ] T090 Pass Golden 100, Golden 294, retrieval/full-answer 1,000 and cold
  performance gates. Golden 100, Golden 294 and cold 1,000 retrieval now pass
  (Recall@10/Top-5 99.905%, P95 1,999 ms, zero wrong-domain/expired selection).
  The 1,000 final-answer gate still fails: 42.5% case pass, 42.912% facet
  coverage, 94.932% citation validity, seven external errors and 89.8%
  provider fallback caused by the configured cloud rate limit. Do not claim
  stable until the same full-answer gate passes with an available provider.
- [ ] T091 Enable the feature flag for citizen rollout, then officer, only after
  T090 passes; BGE remains disabled until its separate activation gate passes.
- [ ] T092 Run separately approved browser UAT and publish the final stable
  report. This is a mandatory stop point.

## Dependencies & Execution Order

- Phase 1 → Phase 2 bắt buộc tuần tự.
- Gate A đạt mới bắt đầu Phase 3; Gate B đạt mới bắt đầu learned reranker/dual embedding.
- Provenance/audit có thể chuẩn bị code sau Gate A nhưng không được dùng để tuyên bố sửa answer correctness.
- OCR/multi-hop chỉ thực hiện sau Gate B/C.
- T055 là điểm dừng bắt buộc; browser UAT không được tự động tiếp tục.

## Implementation Strategy

1. Hoàn tất từng task theo TDD và đánh dấu `[x]` sau khi có test/evidence.
2. Không ghi đè thay đổi không liên quan trong worktree hiện tại.
3. Không apply migration, backfill, re-index hoặc switch active pointer ngoài
   phạm vi Phase 9 đã được phê duyệt rõ; Phase 9 vẫn cấm re-index toàn kho.
4. Khi một gate không đạt, sửa trong cùng phase và chạy lại; không chuyển phase bằng cách nới safety threshold.
