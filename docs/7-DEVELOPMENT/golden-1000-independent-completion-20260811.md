# Golden 1000 độc lập - đặc tả hoàn thiện

## Mục tiêu

Tạo một bộ Golden gồm đúng 1.000 ca hỏi đáp pháp luật cho vai trò người dân,
phục vụ đánh giá truy xuất, hiệu lực, độ đầy đủ, trích dẫn và chế độ dự phòng.
Bộ này độc lập với bộ `golden-1000-luna`, vốn được giữ lại như bộ kiểm thử
paraphrase/stress và không được coi là Golden 1.000 ca độc lập.

Ngày chốt hiệu lực của bộ dữ liệu là **2026-08-11**. Quy trình chỉ đọc kho
PostgreSQL và nguồn chính thức; không nhập, sửa, xóa hay re-index văn bản thật.

## Phân bổ bắt buộc

Mỗi lĩnh vực có 200 ca:

1. Hộ tịch/chứng thực.
2. Đất đai/xây dựng.
3. Cư trú/an ninh.
4. Khiếu nại/tố cáo/xử phạt.
5. An sinh/y tế/giáo dục.

Trong từng lĩnh vực:

- 120 ca thủ tục/tình huống.
- 30 ca truy xuất chính xác điều luật.
- 20 ca đa vấn đề.
- 20 ca kiểm tra hiệu lực.
- 10 ca không đủ dữ kiện hoặc ngoài phạm vi, phải từ chối có nhãn.

Tập đánh giá gồm 600 development, 200 validation và 200 held-out. Một họ bằng
chứng (văn bản + điều + đơn vị nghĩa/claim) chỉ được nằm trong một tập.

## Định nghĩa một ca độc lập

Hai ca không độc lập nếu chỉ đổi cách diễn đạt câu hỏi nhưng giữ nguyên toàn bộ
nguồn, điều và các nhận định bắt buộc. Mỗi ca trả lời có căn cứ phải có chữ ký
`law_number + article + normalized required_claims` duy nhất trong toàn bộ bộ
dữ liệu. Ca đa vấn đề dùng chữ ký có thứ tự của toàn bộ các nguồn/điều/claim.

Ca dự phòng phải khác nhau về loại thông tin thiết yếu bị thiếu hoặc ranh giới
phạm vi; không được biến một câu hỏi có thể trả lời thành ca từ chối.

## Hợp đồng nguồn và bằng chứng

- Chỉ nhận URL chính thức từ `vbpl.vn` hoặc `vanban.chinhphu.vn`.
- Mỗi nguồn dùng để trả lời hiện hành phải có trạng thái hiệu lực tại ngày chốt.
- Văn bản hết hiệu lực chỉ xuất hiện trong `forbidden_sources` hoặc ca lịch sử,
  không được dùng làm nguồn trả lời hiện hành.
- Mỗi claim phải ánh xạ tới một quote có thật trong `legal_articles.content`.
- Lưu `db_document_id`, `db_article_id`, số điều, char start/end và URL nguồn.
- Với câu hỏi “Điều X”, bằng chứng phải bao phủ toàn bộ nội dung điều theo đúng
  thứ tự khoản/điểm; không chấp nhận một chunk khớp cao nhất đại diện cả điều.
- Không suy đoán số điều, thời hạn, phí, thẩm quyền hoặc hiệu lực bị thiếu.

## Cổng chất lượng bắt buộc

1. Đúng 1.000 ca, đúng phân bổ lĩnh vực, loại ca và split.
2. Đúng schema và 1.000 `case_id`/câu hỏi/chữ ký pháp lý duy nhất.
3. Không có cặp gần trùng vượt ngưỡng sau chuẩn hóa.
4. Mọi expected source có URL chính thức, điều tồn tại, quote khớp DB và trạng
   thái hiệu lực rõ ràng.
5. Không có văn bản hết hiệu lực trong nguồn trả lời hiện hành.
6. Mỗi required claim được chứng minh bởi ít nhất một span; không có claim mồ
   côi và không dùng cùng một span để giả vờ chứng minh các ý không tương đương.
7. Ca exact-article bao phủ đủ cấu trúc điều và đúng thứ tự.
8. Ca multi-issue có ít nhất hai issue và bằng chứng riêng cho từng issue.
9. Ca validity chứa nguồn hiện hành cùng nguồn cấm/quan hệ thay thế tương ứng.
10. Ca insufficient-evidence có `expected_refusal=true`, lý do thiếu cụ thể và
    không chứa expected source dùng để tạo câu trả lời khẳng định.
11. Không trùng forbidden source trong một ca; không một nguồn vừa expected vừa
    forbidden.
12. Workbook không có lỗi công thức, có đủ trang duyệt, bộ lọc, cố định tiêu đề,
    danh sách chọn trạng thái và được render kiểm tra trực quan.

## Đầu ra

Đầu ra mới nằm tại `outputs/golden-1000-complete/` và tối thiểu gồm:

- `golden-1000-complete.json`: dữ liệu máy đọc.
- `golden-1000-complete.xlsx`: gói duyệt của chuyên gia pháp lý.
- `source-inventory.json`: danh mục nguồn/điều đã dùng.
- `validation-report.json`: kết quả từng cổng và mẫu lỗi.
- `manifest.json`: hash, ngày chốt, trạng thái chỉ đọc và khả năng tái lập.
- `final-report.md`: kết quả, giới hạn còn lại và hướng dẫn duyệt.

Chỉ gắn nhãn `ready_for_human_approval` khi toàn bộ cổng máy kiểm tra đều đạt.
Việc máy kiểm tra đạt không thay thế phê duyệt nội dung của chuyên gia pháp lý.

## Kết quả thực hiện ngày 11/08/2026

- Đã tạo đúng 1.000 ca và gắn trạng thái `ready_for_human_approval`.
- Đã bổ sung nguồn hiện hành riêng cho nhánh xây dựng (62/2020/QH14) và chứng thực tại Hải Phòng (43/2025/NQ-HĐND).
- Báo cáo kiểm định dữ liệu và kiểm định nhập ngược workbook đều `all_passed=true`.
- Tệp duyệt cuối có 9 sheet, 1.000 ca, 1.243 claim và 1.050 lượt nguồn.
- Không có thao tác ghi vào PostgreSQL hoặc thay đổi vector production.

## Phê duyệt toàn bộ

Người dùng xác nhận đã xem và duyệt toàn bộ 1.000 ca. Việc phát hành tạo hai lớp dữ liệu:

- Bản đầy đủ giữ provenance, split, lý do dự phòng và thông tin kiểm soát hiệu lực.
- Bản chiếu tương thích Golden v2 loại bỏ các trường ngoài hợp đồng, đổi
  `explicit_fallback` thành `source_view_only + expected_refusal=true` nhưng giữ nhãn gốc
  trong metadata/risk tag.

Bản canonical được lưu ở `notebook_data/feature016-golden-1000-approved.json`. Việc phê
duyệt này không kích hoạt huấn luyện reranker, không thay collection đang hoạt động và không
ghi corpus/vector production. Mọi thay đổi hiệu lực nguồn phải làm ca liên quan chuyển sang
`needs_revalidation` trước lần chấm tiếp theo.

## Post-approval serving audit

Human approval does not override the serving validity gate. The follow-up
audit documented in `golden-1000-post-approval-live-audit-20260811.md` found
294 approved cases whose expected source is blocked by the 2026-08-11 serving
snapshot. The release status is therefore `needs_revalidation` until those
cases are corrected and re-approved. The original approval receipt remains
unchanged for audit.
