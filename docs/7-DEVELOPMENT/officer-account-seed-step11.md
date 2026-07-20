# Seed tài khoản cán bộ theo lĩnh vực

## Mục tiêu

Tạo năm tài khoản cán bộ theo nguyên tắc quyền tối thiểu cho pilot Phường Lê Chân, Hải Phòng. Mỗi tài khoản chỉ nhận hỗ trợ trực tuyến và đề xuất tài liệu trong đúng một lĩnh vực.

## Cách chạy

```powershell
python scripts/seed_officer_accounts.py
```

Script chạy lặp an toàn: tài khoản đã tồn tại được bỏ qua, không đổi mật khẩu và không tự mở rộng quyền.

| Username | Lĩnh vực được phép |
|---|---|
| `officer_hotich` | `ho_tich_chung_thuc` |
| `officer_daidai` | `dat_dai_xay_dung` |
| `officer_cutru` | `cu_tru_an_ninh` |
| `officer_khieunai` | `khieu_nai_to_cao_xu_phat` |
| `officer_ansinh` | `an_sinh_y_te_giao_duc` |

Tất cả có `ward_scope = Phường Lê Chân, Hải Phòng`, được nhận live support và được đề xuất candidate trong lĩnh vực được gán.

## Mật khẩu khởi tạo

- Mật khẩu được sinh ngẫu nhiên bằng `secrets.token_urlsafe`; không hard-code trong source.
- Password hash dùng cơ chế chuẩn hiện có của hệ thống.
- Plaintext chỉ ghi một lần vào `data/private/bootstrap_officer_credentials.json`, không in từng mật khẩu ra terminal.
- Trên Windows, file chỉ cấp quyền cho owner hiện tại và Administrators. Nếu không thiết lập được ACL, script xóa file và dừng.
- Admin chuyển credential qua kênh riêng, yêu cầu đổi mật khẩu lần đầu rồi xóa file.

## Đổi và reset mật khẩu

- Tài khoản mới có `must_change_password=true`.
- Frontend chuyển đến `/change-password` và middleware chặn các API khác cho đến khi đổi xong.
- Admin reset bằng `POST /api/users/{user_id}/reset-password`; thao tác revoke session và ghi audit.

## Kiểm thử

```powershell
python -m pytest tests/test_officer_seed_and_password.py tests/test_live_support_integration.py -q
cd frontend
npm run build
```
