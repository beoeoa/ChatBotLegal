# Feature Specification: Hoàn thiện sản phẩm để phát hành Internet

**Feature Branch**: `018-production-release-readiness`

**Created**: 2026-08-13

**Status**: Approved for implementation

**Input**: Hoàn thiện và phát hành hệ thống Chatbot Pháp luật Hải Phòng cho người dân, cán bộ và quản trị viên; thống nhất hỏi đáp, quản lý vòng đời văn bản, thủ tục/biểu mẫu/FAQ, hỗ trợ trực tuyến, dashboard, audit, bảo mật, khả năng chịu tải và quy trình Go/No-Go.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Nhận câu trả lời pháp lý nhất quán và có căn cứ (Priority: P1)

Người dân hoặc cán bộ đặt câu hỏi bằng ngôn ngữ đời thường về văn bản, điều luật, hồ sơ, thủ tục, biểu mẫu hoặc tình huống kết hợp và nhận một câu trả lời dễ đọc, có nguồn, đúng hiệu lực và không bị mất toàn bộ nội dung chỉ vì một phần bằng chứng chưa đủ.

**Why this priority**: Đây là chức năng cốt lõi và rủi ro pháp lý cao nhất của sản phẩm.

**Independent Test**: Dùng bộ câu hỏi đã duyệt và các câu hỏi đời thường mới để kiểm tra đúng văn bản/thủ tục/biểu mẫu, nguồn trích dẫn, hiệu lực, phần còn thiếu và định dạng câu trả lời.

**Acceptance Scenarios**:

1. **Given** có đủ nguồn hiện hành đã duyệt, **When** người dùng hỏi một điều luật hoặc thủ tục, **Then** hệ thống trả phần đã xác minh theo một định dạng thống nhất kèm nguồn phù hợp.
2. **Given** chỉ một phần câu hỏi có đủ căn cứ, **When** hệ thống kiểm định từng ý, **Then** ý đúng được giữ lại và phần thiếu được cảnh báo hoặc hỏi làm rõ thay vì chặn toàn bộ câu trả lời.
3. **Given** câu hỏi yêu cầu văn bản tại thời điểm quá khứ, **When** tài liệu cũ phù hợp với ngày áp dụng, **Then** hệ thống gắn nhãn lịch sử và không trình bày như quy định hiện hành.
4. **Given** thủ tục chưa được xác định chắc chắn, **When** người dùng hỏi biểu mẫu, **Then** hệ thống yêu cầu làm rõ và không tự gắn biểu mẫu.

---

### User Story 2 - Sử dụng chat thuận tiện trên desktop và mobile (Priority: P1)

Người dùng có thể đọc hội thoại dài, quay lại tin mới nhất và tạo cuộc trò chuyện mới mà không phải cuộn toàn trang.

**Why this priority**: Giao diện hiện tại gây khó khăn trực tiếp trong thao tác hỏi đáp, đặc biệt với người ít kinh nghiệm công nghệ.

**Independent Test**: Tạo hội thoại dài trên các kích thước màn hình 360, 768 và 1440 pixel; kiểm tra vùng cuộn, ô nhập, nút tạo chat mới, phân trang và trạng thái chat mới.

**Acceptance Scenarios**:

1. **Given** hội thoại có nhiều tin nhắn, **When** người dùng cuộn nội dung, **Then** ô nhập và nút tạo cuộc trò chuyện mới luôn có thể truy cập.
2. **Given** người dùng đang đọc nội dung cũ, **When** có tin mới, **Then** hệ thống không giật về cuối và cung cấp nút xuống tin mới nhất.
3. **Given** người dùng tạo chat mới, **When** phiên mới mở, **Then** không mang theo lĩnh vực, biểu mẫu, câu trả lời hoặc trạng thái của phiên cũ.

---

### User Story 3 - Gửi và theo dõi yêu cầu hỗ trợ trực tuyến (Priority: P1)

Người dân chọn hoặc xác nhận lĩnh vực, gửi nội dung/tệp và theo dõi vị trí chờ; cán bộ nhận đúng lĩnh vực theo tải hiện tại; quản trị viên theo dõi SLA mà không mặc định đọc nội dung riêng tư.

**Why this priority**: Cách mở kết nối hiện tại không phù hợp khi có 1.000 người chờ và chỉ 30 cán bộ.

