# Runbook hoàn tất chất lượng câu trả lời pháp luật

## 1. Mục đích

Runbook này giải quyết lần lượt các nguyên nhân đã xác định: truy xuất, tách yêu
cầu, dữ liệu thủ tục, biểu mẫu, model, kiểm chứng và hiển thị theo vai trò.

Một bước không phải là một lần chạy lệnh. Mỗi bước là một vòng khép kín:

```text
đo -> tái hiện lỗi -> viết regression test -> sửa -> chạy lại nhóm lỗi
    -> chạy lại toàn bộ cổng của bước -> PASS
```

Trạng thái hợp lệ chỉ gồm `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED_EXTERNAL` và
`PASS`. Không có trạng thái `DONE_WITH_ERRORS` hoặc `PASS_WITH_WARNINGS`.

- Lỗi code, test, cấu hình, retrieval, model contract, validator, renderer hoặc
  UI phải được sửa trong chính bước đang chạy.
- Thiếu nguồn pháp lý chính thức không được che bằng nội dung do model tạo. Bước
  giữ trạng thái `BLOCKED_EXTERNAL` cho đến khi có nguồn được phê duyệt.
- Toàn bộ kế hoạch chỉ được tuyên bố hoàn thành khi tất cả bước ở trạng thái
  `PASS` và có phê duyệt pháp lý bắt buộc.
- `LEGAL_SECTION_GROUNDING_ENABLED=false` trong quá trình sửa. Chỉ bật tạm trong
  môi trường nghiệm thu cô lập và phải khôi phục `false` nếu cổng thất bại.
- Không xóa corpus và không embedding lại toàn bộ kho. Dữ liệu mới, nếu có, chỉ
  được xử lý gia tăng bằng pipeline nhập hiện tại sau khi nguồn được xác minh.

## 2. Thứ tự thực hiện và cổng hoàn thành

### Bước 0 — Khóa môi trường và baseline

**Mục tiêu:** Loại trừ lỗi giả do môi trường và tạo một điểm đo có thể lặp lại.

**Công việc:**

1. Xác minh SurrealDB, retrieval, API và frontend dùng đúng cấu hình local/test.
2. Xác minh test không đọc credential runtime và feature flag đang `false`.
3. Chạy full backend, frontend test, type-check và các test rollback.
4. Lưu số lượng test, commit hiện tại, trạng thái dịch vụ và timing baseline
   dưới dạng tổng hợp không chứa câu hỏi/câu trả lời.
5. Mọi test đỏ phải được tái hiện độc lập và sửa ngay trong bước này.

**Cổng PASS:**

- Backend và frontend test xanh 100%.
- Readiness của database, retrieval và API đạt.
- Flag-off giữ đúng luồng cũ.
- Không có test phụ thuộc ngầm vào database runtime hoặc fixture fallback.

**Prompt thực thi:**

```text
Thực hiện Bước 0 của runbook chất lượng trong J:\ChatBotLegal. Giữ
LEGAL_SECTION_GROUNDING_ENABLED=false. Kiểm tra cấu hình test tách khỏi runtime,
readiness của SurrealDB/retrieval/API/frontend, chạy full backend, frontend,
type-check và rollback tests. Nếu có lỗi, không dừng ở việc liệt kê lỗi: tái hiện
lỗi nhỏ nhất, viết hoặc sửa regression test, sửa nguyên nhân gốc và chạy lại cho
đến khi toàn bộ cổng Bước 0 xanh. Không sửa hoặc xóa dữ liệu pháp luật. Chỉ báo
PASS khi mọi cổng đạt; nếu bị chặn bởi hệ thống ngoài, báo BLOCKED_EXTERNAL cùng
đúng dependency cần cung cấp, không báo hoàn thành.
```

### Khối DB — Tối ưu kho phục vụ phường/xã tại Lê Chân

Khối DB phải hoàn thành trước Bước 1. Trình tự bắt buộc:

```text
DB-1 backup/manifest
  -> DB-2 phân tầng shadow
  -> DB-3 duyệt coverage
  -> DB-4 tạo collection subset
  -> DB-5 exact lookup + retrieval theo tầng
  -> Bước 1 kiểm định 167 câu
```

Không xóa hoặc sửa corpus gốc. Tập 25.426 văn bản tiếp tục được giữ trong
PostgreSQL để tra cứu lịch sử, kiểm toán và rollback. Chỉ thay đổi tập được phép
tham gia retrieval mặc định.

Baseline đã xác minh:

| Hạng mục | Số lượng |
|---|---:|
| Văn bản gốc | 25.426 |
| Văn bản active, áp dụng tại thời điểm kiểm tra | 20.363 |
| Văn bản core hiện tại | 6.034 |
| Chunk trong PostgreSQL | 544.078 |
| Vector trong collection core | 159.530 |
| Seed có lý do ưu tiên trực tiếp | 940 văn bản / 32.165 chunk |

Quy mô thiết kế ban đầu:

| Tầng | Văn bản mục tiêu | Chunk/vector mục tiêu |
|---|---:|---:|
| `primary` | 1.200–1.800 | 35.000–60.000 |
| `support` | 1.500–3.000 | 40.000–90.000 |
| `historical_quarantine` | Phần còn lại | Không tìm mặc định |

Đây là cửa sổ thiết kế, không phải quota. Không được loại nguồn bắt buộc chỉ để
đạt con số nhỏ hơn.

#### Bước DB-1 — Backup và khóa manifest baseline

**Mục tiêu:** Có khả năng đối chiếu và rollback trước mọi thay đổi scope hoặc
collection.

**Công việc:**

1. Xác minh kết nối PostgreSQL và Chroma đang được runtime sử dụng.
2. Sao lưu PostgreSQL, collection core, collection source và active pointer.
3. Ghi manifest gồm:
   - ngày đánh giá;
   - số văn bản theo trạng thái/hiệu lực;
   - số article/chunk/vector;
   - collection name;
   - checksum danh sách document/chunk/vector ID;
   - commit và cấu hình retrieval không chứa credential.
