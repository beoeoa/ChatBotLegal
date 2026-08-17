# Kiểm tra vòng đời duyệt và kế hoạch cải thiện trang Legal Import

> Cập nhật P1 (2026-08-07): có hai kiểm tra sẵn sàng riêng. `GET /ready/import`
> chỉ yêu cầu database và legal retrieval/embedding; `GET /ready/answer` (và
> `/ready` tương thích ngược) yêu cầu thêm nhà cung cấp mô hình chat. Trình khởi
> động cục bộ chờ `/ready/import`, nên timeout mô hình trả lời không còn chặn
> vận hành nhập kho. Worker heartbeat/ETA vẫn là hạng mục tiếp theo.

> Cập nhật tiếp theo (2026-08-07): `GET /ready/import` hiện cũng yêu cầu
> heartbeat còn hạn của worker nhập kho. Hai endpoint duyệt/xếp lại job trả
> `503 import_pipeline_not_ready` và không ghi job mới khi một dependency không
> sẵn sàng. ETA theo hàng đợi vẫn là hạng mục quan sát tiếp theo.

> Giao diện Legal Import lấy trạng thái này mỗi 15 giây và khóa riêng các nút
> tạo/retry job import. Các quyết định không đưa dữ liệu vào kho (yêu cầu bổ
> sung, bỏ qua) vẫn thực hiện được để không làm tắc quy trình kiểm duyệt.

> Worker cũng ghi rõ từng job là `queued` hoặc `running`. Một job `running`
> quá hạn mặc định 30 phút sau sự cố/restart được đưa về `queued`, kèm lý do
> kiểm toán; không có văn bản/vector nào được kích hoạt bởi quá trình thu hồi.

> Cập nhật P2 (2026-08-07): API summary và trang Legal Import hiển thị số job
> chờ/chạy/hoàn tất/thất bại, thời gian chờ và xử lý trung bình từ timestamp
> thực, tỷ lệ hoàn tất, lỗi validation, xung đột trùng và số candidate đã kích
> hoạt. Các timestamp thiếu không được suy diễn. Thao tác retry cũng kiểm tra
> import readiness và duplicate preflight trước khi tạo job.
> Readiness có alias `/api/ready/import` cho proxy Next.js; khi API URL là
> relative, frontend dùng alias này thay vì gọi sai route gốc.

Ngày kiểm tra: 2026-08-07

## Phạm vi

Kiểm tra không ghi dữ liệu đối với trang `/legal-import`, luồng duyệt văn bản,
worker nhập kho/embedding, luồng duyệt biểu mẫu và các bề mặt phục vụ tra cứu.
Không có candidate nào được duyệt, nhập kho hoặc thay đổi trạng thái trong lần
kiểm tra này.

## Kết luận

Luồng **văn bản** đã có kiến trúc an toàn theo trạng thái:

`pending -> approved -> import_queued -> imported`

Nút duyệt chỉ xếp hàng. Worker nền mới chuẩn hóa, chia đoạn, tạo vector bằng
VNLegal-LAL, ghi cả hai collection và kích hoạt document. Chỉ document có
`activation_status=active`, article `status=active` và search scope `included=true`
mới được coi là phục vụ tra cứu.

Tuy nhiên hệ thống hiện **chưa đạt trạng thái vận hành hoàn chỉnh**:

- live database có 0 candidate văn bản ở trạng thái `imported`;
- có 1 `import_failed` vì số hiệu đã tồn tại trong kho;
- có 6 bản ghi `approved` cũ, phần lớn thiếu review/import metadata mới;
- readiness tổng trả 503 do model provider timeout, trong khi legal retrieval và
  VNLegal-LAL vẫn healthy trên CUDA;
- hai regression test của officer proposal/manual scan chưa đồng bộ với service;
- luồng **biểu mẫu** còn đường tắt legacy: sơ duyệt candidate có thể ghi
  `review_status=approved`, `is_canonical=true` và xuất hiện ở bề mặt official/prompt
  trước khi đi qua canonical legal attestation và toàn bộ hard gate.

