# Kế Hoạch Nâng Cấp Chatbot Pháp Luật Phường/Xã

## Mục tiêu đã chốt

Nâng hệ thống thành nền tảng phục vụ đồng thời người dân, cán bộ và admin:

- Người dân có chatbot nhiều lượt, lịch sử riêng, FAQ, biểu mẫu chính xác và có thể kết nối cán bộ.
- Cán bộ có chat theo lĩnh vực, tiếp nhận hỗ trợ trực tuyến, đề xuất văn bản/biểu mẫu và hồ sơ riêng.
- Admin quản trị toàn bộ vận hành nhưng chỉ mở nội dung chat/hồ sơ nhạy cảm khi nhập lý do nghiệp vụ; thao tác phải được audit.
- Chat lưu 12 tháng; file hồ sơ lưu 6 tháng sau khi xử lý xong; audit log lưu 24 tháng.

Năm lĩnh vực chính:

1. Hộ tịch - chứng thực
2. Đất đai - xây dựng
3. An sinh - y tế - giáo dục
4. Hành chính công
5. Trật tự đô thị

---

## Bước 1 - Khảo sát lỗi và chốt baseline

**Mục tiêu:** xác định chính xác nguyên nhân của trả lời trùng, chat mất ngữ cảnh, PDF lỗi font, chậm khi mở/tải văn bản và lỗi `403 /transformations`.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 1: khảo sát và lập baseline kỹ thuật cho các lỗi hiện tại.
1) Trace luồng Ask từ submit, streaming, final response đến StreamingResponse để tìm lý do render hai câu trả lời.
2) Trace luồng xem/tải PDF để xác định generator, font hiện dùng, thời gian phản hồi và nơi text tiếng Việt bị lỗi.
3) Trace chat/hồ sơ pháp lý để xác định data model, cách lưu message và lý do câu hỏi follow-up không có context.
4) Trace API /transformations đang trả 403: endpoint, auth guard, role và frontend caller.
5) Đo thời gian API của Ask, xem văn bản và tải PDF bằng log/smoke test.
Không sửa code ở bước này. Tạo docs/7-DEVELOPMENT/chat-platform-baseline.md gồm nguyên nhân, file liên quan, latency baseline và hướng sửa.
```

**Done khi:** có bằng chứng kỹ thuật cho từng lỗi, không chỉ phỏng đoán.

---

## Bước 2 - Thiết kế dữ liệu và migration an toàn

**Mục tiêu:** tạo nền dữ liệu cho chat nhiều lượt, hỗ trợ trực tuyến, phân quyền lĩnh vực, hồ sơ riêng, audit và retention.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 2: thêm migration/schema tối thiểu cho nền tảng chat và quản trị.
Tạo hoặc chuẩn hóa:
- conversations: owner_user_id, role_context, domain, title, status, created_at, last_message_at, expires_at.
- conversation_messages: conversation_id, sender_user_id, sender_role, content, attachments, citations_snapshot, created_at.
- support_sessions: citizen_id, officer_id, domain, queue_status, assigned_at, closed_at, resolution_note.
- legal_cases: owner_user_id, assigned_officer_id nullable, status, closed_at, expires_at; không dùng chung giữa tài khoản.
- sensitive_access_audit: actor_id, resource_type, resource_id, reason, action, created_at, request metadata.
- document_candidates: submitted_by, domain, source type, review status, OCR/AI scores, rejection reason.
- user profile: department, allowed_domains, ward_scope, must_change_password.
Không đổi hoặc xóa dữ liệu cũ. Backfill an toàn dữ liệu hiện có về owner phù hợp; tài liệu/hồ sơ không xác định chủ sở hữu phải bị đánh dấu cần admin xử lý, không tự gán.
Ghi mô tả migration và rollback trong docs/7-DEVELOPMENT.
```

**Done khi:** schema hỗ trợ đủ dữ liệu mới mà không làm lộ hồ sơ giữa tài khoản.

---

