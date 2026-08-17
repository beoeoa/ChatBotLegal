# Bước 7 — API, SSE, history và role acceptance

## Boundary đã sửa

- `ExternalServiceError` không đưa nguyên văn provider response vào HTTP error.
- Public API/SSE citations đi qua allow-list metadata.
- `answer_sections` chỉ giữ các trường hiển thị và public citations.
- `chunk_id`, `source_id`, `packet_id`, `evidence_id`, `request_id` bị loại khỏi
  public `rag_trace`; non-admin không nhận `rag_trace`.
- Legacy `answer` và `citations` vẫn được giữ.
- Credential test được tạo ngẫu nhiên và lưu ngoài runtime/corpus.

## Kiểm thử

- Backend role/SSE/history/privacy focused suite: 39 passed.
- Frontend Vitest: 87 tests, 21 files passed.
- Frontend lint: 0 errors, 11 existing warnings.
- TypeScript type-check: passed.
- 9 live cases: 3 citizen, 3 officer, 3 admin.

## Kết quả live

Tất cả 9 request trả `502 AI_SERVICE_ERROR`. `/health` báo retrieval healthy,
nhưng `/ready` trả `503` vì provider/model không sẵn sàng; Ollama local cũng
không chạy. Vì vậy không được suy diễn nội dung answer, citation, answer_sections,
form hoặc rag_trace là đạt. Artifact summary ghi `pass_count=0`,
`critical_failure_count=9`, và giữ trạng thái `BLOCKED_EXTERNAL`.

Rollback: giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`; không thay đổi corpus,
active collection hoặc model mặc định.

