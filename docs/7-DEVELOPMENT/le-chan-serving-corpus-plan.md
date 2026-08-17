# Kế hoạch tối ưu kho phục vụ phường/xã tại Lê Chân, Hải Phòng

## 1. Mục tiêu

Tạo một kho phục vụ nhỏ, đúng thẩm quyền và đủ căn cứ cho nghiệp vụ hành chính
cấp phường/xã tại Lê Chân. Kho gốc được giữ nguyên để tra cứu lịch sử, kiểm toán
và rollback.

Kế hoạch không dùng số lượng văn bản làm mục tiêu duy nhất. Một tập nhỏ chỉ được
chuyển sang phục vụ khi vẫn đạt Recall@10, source-top-5, coverage và grounding
gate trên bộ kiểm thử đã duyệt.

## 2. Baseline đã xác minh

| Hạng mục | Số lượng |
|---|---:|
| Văn bản gốc trong PostgreSQL | 25.426 |
| Văn bản active | 20.436 |
| Văn bản active, áp dụng tại thời điểm kiểm tra | 20.363 |
| Văn bản trong core hiện hành | 6.034 |
| Chunk trong PostgreSQL | 544.078 |
| Chunk đã được đánh giá chất lượng | 544.078 |
| Chunk được đánh dấu eligible | 283.552 |
| Vector trong collection core đang phục vụ | 159.530 |
| Tập seed có lý do ưu tiên trực tiếp | 940 văn bản / 32.165 chunk |

Core 6.034 văn bản hiện gồm 2.934 thông tư, 1.621 quyết định, 569 nghị định,
378 thông tư liên tịch, 182 chỉ thị và 98 luật. Hai lý do đưa văn bản vào core
đang làm phạm vi rộng quá mức:

- `central_relevant_framework`: 3.294 văn bản;
- `core_domain_expansion_2026_07_14`: 1.800 văn bản.

Tập có lý do gắn trực tiếp với nhiệm vụ cấp cơ sở hoặc Hải Phòng gồm:

- `central_commune_specific`;
- `hai_phong_commune_specific`;
- `hai_phong_relevant_domain`;
- `current_core_law`;
- `related_current_instrument`;
- `manual_verified_import`.

Hợp của các nhóm này hiện có 940 văn bản. Đây là seed hợp lý để xây tập phục vụ,
không phải danh sách cuối cùng.

## 3. Quyết định kiến trúc

### Quyết định 1 — Không xóa corpus gốc

**Chọn:** Giữ 25.426 văn bản trong PostgreSQL.

**Lý do:** Văn bản hết hiệu lực vẫn cần cho câu hỏi theo thời điểm, quan hệ sửa
đổi/thay thế và kiểm toán nguồn. Xóa dữ liệu làm mất khả năng trả lời lịch sử và
làm rollback khó hơn.

**Không chọn:** Xóa toàn bộ văn bản ngoài Lê Chân hoặc hết hiệu lực.

### Quyết định 2 — Tạo ba tầng phục vụ

| Tầng | Mục đích | Cách truy vấn |
|---|---|---|
| `primary` | Văn bản trực tiếp phục vụ nghiệp vụ phường/xã | Tìm mặc định |
| `support` | Văn bản hiện hành liên quan, hướng dẫn hoặc ngoại lệ | Chỉ tìm cho issue còn thiếu nguồn |
| `historical_quarantine` | Hết hiệu lực, sai metadata, ngoài phạm vi hoặc chưa duyệt | Không đưa vào model cho câu hỏi hiện tại |

Kho `historical_quarantine` vẫn tra được bằng trang Admin và câu hỏi có `as_of`
phù hợp sau khi kiểm tra hiệu lực.

### Quyết định 3 — Tạo collection mới bằng vector đã có

**Chọn:** Sao chép các vector đủ điều kiện từ collection hiện có sang collection
version mới. Không embedding lại toàn bộ corpus.

Tên gợi ý:

```text
legal_chunks_lechan_primary_v1
legal_chunks_lechan_support_v1
```

Chỉ embedding gia tăng nếu một văn bản chính thức mới chưa từng có vector. Mọi
trường hợp này phải đi qua ingestion và quality gate hiện tại.

### Quyết định 4 — Exact lookup đứng trước semantic retrieval

Số hiệu văn bản, điều/khoản, mã thủ tục, mã biểu mẫu và tên mẫu phải đi qua tra
cứu deterministic trước ANN. Vector search chỉ xử lý ngôn ngữ tự nhiên và mở
rộng ngữ nghĩa.

