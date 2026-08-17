# Pipeline đánh giá retrieval cho tập câu hỏi lớn

## Phạm vi

`scripts/evaluate_retrieval_dataset.py` đánh giá toàn bộ dataset bằng endpoint
retrieval live `/search/batch`. Retrieval không gọi DeepSeek, không dùng fixture và
không có nhánh fallback sang dữ liệu giả. Script chỉ đọc corpus qua dịch vụ đang
chạy; không đổi active collection, không xóa hoặc embedding lại corpus.

Dataset bắt buộc được truyền bằng `--dataset` ở dạng JSON/JSONL. Gold contract nguồn
được truyền riêng bằng `--expected-sources`; chỉ hai outcome đã được legal review là
`AVAILABLE_CORRECTLY_TIERED` và `VERIFIED_DATA_GAP` được dùng để chấm.

## Luồng đánh giá

1. Chuẩn hóa câu hỏi bằng Unicode NFC, case-fold và khoảng trắng.
2. Lập tối đa sáu issue bằng issue planner deterministic.
3. Gọi core retrieval live; chỉ gọi expanded retrieval khi facet còn không chắc chắn.
4. Gộp và khử trùng source, rồi phân loại:
   `FOUND_AND_RETRIEVED`, `FOUND_NOT_RETRIEVED`, hoặc `VERIFIED_DATA_GAP`.
5. Ghi rank top 5/top 10, source URL, hiệu lực, domain và reason code.
6. Cache bằng SHA-256 của `normalized_question + legal_as_of`; cache và report không
   lưu câu hỏi gốc.

Model review là bước opt-in riêng qua `--model-url`. Nếu không truyền tham số này,
model request count luôn bằng 0. Khi bật, chỉ hợp của bốn nhóm được gửi: đại diện đầu
tiên theo lĩnh vực, retrieval không chắc chắn, regression mới, và mẫu ngẫu nhiên xác
định 1–5%.

## Cổng PASS mặc định

- Recall@10 >= 0,95.
- Direct-source top 5 >= 0,95.
- Wrong-field = 0.
- Expired selection = 0.
- Coverage >= 0,90.
- Retrieval P95 <= 3.000 ms.
- Fallback rate = 0.
- Có gold contract nguồn và có kết quả retrieval live.
- Số model request không vượt số case được policy sampling chọn.

Nếu một cổng không đạt, artifact có `status=FAIL` và tiến trình trả exit code khác 0.
Không được đổi ngưỡng phát hành chỉ để làm xanh một lần benchmark.

## Artifact riêng tư

Artifact chỉ chứa case ID, thống kê tổng hợp và metadata nguồn allowlist. Trước khi
ghi file, validator từ chối mọi field chứa question, answer, content, prompt, token,
credential hoặc secret. Không lưu câu trả lời pháp lý hay nội dung chunk.

## Cách chạy

```powershell
python scripts/evaluate_retrieval_dataset.py `
  --dataset notebook_data/legal-golden-set.json `
  --expected-sources reports/feature005/step2-data-gap-20260724/expected-sources.jsonl `
  --retrieval-url http://127.0.0.1:8765 `
  --legal-as-of 2026-07-23 `
  --concurrency 1 `
  --sample-percent 1 `
  --cache reports/feature005/retrieval-dataset-20260726/cache.json `
  --output reports/feature005/retrieval-dataset-20260726/report.json
```

Không truyền `--model-url` cho lượt retrieval-only. Chạy lại cùng cache để xác nhận
reuse theo câu hỏi đã chuẩn hóa và ngày áp dụng.

