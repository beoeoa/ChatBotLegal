# Answer-quality final gate — 2026-07-27

Trạng thái cuối là **BLOCKED_RELEASE**. Runtime persisted vẫn giữ
`LEGAL_SECTION_GROUNDING_ENABLED=false`; rollout ở stage 0 và không role nào
được bật. Không xóa, viết lại hay embedding lại corpus; active collection vẫn
là `legal_chunks_lechan_primary_v20260723`.

## Kết quả đã đạt

- Backend full: 952 passed.
- Frontend: 87 passed; type-check, lint và production build đạt. Lint còn 11
  warning, không có error.
- Issue planner: 167 golden + 9 role cases, 176/176 đạt.
- Retrieval-only 167 câu: Recall@10 97,059%, direct-source top-5 96,154%,
  coverage 91,617%, wrong-field/expired bằng 0. Warm P95 concurrency 1 là
  11 ms; concurrency 5 là 2.014 ms và không có external error.
- Authorization/isolation: 56 passed. Privacy/rollback: 25 passed.
- UI role matrix trên runtime cô lập: 9/9; rollback flag-off: 1/1.
- Claim hiển thị trong 9 API cases có evidence: 100%. Không repair call.
- Ma trận form deterministic: 3.120/3.120, không sai mapping, role leakage,
  seed/pending exposure hay expired exposure.

## Cổng còn đỏ

1. **BLOCKED_EXTERNAL**: provider/model hiện trả readiness thành công nhưng
   100% structured generation benchmark chạm deadline khoảng 23,8 giây và dùng
   fallback. API role end-to-end P50/P95 là 33.544/39.521 ms, vượt cổng 30 giây.
2. **BLOCKED_LEGAL_REVIEW**: 93 canonical form chưa có authenticated human
   attestation nên runtime-approved bằng 0. Bốn nhóm ưu tiên còn thiếu đã có
   candidate, file, checksum và provenance nhưng vẫn pending review.
3. **BLOCKED_DATASET**: workspace chỉ có 167 câu retrieval đã review, chưa có
   dataset ít nhất 1.000 câu khác biệt. Evaluator đã có hard gate
   `--minimum-case-count`; không nhân bản câu hỏi để làm đủ số lượng.
4. API role quality chỉ đạt 1/9, facet coverage trung bình 83,148%. Bảy ca bị
   chặn bởi form hard gate; một ca xây dựng thiếu expected citation contract.

## Cách mở lại

- Provider/model mặc định phải hết mass timeout; generation P95 <=24 giây,
  end-to-end P95 <=30 giây và concurrency 5 không có lỗi.
- Người rà soát pháp lý gửi một batch attestation có danh tính/thời gian/quyết
  định cho các form đã rà soát, rồi duyệt hoặc từ chối bốn candidate mới.
- Bổ sung dataset tối thiểu 1.000 case có expected facet/source/gap đã review.
- Chạy lại cohort generation, 9 API + 9 UI role cases và toàn bộ release gate.

Báo cáo máy đọc nằm tại
`reports/feature005/final-gate-20260727/release-report.json`. Automated test
không phải là phê duyệt pháp lý.