**Independent Test**: Mô phỏng 1.000 ticket, 30 cán bộ và tối đa 3 phiên hoạt động mỗi cán bộ; kiểm tra không nhận trùng, không sai lĩnh vực, người chờ không duy trì kết nối realtime và ticket được trả lại hàng chờ khi cán bộ mất kết nối.

**Acceptance Scenarios**:

1. **Given** người dân chưa chọn lĩnh vực, **When** gửi yêu cầu, **Then** hệ thống hiển thị bước chọn/xác nhận rõ ràng thay vì chỉ báo lỗi.
2. **Given** nhiều cán bộ cùng nhận việc, **When** allocator phân công, **Then** một ticket chỉ được giao một lần cho cán bộ đúng lĩnh vực còn dung lượng.
3. **Given** người dân đang chờ, **When** rời trang và quay lại, **Then** vẫn xem được yêu cầu, vị trí và trạng thái của mình.
4. **Given** quản trị viên muốn xem nội dung hỗ trợ, **When** mở ticket, **Then** phải nhập lý do và hành động được audit.

---

### User Story 4 - Quản lý vòng đời văn bản và tác động pháp lý (Priority: P1)

Quản trị viên xem được văn bản đang hiệu lực, sắp có hiệu lực, sắp hết hiệu lực theo các mốc, đã hết hiệu lực, hiệu lực một phần, bị thay thế và chưa sẵn sàng tìm kiếm; hệ thống giữ lịch sử nhưng không dùng sai văn bản cho câu trả lời hiện hành.

**Why this priority**: Sai hiệu lực hoặc dùng văn bản cũ là lỗi phát hành nghiêm trọng.

**Independent Test**: Dựng các ca hiệu lực toàn bộ, một phần, thay thế khác nội dung, đình chỉ/khôi phục và thiếu vector; kiểm tra cảnh báo, impact scan, serving state, lịch sử và rollback.

**Acceptance Scenarios**:

1. **Given** văn bản sẽ hết hiệu lực trong 30 ngày, **When** quản trị viên mở dashboard, **Then** thấy cảnh báo dễ hiểu và danh sách đã lọc để xử lý.
2. **Given** văn bản đã hết hiệu lực, **When** câu hỏi hiện hành được xử lý, **Then** văn bản bị loại nhưng vẫn còn trong tra cứu lịch sử và audit.
3. **Given** văn bản mới thay thế có nội dung khác, **When** quan hệ được xác nhận, **Then** hai bản giữ identity riêng, phần phụ thuộc chuyển sang cần rà soát và nội dung cũ không được dùng cho hiện hành.
4. **Given** văn bản mới chưa vector hóa xong, **When** serving hiện hành chạy, **Then** hệ thống không bật lại văn bản cũ và hiển thị khoảng trống nguồn cần xử lý.

---

### User Story 5 - Quản trị thủ tục, biểu mẫu và FAQ không cần nhớ mã kỹ thuật (Priority: P2)

Cán bộ đề xuất nguồn hoặc tệp; quản trị viên duyệt theo từng bước rõ ràng, tìm thủ tục bằng tên/lĩnh vực, gắn file hoặc liên kết và phát hành có kiểm soát; người dân chỉ thấy dữ liệu đã phát hành.

**Why this priority**: Luồng hiện tại yêu cầu nhớ mã và dùng nút “Duyệt” không nói rõ kết quả bước tiếp theo.

**Independent Test**: Chạy hành trình đề xuất → bổ sung → duyệt nguồn → hoàn thiện pháp lý → xác nhận → release; kiểm tra biểu mẫu/FAQ chưa release không xuất hiện và bản đã release có đúng checksum/thủ tục/role/hiệu lực.

**Acceptance Scenarios**:

1. **Given** quản trị viên không nhớ mã thủ tục, **When** hoàn thiện biểu mẫu, **Then** có thể tìm theo tên hoặc lĩnh vực và xem mã như thông tin phụ.
2. **Given** nguồn mới chỉ được duyệt, **When** quản trị viên hoàn tất thao tác, **Then** hệ thống hiển thị rõ bước kế tiếp và dữ liệu chưa được công khai.
3. **Given** FAQ gắn một thủ tục đã xác nhận, **When** thủ tục có biểu mẫu phát hành, **Then** FAQ lấy biểu mẫu từ bản phát hành hiện hành thay vì nhập mã mẫu thủ công.
4. **Given** tệp sai checksum, sai role, hết hiệu lực hoặc chưa phát hành, **When** người dân hỏi, **Then** tệp không xuất hiện.