## Bước 3 - Sửa hiện hai câu trả lời và giữ giao diện đẹp

**Mục tiêu:** chỉ giữ phiên bản trả lời đẹp thứ hai của ảnh, kể cả sau nhiều câu hỏi.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 3: sửa lỗi Ask render hai lần câu trả lời.
- Xác định và loại bỏ việc render đồng thời raw streaming answer với Final Answer/StreamingResponse.
- Mỗi assistant message chỉ có một renderer ổn định: lúc stream hiển thị skeleton hoặc nội dung stream; khi hoàn tất chuyển cùng message đó sang giao diện Final Answer đẹp, không append thêm card khác.
- Lưu rendered final state theo từng conversation message, không reset khi gửi câu hỏi mới.
- Không mất citations, forms, FAQ, procedure reference hoặc rag trace theo quyền.
- Thêm UI test: gửi hai câu liên tiếp, mỗi câu có đúng một answer card đẹp và câu đầu vẫn giữ nguyên.
```

**Done khi:** không còn câu trả lời trùng, card đẹp không biến mất sau câu hỏi tiếp theo.

---

## Bước 4 - Chat nhiều lượt và bố cục mới cho ba role

**Mục tiêu:** mọi role hỏi tiếp được trong cùng cuộc hội thoại, có lịch sử như chatbot thực tế.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 4: thay Ask một-lần bằng chat nhiều lượt có conversation persistence.
- Layout desktop: sidebar lịch sử cuộc trò chuyện; vùng nội dung ở trên; composer/chat input cố định phía dưới.
- Mobile: sidebar chuyển thành drawer.
- Mỗi conversation lưu title tự sinh từ câu đầu, domain, timestamps và messages theo owner.
- Khi hỏi lại, backend nhận recent conversation context đã được giới hạn token, ưu tiên các kết luận/citation trước đó; không dùng memory của user khác.
- Cho phép tạo chat mới, đổi tên, xóa conversation của chính mình; xóa là soft-delete và theo policy retention.
- Citizen, officer, admin dùng cùng nền chat nhưng giữ UI và dữ liệu riêng theo role.
- Không để browser state là nguồn dữ liệu duy nhất.
Thêm API và tests cho create/list/get/send/delete conversation, ownership và follow-up case.
```

**Done khi:** hỏi “tôi vừa hỏi gì?” trong cùng chat nhận được câu trả lời đúng; mở lại trang vẫn xem được lịch sử riêng.

---

## Bước 5 - Phân quyền giao diện, hồ sơ pháp lý và lỗi 403

**Mục tiêu:** công dân không còn thấy Hồ sơ pháp lý; officer/admin thấy dữ liệu đúng quyền; xử lý lỗi `403 /transformations`.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 5: chuẩn hóa visibility và permission của Hồ sơ pháp lý.
- Role citizen: bỏ hoàn toàn navigation và route entry Hồ sơ pháp lý; backend vẫn chặn trực tiếp nếu truy cập URL/API.
- Role officer: chỉ thấy hồ sơ do mình tạo, được phân công hoặc được citizen chia sẻ qua support session.
- Role admin: thấy metadata/danh sách; muốn mở nội dung chat/hồ sơ phải nhập lý do nghiệp vụ bắt buộc.
- Mỗi lần admin mở nội dung chi tiết, tải file hoặc export phải ghi sensitive_access_audit.
- Sửa lỗi 403 /transformations bằng cách đồng bộ frontend caller với router/role guard; không mở public endpoint để chữa lỗi.
- Khi không có quyền, UI hiển thị trang 403 thân thiện, không hiện Next error overlay.
Thêm tests citizen/officer/admin cho navigation, API, resource ownership, reason-required audit và transformations.
```

**Done khi:** lỗi hình 6 không còn; không tài khoản nào nhìn thấy hồ sơ chung một cách sai quyền.

---

## Bước 6 - Trình xem văn bản pháp luật nội bộ đẹp và đúng điều viện dẫn

**Mục tiêu:** bấm vào “Nghị định …, Điều …” mở văn bản đã nạp trong hệ thống, hiển thị đẹp và làm nổi bật điều/khoản được nhắc.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 6: xây LegalDocumentViewer nội bộ thay cho link VBPL lỗi.
- Citation phải có document id, số hiệu, tiêu đề, điều/khoản, source metadata và nội dung đúng văn bản đã import.
- Click citation mở route nội bộ /legal-documents/{doc_id}?article={article_number}.
- Viewer hiển thị metadata, mục lục điều, nội dung đầy đủ hoặc lazy-load theo điều; scroll đúng Điều được viện dẫn.
- Highlight Điều/Khoản/Điểm được nhắc bằng nền nhẹ và outline; không in đậm sai phần khi article number không xác minh được.
- Có nút tải bản PDF gốc nếu file gốc tồn tại; nếu không có thì xuất PDF nội bộ có nhãn “Bản trích xuất từ kho hệ thống”, không giả là file công báo.
- Không dùng VBPL direct URL làm đường dẫn mặc định trong Ask.
- Cache document metadata/text, lazy-load nội dung để mở văn bản nhanh.
Thêm test citation mapping, article scroll/highlight và trường hợp thiếu source file.
```

