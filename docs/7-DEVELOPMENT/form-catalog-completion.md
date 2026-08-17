# Procedure Catalog và Form Catalog

## Mục đích

Hệ thống tách ba loại dữ liệu độc lập:

1. Legal retrieval trả căn cứ pháp luật từ collection văn bản.
2. Procedure Catalog lưu định danh thủ tục và, sau khi được xác minh, hồ sơ,
   thẩm quyền, nơi nộp, trình tự, thời hạn và lệ phí.
3. Form Catalog lưu metadata và file/URL của biểu mẫu.

Binary DOC/DOCX/PDF/XLS/XLSX không được embedding vào legal RAG. Với quy mô
khoảng 100 mẫu, exact code, alias và full-text deterministic đủ nhanh; semantic
metadata collection chỉ được cân nhắc nếu exact/full-text không đạt cổng.

## Artifact version 1

- `notebook_data/forms/canonical_procedures_v1.json`
- `notebook_data/forms/canonical_form_requirements_v1.json`
- `notebook_data/forms/canonical_forms_catalog_v1.json`
- `notebook_data/forms/procedure_form_bindings_v1.json`

Builder:

```powershell
python -X utf8 scripts/build_canonical_form_catalog.py --check
python -X utf8 scripts/build_canonical_form_catalog.py
```

Kết quả baseline ngày 2026-07-24:

- 202/202 dòng discovery đã phân loại;
- 90 dòng synthetic bị cách ly;
- 52 thủ tục thực;
- 93 ứng viên biểu mẫu canonical;
- 11 giấy tờ hỗ trợ, 4 kết quả do cơ quan cấp, 13 mẫu nội bộ cán bộ,
  1 e-form và 2 mục ngoài phạm vi cấp phường.

Không dòng nào được tự động chuyển sang `approved`.

## Hard gate runtime

Một mẫu chỉ được phục vụ khi đồng thời đạt:

- binding thủ tục đã duyệt;
- nguồn thuộc allowlist chính thức;
- đúng domain, cấp hành chính và đối tượng;
- có ngày hiệu lực;
- không hết hiệu lực hoặc bị thay thế;
- `review_status=approved`;
- file local mở được và checksum khớp, hoặc URL tải/e-form chính thức hợp lệ;
- không phải seed, reference, supporting document hoặc official result.

Citizen chỉ nhận `audience=citizen|both`. Officer nhận thêm mẫu nội bộ. Admin
nhận provenance và reason code trong trace; renderer không tạo URL hoặc claim
pháp lý mới.

Nếu không có mẫu đạt hard gate:

```json
{
  "recommended_forms": [],
  "forms_unavailable": true
}
```

## Crawler candidate-only

Crawler đọc URL thủ tục đã được ánh xạ trong Procedure Catalog, kiểm tra robots,
host allowlist, redirect, MIME, magic bytes, macro, checksum và duplicate:

```powershell
python -X utf8 scripts/crawl_canonical_forms.py --no-network
python -X utf8 scripts/crawl_canonical_forms.py --rate-limit 0.25
```

Mọi file tải về có `review_status=candidate_pending_review` và
`approved=false`. Crawler không sửa catalog. Các trang thiếu URL được ghi
`NEEDS_SOURCE_MAPPING`; đăng nhập/CAPTCHA/robots/HTTP block mới được ghi
`BLOCKED_EXTERNAL`.

Review packet:

```powershell
python -X utf8 scripts/build_form_review_packet.py
```

Chỉ người rà soát pháp lý mới được xác nhận tên, mã mẫu, thủ tục, căn cứ,
hiệu lực và quyết định promote. Automated test không thay thế phê duyệt này.

## Kiểm thử không dùng model

```powershell
python -X utf8 scripts/evaluate_form_resolution_matrix.py
```

Ma trận gồm 52 thủ tục × 20 biến thể × 3 vai trò = 3.120 ca. Báo cáo chỉ chứa
counter/timing/reason code, không chứa câu hỏi hoặc câu trả lời. Baseline hiện
tại đạt procedure top-1 100%, wrong procedure/form bằng 0, role leakage bằng 0
và lookup P95 dưới 200 ms.

