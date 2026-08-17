# Lịch sử hỏi đáp trong quản lý tài khoản

## Quyết định

- Chỉ quản trị viên được truy cập API lịch sử hỏi đáp trong chức năng quản lý tài khoản.
- Danh sách toàn hệ thống chỉ trả dữ liệu tóm tắt, không gồm câu trả lời, nguồn pháp lý, dấu vết RAG hoặc thông tin tệp tải lên.
- Khi chọn một tài khoản cụ thể, API mới trả nội dung câu trả lời và nguồn pháp lý của chính tài khoản đó.
- Mỗi lần xem lịch sử chi tiết theo tài khoản được ghi vào `user_audit_log` với hành động `user.ask_history.view`.
- Không thay đổi schema và không sửa hoặc lập chỉ mục lại dữ liệu hỏi đáp đã có.

## Bộ lọc

Lịch sử hỗ trợ lọc xác định trước theo tài khoản, vai trò, lĩnh vực, đơn vị, mức căn cứ và khoảng thời gian. Bộ lọc tài khoản dùng tham chiếu bản ghi SurrealDB thay vì so sánh chuỗi tự do để tránh trộn dữ liệu giữa các tài khoản.

## Giao diện

Màn quản lý tài khoản có hai luồng riêng:

- **Lịch sử hỏi đáp**: câu hỏi, câu trả lời, thời gian xử lý, mức căn cứ và nguồn pháp lý.
- **Lịch sử thao tác**: thay đổi tài khoản và các lần quản trị viên xem lịch sử chi tiết.

Nút **Đổi tài khoản** đã được loại bỏ khỏi màn hình này; việc đăng xuất vẫn nằm ở điều hướng chung của hệ thống.
