# Truy xuất cha–con theo cấu trúc văn bản pháp luật

## Mục tiêu

Retrieval tiếp tục khớp chính xác ở đoạn con nhỏ nhất đáng tin cậy (Điểm/Khoản),
nhưng mô hình trả lời nhận thêm ngữ cảnh của đúng Điều hoặc Phụ lục cha. Cơ chế
này giảm tình trạng tách “điều kiện”, “hồ sơ”, “trình tự”, “thời hạn” và “lệ
phí” thành các mẩu rời rạc.

Thay đổi không thêm schema, không viết lại kho văn bản, không đổi active Chroma
pointer và không chạy dịch vụ nền mới.

## Mô hình dữ liệu được tái sử dụng

- `legal_articles` là lớp cha: `id`, `document_id`, `article_number`, `title`,
  `content`.
- `legal_article_chunks` là lớp con: `id`, `article_id`, `chunk_index`,
  `heading`, `content`.
- Với Phụ lục mới, `article_number` dùng nhãn rõ ràng như `PL-I`; nội dung vẫn
  được lưu trong cùng bảng cha hiện có.
- Khoản/Điểm mới được lưu trong đường dẫn `heading`, ví dụ
  `Điều 16. Thủ tục > Khoản 2 > Điểm a`. Không cần cột mới.

## Cắt khúc xác định

`api/legal_structural_chunking.py` chỉ công nhận cấu trúc ở đầu dòng:

1. `Điều N` tạo một parent loại `article`.
2. `PHỤ LỤC`, `PHỤ LỤC I`, `Phụ lục số 01` có nhãn rõ ràng tạo parent loại
   `appendix`.
3. Ranh giới parent dừng ở Điều/Phụ lục kế tiếp; Phụ lục không bị nhập vào Điều
   cuối cùng như trước.
4. Trong mỗi parent, `1.`, `2.` ở đầu dòng tạo Khoản; `a)`, `b)` ở đầu dòng tạo
   Điểm.
5. Child Điểm giữ phần dẫn của Khoản để passage retrieval vẫn có nghĩa, nhưng
   không chứa Điểm anh em.
6. Khi không có ranh giới đáng tin cậy, hệ thống dùng paragraph/length fallback
   hiện có và ghi `child_kind=fallback`; tuyệt đối không nhờ LLM đoán cấu trúc.

Văn bản nhắc “theo Điều 5” hoặc “Phụ lục kèm theo” trong câu thường không tạo
parent mới.

## Trình tự retrieval và an toàn pháp lý

```text
truy xuất child
-> lọc nguồn active/scope/as-of/căn cứ hết hiệu lực
-> rerank độ liên quan
-> xếp hạng authority xác định
-> diversity và giới hạn kết quả
-> áp lại authority trên tập cuối
-> validity-sync overlay loại nguồn bị chặn
-> một batch query lấy parent còn lại
-> nhóm evidence theo parent
```

Parent context không tham gia tính `score`, không thay authority rank, không sửa
metadata hiệu lực và không làm nguồn pending/expired/conflicted trở thành hợp
lệ. Retrieval service áp validity snapshot trước parent lookup;
`LegalSearchClient` áp lại cùng policy ở cổng model-facing để fail closed nếu
payload đến từ service cũ hoặc snapshot thay đổi. Nếu snapshot chặn một child,
parent của child đó không được đưa vào prompt.

## Chiếu ngữ cảnh cha

`api/legal_parent_context.py` nhận tập child cuối cùng và các parent được tải
bằng một truy vấn:

- Khóa lookup gồm cả `article_id` và `document_id`, ngăn gắn nội dung từ văn bản
  khác.
- Mỗi child luôn giữ `matched_child_heading` và `matched_child_content`.
- Child đầu tiên của một parent mang `parent_context`; child anh em chỉ giữ
  `parent_context_ref`, tránh nhân bản payload.
- Ask nhóm theo `parent_context_ref`, đưa parent vào prompt đúng một lần và liệt
  kê toàn bộ child đã khớp.
- Bộ kiểm tra grounding xét cả child và parent, nên thời hạn/lệ phí nằm ở Khoản
  khác trong cùng Điều có thể được xác minh mà không nới lỏng citation identity.

## Ngân sách và fallback

Biến môi trường:

| Biến | Mặc định | Ý nghĩa |
|---|---:|---|
| `LEGAL_PARENT_CONTEXT_MAX_CHARS` | 12.000 | Tối đa cho một Điều/Phụ lục |
| `LEGAL_PARENT_CONTEXT_TOTAL_CHARS` | 36.000 | Tổng parent context trong một request |

Parent được trả đầy đủ khi nằm trong cả hai ngân sách. Parent quá dài dùng cửa
sổ ký tự xác định quanh child khớp và bắt buộc có
`parent_context_truncated=true`. Không tóm tắt bằng LLM.

Reason code nội bộ:

- `complete`: parent đầy đủ.
- `oversized_parent`: đã dùng cửa sổ giới hạn.
- `request_budget_exhausted`: chỉ giữ child vì hết ngân sách request.
- `missing_parent`: không tìm được row cha đúng document.
- `empty_parent`: row cha không có nội dung dùng được.
- `projection_error`: lỗi chiếu ngữ cảnh không làm search sập.

Mọi fallback đều giữ child đã qua lọc; hệ thống không lấy parent lân cận và không
tạo văn bản thay thế.

## Trace và ranh giới công khai

Internal trace thêm thời gian `parent_hydration`, số batch lookup, số parent duy
nhất/đã hydrate/bị truncate/fallback. Chunk trace chỉ ghi reference, loại,
character count, trạng thái truncate và reason code; không ghi full parent body.

`parent_context`, `matched_child_content` và chi tiết fallback không được thêm
vào public citation hoặc SSE allowlist. Citation công khai tiếp tục dùng số/tên
văn bản, Điều/Khoản/Điểm, cơ quan ban hành, ngày hiệu lực và URL nguồn chính
thức.

## Rollout

- Record cũ có `legal_articles.content` được hưởng parent hydration ngay, không
  cần re-index.
- Chỉ tài liệu import mới có đường dẫn Khoản/Điểm và nhận diện Phụ lục phong phú
  hơn.
- Record cũ thiếu parent tiếp tục child-only với reason code.
- Bulk re-index hoặc migration cột là lát cắt riêng, phải được phê duyệt trước.

Rollback chỉ cần hoàn nguyên wrapper parser, bước batch parent hydration và
grouping Ask; dữ liệu cũ không bị biến đổi.

## Kiểm thử và hiệu năng

- 18 kiểm thử mới/điều chỉnh cho parser, projection, grouping, grounding và
  public contract đạt.
- 24 kiểm thử hybrid retrieval và exact tiered retrieval đạt trong runtime
  retrieval hiện tại.
- 3 kiểm thử import/provenance/active-gating mục tiêu đạt.
- Benchmark 3.000 vòng với 12 child, 8 parent: p50 `0,0444 ms`, p95
  `0,0605 ms`, max `3,6725 ms`, không tính DB/network. Gate p95 dưới `15 ms`
  đạt; mỗi search có tối đa một parent lookup.
