# Prompt `/goal` triển khai hệ thống quản lý văn bản pháp luật

Sao chép toàn bộ khối dưới đây vào Codex ở luồng triển khai chính:

```text
/goal Triển khai hệ thống quản lý toàn bộ vòng đời văn bản pháp luật theo kế hoạch tại:
J:\ChatBotLegal\docs\7-DEVELOPMENT\legal-document-management-system-plan.md

Mục tiêu cuối:
- Quản lý được danh sách 20.000–100.000 văn bản bằng dashboard, tìm kiếm, bộ lọc và trang chi tiết.
- Có quy trình thêm văn bản bằng bản nháp, chống trùng, kiểm tra, phê duyệt, lập chỉ mục rồi mới kích hoạt.
- Sửa metadata có lịch sử; sửa nội dung phải tạo phiên bản mới và không ghi đè phiên bản đang phục vụ.
- Có ngừng phục vụ, lưu trữ, dọn vector, phục hồi và xóa vĩnh viễn có kiểm soát.
- Theo dõi hiệu lực toàn phần/một phần và chặn tất cả đường retrieval trước khi kết quả vào mô hình.
- Tự tìm ứng viên văn bản tác động/thay thế từ nguồn chính thức; metadata/vector chỉ xếp hạng, không tự xác nhận pháp lý.
- Quản lý SQL, kho nhanh, kho mở rộng, kho lịch sử, manifest, background job, retry và kiểm đếm.
- Đưa FAQ, câu trả lời mẫu, thủ tục và biểu mẫu có căn cứ cũ vào hàng chờ rà soát.
- Phân quyền rõ người dân, cán bộ, biên tập viên pháp lý, kiểm duyệt viên, Admin kỹ thuật và Super Admin.
- Mọi mutation có reason, authorization, idempotency, audit, backup/rollback và kiểm thử.

Nguyên tắc bắt buộc:
1. Không bịa nguồn, số hiệu, điều khoản, ngày, phí hoặc quan hệ pháp lý.
2. Giữ URL và metadata nguồn chính thức xuyên suốt.
3. SQL/kho file là nguồn gốc; vector chỉ là chỉ mục có thể tạo lại.
4. Chặn phục vụ trước, dọn vector sau. Cleanup thất bại không được bỏ chặn.
5. Văn bản chưa duyệt và phiên bản lập chỉ mục chưa hoàn tất không được active.
6. Không ghi đè nội dung đã duyệt; mọi sửa nội dung tạo DocumentVersion mới.
7. Không dùng LLM/vector similarity làm bằng chứng xác nhận thay thế.
8. Hết hiệu lực một phần chưa rõ phạm vi phải fail closed.
9. Không xóa nội dung/lịch sử pháp lý theo mặc định; ưu tiên deactivate/archive.
10. Không hard delete, rewrite/reindex corpus thật hoặc chạy migration khi chưa có phê duyệt riêng.
11. Giữ nguyên mọi thay đổi không liên quan trong worktree.
12. Không tuyên bố hoàn tất nếu test, browser journey hoặc release gate bắt buộc còn lỗi.

Cách thực hiện:
- Đọc đầy đủ AGENTS.md, constitution và kế hoạch nêu trên.
- Rà soát code và dữ liệu hiện có trước khi thiết kế; tận dụng lớp validity, crawler, import, retrieval và vector cleanup đã có.
- Tạo một feature Spec Kit mới dành riêng cho legal document lifecycle management; không ghi đè feature 013.
- Viết spec, research, data-model, API/UI contracts, quickstart và tasks theo dependency.
- Nêu rõ module bị chạm, rủi ro pháp lý, rủi ro dữ liệu, chiến lược backup/rollback và tiêu chí nghiệm thu.
- Chia implementation thành lát cắt nhỏ theo các giai đoạn 0–7 trong kế hoạch.
- Viết test đỏ trước mỗi hành vi nguy hiểm, sau đó triển khai tối thiểu để test xanh.
- Sau mỗi giai đoạn chạy unit, contract, authorization, integration và regression retrieval liên quan.
- Với thay đổi nhìn thấy trên web, khởi động đúng dịch vụ và kiểm thử trực tiếp bằng browser với dữ liệu thử nghiệm; không dùng hoặc xóa corpus thật.
- Cập nhật tài liệu dưới docs/7-DEVELOPMENT/ cho mọi quyết định ảnh hưởng ingestion, retrieval, hiệu lực, vector hoặc background jobs.

Gate bắt buộc phải dừng và xin tôi phê duyệt trước khi thực hiện:
- Migration/schema ảnh hưởng imported legal records.
- Tạo hoặc sửa dữ liệu corpus thật.
- Reindex toàn kho hoặc xóa/chuyển vector thật.
- Hard delete văn bản, phiên bản, lịch sử hoặc audit.
- Thêm background daemon/external service mới hoặc dịch vụ trả phí.
- Chọn kiến trúc kho lịch sử và cơ chế hai người duyệt.

Thứ tự ưu tiên:
1. Giai đoạn 0: baseline, đặc tả, schema proposal, backup/rollback; không mutation.
2. Giai đoạn 1: management console read-only, dashboard/filter/detail/audit projection.
3. Dừng xin phê duyệt schema.
4. Giai đoạn 2: draft, add, edit, version, approval và activation an toàn.
5. Giai đoạn 3: validity/relationship/replacement workflow.
6. Giai đoạn 4: background vector cleanup, historical và restore.
7. Giai đoạn 5: FAQ impact và bulk operations.
8. Giai đoạn 6: controlled deletion.
9. Giai đoạn 7: security, performance, recovery và browser release gate.

Định dạng cập nhật:
- Báo cáo ngắn sau mỗi lát cắt: đã làm, file thay đổi, test đã chạy, dữ liệu thật có bị chạm hay không, rủi ro còn lại.
- Nếu bị chặn bởi gate, dừng đúng điểm, trình bày lựa chọn và xin phê duyệt; không tự mở rộng quyền.
- Kết quả cuối phải có đường dẫn file, test counts, browser evidence, benchmark, migration/rollback status và các giới hạn còn lại.

Bắt đầu bằng Giai đoạn 0 và Giai đoạn 1. Không triển khai migration hoặc mutation corpus cho đến khi tôi phê duyệt gate schema.
```

