# Ràng buộc nguồn thủ tục theo facet và fallback local (2026-08-09)

## Vấn đề

Structured pipeline có thể truy xuất đúng văn bản nhưng vẫn gán sai đoạn cho
thẻ trả lời. Ví dụ, thành phần hồ sơ khai sinh từng xuất hiện trong thẻ thẩm
quyền; khiếu nại lần đầu từng dùng Điều 17/18 theo từ khóa thay vì Điều 7, 8 và
28 trực tiếp. Tổng thời gian retrieval và generation cũng dùng hai ngân sách
độc lập, khiến một lượt khó có thể vượt SLA 60 giây.

## Quyết định triển khai

- `api/legal_official_procedure_evidence.py` cung cấp evidence chính thức,
  issue-bound và `supported_facets` cho khai sinh, tình trạng hôn nhân, tạm trú,
  trợ cấp hưu trí xã hội, khiếu nại lần đầu và giấy phép xây dựng hiện hành.
- `api/legal_section_grounding.py` coi `supported_facets` đã duyệt là ràng buộc
  trực tiếp; regex legacy không được phủ quyết một câu luật chỉ vì câu đó không
  lặp lại nhãn giao diện như "hồ sơ" hoặc "thẩm quyền".
- `api/legal_structured_answer.py` thay claim do model chọn bằng trích xuất xác
  định cho mọi facet đã ràng buộc. Claim legacy khác facet không được trộn lại;
  các thẻ đa vấn đề không nhận thêm một facet `rule` phụ gây nhiễu.
- `api/routers/search.py` áp timeout cho core/expanded retrieval và ngân sách
  tổng 28 giây (câu thường) hoặc 58 giây (câu phức hợp). Khi retrieval/model
  chậm, pipeline dùng fallback trích xuất đã kiểm chứng thay vì trả lỗi rỗng.
- `api/legal_form_catalog.py` giữ nguyên form identity/binding nhưng chiếu gói
  chính thức hiện hành: Phụ lục II Nghị định 217/2026/NĐ-CP và Mẫu số 01 của
  Nghị định 124/2020/NĐ-CP.
- `frontend/src/components/search/StreamingResponse.tsx` mở URL nguồn chính
  thức khi citation chưa có `doc_id` nội bộ, thay vì hiển thị "Không có link
  xem".

Không có migration, không sửa corpus và không re-index. Raw source, metadata và
URL gốc vẫn được bảo toàn; adapter chỉ tạo projection phục vụ request hiện tại.

## Kết quả xác minh local

- 169 backend unit/contract/regression tests đạt cho cleaner, problem map,
  Parent–Child, relevance, coverage, claim validation, form catalog và timeout.
- 6/6 frontend tests của `StreamingResponse` đạt.
- Ma trận API 5 lĩnh vực x 2 role đạt 10/10 HTTP 200, có answer, citation và
  biểu mẫu; 9 lượt từ 5,4–14,8 giây, một lượt cold hôn nhân 48,9 giây.
- Browser đạt cho Citizen và Officer: hỏi khai sinh/khiếu nại, render đúng facet,
  tải biểu mẫu, mở nguồn Chính phủ, lịch sử và role vẫn còn sau refresh.
- Tài khoản browser test là disposable, đã logout, revoke session và deactivate
  sau kiểm thử.

## Rollback

Tắt `LEGAL_SECTION_GROUNDING_ENABLED` và khởi động lại API để quay về legacy
adapter. Local hiện bật `true`; cấu hình release/production không đổi trong lát
cắt này.