---

### User Story 6 - Vận hành hệ thống bằng giao diện dễ hiểu và audit đầy đủ (Priority: P2)

Quản trị viên nhìn thấy việc cần xử lý hôm nay, tình trạng hệ thống và log hoạt động của người dân/cán bộ/admin bằng tiếng Việt dễ hiểu; chi tiết kỹ thuật được thu gọn nhưng vẫn có thể truy vết.

**Why this priority**: Dashboard hiện có quá nhiều chỉ số kỹ thuật, khó xác định hành động cần làm.

**Independent Test**: Dùng dữ liệu mô phỏng cho cảnh báo support, hiệu lực, vector, import, biểu mẫu, FAQ, nguồn và model; kiểm tra mỗi card có ảnh hưởng, hành động chính, danh sách lọc và audit.

**Acceptance Scenarios**:

1. **Given** có nhiều loại sự cố, **When** admin mở dashboard, **Then** các mục được ưu tiên theo tác động và có một hành động chính rõ ràng.
2. **Given** admin lọc Trung tâm hoạt động, **When** chọn role/module/thời gian/kết quả, **Then** thấy mô tả dễ hiểu và có thể xuất báo cáo.
3. **Given** sự kiện chứa dữ liệu nhạy cảm, **When** admin mở chi tiết, **Then** phải nhập lý do và quyền truy cập được audit.

---

### User Story 7 - Phát hành an toàn và chịu tải thực tế (Priority: P2)

Đội vận hành có thể triển khai nhiều phiên bản dịch vụ, giám sát, sao lưu, khôi phục và rollback mà không làm mất dữ liệu hoặc vô hiệu hóa cổng an toàn pháp lý.

**Why this priority**: Hệ thống chưa có đủ bằng chứng load, security và restore để phát hành Internet.

**Independent Test**: Chạy load, security, backup/restore và canary; xác nhận các chỉ tiêu và điều kiện Go/No-Go bằng artefact có thể kiểm tra lại.

**Acceptance Scenarios**:

1. **Given** 1.000 người đăng nhập/chờ và 100 yêu cầu hỏi đáp đồng thời, **When** chạy bài kiểm thử tải, **Then** hệ thống đạt SLA đã khóa mà không mất ticket hoặc vượt quyền.
2. **Given** một release lỗi, **When** rollback, **Then** dữ liệu lịch sử/audit còn nguyên và các cổng hiệu lực, role, thủ tục, citation, checksum vẫn hoạt động.
3. **Given** mất dữ liệu dịch vụ, **When** chạy restore rehearsal, **Then** bản phát hành, file, vector và trạng thái hiệu lực được đối chiếu đúng fingerprint trong RPO/RTO quy định.

### Edge Cases

