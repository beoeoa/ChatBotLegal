# Baseline Kỹ Thuật: Chat, Văn Bản Pháp Lý và Phân Quyền

**Ngày khảo sát:** 2026-07-11  
**Phạm vi:** Bước 1 của kế hoạch nâng cấp nền tảng chat. Đây là khảo sát mã nguồn và smoke test không làm thay đổi logic ứng dụng hay dữ liệu nghiệp vụ.  
**Môi trường đo:** local Windows, Backend `http://127.0.0.1:5055`, Legal Search `http://127.0.0.1:8765`, Frontend `http://127.0.0.1:3000`.

## 1. Tóm tắt phát hiện

| Hạng mục | Trạng thái | Kết luận baseline |
|---|---|---|
| Hai câu trả lời Ask | Đã tái hiện bằng mã nguồn | Một phản hồi được thêm vào `chatHistory`, trong khi `ask.finalAnswer` vẫn được render qua `StreamingResponse`; cùng nội dung có hai vùng render khác nhau. |
| PDF tiếng Việt lỗi | Đã tái hiện bằng smoke test | PDF trả HTTP 200 và `%PDF`, nhưng text được PyMuPDF trích lại thành `��`. PDF hiện đang dùng font mặc định `Helvetica`/`WinAnsiEncoding`, không phải Arial Unicode như mã dự kiến. |
| Xem văn bản/PDF chậm | Đã đo | Legal service trả detail rất nhanh; proxy backend và việc tạo PDF tạo thêm độ trễ. Ask là điểm chậm nhất do retrieval + LLM. |
| Follow-up không có context | Có cơ chế nhưng còn không nhất quán | Ask sessions có lưu JSON UTF-8 và endpoint context hoạt động. Nhánh `ask/simple` chỉ ghép history khi `user_id` tồn tại, nên legacy password (`user_id=None`) không có context dù session được lưu theo `legacy:<role>`. |
| `/transformations` 403 | Đã xác nhận | Đây là chủ đích của middleware: toàn bộ `/api/transformations` là admin-only. Tuy nhiên `SourceDetailContent` gọi API này không kiểm tra role, nên officer/citizen gặp 403 và console/overlay. |

## 2. Luồng Ask và nguyên nhân render hai câu trả lời

### Luồng đang chạy

1. Người dùng gửi ở [`frontend/src/app/(dashboard)/search/page.tsx`](../../frontend/src/app/(dashboard)/search/page.tsx): `handleAsk()`.
2. Frontend tạo/lưu user message vào `/api/ask-sessions/{id}/messages` và append local `chatHistory`.
3. [`frontend/src/lib/hooks/use-ask.ts`](../../frontend/src/lib/hooks/use-ask.ts) gọi **non-streaming** `searchApi.askKnowledgeBaseSimple()`.
4. [`frontend/src/lib/api/search.ts`](../../frontend/src/lib/api/search.ts) POST `/api/search/ask/simple`.
5. [`api/routers/search.py`](../../api/routers/search.py) chạy graph `ask_graph.astream(..., stream_mode="updates")`, lấy `write_final_answer`, grounding, citations/FAQ/form rồi trả `AskResponse` JSON.
6. `handleAsk()` append `response.answer` vào `chatHistory`, đồng thời state của `useAsk` giữ `ask.finalAnswer=response.answer`.
7. Giao diện render:
   - `chatHistory.map(...)` tại `page.tsx` dòng 1183: bubble text thô bằng `<p>`;
   - `StreamingResponse` tại dòng 1210 với `ask.finalAnswer`;
   - `StreamingResponse` render `FinalAnswerContent` (Markdown/citation/link/form) tại dòng 492.

### Nguyên nhân xác nhận

Cùng `response.answer` tồn tại ở hai state render độc lập sau khi request hoàn thành:

- `chatHistory`: để lại phiên trước và hiện tại, nhưng render text thô;
- `ask.finalAnswer`: vẫn truthy nên render một card câu trả lời hoàn chỉnh.

Vì không có cờ `currentTurnPersisted`, không có reset sau khi assistant message đã được đưa vào history, và history không dùng `FinalAnswerContent`, một lượt Ask hoàn tất luôn có hai bản hiển thị. Khi hỏi lượt tiếp, `useAsk` reset `finalAnswer` đầu request, khiến bản đẹp biến mất khỏi câu trả lời cũ; đây đúng với hiện tượng người dùng báo.

### Điểm liên quan

