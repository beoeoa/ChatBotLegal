# Bước 13 — Cô lập dữ liệu theo chủ sở hữu và tác vụ lưu trữ tự động

**Ngày cập nhật:** 2026-07-12  
**Phạm vi:** quyền truy cập hội thoại/hồ sơ/tệp theo tài khoản và chính sách lưu trữ đã chốt: chat 12 tháng, tệp hồ sơ sau khi đóng 6 tháng, audit 24 tháng.

## Chính sách cố định

| Dữ liệu | Thời hạn | Mốc bắt đầu | Hành vi khi hết hạn |
|---|---:|---|---|
| Hội thoại Ask và chat hỗ trợ | 365 ngày | `last_message_at` hoặc lần hoạt động cuối | Xóa nội dung tin nhắn/tệp, đánh dấu `purged` hoặc xóa JSON fallback theo chính sách hiện hữu |
| Tệp của hồ sơ pháp lý | 180 ngày | `closed_at` | Không xóa hồ sơ đang mở; xóa tệp/metadata tệp khi hồ sơ đã đóng quá hạn |
| Tệp hỗ trợ trực tuyến | 180 ngày | `closed_at` của ticket | Xóa thư mục tệp và tham chiếu tệp, vẫn giữ lịch sử chat đến mốc 12 tháng |
| Audit truy cập nhạy cảm | 730 ngày | `created`/`expires_at` | Chỉ retention job xóa; bản ghi của chính lần chạy purge được tạo mới sau khi job hoàn tất |

Các mốc `created_at`, `last_message_at`, `closed_at`, `expires_at` không có endpoint admin nào được phép sửa. Admin chỉ được xem báo cáo metadata sắp hết hạn và kích hoạt purge theo policy cố định.

## Schema và backfill

- Migration nền tảng: [`27.surrealql`](../../open_notebook/database/migrations/27.surrealql), [`28.surrealql`](../../open_notebook/database/migrations/28.surrealql), [`29.surrealql`](../../open_notebook/database/migrations/29.surrealql).
- Migration 29 bổ sung `ownership_status`, link notebook đã được chia sẻ trong phiên hỗ trợ, `retention_policy`, và `sensitive_access_audit.expires_at`.
- Backfill chỉ chạy sau khi admin kiểm tra báo cáo:

```powershell
python scripts/backfill_chat_platform_ownership.py --dry-run
python scripts/backfill_chat_platform_ownership.py --apply
```

Không xác định được chủ sở hữu thì phải gắn `needs_admin_review`; không suy đoán hay tự gán vào tài khoản bất kỳ.

## Điều kiện nhận diện cá nhân

Mật khẩu role cũ (`citizen`/`officer`/`admin`) không xác định được một cá nhân cụ thể. Từ Bước 13, các dữ liệu có tính cá nhân chỉ được tạo hoặc đọc bằng phiên đăng nhập có `user_id` của `user_account`:

- Lịch sử hội thoại `/api/conversations` và JSON legacy `/api/ask-sessions` trả `401` nếu không có tài khoản cá nhân.
- Ticket, message và tệp của `/api/support` trả `401` nếu không có tài khoản cá nhân.
- WebSocket `/api/support/ws` chỉ chấp nhận session token của `user_account`; không tin `role`/`user_id` truyền ở query string.
- Các route tải tệp không nằm trong danh sách public: middleware không còn bypass mọi URL kết thúc bằng /download; quyền owner và audit vẫn được kiểm tra tại router.
- Hỏi đáp một lần không gửi `conversation_id` vẫn có thể hoạt động ở chế độ legacy, nhưng không tải/ghi lịch sử và không tạo dữ liệu hồ sơ cá nhân.

Điều này là fail-closed có chủ đích: không dùng `legacy:citizen`, `legacy:officer` hay username header làm owner của nội dung có dữ liệu cá nhân.
## Quy tắc truy cập

