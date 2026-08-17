# Cổng chất lượng trả lời 5 lĩnh vực

## Mục tiêu

Cổng này kiểm tra trực tiếp API trả lời pháp luật bằng 150 câu hỏi có căn cứ đang có hiệu lực và một ca âm chuyên chặn văn bản hết hiệu lực. Mỗi câu được gửi cho cả người dân và cán bộ, tạo thành 302 lượt trả lời độc lập.

Năm lĩnh vực:

1. Hộ tịch, chứng thực.
2. Đất đai, xây dựng.
3. Cư trú, an ninh.
4. Khiếu nại, tố cáo, xử phạt.
5. An sinh, y tế, giáo dục.

Mỗi lĩnh vực có 10 câu dễ, 10 câu trung bình và 10 câu khó. Mỗi câu gắn với một điều khoản riêng đã qua rà soát trong tập truy xuất cục bộ; API vẫn phải trả lại đúng văn bản, đúng điều, URL chính thức và trạng thái còn hiệu lực trong lần chạy live.

## Điều kiện đạt bắt buộc

Một lượt chỉ đạt khi đồng thời thỏa toàn bộ điều kiện sau:

- HTTP thành công và có câu trả lời.
- Trạng thái `fully_grounded`; mọi mục công khai đều `sufficiently_evidenced` và có trích dẫn gắn với nội dung.
- Có đúng căn cứ trực tiếp đã chọn, gồm số văn bản, điều, trạng thái hiệu lực và URL chính thức.
- Báo cáo độ đầy đủ phải chứng minh bao phủ ít nhất 90% đơn vị cấu trúc của điều luật. Trạng thái `complete` tự khai không đủ nếu số khoản/điểm thực tế được bao phủ thấp hơn ngưỡng.
- Các khoản/điểm phải đúng thứ tự và câu trả lời phải có diễn giải ý nghĩa thực tế theo yêu cầu của câu hỏi.
- Mọi nhận định còn hiển thị phải có quyết định `verified`. Nhận định bị từ chối chỉ được chấp nhận khi nội dung đó đã bị loại khỏi câu trả lời công khai.
- Không dùng trích dẫn hết hiệu lực tại ngày `legal_as_of`; mọi chế độ dự phòng hoặc chặn phải dùng nhãn máy đã được công bố.
- Không xuất hiện câu thoái lui như “không tìm thấy”, “không có trong dữ liệu”, “không tổng hợp được” hoặc yêu cầu người dùng bổ sung thông tin.
- Không lộ mã kỹ thuật nội bộ, không lặp nhãn/lặp ý trong một câu và không trùng nguyên văn với câu hỏi khác.
- Có tiêu đề Markdown, danh sách rõ ràng và cách diễn đạt phù hợp vai trò.
- Độ chi tiết tăng theo độ khó: tối thiểu 100/120 ký tự cho câu dễ, 150/170 cho câu trung bình và 280/300 cho câu khó, lần lượt với người dân/cán bộ.
- Không chấp nhận câu chỉ dẫn chiếu chung kiểu “thực hiện nghĩa vụ khác theo quy định” mà không giải thích được quy tắc.
- Hoàn thành trong ngân sách 900 giây cho mỗi lượt.

Cổng là all-or-nothing: chỉ một lượt không đạt cũng làm toàn bộ lần chạy thất bại và trả mã thoát khác 0.

## Ca hiệu lực bắt buộc từ phiên bản v2

- `96/2014/TT-BQP`, Điều 26 không còn là câu hỏi dương của lĩnh vực an sinh. Văn bản này là ca âm `expired-block:96-2014-tt-bqp:26`: câu trả lời chỉ đạt khi nói rõ văn bản đã hết hiệu lực, không dùng để trả lời hiện hành và không phục vụ nội dung điều này như căn cứ hiện hành.
- Câu hỏi dương thay thế dùng Nghị định `188/2025/NĐ-CP`, Điều 43, nguồn chính thức VBPL. Lần kiểm tra trực tiếp ngày 2026-08-10 xác nhận văn bản đang hiệu lực và gói Điều có đủ 6/6 chunk theo đúng thứ tự.
- Báo cáo và checkpoint v2 nằm mặc định dưới `reports/five-domain-quality-v2/` để không tái sử dụng nhầm kết quả của bộ chấm v1.

## Thay đổi của pipeline trả lời

- Thời gian sinh câu trả lời mặc định là 60 giây; câu khó là 120 giây. Tổng ngân sách mặc định là 90 giây; câu khó là 180 giây. Trần cấu hình lần lượt là 120, 240 và 300 giây.
- Ngân sách đầu ra mặc định tăng lên 2.048 token, có thể cấu hình từ 512 đến 4.096 token.
- Bộ lập kế hoạch câu hỏi trực tiếp chỉ yêu cầu quy tắc áp dụng; không tự dựng thêm điều kiện hoặc ngoại lệ khi câu hỏi không nêu.
- Khi nguồn có nhiều mệnh đề đã kiểm chứng, bộ dựng câu trả lời có thể giữ tối đa ba mệnh đề khác nhau của cùng điều khoản.
- Các mệnh đề cùng loại được gom dưới một nhãn duy nhất để tránh lặp và giúp người dân đọc nhanh.
- Hai vai trò dùng cùng căn cứ đã kiểm chứng nhưng tiêu đề và cách trình bày khác nhau: ngôn ngữ dễ hiểu cho người dân, ngôn ngữ nghiệp vụ cho cán bộ.