**Done khi:** từ câu trả lời có thể mở đúng văn bản/điều luật, không dẫn vào VBPL 404.

---

## Bước 7 - Sửa PDF tiếng Việt và tối ưu hiệu năng xem/tải văn bản

**Mục tiêu:** PDF mở đúng dấu tiếng Việt, không còn hiện ký tự lỗi như hình 3, giảm lag.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 7: sửa pipeline PDF và hiệu năng tài liệu.
- Dùng font Unicode tiếng Việt có giấy phép phù hợp và nhúng đầy đủ vào PDF; không dùng font core PDF thiếu glyph tiếng Việt.
- Bảo toàn UTF-8 từ DB đến generator; thêm kiểm tra text mojibake trước export.
- PDF có title, số hiệu, nguồn, phần Điều được highlight và trang trí tối giản, dễ đọc.
- Nếu PDF gốc có sẵn: stream file với Content-Type, Content-Disposition, Content-Length, cache headers và không render/export lại.
- Nếu export nội bộ: tạo/cached artifact bất đồng bộ; frontend hiển thị loading state không khóa UI.
- Viewer fetch theo điều hoặc phân trang; không tải toàn bộ văn bản lớn trước khi hiển thị.
- Instrument latency cho view/download/export và báo lỗi mềm, không throw overlay.
Thêm test PDF Unicode với các chữ “Phường Lê Chân, Hải Phòng, quyền sử dụng đất”; kiểm tra PDF text extract lại đúng dấu.
```

**Done khi:** tải/mở PDF được bằng browser, Acrobat và Notepad/text extraction; thao tác không làm lag toàn trang.

---

## Bước 8 - FAQ bố cục hai cột, câu hỏi thực tế và biểu mẫu theo yêu cầu

**Mục tiêu:** đổi “Thủ tục hành chính” thành trải nghiệm FAQ dễ dùng.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 8: làm lại trang FAQ hai cột.
- Đổi tên thành “Câu hỏi thường gặp”.
- Bên trái là danh sách lĩnh vực/chủ đề, filter và search; click chủ đề cập nhật panel nội dung bên phải, không điều hướng rời trang.
- Panel phải có câu hỏi, trả lời đã duyệt, nơi nộp, các bước xử lý, căn cứ ngắn và biểu mẫu chính thức liên quan.
- FAQ chỉ hiện biểu mẫu khi FAQ/câu hỏi thực sự yêu cầu; tối đa 1–3 form được match chính xác.
- Form chỉ tải được khi official_level=official, review_status=approved và file/link đã validate.
- Thiếu form hiển thị “Chưa có biểu mẫu chính thức được duyệt”, không gợi ý seed/synthetic như biểu mẫu thật.
- Có nút “Về Hỏi đáp” dẫn về /search, giữ query/domain nếu có.
- Tạo ít nhất 50 FAQ reviewed chia tương đối đều cho 5 lĩnh vực; mỗi FAQ phải có nguồn/căn cứ hoặc nhãn hướng dẫn nghiệp vụ, không bịa.
Thêm UI/API tests cho switching chủ đề, empty form, form valid, max 3 forms và back button.
```