4. Thử đọc manifest và kiểm tra collection rollback.
5. Giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`.

**Cổng PASS:**

- Backup tồn tại và đọc lại được.
- Count/checksum khớp database và Chroma đang phục vụ.
- Collection cũ và active pointer chưa bị thay đổi.
- Không có credential, câu hỏi hoặc câu trả lời trong artifact chia sẻ.

**Prompt thực thi:**

```text
Thực hiện Bước DB-1 trong J:\ChatBotLegal. Chỉ đọc và sao lưu, không sửa corpus,
scope hoặc active collection. Xác minh PostgreSQL/Chroma thật mà runtime đang
dùng; ghi manifest versioned gồm legal_as_of, document/article/chunk/vector
counts, collection names, checksum các ID, commit và cấu hình retrieval đã loại
credential. Sao lưu collection core, source và active pointer, sau đó thử đọc
lại backup. Giữ LEGAL_SECTION_GROUNDING_ENABLED=false. Nếu count hoặc checksum
không khớp, tiếp tục tìm nguyên nhân và sửa quy trình backup; không báo PASS.
Chỉ PASS khi rollback artifact được chứng minh sử dụng được và dữ liệu gốc
không thay đổi.
```

#### Bước DB-2 — Phân tầng toàn bộ văn bản ở chế độ shadow

**Mục tiêu:** Mọi văn bản có một quyết định deterministic: `primary`, `support`
hoặc `historical_quarantine`.

**Hard gate cho `primary`/`support`:**

1. Nguồn chính thức hoặc bản ghi đã được duyệt.
2. Xác định được danh tính văn bản và URL/provenance.
3. Xác định được hiệu lực và áp dụng tại `legal_as_of`.
4. Có lĩnh vực, phạm vi và thẩm quyền áp dụng.
5. Không bị thay thế toàn bộ hoặc xung đột với nguồn cấp cao hơn.
6. Có article/chunk hợp lệ, không rỗng và không orphan.
7. Thuộc pháp luật trung ương áp dụng chung, Hải Phòng hoặc Lê Chân.

**Seed `primary`:**

- `central_commune_specific`;
- `hai_phong_commune_specific`;
- `hai_phong_relevant_domain`;
- `current_core_law`;
- `related_current_instrument`;
- `manual_verified_import`;
- expected source đã duyệt của 167 golden/9 role cases;
- nguồn chính thức của 202 thủ tục và catalog biểu mẫu đã duyệt.

Chỉ mở rộng tối đa một hop cho quan hệ sửa đổi, bổ sung, thay thế hoặc hướng
dẫn/quy định chi tiết. Không mở rộng tự động theo toàn bộ quan hệ `Văn bản căn
cứ`.

**Công việc:**

1. Tạo manifest shadow trước; bảng sidecar chỉ được tạo sau phê duyệt schema.
2. Đánh giá 100% văn bản và chunk bằng rule deterministic.
3. Chuẩn hóa reason code vào/ra; không dùng model để đoán metadata.
4. Chunk rỗng, trùng, orphan hoặc thuộc văn bản không đạt hard gate vào
   `historical_quarantine`.
5. Không đổi collection đang phục vụ.

**Cổng PASS:**

- 100% văn bản/chunk được phân tầng, không có trạng thái `unknown`.
- Mọi exclusion có reason code và truy ngược được về metadata.
- Không có văn bản hết hiệu lực, sai phạm vi hoặc thiếu provenance trong
  `primary`/`support`.
- Không sửa imported tables hoặc active collection.

**Prompt thực thi:**

```text
Thực hiện Bước DB-2 theo le-chan-serving-corpus-plan.md ở chế độ shadow. Bắt đầu
từ sáu reason ưu tiên trực tiếp, expected sources của 167 golden/9 role cases và
nguồn 202 thủ tục/biểu mẫu đã duyệt. Áp dụng hard gate nguồn, hiệu lực, phạm vi,
lĩnh vực, thứ bậc, article/chunk và provenance. Chỉ mở rộng một hop cho quan hệ
sửa đổi, bổ sung, thay thế, hướng dẫn/quy định chi tiết; không dùng toàn bộ quan
hệ Văn bản căn cứ. Phân tầng mọi document/chunk thành primary, support hoặc
historical_quarantine với reason code deterministic. Không dùng model đoán
metadata, không xóa/sửa imported rows và không đổi active collection. Nếu còn
unknown hoặc primary/support chứa bản ghi không đạt hard gate, tiếp tục sửa rule
và chạy lại toàn bộ; không báo PASS. Việc tạo bảng sidecar phải chờ phê duyệt
schema, nhưng manifest shadow phải hoàn thành và kiểm chứng được.
```

#### Bước DB-3 — Duyệt coverage trước khi thu hẹp

**Mục tiêu:** Chứng minh collection nhỏ vẫn giữ đủ nguồn cần thiết cho nghiệp vụ
phường/xã tại Lê Chân.

**Công việc:**

1. Đối chiếu scope shadow với:
   - 167 câu golden;
   - 202 thủ tục ưu tiên;
   - 9 tình huống citizen/officer/admin;
   - catalog biểu mẫu approved có file/URL;
   - nguồn trung ương và Hải Phòng bắt buộc.
2. Với mỗi source bị loại, ghi rõ:
   - không liên quan;
   - hết hiệu lực/bị thay thế;
   - metadata thiếu;
   - nguồn sai phạm vi;
   - duplicate/canonical;
   - chuyển `support` thay vì `primary`.
3. Luật/nghị định/thông tư điều chỉnh trực tiếp không được loại chỉ vì ít xuất
   hiện trong câu hỏi.
4. Người rà soát pháp lý duyệt expected source, forbidden source và quan hệ
   trung ương–Hải Phòng.
5. Sửa scope ngay trong bước cho đến khi không còn missing source đã có trong
   corpus.

**Cổng PASS:**

- 100% expected source đã duyệt nằm trong `primary` hoặc `support`.
- 100% thủ tục ưu tiên có ít nhất căn cứ điều chỉnh trực tiếp hoặc trạng thái
  data gap được xác minh.
- Không có biểu mẫu seed-only/link hỏng được coi là official.
- Không còn nguồn bắt buộc bị loại sai.
- Có phê duyệt pháp lý cho scope trước khi chuyển collection.

**Prompt thực thi:**

```text
Thực hiện Bước DB-3 bằng cách so scope shadow với 167 golden, 202 thủ tục ưu
tiên, 9 role cases và catalog biểu mẫu approved. Với từng expected source, kiểm
tra document/provision, hiệu lực, phạm vi, legal hierarchy và tier. Với nguồn bị
loại, ghi một reason chính xác; không coi ít được hỏi là lý do loại luật điều
chỉnh trực tiếp. Nếu expected source có trong corpus nhưng bị loại hoặc xếp sai
tier, sửa rule/manifest và chạy lại toàn bộ đối chiếu ngay trong bước này. Nếu
corpus thật sự thiếu, ghi VERIFIED_DATA_GAP có bằng chứng cho Bước 2; không bịa
nguồn. Không báo PASS trước khi mọi nguồn có sẵn được phân tầng đúng và người rà
soát pháp lý đã duyệt expected/forbidden sources.
```

#### Bước DB-4 — Tạo collection `primary` và `support` bằng vector sẵn có

**Mục tiêu:** Tạo kho phục vụ nhỏ, versioned và rollback được mà không embedding
lại toàn bộ corpus.

**Công việc:**

1. Tạo tên collection versioned, ví dụ:

   ```text
   legal_chunks_lechan_primary_v1
   legal_chunks_lechan_support_v1
   ```

2. Copy vector và metadata eligible từ collection hiện có; không recompute
   embedding nếu vector đã tồn tại.
3. Chỉ embedding gia tăng cho nguồn chính thức mới chưa có vector và đã qua
   ingestion/quality gate.
4. Kiểm tra:
   - vector count khớp manifest;
   - document/chunk ID tồn tại;
   - không orphan/stale;
   - duplicate trỏ về canonical;
   - metadata request-time cần thiết vẫn đủ.
5. Không chuyển active pointer.

**Cổng PASS:**

- Count/checksum hai collection mới khớp manifest.
- Không có orphan, stale, empty hoặc duplicate không canonical.
- Collection cũ không thay đổi.
- Không có full-corpus embedding.
- Chạy thử ANN/hydration thành công trên từng domain.

**Prompt thực thi:**

```text
Thực hiện Bước DB-4 sau khi DB-3 PASS và schema/manifest đã được phê duyệt. Tạo
hai collection versioned primary/support bằng cách copy embeddings, documents
và metadata eligible từ collection hiện có; không embedding lại toàn bộ corpus.
Chỉ dùng ingestion gia tăng cho văn bản chính thức mới chưa có vector. Kiểm tra
count/checksum với manifest, document/chunk linkage, orphan, stale, empty,
duplicate/canonical và thử ANN/hydration theo từng domain. Không đổi active
pointer. Nếu có một ID hoặc checksum không khớp, sửa builder và tạo lại đúng
collection version đang thử; không báo PASS. Chỉ PASS khi collection mới độc
lập, đầy đủ, kiểm chứng được và collection rollback giữ nguyên.
```

#### Bước DB-5 — Exact lookup, retrieval theo tầng và shadow comparison

**Mục tiêu:** Bảo đảm văn bản đúng được tìm trước, collection nhỏ nhanh hơn và
không giảm recall.

**Công việc:**

1. Thêm exact lookup deterministic cho:
   - số hiệu văn bản đã chuẩn hóa;
   - điều/khoản/điểm;
   - mã thủ tục;
   - tên/mã biểu mẫu.
2. Exact lookup chạy trước ANN và không broad `LIKE` trên nội dung chunk.
3. Luồng semantic:
   - tìm `primary` trước;
   - eligibility/hierarchy/diversity;
   - `support` chỉ chạy một lần cho đúng issue/facet còn thiếu.
4. Ngân sách benchmark ban đầu:
   - primary: 150 vector + 60 lexical;
   - rerank tối đa 40;
   - support: 100 vector + 40 lexical;
   - tối đa 2 chunk cùng điều và 3 chunk cùng văn bản.
5. Chạy shadow old-core/new-serving trên cùng truy vấn; chưa đổi active pointer.
6. Viết regression test cho số hiệu chính xác, văn bản sai lĩnh vực, văn bản hết
   hiệu lực, nguồn ngoại giao trong câu hộ tịch nội địa và văn bản cũ vượt nguồn
   hiện hành.

**Cổng PASS:**

- Exact law number/provision/procedure/form trả đúng bản ghi trước semantic.
- Recall@10 >=95%.
- Direct source top 5 >=95%.
- Wrong-field/expired selection = 0.
- P95 retrieval <=3 giây.
- Kết quả new-serving không giảm coverage so với old-core.
- Active pointer vẫn chưa đổi.

**Prompt thực thi:**

```text
Thực hiện Bước DB-5 bằng test-first. Viết regression cho exact law number,
article/clause, procedure ID, form code, wrong-field, expired/superseded,
domestic civil-status versus foreign-representation sources và current versus
old instruments. Implement exact lookup trước ANN bằng normalized indexed
metadata; không broad LIKE trên chunk content. Truy vấn primary trước và chỉ gọi
support đúng một lần cho facet thiếu. Bắt đầu với ngân sách 150/60, rerank 40,
support 100/40 và diversity 2 chunk/điều, 3 chunk/văn bản. Chạy shadow old-core
và new-serving; không đổi active pointer. Nếu Recall@10 <95%, direct-source
top5 <95%, có wrong-field/expired hoặc P95 >3 giây, tiếp tục sửa exact lookup,
filter/ranking/budget và chạy lại toàn bộ gate; không dừng ở báo cáo lỗi. Chỉ
PASS khi mọi cổng đạt và coverage không giảm.
```

### Bước 1 — Giải quyết truy xuất và tách yêu cầu trên 167 câu

**Mục tiêu:** Phân biệt chính xác “nguồn có nhưng không tìm ra” với “kho thực sự
thiếu nguồn”, đồng thời sửa hết lỗi retrieval có thể sửa bằng code.

**Điều kiện bắt đầu:** DB-1 đến DB-5 đã `PASS`. Bước 1 chạy trên collection
new-serving ở chế độ shadow và tiếp tục so sánh với old-core; chưa rollout.

**Công việc:**

1. Chạy 167 câu ở tầng issue planner và retrieval trước, chưa gọi model.
2. Với từng issue, kiểm tra:
   - intent/facet bắt buộc;
   - nguồn điều chỉnh trực tiếp có tồn tại trong kho hay không;
   - thứ hạng nguồn trong top 5/top 10;
   - hiệu lực, lĩnh vực, phạm vi và thứ bậc pháp lý;
   - nguồn sai lĩnh vực, hết hiệu lực hoặc trùng lặp bị loại.
3. Phân loại duy nhất:
   - `FOUND_AND_RETRIEVED`;
   - `FOUND_NOT_RETRIEVED` — lỗi kỹ thuật, phải sửa trong Bước 1;
   - `VERIFIED_DATA_GAP` — đã kiểm tra kho nhưng không có nguồn hợp lệ, chuyển
     thành manifest đầu vào bắt buộc cho Bước 2.
4. Với mọi `FOUND_NOT_RETRIEVED`, viết regression test trước khi sửa planner,
   hybrid search, RRF, intent filter, adjacent-clause expansion hoặc diversity.
5. Chạy lại lĩnh vực bị ảnh hưởng, sau đó chạy lại toàn bộ 167 câu.
6. Đo retrieval độc lập, không dùng thời gian model để che sai số.

**Cổng PASS:**

- 100% issue được phân loại, không còn trạng thái không rõ nguyên nhân.
- Recall@10 của nhóm có nguồn hợp lệ đạt ít nhất 95%.
- Văn bản điều chỉnh chính nằm top 5 ở ít nhất 95% câu có nguồn.
- Không chọn nguồn sai lĩnh vực, hết hiệu lực hoặc sai request/issue.
- P95 retrieval không quá 3 giây.
- Tất cả `FOUND_NOT_RETRIEVED` đã được sửa; manifest data gap có bằng chứng kiểm
  tra và không bị tính nhầm thành lỗi retrieval.

**Prompt thực thi:**

```text
Thực hiện trọn Bước 1 trên bộ 167 câu golden của Feature 005. Chạy retrieval-only
để tiết kiệm thời gian, không gọi model và không fallback sang fixture khi live
retrieval lỗi. Với mỗi issue hãy xác định intent, nguồn mong đợi, nguồn có thật
trong kho, rank top 5/top 10, hiệu lực, lĩnh vực và lý do chọn/loại. Phân loại
chính xác FOUND_AND_RETRIEVED, FOUND_NOT_RETRIEVED hoặc VERIFIED_DATA_GAP.
Không dừng sau khi xuất báo cáo lỗi. Với mọi FOUND_NOT_RETRIEVED, viết regression
test, sửa nguyên nhân nhỏ nhất trong issue planner/retrieval/ranking/eligibility,
chạy lại nhóm lĩnh vực rồi chạy lại cả 167 câu cho đến khi Recall@10 >=95%,
direct-source top5 >=95%, wrong-field/expired selection = 0 và P95 retrieval
<=3 giây. Không embedding lại corpus. Xuất manifest VERIFIED_DATA_GAP đã kiểm
chứng để Bước 2 xử lý. Chỉ đánh dấu Bước 1 PASS khi không còn lỗi retrieval chưa
được giải quyết.
```

### Bước 2 — Lấp đầy dữ liệu thủ tục và biểu mẫu

**Mục tiêu:** Mọi khoảng trống đã xác minh ở Bước 1 có nguồn chính thức hoặc
được xác nhận ngoài phạm vi phát hành.

**Công việc:**

1. Đối chiếu manifest data gap với nguồn trung ương và nguồn Hải Phòng chính
   thức.
2. Bổ sung hồ sơ, nơi nộp, trình tự, thời hạn, lệ phí, điều kiện và ngoại lệ chỉ
   khi có URL, hiệu lực, phạm vi và căn cứ.
3. Mỗi biểu mẫu phải có `procedure_id`, tên chuẩn, trạng thái `approved`, nguồn
   chính thức, căn cứ áp dụng và file/URL tải được.
4. Bản ghi seed, thiếu file hoặc thiếu provenance không được quảng bá là biểu mẫu
   chính thức.
5. Chạy ingestion gia tăng và kiểm tra orphan/stale record; không rebuild hoặc
   embedding lại toàn bộ collection.
6. Chạy lại toàn bộ ca `VERIFIED_DATA_GAP` và retrieval gate của Bước 1.

**Cổng PASS:**

- Mọi dữ liệu được sử dụng có provenance và hiệu lực kiểm chứng được.
- Không còn biểu mẫu giả, seed-only hoặc link hỏng trong runtime.
- Ca thực sự không có nguồn phải trả `forms_unavailable`/thiếu căn cứ an toàn và
  được loại khỏi tiêu chí “kho có dữ liệu”, không được model tự điền.
- Retrieval gate của Bước 1 vẫn đạt sau khi bổ sung dữ liệu.

**Prompt thực thi:**

```text
Thực hiện Bước 2 từ manifest VERIFIED_DATA_GAP của Bước 1. Chỉ sử dụng nguồn
pháp luật hoặc thủ tục chính thức có URL, hiệu lực, phạm vi và căn cứ. Đối với
biểu mẫu, bắt buộc có procedure_id, tên chuẩn, approved, nguồn chính thức, căn
cứ và file/URL tải được. Không coi seed hoặc metadata thiếu file là mẫu chính
thức. Không bịa dữ liệu, không xóa corpus và không embedding lại toàn bộ kho;
chỉ nhập gia tăng qua pipeline hiện có. Sau mỗi bổ sung, kiểm tra orphan/stale,
chạy lại ca thiếu và toàn bộ retrieval gate. Không dừng ở danh sách nguồn thiếu.
Chỉ PASS khi các nguồn có thể bổ sung đã hoạt động trong runtime và mọi trường
hợp chưa có nguồn được hệ thống xử lý fail-closed đúng hợp đồng.
```

### Bước 3 — Hoàn thiện câu trả lời có cấu trúc và kiểm chứng claim

**Mục tiêu:** Hệ thống không chỉ nêu điều luật mà giải quyết đủ việc người dùng
hỏi, trong giới hạn nguồn đã kiểm chứng.

**Công việc:**

1. Lập ma trận coverage cho từng issue: kết luận, điều kiện, thẩm quyền/nơi nộp,
   hồ sơ, trình tự, thời hạn, lệ phí, ngoại lệ, việc tiếp theo, biểu mẫu và căn
   cứ.
2. Gọi model một lần cho toàn câu hỏi với evidence đã chọn theo issue.
3. Model chỉ trả JSON gồm claim nguyên tử; mỗi claim có `claim_type`,
   `evidence_id` và `support_quote`.
4. Backend xác minh quote tồn tại trong evidence, nguồn đúng request/issue,
   điều-khoản đúng metadata và mọi con số/thời hạn/lệ phí/thẩm quyền xuất hiện
   trong nguồn.
5. Claim không đạt bị loại riêng. Không gọi model lần hai để “sửa” căn cứ.
6. Renderer dựng câu trả lời và citation deterministic.

**Prompt gửi model ở runtime:**

```text
Bạn là trợ lý tổng hợp pháp luật dựa hoàn toàn trên EVIDENCE được cung cấp.
Không sử dụng kiến thức bên ngoài EVIDENCE. Không suy đoán số hiệu, điều khoản,
thẩm quyền, hồ sơ, thời hạn, lệ phí, biểu mẫu, quyền hoặc nghĩa vụ.