Vì vậy câu trả lời vận hành là: bấm duyệt văn bản **không dùng ngay lập tức**.
Chỉ dùng được sau khi job hoàn tất và UI/API xác nhận `imported + active`. Bấm
duyệt biểu mẫu không tạo embedding; biểu mẫu phải đi qua catalog, binding,
effectivity, checksum và legal attestation trước khi được phục vụ.

## Sai lệch cần xử lý

### P0 - Loại bỏ đường tắt công khai biểu mẫu

1. Đổi nút `Duyệt` ở candidate biểu mẫu thành `Xác nhận phân loại và chuyển chờ pháp lý`.
2. Candidate sơ duyệt chỉ được chuyển sang `triaged` hoặc
   `ready_for_legal_attestation`; không được tự đặt `approved`, `is_canonical` hay
   `available_official_source`.
3. `_get_approved_form_ids`, official form API, download API và prompt context chỉ
   nhận form đã qua canonical `FormCatalog.gate_form` và attestation hợp lệ.
4. Ngừng dùng `build_official_form_context` legacy làm nguồn độc lập; mọi gợi ý form
   phải đi qua cùng canonical resolver đang tạo `recommended_forms`.
5. Lập báo cáo đối chiếu các candidate đang mang `approved`; không tự sửa dữ liệu.
   Việc backfill trạng thái cần backup, dry-run và phê duyệt riêng vì ảnh hưởng catalog.

Tiêu chí nghiệm thu:

- candidate sơ duyệt không xuất hiện trên API public, download hoặc prompt;
- thiếu một hard gate bất kỳ trả `forms_unavailable=true`;
- citizen/officer không nhận form thiếu binding, effectivity, checksum hoặc attestation;
- mọi thay đổi giữ provenance và audit.

### P0 - Chứng minh luồng văn bản từ duyệt đến phục vụ

1. Tạo integration test trong kho kiểm thử riêng: duyệt candidate hợp lệ, quan sát
   job `queued -> running -> completed`, kiểm tra vector ở cả hai collection và
   truy vấn lại có citation đúng.
2. Thêm preflight ngay trên card: nguồn chính thức, số hiệu, hiệu lực, nội dung/OCR,
   phạm vi và trùng lặp. Không cho xếp hàng nếu preflight chưa đạt.
3. Thêm worker heartbeat, thời điểm nhận job, số lần thử, lỗi cuối và thời lượng từng bước.
4. UI chỉ hiển thị `Có thể tra cứu` khi backend trả document active và search scope included.

Tiêu chí nghiệm thu:

- một candidate hợp lệ hoàn thành end-to-end và được tìm thấy bằng truy vấn kiểm tra;
- lỗi ở bất kỳ bước nào không để lại document/vector phục vụ một phần;
- trạng thái UI khớp trạng thái lưu trữ sau reload.

### P1 - Xử lý trùng văn bản trước khi duyệt

1. Preflight số hiệu, URL, checksum và content fingerprint trước khi Admin duyệt.
2. Nếu trùng, cho Admin chọn một trong các quyết định có kiểm soát: dùng bản hiện có,
   cập nhật phiên bản/thay thế sau khi kiểm tra, hoặc bỏ candidate.
3. Không tạo job chỉ để thất bại muộn vì số hiệu đã tồn tại.

Tiêu chí nghiệm thu:

- duplicate được giải thích trước khi duyệt;
- không ghi đè văn bản active âm thầm;
- quan hệ thay thế/sửa đổi có audit và effectivity rõ ràng.

### P1 - Một nguồn sự thật cho số liệu và trạng thái biểu mẫu

1. Badge tab dùng `summary.filtered_total`, không dùng độ dài danh sách bị giới hạn 200.
2. Thêm phân trang; giữ riêng số `chờ sơ duyệt`, `chờ xác nhận pháp lý`,
   `runtime-ready`, `bị loại` và `cần bổ sung`.
