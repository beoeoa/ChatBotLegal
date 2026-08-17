# Phân loại 6.141 văn bản chưa gắn lĩnh vực

## Phạm vi

Ngày 14-08-2026, chạy bộ phân loại `legal-document-domain-classification-v1`
trên các dòng:

```sql
legal_documents.status = 'active' AND legal_documents.field_id IS NULL
```

Kết quả được xuất thành manifest đề xuất, không sửa `legal_documents.field_id`,
không sửa `legal_search_scope` và không re-index vector.

Taxonomy cố định gồm đúng bảy giá trị:

1. `ho_tich_chung_thuc`
2. `dat_dai_xay_dung`
3. `an_sinh_y_te_giao_duc`
4. `cu_tru_an_ninh`
5. `khieu_nai_to_cao_xu_phat`
6. `hanh_chinh_cong`
7. `ngoai_pham_vi`

`trat_tu_do_thi` chỉ được xử lý như alias cũ của nhóm
`khieu_nai_to_cao_xu_phat`, không tạo nhóm thứ tám.

## Phương pháp

Bộ phân loại dùng tiêu đề làm tín hiệu chính, kết hợp một phần giới hạn của
năm điều khoản đầu tiên, metadata và kết quả scope cũ. Các quy tắc bảo vệ gồm:

- Cụm rõ về xử phạt/khiếu nại/tố cáo được ưu tiên vào nhóm thứ năm, kể cả khi
  đồng thời có từ khóa cư trú hoặc xây dựng.
- Cụm cư trú/căn cước được ưu tiên trước `hanh_chinh_cong`.
- “Dịch vụ công nghiệp” không bị hiểu nhầm là “dịch vụ công”.
- Chứng thực chữ ký số/chứng thực thông điệp dữ liệu được đưa vào
  `ngoai_pham_vi`, không nhầm với chứng thực hộ tịch.
- Văn bản chỉ áp dụng cho địa phương khác Hải Phòng được đưa vào
  `ngoai_pham_vi`.
- Văn bản có điểm thấp hoặc điểm gần nhau được gắn `ngoai_pham_vi` với lý do
  `insufficient_domain_evidence` hoặc `ambiguous_domain_evidence` để chờ rà soát.

## Kết quả

| Nhóm | Số văn bản |
|---|---:|
| `ho_tich_chung_thuc` | 32 |
| `dat_dai_xay_dung` | 529 |
| `an_sinh_y_te_giao_duc` | 713 |
| `cu_tru_an_ninh` | 74 |
| `khieu_nai_to_cao_xu_phat` | 163 |
| `hanh_chinh_cong` | 469 |
| `ngoai_pham_vi` | 4.161 |
| **Tổng** | **6.141** |

Kiểm định manifest đạt:

- 6.141/6.141 văn bản được phân loại.
- 6.141 ID duy nhất; không trùng ID.
- Không có domain ngoài taxonomy.
- Không có văn bản chưa phân loại.

Độ tin cậy theo manifest: 4.687 `high`, 1.030 `medium`, 424 `review`.
Các dòng `review` vẫn được gán vào `ngoai_pham_vi` để bảo đảm mỗi văn bản có
đúng một nhóm, nhưng chưa đủ điều kiện công khai theo domain.

## Tái lập

```powershell
\.venv\Scripts\python.exe scripts/classify_fieldless_legal_documents.py --self-test
\.venv\Scripts\python.exe scripts/classify_fieldless_legal_documents.py
```

Artifact:

- `output/legal-document-domain-classification-v1.json`
- `output/legal-document-domain-classification-v1-review.csv`

Manifest có `source.snapshot_sha256`, `classifier_version` và cờ
`activation.status = proposed` để đối chiếu/hoàn tác.

## Điều kiện kích hoạt còn thiếu

Chưa được dùng manifest này làm bộ lọc retrieval. Trước khi kích hoạt cần rà
424 dòng `review`, lấy mẫu tối thiểu từng nhóm, đối chiếu các văn bản Hải Phòng
và chạy shadow retrieval trên Golden V3. Chỉ sau khi đạt gate mới tạo migration
additive hoặc pointer phân loại; không cập nhật hàng loạt `field_id` và không
xóa văn bản ngoài phạm vi.
