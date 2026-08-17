# Kiểm chứng core nạp dữ liệu pháp luật ngày 09/08/2026

## Phạm vi

Kiểm chứng trực tiếp trên local runtime cho ba đường đi:

1. Admin nhập URL VBPL, kiểm tra, duyệt, import và embedding.
2. Cán bộ gửi URL/nội dung trong lĩnh vực được phân công mà không chạy crawler, sau đó Admin kiểm tra và duyệt.
3. Crawl danh sách VBPL Hải Phòng theo quyền Admin, đồng thời xác nhận cán bộ không được phép khởi chạy crawl quản trị.

Không có migration, không xóa lịch sử nguồn/corpus và không re-index toàn bộ kho.

## Lỗi tìm thấy và quyết định sửa

- Trang danh sách VBPL là trang động dùng RSC. HTML tải ban đầu có thể chỉ chứa liên kết điều hướng, nên không được xem là kết quả văn bản. Adapter hiện luôn dựng danh sách động cho hai endpoint `/van-ban/trung-uong` và `/van-ban/dia-phuong`, giữ nguyên ID văn bản chính thức.
- Chuỗi RSC đôi khi bị giải mã sai UTF-8/Latin-1. Tiêu đề, số hiệu và cơ quan được sửa mojibake theo quy tắc xác định trước khi tạo candidate.
- Phân trang VBPL có trạng thái tải tạm thời. Crawler retry tối đa ba lần với backoff; trang cuối được xác định bằng trạng thái nút phân trang hiển thị, không tin tuyệt đối vào `total` có thể thuộc payload tạm.
- Bộ làm sạch HTML có thể gặp node con đã bị tách khi node cha bị xóa. Node rời cây hiện được bỏ qua thay vì làm hỏng cả lần chuẩn hóa.
- Ngày hiệu lực viết theo câu `có hiệu lực thi hành từ/kể từ ngày DD tháng MM năm YYYY` được trích xuất xác định, không suy đoán.
- Khi JSON-LD đưa số hiệu rút gọn như `253/2025` nhưng tiêu đề/phần ký có `253/2025/QĐ-UBND`, chỉ dùng số dài hơn nếu nó mở rộng đúng tiền tố đã công bố.
- Nguồn danh sách VBPL Hải Phòng được bật `content_fetch_allowed=true`; giới hạn vận hành được trả lại 30 candidate và 10 trang mỗi lượt sau bài test giới hạn.

## Bằng chứng chạy thật

### Nhập URL bởi Admin

- Văn bản: `11/2026/NQ-HĐND`.
- Nguồn: URL chi tiết chính thức trên `vbpl.vn`.
- Chuẩn hóa: 4.299 ký tự, 5 điều, 9 chunk.
- Kết quả: document `127594`, `active`, tier `core`.
- Vector: expected 9; fast 9/9, expanded 9/9, thiếu 0.

### Cán bộ đề xuất, Admin duyệt

- Văn bản: `21/2026/NQ-HĐND`.
- Cán bộ chỉ thấy các lĩnh vực được phân công và gửi candidate ở trạng thái `pending`; màn hình cán bộ không crawl URL và văn bản chưa xuất hiện trong kho trả lời trước khi Admin duyệt.
- Admin đối chiếu URL, phạm vi Hải Phòng, ngày ban hành `2026-07-28`, ngày hiệu lực `2026-08-08`, sau đó duyệt.
- Kết quả: document `127595`, `active`, tier `core`, 5 điều, 21 chunk.
- Vector: expected 21; fast 21/21, expanded 21/21, thiếu 0.

### Crawl và phân quyền

- Officer gọi crawl quản trị: HTTP 403, đúng policy.
- Hai endpoint crawl từng dành cho cán bộ (`/api/legal/proposals/preview` và `/api/legal/proposals/weekly-monitor`) đã được gỡ; crawler dùng chung chỉ còn ở namespace Admin.
- Admin crawl nguồn VBPL Hải Phòng: `completed`, 10 trang, 94 record phát hiện, 10 candidate mới, 26 duplicate, 58 ngoài domain, không có `failure_reason`; cursor lưu sang trang 11.
- Một lượt giới hạn tiếp theo với lấy toàn văn bật: `completed`, 1 trang, 9 record, tạo 1 candidate có 47.945 ký tự.
- Candidate `253/2025/QĐ-UBND` đã tồn tại trong runtime; bước duyệt chặn đúng bằng `duplicate_conflict`, không tạo bản sao.
- Các candidate metadata-only được tạo trong các lượt trước khi bật lấy toàn văn vẫn ở hàng chờ và không được tự duyệt. Đây là hành vi fail-closed; Admin phải bổ sung toàn văn/bằng chứng hoặc bỏ qua.