3. Hợp nhất các summary hiện đang lệch giữa classified queue, official index,
   priority supplement và canonical runtime catalog.

Tiêu chí nghiệm thu:

- số trên badge, filter và API giống nhau sau reload;
- không gọi danh sách đã duyệt là `Biểu mẫu cần duyệt`;
- không suy ra runtime-ready chỉ từ `review_status=approved`.

### P1 - Tách health theo khả năng

1. Giữ `/health` cho process liveness.
2. Tách `import_readiness` gồm database, legal retrieval, model embedding,
   vector collections và worker heartbeat.
3. Tách `answer_readiness` cho LLM provider. Provider timeout không được làm UI
   hiểu nhầm rằng embedding backend đã chết, nhưng vẫn phải hiển thị cảnh báo rõ.

Tiêu chí nghiệm thu:

- trang Admin biết chính xác subsystem nào đang lỗi;
- nút duyệt bị khóa khi import subsystem không sẵn sàng;
- lỗi LLM không che khuất trạng thái retrieval/embedding.

### P2 - Hoàn thiện kiểm thử và khả năng quan sát

1. Sửa hai regression test của officer proposal/manual scan để mock đúng boundary mới.
2. Thêm test đảm bảo candidate sơ duyệt không lọt vào legacy prompt/download.
3. Thêm test idempotency, restart worker, retry, duplicate và rollback hai collection.
4. Thêm dashboard thời lượng hàng đợi, tỷ lệ thành công, lỗi validation, duplicate,
   embedding và activation.

## Thứ tự triển khai đề xuất

1. Khóa đường tắt form legacy và thêm regression tests.
2. Chuẩn hóa state/count UI, đổi nhãn hành động để phân biệt sơ duyệt và xác nhận pháp lý.
3. Thêm duplicate preflight và worker/import readiness.
4. Chạy end-to-end trên kho kiểm thử, sau đó mới lập dry-run đối chiếu dữ liệu live.
5. Chỉ sau khi có backup và báo cáo đối chiếu mới đề xuất backfill các trạng thái legacy.

## Bằng chứng kiểm tra ngày 2026-08-07

- Focused backend lifecycle/source tests: 18 passed.
- Import guard và end-to-end service tests: 21 passed.
- Focused frontend tests: 8 passed.
- Officer proposal/manual scan suite: 27 passed, 2 failed do regression test/mocking
  chưa đồng bộ.
- Legal retrieval: healthy, VNLegal-LAL trên CUDA float16, 159,933 vector records.
- API readiness: 503 do DeepSeek provider timeout; database và legal retrieval healthy.
- Live crawler candidate: 0 pending, 6 approved legacy, 0 queued, 1 failed, 0 imported,
  6 rejected.
- Classified form queue API: 835 records; trạng thái và aggregate giữa các artifact
  hiện không nhất quán, cần đối chiếu trước mọi migration.

## Triển khai P0 ngày 2026-08-07

Đã triển khai lát cắt fail-closed, không thực hiện migration hay sửa dữ liệu
catalog hiện có:

- Sơ duyệt candidate chỉ lưu trạng thái `catalog_sync_required`; không sao chép
  tệp, không ghi official index/catalog, không cấp URL tải và không bật runtime.
- Upload biểu mẫu mới mặc định vào candidate queue; không còn đường upload trực
  tiếp ở trạng thái `approved`.
- Mọi legacy public surface chỉ công nhận form có trong canonical catalog, có
  binding đã duyệt và cờ runtime đầy đủ. Các bản ghi lịch sử chỉ có
  `review_status=approved` không còn đủ điều kiện.
- Đã loại việc chèn legacy form context vào prompt sinh câu trả lời. Danh sách
  biểu mẫu cho người dùng tiếp tục đi qua canonical resolver.
- UI đổi từ “Duyệt” sang “Xác nhận sơ bộ”, bỏ badge đếm theo danh sách giới hạn
  và giải thích rõ biểu mẫu chưa thể tải/tra cứu cho đến khi xác nhận pháp lý.
