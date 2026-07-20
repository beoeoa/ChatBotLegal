# Bước 15 - Crawler VBPL candidate-first

## Mục đích và phạm vi

Crawler quét metadata/listing từ hai nguồn VBPL:

- `https://vbpl.vn/van-ban/trung-uong`
- `https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong`

Crawler chỉ tạo `legal_crawl_candidate` chờ duyệt. Không tải nội dung/PDF chi tiết, không normalize, import hay embed bất kỳ văn bản nào trước khi admin duyệt.

## Vận hành an toàn

- Chạy qua API admin `/api/legal/crawl/*`; scheduler chỉ quét nguồn đến hạn theo `interval_minutes`.
- Tôn trọng `robots.txt`: không cho phép hoặc không kiểm tra được thì run fail mềm, có lý do.
- Listing request có user agent rõ ràng, retry tối đa 3 lần, exponential backoff và rate limit từ config. Không quét song song, CAPTCHA bypass hay anti-bot bypass.
- Mặc định cho nguồn mới: 30 candidate mới và 10 listing page mỗi run; `max_listing_pages_per_run` do admin cấu hình, bị giới hạn cứng 50. Cấu hình nguồn đã tồn tại không bị crawler tự ý thay đổi.
- Giới hạn candidate áp dụng cho **candidate mới tạo**, không áp dụng cho link trùng. Nhờ vậy một trang có nhiều bản ghi đã biết không làm cursor bị kẹt và các trang sau vẫn được quét trong các run kế tiếp.

## Phân trang bền vững

Nguồn lưu `listing_cursor`. Crawler chỉ theo liên kết trang sau cùng host/path có trong HTML hiện tại; không tự đoán URL trang. Cursor sau trang tải thành công được lưu để quét dần hàng chục/hàng trăm trang qua nhiều run. Hết catalog, cursor quay lại URL gốc để lần sau kiểm tra cập nhật mới.

`last_run_stats` có `listing_pages`, `cursor_reset`; response run có object `pagination`. URL tracking (`utm_*`, `fbclid`, `gclid`) và fragment bị loại trước khi dedup. Link navigation/pagination không thể trở thành candidate.

## Candidate và phân loại

1. Listing parse số hiệu, ngày ban hành nếu có, loại văn bản, cơ quan ban hành, tiêu đề và ngữ cảnh.
2. Domain map theo rule xác định từ cơ quan, loại văn bản, title/context vào 5 lĩnh vực: `ho_tich_chung_thuc`, `dat_dai_xay_dung`, `an_sinh_y_te_giao_duc`, `hanh_chinh_cong`, `trat_tu_do_thi`.
3. Match hòa hoặc không rõ với **văn bản** không tạo candidate tự động. Với link biểu mẫu có tên quá chung, crawler vẫn tạo `source_type=form`, `domain=unclassified` để admin gán lĩnh vực sau; AI chỉ đưa review recommendation, không tự đổi domain, approve hay import.
4. Candidate mới luôn có `status=pending`, `review_status=pending`, `metadata_only=true`, `submitted_by=null` và provenance `vbpl_listing_scan`.
5. PDF/DOC/DOCX/XLS/XLSX link được tạo `source_type=form` riêng; form không được import vào legal RAG.

## Chống trùng và duyệt

Dedup theo URL canonical, cặp `law_number + issued_date` nếu có, fallback `law_number` cho candidate legacy, sau đó là fingerprint của metadata listing. Candidate pending không đi vào Ask/RAG. Cán bộ chỉ xem candidate trong `allowed_domains`; admin xem preview, metadata, extraction/OCR, duplicate và legal-validity flags, sau đó duyệt cuối.

Approval phải qua guard metadata/nguồn/nội dung có sẵn trước khi normalize/import/embed. Reject và changes-requested lưu reason, không vào corpus chính thức.

## Quan sát run

Mỗi run lưu `status`, `statistics`, `failure_reason`, `source_freshness`, cursor và thời điểm. Khi có candidate mới, hệ thống tạo `legal_admin_notification`. Không bật `content_fetch_allowed` để lách lỗi listing; nội dung/PDF chi tiết chỉ được xử lý khi URL/phép tải đã được admin xác nhận.

## Migration và kiểm thử

