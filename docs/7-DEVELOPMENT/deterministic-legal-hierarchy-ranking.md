# Xếp hạng thứ bậc pháp lý xác định

## Mục tiêu

Thay cơ chế ưu tiên địa phương bằng phép nhân hoặc cộng điểm bằng một lớp xếp
hạng pháp lý xác định. Điểm retrieval tiếp tục chỉ biểu diễn độ liên quan;
authority không làm thay đổi `score`.

Policy version: `vn-lvbqppl-64-87-2025-v1`.

## Cơ sở nguồn chính thức

- Luật Ban hành văn bản quy phạm pháp luật số 64/2025/QH15, ban hành ngày
  19/02/2025, có hiệu lực từ 01/04/2025:
  <https://vanban.chinhphu.vn/?classid=1&docid=213327&pageid=27160>
- Luật số 87/2025/QH15 sửa đổi Luật số 64/2025/QH15, có hiệu lực từ
  01/07/2025; khoản 1 Điều 1 sửa Điều 4 về hệ thống VBQPPL:
  <https://vbpl.moj.gov.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=178717&Keyword=>
- Văn bản hợp nhất số 54/VBHN-VPQH ngày 11/08/2025 dùng làm nguồn tra cứu
  hợp nhất, không được coi là một thẩm quyền ban hành mới:
  <https://vbpl.vn/TW/Pages/vbpq-thuoctinh-hopnhat.aspx?ItemID=180419&View=0>

Khoản 3 và khoản 4 Điều 58 của Luật số 64/2025/QH15 là hai ràng buộc áp dụng
trực tiếp cho policy: quy định có hiệu lực pháp lý cao hơn được ưu tiên; nếu
cùng cơ quan ban hành và quy định khác nhau về cùng vấn đề thì áp dụng văn bản
ban hành sau. Retrieval chỉ sắp xếp bằng metadata theo các ràng buộc này; nó
không tự kết luận hai điều khoản thực sự mâu thuẫn.

## Bảng authority nội bộ

| Level | Rank | Nhãn công khai |
|---|---:|---|
| `constitution` | 140 | Hiến pháp |
| `national_assembly` | 130 | Luật, bộ luật hoặc nghị quyết của Quốc hội |
| `standing_committee` | 120 | Pháp lệnh/nghị quyết của UBTVQH |
| `president` | 110 | Lệnh/quyết định của Chủ tịch nước |
| `government` | 100 | Nghị định/nghị quyết của Chính phủ |
| `prime_minister` | 90 | Quyết định của Thủ tướng Chính phủ |
| `judicial_council` | 85 | Nghị quyết của Hội đồng Thẩm phán TANDTC |
| `ministerial` | 80 | Thông tư của cơ quan trung ương có thẩm quyền |
| `provincial_people_council` | 70 | Nghị quyết HĐND cấp tỉnh |
| `provincial_people_committee` | 60 | Quyết định UBND cấp tỉnh |
| `legacy_district_people_council` | 55 | Nghị quyết cấp huyện còn hiệu lực chuyển tiếp |
| `legacy_district_people_committee` | 50 | Quyết định cấp huyện còn hiệu lực chuyển tiếp |
| `commune_people_council` | 45 | Nghị quyết HĐND cấp xã |
| `commune_people_committee` | 40 | Quyết định UBND cấp xã |
| `reference_document` | -10 | Văn bản hợp nhất/tài liệu tra cứu |
| `authority_unverified` | -20 | Chưa xác minh được cấp hiệu lực pháp lý |

Các con số chỉ là khóa sắp xếp versioned, không phải “điểm hiệu lực pháp lý”
được công bố cho người dùng. Nghị quyết và quyết định mơ hồ phải được phân giải
bằng loại văn bản kết hợp cơ quan ban hành hoặc số ký hiệu. Title và nội dung
chunk không được dùng để suy đoán authority.

## Trình tự xử lý

1. Lọc `as_of`, trạng thái văn bản/điều và quan hệ căn cứ đã hết hiệu lực.
2. Rerank độ liên quan nhưng giữ nguyên ý nghĩa của `score`.
3. Phân loại authority từ metadata xác định.
4. Xếp verified trước unverified, rồi theo authority rank.
5. Trong cùng rank, tạo ràng buộc văn bản ban hành sau đứng trước văn bản cũ
   chỉ khi normalized issuer giống nhau và cả hai có `issued_date` hợp lệ.
