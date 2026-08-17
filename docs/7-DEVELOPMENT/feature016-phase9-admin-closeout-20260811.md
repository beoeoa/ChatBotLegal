# Chốt Phase 9 và trung tâm vận hành Admin — 11/08/2026

## Phạm vi đã được phép

Người dùng đã phê duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca. Lát cắt
này không thay đổi schema, không hard-delete, không tái lập chỉ mục toàn kho và
không kích hoạt dịch vụ trả phí. Bản sao lưu trước thay đổi nằm tại
`backups/golden294-live/20260811T024600Z/backup-seal.json`.

## Dữ liệu đã chốt

Snapshot đọc-only cuối cùng:
`outputs/019fe6cd-c481-70c0-8c58-3f69816592fc/golden-294-live/final-datastore-seal-snapshot.json`.

- PostgreSQL: 25.441 văn bản, 233.000 điều, 545.625 chunk và 25.441 bản ghi
  phạm vi tìm kiếm.
- SurrealDB: 66 ứng viên crawl, 15 job nhập, 7.673 quan sát hiệu lực và 4.172
  sự kiện hiệu lực tại thời điểm chụp.
- Dịch vụ retrieval: healthy, 545.625 chunk PostgreSQL và 161.077 vector trong
  collection core đang phục vụ.
- `116/2026/TT-BCA`: active, 30 điều, 246 chunk; đủ 246/246 vector ở cả core
  và source-wide.
- `55/2021/TT-BCA` và `66/2023/TT-BCA`: trạng thái tại ngày chốt là expired;
  dữ liệu và vector vẫn được giữ cho tra cứu lịch sử, current retrieval loại bỏ.
- `31/2024/QH15`: active, 676 chunk; đủ vector ở cả hai collection.
- `73/2025/QH15`: active, 65 chunk; đủ 65/65 vector ở source-wide, không đưa
  vào core do quyết định phạm vi cấp xã đã được duyệt.
- `88/2025/QH15`: active, 60 chunk; Điều 87/99/100/101 lần lượt có
  2/5/4/6 chunk; đủ vector ở cả hai collection.
- `62/2020/QH14`: active, 108 chunk; riêng Điều 1 có đủ 101 chunk theo thứ tự
  0..100 và đủ vector ở cả hai collection.
- `43/2025/NQ-HĐND`: giữ nguyên metadata nhập cũ để audit, nhưng retrieval có
  exact-document override đã duyệt sang lĩnh vực hộ tịch/chứng thực; đủ 5/5
  vector ở cả hai collection.

## Luồng xử lý câu hỏi đã chốt

1. Chuẩn hóa câu hỏi, tách tối đa sáu vấn đề và giữ thứ tự vấn đề.
2. Nếu người dùng nêu số hiệu + Điều, chạy exact PostgreSQL trước vector.
3. Áp dụng cổng lĩnh vực, phạm vi cấp xã, hiệu lực theo ngày và thứ bậc pháp lý.
4. Với truy vấn không exact, hợp nhất vector + BM25 bằng RRF; metadata và quan
   hệ sửa đổi/thay thế chỉ rerank các ứng viên đã qua cổng an toàn.
5. Mỗi vấn đề có top-10 độc lập. Bộ chấm không còn cắt mười kết quả đầu của
   toàn câu và làm mất nguồn chính xác thuộc vấn đề thứ hai trở đi.
6. Exact Article nạp toàn bộ chunk của Điều, kiểm tra thiếu/trùng/rỗng, sắp theo
   `chunk_index`; Điều cực dài chỉ chọn cửa sổ phục vụ sau khi đã xác minh đủ
   101/101 chunk. Exact Article bỏ qua learned reranker để không phá thứ tự.
7. Hydrate parent context, kiểm tra độ đầy đủ claim, citation, hiệu lực và
   nguồn; thiếu căn cứ phải trả fallback có nhãn, không dùng kiến thức chung để
   bù nội dung pháp luật.
8. BGE `BAAI/bge-reranker-v2-m3` vẫn là adapter tùy chọn sau BM25/RRF. Không
   kích hoạt trên runtime GTX 1660 hiện tại vì p95 thực đo 25,38 giây vượt cổng
   3 giây; cấu hình production hiện dùng deterministic rerank/hard gates.

## Trung tâm vận hành Admin

- Landing của Admin chuyển sang `/admin-dashboard`; Admin không còn màn hình
  hỏi đáp riêng.
- Dashboard tổng hợp health, văn bản/vector, hàng duyệt/import, crawler, tài
  khoản, model/provider, hỗ trợ, hồ sơ pháp lý và audit gần nhất.
- API dashboard và API credential là Admin-only; response chỉ có count/trạng
  thái, không trả API key, mật khẩu hoặc chi tiết audit nhạy cảm.
- Citizen/officer giữ nguyên hỏi đáp và không thấy chức năng quản trị model.

## Thiết lập model cho người không chuyên

- Checkbox Ollama đã được gỡ khỏi từng câu hỏi và chuyển vào trang
  `/settings/api-keys`.
- Wizard có bốn bước: chọn local/cloud, lưu kết nối, test/lấy model, kích hoạt
  mặc định.
- Local có preset cùng máy (`localhost:11434`) và ứng dụng Docker
  (`host.docker.internal:11434`).
- Kích hoạt chỉ mở khi cùng kết nối có một model trả lời và một model embedding
  hợp lệ. Model OpenRouter cũ bị gắn nhầm loại embedding bị fail-closed, không
  còn được coi là mặc định hợp lệ và được hiển thị cảnh báo để Admin dọn thủ
  công; hệ thống không tự xóa lịch sử cấu hình.
- Đổi embedding vẫn hiện cảnh báo tái lập chỉ mục; wizard không âm thầm đổi
  embedding của kho đang chạy.
- Các endpoint thêm/xóa/test/discover/sync/auto-assign model đều Admin-only.

## Kiểm định và quyết định stable

- Backend Admin/model/credential: 30 test đạt.
- Retrieval/evaluator/exact/domain: 70 test đạt.
- Frontend helper model setup: 2 test đạt; lint không lỗi; TypeScript đạt.
- UAT trực tiếp bằng tài khoản Admin xác nhận dashboard, menu Admin, wizard
  local/cloud và trạng thái fail-closed của embedding.
- Regression v2: recall@10 80,19%, 0 chọn văn bản hết hiệu lực, 0 sai lĩnh vực,
  nhưng bị loại vì evaluator cắt top-10 sai theo toàn câu và p95 19,424 giây.
- Regression v3 dùng top-10 theo từng vấn đề đang là bằng chứng quyết định cuối.
  Chỉ đánh dấu T069/T070/T077 và stable khi file
  `regression-1000-multiissue-v3.json` tồn tại và mọi gate bắt buộc đạt.

Internet deployment chưa được thực hiện vì chưa có máy đích/domain/quyền truy
cập được chỉ định. Runtime local hiện tại chỉ là ứng viên stable sau khi gate v3
đạt; không được gọi là bản production Internet.
