# Bàn giao sửa và kiểm thử chức năng nạp dữ liệu pháp luật

Ngày cập nhật: 2026-08-09  
Phạm vi: nạp văn bản VBPL, hàng chờ duyệt, tạo vector, hỏi đáp, biểu mẫu, crawler tự động và đề xuất cán bộ–admin.

## 1. Mục tiêu bắt buộc

- Chỉ dùng nguồn chính thức; không bịa số hiệu, điều khoản, ngày, phí hoặc thời hạn.
- Trang VBPL dạng tải động phải lấy được toàn văn thật, không lưu màn hình “Đang tải dữ liệu”.
- Văn bản chỉ được kích hoạt sau khi lưu cơ sở dữ liệu và tạo ít nhất một vector/chunk thành công.
- Văn bản và biểu mẫu đi qua hai luồng xét duyệt riêng.
- Cán bộ chỉ thấy và đề xuất trong lĩnh vực được phân quyền; admin xét duyệt.
- Kiểm thử trực tiếp trên web bằng câu hỏi tự nhiên, không dùng bộ câu hỏi có sẵn.

## 2. Việc đã hoàn thành

### 2.1. Sửa đọc toàn văn và metadata VBPL

Đã sửa:

- `api/crawlers/legal_document_pipeline.py`
- `open_notebook/utils/vbpl_crawler.py`

Kết quả:

- Đọc JSON-LD `Legislation` để lấy tên, số hiệu, loại văn bản, cơ quan ban hành và ngày ban hành.
- Ưu tiên metadata chính thức thay vì đoán từ phần “văn bản liên quan”.
- Nhận diện và từ chối trang chỉ có nội dung “Đang tải dữ liệu”.
- Dùng trình duyệt nền riêng cho trang VBPL tải động; chỉ chấp nhận nội dung có cấu trúc pháp luật.
- Giữ URL cuối cùng, phương pháp trích xuất, chẩn đoán và hash nội dung.
- Không âm thầm cắt bỏ phần văn bản nằm trong `div/span`.

Kiểm tra thật với Nghị quyết `22/2025/NQ-HĐND` của Hải Phòng:

- Trạng thái: `ok`.
- Số hiệu: `22/2025/NQ-HĐND`.
- Ngày ban hành/hiệu lực: `2025-10-26`.
- Cơ quan: HĐND Thành phố Hải Phòng.
- Toàn văn: 6.941 ký tự.
- Nhận diện lĩnh vực: đất đai–xây dựng và trật tự đô thị.
- Phương pháp: `vbpl_playwright`.

### 2.2. Sửa crawler tự động

Đã sửa:

- `api/legal_crawl_service.py`
- `frontend/src/components/legal-import/CrawlerSourceManager.tsx`
- `scripts/start_local.ps1`
- `scripts/restart_local_api.ps1`

Kết quả:

- Nguồn crawler có công tắc `content_fetch_allowed` để admin bật/tắt lấy toàn văn.
- Khi cả cờ toàn cục và cờ nguồn được bật, crawler lấy trang chi tiết rồi mới tạo candidate.
- Candidate dùng metadata và nội dung đã chuẩn hóa từ trang chi tiết, không dùng metadata nhiễu từ trang danh sách.
- Trang tải dở hoặc nội dung không hoàn chỉnh được ghi là lỗi trích xuất, không đưa vào xét duyệt như văn bản hợp lệ.
- Crawler vẫn chỉ tạo candidate chờ duyệt, không tự kích hoạt.
- Biểu mẫu vẫn đi theo luồng riêng.
- Đã bổ sung `LEGAL_CRAWL_DETAIL_FETCH_ENABLED` vào hai script khởi động để cờ trong `.env` thực sự có hiệu lực.
- Cấu hình local hiện đã đặt `LEGAL_CRAWL_DETAIL_FETCH_ENABLED=true`; phải giữ cờ nguồn bật khi kiểm thử auto crawl.

### 2.3. Sửa quyền cán bộ/admin và chống trùng đề xuất

Đã sửa `api/routers/legal_search.py`:

- Preview đề xuất của cán bộ gọi đúng hàm chuẩn hóa, không còn lỗi thiếu tham số request.
- Lĩnh vực nhận diện bắt buộc giao với `allowed_domains` của cán bộ.
- Danh sách đề xuất của cán bộ kiểm tra đúng chủ sở hữu, kể cả ID có tiền tố `user_account:`.
- Cán bộ chỉ nhận projection an toàn; không lộ toàn văn, chẩn đoán nội bộ hoặc dữ liệu AI.
- API thông báo crawler và đánh dấu đã đọc chỉ dành cho admin.
- Gửi lặp lại cùng một đề xuất của cùng cán bộ trả candidate cũ với `deduplicated: true`, không tạo bản ghi thứ hai.

### 2.4. Sửa metadata của form nạp trực tiếp

Đã sửa:

- `frontend/src/lib/api/legal-import.ts`
- `frontend/src/app/(dashboard)/legal-import/page.tsx`

Kết quả:

- Sau khi lấy URL VBPL, giao diện tự điền tên, số hiệu, loại, cơ quan, ngày ban hành, ngày hiệu lực và URL từ kết quả chuẩn hóa của backend.
- Kiểm thử trực tiếp trên web xác nhận Nghị quyết 22 có 6.941 ký tự và các ngày được điền đúng.
- Phạm vi/lĩnh vực hiện vẫn phải chọn thủ công; trong lần test đã chọn “Thành phố Hải Phòng” và “Đất đai - Xây dựng - Đô thị”.

### 2.5. Sửa số lượng hàng chờ không khớp danh sách

Đã sửa:

- `api/legal_crawl_service.py`
- `api/routers/legal_search.py`
- `frontend/src/app/(dashboard)/legal-import/page.tsx`

Kết quả:

- Backend có thêm `pending_document_candidates`; badge “Văn bản cần duyệt” không còn tính candidate biểu mẫu.
- Giao diện tách `documentCandidates` khỏi candidate biểu mẫu.
- Tiêu đề hiển thị rõ số đang hiển thị/số văn bản theo trạng thái.
- Nếu bộ lọc nguồn che văn bản, giao diện nói rõ và có nút “Hiện tất cả nguồn”.
- Test web sau khi restart: badge văn bản cần duyệt từ 1 sai lệch về đúng 0; candidate biểu mẫu không còn làm danh sách văn bản bị hiểu nhầm.

### 2.6. Môi trường web

- Đã phát hiện `npm install` vô tình nâng Next.js trong `node_modules` lên 16.2.12, gây Turbopack khóa trình duyệt.
- Đã khôi phục chính xác `frontend/package.json` và `frontend/package-lock.json` về HEAD.
- Đã chạy `npm ci`; runtime quay lại Next.js 16.2.6 theo lockfile.
- Frontend và API đã khởi động lại; trang `/legal-import` hoạt động và phản hồi.
- API `/ready/import`: database, retrieval và import worker đều `ready`.
- Retrieval dùng CUDA, vector index khả dụng, khoảng 160.760 vector đang được lập chỉ mục tại thời điểm kiểm tra.

## 3. Bằng chứng kiểm thử đã đạt

### Backend/crawler/import

Lệnh đã chạy:

```powershell
python -m pytest -q tests/test_role_scoped_legal_crawl.py tests/test_vbpl_candidate_crawler.py tests/test_vbpl_pagination_crawler.py tests/test_crawler_source_management.py tests/test_officer_document_proposals.py tests/test_legal_import_e2e.py
```

Kết quả gần nhất trước sửa đếm hàng chờ: `57 passed`.

Nhóm con sau chống trùng: `32 passed`.

### Biểu mẫu

- `tests/test_official_form_runtime.py`: 5 passed.
- `tests/test_form_resolution_api.py`: 15 passed.
- `tests/test_form_role_api_matrix.py`: 1 passed; kiểm tra đủ 418 thủ tục × 3 vai trò = 1.254 lượt, mất khoảng 196 giây.
- `test_canonical_form_catalog`, `test_form_candidate_reconciliation`, `test_form_review_sync`, `test_legal_forms_pipeline`: 68 passed.
- Tổng các ca biểu mẫu đã chạy: 89 test file-level, trong đó ma trận bao phủ 1.254 trường hợp vai trò.

### Frontend

```powershell
npm test -- "src/app/(dashboard)/legal-import/page.test.tsx" "src/lib/api/legal-import.test.ts" "src/components/legal-import/CrawlerSourceManager.test.tsx"
npx tsc --noEmit
```

Kết quả:

- 3 test files, 11 tests passed.
- TypeScript không có lỗi.

