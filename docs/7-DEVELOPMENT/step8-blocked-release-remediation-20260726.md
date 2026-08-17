# Khắc phục BLOCKED_RELEASE — 2026-07-26

Trạng thái cuối: **BLOCKED_RELEASE**. Hai dependency còn đỏ là
**BLOCKED_EXTERNAL** và **BLOCKED_LEGAL_REVIEW**.
`LEGAL_SECTION_GROUNDING_ENABLED=false` vẫn được giữ trong `.env` runtime
persisted; rollout ở stage `0`, không có role nào được bật.

Không corpus nào bị xóa, sửa hàng loạt hoặc embedding lại. Active collection
vẫn là `legal_chunks_lechan_primary_v20260723`; model mặc định vẫn là
`deepseek-v4-pro`; không có repair call, dịch vụ trả phí mới hoặc fixture
fallback.

## Kết quả khắc phục retrieval

Các regression được viết trước khi sửa cho:

- domain của case đã rà soát bị mất khi issue planner trả `unknown`;
- câu hành chính chung không giữ domain của case;
- domain trật tự đô thị nhận nhầm nguồn cư trú;
- câu xử phạt không biên bản bị route nhầm sang xây dựng;
- tra cứu bản sao chứng thực và cải chính hộ tịch thiếu định danh văn bản;
- case ngoài phạm vi đã khai báo không được ghi `VERIFIED_DATA_GAP`;
- `VERIFIED_DATA_GAP` hợp lệ không được tính là coverage.

Lần chạy live retrieval-only cuối trên 167 câu:

| Chỉ số | Kết quả | Ngưỡng |
|---|---:|---:|
| Recall@10 | 97,059% | >=95% |
| Direct-source top5 | 96,154% | >=95% |
| Wrong-field | 0 | 0 |
| Expired selection | 0 | 0 |
| Coverage | 91,617% | >=90% |
| Retrieval P50 | 330 ms | ghi nhận |
| Retrieval P95 | 707 ms | <=3.000 ms |
| Model request | 0 | 0 |
| Fixture fallback | 0 | 0 |

Retrieval gate hiện **PASS**. Artifact chi tiết không chứa câu hỏi hoặc câu trả
lời pháp lý; nó chỉ giữ case ID, rank, metadata nguồn và reason code.

## Provider và generation

`/ready` trả HTTP 503 với mã chính xác:

```text
provider_payment_required
provider=deepseek
model=deepseek-v4-pro
```

Database và retrieval đều ready. Lần chạy generation concurrency 5 hoàn thành
0/5, có 5 server failure; repair count bằng 0. Lần chạy 9 role cases qua API
dùng ba tài khoản test cô lập cũng đạt 0/9, cả 9 trả HTTP 502. Summary API và
generation đã qua privacy scanner; báo cáo chi tiết tạm thời có nội dung pháp
lý đã bị xóa.

Đây là **BLOCKED_EXTERNAL** thuộc Step 5/provider operations. Không được đổi
model hoặc tự thêm provider khác. Điều kiện mở lại: provider/model hiện tại
ready, concurrency 5 không mass failure, API 9/9, UI 9/9, coverage >=90%,
100% claim hiển thị có evidence hợp lệ và P95 end-to-end <=30 giây.

## Form Catalog và rà soát pháp lý

25/25 test kỹ thuật của catalog và runtime hard gate đạt. Tuy nhiên:

- 93 biểu mẫu được kiểm tra;
- 0 biểu mẫu đạt điều kiện runtime;
- 93 biểu mẫu cần legal review;
- chưa ghi nhận human legal review;
- 6/7 nhóm ưu tiên ở trạng thái `LEGAL_REVIEW_REQUIRED`;
- đăng ký kết hôn có yếu tố nước ngoài là `VERIFIED_DATA_GAP` do chưa có
  `procedure_id` chuẩn trong catalog.

Runtime tiếp tục trả `forms_unavailable=true`; seed, candidate và record chưa
approved không được phục vụ. Đây là **BLOCKED_LEGAL_REVIEW**, không phải phê
duyệt có thể thay thế bằng automated test.

Người rà soát phải xác nhận procedure mapping, mã/tên chuẩn, URL/file chính
thức, căn cứ và hiệu lực, phạm vi, checksum, provenance và quyết định approved
cho từng record. Chỉ record qua toàn bộ hard gate mới được đưa vào runtime.

## Các cổng kỹ thuật đã chạy

- Backend full: 916 passed, 0 failed.
- Frontend: 87 passed, 0 failed.
- Type-check: PASS.
- Lint: 0 error, 11 warning.
- Production build: PASS.
- Authorization: 48 passed, 0 failed.
- Form validation: 25 passed, 0 failed.
- Privacy scanner: PASS cho summary API role và generation.
- Rollback state dry-run: stage 0, enabled roles rỗng, không ghi state.

UI 9 role cases không chạy lại vì dependency provider đang đỏ và feature flag
phải giữ false. Vì vậy không có cơ sở báo PASS hay bắt đầu rollout
Admin → Officer → Citizen.

Báo cáo máy đọc:
`reports/feature005/remediation-20260726/release-remediation-summary.json`.
Đây là bằng chứng release-kỹ-thuật, không phải phê duyệt pháp lý.
