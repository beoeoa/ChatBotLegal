# Kế hoạch kiểm tra sâu role Cán bộ và Admin

Ngày lập: 2026-08-07
Trạng thái: Đã lập kế hoạch, chưa thay đổi chức năng hoặc dữ liệu vận hành

## Mục tiêu

Kiểm kê toàn bộ chức năng hiện có của Cán bộ và Admin, chứng minh quyền ở phía máy chủ, kiểm tra hành trình thực tế và phân loại từng năng lực thành: giữ nguyên, cần tinh chỉnh, cần hợp nhất, cần ẩn khỏi vận hành thường, loại bỏ có kiểm soát hoặc cần bổ sung.

Kế hoạch chi tiết và contract:

- `specs/008-audit-officer-admin/spec.md`
- `specs/008-audit-officer-admin/plan.md`
- `specs/008-audit-officer-admin/research.md`
- `specs/008-audit-officer-admin/data-model.md`
- `specs/008-audit-officer-admin/contracts/role-capability-audit.md`
- `specs/008-audit-officer-admin/quickstart.md`

## Phạm vi kiểm tra

### Cán bộ

- Hỏi đáp AI, tra cứu văn bản và phạm vi lĩnh vực.
- Source/notebook theo chủ sở hữu.
- Đề xuất văn bản từ URL/file và theo dõi trạng thái.
- Live support: hàng đợi lĩnh vực, tiếp nhận, chuyển, đóng, tệp đính kèm và AI Copilot.
- FAQ/thủ tục và mọi hành trình hồ sơ/công việc thực sự có trong hệ thống.

### Admin

- Tài khoản, role, domain/ward scope và audit.
- Nguồn crawl, scan, candidate, duyệt, nhập kho, embedding, activation và tra cứu lại.
- Biểu mẫu và canonical legal attestation.
- FAQ, legal quality, model/credential, settings, source/notebook.
- Trung tâm quản trị, retention, health, queue và giám sát vận hành.
- Advanced, Transformations và Podcasts để phân loại vận hành/experimental/legacy.

## Trình tự

1. Chốt baseline chỉ đọc và môi trường test cách ly.
2. Lập capability inventory từ UI, URL, API, worker, storage và audit event.
3. Chạy ma trận quyền Citizen/Officer/Admin, gồm sai role, thiếu phiên, header giả, cross-account, cross-domain/ward và replay.
4. Kiểm thử hành trình Cán bộ end-to-end.
5. Kiểm thử hành trình Admin, ưu tiên duyệt → import → embedding → active → tra cứu.
6. Đối chiếu Sidebar, Command Palette, route guard, badge, filter và trạng thái sau reload.
7. Phân loại tính năng và tạo backlog P0-P3 có nghiệm thu/rollback.

## Phát hiện sơ bộ

1. Sidebar và route guard phân role tương đối rõ, nhưng Command Palette đang hiển thị một số lối đi và hành động tạo không đúng role. Cần dùng chung một registry năng lực theo role.
2. `/admin-control` có phần trùng với `/users`, `/legal-import` và `/legal-quality`. Hướng ưu tiên là biến nó thành overview/triage và deep-link tới điểm ghi chuẩn; chưa xóa khi chưa có telemetry và xác nhận owner.
3. Podcasts, Transformations và Advanced chưa đủ bằng chứng để kết luận không cần. Cần gắn nhãn operational/advanced/experimental/recovery/legacy; ẩn khỏi menu chính trước khi cân nhắc xóa.
4. Dashboard root có thể tạo một vòng redirect thừa cho Citizen; landing route nên tính trực tiếp theo role.
5. Admin không tham gia chat live support là hợp lý; phần giám sát phải đi qua metadata/SLA và xem nội dung có lý do + audit.

## Baseline kỹ thuật

- 22 trang dashboard.
- 219 OpenAPI path, 276 thao tác tại thời điểm kiểm tra.
- 29 test backend trọng yếu về auth/isolation/Admin/Officer/live support đạt.
- 10 test frontend route guard đạt.

Kết quả này mới chứng minh nền hiện có, chưa chứng minh mọi route/action đã được phủ quyền hoặc mọi hành trình hoạt động end-to-end.

## Nguyên tắc quyết định xóa

Không xóa chỉ vì ít dùng. Muốn phân loại `REMOVE_CONTROLLED` phải có:

- owner/telemetry hoặc bằng chứng thay thế;
- quét phụ thuộc mã, dữ liệu, job và runbook;
- phương án chuyển đổi, deprecation window và rollback;
- xác nhận đây không phải năng lực audit, khôi phục hoặc nghĩa vụ pháp lý.

Thiếu một điều kiện thì dùng `HIDE`, `REFINE` hoặc `MERGE`.

## Kết quả bàn giao sau audit

- capability matrix;
- permission matrix và negative tests;
- sơ đồ trạng thái các hành trình chính;
- findings P0-P3 có bằng chứng;
- danh sách giữ/sửa/hợp nhất/ẩn/xóa/bổ sung;
- backlog triển khai theo lát cắt có rollout và rollback;
- evidence index và checksum.

Trong giai đoạn audit không duyệt candidate, không nhập corpus, không sửa role, không xóa dữ liệu và không đổi cấu hình vận hành.