- Danh sách sơ duyệt dùng `filtered_total` do API trả về và phân trang 50 mục,
  nên không còn diễn giải một trang tối đa 200 mục là toàn bộ hàng đợi.
- Trước khi duyệt văn bản vào hàng import, hệ thống tra số hiệu trong kho runtime.
  Nếu đã có, candidate chuyển sang `duplicate_conflict`/`changes_requested` thay
  vì để worker embedding thất bại muộn.

## Bổ sung ràng buộc biểu mẫu (2026-08-07)

Resolver runtime nay đối chiếu thêm các cặp `procedure_id` + `form_id` với
identity có `release_status=APPROVED_RUNTIME` trong
`reports/feature006/form-requirement-manifest-2026-07-30.json`. Cặp chưa nằm
trong manifest không được trả ra, dù catalog/binding mang cờ `approved`; trace
trả `FORM_NOT_RELEASED_FOR_PROCEDURE`. Điều này ngăn biểu mẫu đã được xác nhận
về kỹ thuật nhưng không phải yêu cầu chính thức của thủ tục xuất hiện ở bề mặt
trả lời công khai.

Đường trả lời công khai và cấp gọi kiểm tra/kiểm duyệt đều nhận tối đa 12 mẫu
đã qua hard gate, để không mất mẫu bắt buộc thứ tư trở lên. Thành phần giao
diện có thể gom/thu gọn phần trình bày, nhưng không được bỏ dữ liệu.

Ma trận hồi quy sâu của catalog được giữ ở 52 thủ tục canonical (3.120 ca),
trong khi bộ phát hành 418 thủ tục kiểm tra riêng mapping chính xác. Không để
runtime bridge làm ma trận sâu phình lên 25.080 ca và che khuất lỗi hồi quy.

Kiểm chứng sau thay đổi:

- 45 kiểm thử về catalog, xác nhận pháp lý, gợi ý biểu mẫu và citation: đạt.
- 27 kiểm thử import/crawler: đạt.
- 10 kiểm thử giao diện/API liên quan: đạt.
- Python compile và kiểm tra whitespace cho phần thay đổi: đạt. Lint toàn bộ
  frontend còn 16 lỗi có sẵn tại các trang không thuộc lát cắt này.

## Bằng chứng import cách ly (2026-08-07)

`tests/test_legal_import_e2e.py` nay chạy trực tiếp
`LegalRetriever.import_document` trên harness cách ly (không kết nối database
hoặc Chroma đang vận hành). Kiểm thử xác nhận đúng thứ tự: ghi document/article/
scope ở `staging` → ghi vector vào cả hai collection → chỉ sau đó mới chuyển
document/article/search scope sang trạng thái phục vụ. Một collection vector thất
bại sẽ buộc rollback vector và document nháp; không được có activation.

Đây là bằng chứng code-level cho transaction boundary mà không ghi mẫu thử vào
corpus pháp luật thật. Kiểm chứng live end-to-end vẫn phải dùng một database/Chroma
test tách biệt, tạo bởi một quy trình release được phê duyệt, không dùng corpus
vận hành.

## Phân loại lỗi trùng lặp muộn (2026-08-07)

Một job cũ có thể được tạo trước khi duplicate preflight được bổ sung. Khi worker
nhận phản hồi import xác nhận số hiệu đã tồn tại, worker nay đánh dấu job là thất
bại để giữ audit trail nhưng chuyển candidate sang `changes_requested` với
`import_status=duplicate_conflict`. Vì vậy candidate không còn hiển thị nút retry
gây thất bại lặp lại; Admin phải đối chiếu bản đang phục vụ hoặc quan hệ sửa đổi/
thay thế trước khi quyết định tiếp.

Nếu Admin bấm retry trên một candidate lỗi lịch sử, endpoint recheck sẽ trả về
`duplicate_conflict` như một kết quả phân loại thành công, không phải lỗi 409 chung.
Giao diện chuyển trực tiếp sang bộ lọc “Cần bổ sung”, đồng thời có nút “Xem mục
cần xử lý” cạnh thống kê job lỗi để không che các bản ghi cần xử lý sau bộ lọc
“Chờ duyệt” mặc định.

