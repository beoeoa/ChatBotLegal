# Feature Specification: Nâng cấp độ tin cậy câu trả lời pháp luật

**Feature Branch**: `codex/006-public-production-readiness`

**Created**: 2026-08-10

**Status**: Approved for phased implementation; live activation and corpus mutation remain separately gated

**Input**: Kế hoạch 016 do người dùng cung cấp, yêu cầu triển khai tuần tự, kiểm thử mã sau từng giai đoạn và chỉ thực hiện kiểm thử trực tiếp trên web sau khi xin phép lại.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Đo chất lượng có thể tái lập (Priority: P1)

Người phụ trách chất lượng có thể dùng một bộ ca duy nhất, có phiên bản và đáp án mong đợi rõ ràng để phân biệt câu trả lời đúng với câu trả lời chỉ có nguồn hoặc chỉ sao chép một đoạn nhỏ.

**Why this priority**: Không có phép đo đúng thì mọi thay đổi retrieval, hiệu lực hoặc fallback đều không thể chứng minh là cải thiện.

**Independent Test**: Nạp workbook baseline, xác nhận đúng 100 câu duy nhất chia đều năm lĩnh vực, chạy validator trên golden schema v2 và tạo báo cáo chỉ từ trường trả lời có cấu trúc.

**Acceptance Scenarios**:

1. **Given** workbook có các câu bị lặp nhiều lần, **When** tạo regression baseline, **Then** mỗi câu pháp lý duy nhất chỉ có một `case_id` và vẫn giữ thống kê các lần chạy.
2. **Given** một lượt trả về URL nhưng sai văn bản hoặc sai điều, **When** chấm theo golden v2, **Then** lượt đó thất bại dù cờ `has_sources` là đúng.
3. **Given** log giao diện chứa nút bấm và thông báo, **When** xuất báo cáo, **Then** DOM không được coi là nội dung câu trả lời.

---

### User Story 2 - Câu trả lời hiện hành an toàn và đầy đủ (Priority: P1)

Người dân và cán bộ nhận câu trả lời đúng thủ tục, đúng văn bản/điều còn hiệu lực, đủ các phần được hỏi; nếu thiếu căn cứ thì hệ thống từ chối rõ ràng thay vì dùng nguồn gần giống.

**Why this priority**: Đây là rủi ro pháp lý trực tiếp và là nguyên nhân chính của các lỗi trong workbook.

**Independent Test**: Chạy các regression về hiệu lực, cặp thủ tục dễ nhầm, fallback, Điều cụ thể nhiều chunk, trùng claim và determinism mà không gọi trình duyệt.

**Acceptance Scenarios**:

1. **Given** văn bản hết hiệu lực hoặc điều khoản đã bị bãi bỏ, **When** hỏi theo thời điểm hiện hành, **Then** nguồn đó không được dùng để tạo kết luận.
2. **Given** câu hỏi tố cáo, **When** retrieval tìm thấy cả khiếu nại và tố cáo, **Then** chỉ evidence hỗ trợ đúng thủ tục và facet mới được dùng.
3. **Given** câu hỏi “Điều X của văn bản Y”, **When** điều có nhiều khoản/điểm, **Then** hệ thống nạp đủ, sắp đúng thứ tự và báo thiếu nếu packet không hoàn chỉnh.
4. **Given** provider không khả dụng, **When** evidence chưa đủ ở cấp claim, **Then** hệ thống chỉ hiển thị nguồn hoặc từ chối; không cắt các nguồn đầu làm câu trả lời.

---

### User Story 3 - Truy xuất chính xác trong giới hạn máy cục bộ (Priority: P2)

Người dùng nhận nguồn liên quan hơn nhờ reranking học từ hard-negative, trong khi máy không đủ GPU vẫn dùng được chế độ an toàn với degraded mode minh bạch.

**Why this priority**: Chỉ thực hiện sau khi cổng hiệu lực, intent và fallback đạt; reranker không được phép bù cho hard gate sai.

**Independent Test**: So sánh baseline với shadow reranker trên cùng tập dữ liệu và cùng phiên bản index; xác nhận không giảm recall, không OOM và không có safety regression.

**Acceptance Scenarios**:

1. **Given** hai chunk có từ vựng gần nhau nhưng thuộc hai thủ tục khác nhau, **When** rerank, **Then** chunk đúng thủ tục đứng trên hard-negative.
2. **Given** learned model lỗi hoặc thiếu tài nguyên, **When** truy xuất, **Then** hệ thống quay về heuristic, ghi degraded mode và vẫn giữ hard gate.
3. **Given** embedding shadow không đạt cổng chất lượng/tài nguyên, **When** kết thúc benchmark, **Then** active index không thay đổi.