Không có mẫu nào được khuyến nghị cho tới khi catalog có record đã qua hard
gate; vì vậy kết quả `forms_unavailable` trong giai đoạn review là hành vi đúng.

## Theo dõi và revalidation

```powershell
python -X utf8 scripts/monitor_canonical_forms.py
python -X utf8 scripts/monitor_canonical_forms.py --network
```

Job chỉ đọc. Checksum hoặc URL thay đổi tạo reason
`NEEDS_REVALIDATION`; job không tự approve hoặc sửa catalog.

## Rollback

1. Giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`.
2. Không đổi active legal collection.
3. Khởi động API với catalog cũ hoặc bỏ các file canonical khỏi release bundle.
4. Không xóa file candidate, corpus hoặc history.
5. Candidate/pending không bao giờ được quảng bá trong rollback.

Trạng thái hiện tại là `TECHNICAL_PASS_LEGAL_REVIEW_REQUIRED` cho resolver và
catalog framework. F2/F3 chưa thể phát hành vì 95 requirement runtime chưa có
ánh xạ URL thủ tục chính thức và 93 form candidate chưa có nguồn, hiệu lực cùng
phê duyệt pháp lý.
## Step 1 priority catalog checkpoint (2026-07-26)

`reports/feature005/forms-completion-20260726/step1-priority-catalog.json` is
the read-only evidence packet for the seven priority groups. It records the
canonical `procedure_id`, form code/name, official source URL, local file and
checksum (when present), legal basis, effectivity, scope, approval flag,
provenance, binding status, and legal-review status. No record was approved by
automation.

Runtime requires all hard gates simultaneously: canonical procedure mapping,
approved binding, `review_status=approved`, `approved=true`, official
downloadable source, legal basis, provenance, current effectivity, official
procedure page, and a valid local checksum or verified download URL. A failure
returns `forms_unavailable=true` with `data_gap_status` and reason codes; seed,
candidate, package-page, pending, expired, mismapped, missing-file, and
unverified-URL records are never served.

The current packet is `BLOCKED_EXTERNAL`: the seven groups have no runtime
eligible forms because legal review is still pending. The foreign-element
marriage group has no canonical `procedure_id`; this is recorded as
`VERIFIED_DATA_GAP` rather than mapping it to the domestic procedure.

## Xác nhận pháp lý hàng loạt (2026-07-27)

Trang Admin `/legal-import` có thêm preview chỉ đọc trước khi tạo quyết định.
Backend tính hard gate từ catalog và file đang lưu, không nhận `reviewer_id` từ
trình duyệt. Preview hiển thị tổng số, số đạt, số bị loại, reason code,
`procedure_id`, mã/tên mẫu, URL chính thức, file, checksum, căn cứ pháp lý,
hiệu lực, phạm vi và trạng thái review.

Luồng xác nhận:

1. `GET /api/procedures/forms-catalog/legal-review-attestations/preview` tạo
   snapshot có fingerprint, không sửa dữ liệu.
2. Admin đang đăng nhập bấm **Xác nhận pháp lý hàng loạt** một lần.
3. `POST /api/procedures/forms-catalog/legal-review-attestations` đối chiếu lại
   fingerprint và toàn bộ item. Snapshot thay đổi hoặc bị sửa sẽ trả
   `PREVIEW_STALE_OR_TAMPERED`.
4. Một transaction có rollback đồng bộ candidate queue, canonical form,
   procedure binding, official index, checksum manifest và attestation/audit.
5. Cùng `attestation_id` và payload được xử lý idempotent; không tạo audit trùng.

Seed, demo, synthetic, quarantined, sai procedure/domain, thiếu file/checksum,
nguồn không chính thức, thiếu căn cứ, chưa rõ hiệu lực, hết hiệu lực hoặc bị
thay thế đều bị loại. Queue approval riêng lẻ vẫn không đủ để runtime phục vụ.

Kết quả live ngày 2026-07-27:

- tổng canonical form: 93;
- đạt toàn bộ hard gate: 0;
- bị loại: 93;
- runtime-approved: 0;
- attestation: 0.

Các nguyên nhân chính là 80 form chưa có candidate mapping và 13 form liên quan
vẫn thiếu metadata hiệu lực hoặc còn lỗi nguồn/mapping. Vì vậy nút xác nhận bị
vô hiệu hóa và không có quyết định pháp lý nào được tạo. Trạng thái là
`BLOCKED_LEGAL_REVIEW_DATA`, không phải PASS. Automated test không thay thế
quyết định của người rà soát pháp lý.

## Đối chiếu candidate và effectivity (2026-07-27)

`scripts/reconcile_canonical_form_candidates.py` đối chiếu toàn bộ 93 canonical
form với candidate queue và procedure-source catalog theo khóa xác định:

1. `procedure_id` phải trùng chính xác;
2. ưu tiên form code, checksum và tên biểu mẫu đã chuẩn hóa;
3. `official_procedure_code` được dùng để tăng độ chắc chắn, không thay thế
   procedure mapping;
4. candidate phải queue-approved, không phải seed/demo/quarantine, có URL chính
   thức, file thật và checksum đúng;
5. nhiều candidate khác checksum cùng đạt khóa chính xác sẽ bị chặn với
   `AMBIGUOUS_EXACT_CANDIDATES`.

Ngày hiệu lực chỉ được bổ sung từ
`notebook_data/forms/official_form_effectivity_evidence_v1.json`. Mỗi rule bắt
buộc có URL cơ quan nhà nước, căn cứ, ngày hiệu lực và exact procedure code hoặc
procedure ID. Các nguồn đã đối chiếu gồm Quyết định 1833/QĐ-BTP, Thông tư
53/2025/TT-BCA, Thông tư 05/2021/TT-TTCP, Nghị định 101/2024/NĐ-CP và Nghị định
175/2024/NĐ-CP. Allowlist catalog chấp nhận `bocongan.gov.vn` và các subdomain
vì đây là cổng chính thức của Bộ Công an; regression test giữ đồng nhất với
candidate preparation.

Những form không có exact candidate hoặc nguồn/file/effectivity phù hợp được
ghi `VERIFIED_DATA_GAP` với reason code. Reconciliation không thay đổi
`review_status`, `legal_review_status`, `approved`, runtime catalog, corpus,
embedding hoặc active collection.

Kết quả live sau khi restart API:

- 93 form đã được đối chiếu;
- 80 form chưa map ở baseline đã được thử ánh xạ;
- 10 form đạt toàn bộ hard gate và xuất hiện trong shortlist;
- 83 form vẫn fail-closed với `VERIFIED_DATA_GAP`;
- 0 form được automation phê duyệt hoặc runtime-promote;
- nút xác nhận Admin đã bật, nhưng chưa được bấm;
- `LEGAL_SECTION_GROUNDING_ENABLED=false`.

Artifact aggregate:
`reports/feature005/form-reconciliation-20260727.json`.
Shortlist dành cho người xác nhận:
`notebook_data/forms/legal_attestation_shortlist_v1.json`.

## Post-attestation gate (2026-07-27)

Admin đã xác nhận một batch gồm 10 biểu mẫu. Giao dịch đã đồng bộ nguyên tử
candidate queue, canonical catalog, procedure binding, official index, checksum
manifest và legal-review audit. Kết quả sau đồng bộ:

- 93 canonical forms;
- 10 runtime-approved forms và 10 approved bindings;
- 1 attestation, reviewer lấy từ phiên Admin;
- 83 forms còn lại tiếp tục fail-closed;
- 0 `sync_failed`, 0 form approved thiếu file/URL và 0 seed/demo/quarantine được
  phục vụ.

Regression hậu xác nhận sửa ba lớp:

1. planner giữ cụm “trẻ sinh ở nước ngoài” trong domain hộ tịch;
2. context 4.000 ký tự dành chỗ cho hai provision so sánh trên mỗi issue, tránh
   Điều 38 lấn mất Điều 37;
3. form resolver chỉ dùng top procedure mặc định; multi-procedure caller phải
   truyền `procedure_ids` đã plan, nên “cấp lại giấy phép” không rơi xuống form
   “cấp giấy phép” chung.

Gates kỹ thuật: backend 981/981, frontend 89/89, type-check/build pass, lint 0
error (11 warning), form/legacy/isolation focused 117/117, UI role cases 9/9,
privacy scan pass. API role cases đạt 8/9. Ca còn lại cần Nghị định
16/2022/NĐ-CP; live legal store chưa có document này. SourceGapJob đã tải PDF
chính thức từ Cổng văn bản Chính phủ, kiểm tra magic bytes, checksum và provenance,
nhưng giữ `candidate_pending_review`, không index/embedding và không tự duyệt.

Trạng thái là `POST_ATTESTATION_BLOCKED_RELEASE`, chưa phải release PASS.
`LEGAL_SECTION_GROUNDING_ENABLED=false`; active collection vẫn
`legal_chunks_lechan_primary_v20260723`.

### Source-gap review queue follow-up

The downloaded official PDF for `16/2022/NĐ-CP` was submitted idempotently to
the existing `legal_crawl_candidate` Admin queue. Live verification found that
the current SurrealDB schema strips nested keys from `uploaded_file`,
`extraction_result` and `raw_metadata`; the submitted row was therefore moved
to `changes_requested` instead of being left approvable. The source-gap store
continues to hold the checksum-verified PDF and provenance.

The PDF is also scan-only and the optional Vietnamese OCR adapter is unavailable
in the current runtime. A reviewed additive migration making the three candidate
metadata objects `FLEXIBLE`, followed by metadata restoration and OCR, is
required before this document can be presented for legal approval. No legal
approval, indexing, embedding, corpus rewrite or active-collection change was
performed.

## Đối chiếu 72 form chưa có candidate (2026-07-27)

Pipeline dò nguồn dùng API của Cổng Dịch vụ công Quốc gia và các catalog nguồn
Hải Phòng hiện có. Tên chính thức chỉ được dùng làm alias tìm kiếm đã rà soát;
không ghi đè metadata canonical và không dùng model để đoán mapping. Selector ưu
tiên exact action/subtype, lĩnh vực và cơ quan ban hành Hải Phòng khi có nhiều
thủ tục cùng tên.

Kết quả của đúng 72 form từng mang reason `CANDIDATE_NOT_FOUND`:

- 28 `VERIFIED_DATA_GAP`: thủ tục chính thức đã kiểm tra nhưng không công bố
  biểu mẫu/file trong thành phần hồ sơ;
- 13 `SOURCE_DOWNLOAD_RETRY_REQUIRED`: trang thủ tục có liệt kê biểu mẫu nhưng
  lần kiểm tra ngày 2026-07-27 không có file tải; mỗi form có SourceGapJob
  idempotent và chưa được giả lập đủ bảy ngày kiểm tra;
- 29 `SOURCE_MAPPING_UNRESOLVED`: chưa có một thủ tục chính thức duy nhất đủ
  chắc chắn để ánh xạ;
- 2 `SOURCE_MAPPING_UNRESOLVED` với reason
  `OFFICIAL_EFORM_REQUIRES_SEPARATE_GATE`: chỉ có eForm, không có file độc lập.

Sau reconciliation, preview Admin không còn reason chung
`CANDIDATE_NOT_FOUND`; mỗi form hiển thị reason cụ thể cùng URL nguồn chính thức
khi có. Các form này vẫn bị loại khỏi runtime. Có 0 auto-approval, 0 runtime
promotion, 0 corpus mutation, 0 embedding và 0 thay đổi active collection.

Lệnh tái lập:

```powershell
python scripts/discover_official_procedure_sources.py --rate-limit 0.05
python scripts/reconcile_canonical_form_candidates.py --legal-as-of 2026-07-27
python scripts/reconcile_canonical_form_candidates.py --apply --legal-as-of 2026-07-27
```

Artifact privacy-safe:
`reports/feature005/form-reconciliation-20260727.json`. SourceGapJob chỉ được
chuyển sang `verified_gap` sau bảy ngày kiểm tra khác nhau; hệ thống không tạo
ngày giả để vượt gate.

## Chiến dịch tự động hoàn thiện biểu mẫu còn lại (2026-07-27)

Trang Admin `legal-import` có nút **Tự động xử lý biểu mẫu còn lại**. Một lần
bấm khởi động worker nền theo chuỗi:

1. sao lưu catalog, binding, candidate queue, source findings và source-gap
   store;
2. làm mới nguồn thủ tục trên cổng cơ quan nhà nước;
3. giữ lại bằng chứng mạnh hơn từ lần kiểm tra trước, vì lỗi mạng hoặc kết quả
   tìm kiếm mơ hồ không được phép mở lại bản ghi đã xác minh không có mẫu;
4. tạo SourceGapJob ổn định theo form, ngày áp dụng và URL chính thức;
5. tải candidate, kiểm tra redirect, loại file giả, checksum và provenance;
6. chỉ đánh dấu `ready_for_human_review` khi procedure mapping, căn cứ và hiệu
   lực đều có bằng chứng chính thức;
7. chạy reconciliation và cập nhật preview. Worker không đổi
   `review_status=approved`, không runtime-serve và không tạo legal attestation.

Các gói PDF/DOCX chung chưa tách được đúng biểu mẫu, eForm chưa có gate riêng,
nguồn mơ hồ hoặc thiếu bằng chứng hiệu lực tiếp tục fail-closed. Admin chỉ bấm
**Xác nhận pháp lý hàng loạt** khi `ready_for_attestation > 0`.

Sau khi attestation thành công, giao diện tự khởi động worker kiểm tra
post-attestation gồm form catalog, full backend, frontend test/type/lint/build,
authorization/legacy/rollback, retrieval benchmark, 9 API role cases, 9 UI
role cases và generation warm concurrency 1/5. Nếu thiếu credential benchmark
hoặc bất kỳ cổng nào lỗi, trạng thái là `BLOCKED_RELEASE`; worker không bật
`LEGAL_SECTION_GROUNDING_ENABLED`.

Trạng thái và báo cáo của hai worker chỉ chứa run ID mờ, số lượng, reason code,
thời gian và PASS/FAIL/BLOCKED. Không lưu tên/URL biểu mẫu, câu hỏi, câu trả lời,
credential hay nội dung nguồn trong artifact chia sẻ.

Lần chạy thực đầu tiên phát hiện một regression: kết quả discovery mơ hồ đã làm
15 bản ghi `NO_PUBLIC_DOWNLOAD_VERIFIED` yếu đi thành `NEEDS_SOURCE_MAPPING`.
Regression test được thêm trước khi sửa; dữ liệu được khôi phục từ backup và
chạy lại. Kết quả an toàn sau sửa trở về 65 form active, 10 approved, 28
excluded-no-official và 55 unresolved, không có runtime promotion.

## Loại bản ghi không có biểu mẫu chính thức (2026-07-27)

Theo quyết định sản phẩm, bản ghi có kết quả
`NO_PUBLIC_DOWNLOAD_VERIFIED` kèm
`FORM_NOT_LISTED_IN_OFFICIAL_PROCEDURE` không còn được coi là một biểu mẫu
đang chờ bổ sung. Reconciliation chuyển chúng sang:

- `catalog_disposition=excluded_no_official_form`;
- `catalog_status=excluded_no_official_form`;
- `review_queue_eligible=false`;
- `runtime_eligible=false`;
- reason `NO_OFFICIAL_STATE_FORM_CONFIRMED`.

Đây là loại khỏi catalog phục vụ, không phải xóa dữ liệu. Tên, procedure, URL
nguồn đã kiểm tra, ngày đối chiếu và reason được giữ trong
`catalog_exclusion` và `candidate_source_evidence` để kiểm toán. Nếu một lần
đối chiếu sau tìm thấy file/eForm chính thức phù hợp, reconciliation có thể mở
lại bản ghi ở trạng thái candidate; vẫn phải qua hard gate và xác nhận pháp lý
của người dùng.

Kết quả áp dụng:

- 93 bản ghi kiểm toán;
- 65 biểu mẫu còn trong catalog đang quản lý;
- 10 biểu mẫu đã được xác nhận và runtime-approved;
- 28 bản ghi bị loại khỏi hàng chờ duyệt do không có mẫu được liệt kê tại thủ
  tục chính thức;
- 55 biểu mẫu còn cần xử lý: 29 cần tìm đúng nguồn, 11 có file nhưng chưa ghép
  đúng thủ tục, 13 trang gói đang thử tải lại và 2 eForm cần gate riêng;
- 0 biểu mẫu tự động được phê duyệt;
- corpus, embedding, active collection và feature flag không thay đổi.

Admin preview hiển thị riêng “Không có mẫu nhà nước” và không trộn 28 bản ghi
này vào số biểu mẫu cần xác nhận.

### Verification

- Backend: `1018 passed`, 13 warnings đã phân loại.
- Frontend: `90 passed`; TypeScript, lint và production build đều pass.
- UI live: 65 biểu mẫu đang quản lý, 55 cần bổ sung, 10 đã xác nhận, 28
  không có mẫu nhà nước; danh sách audit mở ra đúng 28 bản ghi.
- Nút xác nhận hàng loạt bị vô hiệu hóa khi `eligible_forms=0`.
- API readiness và retrieval health pass; active collection vẫn là
  `legal_chunks_lechan_primary_v20260723`.
- `.env` vẫn ghi `LEGAL_SECTION_GROUNDING_ENABLED=false`.
- Privacy regression: 8/8 pass.

## Tái kiểm tra tự động hằng ngày (2026-07-27)

Campaign được nối vào `legal_crawl_scheduler_loop` bằng cờ
`LEGAL_FORM_COMPLETION_ENABLED=true`. Scheduler chỉ khởi chạy tối đa một lần cho
mỗi `legal_as_of`; Admin vẫn có thể chạy lại thủ công trong cùng ngày. Worker
tiếp tục chỉ tìm nguồn chính thức, tải candidate, kiểm tra checksum/provenance,
đối chiếu procedure/effectivity và cập nhật preview. Không worker nào tạo
attestation, không tự promote runtime và không thay đổi corpus, embedding hoặc
active collection.

Kết quả chạy lại sau regression bảo toàn bằng chứng mạnh hơn:

- 65 form active; 10 đã approved; 28 đã loại vì không có mẫu nhà nước;
- 55 còn unresolved: 29 cần tìm đúng nguồn, 11 cần ghép file–thủ tục,
  13 package page cần thử tải lại và 2 eForm cần gate riêng;
- `ready_for_attestation=0`, `runtime promotion=0`;
- artifact aggregate: `reports/feature005/form-completion-campaign-20260727191911-b9cdaf18.json`;
- trạng thái: `data/form_completion_campaign/status_v1.json`.

Kiểm tra live trên `/legal-import` hiển thị đúng các số liệu trên và nút
**Xác nhận pháp lý hàng loạt** bị vô hiệu hóa khi chưa có form đạt hard gate.
API `/health` và retrieval đều khỏe; `/ready` vẫn `503` có chủ đích do
`provider_probe_failed` của DeepSeek. Vì vậy hệ thống tiếp tục
`BLOCKED_EXTERNAL`, giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`.

Verification sau thay đổi: backend `1030 passed`, frontend `91 passed`, type-check,
lint, build, campaign/release/privacy regressions và compileall đều pass.
