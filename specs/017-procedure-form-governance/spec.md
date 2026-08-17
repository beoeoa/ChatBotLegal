# Feature Specification: Quản trị thủ tục, biểu mẫu và Golden V3

**Feature Branch**: `017-procedure-form-governance`

**Created**: 2026-08-11

**Status**: Approved for implementation; live migration, publication, browser UAT and production activation remain separately gated

**Input**: User-approved Feature 017 plan for governing commune-level procedures, official forms, deterministic form selection and Golden V3.

## User Scenarios & Testing

### User Story 1 - Cán bộ đề xuất và bổ sung biểu mẫu đúng lĩnh vực (Priority: P1)

Cán bộ gửi URL hoặc file biểu mẫu chính thức cho một thủ tục thuộc lĩnh vực được phân công, theo dõi trạng thái và bổ sung đúng phiên bản hồ sơ khi Admin yêu cầu.

**Why this priority**: Đây là đầu vào của toàn bộ chuỗi quản trị; nếu không kiểm soát lĩnh vực, nguồn và phiên bản thì các bước xác nhận sau không đáng tin cậy.

**Independent Test**: Dùng dữ liệu cô lập để cán bộ tạo đề xuất trong lĩnh vực, bị từ chối khi sửa `domain/procedure_id`, nhận yêu cầu bổ sung và gửi lại phiên bản mới mà lịch sử cũ vẫn còn.

**Acceptance Scenarios**:

1. **Given** cán bộ đã xác thực và thủ tục thuộc lĩnh vực được gán, **When** gửi URL/file và metadata tối thiểu, **Then** hệ thống tạo ca `submitted`, lưu nguồn/checksum và ghi audit.
2. **Given** thủ tục ngoài lĩnh vực cán bộ, **When** cán bộ gửi hoặc sửa trực tiếp ID qua API, **Then** server từ chối và không tạo/đổi dữ liệu.
3. **Given** ca ở `needs_supplement`, **When** cán bộ nộp thông tin/file mới, **Then** ca chuyển `resubmitted`, tăng phiên bản và giữ lịch sử cũ.
4. **Given** một ca không thuộc cán bộ hiện tại, **When** truy cập bằng ID, **Then** server không trả nội dung riêng tư của ca.

---

### User Story 2 - Admin duyệt nguồn và xác nhận pháp lý qua hai bước riêng (Priority: P1)

Admin xử lý đề xuất bằng ba hành động đầu vào, hoàn thiện metadata pháp lý qua wizard và xác nhận pháp lý bằng một thao tác tách biệt với duyệt nguồn.

**Why this priority**: Duyệt nguồn không đồng nghĩa biểu mẫu đủ điều kiện pháp lý hoặc được công khai; tách hai hành động giảm phát hành nhầm.

**Independent Test**: Tạo một ca submitted cô lập, duyệt nguồn, kiểm tra chưa runtime-eligible, hoàn thiện wizard, xác nhận với checksum đúng và chứng minh checksum/reviewer bị sửa làm thao tác thất bại.

**Acceptance Scenarios**:

1. **Given** ca `submitted/resubmitted`, **When** Admin yêu cầu bổ sung, **Then** trạng thái thành `needs_supplement`, ghi rõ trường/nội dung cần bổ sung và gửi thông báo.
2. **Given** ca có nguồn chính thức hợp lệ, **When** Admin duyệt nguồn, **Then** trạng thái thành `source_approved` nhưng người dân chưa thể thấy hoặc tải mẫu.
3. **Given** ca đã duyệt nguồn, **When** Admin hoàn thiện thủ tục, biểu mẫu, căn cứ, hiệu lực, đối tượng, điều kiện, alias và checksum, **Then** preview xác nhận hiển thị toàn bộ dữ liệu ràng buộc.
4. **Given** preview hợp lệ, **When** Admin thực hiện xác nhận pháp lý riêng, **Then** trạng thái thành `attested`; thay ID/checksum/reviewer sau preview phải fail-closed.
5. **Given** nguồn không hợp lệ, **When** Admin từ chối, **Then** ca thành `rejected`, có lý do và không thể vào release candidate.

---

