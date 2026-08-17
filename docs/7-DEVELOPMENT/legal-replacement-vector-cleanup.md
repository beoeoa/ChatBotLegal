# Tìm văn bản thay thế và dọn vector an toàn

Mở rộng này giữ nguyên nguyên tắc **chặn phục vụ trước, dọn vật lý sau**. Không có migration mới, không xóa `legal_documents`, `legal_articles`, `legal_chunks`, nội dung nguồn hay lịch sử hiệu lực.

## Tìm văn bản thay thế

`api/legal_replacement_candidates.py` chỉ tạo ứng viên khi observation bất lợi có đủ bốn điều kiện:

1. Số/ký hiệu văn bản bị tác động được nhận diện chính xác.
2. Nguồn bằng chứng là HTTPS chính thức trên `vbpl.vn` hoặc `.gov.vn`.
3. `identity_status=exact` và `evidence_status=sufficient`.
4. Nguồn trả trường quan hệ trực tiếp `affecting_document_number`.

Ứng viên được gắn `explicit_official_relationship` và `verified`, nhưng `relation_status` luôn là `pending_admin_review`. Hệ thống không tự phê duyệt, không tự kích hoạt văn bản mới, và không dùng tiêu đề, embedding hay LLM làm bằng chứng pháp lý. Khi nguồn xung đột, không chính thức hoặc thiếu quan hệ trực tiếp, API trả reason code để Admin xử lý.

Timeline Admin `GET /api/legal/validity/documents/{document_id}` có thêm `replacement_discovery`. Giao diện **Hiệu lực pháp lý** hiển thị số/ký hiệu, liên kết bằng chứng và cảnh báo đang chờ duyệt.

## Điều kiện dọn vector

Dọn toàn văn chỉ được tạo manifest khi snapshot hiện hành đồng thời thỏa:

- `serving_action` là `historical_only` hoặc `block_document`;
- trạng thái toàn phần thuộc `expired`, `suspended`, `replaced`, `repealed`;
- identity chính xác, evidence đầy đủ và có fingerprint;
- document ID và số/ký hiệu trong SQL khớp snapshot.

`block_provisions`, trạng thái một phần, `unknown`, bằng chứng xung đột hoặc snapshot thiếu đều bị từ chối với `verified_full_document_block_required`. Vì vậy sự cố dọn vector không thể bật lại văn bản và cũng không thể biến một nghi vấn thành quyết định pháp lý.

## Manifest, trạng thái và chạy lại

Manifest `legal-vector-cleanup-v1` được ghi nguyên tử dưới `notebook_data/operations/legal-vector-cleanup/`. Job ID là SHA-256 ổn định từ document ID, fingerprint bằng chứng và danh sách chunk ID. Danh sách xóa chỉ gồm ID chính xác dạng `chunk-{chunk_id}` lấy từ SQL.

```text
blocking_applied
  -> vector_cleanup_running
  -> vector_cleanup_completed | vector_cleanup_partial | vector_cleanup_failed

vector_cleanup_partial | vector_cleanup_failed
  -> vector_cleanup_running
```

Executor kiểm tra trước/sau và xóa ở cả collection nhanh đang hoạt động lẫn collection mở rộng. Retry với ID đã vắng là thành công idempotent. Nếu một collection lỗi, job là `vector_cleanup_partial`; snapshot chặn vẫn giữ nguyên.

## API và bảo vệ thao tác

API Admin:

- `GET /api/legal/validity/documents/{document_id}/vector-cleanup/preview`;
- `POST /api/legal/validity/documents/{document_id}/vector-cleanup` với `reason` tối thiểu 10 ký tự.

Dịch vụ retrieval nội bộ yêu cầu header `X-Legal-Operations-Token`, lấy từ biến môi trường `LEGAL_VECTOR_CLEANUP_TOKEN`, cho cả preview và execute. API chính chỉ chuyển khóa này sau khi xác thực role Admin. Execute ghi audit `admin.legal_validity.vector_cleanup` trước khi gọi mutation; audit lỗi thì không dọn vector.

## Vận hành và khôi phục

1. Admin kiểm tra timeline và bằng chứng chính thức.
2. Xác nhận văn bản đã bị chặn logic trên snapshot.
3. Chạy preview, kiểm tra số vector dự kiến và ghi lý do.
4. Chạy cleanup; chỉ coi hoàn tất khi cả hai collection có `after=0`.
5. Với `partial/failed`, sửa nguyên nhân kỹ thuật và chạy lại cùng document; không chỉnh snapshot để né lỗi.
6. Khôi phục vector hoặc kích hoạt lại văn bản là thao tác riêng, cần bằng chứng hiệu lực mới và phê duyệt Admin; mở rộng này không tự phục hồi.

Kiểm thử tự động chỉ dùng snapshot/collection giả, không kết nối và không xóa vector trong kho thật.
