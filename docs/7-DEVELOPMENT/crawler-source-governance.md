# Quản trị nguồn crawler và đánh giá tự động

## Mục đích

Màn hình **Nạp dữ liệu pháp luật** cho phép Admin nhìn thấy cả nguồn web
chính thức và các hàng đợi nội bộ. Đây là cấu hình thu thập, không phải quyền
tự động đưa dữ liệu vào kho văn bản pháp luật.

## Phân loại nguồn

| Loại | Ví dụ | Cách xử lý |
|---|---|---|
| Nguồn web chính thức | VBPL, Cổng DVC Quốc gia, cổng Hải Phòng | Có URL HTTPS, quét theo lịch hoặc do Admin quét riêng. |
| Hàng đợi nội bộ | Đề xuất văn bản của cán bộ, source-gap candidate | Hiển thị để truy vết, không có website để quét và không được ghi nhận là lỗi crawler. |

Nguồn mới chỉ được tạo khi URL dùng HTTPS và hostname thuộc
`OFFICIAL_HOST_SUFFIXES`. Hệ thống lưu nguồn mới ở trạng thái **tắt** để Admin
kiểm tra trước khi bật. Nguồn web mặc định chỉ có thể tắt. Xóa nguồn tùy chỉnh
chỉ xóa cấu hình, không xóa candidate, run, tệp nguồn hoặc audit log.

Admin có thể đổi **tên hiển thị** của hàng đợi nội bộ để phù hợp cách vận hành
thực tế. `source_type`, địa chỉ `local://` và trạng thái không-quét-web vẫn
được khóa ở API; giao diện không cho sửa các trường này. Khi Admin xóa một hàng
đợi nội bộ, hệ thống đặt nguồn ở trạng thái `deleted`, tắt nguồn và ẩn khỏi danh
sách vận hành. Bản ghi nguồn không bị xóa vật lý để các candidate, run và audit
đã có vẫn giữ nguyên tham chiếu nguồn.

Mọi tạo, sửa, tắt/bật và xóa cấu hình đều cần role Admin và ghi audit log.

## Đánh giá tự động nghiêm ngặt

Ngay khi candidate được tạo, khi trích xuất hoàn thành hoặc khi Admin sửa
metadata, hệ thống ghi lại `review_recommendation` bằng các gate quyết định:

1. URL HTTPS chính thức đã được Admin xác minh.
2. Đủ tên, số hiệu, loại văn bản, cơ quan ban hành và phạm vi.
3. Có ngày hiệu lực hợp lệ; không thuộc trường hợp chưa có hiệu lực hoặc đã hết hiệu lực.
4. Trích xuất/OCR đủ, không còn trang lỗi và có ít nhất 500 ký tự để kiểm tra.
5. Không còn ứng viên trùng số hiệu, URL hoặc fingerprint.
6. Có lĩnh vực phù hợp phạm vi phục vụ cấp phường/xã và không phải biểu mẫu/thủ tục.

Thiếu một gate sẽ tạo trạng thái khuyến nghị **tạm giữ — cần bổ sung bằng
chứng**. Biểu mẫu/thủ tục được khuyến nghị không đưa vào kho văn bản pháp luật.
Các điểm số và lý do hiển thị bằng tiếng Việt. Đánh giá LLM, nếu được gọi từ nút
**Đánh giá lại bằng AI (khi cần)**, chỉ là diễn giải bổ sung và không được ghi
đè evidence gate.

## Vòng đời candidate và nhập kho

Các trạng thái được tách riêng để tránh hiểu nhầm “Admin đã duyệt” với “đã
được phục vụ trong hệ thống”:

| Trạng thái | Ý nghĩa vận hành | Hành động trên giao diện |
|---|---|---|
| `pending` | Chờ quyết định của Admin | Duyệt, bỏ qua hoặc yêu cầu bổ sung |
| `changes_requested` | Chờ cán bộ cập nhật dữ liệu | Chỉ duyệt hoặc bỏ qua sau khi cập nhật |
| `approved` | Đã ghi nhận quyết định, chưa chắc đã nhập kho | Xem gate, đánh giá lại khi dữ liệu đổi, thử nhập kho |
| `import_queued` | Đang chờ worker chuẩn hóa/chia đoạn/embedding | Chỉ xem tiến độ; tự làm mới định kỳ |
| `import_failed` | Nhập hoặc embedding thất bại | Xem lỗi và thử nhập lại |
| `imported` | Hai collection vector đã nhận dữ liệu và document đã kích hoạt | Chỉ đọc; ẩn nút duyệt và đánh giá lại |
| `rejected` | Không đưa vào kho tra cứu | Chỉ xem lịch sử |

API chỉ cho phép quyết định mới ở `pending` và `changes_requested`. Candidate
đã xếp hàng, đã nhập, thất bại hoặc bị bỏ qua không thể bị đổi ngược bằng endpoint
review; lỗi import phải đi qua endpoint nhập lại có kiểm tra gate.

Khi Admin chọn **Duyệt và xếp hàng nhập kho**, worker nền gọi dịch vụ Legal
Search. Dịch vụ tách văn bản thành chunk, tạo vector bằng **VNLegal-LAL**, ghi
vào collection nội dung và collection nguồn, rồi chỉ kích hoạt document sau khi
cả hai lần ghi thành công. Nếu bất kỳ bước nào lỗi, candidate giữ trạng thái
không phục vụ và ghi `import_failed` để có thể xử lý lại.

## Giới hạn an toàn và khôi phục

Đánh giá tự động không được đổi trạng thái duyệt, không nhập kho, không tạo
embedding và không công bố văn bản. Chỉ Admin mới có thể duyệt và luồng import
vẫn chạy toàn bộ kiểm tra metadata/effectivity/provenance trước khi kích hoạt
retrieval.

Nếu cần dừng một nguồn, tắt nó trong màn hình quản trị. Nếu một nguồn tùy chỉnh
có vấn đề, xóa cấu hình của nó; history vẫn giữ nguyên. Nếu AI tùy chọn không
sẵn sàng, guidance theo gate cục bộ vẫn có mặt và candidate tiếp tục ở hàng
đợi chờ Admin.
