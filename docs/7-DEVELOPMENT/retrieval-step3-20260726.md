# Bước 3 — Retrieval theo issue (2026-07-26)

Trạng thái kỹ thuật: `PASS`.

Phạm vi thay đổi chỉ gồm đường retrieval/benchmark và regression test; không
xóa corpus, không sửa bản ghi nhập, không đổi active collection và không
embedding lại corpus.

## Serving contract

- Exact law/article/clause lookup dùng metadata đã chuẩn hóa trước ANN.
- Multi-law exact lookup giữ document-level match khi một article không tồn tại
  trên mọi văn bản được nêu.
- Issue dài hơn 40 ký tự không chạy wildcard lexical scan; ANN và exact lookup
  vẫn giữ nguyên.
- Primary chạy trước support; support tối đa một lần cho issue còn thiếu.
- Eligibility loại wrong-field, inactive/expired, superseded, sai scope hoặc
  hierarchy trước khi rerank.
- RRF/rerank giữ ngân sách primary `150/60/40`, support `100/40/40`; diversity
  tối đa 2 chunk/điều và 3 chunk/văn bản.

## Shadow gate

Artifact đầy đủ:
`reports/feature005/step3-retrieval-20260726/retrieval-167-role9-r2.json`

| Run | Recall@10 | Direct top-5 | Wrong/expired | P95 (ms) | Coverage |
|---|---:|---:|---:|---:|---:|
| old-core, 167 | 96.552% | 95.238% | 0 | 350.482 | 61.170% |
| new-serving, 167 | 100% | 100% | 0 | 267.754 | 97.606% |
| old-core, role 9 | 100% | 100% | 0 | 462.279 | 100% |
| new-serving, role 9 | 100% | 100% | 0 | 434.453 | 100% |

`new-serving` có 0 `FOUND_NOT_RETRIEVED`; các issue không có approved
expected-source contract được ghi `EXPECTED_CONTRACT_GAP`, không bị biến thành
lỗi retrieval hay nguồn pháp lý mới. Active pointer trước/sau đều không đổi.

## Verification

- 74 regression/retrieval tests: PASS.
- Full backend: 885 passed, 11 warnings.
- `corpus_reembedded=false`; collection counts được kiểm tra read-only.
