# Ask grounding, audit và readiness

## Mục tiêu

Luồng Ask chỉ đưa ra kết luận pháp lý khi citation có thể đối chiếu với evidence
packet retrieval đã lọc hiệu lực. Việc sửa Markdown, heading hoặc thiếu dấu kỹ
thuật không được gọi thêm mô hình. Lượt gọi bổ sung chỉ được phép khi citation
không hợp lệ, có tham chiếu pháp lý không được evidence hỗ trợ hoặc câu bị cắt.

## Hợp đồng câu trả lời và grounding

`api/legal_question_policy.py` là nguồn sự thật duy nhất về cấu trúc theo role
và loại câu hỏi. Prompt cloud, prompt local và validator nhận cùng contract.
Formatter chỉ chuẩn hóa heading, khoảng trắng và bullet; không tạo nội dung
pháp luật.

`api/legal_grounding.py` xác thực cả citation tự nhiên (số hiệu văn bản, Điều và
cặp văn bản–Điều) lẫn marker nội bộ cũ. Citation không tồn tại, cặp sai hoặc
thời hạn/phí/mức tiền không có trong evidence bị fail-closed. Marker nội bộ chỉ
còn được nhận để tương thích bản trả lời cũ và luôn được đổi thành metadata
nguồn công khai trước khi trả về người dùng.

Seed thủ tục chỉ được dùng để nhận diện nhu cầu và gắn biểu mẫu official đã
duyệt. Steps, hồ sơ, thời hạn và lệ phí seed không được đưa vào dữ liệu trả lời
Ask vì không phải evidence retrieval đã kiểm tra hiệu lực.

## Audit và migration 37

Migration 37 đổi `user_ask_history.sources` thành `option<array<any>>`. Nó không
rewrite hoặc xóa bản ghi: string JSON cũ và snapshot object mới cùng đọc được.
Rollback giữ đúng kiểu tương thích này, do đó rollback code không làm mất source
snapshot có cấu trúc.

Audit chỉ lưu tối đa 25 snapshot metadata: ID chunk/văn bản/điều, hiệu lực,
cơ quan, phạm vi, URL nguồn và score. Nội dung chunk, câu hỏi, câu trả lời,
credential và trace thô không được đưa vào telemetry. Khi audit ghi thất bại,
câu trả lời vẫn được trả về; telemetry chỉ ghi tên lớp lỗi và counter fail.

## Readiness và khởi động

`/health` là liveness tương thích. `/ready` trả 200 khi SurrealDB, retrieval và
default cloud chat model đã cấu hình sẵn sàng; trả 503 nếu một dependency bắt
buộc lỗi. Check model chỉ đọc cấu hình, không sinh nội dung và không gửi secret.
Ollama là thành phần tùy chọn được công bố trạng thái riêng.

Container API/worker chờ SurrealDB qua `scripts/wait-for-surreal.sh`; Compose
chỉ khởi động chúng khi healthcheck DB pass. Local startup chạy retrieval trước
khi chờ `/ready`. `scripts/start_local.ps1 -PreflightOnly` chỉ báo container
orphan/restart-loop hoặc cổng xung đột; không stop, remove hoặc thay đổi
container ngoài phạm vi project.

## Vận hành và rollback

- Trước rollout: chạy `python -m pytest -q tests/test_readiness.py tests/test_migration_37_registry.py tests/test_grounding_verification.py`.
- Chạy `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_local.ps1 -PreflightOnly`; xử lý container được báo thủ công rồi mới khởi động.
- Nếu migration 37 cần rollback, dùng migration down; schema vẫn nhận cả string và object, không chạy lệnh xóa dữ liệu.
- Nếu `/ready` không pass, không đưa traffic Ask vào API. `/health` không phải tín hiệu dependency đã sẵn sàng.
- `show_rag_trace` mặc định false và chỉ admin đã xác thực mới nhận được trace.

## Bản vá xác minh runtime 2026-07-16

- Readiness database dùng `RETURN 1;`, tương thích SurrealDB 2.6. Cú pháp
  kiểu SQL `RETURN 1 AS ready;` không hợp lệ và từng làm `/ready` báo
  `connection_failed` dù database vẫn khỏe.
- Type annotation của module chunking được defer để tiến trình API sạch có
  thể import khi các splitter tùy chọn chưa được nạp. Chế độ fallback cục
  bộ không thay đổi.
- Bản ghi `ward_procedure` cũ thiếu metadata duyệt biểu mẫu luôn
  fail-closed thành `reference` / `candidate_pending_review`. Chỉ catalog biểu
  mẫu official được admin duyệt mới có thể cấp file official.
- Xác minh trên API tạm thời cổng 5056: `/health` và `/ready` cùng trả
  HTTP 200; tiến trình tạm thời được dừng ngay sau kiểm tra.
