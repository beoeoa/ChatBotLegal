# Bước 13 - Legal quality gate

Bước 13 là cổng kiểm tra trước khi coi pilot đã sẵn sàng. Nó không đánh dấu chất lượng pháp lý bằng cảm tính và không tự tạo căn cứ luật.

## Đã triển khai

- Golden set có câu hỏi riêng cho `citizen` và `officer`.
- Năm nhóm chuẩn của hệ thống đều phải có tối thiểu 2 ca kiểm thử:
  `ho_tich_chung_thuc`, `dat_dai_xay_dung`, `cu_tru_an_ninh`,
  `khieu_nai_to_cao_xu_phat`, `an_sinh_y_te_giao_duc`.
- Mỗi ca bắt buộc có `critical_facts`, `expected_citations`, cờ chống bịa thời hạn/lệ phí và kiểm tra mojibake.
- Các câu không có nguồn kỳ vọng cụ thể được giữ `expected_citations: []`; bộ kiểm thử không được tự đoán số hiệu văn bản.

## Chạy kiểm tra

```powershell
python scripts/complete_step13_golden_set.py
python scripts/audit_step13_quality_gate.py
```

Đây là gate dữ liệu kiểm thử. Nó chưa thay thế đánh giá retrieval/grounding chạy thật với từng model; bước đó phải chạy bằng benchmark runtime sau khi kho pháp luật chính thức đã được duyệt.
