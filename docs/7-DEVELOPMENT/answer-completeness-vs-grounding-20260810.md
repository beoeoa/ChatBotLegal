# Tách căn cứ hợp lệ khỏi mức độ trả lời đầy đủ

Ngày áp dụng thiết kế: 2026-08-10.

## Quyết định

`grounding_status` và `answer_completeness.status` là hai kết quả độc lập:

- `grounding_status` trả lời câu hỏi: các nhận định pháp lý hiển thị có được
  nguồn hiện hành hỗ trợ hay không;
- `answer_completeness.status` trả lời câu hỏi: nội dung cuối cùng có thực hiện
  đủ yêu cầu người dùng hay không.

Giao diện không được suy ra “Trả lời đầy đủ” từ `fully_grounded`. Nhãn
“Trả lời đầy đủ” chỉ xuất hiện khi `answer_completeness.status=complete`.

## Completeness gate

Gate chạy xác định, sau claim validation và không gọi thêm mô hình. Tùy nội
dung câu hỏi, gate kiểm tra:

1. Độ bao phủ tối thiểu 90% các đơn vị cấu trúc của điều khoản hoặc coverage
   matrix của lượt hỏi.
2. Thứ tự trình bày nếu người dùng yêu cầu “lần lượt”, “theo thứ tự” hoặc
   “có hệ thống”.
3. Dấu hiệu diễn giải dễ hiểu nếu người dùng yêu cầu ngôn ngữ cho người dân.
4. Phần ý nghĩa/vận dụng thực tế nếu câu hỏi yêu cầu nội dung này.

Với câu hỏi chỉ rõ số văn bản và điều, gate ưu tiên parent context không bị
cắt. Nếu không chứng minh được toàn bộ cấu trúc nguồn, câu trả lời không được
đánh dấu đầy đủ dù một đoạn trích có thể vẫn `fully_grounded`.

Public projection chỉ chứa trạng thái, tỷ lệ, số lượng, các boolean kiểm tra và
reason code. Nội dung nguồn, chunk ID, evidence ID và provenance nội bộ không
được đưa vào projection này.

## Hành vi tương thích

Phản hồi cũ không có `answer_completeness` được xem là `not_assessed` ở giao
diện và không bao giờ được gắn nhãn “Trả lời đầy đủ”. Không có schema migration
trong lát cắt này; lịch sử cũ được giữ nguyên và phản hồi trực tiếp mới mang
projection completeness.

## Release gate

- Một đoạn trích có nguồn nhưng thiếu khoản/điểm phải đồng thời hiển thị
  “Căn cứ hợp lệ” và “Trả lời chưa đầy đủ”.
- Thiếu thứ tự, diễn giải dễ hiểu hoặc ý nghĩa thực tế đã được yêu cầu đều làm
  completeness gate thất bại.
- Bộ đánh giá năm lĩnh vực phải yêu cầu cả `fully_grounded` và
  `answer_completeness.status=complete`.

