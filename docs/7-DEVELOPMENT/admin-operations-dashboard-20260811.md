# Dashboard điều hành Admin — 2026-08-11

## Quyết định

`/admin` là điểm vào mặc định sau khi Admin đăng nhập. Dashboard là bề mặt
chỉ đọc để tổng hợp sức khỏe hệ thống, việc cần ưu tiên, kho pháp lý, crawl và
import, tri thức, tài khoản, SLA hỗ trợ, cấu hình AI, telemetry hiện tại và
audit bảy ngày. Các thao tác thay đổi dữ liệu vẫn nằm tại trang nghiệp vụ
chuẩn; Dashboard chỉ cung cấp liên kết có bộ lọc.

Dashboard không khôi phục Control Center cũ. Nó không duyệt candidate, chạy
retention, sửa cấu hình, tham gia live support hoặc đọc nội dung hồ sơ/chat.

## Contract và dữ liệu

`GET /api/admin/control/dashboard` chỉ dành cho session Admin và trả về:

- `freshness` và `attention_items` do backend xác định;
- các section `health`, `legal_repository`, `crawl_import`, `knowledge`,
  `users`, `support`, `models`, `runtime_metrics`, `audit_7d`;
- `recent_audit` chỉ gồm action, resource type, role và thời điểm;
- các trường tương thích `legal_cases` và `metrics` cho client cũ.

Mỗi section có `status`, `observed_at` và `reason_code` khi không đọc được.
Các nguồn được đọc song song với timeout riêng. Lỗi một nguồn không biến thành
số 0 và không làm mất các section còn hoạt động. Snapshot được cache trong
tiến trình 30 giây; UI tự làm mới 60 giây một lần khi tab đang hiển thị.

Audit bảy ngày được nhóm theo `Asia/Ho_Chi_Minh` từ bảng audit hiện có. Không có
schema, migration, worker hoặc kho time-series mới. Runtime telemetry vẫn là
cửa sổ trong bộ nhớ và được đặt lại khi API khởi động lại.

## Quy tắc an toàn

- Role lấy từ session đã xác thực; header giả không cấp quyền Admin.
- Response không chứa secret, token, cookie, API key, câu hỏi/câu trả lời,
  nội dung ticket, tên tệp hoặc stack trace.
- Cảnh báo và severity được tính ở backend để mọi client dùng cùng một logic.
- Admin chỉ thấy số lượng support/SLA; không tham gia hoặc đọc chat từ Dashboard.
- Không sửa corpus, trạng thái hiệu lực, candidate, vector hoặc cấu hình khi đọc
  Dashboard.

## Điều hướng và rollback

Capability registry, route guard, trang gốc và luồng đăng nhập cùng sử dụng
`ADMIN_LANDING_PATH=/admin`. Alias `/admin-dashboard` chỉ chuyển hướng về
`/admin` để giữ bookmark cũ.

Rollback giao diện bằng cách đưa `ADMIN_LANDING_PATH` về `/legal-import` và bỏ
mục `/admin` khỏi capability registry. Endpoint tổng hợp có thể giữ lại vì chỉ
đọc và tương thích ngược; không cần rollback dữ liệu.

## Kiểm chứng

- Backend kiểm tra quyền, timeout từng section, không rò rỉ secret, quy tắc cảnh
  báo, ranh giới ngày Hải Phòng và cache/force refresh.
- Frontend kiểm tra route theo role, cảnh báo có bộ lọc, trạng thái unavailable
  không thành số 0 và làm mới thủ công.
- Production build và kiểm thử viewport 390×844 phải không có tràn ngang.