---

### User Story 4 - Bằng chứng và audit kiểm chứng được (Priority: P2)

Người dùng có thể mở đúng bằng chứng của citation; quản trị viên phát hiện được log pháp lý bị sửa và biết provider/model nào đã tham gia mà không lộ dữ liệu cá nhân.

**Why this priority**: Đây là lớp chứng minh và quản trị cần thiết sau khi tính đúng của câu trả lời đã được khóa.

**Independent Test**: Dùng dữ liệu cô lập để kiểm tra ba cấp citation, sai trang/quote/file, chuỗi audit bị sửa/xóa/đảo và PII trước cloud egress.

**Acceptance Scenarios**:

1. **Given** quote có vị trí vật lý, **When** mở citation, **Then** viewer mở đúng bản nguồn và đúng đoạn.
2. **Given** citation chỉ có metadata, **When** tạo kết luận pháp lý, **Then** citation đó không đủ điều kiện hỗ trợ claim.
3. **Given** một audit entry bị sửa hoặc xóa, **When** xác minh chain, **Then** integrity check thất bại.
4. **Given** câu hỏi có PII và provider cloud, **When** chuẩn bị egress, **Then** PII bị che; nếu không che được thì cloud call bị chặn.

---

### User Story 5 - Tài liệu phức tạp và dẫn chiếu có giới hạn (Priority: P3)

Người dùng tra cứu được bảng, phụ lục và quan hệ sửa đổi/dẫn chiếu mà hệ thống vẫn giữ cấu trúc nguồn và không cho mô hình mở rộng tự do.

**Why this priority**: Tăng độ phủ cho tài liệu phức tạp nhưng chỉ an toàn sau khi single-hop đạt cổng chất lượng.

**Independent Test**: Dùng fixtures PDF scan, bảng và graph pháp luật cô lập để kiểm tra layout, vòng lặp, query budget và hiệu lực ở từng hop.

**Acceptance Scenarios**:

1. **Given** bảng lệ phí nhiều ô, **When** trích xuất, **Then** hàng/cột/ô và provenance được giữ.
2. **Given** quan hệ dẫn chiếu đã xác minh, **When** cần hop tiếp theo, **Then** hệ thống chỉ đi trong graph được duyệt, tối đa hai vòng và mười sáu query.
3. **Given** văn bản ở hop tiếp theo hết hiệu lực, **When** tái áp dụng hard gate, **Then** evidence đó bị chặn.

### Edge Cases