**Done khi:** layout đúng mô hình chủ đề trái/nội dung phải; biểu mẫu xuất hiện đúng nhu cầu và tải được.

---

## Bước 9 - Định tuyến biểu mẫu và citation chính xác cho câu hỏi hỗn hợp

**Mục tiêu:** câu hỏi không cần mẫu không bị spam form; câu cần cả văn bản và mẫu trả đúng 1–3 mẫu.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 9: chuẩn hóa form recommendation và citation selection.
- Detect explicit form intent: “mẫu”, “tờ khai”, “đơn”, “biểu mẫu”, “tải về”, hoặc workflow bắt buộc biểu mẫu.
- Nếu không có explicit/required form intent, recommended_forms phải rỗng.
- Với nhiều thủ tục, rank theo domain, procedure match, form type, official status, ward scope và availability; giới hạn 3.
- Không suy diễn form hộ chiếu, đơn khiếu nại hoặc form không liên quan.
- Citations giới hạn 1–3 nguồn mạnh nhất, ưu tiên văn bản hiệu lực và điều/khoản thực sự dùng để trả lời.
- Câu trả lời citizen/officer dùng links nội bộ từ Bước 6.
Thêm regression cho mái che Tô Hiệu, khai sinh, khiếu nại và câu hỏi thừa kế kết hợp.
```

**Done khi:** không có biểu mẫu dư, không có văn bản dư, và các trường hợp yêu cầu biểu mẫu có file đúng.

---

## Bước 10 - Hỗ trợ trực tuyến realtime người dân - cán bộ

**Mục tiêu:** người dân có thể xin hỗ trợ sau chatbot và được kết nối đúng cán bộ.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 10: tạo live support chat realtime.
- Citizen chọn lĩnh vực, mô tả thắc mắc, hệ thống tạo support request và đưa vào hàng chờ cán bộ của lĩnh vực đó.
- Cán bộ online nhận notification, có thể nhận, từ chối có lý do hoặc chuyển đúng lĩnh vực.
- Sau khi nhận, hai bên chat realtime qua WebSocket; fallback polling nếu WebSocket unavailable.
- Chat có trạng thái waiting/assigned/active/closed, unread count, timestamp, typing indicator tối giản và file attachment theo chính sách upload hiện có.
- Cán bộ không tự thấy dữ liệu citizen ngoài phiên được giao.
- Admin có dashboard hàng chờ, có quyền reassignment/escalation; xem nội dung chat phải nhập lý do và audit.
- Người dân được thông báo rõ đây là hỗ trợ cán bộ, không phải chatbot; có thể đóng phiên và đánh giá.
Thêm integration tests cho queue, routing domain, ownership, realtime delivery và admin audit access.
```

**Done khi:** citizen chọn lĩnh vực và nhắn được đúng officer; officer không thấy phiên của lĩnh vực khác.

---

## Bước 11 - Tạo 5 tài khoản cán bộ và quản lý credential an toàn