Với từng ISSUE:
1. Trả lời đúng các mục coverage được yêu cầu.
2. Tách thành các claim nguyên tử.
3. Mỗi claim pháp lý phải có đúng evidence_id và support_quote nguyên văn có
   trong evidence của chính issue đó.
4. Nếu evidence chỉ chứng minh một phần, chỉ trả phần đã chứng minh và ghi đúng
   mục còn thiếu; không biến hướng dẫn tham khảo thành kết luận pháp lý.
5. Chỉ hỏi một câu làm rõ nếu dữ kiện đó làm thay đổi luật áp dụng.
6. Không tạo Markdown, citation marker, chunk ID hoặc URL mới.

Chỉ trả JSON hợp lệ theo schema:
{
  "issues": [{
    "issue_id": "string",
    "claims": [{
      "claim_type": "conclusion|condition|authority|documents|procedure|deadline|fee|exception|next_action|warning|form",
      "text": "string",
      "evidence_id": "string",
      "support_quote": "string"
    }],
    "missing_items": ["string"],
    "clarifying_question": "string|null"
  }]
}
```

**Prompt thực thi Bước 3:**

```text
Thực hiện Bước 3 bằng structured JSON contract và một lần gọi model cho toàn câu
hỏi. Tạo coverage matrix cho mọi issue. Viết regression test cho quote không tồn
tại, evidence sai request/issue, sai điều-khoản, con số/thời hạn/lệ phí không có
trong nguồn, JSON lỗi và model timeout. Không dừng khi test chỉ ra claim sai:
sửa model contract, validator hoặc renderer, chạy lại ca lỗi, nhóm liên quan và
toàn bộ quality suite. Không dùng model-repair lần hai. Chỉ PASS khi 100% claim
pháp lý hiển thị có evidence hợp lệ và coverage >=90% trên các mục mà kho có
nguồn.
```

### Bước 4 — Ổn định tốc độ và fallback

**Mục tiêu:** Đạt thời gian phát hành mà không giảm độ chính xác.

**Công việc:**

1. Giới hạn context khoảng 7.000 ký tự và output tối đa 2.048 token hoặc thấp hơn
   nếu quality gate vẫn đạt.
2. Loại breadcrumb, chunk trùng và evidence không được chọn.
3. Cache embedding/retrieval; expanded retrieval chỉ chạy cho issue thiếu nguồn.
4. Giữ hard deadline generation 24 giây.
5. Timeout trả fallback trích nguồn an toàn, không gọi repair.
6. Benchmark cold/warm concurrency 1 và 5; 10/20 báo riêng theo hợp đồng hiện có.

**Cổng PASS:**

- P95 retrieval không quá 3 giây.
- P95 generation không quá 24 giây.
- P95 end-to-end không quá 30 giây ở warm concurrency 1.
- Concurrency 5 không lỗi.
- Quality gate của Bước 3 không giảm.

**Prompt thực thi:**

```text
Thực hiện Bước 4 với timing thật theo retrieval, provisioning, generation,
validation và end-to-end. Tối ưu context, duplicate evidence, cache và expanded
retrieval nhưng sau mỗi thay đổi phải chạy lại quality gate Bước 3. Nếu benchmark
không đạt, tiếp tục tìm và sửa nút thắt trong phạm vi hiện tại; không chỉ báo
'model chậm'. Giữ deadline generation 24 giây và fallback fail-closed, không gọi
repair. Không tự đổi model mặc định hoặc thêm dịch vụ trả phí. Chỉ PASS khi đạt
toàn bộ latency gate và concurrency 5 không lỗi. Nếu model mặc định vẫn không
đạt sau tối ưu, giữ BLOCKED_EXTERNAL/DECISION_REQUIRED và không bật feature.
```

### Bước 5 — Hoàn thiện trải nghiệm ba vai trò

**Mục tiêu:** Cùng một căn cứ nhưng nội dung thực hành phù hợp từng vai trò.

**Cổng nội dung:**

- Người dân: kết luận ngắn, chuẩn bị gì, làm ở đâu, các bước, thời gian/chi phí,
  mẫu tải và lưu ý.
- Cán bộ: thẩm quyền, thứ bậc căn cứ, quy trình nghiệp vụ, hồ sơ/biểu mẫu, dữ
  kiện cần xác minh.
- Admin: toàn bộ nội dung pháp lý cùng coverage, nguồn chọn/loại, lý do,
  provenance biểu mẫu, diversity và timing.

**Prompt thực thi:**

```text
Thực hiện Bước 5 trên API, SSE, history và giao diện cho citizen, officer, admin.
Dùng cùng evidence đã kiểm chứng; renderer theo vai trò không được thêm claim
pháp lý mới. Viết test cho role leakage, trace leakage, citation marker,
forms_unavailable và legacy client. Chạy 9 tình huống qua API và in-app browser.
Với mỗi lỗi, viết regression test, sửa đúng lớp gây lỗi rồi chạy lại ca lỗi và
cả 9 ca. Không kết thúc bằng báo cáo 'còn lỗi'. Chỉ PASS khi 9/9 không có lỗi
nghiêm trọng, không lộ ID nội bộ và nội dung mỗi vai trò đạt coverage contract.
```

### Bước 6 — Nghiệm thu cuối và rollout

**Mục tiêu:** Chỉ bật tính năng sau khi toàn bộ cổng kỹ thuật và pháp lý đạt.

**Công việc và cổng PASS:**

1. Full backend, frontend, type-check, retrieval, authorization, privacy,
   migration/rollback và browser tests xanh.
2. 167 câu retrieval đạt cổng Bước 1.
3. 9/9 câu API và 9/9 câu giao diện đạt cổng Bước 3–5.
4. Không có hallucination nghiêm trọng, citation hỏng hoặc biểu mẫu giả.
5. Người rà soát pháp lý duyệt nguồn và đáp án.
6. Bật lần lượt Admin, Cán bộ, Người dân; sau mỗi tầng chạy smoke test và kiểm tra
   trace. Bất kỳ cổng nào đỏ phải đưa flag về `false`.

**Prompt thực thi:**

```text
Thực hiện Bước 6 như một release gate, không phải một lượt thu thập lỗi. Chạy
toàn bộ test và nghiệm thu đã định nghĩa. Nếu lỗi kỹ thuật xuất hiện, quay về
đúng bước sở hữu lỗi, sửa, chạy regression và chạy lại toàn bộ Bước 6; không
đánh dấu T038, T039 hoặc T042 hoàn thành khi còn cổng đỏ. Không coi automated
test là phê duyệt pháp lý. Sau khi có phê duyệt, bật cô lập theo thứ tự Admin,
Cán bộ, Người dân và smoke-test mỗi tầng. Bất kỳ lỗi nghiêm trọng nào phải
rollback LEGAL_SECTION_GROUNDING_ENABLED=false ngay. Chỉ báo kế hoạch hoàn thành
khi tất cả cổng PASS.
```

## 3. Quy tắc báo cáo tiến độ

Mỗi lần báo cáo chỉ dùng mẫu:

```text
Bước: <số và tên>
Trạng thái: IN_PROGRESS | BLOCKED_EXTERNAL | PASS
Cổng đã đạt: <danh sách>
Cổng chưa đạt: <danh sách>
Nguyên nhân gốc đang xử lý: <một lớp chính>
Hành động đang chạy: <test/sửa/rerun cụ thể>
```

Không được ghi “đã hoàn thành” nếu còn cổng chưa đạt. Không được chuyển lỗi code
sang “kế hoạch sau”. Một lỗi chỉ được chuyển bước khi nó đã được chứng minh là
đầu vào thuộc đúng bước kế tiếp, ví dụ manifest thiếu nguồn chính thức từ Bước 1
sang Bước 2.

## 4. Kết quả thực thi Bước 1 (2026-07-24)

Trạng thái: `PASS`.

- Đã chạy đủ 167 câu/167 issue bằng live retrieval, không gọi model và không
  fallback sang fixture.
- `FOUND_AND_RETRIEVED`: 165 issue; `VERIFIED_DATA_GAP`: 2 issue;
  `FOUND_NOT_RETRIEVED` chưa xử lý: 0.
- Recall@10: 100%; direct-source top 5: 100%.
- Nguồn sai lĩnh vực/hết hiệu lực: 0.
- P95 retrieval: 372,329 ms; coverage: 100%.
- Đã chạy lại các nhóm bị ảnh hưởng và khóa nguyên nhân bằng 45 regression test.
- Có 8 expected-source row trong manifest data gap: 5 nguồn không có trong
  corpus và 3 số hiệu trùng/ánh xạ sang văn bản sai chủ đề.
- Không embedding lại corpus, không đổi active pointer, giữ
  `LEGAL_SECTION_GROUNDING_ENABLED=false`.

Artifact bắt buộc cho Bước 2:

- `reports/feature005/step1-retrieval-20260724/manifest.json`
- `reports/feature005/step1-retrieval-20260724/verified-data-gaps.json`

## 5. Kết quả thực thi Bước 2 (2026-07-24)

Trạng thái: `PASS`.

- Chỉ nhập gia tăng ba văn bản chính thức hiện hành còn thiếu và tái sử dụng
  vector sẵn có cho ba văn bản đã có trong kho; không embedding lại toàn bộ.
- Shadow primary có 25.396 vector; source collection vẫn có 430.968 vector.
- Kiểm tra 951 chunk liên quan: missing/orphan/stale/empty/duplicate/
  missing-embedding đều bằng 0.
- Active pointer không đổi và corpus gốc không bị xóa hoặc sửa nội dung.
- Retrieval-only trên 167 issue: Recall@10 100%, direct-source top 5 100%,
  wrong-field/expired bằng 0, P95 445,857 ms, không còn
  `FOUND_NOT_RETRIEVED`.
- Sáu data-gap row còn lại đều fail-closed: bốn văn bản chỉ còn hiệu lực một
  phần cần rà soát cấp điều khoản và hai câu hỏi không chỉ rõ thủ tục cần hỏi
  lại dữ kiện; không có nguồn được bịa.
- Bộ kiểm thử retrieval, incremental ingestion và form fail-closed: 54 PASS,
  0 FAIL.
- Giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`.

