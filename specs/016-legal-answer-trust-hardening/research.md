# Research: Legal Answer Trust Hardening

## Decision 1 — Đo lường trước khi thay đổi hành vi

**Decision**: Dedupe workbook thành 100 ca quan sát; tạo golden v2 riêng và không coi workbook là ground truth.

**Rationale**: Workbook có 1.540 lượt nhưng chỉ 100 câu; cờ “Có nguồn” không đo đúng/sai và answer text chứa DOM.

**Alternatives considered**: Chấm trực tiếp 1.540 dòng bị loại vì lặp và không có expected evidence/facts.

## Decision 2 — Strict validity theo readiness

**Decision**: Giữ registry/snapshot hiện có, hoàn thiện coverage đến cấp provision; shadow trước, current-answer fail closed sau readiness 100% và freshness 24 giờ.

**Rationale**: Thuật toán partial blocking đã tồn tại; lỗi thực tế đến từ coverage/freshness hoặc protect mode cho unobserved documents.

**Alternatives considered**: Chặn toàn văn bản partial làm mất recall; protect mode cho câu trả lời hiện hành không đủ an toàn.

## Decision 3 — Intent/facet là hard eligibility

**Decision**: Chuẩn hóa `LegalIntent`; legacy keywords chỉ tạo candidates, không làm evidence đủ điều kiện.

**Rationale**: Các lỗi khiếu nại/tố cáo, xử phạt/thi hành án và thường trú/xóa thường trú là semantic confusion trước generation.

**Alternatives considered**: Tăng vector similarity hoặc thêm prompt bị loại vì không bảo đảm procedure/facet.

## Decision 4 — Fallback ở cấp claim

**Decision**: `verified_source_condensed` cần claim–facet–quote validation; `source_view_only` không phải câu trả lời pháp lý; bỏ first-two-source excerpts.

**Rationale**: Fallback hiện là nguồn chính của các câu có nhãn xác minh nhưng sai nội dung.

**Alternatives considered**: Giữ extractive fallback và tăng threshold bị loại vì source eligibility không chứng minh đoạn trích trả lời facet.

## Decision 5 — Exact article packet

**Decision**: Tái sử dụng exact-article/section grounding hiện có, nạp toàn bộ Điều theo structural index và fail incomplete.

**Rationale**: Similarity top-1 không đại diện cho toàn bộ Điều.

**Alternatives considered**: Tăng top-k bị loại vì vẫn không chứng minh đủ khoản/điểm và thứ tự.

## Decision 6 — Deterministic path

**Decision**: Temperature 0 cho mọi legal generation path, stable tie-break, versioned trace và semantic claim dedupe.

**Rationale**: 11/100 câu thay đổi giữa các lượt giống nhau; một số sort chỉ dùng score.

**Alternatives considered**: Chấp nhận wording variation bị loại cho claim/citation/mode; prose có thể khác chỉ khi provider/version khác và phải ghi trace.

## Decision 7 — Một learned reranker trước

**Decision**: `BAAI/bge-reranker-v2-m3`, window 40, batch 8, optional FP16; heuristic fallback.

**Rationale**: GPU 6 GB không đủ giữ nhiều model; cross-encoder có lợi nhất cho hard-negative pháp luật.

**Alternatives considered**: Thay BM25/RRF bị loại; tải đồng thời reranker và dual embed bị loại vì OOM risk.

## Decision 8 — Dual embedding shadow

**Decision**: VNLegal-LAL vẫn active; BGE-M3 collection riêng, standard RRF, không trộn vector, không switch nếu chưa đạt gate.

**Rationale**: Đổi embedding cần re-index toàn bộ và có rủi ro corpus.

## Decision 9 — Citation provenance sidecar

**Decision**: Ba mức `physical_span`, `content_quote`, `metadata_only`; sidecar theo chunk/span và source hash.

**Rationale**: Cho rollout lớp, không giả trang cho DOCX/legacy, không sửa corpus chính.

## Decision 10 — Audit chain có checkpoint ký

**Decision**: Hash canonical payload + previous hash; checkpoint Ed25519 với private key ngoài DB; chain-required operation fail closed.

**Rationale**: Hash chain không có anchor ngoài DB vẫn có thể bị thay toàn bộ bởi DB admin.

## Decision 11 — Provider giữ đa nguồn nhưng kiểm soát egress

**Decision**: Công bố local/cloud/provider/model, redact PII trước cloud, audit hash/reason code thay raw prompt.

**Rationale**: Local-only không phù hợp hiện trạng; cloud không được phép làm mất privacy/audit.

## Decision 12 — OCR/layout và multi-hop sau correctness

**Decision**: ExtractionBlock có bbox/table path; multi-hop chỉ graph đã duyệt, max 2 hops/16 queries.

**Rationale**: Đây là tăng coverage, không sửa được wrong-intent/wrong-validity.

## Decision 13 — Deployment portability

**Decision**: CPU/CUDA images riêng; model path/fingerprint qua environment; safety không bị tắt trên máy yếu; target 5.000 accounts/30 concurrent Ask được xác minh bằng non-browser load test trước web UAT.

**Rationale**: Release retrieval hiện cài CPU-only torch dù có biến CUDA; secondary embedding còn hard-coded Windows path.

## Decision 14 — Không tự chạy browser UAT

**Decision**: Hoàn thành code tests và isolated API gates, sau đó dừng và xin phép.

**Rationale**: Yêu cầu trực tiếp của người dùng; tránh 2.000 lượt live trước khi các safety gates nền đạt.