**Mục tiêu:** có 5 officer account tương ứng 5 lĩnh vực.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 11: seed 5 tài khoản officer theo lĩnh vực.
Tạo:
- officer_hotich - Hộ tịch - chứng thực
- officer_daidai - Đất đai - xây dựng
- officer_ansinh - An sinh - y tế - giáo dục
- officer_hanhchinh - Hành chính công
- officer_trattu - Trật tự đô thị
Mỗi tài khoản chỉ có allowed_domains tương ứng, ward_scope Phường Lê Chân/Hải Phòng và quyền nhận live support/candidate proposal.
Tạo mật khẩu khởi tạo ngẫu nhiên, không hard-code trong repo, hash bằng auth flow hiện có, must_change_password=true.
Xuất duy nhất một lần file credential khởi tạo ngoài source-control với quyền owner/admin; in username và mật khẩu khởi tạo cho admin sau seed, rồi yêu cầu đổi mật khẩu khi login đầu.
Thêm tests domain restriction, login lần đầu và reset password admin.
```

**Done khi:** bạn nhận được riêng danh sách 5 username và mật khẩu khởi tạo; tài khoản không thể truy cập sai lĩnh vực.

---

## Bước 12 - Admin Control Center và kiểm soát truy cập nhạy cảm

**Mục tiêu:** admin quản lý toàn bộ hệ thống có trách nhiệm, không truy cập nội dung riêng tư tùy tiện.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 12: tạo Admin Control Center.
Các module:
- User & Role: tạo/khóa/reset/gán lĩnh vực/online state.
- Live Support: queue, SLA, chuyển cán bộ, phiên quá hạn.
- Legal Cases: metadata/status; mở nội dung phải có modal nhập lý do nghiệp vụ.
- Knowledge: documents, forms, FAQ, candidates, OCR, embedding.
- Quality: câu insufficient, citation dead, form broken, OCR fail, feedback.
- Audit: filter actor/resource/action/date/reason; không cho sửa log.
Mọi action nhạy cảm gồm view detail, download attachment, export, change role, approve/reject/import phải audit.
Thêm admin dashboard counters và tests reason-required/audit immutable/permission boundaries.
```

**Done khi:** admin quản trị được vận hành nhưng mọi truy cập dữ liệu nhạy cảm có lý do và audit.

---

## Bước 13 - Tách hồ sơ pháp lý và áp dụng retention

**Mục tiêu:** hồ sơ không dùng chung giữa các tài khoản, có thời hạn lưu rõ ràng.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 13: tách dữ liệu hồ sơ và retention jobs.
- Enforce owner_user_id/assignment ở mọi query/API/file download.
- Chat thông thường: auto-delete/soft-delete + purge sau 12 tháng.
- File hồ sơ: expire 6 tháng sau closed_at; khi chưa đóng, không bắt đầu thời hạn purge.
- Audit log: lưu 24 tháng, chỉ purge theo scheduled job có audit record.
- Trước purge, xóa file object/storage, metadata nhạy cảm và embeddings upload context liên quan nếu policy yêu cầu.
- Có admin report record sắp hết hạn; không để admin chỉnh date để né audit.
Thêm tests cross-user denial, expiration calculation, idempotent purge và audit retention.
```

**Done khi:** không còn dữ liệu hồ sơ dùng chung; retention chạy đúng mốc đã chốt.

---

## Bước 14 - Đề xuất văn bản thủ công của cán bộ

**Mục tiêu:** cán bộ có thể gửi văn bản/link/form để admin duyệt, chưa duyệt không được dùng trong trả lời chính thức.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 14: tạo Officer Document Proposal.
- Officer chỉ đề xuất trong allowed_domains của mình.
- Nhập link nguồn, upload file, metadata xác minh được, lý do cần bổ sung và loại document/form.
- Dữ liệu vào candidate queue với submitted_by, domain, source, status=pending.
- Admin review: preview, metadata, OCR/extraction result, duplicates, legal validity flags, approve/reject/request changes.
- Approve mới cho phép normalize/import/embed; reject giữ reason và không vào RAG official.
- Tất cả action có audit.
Thêm tests officer wrong-domain denied, pending excluded from Ask, admin approval creates import job.
```

**Done khi:** cán bộ đề xuất được tài liệu nhưng không thể tự đẩy vào kho chính thức.

---

## Bước 15 - Crawler VBPL có giới hạn, theo lĩnh vực và candidate-first