Artifact kết thúc:

- `reports/feature005/step2-data-gap-20260724/completion.json`
- `reports/feature005/step2-data-gap-20260724/runtime-validation.json`
- `reports/feature005/step2-data-gap-20260724/retrieval-gate.json`
- `reports/feature005/step2-data-gap-20260724/post-step2-gaps.json`

## 6. Kết quả thực thi Bước 3 (2026-07-24)

Trạng thái: `PASS`.

- Structured JSON contract dùng một lần gọi model cho toàn câu hỏi; không có
  model-repair lần hai.
- Mọi issue có coverage matrix theo facet yêu cầu, trạng thái `covered`,
  `missing` hoặc `not_available`; mẫu số coverage chỉ gồm facet có evidence.
- Validator đã khóa quote không tồn tại, evidence sai request/issue, sai
  điều-khoản, con số không có trong quote và phát biểu thời hạn/lệ phí không có
  ngôn ngữ chứng minh trong nguồn.
- JSON lỗi và timeout đều fail-closed về trích nguồn có citation; trace vẫn có
  coverage matrix và ghi `repair_count=0`.
- Renderer chỉ hiển thị claim đã qua validator; quality gate bắt buộc
  claim-grounding 100% và coverage tối thiểu 90%.
- Regression trọng tâm: 27 PASS; quality subset: 109 PASS; full backend:
  765 PASS; frontend: 82 PASS; type-check PASS; lint 0 lỗi.