- [`use-ask.ts`](../../frontend/src/lib/hooks/use-ask.ts): `sendAsk()` thực tế dùng endpoint simple, không dùng SSE dù state/component còn mang tên streaming.
- [`api/routers/search.py`](../../api/routers/search.py): từ ngày 2026-07-16, `/search/ask` là endpoint SSE tương thích chỉ phát lifecycle, nguồn đã xác minh và kết quả cuối đã validation; không còn phát `strategy` hoặc `answer` trung gian. Alias `final_answer` cũ chỉ xuất hiện sau structured `final` và chứa đúng nội dung đã kiểm chứng. Quyết định và rollback được ghi tại [`ask-progress-local-fallback-2026-07-16.md`](ask-progress-local-fallback-2026-07-16.md).
- [`StreamingResponse.tsx`](../../frontend/src/components/search/StreamingResponse.tsx): `sanitizeDisplayAnswer()` chỉ xử lý presentation, `FinalAnswerContent` xử lý Markdown và link nội bộ.

### Hướng sửa kế tiếp

- Lưu một object `AskMessage` hoàn chỉnh (answer, citations, forms, FAQs, grounding) vào history; history render bằng cùng `FinalAnswerContent`/message card.
- Chỉ render `StreamingResponse` cho turn đang pending; sau khi persistence/local append thành công, reset `useAsk` hoặc đánh dấu turn đã committed.
- Nếu giữ SSE, stream chỉ dùng cho progress của turn hiện hành; không render intermediate answer sau final answer.

## 3. Luồng xem văn bản, tải PDF và lỗi font tiếng Việt

### Luồng xem văn bản

1. Citation tạo `internal_url=/legal-docs/{document_id}?article={article}` tại [`api/routers/search.py`](../../api/routers/search.py), hàm `_ensure_answer_has_source_links()`.
2. [`frontend/src/app/(dashboard)/legal-docs/[docId]/page.tsx`](../../frontend/src/app/(dashboard)/legal-docs/%5BdocId%5D/page.tsx) GET `/api/legal/docs/{docId}?article=...`.
3. [`api/routers/legal_search.py`](../../api/routers/legal_search.py) proxy GET `/documents/{doc_id}` sang Legal Search.
4. [`scripts/legal_search_server.py`](../../scripts/legal_search_server.py), `document_detail()`, đọc `legal_documents`, `legal_articles`, `legal_article_chunks`; có fallback resolve từ chunk/article ID sang document ID.
5. Viewer hiện card mỗi điều và scroll/highlight điều theo query parameter. Với `article` API hiện chỉ trả điều đã lọc, không phải toàn bộ văn bản.

### Luồng tải PDF

1. Viewer fetch `/api/legal/docs/{docId}/download.pdf?article=...` kèm bearer token.
2. Backend kiểm tra văn bản active bằng chính `get_legal_document()`.
3. Nếu tồn tại `data/uploads/pdfs/{docId}.pdf`, backend trả file gốc.
4. Nếu không, backend proxy `GET /documents/{docId}/download.pdf` sang Legal Search (timeout 120 giây).
5. `LegalRetriever.document_pdf()` tạo A4 PDF bằng PyMuPDF (`fitz`), tách paragraph theo ký tự, tô nền vàng heading của điều được yêu cầu (`is_requested`).

### Generator và font thực tế

Mã generator tại [`scripts/legal_search_server.py`](../../scripts/legal_search_server.py) dòng 1245:

```python
font_path = "C:/Windows/Fonts/arial.ttf"
bold_font_path = "C:/Windows/Fonts/arialbd.ttf"
font_kwargs = {"fontfile": font_path} if Path(font_path).is_file() else {}
page.insert_text(..., **font_kwargs)
```

Hai file Arial đều tồn tại trên máy. Tuy nhiên inspect PDF sinh ra bằng `fitz.Page.get_fonts()` cho thấy:

```text
Type1 / Helvetica / WinAnsiEncoding
```

Điều này cho thấy `page.insert_text()` không nhúng/chọn font Arial như kỳ vọng trong PDF kết quả; fallback WinAnsi không có đầy đủ glyph tiếng Việt.

### Smoke test tái hiện lỗi Unicode

Với `document_id=71208`, `article=26`, văn bản `123/2015/NĐ-CP`:

- PDF trả `%PDF`, HTTP 200, 10,378 bytes.
- Text trích lại từ chính PDF bắt đầu bằng `Quy ��nh chi ti�t...`, không phải `Quy định chi tiết...`.
- Document JSON nguồn đúng UTF-8, title/số hiệu/các article đều đúng dấu.