- Một câu chứa nhiều ý nhưng chỉ một ý có đủ nguồn.
- Hai thủ tục có tên/alias gần nhau hoặc dùng cùng mã biểu mẫu.
- Văn bản hết hiệu lực một phần, bị sửa nhiều lần hoặc có quan hệ thay thế chưa xác nhận.
- Văn bản thay thế đã duyệt metadata nhưng index chưa sẵn sàng.
- Tệp biểu mẫu đổi nội dung nhưng URL không đổi hoặc checksum không khớp.
- Cán bộ mất heartbeat khi đang giữ ticket; hai cán bộ nhận cùng lúc.
- Người dân đóng trình duyệt khi đang chờ rồi đăng nhập lại.
- Provider AI timeout, trả JSON lỗi hoặc không hỗ trợ tắt reasoning.
- Database thông báo lỗi trong khi nguồn pháp lý chính vẫn hoạt động.
- Route cũ còn truy cập trực tiếp dù đã bị xóa khỏi menu.
- Session bị thu hồi nhưng vẫn còn trong cache ngắn hạn.
- Backup tồn tại nhưng không khôi phục hoặc đối chiếu được.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Hệ thống MUST có sổ đăng ký cho mọi chức năng công khai/nội bộ, nêu owner, role, dữ liệu chuẩn, audit, retention, SLA, test, rollback và trạng thái.
- **FR-002**: Hệ thống MUST từ chối phát hành route không có phân quyền, owner hoặc kiểm thử phù hợp và MUST vô hiệu hóa route cũ đã thay thế.
- **FR-003**: Hệ thống MUST dùng một luồng hỏi đáp thống nhất cho bốn loại: điều/văn bản cụ thể, thủ tục/biểu mẫu, pháp luật tổng quát và lịch sử.
- **FR-004**: Hệ thống MUST giữ từng ý và evidence riêng; độ bao phủ thiếu chỉ cảnh báo, không xóa claim khác đã xác minh.
- **FR-005**: Hệ thống MUST trả mọi câu trả lời bằng một cấu trúc thống nhất, ẩn phần không liên quan nhưng giữ thứ tự phần còn lại.
- **FR-006**: Hệ thống MUST chỉ gắn biểu mẫu sau khi xác nhận duy nhất thủ tục và sau các cổng phát hành, nguồn, checksum, role, điều kiện và hiệu lực.
- **FR-007**: Hệ thống MUST cung cấp giao diện chat có vùng tin nhắn cuộn độc lập, composer cố định, nút chat mới luôn thấy, phân trang lịch sử và nhãn đúng role.
- **FR-008**: Hệ thống MUST cung cấp luồng gửi hỗ trợ có bước xác nhận lĩnh vực, nội dung, tệp, trạng thái, vị trí và thời gian chờ dự kiến.
- **FR-009**: Hệ thống MUST tự động phân công ticket theo lĩnh vực, trạng thái online, tải và thời gian chờ; không giao trùng và không vượt 3 phiên active/cán bộ theo mặc định.
- **FR-010**: Người đang chờ MUST không duy trì kết nối realtime; chỉ phiên đã nhận mới mở realtime và ticket MUST trở lại hàng chờ khi heartbeat hết hạn.
- **FR-011**: Hệ thống MUST quản lý đầy đủ trạng thái hiệu lực, các mốc sắp hết hạn 90/30/7/1 ngày, quan hệ thay thế/sửa đổi/đình chỉ/khôi phục và trạng thái vector.
- **FR-012**: Văn bản hết hiệu lực MUST được giữ cho lịch sử/audit, bị loại khỏi câu trả lời hiện hành và không bị hard-delete.
- **FR-013**: Khi văn bản thay thế có nội dung khác, hệ thống MUST giữ hai identity, xác nhận quan hệ, rà tác động từng phần và không sao chép quy định cũ sang bản mới.
- **FR-014**: Hệ thống MUST tái index theo phần thay đổi khi đủ điều kiện và chỉ full re-index khi thay đổi mô hình/splitter/schema hoặc index hỏng diện rộng.
- **FR-015**: Luồng thủ tục/biểu mẫu/FAQ MUST hiển thị rõ bước hiện tại, bước kế tiếp và lý do bị chặn; MUST cho tìm thủ tục theo tên/mã/lĩnh vực và nhận URL/PDF/DOCX/e-form.
- **FR-016**: FAQ MUST có trạng thái công khai rõ ràng, gắn thủ tục đã xác nhận và lấy biểu mẫu từ release hiện hành thay vì nhập mã mẫu thủ công.
- **FR-017**: Dashboard MUST ưu tiên việc cần làm hôm nay, giải thích tác động và hành động chính; chi tiết kỹ thuật mặc định thu gọn.
- **FR-018**: Trung tâm hoạt động MUST lọc theo role, module, kết quả, người dùng và thời gian; hỗ trợ xuất báo cáo và bảo vệ nội dung nhạy cảm bằng reason + audit.
- **FR-019**: Hệ thống MUST enforce role/domain/ownership ở server, thu hồi session khi đổi mật khẩu/khóa tài khoản và không tin identifier do frontend tự khai báo.
- **FR-020**: Upload MUST được kiểm tra loại tệp, kích thước, đường dẫn, checksum và nội dung độc hại trước khi phục vụ.
- **FR-021**: Audit MUST bất biến hoặc phát hiện chỉnh sửa, ghi đủ sự kiện nguy hiểm nhưng không ghi plaintext credential, token hay nội dung riêng tư vào telemetry.
- **FR-022**: FAQ, support và dữ liệu nghiệp vụ MUST có đúng một nguồn chuẩn; projection/thông báo không được quyết định trạng thái pháp lý.
- **FR-023**: Mọi migration MUST additive, có backup, rehearsal, reconciliation, rollback pointer và giữ đường đọc cũ read-only trong ít nhất một release.
- **FR-024**: Hệ thống MUST có một writer index staging, immutable active snapshot, manifest/fingerprint và chuyển active pointer nguyên tử sau release gate.
- **FR-025**: Hệ thống MUST có giám sát latency, lỗi, provider, pool, vector/effectivity freshness, support SLA, job, tài nguyên, backup và citation/form/source failure mà không thu thập nội dung người dùng.
- **FR-026**: Production MUST chạy nhiều replica có thể scale độc lập và không phụ thuộc ngrok.
- **FR-027**: Rollout MUST qua staging và canary theo role; rollback MUST không được vô hiệu hóa các cổng pháp lý/bảo mật.
- **FR-028**: Hệ thống MUST chỉ gắn nhãn production-ready sau khi toàn bộ kiểm thử pháp lý, UAT, load, security, backup/restore và build gate đạt.
- **FR-029**: Retrieval evaluation MUST tính Recall@10/MRR@10 trên câu cần trả lời, đo expected refusal riêng, giữ câu bị chặn nhầm trong mẫu số và báo cáo riêng từng lĩnh vực/dataset.
- **FR-030**: Golden-1.000 và Hard-negative MUST dùng `legal_as_of` từng case, manifest/checksum bất biến và source-gap có audit; benchmark không được tự nhập nguồn, đổi collection hoặc đổi active pointer.