- Ca acceptance đa issue đạt claim-grounding 100%, coverage 100%, đúng một lượt
  model. Ca 89,99% coverage và ca thiếu evidence đều bị gate chặn.
- `LEGAL_SECTION_GROUNDING_ENABLED=false` trong runtime và mọi file môi trường.

Bước 3 không bật tính năng. Nghiệm thu model live 9 vai trò và cổng latency được
thực hiện ở Bước 4–5 theo đúng thứ tự của runbook.

## 7. Kết quả thực thi Bước 4 (2026-07-24)

Trạng thái: `BLOCKED_EXTERNAL / DECISION_REQUIRED`.

Đã tối ưu trong phạm vi hiện tại mà không đổi model, không thêm dịch vụ trả phí,
không sửa corpus và không đổi active collection:

- tách timing thật thành `retrieval`, `provisioning`, `generation`,
  `validation` và `end_to_end`;
- giới hạn structured context ở 4.000 ký tự và output ở 256 token;
- loại evidence trùng, chỉ gửi evidence đã chọn theo issue;
- cache retrieval 10 phút bằng khóa SHA-256 không chứa câu hỏi thô, kể cả luồng
  Admin trace;
- chỉ mở rộng support đúng một lần cho facet thiếu;
- giữ deadline cấu hình 24 giây, ngân sách gọi model 23,75 giây để còn thời gian
  fallback;