**Mục tiêu:** tự phát hiện văn bản mới từ VBPL trung ương/Hải Phòng theo lĩnh vực cán bộ, không bypass anti-bot và không tự import bừa.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 15: crawler đề xuất văn bản VBPL theo lĩnh vực.
Nguồn:
- https://vbpl.vn/van-ban/trung-uong
- https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong
Yêu cầu:
- Tôn trọng robots, rate limit, retry/backoff và không vượt anti-bot.
- Chạy định kỳ admin-controlled; ingest metadata/listing trước, nội dung/PDF chỉ khi URL được phép tải.
- Map deterministic theo 5 domain bằng agency, loại văn bản, title/keyword rules; AI chỉ gợi ý, không tự quyết domain.
- Dedup theo law number, issued date, fingerprint/source URL; skip document đã có.
- Mỗi run có max_per_run, statistics, failure reason, source freshness và tạo candidate pending.
- Form links phát hiện được cũng thành form candidate riêng.
- Cán bộ thấy candidate thuộc domain mình; admin duyệt cuối.
Không tự embedding văn bản crawler mới trước approval.
Cập nhật docs/7-DEVELOPMENT/crawl-candidate-workflow.md và thêm crawler tests với fixture HTML/PDF.
```

**Done khi:** candidate mới xuất hiện theo đúng lĩnh vực, không có auto-import thiếu duyệt.

---

## Bước 16 - OCR và AI recommendation cho văn bản

**Mục tiêu:** đọc được PDF scan/ảnh, giúp admin đánh giá tài liệu nhưng không để AI tự duyệt.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 16: tích hợp pipeline OCR + AI recommendation cho candidate.
- Phát hiện PDF text-based hay scan; text-based ưu tiên PyMuPDF/extractor hiện có, scan thì OCR adapter optional.
- OCR fail graceful khi engine/model không cấu hình; ghi failure reason, không crash hoặc bịa text.
- Lưu extraction text, OCR confidence, page count, language, fingerprint và preview.
- AI scoring chỉ là recommendation: domain fit, ward/Hải Phòng relevance, form relevance, duplicate risk, extraction quality, estimated usefulness.
- Admin UI hiển thị score, reasoning ngắn, evidence snippets và manual override approve/reject.
- Không dùng nội dung OCR chưa duyệt cho câu trả lời chính thức.
Thêm test text PDF, scan PDF fixture, OCR unavailable và admin override.
```

**Done khi:** admin biết tài liệu scan có đọc được không, có phù hợp không, và vẫn là người duyệt cuối.

---

## Bước 17 - Import, embedding, citation và PDF source pipeline sau duyệt

**Mục tiêu:** tài liệu được duyệt được nạp đúng, truy xuất đúng, mở/tải được ở Bước 6.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 17: nối approval -> normalize -> import -> embedding -> citation asset.
- Validate scope, legal number, effective/review status, source provenance, text quality và dedup trước import.
- Structured documents chunk theo Điều; unstructured theo section/paragraph và gắn structure metadata.
- Lưu file source/PDF gốc nếu hợp lệ; tạo internal document view + article index.
- Chỉ activate retrieval sau embed success; failure phải rollback/mark partial rõ ràng.
- Citation API trả đúng doc_id/article/source file; Ask chỉ dùng citations active/approved.
- Queue/background worker để không block UI khi OCR/import/embed/PDF export.
Thêm E2E test candidate approved -> embedded -> retrieval -> open highlighted article -> valid Vietnamese PDF download.
```

**Done khi:** một candidate được admin duyệt đi trọn pipeline và xuất hiện đúng trong Ask/citation.

---

## Bước 18 - Quan sát, hiệu năng và lỗi mềm

**Mục tiêu:** giảm lag, phát hiện nhanh source/citation/form hỏng.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 18: bổ sung observability và performance safeguards.
- Track latency cho Ask, stream finalize, document view, PDF stream/export, FAQ load, conversation load, support delivery và crawler/OCR/import jobs.
- Add request cancellation, pagination/lazy loading, cache bounded TTL, debounce search, skeleton/loading state.
- Chuyển các job nặng sang worker/queue; frontend không chờ export/OCR/embed.
- Error boundaries/toast tiếng Việt thân thiện; không để Axios/Next error overlay cho expected API failure.
- Admin Quality dashboard liệt kê citation dead, PDF export fail, broken form URL, slow request, OCR failure và unanswered question.
- Không log nội dung nhạy cảm trong telemetry mặc định.
Thêm load/smoke test cho mở văn bản và tải PDF liên tiếp.
```

