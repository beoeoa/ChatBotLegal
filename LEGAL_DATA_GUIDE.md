# Hướng dẫn xây dựng kho dữ liệu pháp luật Hải Phòng

Hệ thống này là công cụ RAG: chất lượng câu trả lời phụ thuộc trực tiếp vào
chất lượng, phạm vi và tình trạng hiệu lực của văn bản được nạp.

## 1. Cấu trúc hồ sơ khuyến nghị

Tạo các hồ sơ riêng thay vì gom mọi văn bản vào một nơi:

- Hộ tịch, chứng thực và cư trú
- Đất đai, xây dựng và trật tự đô thị
- Hòa giải ở cơ sở, tiếp công dân, khiếu nại và tố cáo
- An sinh xã hội, người có công và bảo trợ xã hội
- Xử lý vi phạm hành chính thuộc thẩm quyền cấp xã
- Phí, lệ phí và dịch vụ công tại Hải Phòng
- Tổ chức chính quyền địa phương và phân cấp, ủy quyền

## 2. Thứ tự ưu tiên nguồn

Chỉ nạp bản toàn văn từ nguồn chính thống và lưu URL gốc:

1. [CSDL quốc gia về VBPL - Hải Phòng](https://vbpl.vn/haiphong/Pages/vanban.aspx?dvid=310&idLoaiVanBan=137)
2. [Công báo thành phố Hải Phòng](https://congbao.haiphong.gov.vn/)
3. [Cổng thông tin điện tử thành phố Hải Phòng](https://haiphong.gov.vn/)
4. [Thủ tục hành chính cấp xã tại Hải Phòng](https://thutuc.dichvucong.gov.vn/p/home/dvc-tthc-category.html?cap_thuc_hien=3&co_quan_cong_bo=387628&doi_tuong_thuc_hien=-1&is_advanced_search=1&linh_vuc=-1&loai_tthc=-1&tinh_bo=1&tu_khoa=)
5. [Cổng Dịch vụ công Quốc gia](https://dichvucong.gov.vn/)
6. [Phổ biến, giáo dục pháp luật Hải Phòng](https://pbgdpl.haiphong.gov.vn/)
7. Website chính thức của bộ, cơ quan ngang bộ và cơ quan chuyên môn

Không dùng bài báo, bài SEO, diễn đàn hoặc câu trả lời mạng xã hội làm căn cứ
pháp lý chính.

Các liên kết trên được kiểm tra ngày 11/06/2026. Cần kiểm tra lại bộ lọc cơ
quan, cấp thực hiện và đơn vị hành chính trước mỗi đợt nhập dữ liệu.

## 3. Metadata tối thiểu

Tên nguồn nên theo mẫu:

`[Cơ quan] [Loại và số hiệu] - [Trích yếu] - hiệu lực từ YYYY-MM-DD`

Trong phần ghi chú của hồ sơ, lưu:

- cơ quan ban hành;
- ngày ban hành và ngày có hiệu lực;
- phạm vi áp dụng;
- văn bản sửa đổi, thay thế hoặc bãi bỏ;
- URL bản gốc;
- ngày gần nhất đã kiểm tra hiệu lực.

## 4. Quy trình cập nhật

1. Kiểm tra hiệu lực trước khi nạp.
2. Nạp cả văn bản gốc và văn bản sửa đổi nếu chưa có bản hợp nhất.
3. Không xóa âm thầm văn bản cũ; chuyển sang hồ sơ lưu trữ và ghi rõ ngày hết
   hiệu lực để có thể giải thích các vụ việc theo thời điểm.
4. Mỗi tháng rà soát văn bản mới của Trung ương và Hải Phòng.
5. Sau mỗi đợt cập nhật, thử bộ câu hỏi chuẩn về thẩm quyền, hồ sơ, thời hạn,
   lệ phí và trường hợp chuyển cấp.

## 5. Giới hạn sử dụng

- Không nhập CCCD đầy đủ, dữ liệu sức khỏe, tài khoản, chữ ký hoặc hồ sơ cá
  nhân chưa được che thông tin.
- Câu trả lời phải được người có chuyên môn kiểm tra trước khi dùng để xử lý
  hồ sơ, ban hành văn bản hoặc quyết định quyền và nghĩa vụ của công dân.
- Với tranh chấp, xử phạt, khiếu nại, tố cáo, đất đai phức tạp hoặc vấn đề sắp
  hết thời hiệu, cần chuyển người có thẩm quyền hoặc luật sư xem xét.