- timeout/JSON lỗi trả fallback fail-closed, `repair_count=0`.

Kết quả warm concurrency 1:

| Cổng | Kết quả |
|---|---:|
| Request hoàn thành | 6/6 |
| Retrieval P95 | 52 ms |
| Provisioning P95 sau warm-up | 0 ms |
| Generation P95 | 23.778 ms |
| HTTP end-to-end P95 | 27.706 ms |
| Repair | 0 |
| Privacy scan | PASS |
| Step 3 live quality gate | 3 PASS / 3 FAIL |

Kết quả warm concurrency 5:

- 6/6 request hoàn thành, không có lỗi HTTP;
- retrieval P95 897 ms;
- HTTP P95 31.381 ms chỉ là số chẩn đoán tải đồng thời, không thay cổng warm
  concurrency 1;
- quality gate vẫn 3 PASS / 3 FAIL.

Ba ca không có nguồn xử lý fail-closed đúng hợp đồng. Ba ca có evidence đều hết
ngân sách ở model mặc định DeepSeek trước khi trả JSON hợp lệ; fallback không bịa
nguồn nhưng coverage của facet có nguồn bằng 0, vì vậy không thể báo PASS. Thử
riêng ca đơn với context/output đã rút xuống 4.000 ký tự/256 token vẫn timeout ở
23.771 ms. Đây là blocker đã được cô lập ở provider/model mặc định, không còn là
nút thắt retrieval, provisioning, validation hoặc renderer.

