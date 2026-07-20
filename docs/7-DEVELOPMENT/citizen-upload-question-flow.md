# Bước 10 - Upload file/hình ảnh trong luồng hỏi người dân

## Quyết định triển khai

Luồng upload được tách thành 2 bước để không phá pipeline hỏi đáp/RAG hiện có:

1. Frontend gửi file lên `POST /api/media/extract-text`.
2. Backend trích xuất text/context rồi trả về cho frontend.
3. Frontend chèn context đã trích xuất vào ô hỏi và gọi `/api/search/ask/simple` như hiện tại.

Cách này giúp câu hỏi kèm file vẫn đi qua toàn bộ kiểm soát hiện có: role, domain, retrieval, grounding, citation verifier và audit/ask-history.

## Phạm vi hỗ trợ

Cho phép:

- `.txt` / `text/plain`: đọc local bằng UTF-8; không đoán encoding legacy để tránh lỗi tiếng Việt.
- `.docx`: đọc local bằng XML trong `word/document.xml`, không cần service ngoài.
- `.pdf`: ưu tiên `pypdf` local nếu PDF có embedded text; PDF scan fallback sang Gemini OCR nếu có `GOOGLE_API_KEY` hoặc `GEMINI_API_KEY`.
- `.png`, `.jpg`, `.jpeg`: OCR bằng Gemini nếu có key.

Không hỗ trợ trong bước này:

- video;
- audio/voice file upload. Voice input sau này nên đi theo nhánh STT riêng, không nhập chung với upload tài liệu.

## Giới hạn an toàn

- Dung lượng tối đa: 10 MB/file.
- Nội dung trích xuất trả về tối đa khoảng 80.000 ký tự để tránh prompt/context quá dài.
- File chưa hỗ trợ trả `415` rõ ràng.
- File quá lớn trả `413` rõ ràng.
- OCR thiếu API key trả lỗi rõ ràng, không crash.

## Lưu ý vận hành

Ảnh và PDF scan phụ thuộc OCR bằng Gemini. Nếu không cấu hình key, hệ thống vẫn xử lý được TXT, DOCX và PDF có text local.
