# Trợ lý Pháp luật Phường Xã Hải Phòng

Phiên bản tùy biến từ Open Notebook, dùng để nạp văn bản chính thống, tìm kiếm
ngữ nghĩa và hỏi đáp có trích dẫn về pháp luật, thủ tục hành chính và thẩm
quyền cấp phường/xã tại Hải Phòng.

## Chạy bằng Docker

1. Sao chép `.env.example` thành `.env`.
2. Đổi `OPEN_NOTEBOOK_ENCRYPTION_KEY` và `OPEN_NOTEBOOK_PASSWORD`.
3. Chạy:

```bash
docker compose up -d --build
```

4. Mở `http://localhost:8502`.
5. Vào **Mô hình AI**, thêm khóa API và đăng ký:
   - một mô hình chat;
   - một mô hình embedding hỗ trợ tiếng Việt.
6. Tạo hồ sơ pháp lý và nạp văn bản theo [LEGAL_DATA_GUIDE.md](LEGAL_DATA_GUIDE.md).
7. Dùng [OFFICIAL_SOURCES.md](OFFICIAL_SOURCES.md) làm danh mục nguồn ban đầu.

API ở `http://localhost:5055`, tài liệu API ở `http://localhost:5055/docs`.

## Bộ câu hỏi nghiệm thu tối thiểu

- Thủ tục này thuộc thẩm quyền UBND phường/xã hay cơ quan cấp trên?
- Hồ sơ gồm những gì, nộp ở đâu, thời hạn và lệ phí bao nhiêu?
- Quy định nào của Trung ương và quy định nào riêng của Hải Phòng?
- Văn bản được trích dẫn còn hiệu lực tại thời điểm hỏi không?
- Nếu thiếu căn cứ trong kho dữ liệu, chatbot có từ chối đoán hay không?

Hệ thống hỗ trợ tra cứu, không thay thế quyết định của cơ quan có thẩm quyền
hoặc tư vấn của luật sư.