Feature flag vẫn là:

```env
LEGAL_SECTION_GROUNDING_ENABLED=false
```

Muốn tiếp tục cần một quyết định riêng được phê duyệt: dùng model/provider có thể
trả structured JSON trong deadline 24 giây, hoặc thay đổi chính cổng latency.
Bước 4 không tự thực hiện một trong hai quyết định này.

Artifact:

- `reports/feature005/step4-performance-20260724/final-warm-c1.json`
- `reports/feature005/step4-performance-20260724/final-warm-c5.json`
- `reports/feature005/step4-performance-20260724/diagnostic-simple-256.json`
- `reports/feature005/step4-performance-20260724/decision.json`

## 8. Kết quả thực thi Bước 5 (2026-07-24)

Trạng thái: `PASS` cho cổng chất lượng, phân quyền và trải nghiệm ba vai trò.
Feature vẫn chưa rollout vì cổng hiệu năng Bước 4 là một quyết định độc lập.

- API live đạt 9/9 ca, HTTP 200, coverage contract 100%, không có lỗi nghiêm
  trọng và không fallback fixture.
- In-app browser đạt 9/9 ca: 3 Người dân, 3 Cán bộ, 3 Admin.
- SSE phát đúng lifecycle và chỉ phát final đã kiểm chứng; history tải lại
  nguyên `answer_sections` và `forms_unavailable`.
- Citizen/officer không nhận hoặc hiển thị Admin trace; Admin có diagnostics
  khi bật tùy chọn. Không lộ chunk ID, packet ID hoặc citation marker nội bộ.
- Renderer theo vai trò chỉ đổi nhãn/cách trình bày của claim đã qua validator,
  không tạo thêm claim pháp lý.
- Mỗi facet được hỏi có một retrieval issue riêng. Facet có nguồn được trả lời
  kèm citation; facet thiếu nguồn được hiển thị fail-closed thay vì bị bỏ qua.
- Hard gate loại nguồn chỉ dành cho Cơ quan đại diện khi câu hỏi không yêu cầu
  kênh này, và loại nguồn cùng lĩnh vực nhưng sai thủ tục cụ thể.
- UI cán bộ đa lĩnh vực giữ `Tự nhận diện`; chỉ preselect khi hồ sơ có đúng một
  lĩnh vực hoặc đơn vị công tác khớp rõ ràng.
- Biểu mẫu chỉ hiển thị khi official + approved + có file/URL tải. Ba ca chưa
  có mẫu hợp lệ trả `forms_unavailable`, không tạo mẫu thay thế.
- Backend full suite: 791 PASS. Frontend: 86 PASS. Type-check PASS. Lint 0 lỗi.
  Rollback/privacy subset: 46 PASS. Privacy scanner PASS cho hai aggregate.
