# Chế độ dự phòng khi nhà cung cấp AI lỗi

## Mục tiêu

Khi mô hình tạo câu trả lời gặp giới hạn lượt gọi, hết thời gian, circuit
breaker hoặc trả về dữ liệu không hợp lệ, hệ thống không được trình bày kết
quả như một câu trả lời đầy đủ. Nguồn pháp luật đã truy xuất vẫn được giữ
nguyên; khả năng sẵn sàng của AI không làm thay đổi trạng thái pháp lý của
nguồn.

## Trạng thái công khai

- `normal`: mô hình và các cổng kiểm tra hoàn tất bình thường.
- `verified_source_condensed`: toàn bộ phạm vi cần trả lời và mọi mệnh đề của
  bản trích xuất xác định đều đã vượt qua cổng kiểm tra. Giao diện hiển thị
  “Nguồn đã xác minh nhưng câu trả lời đang ở chế độ rút gọn”.
- `source_view_only`: không chứng minh được bản rút gọn là đầy đủ và an toàn.
  Người dùng chỉ nhận trích đoạn, liên kết nguồn gốc và hướng dẫn thử lại.

Với câu hỏi chỉ đích danh một Điều của một văn bản, chế độ rút gọn chỉ được
dùng khi có gói Điều hoàn chỉnh hoặc ngữ cảnh Điều cha đầy đủ, không bị cắt.

## Phân loại lỗi

Các lỗi sau được giữ riêng trong trace và phản hồi công khai:

- `provider_rate_limit`
- `provider_timeout`
- `provider_circuit_open`
- `invalid_output`

`validation_failed` chỉ còn là nhóm cuối cho lỗi xác thực khác không thể phân
loại an toàn. Giao diện không dùng nhãn “Căn cứ hợp lệ/đầy đủ” cho hai chế độ
dự phòng; độ đầy đủ của câu trả lời bị ép về `incomplete` dù nguồn trích dẫn đã
được xác minh.

## Lưu lịch sử và phạm vi thay đổi

`answer_mode` được lưu trong metadata đính kèm sẵn có của tin nhắn hội thoại,
không cần đổi schema cơ sở dữ liệu. Thay đổi này không sửa dữ liệu pháp luật,
không chạy backfill, không di chuyển hoặc lập chỉ mục lại vector và không thêm
dịch vụ nền.

