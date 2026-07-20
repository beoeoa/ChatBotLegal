# Benchmark retrieval: tốc độ và cổng chất lượng

## Mục tiêu

`scripts/benchmark_legal_retrieval.py` đo retrieval độc lập với mô hình sinh câu
trả lời. Runner dùng cùng một bộ ca để so sánh:

- `core`: `candidate_count` 80, 120 và 180;
- `expanded`: `candidate_count` 120, 180 và 240;
- mức đồng thời: 1, 5, 10 và 20;
- hai lượt `cold` và `warm`;
- Recall@6 và khả năng giữ lại nguồn thẩm quyền quan trọng.

Runner không thay đổi corpus, index, API hay cấu hình retrieval. Nó chỉ gọi
endpoint tìm kiếm hiện có khi người vận hành bật tải local một cách rõ ràng.

## Ý nghĩa cold/warm

- `cold` là lượt đo đầu tiên của một cấu hình trong tiến trình benchmark.
- `warm` là lượt lặp lại ngay sau đó với cùng ca và cấu hình.

Runner không tự restart dịch vụ hoặc xóa cache. Vì vậy `cold` không khẳng định
đây là lần đầu tiên của toàn bộ tiến trình retrieval. Khi cần đo process-cold
thật, người vận hành phải khởi động dịch vụ sạch trước benchmark và ghi điều
kiện thử nghiệm ở ngoài báo cáo máy đọc. Cách này tránh runner tự ý tác động
đến dịch vụ đang chạy.

## Dữ liệu ca kiểm thử

Runner nhận một trong hai định dạng:

1. `records` từ bộ expert review; mặc định chỉ dùng bản ghi
   `expert_review_status=approved`.
2. `cases` là fixture tổng hợp dùng cho kiểm thử local.

Mỗi ca cần có:

- `case_id`: mã mờ, không chứa câu hỏi;
- `query` hoặc `question`: chỉ đọc vào bộ nhớ để gửi retrieval;
- `expected_source_ids`: mã nguồn dạng `law:60/2014/QH13`, `doc:...` hoặc
  `chunk:...`;
- `critical_authority_source_ids`: các nguồn bắt buộc phải còn trong top 6.

Với expert record, `expected_documents` được chuẩn hóa thành source ID. Nếu
chưa khai báo riêng nguồn quan trọng thì toàn bộ `expected_documents` được xem
là quan trọng; runner không tự đoán tên hoặc số hiệu văn bản còn thiếu.

## Chế độ an toàn mặc định

Lệnh sau chỉ lập kế hoạch, không gửi request:

```powershell
python scripts/benchmark_legal_retrieval.py `
  --cases notebook_data/legal-golden-expert-review.json `
  --report notebook_data/quality_runs/retrieval-benchmark-plan.json
```

Báo cáo dry-run cho biết case ID, lưới cấu hình và tổng số request dự kiến. Với
334 ca và lưới mặc định, tải đầy đủ là lớn; cần xem kế hoạch trước khi chạy.

Kiểm tra bằng fixture local:

```powershell
python scripts/benchmark_legal_retrieval.py `
  --cases path/to/synthetic-cases.json `
  --fixture-results path/to/retrieval-responses.json `
  --report notebook_data/quality_runs/retrieval-benchmark-fixture.json
```

Chỉ khi đã chủ động dành tài nguyên cho phép thử mới bật tải local:

```powershell
python scripts/benchmark_legal_retrieval.py `
  --execute-local `
  --search-url http://127.0.0.1:8765/search `
  --cases path/to/approved-cases.json `
  --report notebook_data/quality_runs/retrieval-benchmark-local.json
```

Chế độ live chỉ chấp nhận `localhost`, `127.0.0.1` hoặc `::1`; URL có user,
password, query string hay fragment bị từ chối. Có thể chạy pilot bằng
`--case-limit` trước khi chạy toàn bộ.

## Cổng chất lượng

Mặc định, từng cấu hình phải đồng thời đạt:

- Recall@6 trung bình ít nhất 0,95;
- tỷ lệ giữ source ID quan trọng là 1,00;
- không có request lỗi.

`pass=true` ở mức toàn báo cáo chỉ khi tất cả cấu hình đều qua. Có thể thay
ngưỡng bằng `--min-recall-at-6` và
`--min-critical-authority-retention`, nhưng mọi thay đổi ngưỡng phát hành cần
được duyệt như một quyết định chất lượng pháp lý.

## Hợp đồng riêng tư của báo cáo

Báo cáo chỉ lưu:

- case ID và source ID đã chuẩn hóa;
- tier, candidate count, concurrency, cold/warm;
- Recall@6, kết quả cổng và timing client/server đã allowlist.

Không lưu câu hỏi, câu trả lời, prompt, nội dung chunk, tiêu đề văn bản, URL,
credential, token, cookie hoặc raw exception. Lỗi chỉ được ghi bằng trạng thái
`error`; thông điệp exception không được sao chép vào báo cáo.

## Kiểm tra tự động

```powershell
python -m pytest -q tests/test_retrieval_benchmark.py
python -m py_compile scripts/benchmark_legal_retrieval.py
```

Các kiểm thử dùng fixture cục bộ, không phát sinh live load.