6. Giữa các candidate còn lại dùng scope fit, relevance và stable identity.
7. Áp lại policy sau bước giới hạn diversity để response cuối không bị đảo
   thứ bậc bởi việc tách primary/complementary evidence.

Ràng buộc cùng issuer được giải bằng thứ tự topo xác định. Cách này tránh comparator
không bắc cầu và bảo đảm cùng một output với mọi hoán vị candidate đầu vào.

## Trace và ranh giới công khai

Admin trace có `trace.hierarchy` gồm version, mô tả thứ tự, số candidate verified,
unverified và cần review. Mỗi chunk trace có level, rank, reason code, vị trí và
rule quyết định.

Public Ask/citation chỉ được nhận `authority_level` và `authority_label`. Các
trường `authority_rank`, reason code, hierarchy rule/position và `score` bị loại
ở cả backend formatter và SSE frontend allow-list.

## Ngoại lệ và giới hạn pháp lý

- Policy là thứ tự ưu tiên evidence, không phải bộ máy giải quyết xung đột pháp
  luật theo ngữ nghĩa và không thay luật sư/cơ quan có thẩm quyền.
- Văn bản hợp nhất là nguồn đọc thuận tiện, không phải authority mới.
- Công văn, hướng dẫn hành chính và record thiếu/mâu thuẫn metadata là
  `authority_unverified`; relevance cao không thể đẩy chúng lên trên VBQPPL đã
  xác minh.
- Marker chế độ đặc thù/ngoại lệ tạo warning
  `legal_precedence_requires_review`. Hệ thống không để LLM tự quyết ngoại lệ.
- Hiệu lực chuyển tiếp của văn bản cấp huyện phụ thuộc metadata hiệu lực và
  validity sync. Record thiếu dữ liệu chuyển tiếp phải được review, không được
  suy đoán từ nội dung.
- Điều ước quốc tế và quy tắc chuyên ngành đặc thù chưa được tự động phân xử ở
  policy version này.

## Hiệu năng và bằng chứng kiểm thử

Benchmark thuần bộ nhớ dùng 150 candidate, metadata lặp theo văn bản như dữ liệu
chunk thực tế, cache phân loại LRU giới hạn 4.096 khóa, không gọi mạng hoặc DB.
Lần chạy chuẩn 5.000 vòng ngày 08/08/2026 đạt p95 `4.4802 ms`, không có
network/DB call và output ổn định qua hoán vị; gate p95 dưới 5 ms đạt.

Bộ hồi quy tối thiểu gồm classification matrix, local-vs-central, same issuer,
different issuer, unknown metadata, score immutability, public privacy và toàn bộ
validity sync. Retrieval integration đầy đủ cần runtime có optional
`chromadb`/`torch`; thiếu dependency này là release gate có điều kiện, không phải
lý do cài dependency nặng ngoài image chuẩn.

Tại checkpoint ngày 08/08/2026, 117 backend tests của policy, legacy search,
validity sync, citation và privacy đạt; 7 frontend SSE tests, ESLint mục tiêu và
TypeScript typecheck đạt. Hai module `test_hybrid_retrieval.py` và
`test_legal_search_batch.py` chưa collect trong runtime nhẹ vì thiếu
`chromadb`. Chúng bắt buộc phải chạy trong retrieval image chuẩn trước rollout.

## Rollout và rollback

1. Chạy shadow top-k cũ/mới trên retrieval evaluation dataset.
2. Chỉ mở production khi có 0 ca local-over-superior, critical-authority
   retention không giảm và direct-source recall không vượt ngưỡng suy giảm đã
   duyệt.
3. Theo dõi tỷ lệ `authority_unverified` và
   `legal_precedence_requires_review`; tăng bất thường thì dừng rollout.
4. Rollback bằng cách hoàn nguyên hai integration call đến policy. Không có
   migration, thay corpus hay đổi active Chroma pointer.

## Xác nhận phạm vi dữ liệu

Thay đổi này không thêm/sửa schema, không viết lại legal corpus, không sửa
metadata nguồn, không đổi active retrieval pointer và không chạy background
service mới. Source URL và metadata gốc được giữ nguyên trong candidate copy.