### User Story 3 - Phát hành nguyên tử và đồng bộ trạng thái (Priority: P1)

Admin dựng release candidate trong staging, chạy release gate và chỉ chuyển active pointer nguyên tử khi mọi kiểm tra đạt; cán bộ nhận trạng thái và citizen chỉ đọc active release.

**Why this priority**: Đây là ranh giới giữa dữ liệu nghiệp vụ và dữ liệu công khai; lỗi nửa chừng không được làm hỏng catalog đang phục vụ.

**Independent Test**: Dựng manifest từ fixture attested, cho một checksum sai để chứng minh pointer không đổi; sửa fixture, phát hành, kiểm tra citizen thấy đúng bản và rollback pointer khôi phục bản trước.

**Acceptance Scenarios**:

1. **Given** tập attested hợp lệ, **When** Admin dựng release candidate, **Then** hệ thống tạo manifest có checksum, catalog/binding/alias snapshot và rollback pointer.
2. **Given** bất kỳ tham chiếu, nguồn, hiệu lực, role hoặc checksum không đạt, **When** chạy release gate, **Then** candidate bị chặn/quarantined và active release giữ nguyên.
3. **Given** release gate đạt, **When** Admin phát hành, **Then** active pointer đổi nguyên tử, workflow/audit được ghi và cán bộ đề xuất nhận thông báo.
4. **Given** active release mới có lỗi vận hành, **When** Admin rollback, **Then** pointer về manifest trước mà không xóa lịch sử hoặc asset.

---

### User Story 4 - Người dân nhận đúng bộ biểu mẫu xác định (Priority: P1)

Khi người dân hỏi về thủ tục/biểu mẫu, hệ thống xác định `procedure_id`, đọc binding đã phát hành và chỉ đưa các file/e-form vượt qua cổng hiệu lực, vai trò, điều kiện và checksum; DeepSeek chỉ diễn giải.

**Why this priority**: Trả sai biểu mẫu có thể khiến hồ sơ bị trả lại; lựa chọn phải xác định từ dữ liệu đã duyệt, không dựa vào trí nhớ LLM.

**Independent Test**: Hỏi exact code/tên/alias, câu mơ hồ và mã “Mẫu 01” dùng chung; đối chiếu toàn bộ form ID trả về với active release và tắt provider để chứng minh tập form không đổi.

**Acceptance Scenarios**:

1. **Given** exact code/tên/alias duy nhất, **When** người dân hỏi, **Then** router xác định một `procedure_id` và trả đủ tất cả mẫu bắt buộc đã phát hành.
2. **Given** mẫu có điều kiện, **When** điều kiện chưa được người dùng xác nhận, **Then** mẫu được gắn nhãn điều kiện; hệ thống không tự suy đoán áp dụng.
3. **Given** nhiều thủ tục ngang điểm hoặc “Mẫu 01” thiếu thủ tục/văn bản, **When** hỏi, **Then** trả `clarifying_questions`, không chọn form.
4. **Given** e-form chính thức, **When** hỏi, **Then** trả liên kết chính thức thay vì tạo file giả.
5. **Given** không có mẫu chính thức hoặc coverage chưa quyết định, **When** hỏi, **Then** fail-closed, tạo source-gap và không bịa tên/link.
6. **Given** provider lỗi, **When** tập binding đã xác định, **Then** hệ thống vẫn có thể trả dữ liệu nguồn xác định ở mode an toàn; provider không thay đổi form ID.

---

### User Story 5 - Admin quản lý coverage 191/131/229 (Priority: P2)

Admin xem coverage theo lĩnh vực, thủ tục, identity, binding và trạng thái để kết thúc từng trường hợp bằng `released` hoặc `verified_gap` thay vì trạng thái mơ hồ.

**Why this priority**: Golden V3 và độ đầy đủ công khai chỉ có ý nghĩa khi toàn bộ phạm vi có quyết định xác định.

**Independent Test**: Nạp snapshot baseline read-only, kiểm tra tổng theo ba trục và bộ lọc; chứng minh một identity dùng nhiều thủ tục chỉ có một asset nhưng nhiều binding.

**Acceptance Scenarios**:

1. **Given** baseline 191 thủ tục/131 identity/229 binding, **When** xem coverage, **Then** số liệu được tính từ nguồn chuẩn PostgreSQL hoặc rehearsal fixture, không cộng lặp asset.
2. **Given** một identity chưa đủ nguồn, **When** Admin đánh dấu `verified_gap` có lý do/chứng cứ, **Then** coverage được quyết định nhưng không tạo runtime form.
3. **Given** một asset dùng nhiều thủ tục, **When** phát hành, **Then** chỉ lưu một asset và nhiều binding độc lập.

---

### User Story 6 - Sinh và kiểm định Golden V3 1.000 câu (Priority: P2)

Sau khi active release có quyết định coverage 100%, hệ thống sinh 1.000 ca từ manifest, xuất workbook để người dùng duyệt và chỉ đóng băng checksum sau phê duyệt.

**Why this priority**: Bộ Golden đo đúng journey thủ tục/biểu mẫu và không biến cách diễn đạt do LLM sinh thành ground truth pháp lý.

**Independent Test**: Dùng release fixture đủ coverage để sinh đúng 1.000 ca/5 lĩnh vực, validate schema/phân bố/ground truth; thử release thiếu coverage phải dừng với reason code rõ.

**Acceptance Scenarios**:

1. **Given** active release đủ coverage, **When** sinh Golden V3, **Then** tạo 200 ca/lĩnh vực và đúng cơ cấu 400/300/100/100/100.
2. **Given** một thủ tục còn hiệu lực, **When** kiểm tra dataset, **Then** thủ tục xuất hiện tối thiểu ba cách hỏi.
3. **Given** cách diễn đạt do DeepSeek đề xuất, **When** tạo case, **Then** `procedure_id/form_ids/condition/source/checksum/state` chỉ lấy từ manifest.
4. **Given** workbook chưa được người dùng duyệt, **When** chạy freeze, **Then** hệ thống từ chối tạo checksum approved.

### Edge Cases

- URL nguồn thay đổi nội dung sau duyệt; checksum hoặc ETag không còn khớp.
- Hai đề xuất trỏ đến cùng asset bằng URL khác nhau hoặc tên mã khác kiểu chữ.
- Một mã mẫu được tái sử dụng bởi nhiều văn bản/thủ tục.
- Văn bản ban hành hết hiệu lực trong lúc candidate đang chờ phát hành.
- `legal_as_of` lịch sử được yêu cầu nhưng public catalog chỉ phục vụ hiện hành.
- Officer role/domain claim trong request bị sửa; server phải dùng assignment trong DB.
- Admin duyệt nguồn rồi sửa metadata/file trước attestation.
- Release candidate được tạo từ snapshot stale hoặc active pointer đã đổi bởi tác vụ khác.
- E-form không có file checksum nhưng phải có URL chính thức và provenance checksum của metadata snapshot.
- Provider timeout/JSON lỗi; deterministic selection và citation vẫn giữ nguyên.
- Một thủ tục không có biểu mẫu chính thức; phải phân biệt với thiếu nguồn chưa xác minh.

## Requirements

### Functional Requirements