Vì vậy lỗi nằm sau dữ liệu truy xuất, trong PDF rendering/font encoding. Đây không phải mojibake nguồn hay lỗi download blob frontend.

### Hướng sửa kế tiếp

- Đăng ký và nhúng font Unicode CID/TTF đúng cách cho PyMuPDF; sau đó kiểm chứng lại bằng `get_fonts()` (font nhúng, không `WinAnsiEncoding`) và extraction text đúng dấu.
- Thêm test PDF tiếng Việt chứa `Điều`, `Nghị định`, `Hộ tịch`, `đường`.
- Thay wrap theo số ký tự bằng đo chiều rộng text (`TextWriter`/font metrics) để tránh cắt từ, đồng thời tạo PDF từ full document hay có chế độ full/cited article rõ ràng.
- Chỉ tạo PDF khi người dùng yêu cầu và có cache theo `{doc_id, article, document_updated_at}`; không pre-generate trong Ask.

## 4. Data model chat/hồ sơ và follow-up context

### Ask sessions

[`api/routers/ask_sessions.py`](../../api/routers/ask_sessions.py) là cơ chế riêng, không dùng chat notebook:

- Lưu file JSON UTF-8 tại `data/ask_sessions/<owner>/<session_id>.json`.
- `AskSession`: `id`, `user_id`, `role`, `domain`, `ward_scope`, `messages`, thời gian tạo/cập nhật.
- `AskMessage`: `role`, `content`, `citations`, `recommended_forms`, `faq_refs`, `grounding_status`, `created_at`.
- `GET /ask-sessions/{id}/context?last_n=...` trả tối đa 20 messages.

Smoke test đã tạo phiên disposable, lưu user + assistant, đọc context nhận đúng 2 message theo thứ tự, sau đó xóa phiên. Thời gian lần đo: create 101.2 ms; append 76.9/78.3 ms; context 72.2 ms; delete 68.1 ms.

### Vì sao follow-up vẫn mất context

1. `handleAsk()` có truyền `sessionId` vào `useAsk`, do đó request payload có `session_id`.
2. Với local/offline path, `_ask_local()` luôn gọi `_get_ask_session_history()` theo owner key tương thích, sau đó `_build_local_prompt(..., history)` đưa history vào prompt.
3. Với online simple path đang dùng bởi UI, `/search/ask/simple` chỉ ghép history khi `ask_request.session_id and user_id`.
4. Trong legacy shared-password authentication, middleware gán `request.state.user_id=None`; sessions lại được lưu dưới `legacy:<role>`. Kết quả: history được lưu và UI reload được, nhưng online simple prompt bỏ history. Đây là nguyên nhân trực tiếp của follow-up không nhớ ngữ cảnh ở mode legacy.
5. Cơ chế `chat`/`source_chat`/notebook dùng state/session khác, không được nối vào `/search/ask/simple`; vì vậy việc “hồ sơ pháp lý” có memory không tự làm Ask có memory.

### Rủi ro thiết kế cần xử lý ở bước sau

- File JSON phù hợp pilot nhưng không có retention job, search/index, locking đa tiến trình, audit truy cập, hay transaction DB.
- Legacy sessions được scope theo role, không phải mỗi công dân. Không được dùng mô hình này cho triển khai có dữ liệu công dân thật.
- `get_session()` có nhánh admin duyệt toàn bộ directory theo `session_id`; chưa có lý do nghiệp vụ/audit bắt buộc theo yêu cầu sản phẩm đã chốt.

## 5. `/api/transformations` trả 403

### Chuỗi gọi

- Caller: [`frontend/src/components/source/SourceDetailContent.tsx`](../../frontend/src/components/source/SourceDetailContent.tsx), `fetchTransformations()` gọi `transformationsApi.list()` ngay khi mở source detail.
- Client: [`frontend/src/lib/api/transformations.ts`](../../frontend/src/lib/api/transformations.ts), GET `/transformations`.
- Axios interceptor: [`frontend/src/lib/api/client.ts`](../../frontend/src/lib/api/client.ts), gửi `Authorization` và `X-User-Role` lấy từ `localStorage.auth-storage`.
- Router: [`api/routers/transformations.py`](../../api/routers/transformations.py), `GET /transformations` không có dependency role riêng.
- Guard thực tế: [`api/auth.py`](../../api/auth.py), `ADMIN_ONLY_PREFIXES` có `/api/transformations`; `PasswordAuthMiddleware.path_requires_admin()` áp dụng cho mọi method. `api/main.py` gắn middleware.

