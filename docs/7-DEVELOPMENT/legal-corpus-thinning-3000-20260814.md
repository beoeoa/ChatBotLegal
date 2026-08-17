# Tinh gọn corpus 7.245 → ứng viên 3.000 (14-08-2026)

## Kết luận hiện tại

Đã hoàn tất lát **read-only** gồm khóa baseline, hard filter, scoring,
coverage balancing, candidate manifest và hợp đồng cô lập retrieval. Kết quả
hiện là `proposed_no_go`; chưa tạo collection ứng viên và không đổi active
pointer.

Lý do NO-GO không phải lỗi build. Corpus hiện có xung đột scope nghiêm trọng:

- 3.825/7.245 dòng `included=true` đồng thời mang reason
  `out_of_commune_scope`;
- 13 dòng là `other_province`;
- 18 dòng hết hiệu lực tại `legal_as_of=2026-08-14`;
- 2 dòng `known_superseded`;
- sau hard filter, nhóm `khieu_nai_to_cao_xu_phat` chỉ còn 7 văn bản đủ điều
  kiện;
- 28 số hiệu được bộ Golden tham chiếu đang không có bản active đủ điều kiện,
  hoặc bị scope cũ đánh dấu ngoài phạm vi;
- collection active không lưu embedding fingerprint trong metadata nên chưa
  thể chứng minh fingerprint candidate giống baseline chỉ từ manifest.

Không được dựng/activate collection 3.000 trước khi xử lý các blocker này.

## Baseline đã khóa

| Chỉ số | Giá trị |
|---|---:|
| Văn bản active/included | 7.245 |
| Article | 71.849 |
| Chunk | 168.155 |
| Chunk rỗng | 1.222 |
| Vector active | 168.155 |
| Missing vector | 0 |
| Orphan vector trong collection active | 0 |
| DB chunk set = active vector set | Đúng tuyệt đối |

Collection baseline:
`legal_chunks_vnlegal_lal_haiphong_unified_v1`.

Baseline manifest SHA-256:
`e2cb95f0289d1cd8ae5af197408f15df01b193cf21bf1349dcbbbb3c527363af`.

## Candidate đã tạo ở mức manifest

| Chỉ số | Giá trị |
|---|---:|
| Văn bản ứng viên | 3.000 |
| Article dự kiến | 35.853 |
| Vector dự kiến | 87.428 |
| Collection dự kiến | `legal_chunks_candidate_3000_v1` |
| Collection đã tạo | Chưa |
| Active pointer thay đổi | Không |

Candidate manifest SHA-256:
`f6a64dca548f39ef25922b88febb71c3a28995c452f6147dabac81e7f7a2e2a9`.

Phân bố 3.000 văn bản được chọn:

| Domain | Số văn bản |
|---|---:|
| `dat_dai_xay_dung` | 1.350 |
| `an_sinh_y_te_giao_duc` | 1.004 |
| `hanh_chinh_cong` | 372 |
| `ho_tich_chung_thuc` | 177 |
| `cu_tru_an_ninh` | 90 |
| `khieu_nai_to_cao_xu_phat` | 7 |

Phân bố này chưa đạt điều kiện coverage do nhóm khiếu nại/tố cáo/xử phạt quá
mỏng. Không được hiểu “đã đủ 3.000” là đủ điều kiện phục vụ.

## Quy tắc hard filter và scoring

Hard filter chỉ dùng dữ liệu có sẵn, không dùng LLM để sinh metadata:

- ngày hiệu lực/hết hiệu lực tại `legal_as_of`;
- reason đã lưu trong `legal_search_scope`;
- nội dung chunk có sử dụng được hay không;
- metadata identity/source/domain bắt buộc;
- số hiệu trùng và canonical record xác định bằng thứ tự ổn định;
- `known_superseded` chỉ lấy từ reason hiện có, không suy đoán quan hệ thay thế.

Văn bản thiếu `effective_date` nhưng còn URL chính thức được giữ trạng thái
`metadata_repairable`; hệ thống không tự điền ngày.

Scoring gồm đúng mười thành phần:

- commune/local relevance;
- legal authority;
- answerability;
- official source trust;
- metadata quality;
- procedure coverage;
- citation value;
- Hải Phòng priority;
- cross-procedure reuse;
- validity confidence.

Selection luôn giữ nguồn Golden còn hợp lệ và văn bản nền tảng, reserve các ô
domain/facet rồi mới xếp theo điểm. Hard safety status không được Golden hoặc
điểm số ghi đè.

## Cô lập retrieval

`api/legal_serving_scope.py` nạp manifest fail-closed chỉ khi có đồng thời:

```text
LEGAL_BENCHMARK_MODE=1
LEGAL_BENCHMARK_SERVING_MANIFEST=<absolute manifest path>
LEGAL_BENCHMARK_SERVING_MANIFEST_FILE_SHA256=<file sha256>
LEGAL_CHROMA_COLLECTION=<collection bound in manifest>
```

Manifest được kiểm tra schema, checksum file, collection name, số document,
số chunk, duplicate ID và exact counts. `scripts/legal_search_server.py` áp
document filter ngay trong SQL trước `LIMIT` cho:

- exact lookup;
- lexical/BM25 source fetch;
- chunk hydration;
- fallback fetch;
- parent hydration.

Vector candidates tiếp tục bị chặn bằng exact chunk set của cùng manifest.
Không đặt các biến benchmark thì runtime giữ nguyên active pointer và không có
manifest filter mới.

## Artifact

- `reports/corpus-thinning/legal-serving-baseline-7245-v1.json`
- `reports/corpus-thinning/legal-serving-candidate-3000-v1.json`
- `reports/corpus-thinning/kept-3000-v1.jsonl`
- `reports/corpus-thinning/excluded-from-3000-v1.jsonl`
- `reports/corpus-thinning/metadata-repairable-v1.jsonl`
- `reports/corpus-thinning/required-source-blockers-v1.json`
- `reports/corpus-thinning/scope-conflicts-v1.jsonl`
- `reports/corpus-thinning/coverage-matrix-3000-v1.json`
- `reports/corpus-thinning/build-summary.json`

## Kiểm thử

```powershell
.venv\Scripts\python.exe -m pytest -q `
  tests/test_legal_corpus_candidate.py `
  tests/test_legal_serving_scope.py

.venv-retrieval-cu126\Scripts\python.exe -m pytest -q `
  tests/test_retrieval_serving_scope.py `
  tests/test_db5_exact_tiered_retrieval.py `
  tests/test_hybrid_retrieval.py
```

Kết quả hợp nhất trong đúng runtime retrieval: **66 test pass**. Có một warning
deprecation từ Starlette/httpx, không ảnh hưởng kết quả. Hai test manifest cũ
không thể import khi chạy nhầm bằng `.venv` vì môi trường đó không cài
`chromadb`; chạy bằng `.venv-retrieval-cu126` đạt đầy đủ.

## Bước tiếp theo bắt buộc

1. Rà 28 required-source blockers; sửa scope sai bằng nguồn chính thức, không
   override hàng loạt. Kết quả inventory toàn bộ 12.235 văn bản: 10 nguồn có
   bản active nhưng xung đột scope/metadata, 18 nguồn hoàn toàn chưa có trong
   database và 0 nguồn chỉ còn bản lịch sử.
2. Rà riêng nhóm khiếu nại/tố cáo/xử phạt và bổ sung/khôi phục các văn bản nền
   tảng đúng hiệu lực.
3. Hoàn thiện 58 metadata-repairable và xác minh 59 metadata-unverified.
4. Ghi embedding fingerprint baseline có bằng chứng hoặc tái dựng baseline
   theo một release index được phê duyệt; không suy đoán fingerprint.
5. Build lại candidate manifest. Chỉ khi không còn required-source blocker và
   coverage matrix đạt mới được copy vector sang collection ứng viên.
6. Sau đó chạy cold/warm Golden baseline/candidate, integrity và rollback
   rehearsal; chỉ activation khi mọi threshold trong yêu cầu đạt.
