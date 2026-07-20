# Hướng dẫn cán bộ — Pilot Phường Lê Chân

## Tài khoản và phạm vi

- Đăng nhập bằng username/mật khẩu khởi tạo được admin cấp qua kênh an toàn, sau đó **đổi mật khẩu ngay lần đầu**.
- Chỉ nhận và xử lý câu hỏi/support/candidate thuộc `allowed_domains` của chính mình. Không dùng tài khoản chung và không chia sẻ session.
- Năm desk pilot: Hộ tịch–Chứng thực; Đất đai–Xây dựng; An sinh–Y tế–Giáo dục; Hành chính công; Trật tự đô thị.

## Trả lời hỗ trợ

1. Kiểm tra lĩnh vực trước. Câu hỏi ngoài domain: không kết luận; chuyển đúng desk và nêu lý do ngắn.
2. Chỉ dùng thông tin/citation đang được hệ thống truy xuất, còn hiệu lực và phù hợp tình huống. Không tự thêm số hiệu, điều/khoản, thời hạn, lệ phí hoặc biểu mẫu.
3. Tách rõ điều đã xác minh, điều cần người dân/cơ quan xác minh và nơi liên hệ/nộp nếu có nguồn.
4. Nếu nguồn yếu hoặc thiếu: nói rõ giới hạn, đề nghị bổ sung dữ kiện hoặc chuyển hỗ trợ; không suy đoán.
5. Không xem RAG trace hoặc dữ liệu nhạy cảm ngoài quyền được giao; không copy/đăng tệp người dân ra ngoài nhiệm vụ.

## Live support

- Vào queue của domain mình, nhận ticket phù hợp, trao đổi trên phiên và ghi resolution note khi đóng.
- Không tự nhận ticket ngoài domain. Chuyển ticket bằng lý do nghiệp vụ và chọn đúng lĩnh vực đích.
- Khi có dấu hiệu nhạy cảm/tranh chấp phức tạp, ghi nhận vấn đề và báo admin theo quy trình nội bộ; không yêu cầu OTP/mật khẩu/thông tin ngân hàng.

## Đề xuất văn bản và biểu mẫu

- Cán bộ có thể submit candidate bằng `/api/legal/proposals` hoặc UI tương ứng, kèm URL nguồn chính thức, lý do phù hợp domain và metadata mình biết chắc.
- Nút **Quét URL** dùng endpoint riêng `/api/legal/proposals/preview`. Endpoint này chỉ chấp nhận HTTPS từ `vbpl.vn` hoặc website cơ quan nhà nước `.gov.vn`, chỉ trả nội dung xem trước và không nhập dữ liệu vào kho.
- Các endpoint `/api/legal/crawl/*` vẫn dành riêng cho admin. Cán bộ không được chạy crawler hệ thống, duyệt candidate hoặc tự kích hoạt embedding.
- Candidate chỉ là đề xuất. Không được hứa với người dân rằng văn bản/form đã được dùng cho trả lời trước khi admin duyệt.
- Form chỉ đề xuất khi có file/link thật hoặc nêu rõ thiếu file. Không đánh dấu synthetic/reference là official.

## Quét tự động hàng tuần

- Scheduler backend kiểm tra nguồn đến hạn mỗi giờ; từng nguồn chính thức có chu kỳ mặc định `10080` phút (7 ngày).
- Nguồn mặc định gồm VBPL Trung ương, VBPL Hải Phòng, Cổng Dịch vụ công Quốc gia, Cổng Hải Phòng (văn bản và thủ tục hành chính) và Sở Tư pháp Hải Phòng.
- Trang `/officer-proposals` gọi `/api/legal/proposals/weekly-monitor` để chỉ hiện nguồn phù hợp và candidate khớp `allowed_domains` của tài khoản cán bộ.
- Cán bộ chỉ theo dõi và gửi đề xuất bổ sung. Candidate do lịch quét tạo vẫn ở trạng thái chờ; admin duyệt cuối trước khi import, embedding và dùng cho RAG.
- Lỗi robots.txt, timeout hoặc lỗi nguồn phải được hiển thị theo từng nguồn; không coi một lần quét lỗi là thành công.
