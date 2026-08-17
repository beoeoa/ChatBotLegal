# Tối giản trải nghiệm duyệt dữ liệu pháp luật (2026-08-09)

## Quyết định

Trang `/legal-import` dành cho Admin chỉ còn ba công việc hiển thị trực tiếp:

1. Đề xuất chờ duyệt.
2. Biểu mẫu chờ duyệt.
3. Thêm văn bản.

Phần hiệu lực pháp lý được chuyển sang `/legal-management/validity`, với lối vào từ
`/legal-management`, vì đây là công việc
quản lý vòng đời các văn bản đã có trong kho, không phải thao tác duyệt dữ liệu
mới.

## Duyệt văn bản

- Mỗi đề xuất hiển thị tên, số hiệu, loại, cơ quan ban hành, phạm vi, nguồn gốc
  và liên kết chính thức khi có.
- Người duyệt chỉ có hai quyết định chính: `Duyệt` và `Từ chối`.
- Đánh giá AI, điểm tin cậy, nút đánh giá lại và yêu cầu bổ sung không còn hiển
  thị trong hàng chờ.
- Nội dung trích xuất và form sửa metadata được gấp lại, chỉ mở khi người duyệt
  cần kiểm tra sâu hoặc sửa dữ liệu.
- `Duyệt` vẫn chạy các rào chắn nguồn chính thức, metadata, trùng lặp và trạng
  thái import phía máy chủ. Chỉ khi import và vector hoàn tất thì văn bản mới
  được kích hoạt cho tra cứu.
- Khi crawler đã tạo một candidate chỉ có metadata, thao tác `Thêm văn bản` bằng
  cùng số hiệu sẽ bổ sung toàn văn và metadata đã đối chiếu vào chính candidate
  đó. Hệ thống không tạo candidate thứ hai và không bỏ qua phần nội dung mới.
- Trước khi crawler tạo candidate mới theo số hiệu, hệ thống đối chiếu cả kho
  văn bản runtime. Văn bản đã tồn tại được tính là trùng và không tiếp tục làm
  đầy hàng chờ duyệt.
- `Từ chối` giữ bản ghi và audit nhưng không import, không embedding và không
  đưa vào RAG.

## Duyệt biểu mẫu

Mỗi biểu mẫu phải cho biết nguồn đề xuất bằng tên nguồn, URL trang/tệp hoặc nhãn
`Tệp được gửi vào hệ thống`. Nếu không có bằng chứng nguồn, UI hiển thị rõ
`Nguồn chưa được ghi rõ`.

- `Duyệt`: xác nhận candidate và chuyển sang bước xác nhận pháp lý của Form
  Catalog. Ở bước này biểu mẫu chưa canonical, chưa công khai và chưa được tải
  xuống trong luồng người dùng.
- `Từ chối`: đóng candidate, giữ lịch sử truy vết, không liên kết thủ tục và
  không công khai.
- Tên, lĩnh vực và mã thủ tục vẫn có thể sửa trong phần chi tiết được gấp lại.

## Phạm vi dữ liệu và rollback

Thay đổi này chỉ sửa cách trình bày và thao tác trên UI. Không có migration,
không xóa candidate/corpus/vector và không hạ các rào chắn pháp lý hiện có.
Rollback là khôi phục các component UI trước đó; dữ liệu không cần phục hồi.

## Kiểm thử bắt buộc

- Component test xác nhận hàng chờ pending chỉ có `Duyệt` và `Từ chối`, không có
  nội dung đánh giá AI.
- Component test xác nhận biểu mẫu hiển thị nguồn và giải thích hậu quả của hai
  quyết định.
- Component test xác nhận hiệu lực pháp lý có trang riêng trong khu vực quản lý kho,
  còn trang danh mục `/legal-management` tiếp tục là màn hình chỉ đọc và chức năng này
  không còn ở trang nhập dữ liệu.
- Backend regression xác nhận duyệt/từ chối, import candidate và form legal gate
  vẫn fail-closed.
- Browser smoke test với tài khoản Admin xác nhận bố cục và thao tác trực tiếp.
