# Migration 29 — Hoàn thiện schema quản trị và quyền sở hữu nền tảng chat

**Ngày:** 2026-07-11  
**Phạm vi:** Bổ sung tối thiểu cho schema đã được tạo tại migration 27–28: cờ kiểm tra quyền sở hữu của dữ liệu legal-profile, liên kết hồ sơ được chia sẻ trong phiên hỗ trợ, cấu hình retention, và các metadata còn thiếu cho case/audit.  
**An toàn:** Additive. Migration không đổi, gán lại hoặc xóa dữ liệu hiện hữu.

## Files

- Up: [`open_notebook/database/migrations/29.surrealql`](../../open_notebook/database/migrations/29.surrealql)
- Down: [`open_notebook/database/migrations/29_down.surrealql`](../../open_notebook/database/migrations/29_down.surrealql)
- Registry: [`open_notebook/database/async_migrate.py`](../../open_notebook/database/async_migrate.py)
- Backfill: [`scripts/backfill_chat_platform_ownership.py`](../../scripts/backfill_chat_platform_ownership.py)

## Schema nền tảng đã có

Migration 27–28 đã tạo các bảng và các trường lõi yêu cầu cho nền tảng chat/quản trị:

| Yêu cầu | Bảng / trường đã có |
|---|---|
| Hội thoại | `conversation.owner_user`, `role_context`, `domain`, `title`, `status`, `created_at`, `last_message_at`, `expires_at` |
| Tin nhắn | `conversation_message.conversation`, `sender_user`, `sender_role`, `content`, `attachments`, `citations_snapshot`, `created_at` |
| Phiên hỗ trợ | `support_session.citizen`, `officer`, `domain`, `queue_status`, `assigned_at`, `closed_at`, `resolution_note` |
| Hồ sơ pháp lý độc lập | `legal_case.owner_user`, `assigned_officer`, `status`, `closed_at`, `expires_at` |
| Audit truy cập nhạy cảm | `sensitive_access_audit.actor`, `resource_type`, `resource_id`, `reason`, `action`, `request_metadata`, `created_at` |
| Đề xuất văn bản | `document_candidate.submitted_by`, `domain`, `source_type`, `review_status`, điểm OCR/AI, `rejection_reason` |
| Hồ sơ người dùng | `user_profile.department`, `allowed_domains`, `ward_scope`, `must_change_password` |

Tên bảng đang dùng là số ít theo convention SurrealDB của dự án (`conversation`, `legal_case`, `document_candidate`), tương đương các danh từ số nhiều trong yêu cầu nghiệp vụ.

## Nội dung migration 29

1. **`support_session`**
   - Thêm `shared_notebook_ids`, `linked_notebook_ids`.
   - Đây là liên kết tối thiểu cho bước phân quyền sau: citizen có thể chia sẻ hồ sơ qua phiên hỗ trợ cho cán bộ được phân công.

2. **`legal_case`**
   - Thêm `title`, `domain` để quản lý hồ sơ theo chủ thể và lĩnh vực, không có workspace dùng chung.

3. **`sensitive_access_audit`**
   - Thêm `expires_at` và index. Bước API phân quyền kế tiếp phải ghi audit trước khi admin mở/tải/export nội dung nhạy cảm.

4. **Dữ liệu legal-profile cũ**
   - Thêm `ownership_status` cho `notebook`, `note`, `source`, `chat_session`.
   - Giá trị hợp lệ trong quy trình hiện tại:
     - `resolved`: đã xác định chủ sở hữu thật;
     - `needs_admin_review`: không xác định được chủ, chờ admin xử lý.
   - Migration chỉ định nghĩa trường; không tự gán chủ sở hữu.

5. **`retention_policy`**
   - Lưu policy vận hành đã chốt, chưa tự xóa dữ liệu:
     - chat: 12 tháng;
     - tệp hồ sơ pháp lý: 6 tháng sau khi xử lý xong;
     - audit: 24 tháng.
   - Retention worker là bước riêng; phải có xác nhận trước khi triển khai xóa/archiving thực tế.

## Backfill an toàn

Lệnh mặc định chỉ báo cáo:

```powershell
python scripts/backfill_chat_platform_ownership.py --dry-run
```

Chỉ chạy ghi sau khi admin đọc báo cáo:

```powershell
python scripts/backfill_chat_platform_ownership.py --apply
```

Báo cáo được ghi tại:

```text
notebook_data/chat_platform_ownership_backfill_report.json
```

Quy tắc:

1. Owner chỉ được coi là hợp lệ khi resolve được tới một `user_account` thật.
2. Owner thiếu, `legacy:*`, `user:*`, hoặc không tồn tại **không bao giờ bị tự gán**.
3. JSON legacy (`data/ask_sessions`, `data/support_tickets`, `data/legal_cases`) không bị sửa/xóa/di chuyển.
4. Notebook, note, source và chat session hiện hữu không có owner hợp lệ chỉ được đánh dấu `ownership_status=needs_admin_review`.
5. Backfill idempotent đối với dữ liệu JSON qua `legacy_source_path`.

## Áp dụng migration

Migration được đăng ký trong `AsyncMigrationManager`; API startup sẽ chạy pending migration.

Chạy thủ công:

```powershell
python -c "import asyncio; from open_notebook.database.async_migrate import AsyncMigrationManager; asyncio.run(AsyncMigrationManager().run_migration_up())"
```

## Rollback

> Không rollback nếu đã có application code phụ thuộc các field mới. Export dữ liệu `retention_policy` và mọi báo cáo backfill trước khi rollback.

Rollback một migration gần nhất:

```powershell
python -c "import asyncio; from open_notebook.database.async_migrate import AsyncMigrationManager; m=AsyncMigrationManager(); asyncio.run(m.runner.run_one_down())"
```

`29_down.surrealql` chỉ gỡ các trường/index/table do migration 29 bổ sung. Nó không đụng tới:

- `conversation`, `conversation_message`, `support_session`, `legal_case`, `sensitive_access_audit`, `document_candidate` được tạo ở migration 27;
- user accounts, notebooks, notes, sources hoặc nội dung của chúng;
- JSON legacy;
- dữ liệu crawler và Chroma.

Nếu cần hoàn nguyên **cờ backfill** mà không rollback schema, chỉ reset sau khi đã xuất danh sách review:

```surql
UPDATE notebook SET ownership_status = 'resolved' WHERE ownership_status = 'needs_admin_review';
UPDATE note SET ownership_status = 'resolved' WHERE ownership_status = 'needs_admin_review';
UPDATE source SET ownership_status = 'resolved' WHERE ownership_status = 'needs_admin_review';
UPDATE chat_session SET ownership_status = 'resolved' WHERE ownership_status = 'needs_admin_review';
```

Không chạy các câu lệnh này nếu admin đã xử lý một phần danh sách review; khi đó phải cập nhật theo record cụ thể.

## Kiểm chứng

```powershell
python -m py_compile scripts/backfill_chat_platform_ownership.py open_notebook/database/async_migrate.py
python scripts/backfill_chat_platform_ownership.py --dry-run
```

Checklist:

- migration registry có migration 29 up/down;
- `--dry-run` không ghi DB, không đổi file JSON nguồn;
- báo cáo có mục `legal_profiles` với số record `needs_admin_review`;
- không có owner nào được tạo hoặc tự gán bởi backfill;
- retention policy được định nghĩa nhưng chưa có job xóa dữ liệu.