### Legal Grounding and Safety *(mandatory for legal-answer or legal-data features)*

- **Applicable sources**: Chỉ nguồn chính thức hoặc đã được xác minh, đúng thẩm quyền, phạm vi, lĩnh vực và ngày áp dụng; ưu tiên luật cấp trên khi xung đột với văn bản địa phương.
- **Citation behavior**: Giữ số hiệu, cơ quan, điều/khoản/điểm, ngày áp dụng, URL và bằng chứng trích dẫn; biểu mẫu/citation là dữ liệu cấu trúc do backend quyết định.
- **Insufficient evidence**: Trả phần đã xác minh, cảnh báo phần thiếu, hỏi làm rõ hoặc chuyển cán bộ; không dùng kiến thức tự do để lấp nguồn và không chỉ trả “Không có thông tin”.
- **Expired or superseded material**: Loại khỏi câu trả lời hiện hành; chỉ dùng ở route lịch sử với ngày áp dụng và nhãn rõ ràng; giữ metadata, nội dung và audit.

### Roles, Ownership, and Privacy *(mandatory when accounts or private data are involved)*

| Role | May access | Must not access | Server-side enforcement |
|------|------------|-----------------|-------------------------|
| Người dân | Hội thoại, yêu cầu hỗ trợ, hồ sơ và biểu mẫu công khai của chính mình | Dữ liệu riêng của người khác, candidate/release nội bộ, log nhạy cảm | Session, ownership, released-state và identifier validation |
| Cán bộ | Tra cứu nghiệp vụ, ticket đúng lĩnh vực được giao, đề xuất và tiến độ của mình | Ticket ngoài phạm vi, duyệt/phát hành, dữ liệu riêng không được giao | Role/domain scope, allocator assignment và ownership |
| Admin | Quản trị dữ liệu và vận hành theo chức năng được cấp | Đọc tùy ý nội dung hỗ trợ riêng tư hoặc bỏ qua release/audit gate | Admin permission, reason-required access, two-step workflow và audit |
| Hệ thống | Job và projection đúng capability | Thay đổi legal truth ngoài workflow được duyệt | Service identity, capability scope, idempotency và audit |

- **Sensitive data**: Nội dung hội thoại, ticket, hồ sơ, tệp, credential, token, thông tin định danh, audit access reason.
- **Negative scenarios**: Direct API access, ID tampering, cross-account read/write/delete, role/domain spoofing, revoked session reuse và draft/pending data leakage.
- **Audit behavior**: Ghi actor, role, action, target, result, timestamp, release/fingerprint và reason cần thiết; không ghi raw token hay nội dung nhạy cảm vào telemetry chung.

### Key Entities

