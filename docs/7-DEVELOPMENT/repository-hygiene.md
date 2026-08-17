# Vệ sinh repository

## Mục đích

Thư mục gốc chỉ chứa mã nguồn, cấu hình và các điểm khởi động được dự án sử dụng.
Tệp thử nghiệm một lần, bản vá tạm, đầu ra debug và log cục bộ không được để ở
thư mục gốc.

## Quy ước

- `scripts/`: script có thể sử dụng lại, có mục đích rõ ràng và nên được theo dõi
  bằng Git.
- `scratch/`: script thử nghiệm, đầu ra kiểm tra, tệp tải tạm và bản vá điều tra.
  Thư mục này bị Git bỏ qua.
- `logs/`: log chạy cục bộ; cũng bị Git bỏ qua.
- Không đặt tệp mới mang tiền tố `tmp_`, `_tmp_`, `_b1_`, `fix_`, `patch_` hoặc
  `scan_` tại thư mục gốc. Các mẫu này đã được Git bỏ qua để tránh vô tình đưa
  chúng vào commit.

Nếu một script trong `scratch/` trở thành cần thiết cho vận hành hoặc kiểm thử,
hãy đổi tên rõ nghĩa, chuyển vào `scripts/` hoặc thư mục test phù hợp, bổ sung
hướng dẫn sử dụng và kiểm thử trước khi theo dõi bằng Git.

## Dọn dữ liệu cục bộ

Xem trước các tệp trong `scratch/` cũ hơn 30 ngày:

```powershell
.\scripts\cleanup-scratch.ps1
```

Xóa các tệp đó sau khi xem danh sách:

```powershell
.\scripts\cleanup-scratch.ps1 -OlderThanDays 30 -Apply
```

Script chỉ tác động bên trong `scratch/`, mặc định không xóa gì và không xử lý
mã nguồn, dữ liệu pháp luật, cấu hình hoặc log đang chạy.