### Quyết định 5 — Không mở rộng theo mọi quan hệ

Không tự động lấy toàn bộ quan hệ `Văn bản căn cứ`, vì bảng hiện có hơn 119.000
quan hệ loại này và sẽ làm collection phình lại.

Chỉ mở rộng tối đa một hop cho quan hệ sửa đổi, bổ sung, hướng dẫn/quy định chi
tiết và thay thế; văn bản được mở rộng vẫn phải qua hiệu lực, lĩnh vực, phạm vi,
thẩm quyền và provenance gate.

## 4. Hợp đồng lựa chọn văn bản

### 4.1 Hard gate bắt buộc

Một văn bản chỉ được vào `primary` hoặc `support` khi:

1. Có trạng thái chính thức/được duyệt.
2. Có danh tính nguồn và URL chính thức hoặc nguồn nội bộ đã xác minh.
3. Có ngày hiệu lực hoặc quyết định pháp lý xác minh được về hiệu lực.
4. Áp dụng tại `as_of`; không hết hiệu lực toàn bộ.
5. Có lĩnh vực và phạm vi áp dụng xác định.
6. Thuộc pháp luật trung ương áp dụng chung, Hải Phòng hoặc Lê Chân.
7. Không xung đột với văn bản có hiệu lực pháp lý cao hơn.
8. Có passage/chunk không rỗng và liên kết được về văn bản/điều.

Văn bản thiếu một hard gate vào `historical_quarantine`, không được model dùng
làm căn cứ cho câu hỏi hiện tại.

### 4.2 Điều kiện vào `primary`

Ít nhất một điều kiện sau phải đúng:

- Quy định trực tiếp thẩm quyền, trách nhiệm hoặc thủ tục của UBND/cơ quan cấp
  xã, phường;
- Là luật, nghị định hoặc thông tư hiện hành điều chỉnh trực tiếp một thủ tục
  trong danh mục ưu tiên;
- Là văn bản Hải Phòng/Lê Chân hiện hành quy định phân cấp, nơi nộp, quy trình,
  thời hạn, lệ phí hoặc biểu mẫu tương thích luật trung ương;
- Là nguồn mong đợi đã được duyệt trong 167 câu golden hoặc 9 tình huống vai trò;
- Là văn bản sửa đổi, bổ sung, thay thế hoặc hướng dẫn trực tiếp một văn bản
  primary và cần thiết để áp dụng đúng;
- Là nguồn thủ tục/biểu mẫu chính thức có provenance đầy đủ.

### 4.3 Điều kiện vào `support`

- Hiện hành và cùng lĩnh vực với primary nhưng chỉ cần cho ngoại lệ hoặc tình
  huống ít gặp;
- Quan hệ một hop hợp lệ với primary;
- Không trực tiếp quy định nhiệm vụ phường/xã nhưng cần giải thích quyền, điều
  kiện hoặc cấp trên tiếp nhận;
- Được retrieval dùng để lấp một facet còn thiếu và đã vượt quality gate.

### 4.4 Điều kiện đưa ra khỏi tìm kiếm mặc định

- Hết hiệu lực hoặc bị thay thế toàn bộ;
- Dự thảo, staging hoặc chưa xác minh;
- Thiếu hiệu lực, phạm vi, lĩnh vực hoặc provenance;
- Thủ tục nội bộ của ngành không liên quan đến cấp phường/xã;
- Ngoại giao/cơ quan đại diện ở nước ngoài, quân sự nội bộ, hàng hải, chứng
  khoán, ngân hàng chuyên ngành hoặc lĩnh vực xa khác, trừ khi một thủ tục ưu
  tiên có quan hệ điều chỉnh trực tiếp;
- Chunk rỗng, trùng hoàn toàn, orphan hoặc tiêu đề/nội dung hỏng;
- Văn bản chỉ được đưa vào bởi mở rộng lĩnh vực rộng nhưng không có bằng chứng
  thẩm quyền, thủ tục, golden case hoặc quan hệ trực tiếp.

Không loại toàn bộ văn bản của Bộ Tài chính, Bộ Công an hoặc bộ chuyên ngành chỉ
dựa vào tên cơ quan. Ví dụ thuế/phí khi sang tên đất và cư trú vẫn có thể cần.

## 5. Phạm vi nghiệp vụ primary

Tập primary phải phủ tối thiểu các nhóm:

1. Hộ tịch, quốc tịch liên quan, chứng thực và xác nhận tình trạng hôn nhân.
2. Cư trú, an ninh trật tự và thủ tục phối hợp cấp cơ sở.
3. Đất đai, đăng ký biến động, hòa giải tranh chấp và môi trường cấp cơ sở.
4. Trật tự xây dựng, nhà ở riêng lẻ và xử lý vi phạm thuộc/phối hợp cấp phường.
5. Khiếu nại, tố cáo, tiếp công dân, xử lý đơn và xử phạt hành chính.
6. Người có công, bảo trợ xã hội, hộ nghèo và an sinh thuộc cấp cơ sở.
7. Giáo dục, y tế, văn hóa và chính sách dân cư trong phạm vi nhiệm vụ phường.
8. Bộ máy, cán bộ, phân cấp, thủ tục hành chính, phí/lệ phí và dịch vụ công.
9. Văn bản Hải Phòng về phân cấp, quy trình, mức thu, nơi tiếp nhận và danh mục
   thủ tục đang áp dụng.
10. Catalog biểu mẫu đã duyệt và có file/URL tải thực.

## 6. Quy mô mục tiêu

Quy mô là cửa sổ thiết kế, không phải quota buộc phải đạt:

| Tầng | Văn bản mục tiêu | Chunk/vector mục tiêu |
|---|---:|---:|
| Primary | 1.200–1.800 | 35.000–60.000 |
| Support | 1.500–3.000 | 40.000–90.000 |
| Historical/quarantine | Phần còn lại | Không tìm mặc định |

940 văn bản/32.165 chunk hiện tại là seed. Sau khi thêm quan hệ một hop, golden
sources và nguồn thủ tục/biểu mẫu, primary dự kiến nằm trong cửa sổ trên. Nếu
quality gate cần nhiều hơn, ưu tiên coverage thay vì ép giảm số lượng.

## 7. Mô hình dữ liệu sidecar

Không sửa hoặc xóa bản ghi imported. Tạo bảng phụ:

### `legal_serving_scope`

| Trường | Ý nghĩa |
|---|---|
| `document_id` | Liên kết văn bản gốc |
| `scope_version` | Phiên bản manifest |
| `serving_tier` | `primary`, `support`, `historical_quarantine` |
| `eligible` | Được dùng cho câu hỏi hiện tại hay không |
| `reason_codes` | Lý do deterministic |
| `legal_as_of` | Ngày đánh giá hiệu lực |
| `metadata_complete` | Kết quả hard gate |
| `review_status` | automated, reviewed, rejected |
| `assessed_at` | Thời điểm đánh giá |

### `legal_serving_chunk_scope`

| Trường | Ý nghĩa |
|---|---|
| `chunk_id` | Chunk gốc |
| `scope_version` | Phiên bản manifest |
| `eligible` | Có được copy sang serving collection |
| `canonical_chunk_id` | Ánh xạ trùng lặp |
| `reason_codes` | empty, duplicate, orphan, metadata, historical... |

### `legal_serving_manifest`

Lưu version, checksum, số văn bản/chunk theo tầng, tên collection, bộ test, kết
quả quality/performance gate và collection rollback.

Đây là thay đổi schema sidecar; cần phê duyệt trước khi triển khai theo quy định
dự án. Nó không thay đổi các bảng imported.

## 8. Luồng truy xuất mục tiêu

```text
Câu hỏi
  -> tách issue/facet
  -> exact lookup số hiệu/điều/mã thủ tục/mẫu
  -> primary ANN + lexical
  -> eligibility + hierarchy + diversity
  -> nếu facet thiếu: support retrieval đúng một lần
  -> context 8 chunk câu đơn / 12 chunk đa issue
  -> generation + claim validation
```

Ngân sách khởi điểm để benchmark:

- Primary: 150 vector candidate + 60 lexical candidate.
- Rerank tối đa 40 ứng viên.
- Support fallback: 100 vector + 40 lexical cho đúng issue thiếu.
- Tối đa 2 chunk cùng một điều và 3 chunk cùng một văn bản trong context.

Các con số chỉ được giảm nếu Recall@10 và coverage không giảm.

## 9. Chỉ mục PostgreSQL cần có