- **FR-001**: PostgreSQL MUST là nguồn chuẩn duy nhất cho trạng thái pháp lý của thủ tục, biểu mẫu, binding, alias, review, release và workflow event.
- **FR-002**: SurrealDB MUST chỉ lưu notification/inbox projection; projection failure không được thay đổi legal state hoặc active release.
- **FR-003**: JSON catalog hiện tại MUST được giữ read-only qua compatibility adapter cho đến khi live migration được phê duyệt.
- **FR-004**: Hệ thống MUST triển khai đầy đủ state machine và từ chối mọi transition không nằm trong bảng chuyển trạng thái đã định nghĩa.
- **FR-005**: Server MUST kiểm tra domain assignment và ownership cho mọi officer operation; không tin role/domain/procedure từ client.
- **FR-006**: Admin source approval và legal attestation MUST là hai thao tác và hai workflow event riêng.
- **FR-007**: Source approval MUST NOT làm asset/binding đủ điều kiện runtime hoặc công khai.
- **FR-008**: `needs_supplement/resubmitted` MUST giữ mọi phiên bản nguồn/file/checksum và ghi người thực hiện, thời gian, lý do.
- **FR-009**: Legal attestation MUST bind procedure/form/source/legal metadata/file checksum/reviewer preview; tampering làm fail-closed.
- **FR-010**: Release MUST được dựng ở staging, kiểm tra đầy đủ và chuyển active pointer nguyên tử; lỗi giữ active release cũ.
- **FR-011**: Rollback MUST chuyển pointer về manifest trước mà không hard-delete lịch sử.
- **FR-012**: Citizen MUST chỉ đọc dữ liệu `released`, còn hiệu lực, đúng role và active release.
- **FR-013**: Coverage MUST được tính riêng cho procedure, identity và binding; mỗi item kết thúc bằng released/verified_gap hoặc decision tương đương được chỉ rõ.
- **FR-014**: Một form asset dùng nhiều procedure MUST chỉ lưu một asset và nhiều binding; duplicate identity phải được phát hiện bằng normalized identity/source/checksum.
- **FR-015**: Form Router MUST ưu tiên exact procedure code/name/approved alias trước candidate retrieval.
- **FR-016**: BM25/vector, nếu dùng, MUST chỉ tìm candidate procedure; chúng không được quyết định `form_id`.
- **FR-017**: Nếu nhiều candidate procedure ngang ngưỡng hoặc mã form không duy nhất, hệ thống MUST trả `clarifying_questions` và không xuất form.
- **FR-018**: Form set MUST lấy từ active SQL binding và vượt qua release, effectivity, audience/role, condition và checksum gate.
- **FR-019**: Hệ thống MUST trả mọi required binding; conditional binding phải kèm nhãn/điều kiện; không suy đoán điều kiện cá nhân.
- **FR-020**: E-form MUST trả official URL; hệ thống MUST NOT tạo file/link giả.
- **FR-021**: Không có form chính thức hoặc chưa đủ nguồn MUST fail-closed và tạo source-gap có reason code.
- **FR-022**: LLM/provider MUST chỉ sinh diễn giải từ Evidence Packet; không được sinh/chọn `procedure_id`, `form_id`, condition hoặc download URL.
- **FR-023**: Tập form/citation/validity decision MUST giống nhau giữa local/cloud và khi provider unavailable.
- **FR-024**: Existing `/api/search/ask` và `recommended_forms` MUST tương thích; endpoint cũ dùng adapter trong giai đoạn chuyển đổi.
- **FR-025**: API mới MUST nằm dưới `/api/procedures/forms-catalog` và hỗ trợ submit, supplement, review, enrichment, preview, attestation, release, rollback và coverage.
- **FR-026**: Mọi hành động nhạy cảm MUST ghi append-only workflow event/audit, không ghi credential, raw PII hoặc private path ra public response.
- **FR-027**: Golden V3 MUST chỉ sinh ground truth từ active release manifest; model chỉ được tạo paraphrase chưa duyệt.
- **FR-028**: Golden builder MUST tạo đúng 1.000 ca theo phân bố và schema, hoặc dừng nếu coverage chưa 100%.
- **FR-029**: Workbook review MUST có trạng thái duyệt; automation không được tự nâng lên approved hoặc đóng băng checksum.
- **FR-030**: Additive migration MUST có forward/rollback rehearsal trên database cô lập và không được tự apply vào live DB.
- **FR-031**: Feature flag MUST cho phép citizen rollout trước và giữ legacy adapter làm rollback; không được rollback bằng cách tắt safety gate.

### Legal Grounding and Safety

- **Applicable sources**: Cổng DVC/official portals, VBPL, cổng Hải Phòng, văn bản ban hành và file/e-form chính thức; lưu authority, jurisdiction, effective dates, document identity, URL và checksum.
- **Citation behavior**: Tên thủ tục/mẫu, văn bản ban hành, ngày hiệu lực, URL và checksum/provenance phải đi theo asset/binding vào Evidence Packet; LLM không được tự khai báo nguồn.
- **Insufficient evidence**: `verified_gap`, `source_gap`, `needs_clarification` hoặc mode nguồn an toàn; không tạo tên mẫu/link để lấp thiếu.
- **Expired or superseded material**: Giữ lịch sử/audit nhưng không phục vụ current citizen answer; thay thế phiên bản phải gắn quan hệ và release mới.

