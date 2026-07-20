# Bước 20 - Checklist rollout pilot

- [x] Có tài khoản citizen, admin và 5 cán bộ đúng 5 domain.
- [x] Tài khoản cán bộ bắt buộc đổi mật khẩu lần đầu.
- [x] Credential khởi tạo nằm trong `data/private`, ACL chỉ owner/admin.
- [x] Có runbook admin, hướng dẫn cán bộ và thông báo người dân.
- [x] Có luồng candidate-first; OCR/AI không tự duyệt.
- [x] Có audit cho truy cập dữ liệu nhạy cảm và thao tác quản trị.
- [x] Có retention tests cho chat/hồ sơ/audit/tệp.
- [x] Chạy regression Bước 19 đầy đủ trên image cuối cùng.
- [x] Chạy smoke thực tế bằng citizen, 5 cán bộ và admin sau rebuild cuối.
- [ ] Admin xác nhận đã chuyển credential và xóa file plaintext.
- [x] Theo dõi dashboard chất lượng trong thời gian pilot; không bật auto-approve/auto-import.