## Hàng đợi cần xử lý của Admin (2026-08-07)

API candidate hỗ trợ trạng thái tổng hợp `needs_attention`, gồm `import_failed`,
`changes_requested` và candidate đã duyệt nhưng `import_status=validation_failed`.
Nút thống kê job lỗi và lựa chọn trên trang Legal Import đều dùng trạng thái này.
Vì vậy cảnh báo ở phần tổng quan luôn dẫn đến các bản ghi có hành động thực tế,
thay vì danh sách `pending` trống hoặc một phân đoạn lỗi bị che khuất.

## Tải danh sách duyệt có giới hạn (2026-08-07)

Danh sách candidate Admin không còn trả toàn văn OCR trong mỗi card. API chỉ trả
`content_characters` và giới hạn `extraction_result.preview` tối đa 2.000 ký tự.
Card phân biệt rõ nội dung toàn văn đã được ghi nhận với trường hợp chỉ có bản
xem trước. Việc giới hạn này không làm nhẹ các hard gate: candidate thiếu toàn
văn vẫn không thể được nhập kho.

## Xác nhận sau triển khai (2026-08-07)

Các kiểm tra hiện hành xác nhận luồng vận hành ở mức mã nguồn và môi trường cục bộ:

- Backend lifecycle/review/import trọng yếu: đạt 42 kiểm thử tập trung, gồm phân loại
  duplicate muộn, rollback khi collection vector thứ hai lỗi và chặn kích hoạt thiếu
  xác nhận đầy đủ.
- Frontend: lint, type-check, 113 kiểm thử và build production đều đạt.
- API `GET /ready/import` đang báo sẵn sàng khi database, retrieval/vector và worker
  nhập kho còn heartbeat; trạng thái trả lời của mô hình chat không còn chặn thao tác
  nhập kho.
- Worker import và OCR/extraction nhận job bằng cập nhật có điều kiện
  `queued -> running`. Nếu một worker khác đã nhận cùng job sau khi hàng đợi được đọc,
  worker còn lại không chạy import hoặc đọc lại tệp OCR; tránh xử lý trùng khi có nhiều
  process worker.
- Cú pháp cập nhật có điều kiện của cả hai hàng đợi đã được chạy thử trên SurrealDB cục
  bộ với một ID không tồn tại; database trả danh sách rỗng, xác nhận không có bản ghi
  nào được tạo hoặc thay đổi trong lần kiểm tra.
- Card candidate chỉ hiển thị “đã kích hoạt tra cứu” khi trạng thái nhập kho hoàn tất,
  backend đã trả `activation_status=active`, có document ID và ít nhất một vector chunk.
  Bản ghi lịch sử thiếu bằng chứng này hiển thị cảnh báo chỉ đọc, không được khẳng định
  là đang phục vụ tra cứu.
- Chỉ số dashboard `Đã kích hoạt` nay được tính từ cùng điều kiện bằng chứng đó, tách
  khỏi số bản ghi lịch sử mang trạng thái `imported`. Nếu còn bản ghi lịch sử chưa xác
  nhận active, Admin được dẫn tới bộ lọc đối chiếu thay vì số liệu bị gộp chung.
- Nếu xung đột trùng lặp chỉ xuất hiện sau preflight nhưng trước lúc ghi job, trạng thái
  `duplicate_conflict`/`changes_requested` được giữ nguyên. Nó không còn bị ghi đè thành
  `validation_failed`, nên Admin luôn nhận đúng hành động cần đối chiếu.
- Recovery sau crash nay áp dụng cho cả hai hàng đợi: import/embedding và OCR/extraction.
  Chỉ job `running` có thời điểm bắt đầu quá ngưỡng mới được đưa lại về `queued` kèm lý
  do kiểm toán; job mới hoặc thiếu timestamp không bị suy diễn hay tự thay đổi.

