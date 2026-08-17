# Retrieval Quality V2 — Recall@10, MRR@10 và source-gap

## Phạm vi Retrieval Release V2

Các artifact cũ trong tài liệu này vẫn là bằng chứng M2/M5 remediation và
không đại diện cho production acceptance cuối. Release V2 phải reconcile đủ
12.236 văn bản từ PostgreSQL, rồi phân loại quan sát thành
`current_retrievable`, `historical_only` hoặc `quarantined`. Baseline 7.245 và
candidate 3.000 chỉ là các snapshot/experiment trước đó; không dùng chúng để
che phần inventory ngoài baseline.

Lệnh audit chỉ đọc:

```powershell
python scripts/audit_retrieval_source_inventory_v2.py --output reports/retrieval-release-v2/source-inventory-reconciliation.json
```

Artifact phải ghi source snapshot SHA-256, trạng thái từng document, article /
chunk counts, baseline/candidate collection summaries và active pointer. Mọi
phân loại quan sát đều giữ `legal_review_required=true` cho tới khi có nguồn
chính thức và reviewer attestation.

Bộ acceptance mới là Golden regression 1.000, Hard-negative 500 và Production
holdout 500. Holdout độc lập phải được khóa trước benchmark cuối; không được
tối ưu theo từng case hoặc tái sử dụng sau khi fail.

## Mục tiêu

Đạt riêng trên Golden-1.000 và Hard-negative-100:

- Recall@10 tổng thể và từng lĩnh vực tối thiểu 95%.
- MRR@10 tổng thể tối thiểu 0,90.
- Correct-refusal tối thiểu 99%.
- Không evidence sai hiệu lực hoặc ngoài serving manifest.
- Retrieval + reranking warm p95 không quá 15 giây trong benchmark local.

Không đổi active pointer, không ghi candidate hiện tại và không kích hoạt
learned reranker trong lát này.

## Hợp đồng metric

`legal-retrieval-metrics-v2` chỉ tính Recall@10/MRR@10 trên câu cần trả lời.
Ca `expected_refusal=true` được đo bằng `correct_refusal_rate`; một câu cần trả
lời bị temporal guard chặn vẫn là miss. Báo cáo bắt buộc chia theo dataset,
lĩnh vực, intent và temporal scope.

Với câu đa vấn đề, `issue_recall_at_10` đo từng nhóm nguồn có provenance
`Vấn đề N`; `all_required_sources_coverage` chỉ đạt cho case khi mọi nguồn
chuẩn đều hiện diện. Không dùng first-hit Recall để tuyên bố câu đa vấn đề đã
đủ bằng chứng.

## Sửa temporal routing

Classifier phân biệt:

- Hiện tại tuyệt đối: “hiện nay”, “bây giờ”, “hôm nay”, “hiện hành”.
- Hiệu lực có ngày: “đang/còn hiệu lực tại ngày X”.

Nhóm thứ hai dùng đúng ngày X và không tạo `TEMPORAL_AS_OF_CONFLICT`. Benchmark
replay bằng `legal_as_of` của từng case; `benchmark_today` chỉ được runtime tôn
trọng khi `LEGAL_BENCHMARK_MODE=1`, không phải public safety bypass.

Nếu ngày ghi rõ trong query không trùng `legal_as_of`, case được ghi là
`DATASET_LEGAL_AS_OF_QUERY_DATE_MISMATCH`, loại khỏi mẫu số retrieval và làm
gate `dataset_integrity` thất bại. Audit hiện tại tìm thấy 77 case như vậy trong
Golden-1.000 và 0 case trong Hard-negative-100. Không tự sửa Golden đã duyệt.
Artifact kiểm toán là
`reports/retrieval-quality-v2/dataset_integrity_report.json` kèm sidecar
SHA-256; toàn bộ 77 case thuộc lĩnh vực cư trú/an ninh và cùng tham chiếu
01/07/2026 trong khi case clock là 11/08/2026.

Các câu hỏi tự khai báo thiếu dữ kiện theo mẫu “hệ thống có thể kết luận ngay
không; ... cần tôi bổ sung...” được dừng trước vector/SQL với
`QUERY_FACTS_INSUFFICIENT`. Đây là clarification/refusal có chủ đích và được
đo bằng `correct_refusal_rate`, không phải source Recall.

## Source-gap hiện tại

Artifact `reports/retrieval-quality-v2/source_gap_manifest.json` được dựng từ
candidate 3.000, Golden-1.000 và Hard-negative-100, có SHA-256 riêng.

- Golden-1.000: không thiếu law reference ở mức document.
- Hard-negative-100: thiếu 46 reference thuộc 19 số hiệu.
- 10 reference có bản ghi PostgreSQL nhưng hết hiệu lực ngày 01/07/2026, sớm
  hơn `legal_as_of=10/08/2026`; chúng phải được rà lại Golden/source validity,
  không được tự đưa vào current candidate.
- 36 reference còn lại cần tìm và thẩm định nguồn chính thức.

PostgreSQL enrichment chỉ đọc metadata. Nó không xác nhận pháp lý, không nhập
văn bản, không tạo vector và không thay serving state.