## Kiểm thử hồi quy

Nhóm kiểm thử import/crawl/phân quyền liên quan: 63 passed. Không có lỗi test; còn một cảnh báo deprecation từ dependency `surreal_commands`/Pydantic.

Các invariant đã xác nhận:

- candidate pending không đi vào RAG;
- chỉ Admin có quyền crawl và quyết định nhập kho;
- duplicate runtime bị chặn trước khi enqueue import;
- import worker hoạt động bất đồng bộ, hoàn thành ở lần thử đầu;
- document active có đủ vector ở cả fast và expanded;
- truy vấn exact metadata trả lại đúng document mới và Điều 3 của `11/2026/NQ-HĐND` chứa ngữ cảnh cha đầy đủ `K = 3` và `K = 0,8`.

## Lần kiểm chứng bổ sung: 53/2026/QĐ-UBND

- Chọn đúng một văn bản chưa có trong PostgreSQL runtime: `53/2026/QĐ-UBND`, nguồn chi tiết chính thức trên `vbpl.vn`.
- Candidate metadata-only do crawler tạo trước đó được bổ sung tại chỗ bằng 4.363 ký tự toàn văn và metadata đã đối chiếu. Không tạo candidate hợp lệ thứ hai; bản tạm phát sinh trong lúc sửa lỗi được đóng ở trạng thái `rejected` và không có import job/document/vector.
- Admin duyệt trực tiếp trên `/legal-import`; job `legal_import_job:r6jwakn155gqkcc8lw9z` hoàn tất ở lần thử đầu, không có `error_reason`.
- Kết quả runtime: document `127596`, trạng thái `active`, mô hình `VNLegal-LAL`, 3 điều, 6 chunk.
- Đối chiếu vector theo đúng sáu ID chunk: fast 6/6, expanded 6/6, thiếu 0. Truy vấn thật theo số hiệu trả đúng document `127596` qua nhánh `exact_metadata`.
- Cán bộ đăng nhập trực tiếp, chỉ thấy các lĩnh vực được phân công, gửi cùng URL/số hiệu trong lĩnh vực `dat_dai_xay_dung`; candidate vào `pending` và xuất hiện trong lịch sử cá nhân.
- Admin duyệt đề xuất cán bộ: runtime duplicate gate chuyển candidate sang `changes_requested`/`duplicate_conflict`, không tạo import job và không sinh bản sao. Admin sau đó chọn `Từ chối`; candidate được giữ audit ở `rejected`, vẫn không có import job.
- Admin preview URL VBPL thành công với đúng số hiệu, tiêu đề và 4.363 ký tự. Crawler nền sau lần khởi động thường có run `completed`; tám nguồn chính thức đang enabled và có `last_status=completed`.
- Officer bị chặn HTTP 403 ở `/api/legal/crawl/preview` và `/api/legal/crawl/scan`; hai endpoint crawl cũ của Officer trả HTTP 404. Đây là hành vi chủ đích: crawler dùng chung chỉ thuộc Admin, Officer chỉ đề xuất thủ công.
- Readiness cuối: database ready, legal retrieval ready với CUDA, import worker ready và heartbeat hợp lệ.

Kiểm thử bổ sung: backend 58/58 passed; frontend 14/14 passed; `npx tsc --noEmit` passed.

### Ghi chú chất lượng dữ liệu của bản kiểm chứng

Lệnh bổ sung metadata dùng trong lần kiểm chứng đã gửi `field_id=8` trong khi chuỗi `sector` mô tả lĩnh vực phát triển công nghiệp và thương mại địa phương. Pipeline đã giữ nguyên dữ liệu đầu vào, nên document `127596` hiện mang nhãn field `Đất đai - Xây dựng - Đô thị` dù nội dung thuộc khuyến công. Sai lệch này không làm sai bằng chứng về hàng đợi, embedding, vector hay chống trùng, nhưng record kiểm chứng chưa nên được dùng làm mẫu phân loại lĩnh vực. Theo gate Feature 014, không sửa trực tiếp metadata của corpus trong lát cắt này; việc hiệu chỉnh cần đi qua một tác vụ lifecycle được phê duyệt riêng.