- Snapshot hiệu lực thiếu, cũ quá 24 giờ hoặc không có mapping đến cấp điều/khoản.
- Cùng số Điều xuất hiện trong nhiều văn bản hoặc nhiều phiên bản văn bản.
- Legacy chunk thiếu `procedure_family`/`supported_facets` nhưng có từ khóa khớp mạnh.
- Hai đoạn giống nhau xuất hiện ở hai trang hoặc hai source asset khác nhau.
- Provider trả chậm sau khi request đã hết deadline.
- Cùng câu hỏi được gửi đồng thời bởi nhiều role hoặc sau khi index/snapshot thay đổi.
- GPU không đủ bộ nhớ, máy chỉ có CPU hoặc model artifact bị thiếu/sai fingerprint.
- Multi-hop gặp vòng lặp, quan hệ chưa duyệt hoặc vượt budget.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Hệ thống MUST tạo baseline 100 câu duy nhất từ workbook nguồn và giữ SHA-256, phân bố lĩnh vực cùng thống kê các lượt chạy.
- **FR-002**: Golden schema v2 MUST hỗ trợ `legal_as_of`, nguồn/điều được phép và bị cấm, facts bắt buộc có thứ tự, exact proof span khi có, cùng kết quả fallback/từ chối mong đợi.
- **FR-003**: Bộ chấm MUST đánh giá đúng văn bản/điều, hiệu lực, facet, facts, thứ tự, claim support, citation và nhãn fallback; `has_sources` không được dùng như kết luận đúng/sai.
- **FR-004**: Nhật ký QA MUST tách câu trả lời khỏi DOM và ghi phiên bản app/index/snapshot/embedding/reranker/provider cùng timing từng stage.
- **FR-005**: Hệ thống MUST chuẩn hóa ý định thành domain, procedure family, action, target, requested facets, identifiers, jurisdiction, `legal_as_of`, confidence và ambiguity reasons.
- **FR-006**: Evidence thiếu procedure/facet rõ ràng MUST NOT được dùng tạo kết luận; keyword legacy chỉ hỗ trợ recall.
- **FR-007**: Một trạng thái hiệu lực chuẩn MUST được dùng nhất quán bởi kho, snapshot, giao diện quản lý và retrieval.
- **FR-008**: Trả lời hiện hành MUST fail closed với văn bản/phần văn bản hết hiệu lực, chưa xác minh hoặc snapshot quá hạn; tra cứu lịch sử phải gắn nhãn riêng.
- **FR-009**: Exact-article retrieval MUST nạp toàn bộ chunk của Điều, sắp theo cấu trúc, phát hiện thiếu/trùng và không coi một chunk là toàn bộ Điều.
- **FR-010**: Fallback MUST chỉ gồm `grounded_answer`, `verified_source_condensed` và `source_view_only`; condensed chỉ hợp lệ khi từng claim có evidence đủ điều kiện.
- **FR-011**: Cùng input và cùng phiên bản dữ liệu MUST có claim, citation, mode và validity decision xác định; mọi đường sinh pháp lý dùng cấu hình không ngẫu nhiên và tie-break cố định.
- **FR-012**: Source gap MUST đi qua hàng đợi admin hiện có; không tự nhập, tự duyệt hoặc tự kích hoạt văn bản.
- **FR-013**: Learned reranker MUST chạy sau hard gate, có shadow benchmark, resource fallback và không thay active ranking khi chưa đạt cổng.
- **FR-014**: Embedding thứ hai MUST chỉ chạy shadow cho đến khi đạt cổng chất lượng/tài nguyên; không trộn vector từ hai model trong cùng collection.
- **FR-015**: Citation MUST công bố một trong ba cấp `physical_span`, `content_quote`, `metadata_only`; chỉ hai cấp đầu được hỗ trợ claim khi validation đạt.
- **FR-016**: Provenance MUST giữ source hash, extractor/version, page/offset/bounding box hoặc table path và text hash khi nguồn cho phép.
- **FR-017**: Audit pháp lý quan trọng MUST tạo hash chain và checkpoint ký ngoài database; sửa/xóa/chèn/đảo phải phát hiện được.
- **FR-018**: Cloud egress MUST redact PII, công bố provider mode và không ghi raw sensitive prompt vào audit.
- **FR-019**: OCR/layout MUST giữ block type, trang, tọa độ/confidence và cấu trúc bảng; optional parser lỗi phải fallback rõ ràng.
- **FR-020**: Multi-hop MUST chỉ theo quan hệ đã xác minh, tối đa hai vòng/mười sáu query và tái áp dụng toàn bộ hard gate ở mỗi hop.
- **FR-021**: Các safety gate về hiệu lực, grounding, role và citation MUST giữ nguyên trên mọi profile CPU/GPU; chỉ tính năng shadow/nền được giảm khi máy yếu.
- **FR-022**: Kiểm thử trực tiếp trên web MUST không tự chạy trong đợt triển khai này; sau khi mọi kiểm thử mã/API không-web đạt, hệ thống triển khai phải dừng và xin phép người dùng.

### Legal Grounding and Safety *(mandatory for legal-answer or legal-data features)*

- **Applicable sources**: Văn bản chính thức từ VBPL, Cổng Chính phủ và nguồn đã được quản trị viên duyệt; giữ số hiệu, cơ quan, thẩm quyền, phạm vi, thời điểm hiệu lực và URL.
- **Citation behavior**: Mỗi claim liên kết evidence đúng issue/facet; không để LLM tự khai trang/offset/quote. Citation vật lý được sinh từ provenance đã xác minh.
- **Insufficient evidence**: Từ chối hoặc `source_view_only` với reason code; không dùng kiến thức chung, web fallback tự do hoặc đoạn gần giống.
- **Expired or superseded material**: Lưu để tra cứu lịch sử nhưng chặn khỏi trả lời hiện hành; hiệu lực một phần được kiểm tra đến cấp quy định.

### Roles, Ownership, and Privacy *(mandatory when accounts or private data are involved)*

