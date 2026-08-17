# Đồng bộ hiệu lực pháp lý gần thời gian thực

## Mục tiêu và ranh giới an toàn

Feature 009 đối chiếu các văn bản đang phục vụ với nguồn VBPL chính thức, lưu lịch sử quan sát bất biến và áp một lớp bảo vệ trước khi kết quả retrieval đi vào model hoặc citation.

Feature không sửa `legal_documents`, `legal_articles`, `legal_chunks`, không tự suy đoán trạng thái bằng LLM và không tự thay metadata gốc. Observation, event, decision, run và lease nằm trong các bảng SurrealDB vận hành riêng; snapshot phục vụ là JSON được thay nguyên tử.

Nguồn không đủ bằng chứng luôn giữ trạng thái `unknown`, `ambiguous`, `mismatch` hoặc `partial_scope_missing`. Hệ thống không điền trạng thái, điều khoản hay ngày hiệu lực bằng suy đoán.

## Luồng dữ liệu

```text
Retrieval inventory (chỉ đọc)
  -> exact law-number lookup trên VBPL
  -> deterministic identity/status/provision normalization
  -> immutable observation + event diff
  -> atomic serving snapshot
  -> shared search/batch/direct-proxy overlay
  -> model context + public citation đã rút gọn
```

Đường đọc overlay chỉ dùng snapshot trong process, theo dõi mtime. Không có network hoặc database call cho từng kết quả. Nếu file mới hỏng, cache giữ snapshot hợp lệ gần nhất.

## Bảng và migration

Migration `43.surrealql` tạo:

- `legal_validity_observation`: bằng chứng nguồn theo fingerprint và thời điểm;
- `legal_validity_event`: chênh lệch cần Admin đối chiếu;
- `legal_validity_decision`: quyết định có reason, idempotent theo replay key;
- `legal_validity_sync_run`: thống kê lượt chạy và failure reason code;
- `legal_validity_sync_lease`: ngăn batch đồng thời.

`43_down.surrealql` chỉ xóa năm bảng này. Migration không cập nhật hoặc xóa corpus pháp luật.

## Nhận diện và quy tắc phục vụ

Identity chỉ được coi là exact khi số/ký hiệu chuẩn hóa khớp duy nhất. Nếu có metadata cơ quan hoặc ngày ban hành ở cả hai phía, mismatch sẽ hạ observation về `unknown` và tạo event.

Các trạng thái chuẩn: `not_yet_effective`, `active`, `expired`, `expired_partial`, `suspended`, `suspended_partial`, `amended`, `replaced`, `repealed`, `unknown`.

- Hết/ngưng hiệu lực toàn bộ, bị thay thế hoặc bãi bỏ: không dùng làm căn cứ hiện hành.
- Hiệu lực một phần có ánh xạ chính xác: chỉ chặn đúng điều/khoản/điểm bị tác động.
- Hiệu lực một phần nhưng thiếu phạm vi: chặn toàn văn bản trong ngữ cảnh hiện hành.
- `as_of` lịch sử: đánh giá lại interval tại ngày hỏi; không dùng trạng thái hiện tại để xóa căn cứ lịch sử hợp lệ.
- Source failure hoặc snapshot hỏng: giữ protection state gần nhất; không tự bật lại căn cứ đã bị chặn.

## Chế độ rollout

| Mode | Adverse exact | Partial chưa rõ | Chưa quan sát / snapshot cũ |
|---|---|---|---|
| `observe` | Không chặn, ghi `would_block` | Không chặn, cảnh báo | Cảnh báo |
| `protect` | Chặn | Chặn | Cảnh báo, dùng bộ lọc metadata hiện hữu |
| `strict` | Chặn | Chặn | Chặn; readiness không đạt |

Quy trình phát hành: `observe` -> Admin duyệt mẫu toàn bộ shadow diff -> đạt coverage/freshness -> `protect`. Không bật `strict` nếu chưa có phê duyệt release riêng.

## Cấu hình

```dotenv
LEGAL_VALIDITY_SYNC_ENABLED=true
LEGAL_VALIDITY_SYNC_INTERVAL_SECONDS=1800
LEGAL_VALIDITY_STALE_AFTER_SECONDS=21600
LEGAL_VALIDITY_SYNC_BATCH_SIZE=100
LEGAL_VALIDITY_SYNC_MAX_CONCURRENCY=3
LEGAL_VALIDITY_SYNC_RATE_LIMIT_SECONDS=1.0
LEGAL_VALIDITY_SYNC_MODE=protect
```