- Migration SurrealDB ở version 38; section/form status round-trip thành công.
- `.env` vẫn giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`; corpus và active
  pointer không bị sửa trong Bước 5.

Artifact:

- `reports/feature005/step5-api-summary-final3.json`
- `reports/feature005/step5-browser-summary-final.json`

Các lỗi được phát hiện và sửa ngay trong Bước 5:

1. JSON hợp lệ nhưng claims rỗng: exact extractive output chạy lại cùng
   validator, không gọi model repair.
2. Planner bỏ sót facet: tạo tối đa một issue cho mỗi facet rõ ràng.
3. Nguồn Cơ quan đại diện bị dùng cho thủ tục nội địa và nguồn hôn nhân bị dùng
   cho khai sinh: thêm gate jurisdiction + procedure topic.
4. Officer có nhiều domain bị ép vào domain đầu tiên: giữ auto-detection.
5. SSE/history có nguy cơ lộ trace hoặc mất section/form state: citation
   allow-list, Admin-only trace và migration history version 38.

## 9. Kết quả thực thi Bước 6 (2026-07-24)

Trạng thái: `BLOCKED_EXTERNAL`.

- Đã sửa deterministic fallback: câu trích có dẫn chiếu pháp lý không khớp
  metadata bị bỏ qua, câu phù hợp tiếp theo vẫn phải qua cùng claim validator.
- Backend 794 PASS; frontend 86 PASS; type-check và build PASS; lint 0 lỗi.
- Warm concurrency 1 đạt retrieval 128 ms, generation 23.771 ms và HTTP
  end-to-end 27.773 ms; quality 6/6.
- Warm concurrency 5 hoàn thành 6/6 không request failure; toàn bộ cold/warm
  concurrency 1/5/10/20 hoàn thành 48/48 và privacy scan PASS.
- API và giao diện flag-on đạt 9/9; không lộ marker nội bộ và không rò Admin
  trace sang Citizen/Officer.
- Browser flag-off chưa đạt do DeepSeek provider trả
  `402 Insufficient Balance`.
- Chưa có phê duyệt pháp lý riêng cho nguồn và đáp án của chín ca Bước 6.
- Không rollout theo vai trò; T038, T039, T042 giữ chưa hoàn thành;
  `LEGAL_SECTION_GROUNDING_ENABLED=false`; runtime chuẩn và collection rollback
  đã được phục hồi.

Dependency ngoài cần cung cấp:

1. Balance/credential hoạt động cho chính DeepSeek provider đang cấu hình.
2. Phiếu duyệt pháp lý Bước 6 có reviewer, thời điểm, quyết định và checksum
   khớp `reports/feature005/step6-release-20260724/legal-review-request.json`.

## 10. Khắc phục integrity sau kiểm toán (2026-07-24)

Trạng thái kỹ thuật: `PASS`. Trạng thái phát hành: `BLOCKED_EXTERNAL`.

- Tạo collection bất biến `legal_chunks_lechan_primary_v20260724_r2`
  (25.003 vector) và `legal_chunks_lechan_support_v20260724_r2`
  (29.961 vector) bằng cách copy embedding sẵn có.
- Builder dùng domain đã duyệt từ manifest DB-2, hard gate lại provenance,
  hiệu lực, trạng thái article/document và nội dung chunk trước khi copy.
- Chỉ nhận 6 document ID chính thức từ Step 2; không nhận mọi vector phát sinh
  sau checksum. Collection đã tồn tại không còn bị xóa/tạo lại ngầm.
- Count/checksum khớp tuyệt đối; missing, stale, empty, orphan, duplicate,
  missing embedding và unknown domain đều bằng 0; source collection và active
  pointer không đổi.
- Bốn chunk test `750091–750094` không có trong primary hoặc support r2.
- Full 167 retrieval-only: 164 `FOUND_AND_RETRIEVED`, 3
  `VERIFIED_DATA_GAP`, 0 `FOUND_NOT_RETRIEVED`, Recall@10 100%, direct-source
  top5 100%, wrong-field/expired 0, coverage 99,401%, P95 394,033 ms.
- Readiness release đã bật live provider probe. DeepSeek trả 402 được phản ánh
  bằng `/ready` HTTP 503 và mã `provider_payment_required`, không còn báo xanh
  chỉ vì credential tồn tại.
- Feature flag vẫn `LEGAL_SECTION_GROUNDING_ENABLED=false`; chưa rollout r2.

Artifact:

- `reports/feature005/db4-serving-20260724-r2/manifest.json`
- `reports/feature005/db5-shadow-20260724-r2/retrieval-167.json`
- `reports/feature005/db5-shadow-20260724-r2/verified-data-gaps.json`

Hai dependency ngoài còn bắt buộc trước release:

1. Khôi phục balance/credential cho đúng DeepSeek provider mặc định.
2. Duyệt pháp lý riêng chín câu trả lời Step 6; phê duyệt scope DB-3 không thay
   thế phê duyệt nội dung trả lời cuối.

## 11. Kết quả khắc phục r2 cuối (2026-07-24)

Trạng thái kỹ thuật API/retrieval/performance: `PASS`.

- Sửa policy câu hỏi chỉ hỏi thẩm quyền: không còn tự thêm facet điều kiện và
  ngoại lệ; bổ sung chuẩn hóa `đ` và nhận diện tranh chấp ranh giới/thửa đất.
- Precompute fallback đã qua validator trước khi gọi model; giới hạn hai lượt
  DeepSeek đồng thời và fail-closed ngay khi vượt capacity, không xếp hàng.
- Không đổi deadline cấu hình 24 giây, không gọi repair, không đổi model, không
  sửa corpus và không embedding lại.
- Backend full suite: 802 PASS.
- API role matrix r2: 9/9 PASS, 0 critical failure.
- Warm concurrency 1: quality 6/6, retrieval P95 91 ms, generation P95
  23.783 ms, internal end-to-end 23.838 ms, HTTP P95 28.482 ms.
- Warm concurrency 5: quality 6/6, không request failure, retrieval P95
  1.485 ms, generation P95 23.765 ms, internal end-to-end 25.054 ms, HTTP P95
  28.765 ms.
- Artifact tổng hợp mới đều qua privacy scanner.

Trạng thái phát hành vẫn là `BLOCKED_EXTERNAL`: phiên in-app browser hiện tại
không attach được webview để chạy lại r2 và chưa có phiếu duyệt pháp lý riêng
cho nội dung cuối của 9 câu Step 6. DeepSeek readiness hiện đã healthy, nên lỗi
402 trước đây không còn là blocker đang hoạt động. Giữ
`LEGAL_SECTION_GROUNDING_ENABLED=false` và không rollout.

## 12. Bước Forms — Procedure Catalog và Form Catalog

Mục tiêu là trả đúng biểu mẫu mà không đưa binary vào legal RAG và không dùng
model đoán metadata.

### Forms-0: build phạm vi canonical

```powershell
python -X utf8 scripts/build_canonical_form_catalog.py --check
python -X utf8 scripts/build_canonical_form_catalog.py
python -m pytest tests/test_canonical_form_catalog.py -q
```

Gate: 202/202 requirement được phân loại; 90 synthetic bị cách ly; 52 thủ tục
thực; không có mojibake hoặc `UNKNOWN`.

### Forms-1: resolver và hard gate

```powershell
python -m pytest tests/test_form_resolution_registry.py `
  tests/test_form_recommendation_and_citations.py `
  tests/test_form_review_packet.py -q
```

Gate: exact code/alias có dấu–không dấu hoạt động; pending/reference/seed,
hết hiệu lực, sai domain, sai role và file hỏng đều bị loại.

### Forms-2: crawl candidate và duyệt

```powershell
python -X utf8 scripts/crawl_canonical_forms.py --no-network
python -X utf8 scripts/crawl_canonical_forms.py --rate-limit 0.25
python -X utf8 scripts/build_form_review_packet.py
```

Crawler chỉ tạo candidate. Không được đổi `approved`. Mọi requirement phải có
trạng thái deterministic; `NEEDS_SOURCE_MAPPING` là việc còn phải hoàn thiện,
không được đổi thành `BLOCKED_EXTERNAL` nếu chưa thử URL chính thức. Chỉ promote
sau phiếu duyệt pháp lý có tên reviewer, thời điểm, quyết định và checksum.

### Forms-3: ma trận 3.120 ca

```powershell
python -X utf8 scripts/evaluate_form_resolution_matrix.py
python -m pytest tests/test_form_resolution_matrix.py -q
```

Gate: procedure/form top-1 tối thiểu 99%; wrong procedure/form, role leakage,
expired/superseded và pending exposure đều bằng 0; lookup P95 không quá 200 ms.
Bộ này không gọi DeepSeek.

### Forms-4: UI, compatibility và monitor

```powershell
cd frontend
npm test
npx tsc --noEmit
cd ..
python -X utf8 scripts/monitor_canonical_forms.py
```

Form card hiển thị tên, mã, thủ tục, nguồn, định dạng và hiệu lực. Không có mẫu
đạt gate thì `recommended_forms=[]`, `forms_unavailable=true`. Job monitor chỉ
đọc, không tự approve hoặc sửa catalog.

Rollback: giữ `LEGAL_SECTION_GROUNDING_ENABLED=false`, không đổi active legal
collection, chuyển runtime về catalog cũ, giữ nguyên candidate/corpus/history.
