# Pilot Ask quality gate và rollout theo vai trò

## Mục đích và ranh giới an toàn

Feature này cung cấp năm công cụ vận hành độc lập, mặc định không gọi mạng:

1. Manifest pilot 30 ca tham chiếu bản ghi review chuyên gia hiện có.
2. Gói 30 ca được làm sạch để chuyên gia điền quyết định.
3. Runner lập hoặc thực thi có chủ đích ma trận Ask 240 lượt.
4. Runner bằng chứng cô lập tài khoản gồm 8 phép thử riêng.
5. Quality gate và trạng thái rollout theo thứ tự `admin -> officer -> citizen`.

Các công cụ không tự phê duyệt review, không sửa corpus pháp luật và không tự bật
tính năng trong API hoặc frontend. Backend có gate opt-in cho endpoint mới
`/api/search/ask/progress`; cả cờ progress và cờ rollout đều mặc định tắt.

## Manifest 30 ca

`data/pilot/ask_quality_manifest.json` phải có đúng tích Descartes:

- 5 domain;
- 2 vai trò được đánh giá: `citizen`, `officer`;
- 3 lớp kịch bản vận hành: `routine`, `complex`, `boundary`.

Mỗi tổ hợp có đúng một `review_id`, tổng cộng 30 ID duy nhất. Manifest không sao
chép câu hỏi, đáp án hoặc căn cứ pháp lý. Mỗi ID phải tồn tại và khớp domain/role
trong `notebook_data/legal-golden-expert-review.json`.

Ba nhãn lớp kịch bản chỉ phục vụ thiết kế tải và độ phủ. Chúng không phải là xác
nhận của chuyên gia về độ khó hoặc loại câu hỏi. Hiện trường `question_type` của
nguồn review chưa cung cấp bằng chứng để diễn giải các nhãn này theo nghĩa khác.

Tại thời điểm thiết lập, 334/334 review trong nguồn đang ở trạng thái `pending`.
Không record nào được công cụ đổi sang `approved`; vì vậy chạy live và quality
gate phải bị chặn cho đến khi chuyên gia thật hoàn tất tên, thời điểm review và
điểm số.

Tạo packet chuẩn bị bằng:

```powershell
python scripts/prepare_pilot_expert_review_packet.py --write
```

Packet chỉ chứa ID/domain/role/scenario/date, các source label được gắn
`candidate_pending_review`, tiêu chí lỗi nghiêm trọng và trường trống dành cho
chuyên gia. Nó không chứa câu hỏi, đáp án hoặc căn cứ đã được automation tuyên
bố là đúng. Output dùng create-only; tên file đã tồn tại thì lệnh dừng.

Packet là biểu mẫu hỗ trợ đọc, không phải artifact đầu vào trực tiếp của live
runner. Sau khi chuyên gia ký duyệt theo quy trình kiểm soát, người quản lý dữ
liệu mới chuyển quyết định và metadata nguồn đã xác minh vào đúng record của
`notebook_data/legal-golden-expert-review.json`. Không có lệnh tự động merge hoặc
đổi `pending` thành `approved`; T008 chỉ hoàn tất sau kiểm tra thủ công đủ 30 ID.

## Ma trận thực thi

Runner bao phủ từng ca trên hai endpoint:

- `/api/search/ask/simple`;
- `/api/search/ask/progress`.

Mỗi endpoint được chạy với concurrency `1`, `5`, `10`, `20`. Tổng số lượt bắt
buộc là `30 x 2 x 4 = 240`; thiếu, thừa hoặc trùng một lượt đều làm gate thất bại.
Luồng progress chỉ được tính hoàn tất khi nhận cả sự kiện `final` và `complete`.

Chế độ mặc định chỉ lập kế hoạch, không gọi API và không ghi báo cáo:

```powershell
python scripts/run_pilot_ask_matrix.py
```

Thực thi live là thao tác có chủ đích, chỉ được làm sau khi 30 record đã được
chuyên gia duyệt. Token chỉ được cấp qua môi trường và không được ghi vào file:

```powershell
$env:PILOT_CITIZEN_TOKEN = "<citizen bearer token>"
$env:PILOT_OFFICER_TOKEN = "<officer bearer token>"
python scripts/run_pilot_ask_matrix.py --execute
```