Các thay đổi này không thêm schema, không sửa/re-index kho văn bản pháp luật và không nới lỏng kiểm tra hiệu lực. Với câu hỏi tùy ý ngoài cổng, hệ thống vẫn phải ưu tiên an toàn pháp lý; cổng tránh tình trạng thiếu nguồn bằng cách chỉ tạo câu hỏi từ 150 điều khoản đã xác nhận thay vì bịa căn cứ để ép trả lời.

## Cách chạy

Tạo tài khoản kiểm thử tạm có đủ 5 phạm vi nghiệp vụ, chạy API cô lập, rồi thực hiện:

```powershell
python scripts/evaluate_five_domain_answer_quality.py `
  --base-url http://127.0.0.1:5057 `
  --credentials-file reports/five-domain-150/private-test-credentials.env `
  --timeout-seconds 900 `
  --concurrency 1
```

Chạy tuần tự vì API local dùng chung kết nối SurrealDB. Checkpoint cho phép tiếp tục khi lỗi vận chuyển, nhưng báo cáo nghiệm thu phải đến từ một lần chạy sạch từ đầu. Báo cáo chi tiết chứa toàn bộ câu hỏi/câu trả lời và được giữ trong thư mục `reports` riêng tư; báo cáo tóm tắt chỉ chứa số lượng, điểm, thời gian và nhận xét.

Sau khi kiểm thử, phải chạy `scripts/cleanup_feature005_test_accounts.py` để vô hiệu hóa đúng các tài khoản có dấu kiểm thử và xóa tệp chứa thông tin đăng nhập tạm.

## Kết quả nghiệm thu v1 ngày 2026-08-09

Lần chạy sạch cuối `final-v8` đạt:

- 150/150 câu nền và 300/300 lượt trả lời đạt; 0 lỗi.
- 30 câu và 30 điều khoản riêng cho mỗi lĩnh vực.
- 50 câu cho mỗi mức dễ, trung bình, khó.
- 150 lượt người dân và 150 lượt cán bộ.
- 0 câu hỏi trùng, 0 câu trả lời trùng nguyên văn, 0 câu chứa cụm từ thoái lui bị cấm.
- 100% câu trả lời `fully_grounded`; toàn bộ trích dẫn có trạng thái `active` và URL chính thức từ `vbpl.vn` hoặc `vanban.chinhphu.vn`.
- Độ dài tối thiểu thực tế: dễ 106/131, trung bình 157/174, khó 293/310 ký tự cho người dân/cán bộ.
- Thời gian median 3,495 giây; p95 5,99 giây; tối đa 57,277 giây.

Báo cáo tóm tắt: `reports/five-domain-150/final-v8-summary.json`.
Báo cáo chi tiết riêng tư: `reports/five-domain-150/final-v8-private.json`.

Kết quả trên là đường cơ sở lịch sử của v1, không phải kết quả nghiệm thu cho v2. V2 phải chạy đủ 151 ca/302 lượt và đạt cả ca chặn văn bản hết hiệu lực trước khi phát hành.

## Kiểm tra khói v2 ngày 2026-08-10

Lần chạy cô lập gồm ca dương Điều 43 Nghị định 188/2025/NĐ-CP và ca âm Điều 26 Thông tư 96/2014/TT-BQP, cho cả người dân và cán bộ:

- 4/4 yêu cầu trả HTTP 200; không còn lỗi phân quyền của token cũ.
- 2/2 phản hồi của ca hết hiệu lực đạt: không có trích dẫn bị cấm, nêu rõ “hết hiệu lực – không dùng để trả lời hiện hành” và có cờ `expired_source_blocked`.
- 2/2 phản hồi dương bị cổng v2 từ chối đúng: nhà cung cấp AI báo giới hạn tần suất nên hệ thống dùng `verified_source_condensed`; phản hồi chỉ bao phủ 15/17 và 16/17 đơn vị cấu trúc, sai tiêu chí bao phủ, thứ tự và diễn giải thực tế.
- Nhãn dự phòng `provider_fallback` chỉ được coi là hợp lệ khi đi kèm `answer_mode` công khai thuộc `verified_source_condensed`/`source_view_only` và mã lỗi `AI_PROVIDER_FALLBACK`.

Kết luận: bộ chấm v2 và ca chặn hết hiệu lực hoạt động đúng. Chưa được công bố đạt nghiệm thu 302/302 cho đến khi có một lần chạy sạch không bị giới hạn nhà cung cấp và mọi phản hồi dương đạt đủ độ bao phủ cấu trúc.