### Kết quả smoke test

Với token hợp lệ lấy từ môi trường local:

| Role | `GET /api/transformations` | Kết quả |
|---|---:|---|
| admin | 200, 12 transformations | Hợp lệ |
| officer | 403 | `Admin role required` |
| citizen | 403 | `Admin role required` |

403 là đúng chính sách hiện tại, không phải router hỏng. Vấn đề UI là `SourceDetailContent` gọi endpoint admin-only không condition theo role, rồi chỉ `console.error`, khiến Next dev overlay/log người dùng thấy lỗi.

### Hướng sửa kế tiếp

- Không gọi transformations từ source detail nếu role không phải admin; đồng thời ẩn selector/action tương ứng.
- Hoặc tách một read-only endpoint role phù hợp, nhưng phải xác định rõ transformation nào được xem/thực thi và không mở quyền ghi ngoài admin.
- Lỗi 403 phải được xử lý im lặng/UX hợp lệ, không để overlay cho người dùng không có quyền.

## 6. Latency baseline

Các số dưới đây là một mẫu local đơn lẻ hoặc ba lần với document nhỏ; dùng để so sánh trước/sau, không phải SLA production.

| Endpoint / thao tác | Kết quả | Latency |
|---|---|---:|
| `GET :5055/health` | 200 | 38.5 ms |
| `GET :8765/health` | 200 | 231.5 ms |
| `POST :8765/search` (`khai sinh`, top_k=3) | 200 | 3,064.0 ms |
| `POST :8765/search` lặp lại | 200 | 4,059.3 ms |
| `GET :8765/documents/71208?article=26` | 200, 17.5 KB | 6.5 / 6.4 / 26.2 ms |
| `GET :5055/api/legal/docs/71208?article=26` | 200 | 475.1–692.1 ms |
| `GET :8765/documents/71208/download.pdf?article=26` | 200, 10.4 KB | 137.7 / 180.0 / 228.5 ms |
| `GET :5055/api/legal/docs/71208/download.pdf?article=26` | 200 | 966.6–1,209.7 ms |
| `POST :5055/api/search/ask/simple` (câu khai sinh) | 200, answer 2,378 ký tự | 21,640.1 ms |
| Ask session create/append/context/delete | 201/201/200/204 | 68.1–101.2 ms |

### Nhận định hiệu năng

- Viewer JSON không phải nút thắt tại Legal Search; proxy Backend thêm khoảng 0.5–0.7 giây trong mẫu đo.
- PDF generate trực tiếp khoảng 0.14–0.23 giây; đường proxy/check active/doc detail làm tổng gần 1–1.2 giây. Người dùng cảm nhận lag còn có thể đến từ main-thread UI/render và việc fetch toàn bộ PDF blob, cần browser profiling ở Bước 7/18.
- Ask 21.6 giây bao gồm graph/LLM, retrieval và grounding. Retrieval đơn lẻ đã 3–4 giây; phần còn lại chủ yếu là model graph/verification. Cần telemetry theo stage trước khi tối ưu sâu.

## 7. Ưu tiên sửa đề xuất sau baseline

1. **Bước 3:** thống nhất một message renderer cho history/current turn và loại bỏ render final answer hai lần.
2. **Bước 4:** đưa session history vào online simple với owner key cho cả real-user và legacy; sau đó chuyển session/memory có dữ liệu công dân sang DB + retention/audit đã thiết kế.
3. **Bước 5:** chặn call transformations ở UI non-admin, hoặc thiết kế endpoint read-only có policy rõ ràng.
4. **Bước 6–7:** sửa PDF font Unicode/embedding, test glyph, giữ highlight; cache PDF và giảm proxy work.
5. **Bước 18:** thêm timings theo stage `retrieval`, `model`, `grounding`, `document detail`, `pdf generation` để có p50/p95 thay cho single-sample baseline.

## 8. Giới hạn khảo sát

- Không có browser performance profile hoặc trace UX trong Bước 1; latency là HTTP smoke test local.
- Không thử token của từng tài khoản thật. Test role dùng password local hợp lệ, đủ xác nhận middleware policy (admin 200, officer/citizen 403).
- Không gọi Ask streaming endpoint vì UI production đang gọi `/search/ask/simple`; stream path được trace qua mã nguồn.
- Không sửa logic, schema hoặc corpus. Các session smoke test tạo ra đã được xóa; không giữ dữ liệu test.