| Role | May access | Must not access | Server-side enforcement |
|------|------------|-----------------|-------------------------|
| Citizen | Câu trả lời công khai và citation an toàn của chính phiên hỏi | Admin trace, audit payload, dữ liệu riêng tài khoản khác | Auth middleware, response projection, ownership tests |
| Officer | Câu trả lời/citation trong lĩnh vực được cấp và dữ liệu nghiệp vụ được phép | Lĩnh vực không được cấp, admin trace, dữ liệu tài khoản khác | Allowed-domain filter và server-side authorization |
| Admin | Trace, integrity report, hàng đợi source gap và công cụ quản trị | Raw secret/private key; dữ liệu ngoài phạm vi quản trị | Admin guard, audit và secret isolation |

- **Sensitive data**: Câu hỏi có PII, hồ sơ riêng, token, provider key, raw prompt, audit detail, private signing key.
- **Negative scenarios**: Đổi ID/role/domain trực tiếp qua API, đọc trace admin, dùng metadata-only làm claim, gửi PII chưa redact ra cloud.
- **Audit behavior**: Ghi ID/hash/reason code/phiên bản và quyết định; không ghi secret hoặc raw PII.

### Key Entities

- **GoldenCaseV2**: Ca kiểm thử duy nhất, vai trò, thời điểm áp dụng, nguồn/facts/proof/fallback mong đợi.
- **StructuredQARun**: Kết quả một lượt chạy có cấu trúc, phiên bản runtime, timings và kết quả chấm.
- **LegalIntent**: Ý định pháp lý chuẩn hóa cùng confidence và ambiguity.
- **EvidenceCandidate**: Chunk có metadata hiệu lực, procedure/facet và provenance xếp hạng.
- **AnswerClaim**: Mệnh đề trả lời liên kết evidence và trạng thái validation.
- **CitationProvenance**: Vị trí/hash vật lý hoặc quote đã xác minh.
- **LegalAuditEntry**: Sự kiện pháp lý quan trọng trong chuỗi hash có checkpoint ký.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Baseline có đúng 100 câu duy nhất, 20 câu cho mỗi lĩnh vực, truy nguyên được tới workbook có checksum.
- **SC-002**: 100% ca dùng văn bản/phần văn bản hết hiệu lực bị chặn khỏi câu trả lời hiện hành.
- **SC-003**: 0 câu dùng nguồn sai procedure hoặc sai facet để tạo kết luận trong bộ regression đã duyệt.
- **SC-004**: Tối thiểu 95% ca regression đạt toàn bộ rubric; mọi ca còn lại phải fail closed thay vì trả lời sai.
- **SC-005**: Unexpected fallback không quá 3%; technical provider fallback không quá 2% trong môi trường nghiệm thu có quota hợp lệ.
- **SC-006**: Claim, citation, answer mode và validity decision tái lập 100% qua mười lần lặp khi mọi phiên bản không đổi.
- **SC-007**: Correct document/article tối thiểu 99%, critical-fact coverage tối thiểu 95%, thứ tự cấu trúc 100% trên golden v2 hoàn chỉnh.
- **SC-008**: Citation bịa, sai nguồn, sai trang hoặc sai quote là hard fail; sửa/xóa/đảo audit được phát hiện 100%.
- **SC-009**: Retrieval P95 không quá 3 giây; API P95 không quá 25 giây; timeout/error dưới 0,5% trong profile nghiệm thu.
- **SC-010**: Profile mục tiêu phục vụ 5.000 tài khoản, 1.000 người hoạt động/ngày, cao điểm 30 lượt Ask đồng thời và 3 request/giây trong mười phút sau tối ưu, mà không tắt safety gate.
- **SC-011**: Trước khi kiểm thử web thật, toàn bộ unit/contract/integration/API-isolated test áp dụng phải đạt và người dùng nhận yêu cầu phê duyệt riêng.

## Assumptions

- PostgreSQL, SurrealDB và kho vector hiện tại được giữ; không chọn lại kiến trúc lưu trữ trong feature này.
- VNLegal-LAL là embedding active; learned reranker được ưu tiên trước embedding shadow thứ hai.
- Không thêm paid service hoặc daemon mới; provider hiện có được giữ với egress policy chặt hơn.
- Migration mới chỉ được viết và rehearsal cô lập; không áp live, backfill corpus thật hoặc re-index vector thật khi chưa có phê duyệt riêng.
- Không thay đổi crawler governance và không tái kích hoạt crawl tự động cho cán bộ.
- Kiểm thử trình duyệt 2.000 lượt là cổng cuối riêng, không nằm trong đợt tự động hiện tại theo yêu cầu người dùng.
