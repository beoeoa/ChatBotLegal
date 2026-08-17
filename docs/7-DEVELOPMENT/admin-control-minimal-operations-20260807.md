# Vận hành Admin tối giản

## Phạm vi

Hệ thống cấp phường/xã dùng một role `Admin`. Chức năng Tổng quan quản trị và
route `/admin-control` đã được gỡ theo quyết định vận hành ngày 2026-08-07.
Quyết định ngày 2026-08-11 bổ sung lại một Dashboard **chỉ đọc** tại `/admin`
làm điểm vào và nơi triage. Dashboard mới không khôi phục Control Center cũ:
mọi thao tác thay đổi dữ liệu vẫn thực hiện tại trang nghiệp vụ tương ứng. Xem
`admin-operations-dashboard-20260811.md`.

`/legal-import` là nơi duy nhất chứa thao tác xây dựng lại embedding. Việc
duyệt văn bản hoặc biểu mẫu không đồng nghĩa dữ liệu đã sẵn sàng tra cứu: chỉ
khi job chuẩn hóa và embedding hoàn tất thành công mới được sử dụng ở retrieval.

## Tự động hóa chỉ đọc

`legal_effectivity_scheduler_loop` chạy trong tiến trình API, kiểm tra tối đa
mỗi 24 giờ. Nó chỉ đọc metadata của văn bản/biểu mẫu và tạo báo cáo các trường
hợp hết hiệu lực, sắp hết hiệu lực trong 30 ngày, thiếu ngày hiệu lực hoặc có
dấu hiệu bị thay thế. Không job nào tự đổi trạng thái, ngày hiệu lực, dữ liệu
runtime hoặc tự thu hồi nguồn. Admin phải mở nguồn chính thức và quyết định.

Health cũng chỉ tổng hợp trạng thái API, retrieval, import worker, embedding,
crawler, database và effectivity monitor. Không trả URL nội bộ, secret hoặc
stack trace cho giao diện.

## Audit và lịch sử cấu hình

Audit export sinh trực tiếp trong bộ nhớ dưới dạng CSV, XLSX hoặc PDF, giới hạn
5.000 dòng, có header `X-Content-SHA256` và `X-Record-Count`; tệp không được
lưu lại trên máy chủ. Nội dung export chỉ gồm metadata nghiệp vụ, không chứa
token, cookie, mật khẩu, API key hay hồ sơ nhạy cảm.

Lịch sử cấu hình giữ 10 revision gần nhất cho settings chung, model defaults
và từng thay đổi nguồn crawl. Giá trị chứa secret bị loại trước khi ghi. Khôi
phục luôn tạo revision/audit mới. Khôi phục một nguồn crawl chỉ dùng các trường
được service nguồn cho phép; nguồn mặc định và nguồn nội bộ vẫn giữ các hàng
rào provenance của chúng.

## Gỡ chức năng cũ an toàn

Podcast, Transformation và route `/advanced` không còn được publish trong
menu, command palette, route guard hoặc OpenAPI. Insight đã tồn tại vẫn đọc/
xóa theo quyền cũ nhưng không thể tạo insight mới bằng Transformation.

Migration decommission chỉ xóa dữ liệu cũ khi toàn bộ baseline khớp chính xác:
6 Transformation mặc định, 3 Episode Profile mặc định, 3 Speaker Profile mặc
định, không có Episode, không có podcast config và không có media podcast.
Nếu bất kỳ dữ liệu nào khác baseline, migration dừng trước khi xóa; không đụng
vào corpus pháp luật, source, notebook hay insight.

### Clarification after the admin-minimal rollout

This paragraph supersedes the older reference to deleting existing insights:
existing Source Insights are read-only. The application no longer creates,
updates, or deletes them through a Transformation workflow.

## Legal quality administration retirement

The admin-facing Legal Quality page and its published dashboard APIs were
retired on 2026-08-07. The application still keeps its internal grounding,
citation validation, answer-quality safeguards, operational telemetry, and
offline quality tests. No legal corpus, assessment history, or safety rule was
deleted by this UI/API retirement.