Lớp [`api/legal_profile_access.py`](../../api/legal_profile_access.py) là điểm kiểm tra tập trung cho notebook, source, note, chat session và legal case.

- Người dân: chỉ thấy dữ liệu do chính mình tạo; không truy cập Legal Profile.
- Hồ sơ pháp lý (`notebook`, `note`, `source`, `chat_session`) được cô lập tuyệt đối theo `owner_user`: cán bộ và admin chỉ được liệt kê, xem, sửa, tải hoặc xóa dữ liệu thuộc chính tài khoản đang đăng nhập.
- Role không cấp quyền vượt qua ownership. Admin không tự động thấy hồ sơ của tài khoản khác; notebook được ghi trong ticket hỗ trợ cũng không còn tạo quyền đọc chéo tài khoản.
- Hồ sơ thiếu `owner_user` hoặc có `ownership_status=needs_admin_review` không hiển thị cho bất kỳ workspace tài khoản nào; không tự suy đoán hay tự gán chủ sở hữu.
- Dữ liệu có `ownership_status=needs_admin_review` bị chặn đối với mọi tài khoản trong workspace Hồ sơ pháp lý.
- Route JSON cũ `/api/ask-sessions` vẫn owner-scoped; không còn fallback cho admin quét thư mục của người dùng khác.

Các route nhạy cảm đã được phủ guard gồm router notebooks/sources/notes/chat, context notebook, source-chat, legal case, và ticket/tệp hỗ trợ trực tuyến.

## Scheduler và API vận hành

`api/main.py` tạo `retention_scheduler_loop()` khi API khởi động. Mặc định chạy 1 lần/ngày (`RETENTION_JOB_INTERVAL_MINUTES=1440`), có thể điều chỉnh cho môi trường kiểm thử nhưng không dùng để thay đổi policy ngày lưu trữ.

Admin endpoints:

```text
GET  /api/admin/control/retention/upcoming?within_days=30
POST /api/admin/control/retention/run
```

Body của `POST` chỉ nhận:

```json
{
  "reason": "Rà soát dữ liệu hết hạn theo quy trình nghiệp vụ",
  "dry_run": true
}
```

`dry_run=true` là lựa chọn vận hành đầu tiên. Endpoint audit thao tác admin, còn `run_retention_purge(dry_run=false)` audit batch purge sau khi chạy. Báo cáo gần nhất nằm tại `data/retention/latest_report.json`; báo cáo không chứa message hoặc nội dung hồ sơ.

## Rollback

1. Dừng API trước khi chỉnh scheduler hoặc rollback migration.
2. Sao lưu `data/conversations/`, `data/ask_sessions/`, `data/support_tickets/`, `data/support_attachments/`, `data/legal_cases/` và database SurrealDB.
3. Nếu cần rollback schema, dùng migration down của migration 29. Không rollback khi code đang phụ thuộc các field đó.
4. Không thể khôi phục dữ liệu đã purge nếu không có bản sao lưu. Vì vậy vận hành lần đầu luôn chạy `dry_run`, kiểm tra report, rồi mới cho phép job thực thi thật.

## Kiểm chứng

```powershell
New-Item -ItemType Directory -Force .tmp\pytest | Out-Null
$env:TMP = (Resolve-Path .tmp\pytest).Path
$env:TEMP = $env:TMP
python -m pytest -q tests/test_retention_jobs.py tests/test_conversation_service.py tests/test_legal_profile_access.py tests/test_live_support_integration.py tests/test_admin_control_center.py tests/test_legacy_identity_isolation_step13.py
python -m compileall api
```

Bộ test phải chứng minh: user A không xem được hội thoại/hồ sơ/tệp của user B; URL tải tệp không bypass authentication; ticket đang mở không mất tệp; ticket đã đóng chỉ mất tệp sau 180 ngày; chat chỉ purge sau 365 ngày; audit tồn tại đến 730 ngày; admin không sửa mốc retention qua API.