Batch và concurrency có giới hạn cứng. TLS luôn được kiểm tra. Adapter chỉ gọi HTTPS allowlisted và trả reason code thay vì raw exception. Khi lỗi liên tiếp, scheduler backoff lũy tiến đến tối đa sáu giờ; một lượt thành công sau lỗi được đánh dấu `recovered`.

## Vận hành Admin

Tại `/legal-import`, tab **Hiệu lực pháp lý** hiển thị coverage, độ tươi, lần thành công, lượt kế tiếp, source health và event đang mở.

API dưới `/api/legal/validity` chỉ dành cho session Admin:

- `GET /status`;
- `POST /run` với reason 10–2000 ký tự;
- `GET /events`;
- `GET /documents/{document_id}`;
- `POST /events/{event_id}/decision`.

Manual run và decision audit trước khi mutation; nếu audit lỗi, thao tác không chạy. ID path được kiểm tra, decision replay trả bản ghi hiện có. Notification dùng hàng `legal_admin_notification`, có dedupe key và không chứa raw payload.

Citizen/Officer chỉ nhận `status`, `serving_action`, `verified_at`, URL nguồn, interval và warning code. Event ID, actor, reason và audit không đi qua public citation hoặc SSE.

## Quan sát và cảnh báo

Các reason code chính:

- `EXACT_OFFICIAL_VALIDITY_OBSERVED`;
- `OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND`;
- `OFFICIAL_DOCUMENT_IDENTITY_AMBIGUOUS`;
- `OFFICIAL_DOCUMENT_IDENTITY_MISMATCH`;
- `OFFICIAL_VALIDITY_PAYLOAD_MALFORMED`;
- nhóm `OFFICIAL_SOURCE_*` cho timeout, TLS, rate-limit và HTTP failure;
- `validity_snapshot_unavailable`, `validity_snapshot_stale`, `partial_scope_unresolved` ở serving trace.

Readiness luôn xuất component `legal_validity_sync` không có URL, credential hoặc exception. Component là quan sát bổ sung ở `observe/protect`, nhưng là required gate ở `strict`.

## Source drift runbook

Khi tỷ lệ malformed/identity-not-found tăng hoặc nguồn degraded:

1. Không xóa snapshot và không chuyển sang `strict`.
2. Xem failure reason aggregation và thời điểm thành công gần nhất; không ghi raw response vào log/ticket.
3. Chạy fixture adapter để phân biệt lỗi parser với lỗi kết nối.
4. Thực hiện một exact lookup read-only, rate-limited cho số/ký hiệu đã biết; xác nhận hostname, TLS, action contract và trường `docNum`, status, date, detail URL.
5. Nếu contract nguồn đổi, cập nhật parser deterministic cùng fixture mới; unknown phải tiếp tục fail closed.
6. Chạy toàn bộ test normalization/source/overlay và benchmark trước khi phục hồi `protect`.
7. Sau lượt thành công, xác nhận `recovered=true`, snapshot timestamp mới và event queue không có false match.

## Backup và rollback

Trước rollout, sao lưu các bảng `legal_validity_*` và file snapshot trong `notebook_data/operations/`. Không cần backup corpus riêng cho feature này vì feature không ghi corpus, nhưng vẫn giữ backup chuẩn của hệ thống.

Rollback nhanh:

1. đặt `LEGAL_VALIDITY_SYNC_MODE=observe` để ngừng enforcement nhưng giữ quan sát;
2. nếu source gây lỗi, đặt `LEGAL_VALIDITY_SYNC_ENABLED=false`; snapshot hiện có không bị xóa;
3. chỉ chạy migration down khi quyết định gỡ hoàn toàn feature; việc này xóa lịch sử vận hành và không thể phục hồi nếu không có backup;
4. xác minh search vẫn dùng metadata filter hiện hữu và corpus checksum không đổi.

## Chiếu trạng thái hiệu lực thống nhất ngày 2026-08-10

PostgreSQL tiếp tục giữ metadata nhập gốc để bảo toàn lịch sử và nguồn gốc dữ
liệu. Trạng thái dùng để phục vụ hiện hành được chiếu từ snapshot bằng cùng một
hàm xác định cho ba đường đọc: retrieval của chatbot, danh sách/chi tiết quản
trị và trang đọc văn bản nội bộ.

Projection công khai bổ sung:

- `status` và `serving_action` từ bằng chứng hiệu lực chính thức;
- `current_answer_eligible` để phân biệt căn cứ hiện hành với tài liệu lịch sử;
- `historical_lookup_allowed` để giữ quyền đọc văn bản cũ;
- `display_label`, trong đó văn bản hết hiệu lực toàn bộ bắt buộc hiển thị
  **“Hết hiệu lực – không dùng để trả lời hiện hành”**.

`stored_status` vẫn được giữ riêng như metadata gốc. Giao diện không còn dùng
`stored_status=active` làm nhãn hiệu lực pháp lý. Văn bản ở trạng thái workflow
chưa duyệt vẫn bị chặn khỏi viewer; văn bản `expired`, `replaced`, `repealed` hoặc
đã ngưng hiệu lực được giữ để tra cứu lịch sử nhưng không đi vào context/citation
cho câu hỏi hiện hành.

Snapshot rebuild nay đọc observation theo trang có giới hạn 2.000 bản ghi/trang
và tổng trần an toàn 100.000, thay vì chỉ đọc 2.000 bản ghi mới nhất. Lần rebuild
trực tiếp ngày 2026-08-10 tăng projection từ 1.984 lên 7.505 văn bản. Ca kiểm tra
`96/2014/TT-BQP` (`document_id=31285`) được khôi phục vào snapshot với trạng thái
`expired`, ngày hết hiệu lực `2016-12-20` và `serving_action=historical_only`.

Đối chiếu trực tiếp sau rebuild:

- danh sách quản trị vẫn cho thấy `stored_status=active` để bảo toàn dữ liệu gốc,
  đồng thời chiếu `validity_status=expired` và nhãn lịch sử bắt buộc;
- viewer trả HTTP 200, cho phép đọc lịch sử và đặt
  `current_answer_eligible=false`;
- truy vấn Điều 26 của `96/2014/TT-BQP` qua client model-facing trả 0 kết quả;
  cả 3 chunk retrieval thô bị overlay loại với reason `expired`;
- 28 test backend hiệu lực/quản trị/viewer và 5 test frontend quản trị/viewer đạt;
  TypeScript và ESLint các file thay đổi đạt.

Trước khi rebuild, snapshot cũ được sao lưu tại
`notebook_data/backups/legal-validity-serving.pre-canonical-20260810-0939.json`.
Rollback không cần sửa PostgreSQL hoặc vector: dừng API, chép bản sao này về
`notebook_data/operations/legal-validity-serving.json`, rồi khởi động lại API.

## Verification và release gate ngày 2026-08-08

- Backend feature/regression mục tiêu: 75 test đạt, gồm ranh giới ngày pháp lý `Asia/Ho_Chi_Minh` và tuần tự hóa ID bản ghi an toàn qua API Admin.
- Frontend panel, citation, SSE và API client: 22 test đạt; TypeScript `tsc --noEmit` và ESLint theo các file feature đạt.
- Migration up/down isolation: 2 test đạt.
- Overlay benchmark 2.000 vòng, 100 kết quả/response: p95 **6,5533 ms**; với retrieval baseline p95 200 ms, delta ước tính **3,2767%**; không có network/DB call.
- `git diff --check` theo các file feature đã theo dõi: đạt; file mới không có trailing whitespace.
- Corpus: feature không thay đổi file/schema corpus. Manifest hiện tại của `data/local_laws` có 3 file, SHA-256 `355b2e7c687296a45f1e9fb6be268e4f5dae9f1753785bc60d39978a913a374a`; dùng giá trị này làm baseline cho lần kiểm tra triển khai tiếp theo. Checksum trước khi bắt đầu chưa được ghi nên không tuyên bố before/after giả tạo.
- Live VBPL probe không phải CI gate và chưa được dùng để thay fixture.

Giới hạn còn lại: các module regression retrieval đầy đủ cần optional `chromadb` và `torch` để collect trong runtime hiện tại. Có 49 regression liên quan đạt, 4 test import/retrieval không collect được vì dependency này. Regression direct helper tương đương đã đạt trong `test_legal_validity_direct_proxy.py`; release environment vẫn phải chạy lại toàn bộ module retrieval khi dependency đầy đủ.

**Trạng thái release gate**: implementation và feature test đạt; phát hành production còn điều kiện cho tới khi chạy lại regression retrieval đầy đủ trong image có `chromadb==1.5.9` và `torch`, đồng thời Admin duyệt shadow diff ở mode `observe`.
