# Bước 18 — Observability & Performance Safeguards

## Mục tiêu

Bổ sung telemetry bảo mật, request cancellation, debounce search, caching bounded, skeleton/loading state, error boundary/toast tiếng Việt, admin quality dashboard và smoke/load test.

## 1. Runtime telemetry (`api/observability.py`)

- `RuntimeTelemetry` sử dụng bounded deque: tối đa 1.000 events và 300 issues.
- Event chỉ bao gồm category, route template, duration, outcome, status và metadata đã allowlist.
- Không lưu question/answer, user ID, cookie/token, query string, tên file hoặc nội dung upload.
- `RuntimeTelemetryMiddleware` thêm `X-Request-Duration-Ms` và ghi request API vào store.
- `summary()` trả `by_category`, `slow_requests`, error status, issue counts và mô tả privacy.

## 2. Đo độ trễ

| Category | Route/template | Vị trí |
| --- | --- | --- |
| `ask` | `/api/search/ask/simple` | `api/routers/search.py` |
| `ask_stream_finalize` | `/api/search/ask` | `api/routers/search.py` |
| `document_view` | `/api/legal/docs/{doc_id}` | `api/routers/legal_search.py` |
| `pdf_stream_export` | `/api/legal/docs/{doc_id}/download.pdf` | `api/routers/legal_search.py` |
| `faq_load` | `/api/faq/`, `/api/faq/match` | `api/routers/faq.py` |
| `conversation_load` | `/api/conversations/*` | `api/routers/conversations.py` |
| `support_delivery` | `websocket:support_delivery` | `api/routers/live_support.py` |
| `crawler`, `ocr`, `import_job` | candidate worker/API routes | middleware + `api/legal_crawl_service.py` |
| `form_download` | official form download | `api/routers/ward_procedures.py` |

## 3. Cancellation, debounce và loading

- `useAsk` và `useSearch` dùng `AbortController`; request cũ bị hủy trước khi gửi request mới.
- Cancelled request không hiển thị toast lỗi; Ask có nút **Dừng**.
- Ô Search dùng `mutateDebounced()` với delay 300 ms; Enter/nút Search vẫn có thể chạy ngay.
- Viewer văn bản dùng metadata-first, article lazy-load và bounded cache header.
- Dashboard chất lượng có skeleton/loading cards; ErrorBoundary/toast dùng thông báo tiếng Việt.

## 4. Cache và queue

- Metadata document: `Cache-Control: private, max-age=300`.
- PDF hợp lệ: `Cache-Control: private, max-age=86400`; legal-search server cache artifact có TTL cấu hình được.
- Scheduler crawler và import worker chạy nền.
- OCR/extract candidate trả `202 Accepted`, worker xử lý file local rồi client poll job. Crawler candidate mới không được auto-import/embed.

## 5. Quality dashboard

Route `/legal-quality` (admin-only) hiển thị:

- citation dead;
- PDF export fail;
- broken form URL;
- slow request (>= 5 giây);
- OCR/import failure;
- unanswered question;
- timing theo category và danh sách request chậm.

Dashboard không hiển thị nội dung câu hỏi hay thông tin định danh.

## 6. Smoke/load tests

- `tests/test_observability.py`, `tests/test_runtime_telemetry.py`: bounded store, route mapping, slow requests, metadata/privacy filtering.
- `tests/test_legal_viewer.py`, `tests/test_legal_document_viewer.py`: viewer, PDF source/fallback cache, Unicode và mojibake guard.
- `tests/test_legal_viewer_smoke.py`: 6 document requests, 5 PDF downloads liên tiếp, burst 12 telemetry events.

Chạy:

```powershell
python -m pytest -q `
  tests/test_observability.py `
  tests/test_runtime_telemetry.py `
  tests/test_legal_viewer.py `
  tests/test_legal_document_viewer.py `
  tests/test_legal_viewer_smoke.py
npm --prefix frontend run build
```

## Vận hành và giới hạn

Telemetry là in-memory, bị xóa khi backend restart; đây không phải audit log. Audit pháp lý/nghiệp vụ vẫn đi qua các bảng audit hiện có. Không đưa thông tin nhạy cảm vào `metadata` của telemetry; chỉ dùng khóa allowlist: `status`, `outcome`, `error_class`, `job_type`, `source_type`, `cached`, `origin`.