Migration `31.surrealql` bổ sung `listing_cursor` và `max_listing_pages_per_run` cho `legal_crawl_source`; `31_down.surrealql` rollback hai field này.

- `tests/test_vbpl_pagination_crawler.py`: fixture HTML trang 1/trang 2, PDF fixture, metadata, form candidate, cursor, canonical URL và no-deep-fetch.
- `tests/test_vbpl_candidate_crawler.py`: robots denied, candidate pending-only, statistics và không có notebook/import side effect.
- `tests/test_vbpl_deep_pagination_and_visibility.py`: deep cursor, dedup không tiêu hao quota candidate mới, filter domain/source_type, summary và source freshness.
- Chạy: `python -m pytest -q tests/test_vbpl_pagination_crawler.py tests/test_vbpl_candidate_crawler.py tests/test_crawler_metadata_parsing.py tests/test_crawler_statistics_migration.py tests/test_crawl_route_registration.py`.


## Điều khiển, phân quyền và ranh giới nội dung

- Chỉ admin gọi `/api/legal/crawl/scan`, `/api/legal/crawl/summary` hoặc thay cấu hình nguồn. Các tham số vận hành gồm `interval_minutes`, `max_documents_per_run` (tối đa 200), `max_listing_pages_per_run` (tối đa 50) và `rate_limit_seconds` (0,2–30 giây).
- `/api/legal/crawl/summary` trả candidate count theo domain/source và trạng thái freshness/lỗi/cursor đã được làm gọn cho từng nguồn. `/api/legal/crawl/candidates` nhận thêm filter `domain`, `status`, `source_type`; cán bộ tiếp tục chỉ dùng `/api/legal/proposals/candidates` và chỉ thấy metadata thuộc `allowed_domains` của mình.
- Scheduler chỉ quét nguồn đến hạn; admin có thể tắt nguồn. Không quét toàn bộ hàng trăm trang trong một run: cursor tiếp tục ở run sau để giảm tải và tránh kích hoạt anti-bot.
- Cán bộ chỉ xem candidate thuộc `allowed_domains`; không thể duyệt cuối, bật deep-fetch hoặc import. Admin duyệt cuối.
- AI assessment chỉ là đề xuất. `candidate.domain` do rule xác định là nguồn phân tuyến; AI không được tự đổi domain, trạng thái hoặc import.
- Listing scan không tải trang chi tiết, PDF hay biểu mẫu. `content_fetch_allowed` không được dùng để vượt robots, anti-bot hoặc tự tải sâu.
- Liên kết PDF/DOC/DOCX/XLS/XLSX tạo candidate `source_type=form` riêng. Biểu mẫu không được import vào legal RAG; chỉ đi qua Form Catalog sau khi duyệt.

### Bộ kiểm thử

```powershell
python -m pytest -q tests/test_vbpl_pagination_crawler.py tests/test_vbpl_candidate_crawler.py tests/test_crawler_metadata_parsing.py tests/test_crawler_statistics_migration.py tests/test_crawl_route_registration.py tests/test_officer_document_proposals.py tests/test_candidate_ocr.py
```



## Bước 16 - OCR và khuyến nghị kiểm duyệt

PDF được xử lý ở trạng thái candidate review:

1. PyMuPDF ưu tiên bóc lớp text. PDF có lớp text được gắn `pdf_kind=text_based`, `ocr_status=not_required`.
2. PDF không có hoặc gần như không có lớp text được gắn `pdf_kind=scan` và gọi OCR adapter tùy chọn. Thiếu `pytesseract`/`pdf2image`, lỗi Tesseract hoặc lỗi runtime đều trả trạng thái mềm (`unavailable`/`failed`/`empty`), kèm reason; chuỗi lỗi OCR không bao giờ được lưu làm content.
3. `extraction_result` lưu text thực sự trích xuất, preview, file/text fingerprint SHA-256, số trang, language, OCR confidence, extractor và failure reason. Metadata này là evidence cho admin review, không phải dữ liệu đã được công bố.
4. `review_recommendation` có sáu score: domain fit, relevance Hải Phòng/phường, form relevance, duplicate risk, extraction quality, estimated usefulness; evidence và preview snippets. AI assessment là đề xuất bổ sung, không thể đổi `candidate.domain`, trạng thái hoặc import.
5. Candidate có OCR chưa hoàn tất/lỗi vẫn ở `pending`; guard import từ chối `pending`, `unavailable`, `failed` hoặc `empty`. Admin có thể reject/approve thủ công và thao tác vẫn được audit ở router.