Lưu ý: cần chạy lại các nhóm trên sau thay đổi cuối về `pending_document_candidates`.

## 4. Trạng thái kiểm thử web đang dở

Đã làm trực tiếp trên `http://localhost:3000/legal-import`:

1. Đăng nhập bằng tài khoản admin kiểm thử.
2. Mở tab “Thêm văn bản”.
3. Dán URL VBPL chính thức của Nghị quyết 22/2025/NQ-HĐND.
4. Bấm “Lấy nội dung từ liên kết”.
5. Xác nhận giao diện nhận đủ 6.941 ký tự, số hiệu, cơ quan và hai ngày đúng.
6. Chọn phạm vi Hải Phòng.
7. Chọn lĩnh vực Đất đai - Xây dựng - Đô thị.
8. Đánh dấu đã đối chiếu nguồn chính thức.

Điểm đang dở:

- Nút “Bước 3 — Kiểm tra trước khi gửi” chưa phát sinh request `/api/legal/import/preview` khi điều khiển tự động.
- Không có trường `:invalid`, không có lỗi console và nút đang enabled.
- Ảnh cuối cho thấy con trỏ/focus nằm trong textarea toàn văn rất dài; cần cuộn đúng vùng dưới textarea và bấm nút bằng thao tác trực quan, hoặc kiểm tra vì sao click semantic không kích hoạt `onSubmit`.
- Chưa được phép coi luồng nạp là đạt cho tới khi candidate được tạo, duyệt, worker tạo chunk/vector và Q&A trả lời đúng.

## 5. Việc phải tiếp tục theo thứ tự

### Bước A — Hoàn tất form nạp trực tiếp

1. Mở lại `/legal-import`, tab “Thêm văn bản”.
2. Nếu state form còn giữ, cuộn ra ngoài textarea tới checkbox/nút submit; nếu mất state thì lấy lại URL Nghị quyết 22.
3. Xác định lỗi click submit:
   - dùng ảnh/DOM vùng cuối form;
   - kiểm tra request `/api/legal/import/preview` trong backend log;
   - nếu là lỗi giao diện thật, thêm test tái hiện rồi sửa tối thiểu.
4. Kết quả preview bắt buộc `valid=true`, có `article_count > 0`, `chunk_count > 0`.
5. Bấm gửi duyệt và xác nhận candidate văn bản xuất hiện, không trùng candidate biểu mẫu.

### Bước B — Duyệt và xác minh vector

1. Admin mở candidate Nghị quyết 22, đối chiếu URL/tên/số hiệu/ngày/toàn văn.
2. Duyệt và đưa vào kho.
3. Theo dõi trạng thái: `approved` → `import_queued/running` → `imported`.
4. Chỉ chấp nhận đạt khi:
   - `import_status=completed`;
   - `activation_status=active`;
   - có `document_id`;
   - `chunk_count > 0`.
5. Không chạy reindex toàn kho và không sửa corpus cũ; chỉ kiểm tra văn bản mới.

### Bước C — Hỏi đáp tự nhiên sau embedding

Hỏi trên web, không dùng bộ câu hỏi có sẵn:

- Dễ: “Nghị quyết 22/2025/NQ-HĐND có hiệu lực từ ngày nào?”
- Khó: “Một khu đất xen kẹt tại phường và một khu đất tại xã phải đạt diện tích tối thiểu bao nhiêu; tỷ lệ đất ở tối đa trong khu thu hồi là bao nhiêu, và trường hợp diện tích nhỏ hơn ngưỡng thì cơ quan nào xem xét?”

Tiêu chí đạt:

- Câu dễ trả đúng ngày 26/10/2025.
- Câu khó nêu đúng: 1.000 m² tại phường, 3.000 m² tại xã/đặc khu, đất ở không vượt 15%, trường hợp nhỏ hơn giao UBND thành phố Hải Phòng xem xét từng trường hợp.
- Có trích dẫn đúng Nghị quyết 22/2025/NQ-HĐND và URL chính thức.
- Không trộn thông tin từ văn bản khác, không bịa điều khoản.
- Kiểm tra cả văn phong công dân và cán bộ.

### Bước D — Auto crawl

