# Role-aware legal answer and chat UX hardening (2026-08-09)

## Mục tiêu

Làm cho câu trả lời pháp luật trên `/search` rõ ràng theo từng vấn đề, có căn cứ gần mệnh đề, phù hợp với vai trò người dân/cán bộ và vẫn an toàn khi model không trả được JSON có cấu trúc. Thay đổi này không sửa schema, không ghi lại corpus và không tái lập chỉ mục.

## Quyết định kiến trúc

- API tiếp tục dùng structured grounding làm đường chuẩn; role chỉ lấy từ phiên đã xác thực.
- `AnswerSection` mang thêm metadata trình bày công khai: `facet`, `priority`, `claim_types`. Các trường này được kiểm tra allowlist trước khi phát qua SSE hoặc lưu lịch sử.
- Citizen và Officer dùng cùng evidence/verified claims. Khác biệt chỉ nằm ở nhãn, thứ tự section và cách trình bày.
- Khi model lỗi hoặc JSON không hợp lệ, fallback chỉ trích xuất câu hoàn chỉnh từ evidence đã duyệt; không cắt giữa câu, không tự thêm Điều, thời hạn, phí hay kết quả pháp lý.
- Nguồn chỉ được dùng cho đúng issue. Các nhánh đặc biệt như người chuyển quyền đã chết, quy hoạch, tranh chấp/không hợp tác và giấy viết tay phải có nội dung hỗ trợ trực tiếp.
- Bộ lọc nội dung chỉ tác động relevance. Nó không được thay đổi `validity_status`, `authority_rank`, scope hoặc làm văn bản địa phương vượt văn bản trung ương.
- Cổng hiệu lực còn kiểm tra trực tiếp `effective_from` và `effective_to/expired_date` theo ngày trả lời để chặn metadata trạng thái bị cũ.

## Các cổng relevance bổ sung

Các rule là issue-aware, không phải blacklist toàn cục:

- Issue giấy viết tay phải có nội dung về nhận/chuyển quyền, chuyển nhượng, hợp đồng hoặc thủ tục chuyển quyền chưa hoàn tất.
- Issue hồ sơ cấp Giấy chứng nhận lần đầu phải có dấu hiệu trực tiếp về hồ sơ người nộp, đơn đăng ký, nơi nộp/cơ quan tiếp nhận hoặc chứng cứ chữ ký/chuyển quyền.
- Loại hướng dẫn phát/in tờ khai và luân chuyển giấy tờ nội bộ sang cơ quan thuế khỏi evidence hồ sơ của người dân.
- Loại trường hợp tăng diện tích, hạn mức nhận chuyển quyền đất nông nghiệp hoặc phân bổ đất của Nhà nước khi câu hỏi không thuộc các trường hợp đó.
- Nhánh người chuyển quyền đã chết, không hợp tác/tranh chấp và quy hoạch phải có từ khóa pháp lý trực tiếp của đúng nhánh; nguồn cùng lĩnh vực nhưng chỉ liên quan gián tiếp không đủ.

Nếu không còn evidence trực tiếp, section được giữ lại với trạng thái `insufficiently_evidenced` hoặc `requires_user_fact`; hệ thống không thay bằng một nguồn gần nghĩa nhưng sai trường hợp.

## UI/UX

- Một khung câu trả lời pháp lý thống nhất thay cho nhiều card rời.
- Header phân biệt “Hướng dẫn dành cho người dân” và “Hướng dẫn nghiệp vụ cán bộ”.
- Mỗi section hiển thị rõ: đã xác minh, kết luận có điều kiện, cần bổ sung thông tin hoặc chưa đủ nguồn.
- Citation nằm ngay dưới nội dung được hỗ trợ; danh sách citation legacy trùng lặp bị ẩn khi đã có structured sections.
- Nội dung evidence giống hệt giữa hai section chỉ hiển thị một lần và section sau trỏ về kết luận phía trên.
- Trạng thái pending/error/cancelled được trình bày gọn, không lồng card thừa.
- Trên màn hình nhỏ, sidebar ứng dụng và lịch sử chat chuyển thành drawer; khung hội thoại dùng toàn bộ chiều rộng còn lại và không tạo horizontal overflow.

## Kiểm chứng thực tế local

- Role Officer, câu đăng ký khai sinh: hồ sơ, UBND cấp xã và thời hạn được hiển thị theo nguồn Cổng Dịch vụ công; lệ phí thiếu nguồn được ghi rõ, không đoán.
- Role Citizen, câu mua đất giấy tay năm 2009: Điều 42 Nghị định 101/2024/NĐ-CP được giữ cho nhánh chuyển quyền chưa hoàn tất; nguồn về hạn mức nông nghiệp, tăng diện tích, phát hành Giấy chứng nhận và luân chuyển tờ khai thuế nội bộ bị loại.
- Các nhánh quy hoạch, người chuyển quyền đã chết, thời hạn, tài chính và hồ sơ không đủ evidence trực tiếp được ghi rõ là chưa đủ nguồn/cần xác minh.
- Refresh `/search` giữ phiên, role và khôi phục câu trả lời từ lịch sử.
- Viewport 390 x 844 có `scrollWidth == innerWidth`; menu ứng dụng và lịch sử chat mở theo drawer.

## Rollback

- Tắt `LEGAL_SECTION_GROUNDING_ENABLED` và khởi động lại API để quay về luồng legacy.
- Không cần rollback dữ liệu hoặc corpus vì thay đổi chỉ nằm ở orchestration, validation và presentation.