Kiểm thử Bước 16:

```powershell
python -m pytest -q tests/test_candidate_pdf_review.py tests/test_candidate_ocr.py tests/test_candidate_review_override.py tests/test_candidate_extraction_migration.py
```


### Phân quyền vận hành (bổ sung)

- Các route vận hành crawler (`/api/legal/crawl/sources`, update source, scan, candidate detail/review/metadata/import/assess/extract) chỉ dành cho `admin`.
- Cán bộ không thể kích hoạt crawl, thay đổi rate-limit/cursor, duyệt cuối, import hoặc trích xuất candidate. Cán bộ dùng `/api/legal/proposals/candidates` để xem metadata candidate thuộc `allowed_domains` của mình và gửi đề xuất thủ công qua luồng proposal riêng.
- Candidate từ listing vẫn là metadata-only và luôn chờ admin xác minh provenance, hiệu lực và file nguồn trước OCR/import. Vì vậy scheduler không thể tự embedding văn bản mới.
## Bước 17 - Approval → normalize → import → embedding → citation asset

### Luồng xử lý bất đồng bộ

1. Admin duyệt candidate. Candidate vẫn cần vượt `validate_candidate_for_import()`:
   - số hiệu, phạm vi `central|haiphong|local`, ngày hiệu lực/hết hiệu lực;
   - xác nhận nguồn HTTPS chính thức;
   - text tối thiểu 100 ký tự, không mojibake, OCR không lỗi/chưa hoàn tất;
   - không có duplicate candidate chưa được xử lý.
2. Approval chỉ tạo `legal_import_job(status=queued)` và candidate thành `import_queued`; request UI không chờ OCR/import/embed.
3. `legal_import_worker_loop` lấy job, gọi legal-search `/import`. Service legal-search:
   - nhận diện `structure`; structured tách theo **Điều N.**, unstructured theo paragraph/section và lưu metadata `structure` trong vector;
   - insert document/article ở `staging`, `legal_search_scope.included=false`;
   - upsert hai Chroma collection;
   - chỉ sau khi cả hai upsert thành công mới transaction chuyển document/article `active` và scope `included=true`.
4. Nếu DB/vector import lỗi, vector đã tạo bị xóa và DB row staging bị xóa. Worker ghi `legal_import_job=failed` và candidate `import_failed`; không có candidate nào active một phần.
5. Nếu có upload nguồn hợp lệ, file được copy sau activation từ khu upload sang `data/uploads/legal_sources/`, kèm filename/content-type/SHA-256/size trong `source_asset`. Không bao giờ expose file thuộc candidate pending.

### Retrieval, citation và viewer

- Retrieval SQL yêu cầu `legal_documents.status='active'`, `legal_articles.status='active'` và `legal_search_scope.included=true`.
- Citation từ Ask bỏ record không active, trả `doc_id`, Điều/khoản/điểm, `internal_url`, `pdf_url`, metadata nguồn và URL gốc chỉ để audit.
- Viewer nội bộ `/legal-documents/{doc_id}?article=N` gọi `/api/legal/docs/{doc_id}`; document detail có article index lazy-load và chặn staging/non-active.
- Download ưu tiên `source_asset` PDF đã duyệt; thiếu file gốc thì legal-search xuất PDF nội bộ font Noto Sans với nhãn **Bản trích xuất từ kho hệ thống**, không giả là Công báo. PDF export chạy qua `asyncio.to_thread` và có cache artifact.

### Migration / rollback

- `35.surrealql`: bảng `legal_import_job`; trường additive `import_job`, `import_status`, `source_asset` trên candidate.
- `35_down.surrealql`: xóa job state/index; không xóa candidate đã duyệt, audit hoặc file asset để tránh mất chứng cứ vận hành.

### Kiểm thử

```powershell
python -m pytest -q `
  tests/test_legal_import_e2e.py `
  tests/test_legal_document_viewer.py `
  tests/test_legal_pdf_pipeline.py
```

Bộ test kiểm tra worker success/failure, guard validation, staged activation, active-only citations, structured/unstructured chunking, internal viewer/PDF Unicode và source asset path.