### Roles, Ownership, and Privacy

| Role | May access | Must not access | Server-side enforcement |
|------|------------|-----------------|-------------------------|
| Citizen | Active released catalog, public official links | Candidate, review notes, private files, audit/admin trace | Active release + eligibility gates |
| Officer | Own/domain-scoped cases, supplementation, status | Other domain/case, approve, attest, release | Auth assignment + ownership/domain predicate |
| Admin | Review, enrich, attest, stage/release/rollback, coverage | Bypass transition/checksum/audit or publish at source approval | Admin ACL + state machine + attestation/release gates |

- **Sensitive data**: officer identity, reviewer notes, attachment storage paths, request metadata, checksum/audit internals and any dossier content.
- **Negative scenarios**: ID tampering, cross-domain access, forged role, stale preview, forged checksum, direct candidate download, admin trace leakage.
- **Audit behavior**: Append-only workflow event with actor/role/action/from/to/object/version/reason/timestamp and integrity link; public responses expose no raw audit detail.

### Key Entities

- **LegalProcedure**: Canonical commune procedure identity, domain, authority, effectivity and official source.
- **LegalFormAsset**: Deduplicated file/e-form identity, source, checksum, effectivity and allowed audience.
- **ProcedureFormBinding**: Required/conditional/no-form relation between procedure and asset with condition and legal provenance.
- **ProcedureQuestionAlias**: Approved exact/alias/negative phrasing used only to identify procedure candidates.
- **FormReviewCase**: Officer proposal and versioned review lifecycle.
- **FormRelease**: Immutable manifest, validation report, rollback pointer and active state.
- **FormWorkflowEvent**: Append-only legal workflow audit event.
- **FormSourceGap**: Deterministic decision that no official form exists or evidence remains missing.
- **GoldenCaseV3**: Manifest-derived user journey case with expected/forbidden form set and legal state.

## Success Criteria

### Measurable Outcomes

- **SC-001**: 100% trong 191 procedure có coverage decision.
- **SC-002**: 100% trong 131 form identity là released hoặc verified_gap.
- **SC-003**: 100% trong 229 binding có quyết định xác định.
- **SC-004**: 100% released file/e-form vượt source, effectivity, role và checksum/link gate.
- **SC-005**: Correct procedure ≥99% và exact form set =100% trên Golden V3.
- **SC-006**: Ambiguous/hard-negative yêu cầu làm rõ 100%; không trả thêm mẫu sai thủ tục.
- **SC-007**: Conditional labels, expired blocking và role gates đúng 100%.
- **SC-008**: Golden V3 đạt ≥95%; mọi ca còn lại fail-closed, không sai có vẻ chắc chắn.
- **SC-009**: Unexpected fallback ≤3%, provider error <0.5%; retrieval P95 ≤3 giây và API P95 ≤25 giây trong isolated benchmark.
- **SC-010**: Release gate failure không đổi active pointer; rollback khôi phục release trước và giữ 100% audit/history.
- **SC-011**: Toàn bộ unit/contract/integration/frontend build đạt trước khi xin phép browser UAT.

## Assumptions

- Phạm vi khóa ở 191 thủ tục cấp xã thuộc 5 lĩnh vực; baseline 131/229 được tính lại khi official catalog refresh được duyệt.
- Một Admin được thực hiện hai bước source approval và legal attestation, nhưng hai thao tác bắt buộc tách biệt.
- DeepSeek V4 Flash chỉ sinh diễn giải/paraphrase; không fine-tune và không quyết định ground truth.
- BGE vẫn tắt cho form selection; PostgreSQL release manifest là nguồn quyết định.
- Live migration, official-source campaign, auto approval/release, browser UAT và production activation không thuộc quyền tự động của lát triển khai đầu.
- 100 identity còn thiếu cần cán bộ/Admin xử lý; code có thể tạo queue, rehearsal fixtures và báo cáo nhưng không tự tuyên bố nguồn hợp lệ.