1. Admin mở “Nguồn thu thập tự động”.
2. Chỉ chọn một nguồn VBPL Hải Phòng để giới hạn tải.
3. Bật “lấy toàn văn” cho nguồn đó.
4. Quét riêng nguồn.
5. Xác nhận candidate mới có:
   - metadata khớp trang chi tiết;
   - toàn văn không phải loading shell;
   - extraction diagnostics/final URL/content hash;
   - matched domains hợp lệ.
6. Không duyệt candidate ngẫu nhiên nếu chưa đối chiếu hiệu lực.

### Bước E — Biểu mẫu

1. Mở tab “Duyệt biểu mẫu”.
2. Dùng biểu mẫu thật từ nguồn chính thức; không dùng lại mẫu BCA sai đã phát hiện trước đó.
3. Kiểm tra candidate biểu mẫu không xuất hiện trong badge/danh sách văn bản.
4. Chỉ duyệt khi có đủ nguồn, checksum, thủ tục gắn kết và hiệu lực.
5. Hỏi trên web về đúng thủ tục để xác nhận hệ thống trả biểu mẫu đã duyệt, không fallback sang mẫu cũ/chưa thẩm định.

### Bước F — Cán bộ đề xuất và admin xử lý

1. Đăng nhập tài khoản cán bộ kiểm thử có hai miền `dat_dai_xay_dung`, `trat_tu_do_thi`.
2. Đề xuất một văn bản hiện hành thuộc đúng phạm vi; không dùng văn bản tương lai.
3. Gửi lại đúng đề xuất lần hai; phải nhận kết quả dedupe, không tạo bản ghi mới.
4. Admin nhìn thấy đề xuất và xử lý.
5. Cán bộ chỉ thấy lịch sử của chính mình và không thấy toàn văn/chẩn đoán nội bộ.

### Bước G — Chạy lại kiểm thử và tài liệu

1. Chạy lại toàn bộ nhóm 57 test backend đã nêu.
2. Chạy lại các test biểu mẫu trọng yếu và frontend 11 test.
3. Chạy `npx tsc --noEmit`.
4. Thêm test cho:
   - `pending_document_candidates` không tính form;
   - UI không báo “bộ lọc nguồn đang ẩn” khi phần tử bị loại vì `source_type=form`;
   - submit form nạp trực tiếp thực sự gọi preview.
5. Cập nhật tài liệu kiến trúc dưới `docs/7-DEVELOPMENT/` về chiến lược VBPL dynamic rendering, fail-closed và cờ crawler toàn văn.

## 6. Tài khoản kiểm thử cần dọn sau khi xong

Đã tạo hai tài khoản local riêng cho E2E:

- `codex_e2e_admin_20260809b` — admin.
- `codex_e2e_officer_20260809b` — officer; được phép `dat_dai_xay_dung`, `trat_tu_do_thi`.

Không ghi mật khẩu vào tài liệu này. Sau khi kiểm thử xong, khóa/soft-delete hai tài khoản bằng chức năng quản trị và xác nhận session bị thu hồi.

## 7. Các tệp đã chạm trong đợt này

- `api/crawlers/legal_document_pipeline.py` (tệp đã untracked từ trước, hiện có thay đổi quan trọng).
- `api/legal_crawl_service.py`.
- `api/routers/legal_search.py`.
- `open_notebook/utils/vbpl_crawler.py`.
- `frontend/src/app/(dashboard)/legal-import/page.tsx`.
- `frontend/src/components/legal-import/CrawlerSourceManager.tsx` (đã untracked từ trước).
- `frontend/src/lib/api/legal-import.ts`.
- `scripts/start_local.ps1`.
- `scripts/restart_local_api.ps1`.
- `tests/test_officer_document_proposals.py`.
- `tests/test_role_scoped_legal_crawl.py` (đã untracked từ trước).

`frontend/package.json` và `frontend/package-lock.json` đã được trả về đúng HEAD; không được đưa thay đổi phụ thuộc ngoài phạm vi vào commit.

## 8. Cảnh báo an toàn

- Worktree có rất nhiều thay đổi của người dùng từ trước; không reset/checkout toàn repo.
- Không sửa hoặc reindex toàn bộ corpus hiện có nếu chưa có phê duyệt riêng.
- Không sửa lịch sử nguồn live và không thêm migration schema trong lát công việc này.
- Không dùng candidate tương lai hoặc nguồn không chính thức chỉ để làm test “xanh”.
- Không công bố mật khẩu, token, `.env` hoặc nội dung bí mật trong log/tài liệu.