**Done khi:** click “Xem văn bản”/“Tải PDF” không làm đơ giao diện và lỗi có thể được truy vết.

---

## Bước 19 - Regression bảo mật, role và trải nghiệm người dùng

**Mục tiêu:** chứng minh các chức năng không phá vỡ luồng cũ.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 19: tạo/chạy regression đầy đủ.
Kiểm tra:
1) Ask hai lần không trùng card và final UI đẹp được giữ.
2) Chat follow-up nhớ đúng context riêng của conversation.
3) Citizen không thấy/không truy cập Hồ sơ pháp lý.
4) Officer chỉ thấy case/support/domain của mình.
5) Admin mở nội dung nhạy cảm thiếu reason bị chặn; có reason tạo audit.
6) Citation mở đúng internal document và highlight điều luật.
7) PDF tiếng Việt đọc đúng dấu, tải không lag.
8) FAQ hai cột, 50 FAQ, form đúng ý định và max 3.
9) Live support route đúng 5 domain.
10) Officer proposal/crawler/OCR candidate chưa approved không xuất hiện trong Ask.
11) Admin approve mới import/embed/retrieval.
12) Retention theo 12 tháng/6 tháng/24 tháng.
13) Voice/upload/PII/domain mismatch/admin model không regress.
14) Frontend production build pass.
Báo PASS/FAIL từng mục, lỗi nào fail phải sửa và chạy lại trước khi kết thúc.
```

**Done khi:** có report test tái lập được, không chỉ test thủ công.

---

## Bước 20 - Rollout pilot và bàn giao quản trị

**Mục tiêu:** triển khai an toàn tại Phường Lê Chân, Hải Phòng.

**Prompt thực hiện**

```text
Hãy thực hiện Bước 20: chuẩn bị rollout pilot.
- Seed 5 officer accounts theo Bước 11 và xuất credential khởi tạo an toàn cho admin.
- Tạo admin runbook: duyệt candidate, xử lý OCR, quản lý form, phân công live support, xem dữ liệu nhạy cảm có lý do, xử lý source lỗi và rollback import.
- Tạo officer guide: trả lời theo domain, nhận/chuyển support, đề xuất văn bản/form.
- Tạo citizen notice: chatbot là hỗ trợ thông tin, cách xin hỗ trợ cán bộ, chính sách lưu chat/hồ sơ.
- Bật feature flags lần lượt: conversations -> document viewer/PDF -> FAQ -> support chat -> proposals/crawler/OCR.
- Theo dõi dashboard quality trong pilot; không bật auto-import hoặc auto-approve.
- Chạy smoke test với citizen, 5 officer accounts, admin và báo URL/endpoint/kết quả.
```

**Done khi:** có thể vận hành pilot, biết ai chịu trách nhiệm cho nội dung và không có tài liệu chưa duyệt đi vào câu trả lời chính thức.

---

## Nguyên tắc bắt buộc xuyên suốt

- Không bịa số điều, mức phạt, thời hạn, phí, biểu mẫu hay nguồn.
- Văn bản/citation chỉ từ nguồn đã duyệt và retrieval thực tế.
- AI/OCR/crawler chỉ đề xuất; admin là người duyệt cuối.
- Không để tài liệu candidate hoặc file OCR chưa duyệt xuất hiện trong câu trả lời chính thức.
- Không để tài khoản truy cập chat, hồ sơ hoặc tệp của người khác.
- Không hard-code mật khẩu thật trong source code, log hoặc tài liệu commit.
- Thay đổi crawler, ingestion, retrieval, OCR, retention và background jobs phải được ghi dưới `docs/7-DEVELOPMENT/`.