## Kiểm thử E2E PostgreSQL + Chroma tách biệt (2026-08-07)

Sau khi có phê duyệt vận hành, đã chạy phép thử thật trên database
`chatbotlegal_retrieval_staging` trống và hai Chroma collection tách biệt. Không sao
chép corpus pháp luật, dữ liệu người dùng, hay credential; chỉ sao chép phần schema
cần cho import và tạo một `legal_fields` cùng mẫu dữ liệu tổng hợp được gắn nhãn
`E2E TEST ONLY`.

- Luồng thành công qua `POST /import` tạo 1 document, 2 article và 2 chunk; API trả
  `embedded_active`/`active`. Đối chiếu trực tiếp cho thấy document/article đều
  `active`, `legal_search_scope.included=true`, và cả hai Chroma collection đều có
  2 vector. Sau đó `GET /documents/lookup` trả document đang active.
- Phép thử rollback dùng collection nguồn được cố ý tạo với vector dimension khác.
  Import trả lỗi 500; sau đó document và chunk của mẫu rollback bằng 0, collection
  chính bằng 0 vector và lookup trả 404. Seed dimension-mismatch duy nhất còn lại ở
  collection lỗi là dữ liệu kiểm thử tạo trước, không phải dữ liệu import dở dang.
- Dịch vụ test trên các cổng tách biệt đã được dừng sau khi kiểm thử. Các artifact
  tạm nằm trong `.tmp/legal-import-e2e-20260807` và không thuộc kho dữ liệu phục vụ.
- Trong phép thử phát hiện `GET /documents/lookup` trước đây bị tuyến động
  `/documents/{doc_id}` bắt mất. Đã đặt tuyến lookup trước tuyến động và bổ sung hồi
  quy `test_lookup_route_precedes_dynamic_document_route`; lookup theo số hiệu đã
  được kiểm chứng lại thành công trên API thật, bao gồm một văn bản active của kho
  đang phục vụ sau khi retrieval được khởi động lại.

Kiểm thử này xác nhận ranh giới retrieval thật (PostgreSQL + hai Chroma collection)
và việc chỉ kích hoạt sau embedding. Cầu nối candidate/worker sang endpoint import
vẫn được kiểm chứng bởi bộ test service; không tạo candidate, job SurrealDB hay
runtime import trong môi trường đang phục vụ.

## Hồi quy duyệt candidate và hard gate biểu mẫu (2026-08-07)

Đã sửa test duyệt candidate từng phụ thuộc ngầm vào số hiệu đang có trong kho runtime.
Test nay mock rõ ranh giới duplicate lookup và import queue, nên chỉ kiểm chứng đúng
hành vi cần có: duyệt hợp lệ ghi quyết định `approved`, sau đó tự xếp job và trả về
`import_queued`. Điều này không nới lỏng duplicate preflight ngoài môi trường test.

Nhóm kiểm tra review workflow, runtime form, legal attestation, stale-preview và
completion batch đạt 60/60. Các kiểm tra này xác nhận candidate sơ duyệt không được
đưa lên bề mặt runtime khi còn thiếu hard gate; trạng thái runtime vẫn đòi canonical
binding, checksum, effectivity và attestation hợp lệ.

Các regression của đề xuất văn bản từ cán bộ và quản lý/quét nguồn crawl cũng đã chạy
lại sau thay đổi boundary, đạt 20/20. Mốc “2 test lỗi” trong snapshot khảo sát đầu
tài liệu là kết quả lịch sử, không còn phản ánh trạng thái hiện hành.

Vì vậy, sau khi Admin bấm duyệt một candidate hợp lệ, hệ thống chỉ xếp job vào hàng
đợi. Candidate chỉ có thể được dùng để tra cứu khi worker hoàn tất embedding ở cả hai
collection và backend trả trạng thái `imported` cùng `active`. Candidate trùng hoặc
thiếu bằng chứng được chuyển vào nhóm cần xử lý, không được retry/nhập kho tự động.