1. Exact/normalized index cho `law_number`.
2. Unaccent/trigram index cho tên văn bản và cơ quan ban hành.
3. Composite/partial index cho `status`, `effective_date`, `expired_date`.
4. Index `legal_serving_scope(scope_version, serving_tier, eligible, document_id)`.
5. Index quan hệ theo source/target và loại quan hệ.
6. Index chunk theo article/document phục vụ hydrate sau ANN.

Exact lookup không được chạy broad `LIKE` trên nội dung chunk.

## 10. Các bước triển khai và cổng hoàn thành

### Bước A — Backup và manifest baseline

- Sao lưu PostgreSQL/Chroma và ghi checksum.
- Ghi baseline 25.426/20.363/6.034/159.530.
- Giữ collection cũ làm rollback.

**PASS:** backup đọc lại được, manifest đủ count/checksum, flag vẫn false.

### Bước B — Sinh scope ở chế độ shadow

- Chạy hard gate và tier rules vào manifest/bảng sidecar.
- Không đổi collection đang phục vụ.
- Xuất lý do vào/ra cho Admin, không chứa nội dung pháp lý thô.

**PASS:** 100% văn bản được phân tầng; không còn `unknown`; mọi exclusion có
reason code; luật/nguồn golden bắt buộc không bị loại.

### Bước C — Duyệt coverage

- Đối chiếu 167 golden, 202 thủ tục ưu tiên và 9 tình huống vai trò.
- Người rà soát pháp lý duyệt nguồn bắt buộc, nguồn cấm và quan hệ trung
  ương–Hải Phòng.
- Sửa scope ngay trong bước cho đến khi không còn thiếu nguồn đã có trong kho.

**PASS:** 100% expected source đã duyệt nằm trong primary/support đúng tầng.

### Bước D — Tạo collection subset

- Copy vector eligible đã có sang collection version mới.
- Không recompute embedding toàn corpus.
- Kiểm tra vector count, orphan, stale, duplicate và checksum.

**PASS:** count khớp manifest, không orphan/stale, collection cũ không đổi.

### Bước E — Sửa retrieval

- Exact lookup trước semantic search.
- Truy vấn primary trước; support chỉ cho facet thiếu.
- Áp dụng hiệu lực, phạm vi, thứ bậc và source diversity.
- Không broad scan từng issue.

**PASS:** Recall@10 >=95%, direct source top 5 >=95%, wrong-field/expired = 0,
P95 retrieval <=3 giây.

### Bước F — Shadow comparison và nghiệm thu

- Chạy old core và new serving collection trên cùng 167 câu.
- Chỉ lưu metric/opaque ID, không lưu câu hỏi/câu trả lời trong artifact chia sẻ.
- Chạy 9 API và 9 browser cases cho ba vai trò.
- Chạy full backend/frontend/auth/privacy/rollback suite.

**PASS:** quality không giảm, coverage >=90% khi kho có nguồn, 100% claim pháp
lý hiển thị có evidence, 9/9 không có lỗi nghiêm trọng, concurrency 5 không lỗi.

### Bước G — Chuyển collection và rollback

- Chuyển pointer theo thứ tự Admin -> Cán bộ -> Người dân.
- Theo dõi missing-evidence, fallback, top-source và timing.
- Lỗi nghiêm trọng lập tức chuyển pointer cũ và flag false.

**PASS:** từng tầng ổn định và có phê duyệt pháp lý. Không xóa collection cũ
trước khi hết thời gian rollback đã phê duyệt.

## 11. Điều kiện không được chuyển collection

- Một luật/nghị định điều chỉnh trực tiếp bị loại khỏi primary/support.
- Nguồn hết hiệu lực hoặc sai lĩnh vực được chọn cho câu hỏi hiện tại.
- Thiếu nguồn thủ tục/biểu mẫu nhưng hệ thống vẫn trả như đã xác minh.
- Recall, coverage hoặc grounding thấp hơn collection cũ.
- P95 retrieval vượt 3 giây hoặc concurrency 5 có lỗi.
- Chưa có người rà soát pháp lý phê duyệt scope và golden expectations.

## 12. Ước lượng thực hiện

Nếu dữ liệu metadata hiện có đủ:

- Sinh scope, manifest và kiểm tra tự động: 1–2 giờ.
- Tạo subset collection bằng vector có sẵn: 30–90 phút.
- Exact lookup, tier retrieval và regression: 2–4 giờ.
- Shadow evaluation và full test: 2–4 giờ.

Thời gian rà soát pháp lý hoặc bổ sung nguồn chính thức được báo riêng; không
được thay bằng suy đoán của model.