- **Capability Record**: Sổ đăng ký chức năng, owner, quyền, dữ liệu, SLA, test và rollback.
- **Legal Answer Presentation**: Cấu trúc thống nhất cho phần trả lời, hành động, hồ sơ, thủ tục, biểu mẫu, căn cứ và cảnh báo.
- **Support Ticket / Assignment / Presence**: Yêu cầu hỗ trợ, hàng chờ, phân công và heartbeat cán bộ.
- **Legal Change / Relationship / Provision Effectivity**: Sự kiện và quan hệ làm thay đổi hiệu lực toàn bộ hoặc từng phần.
- **Legal Impact Case**: Danh sách FAQ, thủ tục, biểu mẫu, Golden, cache, citation và index cần rà sau thay đổi pháp lý.
- **Index Manifest / Index Job**: Snapshot vector, fingerprint, trạng thái serving và công việc index.
- **Procedure / Form / FAQ Revision and Release**: Dữ liệu thủ tục, tệp/URL/e-form, binding, revision và bản phát hành.
- **Activity Event / Audit Entry**: Sự kiện vận hành dễ đọc và bằng chứng audit chống chỉnh sửa.
- **Release Evidence**: Kết quả test, benchmark, UAT, security, backup/restore và quyết định Go/No-Go.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Đúng văn bản hoặc thủ tục tối thiểu 99%; tập biểu mẫu chính xác 100%; claim có citation hỗ trợ 100%; bao phủ fact quan trọng tối thiểu 95%.
- **SC-002**: Không có kết luận hiện hành dùng nguồn hết hiệu lực và không có dữ liệu draft/pending/sai role bị công khai.
- **SC-003**: Tỷ lệ fallback bất ngờ không quá 3%, không tính khoảng trống nguồn thực đã được xác định.
- **SC-004**: 95% lượt retrieval hoàn tất không quá 3 giây và 95% câu trả lời cuối không quá 25 giây; lỗi provider/timeout dưới 0,5%.
- **SC-005**: Hệ thống phục vụ được 1.000 người đăng nhập/chờ, 100 yêu cầu hỏi đáp đồng thời và 30 cán bộ mà không mất ticket, giao trùng, sai lĩnh vực hoặc vượt capacity.
- **SC-006**: 95% thao tác tạo/gửi support hoàn tất trong 1 giây và 95% truy vấn trạng thái hàng chờ trong 500 ms.
- **SC-007**: 100% chức năng có owner, role, audit, retention, test và rollback; không còn route ma hoặc route vượt quyền.
- **SC-008**: 100 regression, 294 Golden, Golden V3 1.000 ca, 1.000 full-answer live và UAT 20 citizen + 20 officer đều đạt gate tương ứng.
- **SC-009**: Restore rehearsal đạt RPO không quá 15 phút cho dữ liệu chính, RTO không quá 2 giờ cho dữ liệu nghiệp vụ và không quá 4 giờ cho vector.
- **SC-010**: Không có P0/P1, secret mặc định, lỗi build/type/lint/test hoặc console/HTTP 500 trong hành trình UAT trước production-ready.
- **SC-011**: Golden-1.000 và Hard-negative đạt riêng Recall@10 ≥95%, Recall@10 từng lĩnh vực ≥95%, MRR@10 ≥0,90 và correct-refusal ≥99%; safety count bằng 0, warm retrieval+reranking P95 ≤15 giây và active pointer không đổi trong benchmark.

## Assumptions

- Phạm vi pháp luật ban đầu là năm lĩnh vực cấp xã Hải Phòng đã thống nhất.
- Hệ thống tài khoản hiện tại được giữ nhưng sẽ được harden, không thay bằng một nhà cung cấp định danh mới trong lát đầu.
- Một admin có thể thực hiện các bước quản trị khác nhau nhưng các bước duyệt nguồn và xác nhận pháp lý phải tách biệt và được audit.
- Dữ liệu sống, migration sống, re-index thật và production activation chỉ thực hiện sau backup/rehearsal và phê duyệt lát riêng theo cổng Go/No-Go.
- BGE chỉ bật sau benchmark activation gate; không fine-tune LLM bằng biểu mẫu.
- Cloud LLM là đường production mặc định; local model là lựa chọn cấu hình thêm, dùng cùng evidence và validator.
- Nội dung support giữ 180 ngày sau khi đóng; audit metadata giữ 730 ngày, trừ khi chính sách pháp lý được phê duyệt thay đổi.
- Ngrok chỉ dùng demo/UAT, không dùng cho production.
