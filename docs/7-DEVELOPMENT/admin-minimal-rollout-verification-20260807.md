# Xác minh triển khai bộ khung Admin tối giản — 2026-08-07

## Phạm vi đã hoàn tất

- Baseline ngày 2026-08-07 dùng một capability registry; Admin vào `/legal-import`, còn các
  trang Admin không được hiển thị hoặc truy cập bởi Citizen/Officer.
- Trang Tổng quan quản trị đã được gỡ; các thao tác Admin nằm ở Legal Import,
  Sources, Users và các trang chuyên môn.
- Podcast, Transformation và route `/advanced` đã rời khỏi runtime và OpenAPI.
  Rebuild embedding nằm trong Legal Import; thay đổi model embedding cũng dẫn
  về Legal Import.
- Insight cũ là dữ liệu chỉ đọc; không còn API tạo hoặc xóa insight qua
  Transformation.
- API quản trị nền vẫn có health, effectivity chỉ đọc/cảnh báo, retention
  metadata, lịch sử cấu hình và audit export CSV/XLSX/PDF.

## Kiểm chứng đã chạy

| Kiểm tra | Kết quả |
| --- | --- |
| Backend compile | Passed |
| `tests/test_admin_control_center.py` | 4 passed |
| Route guard frontend | 12 passed |
| ESLint frontend | Passed |
| Next production build | Passed |
| OpenAPI retired endpoints | Không còn Podcast, Transformation, Advanced hoặc candidate review trùng |
| OpenAPI insight | Chỉ `GET /sources/{source_id}/insights` và `GET /insights/{insight_id}` |
| Dịch vụ cục bộ | Frontend `200`; `/ready/import` `200`, retrieval CUDA và import worker ready |

## Ranh giới an toàn

Không có corpus pháp luật, biểu mẫu runtime, candidate hoặc source chính thức
nào bị sửa, nhập hoặc phê duyệt trong đợt triển khai này. Kiểm tra hiệu lực chỉ
tạo báo cáo/cảnh báo; quyết định pháp lý vẫn do Admin thực hiện từ nguồn chính
thức.

> Cập nhật 2026-08-11: `/admin` trở thành Dashboard triage chỉ đọc và điểm vào
> mới của Admin. Các thao tác ghi vẫn giữ tại trang chuyên môn; xem
> `admin-operations-dashboard-20260811.md`.