Không đặt token hoặc mật khẩu mặc định trong mã nguồn. Các runner pilot cũ cũng
phải dừng an toàn nếu thao tác live thiếu biến môi trường tương ứng.

## Bằng chứng cô lập tài khoản — tách khỏi 240 lượt

Role-isolation runner kiểm tra trực tiếp việc đổi ID giữa hai tài khoản thường
theo cả hai chiều cho bốn loại tài nguyên: conversation, profile, notebook và
legal dossier. Tổng cộng đúng 8 attempt. Mọi attempt phải trả 403 hoặc 404;
status khác, lỗi mạng, placeholder, tài khoản admin, trùng account/resource ID
hoặc thiếu token đều fail-closed.

Phép thử profile dùng `GET /api/users/{user_id}`. Endpoint này chỉ trả hồ sơ
cho chính tài khoản đó hoặc admin; tài khoản thường đọc ID của tài khoản khác
nhận 404. Không dùng `GET /api/users?user_id=...` vì route danh sách chỉ dành
cho admin và tham số đó không chứng minh được cô lập theo từng hồ sơ.

Trước 8 attempt, runner gọi read-only `/api/users/me` cho từng token và yêu cầu
ID/role trả về khớp input, đồng thời role phải là citizen hoặc officer. Tất cả
phép thử sau preflight đều dùng request đọc; không có thao tác sửa/xóa dữ liệu.

Sao chép `specs/004-gate-role-rollout/role-isolation-input.template.json` sang
artifact được kiểm soát và điền ID thật. Token chỉ được inject qua:

```text
PILOT_ISOLATION_ACCOUNT_A_TOKEN
PILOT_ISOLATION_ACCOUNT_B_TOKEN
```

Plan-only không gọi mạng:

```powershell
python scripts/run_pilot_role_isolation.py --input <controlled-input.json>
```

Live run chỉ thực hiện sau T008 và phải ghi ra tên file evidence mới:

```powershell
python scripts/run_pilot_role_isolation.py --execute `
  --input <controlled-input.json> `
  --output specs/004-gate-role-rollout/evidence/role-isolation-<run-id>.json
```

Report chỉ giữ attempt/account/resource IDs, HTTP status, timing, normalized
outcome code và input hash; không giữ request/response body, token hoặc exception
message. Trường `quality_matrix_attempts` luôn bằng 0 nên 8 attempt bảo mật không
được dùng để bù hoặc cộng vào 240 lượt Ask.

## Quality gate

Gate đọc manifest, nguồn review và báo cáo runner; nó không gọi Ask. Điều kiện
đạt đồng thời là:

- toàn bộ 30 review có trạng thái `approved`, có tên chuyên gia, thời điểm review
  và điểm hữu hạn từ 0 đến 10;
- điểm trung bình của từng nhóm domain-role (10 nhóm) `>= 9.0`;
- không có critical hallucination;
- tỷ lệ citation đúng `>= 99%` trên các ca có yêu cầu citation;
- tỷ lệ biểu mẫu đúng `>= 99%` trên các ca có yêu cầu biểu mẫu;
- error rate `< 1%`;
- repair rate `< 10%` và mọi lượt phải có tín hiệu quan sát repair;
- đủ đúng 240 lượt và dữ liệu áp dụng citation/form khớp nguồn review.

Chạy gate ngoại tuyến:

```powershell
python scripts/pilot_quality_gate.py
```

Lệnh ghi `reports/pilot-quality-gate.json` và trả mã khác 0 nếu bất kỳ điều kiện
nào không đạt. Báo cáo có schema version và SHA-256 của ba artifact đầu vào.
Rollout kiểm tra lại đầy đủ cấu trúc, ngưỡng, coverage, metric và fingerprint;
không tin riêng trường `pass: true`.

Runner chỉ lưu metadata ca, trạng thái, thời gian, số lượng, metric và SHA-256 của
đáp án. Báo cáo không chứa câu hỏi, câu trả lời, citation thô, biểu mẫu thô,
token, mật khẩu hoặc exception message. SHA-256 chỉ dùng để so sánh tính ổn định,
không thay thế review nội dung của chuyên gia. Các cờ hallucination/citation/form
tự động cũng không thay thế việc chuyên gia đọc câu trả lời.

## Rollout và rollback

### Phạm vi runtime

