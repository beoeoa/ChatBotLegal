# Truy xuất gói Điều chính xác trước khi tạo câu trả lời

## Quyết định

Khi câu hỏi chứa đúng một số văn bản và đúng một số Điều, hệ thống coi đây là
truy xuất định danh, không phải bài toán tìm đoạn tương tự. Pipeline bắt buộc:

```text
số văn bản + số Điều
-> SQL metadata chính xác
-> toàn bộ chunk của đúng article_id
-> sắp xếp chunk_index, chunk_id
-> kiểm tra identity/count/index/empty/structure/content coverage
-> validity overlay và legal hard gates
-> một gói Điều đầy đủ trong model context
-> generation
```

ANN, lexical fallback, broad fallback, diversity cap theo Article/document và
per-hit excerpt cap không được áp dụng cho nhánh này. Các query variant trong
batch được co lại thành một truy vấn vì chúng đều trỏ đến cùng một Điều.

## Gói Điều và điều kiện `complete`

`api/legal_exact_article.py` dựng packet từ các hàng SQL và chỉ trả
`status=complete` khi:

- mọi hàng thuộc cùng document/article identity;
- số chunk đã nạp bằng số chunk SQL khai báo;
- `chunk_index` liên tục, không trùng;
- không có chunk rỗng;
- các Khoản/Điểm và các mảnh dòng của parent body đều được chunk bao phủ;
- toàn văn nằm trong giới hạn context 12.000 ký tự và tối đa 1.000 chunk.

Parent body lưu trong `legal_articles.content` là bản ghép theo thứ tự nguồn.
Nó chỉ được dùng làm context sau khi toàn bộ chunk đã chứng minh coverage. Dữ
liệu nội dung này không được đưa vào trace công khai; trace chỉ có ID, count,
missing index/unit, coverage và reason code.

## Fail-closed trước generation

Search và batch truyền packet integrity đến Ask orchestration. Nếu core/expanded
không tạo được packet đầy đủ, hoặc packet đầy đủ không vừa nguyên vẹn trong
model context:

- `evidence_by_id` bị để trống;
- provider không được provision/invoke;
- câu trả lời nêu rõ kho chưa đủ gói Điều và không dùng một chunk riêng lẻ để
  suy ra toàn bộ Điều;
- Admin trace có `exact_article_gate.ready=false` cùng phần thiếu.

Khi packet đầy đủ, toàn văn chỉ xuất hiện một lần trong prompt, nhưng mọi child
chunk vẫn được giữ để audit và citation. Bộ lọc content relevance không được bỏ
Khoản/Điểm của packet; hiệu lực, nguồn chính thức, conflict và hierarchy vẫn là
hard gate trước generation.

## Tương thích và rollback

Không có schema migration, corpus rewrite, vector mutation hay re-index. Các
truy vấn không định danh đúng một văn bản + một Điều giữ nguyên pipeline hybrid
và parent hydration Feature 011. Rollback là bỏ module/nhánh packet và khởi động
lại retrieval/API; không cần phục hồi dữ liệu.

## Bằng chứng kiểm thử

- Unit: full order, missing index/unit, empty/duplicate chunk, oversized packet,
  one-context/many-child audit.
- Structured context: packet không chịu giới hạn 700 ký tự mỗi hit.
- Orchestration: packet thiếu gọi search core/expanded nhưng gọi model `0` lần.
- Live retrieval: `60/2014/QH13`, Điều 73 tải `2/2` chunk, order `0,1`, coverage
  `1.0`, một parent context 1.640 ký tự; vector cache hit/miss không tăng.
- Live batch: một query, packet complete, vector prefetch xấp xỉ 0 ms.
- Live Ask: exact gate `ready=true`, 2 chunk vào context, câu trả lời được kiểm
  tra grounding/completeness độc lập.
