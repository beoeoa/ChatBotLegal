# Golden 1.000 — kiểm tra lại sau phê duyệt với dữ liệu đang phục vụ

## Kết luận

Phê duyệt của người dùng được giữ nguyên để audit, nhưng bản Golden chưa được
dùng làm chuẩn phát hành tại mốc `2026-08-11`. Kiểm tra chéo với snapshot hiệu
lực mà chatbot đang dùng đã phát hiện 294/1.000 ca có nguồn kỳ vọng bị cổng hiệu
lực chặn. Trạng thái phát hành phải là `needs_revalidation`, không phải
`approved_frozen`.

Không có dữ liệu PostgreSQL, Chroma, vector hoặc snapshot production nào bị
thay đổi trong lát cắt này.

## Lỗi truy xuất đã sửa

Câu hỏi hiệu lực thường nêu văn bản cũ trước rồi mới nêu `Điều X` của văn bản
hiện hành. Bộ lập kế hoạch cũ truy vấn tích chéo mọi số hiệu với mọi điều, nên
có thể lấy văn bản cũ trước và bỏ lỡ cặp hiện hành.

Lát cắt mới:

1. Giữ mọi số hiệu người dùng đã nêu để audit.
2. Ghép một điều duy nhất với số hiệu gần nó nhất.
3. Dùng đúng cặp điều–văn bản cho exact lookup và gói toàn bộ điều.
4. Cho phép exact lookup dùng chunk chỉ thiếu metadata lĩnh vực khi tiêu đề đã
   làm sạch và nội dung không rỗng; truy vấn mơ hồ vẫn giữ cổng chất lượng cũ.
5. Không bỏ qua cổng hiệu lực, không phục vụ văn bản hết hiệu lực và không tự
   chuyển sang ANN khi gói điều không đầy đủ.

## Kết quả chạy thật 100 ca hiệu lực

- 100/100 ca hiện được đưa vào mẫu số; ca rỗng không còn bị bỏ qua khỏi báo cáo.
- 60/100 ca có evidence, 40/100 ca fail-closed.
- Top-1: 57%; Top-5: 58%; MRR: 0,575; Recall@10: 58%.
- 0 nguồn cấm trong Top-5 và 0 nguồn không đủ điều kiện trong Top-10.
- BGE reranker vẫn chưa được kích hoạt vì chưa có model path/config đã duyệt;
  kết quả learned hiện bằng baseline.

Các nhóm fail-closed chính:

- `exact_article_filtered_after_integrity_check`: gói điều đủ nhưng snapshot
  hiệu lực chặn ở bước cuối.
- `article_context_too_large`: toàn bộ Điều 1 của 62/2020/QH14 gồm 101 chunk,
  vượt ngân sách ngữ cảnh an toàn.
- `exact_article_not_found`: nguồn hiện hành chưa nằm trong core scope hoặc
  chưa có trong corpus.
- `incomplete_parent_coverage` / `missing_structural_units`: dữ liệu chunk hiện
  tại chưa phủ đủ thân điều gốc.

## Sai lệch hiệu lực trong Golden đã duyệt

Audit 1.050 nguồn kỳ vọng phát hiện 294 ca bị chặn, thuộc bốn số hiệu:

- 31/2024/QH15: 118 ca; trạng thái một phần nhưng thiếu phạm vi điều bị tác động.
- 55/2021/TT-BCA: 59 ca; nguồn VBPL hiện cho biết hết hiệu lực toàn bộ từ
  01/07/2026.
- 66/2023/TT-BCA: 31 ca; hết hiệu lực toàn bộ từ 01/07/2026.
- 73/2025/QH15: 86 ca; trạng thái một phần nhưng thiếu phạm vi điều bị tác động.

VBPL hiện trả về 116/2026/TT-BCA, hiệu lực từ 01/07/2026, là văn bản mới về
đăng ký và quản lý cư trú. Văn bản này chưa có trong PostgreSQL hiện tại, do đó
không thể sửa an toàn 90 ca cư trú chỉ bằng reranking.

## Việc cần phê duyệt riêng trước khi tiếp tục

1. Nhập, duyệt và embedding 116/2026/TT-BCA vào corpus thật.
2. Xác minh phạm vi sửa đổi cụ thể của 31/2024/QH15 và 73/2025/QH15; chỉ mở
   những điều không bị tác động hoặc chuyển Golden sang văn bản hợp nhất.
3. Nạp lại các điều có lỗi coverage của 88/2025/QH15.
4. Quyết định cách phục vụ điều cực dài: giữ fail-closed hoặc bổ sung cơ chế
   kiểm tra toàn điều rồi truy xuất tiểu mục có cấu trúc; không tăng mù quáng
   giới hạn context.
5. Sau các sửa dữ liệu, tái sinh 294 ca liên quan, yêu cầu duyệt lại đúng các ca
   thay đổi, rồi chạy lại 100 ca hiệu lực và bộ 1.000 ca.
