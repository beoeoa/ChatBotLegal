# Bước 20 — Runbook Admin cho pilot Phường Lê Chân

## 1. Trước khi mở pilot

1. Xác nhận các service healthy:
   - Frontend: `http://127.0.0.1:3000`
   - API health: `GET http://127.0.0.1:5055/health`
   - Legal Search: `GET http://127.0.0.1:8765/health`
2. Mở dashboard chất lượng bằng admin:
   - `GET /api/admin/control/overview`
   - `GET /api/admin/control/quality`
   - `GET /api/legal/quality/summary`
3. Xác nhận pilot feature flags đang ở stage được phép và **luôn** có:
   - `auto_import=false`
   - `auto_approve=false`
4. Nhận file `data/private/bootstrap_officer_credentials.json` bằng kênh nội bộ an toàn. File chỉ được cấp cho owner/admin, không đưa vào source-control, chat nhóm hoặc email công khai.
5. Sau khi năm cán bộ đã đổi mật khẩu thành công, xóa file credential plaintext theo quy trình an toàn.

## 2. Bật tính năng tuần tự

Dùng lệnh dưới đây, chạy smoke test sau **mỗi stage**; chỉ sang stage tiếp theo khi dashboard không có lỗi nghiêm trọng:

```powershell
python scripts/rollout_pilot_features.py --stage 1 # conversations
python scripts/rollout_pilot_features.py --stage 2 # document viewer/PDF
python scripts/rollout_pilot_features.py --stage 3 # FAQ
python scripts/rollout_pilot_features.py --stage 4 # live support
python scripts/rollout_pilot_features.py --stage 5 # proposals/crawler/OCR
python scripts/smoke_step20_pilot.py
```

File trạng thái: `data/pilot/feature_flags.json`. History lưu stage/thời điểm/người rollout. Không sửa thủ công để bật tự động import/duyệt.

## 3. Duyệt candidate, OCR và import

1. Xem nguồn/candidate: `GET /api/legal/crawl/sources`, `GET /api/legal/crawl/candidates`.
2. Kiểm tra nguồn HTTPS chính thức, số hiệu, cơ quan, ngày ban hành/hiệu lực/hết hiệu lực, phạm vi và duplicate flags.
3. Với PDF scan, request OCR/extraction qua `POST /api/legal/crawl/candidates/{candidate_id}/extract`; đọc preview và **đối chiếu file gốc**. OCR/AI chỉ là gợi ý.
4. Candidate lỗi OCR, thiếu metadata, source hỏng hoặc nghi trùng: giữ `pending`/`changes_requested` hoặc reject có lý do. Không suy diễn để điền dữ liệu.
5. Chỉ admin duyệt cuối. Approval tạo job; theo dõi `GET /api/legal/crawl/import-jobs/{job_id}`. Candidate chỉ được retrieval khi import/embed hoàn tất.
6. Không dùng candidate/form pending làm căn cứ trả lời dân. Biểu mẫu duyệt trong Form Catalog, không import form vào legal RAG.

## 4. Quản lý biểu mẫu và FAQ

- Xem audit gap tại `notebook_data/forms/forms_inventory_report.json` và `/api/admin/control/knowledge`.
- Chỉ form `official` + `approved` + file kiểm tra được mới có nút tải.
- Link/file lỗi: gỡ download URL, ghi lý do, giữ trạng thái chưa có file hợp lệ; không thay bằng seed/synthetic giả official.
- Quản lý FAQ qua `/api/faq`; write API chỉ admin. Kiểm tra FAQ đúng domain/ward và form IDs trước publish.

## 5. Phân công live support

- Dashboard queue: `GET /api/admin/control/live-support`.
- Các queue hợp lệ: `ho_tich_chung_thuc`, `dat_dai_xay_dung`, `an_sinh_y_te_giao_duc`, `hanh_chinh_cong`, `trat_tu_do_thi`.
- Assignment/transfer bắt buộc nêu lý do; không gán cán bộ ra ngoài `allowed_domains`.
- Theo dõi SLA quá hạn, close note và rating. Khi có dữ liệu nhạy cảm, chỉ mở chi tiết qua endpoint yêu cầu reason.

## 6. Dữ liệu nhạy cảm, audit và retention

- Mở support/case của người khác phải nhập lý do nghiệp vụ tối thiểu 3 ký tự; hệ thống phải ghi audit **trước** khi disclose.
- Tra audit: `GET /api/admin/control/audit`.
- Retention: chat 12 tháng, tệp/hồ sơ 6 tháng sau khi xử lý xong, audit 24 tháng. Xem trước bằng `GET /api/admin/control/retention/upcoming`; chạy purge chỉ bằng `POST /api/admin/control/retention/run` với reason và `dry_run=true` trước.

## 7. Xử lý source lỗi / rollback import

- Source lỗi robots/rate-limit/anti-bot: dừng source, xem `last_error`, không tăng concurrency hoặc bypass CAPTCHA. Chờ retry interval hay dùng nguồn chính thức khác.
- Citation/PDF/form hỏng: tạo issue trong quality dashboard, disable asset khỏi UI nếu cần và giữ evidence/audit.
- Rollback import: không xóa tay corpus hoặc Chroma. Khóa document khỏi retrieval theo runbook import, lưu document ID/candidate ID/người thao tác/lý do/thời điểm, sau đó rebuild index có kiểm soát. Candidate pending/approved không được tự import lại.

## 8. Theo dõi quality mỗi ngày trong pilot

Theo dõi `citation_dead`, `form_broken`, `forms_without_file`, `ocr_fail`, `import_failed`, `unanswered_question`, slow requests, insufficient evidence, ticket SLA/rating. Dừng stage mới nếu có citation sai, disclosure trái quyền, source không xác minh hoặc lỗi rate-limit lặp lại.
