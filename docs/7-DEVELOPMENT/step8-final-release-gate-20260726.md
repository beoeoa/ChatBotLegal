# Bước 8 — Final release gate (2026-07-26)

Trạng thái: **BLOCKED_RELEASE**. `LEGAL_SECTION_GROUNDING_ENABLED=false` vẫn được
giữ trong runtime persisted; rollout stage là `0`, chưa bật Admin, Officer hay
Citizen. Không có corpus nào bị xóa, đổi active collection hoặc embedding lại.

## Kết quả cổng

- Backend full: **910 passed**, 0 failed.
- Frontend full: **87 passed**, 0 failed; type-check và build xanh; lint 0
  error (11 warning).
- Authorization: **48 passed**.
- Privacy/structured/rollback contracts: **12 passed**; rollback UI **1/1**.
- Form catalog validation: **25 passed** về kỹ thuật, nhưng legal review chưa
  được ghi nhận: **0 runtime-approved**, **93 pending**, 6 priority groups cần
  legal review và 1 verified data gap.
- Retrieval-only 167 câu: live retrieval, model request **0**, fixture fallback
  **0**; 145 `FOUND_AND_RETRIEVED`, 19 `FOUND_NOT_RETRIEVED`, 3
  `VERIFIED_DATA_GAP`; 20 live timeout; coverage **86.826%**, P95
  **181.605 giây**, wrong-field **11**.
- API role matrix: **0/9** (9 HTTP 502). UI role matrix: **0/9**; các ca đều
  thiếu `answer_sections` sau provider failure. Đây là lần chạy thật đủ 9 ca
  sau khi cài Chromium, không dùng artifact cũ.
- Generation concurrency 5: cold **0/5**, warm **0/5**; mọi request lỗi server,
  P95 lần lượt **58.459 giây** và **49.283 giây**. Vì vậy P95 end-to-end
  <=30 giây và “không timeout hàng loạt” đều không đạt.
- Privacy scan của các aggregate benchmark artifact: **PASS**. Artifact chia sẻ
  không chứa credential, câu hỏi, câu trả lời hoặc chunk content.

Baseline privacy-safe đầy đủ nằm tại
`reports/feature005/step8-release-20260726/release-gate-summary.json`.

## Nguyên nhân, tác động và cách mở lại

1. Provider/model readiness không khả dụng: `/health` còn healthy nhưng `/ready`
   không hoàn tất và `/api/search/ask/simple` trả 502. Bước sở hữu là Step 5 /
   provider operations. Cần khôi phục provider/model được phê duyệt hoặc runtime
   local được phê duyệt, rồi đạt 9/9 API, 9/9 UI, không mass failure và P95
   end-to-end <=30 giây.
2. Retrieval live có timeout và wrong-field trên 167 câu. Bước sở hữu là Step 3.
   Chỉ sửa nguyên nhân nhỏ nhất, giữ active collection; đạt Recall@10/direct
   top-5 >=95%, wrong-field/expired=0, coverage >=90% và P95 retrieval <=3 giây.
3. Chưa có human legal review cho catalog. Bước sở hữu là Step 1/legal review.
   Người rà soát phải duyệt nguồn và biểu mẫu của bảy nhóm ưu tiên; chỉ bản ghi
   có URL/file chính thức, mapping đúng, còn hiệu lực, checksum/provenance đầy đủ
   và `approved` mới được runtime sử dụng.

Đây là bằng chứng release-kỹ-thuật, không phải phê duyệt pháp lý. Bất kỳ cổng
đỏ nào tiếp tục giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`.
