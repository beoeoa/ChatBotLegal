# Research: Feature 018 Production Release Readiness

## Decision 1 — Capability Registry là cổng bao phủ chức năng

**Decision**: Dùng một manifest versioned cho mọi frontend route, API router và job; CI kiểm tra owner, role, source of truth, sensitivity, audit, retention, SLA, test, flag và rollback.

**Rationale**: Menu không chứng minh route đã được bảo vệ hoặc không còn consumer. Registry tạo inventory kiểm định được và phát hiện route ma.

**Alternatives considered**: Duy trì danh sách tài liệu thủ công; quét route mà không có owner/policy. Cả hai không đủ làm release gate.

## Decision 2 — PostgreSQL là canonical cho support và FAQ

**Decision**: Chuyển write-side support/FAQ sang PostgreSQL bằng additive schema, import idempotent, shadow comparison và pointer switch; SurrealDB chỉ nhận notification projection.

**Rationale**: JSON file không phù hợp multi-replica, transaction allocator hoặc truy vấn/audit chịu tải. Hai nguồn write-side tạo race.

**Alternatives considered**: Giữ JSON trên shared volume; dual-write JSON/PostgreSQL; dùng SurrealDB làm nguồn thứ hai. Đều làm consistency/rollback khó chứng minh.

## Decision 3 — Waiting support dùng polling, assigned support mới realtime

**Decision**: Người chờ dùng polling có backoff; mỗi officer có một queue stream và chỉ ticket đã phân công mới mở realtime. Allocator dùng transaction row lock/skip-locked, capacity mặc định 3.

**Rationale**: 1.000 WebSocket chờ là lãng phí, trong khi trần hội thoại realtime thực là 90.

**Alternatives considered**: Một socket mỗi ticket ngay từ lúc tạo; broadcast mọi queue event. Hai cách tăng connection/fan-out và lộ dữ liệu.

## Decision 4 — Một projection câu trả lời duy nhất

**Decision**: Backend chiếu mọi output sang `LegalAnswerPresentationV1`; frontend chỉ render cấu trúc này, với adapter cho payload cũ.

**Rationale**: Nhiều format gây UX không ổn định và dễ gắn nguồn/form từ prose. Projection bảo toàn contract cũ nhưng tạo renderer duy nhất.

**Alternatives considered**: Chuẩn hóa prompt LLM mà không có schema; duy trì nhiều component. Không kiểm soát được optional sections và dữ liệu backend-owned.

## Decision 5 — Coverage là cảnh báo sau claim validation

**Decision**: Validator giữ claim được hỗ trợ; completeness chỉ đánh dấu phần thiếu. Chỉ claim unsupported bị loại; output hoàn toàn không có evidence vẫn fail-closed.

**Rationale**: Chặn cả câu trả lời vì thiếu một facet làm giảm tính hữu dụng và không tăng độ đúng của các claim đã xác minh.

**Alternatives considered**: Tắt mọi fallback/gate; hoặc giữ full-answer reject. Một bên bịa, bên kia quá im lặng.

## Decision 6 — Vòng đời theo event + provision effectivity

**Decision**: Lưu sự kiện pháp lý và quan hệ tài liệu, đồng thời cho phép effectivity theo Điều/khoản. Máy tạo candidate/impact, người có thẩm quyền xác nhận.

**Rationale**: Một status ở document không biểu diễn hết hiệu lực một phần, đình chỉ/khôi phục hoặc nhiều lần sửa đổi.

**Alternatives considered**: Chỉ cập nhật `is_active`; xóa văn bản cũ; tự động chấp nhận diff. Đều mất lịch sử hoặc tạo legal truth không kiểm duyệt.

## Decision 7 — Văn bản thay thế khác nội dung không kế thừa rule cũ

**Decision**: Giữ identity/version/vector riêng, quan hệ `replaces`, clause mapping reviewable và dependency `needs_review`. Nếu bản mới chưa index, tạo source gap thay vì bật lại bản cũ.

**Rationale**: Quan hệ thay thế không có nghĩa nội dung tương đương.

**Alternatives considered**: Trỏ alias bản cũ sang bản mới; clone chunk; full re-index toàn kho. Hai cách đầu sai pháp lý, cách cuối tốn kém không cần thiết.

## Decision 8 — Vector active snapshot bất biến, một staging writer

**Decision**: Query chỉ đọc active snapshot; một writer tạo staging; manifest kiểm tra chunk ID, embedding fingerprint, pipeline, validity snapshot; switch pointer nguyên tử.

