# Thiết kế quản lý tài khoản dễ dùng và an toàn

## Mục tiêu

Trang quản lý tài khoản phải giúp quản trị viên hoàn thành nhanh các việc thường gặp mà không nhầm giữa khóa và xóa:

- Tạo tài khoản người dân, cán bộ hoặc quản trị viên.
- Xem một hồ sơ đầy đủ: họ tên, tên đăng nhập, email, số điện thoại, đơn vị, chức vụ, phường/xã, phạm vi nghiệp vụ, trạng thái và mốc thời gian.
- Sửa thông tin và phân quyền; đặt lại mật khẩu bằng luồng riêng.
- Khóa/mở khóa tài khoản; xóa mềm nhưng giữ lịch sử kiểm tra.
- Lọc và xem nhật ký theo tài khoản, hành động, vai trò, lý do và thời gian.
- Đăng xuất an toàn để đổi sang tài khoản khác; không cung cấp chức năng giả mạo phiên người dùng.

## Quyết định trải nghiệm

1. Danh sách dùng bố cục bảng quản trị cô đọng thay cho các thẻ cao: avatar viết tắt, thông tin liên hệ, vai trò/trạng thái, hoạt động gần nhất và thao tác nằm trên một hàng ở desktop; tự xếp dọc trên màn hình nhỏ.
2. Mỗi hàng chỉ giữ biểu tượng **Xem hồ sơ**, **Sửa** và một menu ba chấm. Lịch sử, mật khẩu, khóa/mở khóa và xóa được gom vào menu để giảm nhiễu và hạn chế bấm nhầm.
3. Form tạo/sửa mở trong hộp thoại riêng có tiêu đề và chân trang cố định; thông tin cơ bản, phân công cán bộ và thông tin bổ sung được tách nhóm rõ ràng.
4. Hồ sơ chi tiết mở trong hộp thoại, chia thông tin nhận diện, liên hệ, tổ chức, phạm vi và trạng thái thành các ô dễ quét.
5. Tìm kiếm bao phủ họ tên, tên đăng nhập, email, số điện thoại, đơn vị và chức vụ; bộ lọc có nút đặt lại khi đang áp dụng.
6. **Đổi tài khoản** có nghĩa là kết thúc phiên hiện tại rồi trở về trang đăng nhập. Quản trị viên không được đăng nhập hộ một người khác chỉ bằng một nút trên danh sách.
7. Khóa và xóa là hai trạng thái khác nhau. Khóa có thể mở lại; xóa mềm không thể mở lại từ giao diện nhưng nhật ký vẫn được giữ.

## Quy tắc an toàn

- Mật khẩu khởi tạo hoặc mật khẩu tạm có ít nhất 12 ký tự.
- Email phải có cấu trúc hộp thư và tên miền hợp lệ.
- Số điện thoại là tùy chọn; nếu có thì chỉ nhận các dấu trình bày thông dụng và từ 8 đến 15 chữ số.
- Không cho người quản trị tự khóa hoặc tự xóa tài khoản đang đăng nhập.
- Không cho khóa, xóa hoặc hạ quyền quản trị viên hoạt động cuối cùng.
- Khóa, mở khóa, đặt lại mật khẩu, cập nhật và xóa đều yêu cầu lý do nghiệp vụ.
- Khóa/xóa/đổi mật khẩu thu hồi các phiên đăng nhập liên quan theo chính sách hiện có.
- Xóa là xóa mềm: dữ liệu vòng đời và nhật ký quản trị được giữ, không xóa vật lý bản ghi.

## Luồng sử dụng

### Xem và sửa hồ sơ

1. Tìm tài khoản bằng tên, email, số điện thoại hoặc đơn vị.
2. Chọn **Xem hồ sơ** để kiểm tra toàn bộ thông tin.
3. Chọn **Sửa hồ sơ**, nhập lý do thay đổi và lưu.
4. Máy chủ kiểm tra trùng tên đăng nhập/email, định dạng liên hệ và quyền quản trị cuối cùng trước khi cập nhật.

### Thao tác nhạy cảm

1. Mở **Thao tác khác**.
2. Chọn đặt lại mật khẩu, khóa/mở khóa hoặc xóa.
3. Hộp thoại giải thích hậu quả và bắt buộc nhập lý do.
4. Sau khi thành công, danh sách được tải lại và sự kiện xuất hiện trong lịch sử.

### Đổi tài khoản

1. Chọn **Đổi tài khoản** ở đầu trang.
2. Hệ thống thu hồi/kết thúc phiên theo luồng đăng xuất hiện có.
3. Người dùng được đưa về trang đăng nhập để nhập thông tin của tài khoản khác.

## Kiểm chứng

- Backend: `tests/test_user_management_security.py`, `tests/test_user_pilot.py`, `tests/test_officer_seed_and_password.py`, `tests/test_user_profile_isolation.py`.
- Frontend: `frontend/src/lib/utils/user-account-actions.test.ts`, `frontend/src/lib/stores/auth-store.cookie.test.ts` và kiểm tra kiểu TypeScript.
- Kiểm thử web: đăng nhập quản trị, tìm kiếm bằng số điện thoại/email, mở hồ sơ, mở menu thao tác, kiểm tra lịch sử và luồng đổi tài khoản.
