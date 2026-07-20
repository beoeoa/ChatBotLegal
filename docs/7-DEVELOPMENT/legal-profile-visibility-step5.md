# Bước 5 — Visibility & Permission của Hồ sơ pháp lý

**Ngày:** 2026-07-11  
**Phạm vi:** chuẩn hóa quyền xem/mở/tải/export Hồ sơ pháp lý theo role, bắt buộc audit lý do nghiệp vụ với admin, ẩn entry citizen, sửa caller `/transformations`.

## Chính sách

| Role | Danh sách metadata | Mở nội dung/chat | Tải file/export |
|---|---|---|---|
| citizen | 403 | 403 | 403 |
| officer | chỉ hồ sơ mình tạo hoặc được citizen chia sẻ qua support ticket đã assign | chỉ own/shared | only own (shared read-only) |
| admin | được list metadata | bắt buộc `X-Business-Reason` + audit | bắt buộc reason + audit |

Citizen message chuẩn:

> Hồ sơ pháp lý chỉ dành cho cán bộ và quản trị viên.

## Backend

- Service: `api/legal_profile_access.py`
- Áp dụng tại:
  - `api/routers/notebooks.py`
  - `api/routers/chat.py`
  - `api/routers/notes.py`
  - `api/routers/sources.py`
- Support share:
  - `POST /api/support/tickets/{ticket_id}/share-notebook`
  - ticket JSON field `shared_notebook_ids`
  - officer chỉ đọc ticket được assign trực tiếp
- Admin audit fail-closed: nếu không ghi được `sensitive_access_audit` thì không mở nội dung.

## Frontend

- Citizen navigation/route entry `/notebooks` bị ẩn.
- Dashboard guard chặn citizen vào `/notebooks`.
- Command palette không list notebook cho citizen.
- `ForbiddenState` cho 403 thân thiện.
- `BusinessReasonDialog` cho admin trước khi mở detail.
- `SourceDetailContent` không gọi `/transformations` nếu không phải admin.
- API client gắn `X-Business-Reason` từ sessionStorage khi admin đang ở route notebook.

## Tests

```powershell
pytest -q tests/test_legal_profile_access.py
cd frontend
npm test -- --run src/components/layout/AppSidebar.test.tsx src/components/source/transformation-access.test.ts
npx tsc --noEmit
```

## Rollback

1. Revert service/router/frontend files of this step.
2. Schema `sensitive_access_audit` từ migration 27/29 vẫn giữ; không xóa audit rows đã ghi.
3. Support ticket JSON field `shared_notebook_ids` có thể bỏ qua an toàn nếu chưa dùng.

## Không làm ở bước này

- Không xóa dữ liệu legal-profile cũ.
- Không auto-assign owner cho hồ sơ `needs_admin_review`.
- Không mở public endpoint `/api/transformations`.