**Rationale**: Giảm write-lock/race và cho rollback xác định mà không xóa lịch sử.

**Alternatives considered**: Background job ghi trực tiếp active collection; nhiều writer; in-place cleanup. Không có atomic cutover.

## Decision 9 — Reuse Feature 017 cho form release

**Decision**: Không tạo form truth mới. FAQ chỉ giữ `procedure_id`; form set luôn lấy từ Feature 017 active release sau identity/checksum/role/effectivity gates.

**Rationale**: Tránh năm tầng fallback và manual form IDs; giữ kết quả 1.000/1.000 router đã đạt.

**Alternatives considered**: FAQ lưu form IDs; LLM chọn mẫu; static in-memory catalog. Dễ lệch release và nguồn.

## Decision 10 — Dashboard theo hành động, chi tiết kỹ thuật thu gọn

**Decision**: Card nghiệp vụ gồm tình trạng, ảnh hưởng, hành động chính và deep-link danh sách lọc; metric kỹ thuật nằm lớp thứ ba.

**Rationale**: No-tech admin cần quyết định việc cần làm, không phải diễn giải số chunk/vector.

**Alternatives considered**: Chỉ đổi label; giữ grid metric hiện tại. Không giải quyết information hierarchy.

## Decision 11 — Scale bằng replica, không bỏ retrieval service trong lát đầu

**Decision**: Tách frontend/API production, giữ retrieval boundary hiện có và benchmark trước khi cân nhắc in-process.

**Rationale**: Worktree lớn và retrieval cần scale/tài nguyên riêng; bỏ IPC cùng lúc tăng blast radius.

**Alternatives considered**: Monolith single container; rewrite in-process ngay. Không đáp ứng scale hoặc khó rollback.

## Decision 12 — Telemetry không chứa nội dung

**Decision**: Chỉ ghi timing, status, fingerprints, counts, reason codes và opaque IDs; nội dung chỉ ở kho nghiệp vụ có ACL/retention.

**Rationale**: Vẫn quan sát được SLA mà không nhân bản dữ liệu cá nhân vào logging stack.

**Alternatives considered**: Log full prompt/answer để debug. Rủi ro privacy và retention cao.

## Decision 13 — Chunk release V2 là additive và manifest là active truth duy nhất

**Decision**: Lưu chunk tái tạo trong bảng release/revision mới, giữ nguyên `legal_article_chunks` V1 và chọn store bằng manifest V3. Mọi document trong inventory có trạng thái `retrievable` hoặc `quarantined`; pointer file vẫn là nguồn duy nhất quyết định release đang phục vụ.

**Rationale**: Ghi thêm vào bảng chunk cũ tạo xung đột index/hydration, còn rewrite phá rollback. Bảng versioned cho phép build, audit và rollback độc lập mà không tạo active pointer thứ hai.

**Alternatives considered**: Chỉ re-embed V1; ghi đè corpus; ghi chunk V2 vào bảng cũ. Các phương án này không giải quyết chunking hỗn hợp hoặc không bảo toàn lịch sử.

## Decision 14 — Provenance tách model, recipe và vector bytes

**Decision**: Manifest V3 bắt buộc các fingerprint riêng cho model artifact, tokenizer, embedding recipe, passage recipe, splitter, source snapshot, dependency lock và persisted vector content. Builder không được gắn model hiện tại cho vector được sao chép; clean candidate phải re-embed 100% chunk eligible.

**Rationale**: Một trường `embedding_fingerprint` không chứng minh vector được sinh bởi model/passage nào và đã dẫn đến attestation không tái lập được.

**Alternatives considered**: Giữ trường fingerprint cũ; chấp nhận provenance theo tên collection; copy baseline rồi đánh dấu model mới. Không phương án nào đủ cho release gate hoặc independent replay.

## Decision 15 — Quality V2 mặc định fail-closed

**Decision**: Empty, unassessed, missing required metadata/domain/effectivity và duplicate không canonical không được vào vector collection. Tài liệu chưa đủ bằng chứng vẫn được giữ trong inventory với reason code và quarantine; cross-document legal duplicates không tự xóa.

**Rationale**: Lọc sau ANN làm chunk xấu chiếm candidate slot, còn `COALESCE(eligible, TRUE)` biến thiếu assessment thành quyền phục vụ.

**Alternatives considered**: Tăng Top-K để bù; tiếp tục lọc sau hydration; xóa mọi nội dung trùng. Các cách này che lỗi hoặc có thể mất căn cứ pháp lý khác nguồn.