Audit sâu tới article/chunk trong
`reports/retrieval-quality-v2/source_article_integrity_report.json` kiểm tra
1.246 reference chuẩn: 1.192 reference có đủ law/article, 46 reference thiếu
law và 8 reference có law nhưng thiếu article/chunk tương ứng. Golden có một
article gap (`golden-0324`): gold trỏ Điều 17 của `62/2020/QH14`, trong khi văn
bản sửa đổi này chỉ có Điều 1–3 và nội dung sửa Điều 17 nằm lồng trong Điều 2;
case cần được rà mapping nested provision thay vì nhập một Điều 17 không tồn
tại. Hard-negative có 7 article gap và 46 law gap. M6 không thể khắc phục các
gap này bằng reranking.

## Lệnh vận hành

```powershell
pytest -q tests/test_m4_query_understanding.py tests/test_retrieval_evaluation_v2.py
python scripts/build_retrieval_source_gap_manifest.py
python scripts/build_retrieval_dataset_integrity_report.py
python scripts/build_source_article_integrity_report.py
python scripts/run_retrieval_quality_v2.py --output reports/retrieval-quality-v2/control-full-v2.json
python scripts/run_retrieval_stage_diagnostics_v2.py --output reports/retrieval-quality-v2/stage_diagnostics_hard100_v2.json
python scripts/merge_retrieval_control_replay.py
```

Control cố định vector Top-K=20, lexical Top-K=20, `legacy_stack`, learned
reranker tắt, final evidence=10. Stage diagnostics dùng Top-K=50 để đo trần
candidate trước khi quyết định có chạy reranker hay không.

Full warm benchmark dùng process-local cache 4.096 entries với TTL 7.200 giây
cho query vector và exact rows. Giá trị production không bị sửa. Cấu hình này
ngăn warm-up gần 1.000 query hết hạn sau TTL mặc định 300 giây trước khi lượt
đo kết thúc.

Stage diagnostic v2 trên đủ Hard-negative-100 hiện cho kết quả: source/article
availability first-hit 97%, vector Recall@50 67%, lexical Recall@50 24%, fusion
Recall@50 78%, candidate union Recall@50 80%, trước expansion Recall@10 64% và
final Recall@10 52%. Nguyên nhân chính gồm 17 `candidate_miss`, 13
`fusion_rank_loss`, 15 `expansion_loss` và 3 `source_absent`. Safety thật là 0
evidence ngoài manifest, 0 evidence sai hiệu lực; active pointer không đổi.
Hard-negative-100 hiện không có case nào vừa nêu exact identity phù hợp với
Golden source, vì vậy exact Recall không có mẫu số và phải được bổ sung trong
Hard-negative-500.

## Kết quả control đã hiệu chỉnh

`control-full-v3-derived.json` là báo cáo dẫn xuất có kiểm toán: 1.095 case
giữ nguyên từ full run và chỉ thay 5 case đa vấn đề bằng selective replay sau
khi sửa truyền `benchmark_today` qua batch worker. Lỗi cũ làm năm case này lấy
ngày hệ thống thay vì case clock và bị temporal guard chặn nhầm. Artifact dẫn
xuất lưu đường dẫn, SHA-256 của hai parent và SHA-256 phép merge.

- Golden-1.000: 77 dataset error, 873 câu cần trả lời và 50 expected refusal.
  Recall@10 = 99,8855%, MRR@10 = 0,997709, correct-refusal = 100%, false block
  = 0. Recall từng lĩnh vực đều đạt 95%; riêng đất đai đạt 99,4737%. Còn một
  miss là `golden-0324`; gate tổng vẫn FAIL vì 77 lỗi hợp đồng ngày của dataset.
- Hard-negative-100: Recall@10 = 53%, MRR@10 = 0,402 và
  all-required-sources coverage = 13%. Recall theo lĩnh vực: cư trú 75%, đất
  đai 60%, hộ tịch 50%, an sinh 45%, khiếu nại/tố cáo 35%. Warm retrieval p95
  = 5.393 ms, không OOM/timeout, không evidence ngoài manifest hoặc sai hiệu
  lực.

Như vậy temporal/metric đã được sửa và chứng minh, nhưng chất lượng
Hard-negative chưa đạt. Không được dùng kết quả Golden cao để che source gap và
candidate miss của Hard-negative.

## Cổng tiếp theo

Chỉ benchmark BGE/GTE và expansion khi candidate Recall@50 đạt tối thiểu 99%
tổng thể, 98% từng lĩnh vực, safety bằng 0 và source-gap đã có quyết định pháp
lý. Runner M6/M6.1 fail closed nếu `missing_reference_count` chưa bằng 0, kể cả
khi first-hit candidate Recall@50 đã đạt. Lần kiểm tra hiện tại dừng trước khi
load model do bốn gate không đạt: `candidate_recall_at_50`,
`per_domain_candidate_recall_at_50`, `approved_source_gap_zero` và
`approved_source_article_gap_zero`.

Candidate-vNext phải là collection staging riêng. Activation, live import,
re-index và pointer switch vẫn cần lát phê duyệt riêng cùng rollback rehearsal.