Rollout theo vai trò chỉ áp dụng cho `/api/search/ask/progress`. Hai endpoint Ask
hiện hữu `/api/search/ask` và `/api/search/ask/simple` tiếp tục hoạt động trong
suốt canary, nên stage 0 không làm mất dịch vụ citizen/officer và frontend vẫn có
đường fallback an toàn. Đây là rollout endpoint progress, không phải công tắc tắt
toàn bộ chức năng Ask.

Hai cờ runtime mặc định là `false`:

```text
LEGAL_ASK_PROGRESS_ENABLED=false
LEGAL_ASK_ROLE_ROLLOUT_ENFORCED=false
LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH=data/pilot/ask_role_rollout.json
```

Khi enforcement được bật, backend đọc state ở mỗi request progress để rollback có
hiệu lực ngay. Thiếu file, JSON/schema/stage sai hoặc danh sách role không đúng
prefix `admin -> officer -> citizen` trả 503 và không chạy Ask. Role chưa mở trả
403. Backend chỉ dùng role của phiên `user_session` cùng user ID đã xác thực;
`AskRequest.role`, `X-User-Role`, chế độ không mật khẩu và shared password không
thể tự nâng quyền vào canary.

Xem trạng thái là chế độ mặc định và không ghi file:

```powershell
python scripts/manage_ask_role_rollout.py
```

Mỗi lần advance bắt buộc quality report hợp lệ:

```powershell
python scripts/manage_ask_role_rollout.py --advance
```

Trình tự là:

1. Gate đạt mới chuyển stage 0 sang admin.
2. Admin chạy canary riêng và soak đủ ít nhất 24 giờ; soak đạt mới mở officer.
3. Officer soak đủ ít nhất 24 giờ; soak đạt mới mở citizen.
4. Citizen là stage cuối; tiếp tục giám sát theo runbook vận hành.

Manifest 30 ca không có vai trò admin. Do đó bằng chứng canary/soak admin phải
được thu riêng, không được tạo giả ca admin từ manifest citizen/officer. Soak
report cho stage đang mở phải có đúng role, `duration_hours >= 24`, không critical
hallucination, citation/form `>= 99%`, error `< 1%` và repair `< 10%`. Đồng hồ
trạng thái cũng phải cho thấy đủ 24 giờ; sửa riêng `duration_hours` không đủ.

Rollback có hiệu lực ngay trong trạng thái và không cần chờ soak:

```powershell
python scripts/manage_ask_role_rollout.py --rollback --to-stage 1
python scripts/manage_ask_role_rollout.py --rollback --to-stage 0
```

Rollback về stage 1 chỉ giữ admin trên endpoint progress; stage 0 chặn progress
cho cả ba vai trò nhưng không ảnh hưởng `/ask` và `/ask/simple`. Mỗi lần advance
hoặc rollback ghi history và dùng thay file nguyên tử. Có thể tắt hẳn endpoint
progress bằng `LEGAL_ASK_PROGRESS_ENABLED=false` nếu cần rollback route.

Không dùng `LEGAL_ASK_ROLE_ROLLOUT_ENFORCED=false` như rollback trong khi progress
vẫn bật: tắt enforcement là bỏ qua gate và có thể mở progress cho mọi role. Cách
rollback an toàn là đưa state về stage 0, hoặc tắt `LEGAL_ASK_PROGRESS_ENABLED`.
Không bật/advance cờ thật nếu chưa hoàn tất quality gate, canary admin và bằng
chứng soak tương ứng.

## Cấp phát artifact và kiểm tra phiên bản

`data/` và `notebook_data/` đang nằm trong `.gitignore`. Manifest, trạng thái và
nguồn review vì vậy không tự đi theo commit. Mỗi môi trường phải được cấp phát
artifact có kiểm soát, giữ `version/schema_version`, và đối chiếu SHA-256 trong
quality report trước khi advance. Không dùng báo cáo cũ với manifest, review hoặc
kết quả runner đã thay đổi.

## Kiểm thử ngoại tuyến

```powershell
python -m pytest -q tests/test_pilot_quality_manifest.py tests/test_pilot_quality_gate.py tests/test_pilot_ask_matrix_runner.py tests/test_pilot_expert_review_packet.py tests/test_pilot_role_isolation_runner.py tests/test_ask_role_rollout.py tests/test_ask_role_rollout_runtime.py tests/test_pilot_runner_credentials.py
```

Bộ này không gọi live, không advance/rollback trạng thái thật và không sửa corpus.
