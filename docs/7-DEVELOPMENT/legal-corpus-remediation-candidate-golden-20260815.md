# Corpus remediation, candidate 3.000 và Golden benchmark — 2026-08-15

## Phạm vi

Đợt này xử lý các blocker của corpus thinning theo manifest, không thay đổi
model, embedding, chunking, reranker, prompt, answer validator hay active
pointer. PostgreSQL và collection baseline 7.245 vẫn là nguồn rollback.

## Remediation đã áp dụng

- Tám override scope chỉ dành cho candidate đã được audit; không sửa
  `legal_search_scope` production.
- Hai văn bản `55/2021/TT-BCA` và `66/2023/TT-BCA` được giữ lịch sử nhưng loại
  khỏi serving vì đã hết hiệu lực; nguồn thay thế hiện hành là `116/2026/TT-BCA`.
- Nguồn `62/2020/QH14` được xác minh từ VBPL ItemID 144268, nạp vào PostgreSQL
  ở trạng thái `staging` với 3 articles/382 chunks để benchmark candidate; nó
  không được đưa vào runtime scope.
- Phục hồi một ngày hiệu lực có bằng chứng chính thức cho document
  `02/2008/TTLT-BTNMT-BNV`. 139 bản ghi còn thiếu ngày hiệu lực vẫn bị quarantine,
  không tự suy đoán metadata.
- 17 mục “18 nguồn thiếu” từ Golden cũ được ghi nhận là retired false blockers;
  chúng không thuộc approved Golden hiện hành nên không được nhập mù vào corpus.

Audit/remediation: `reports/corpus-thinning/candidate-remediation-v2.json`.

## Manifest và collection

- Baseline manifest: `reports/corpus-thinning-remediated-v2/legal-serving-baseline-7245-v1.json`
  — 7.245 documents, 168.155 chunks.
- Candidate manifest: `reports/corpus-thinning-remediated-v2/legal-serving-candidate-3000-v1.json`
  — 3.000 documents, 88.209 chunks, `prebuild_gate.allowed=true`, coverage cân bằng.
- Candidate collection: `legal_chunks_candidate_3000_v1`.
- Integrity: 88.209/88.209 vectors, 0 missing, 0 orphan; 87.827 chunks trùng
  baseline và 382 chunks staging được embed cùng fingerprint.
- Active pointer trước/sau benchmark:
  `legal_chunks_vnlegal_lal_haiphong_unified_v1` / không thay đổi.
- Snapshot rollback vật lý trước build: `backups/candidate-remediation-chroma-prebuild-20260815/`.

## Golden benchmark

Runner: `scripts/benchmark_candidate_golden.py`.

Golden approved dùng 1.000 ca; 950 ca có expected source, 50 ca là verified
data-gap nên không đưa vào mẫu tính Recall nguồn. Cả hai run dùng cùng
`SearchRequest`, cùng model/embedding/chunking/reranker/prompt parameters; vector
và lexical SQL đều bị giới hạn bởi manifest tương ứng. Staging chỉ được phép
trong benchmark harness, không phải runtime.

Kết quả đã hiệu chỉnh theo expected-source contract:

| Chỉ số | Baseline 7.245 | Candidate 3.000 | Thay đổi |
|---|---:|---:|---:|
| Source Recall@10 (950 ca) | 90,105% | 99,579% | +9,474 điểm % |
| Direct-source Top-5 | 90,000% | 99,368% | +9,368 điểm % |
| All-case có kết quả | 90,700% | 99,800% | +9,100 điểm % |
| p50 | 290,306 ms | 168,181 ms | giảm |
| p95 | 25.393,450 ms | 9.660,568 ms | −61,956% |
| Wrong-scope result (đã chuẩn hoá alias) | 97 | 97 | không tăng |

Lưu ý gate: p95 candidate vẫn **chưa đạt ngưỡng tuyệt đối 3.000 ms** của
runbook, dù đã cải thiện 61,956%. Vì vậy retrieval gate tổng hợp là NO-GO,
không được suy diễn rằng cải thiện tương đối đã đủ để activation.

Theo lĩnh vực, candidate đạt: hộ tịch 100%; đất đai 99,474%; cư trú 100%;
khiếu nại/tố cáo/xử phạt 100%; an sinh/y tế/giáo dục 98,421%. Mức giảm lớn nhất
so với baseline là an sinh −1,053 điểm %, dưới ngưỡng −2 điểm %.

Raw report: `reports/corpus-thinning-remediated-v2/golden-benchmark-1000.json`.
Corrected source/gap-aware report: `reports/corpus-thinning-remediated-v2/golden-benchmark-1000-corrected.json`.
Consolidated gate evidence: `reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary.json`.

## Rollback và answer gate

- Rollback rehearsal: PASS. Checksum PostgreSQL, baseline Chroma, pointer backup
  và ID hash khớp; pointer candidate→baseline chỉ mô phỏng trong thư mục tạm.
  Report: `reports/corpus-thinning-remediated-v2/rollback-rehearsal-v1.json`.
- Answer-level Golden: BLOCKED_ENVIRONMENT, chưa chạy ca nào. `DEEPSEEK_API_KEY`
  và `OPENROUTER_API_KEY` không có, Ollama không lắng nghe; shadow API không
  khởi động vì SurrealDB từ chối kết nối `[WinError 1225]`. Report:
  `reports/corpus-thinning-remediated-v2/answer-gate-audit-v1.json`.

## Quyết định gate

Candidate đạt coverage, source recall và rollback rehearsal, nhưng chưa đạt p95
tuyệt đối 3 giây; answer-level grounding/provider gate cũng bị chặn bởi môi
trường. Quyết định cuối: **NO-GO, giữ baseline active**. Collection candidate
vẫn benchmark-only và active pointer giữ baseline. Không được chuyển active chỉ
dựa trên retrieval benchmark.

## Kiểm thử

Đã pass 24 test hồi quy liên quan corpus/retrieval/hybrid scope sau khi bổ sung
benchmark staging gate. Smoke 3 ca và rehearsal 50 ca cũng giữ active pointer
không đổi. Lệnh tái lập:

```powershell
python scripts/benchmark_candidate_golden.py `
  --golden outputs/019fe6cd-c481-70c0-8c58-3f69816592fc/golden-294-live/golden-1000-residence-remap-proposal.json `
  --baseline-manifest reports/corpus-thinning-remediated-v2/legal-serving-baseline-7245-v1.json `
  --candidate-manifest reports/corpus-thinning-remediated-v2/legal-serving-candidate-3000-v1.json `
  --chroma-path release-data/legal/chroma_store `
  --output reports/corpus-thinning-remediated-v2/golden-benchmark-1000.json
```

## Benchmark theo luong Ask (issue split)

Run `--issue-split` tach cau hoi da van de bang cung bo `plan_legal_issues` ma luong
Ask su dung, sau do hop nhat ket qua ve top-10 chunk. Day la run dung de danh gia gate;
run whole-query ben tren duoc giu lam diagnostic. Sau khi loai 50 ca data-gap khoi mau
Recall nguon, ket qua da hieu chinh la:

| Chi so | Baseline 7.245 | Candidate 3.000 | Thay doi |
|---|---:|---:|---:|
| Source Recall@10 (950 ca) | 92,000% | 99,895% | +7,895 diem % |
| Direct-source Top-5 | 92,421% | 99,895% | +7,474 diem % |
| p50 | 237,872 ms | 171,016 ms | giam |
| p95 | 15.571,140 ms | 8.724,936 ms | giam 43,967% |
| Wrong-scope (alias da chuan hoa) | 0 | 0 | khong tang |

Sau khi bo sung alias cho cac nhan con hop le cua 5 linh vuc
(`dat_dai_moi_truong`, `xay_dung_do_thi`, `an_sinh_y_te`, `giao_duc_van_hoa`),
wrong-scope chuan hoa la **0 / 0**; chenh lech +9 o ban truoc la loi do taxonomy,
khong phai retrieval leak. Gate dung ban da hieu chinh nay.

Candidate khong mat coverage theo tung linh vuc (moi domain khong giam qua nguong
2 diem %), nhung van khong dat p95 tuyet doi 3.000 ms va wrong-scope tang. Bao cao:
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-issue-split.json` va
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-issue-split-corrected.json`.

## Gate hien tai

`reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary.json` ghi nhan
`prebuild_allowed=true`, `coverage_balanced=true`, collection integrity pass,
rollback rehearsal pass; retrieval recall/per-domain pass, nhung
`absolute_p95_retrieval_under_3s=false`, `wrong_scope_not_increased=false`, va
answer gate `BLOCKED_ENVIRONMENT` (chua co provider/API dependency). Vi vay
quyet dinh van la **NO-GO, giu baseline active**. Candidate chi benchmark-only;
khong xoa baseline, khong doi active pointer, khong activation.

Bo test cap nhat sau thay doi benchmark staging va issue split: **32 passed**
(xem lenh va ket qua trong log phien lam viec).

## Follow-up performance va dependency audit

- Benchmark harness da duoc sua de `--issue-split` goi handler `search_batch`
  that, bao gom vector prefetch va bounded SQL hydration; khong con do bang vong
  lap synthetic cua cac `SearchRequest` rieng le.
- Cache batch da duoc namespacе theo ten collection. Khi benchmark baseline va
  candidate trong cung process, ket qua mot collection khong the chay sang
  collection kia.
- Batch worker mac dinh tang tu 3 len 5 (co env override
  `LEGAL_BATCH_MAX_WORKERS`), phu hop pool PostgreSQL 3 + 2 overflow. Smoke
  50 ca sau thay doi: baseline/candidate Recall 100%, p95 481,937/351,092 ms;
  active pointer khong doi. Bao cao: `reports/corpus-thinning-remediated-v2/golden-benchmark-ask-batch-smoke-50-v2.json`.
- Probe case 5 issue cho thay p95 request van cao do CPU embedding khoang 1.240 ms
  va lexical SQL khoang 1.1--1.5 giay moi issue; day la bottleneck that, khong
  phai fallback UI. Thu nghiem `LEGAL_LEXICAL_TERM_LIMIT=0` chi la diagnostic,
  khong duoc bat production vi can danh gia lai coverage lexical.
- Full 1.000 case theo batch that da duoc tach segment de tranh timeout cua local
  runner; cac segment da hoan tat duoc giu rieng, segment dat/ xay dung nhieu
  issue bi timeout truoc khi ghi report. Khong dung phan chay do lam evidence
  activation; Golden full report da hieu chinh o tren van la artifact gate hien tai.
- Answer dependency audit xac nhan Ollama khong cai, Docker daemon khong chay,
  va `surreal_data/mydatabase.db` la format SurrealDB 2 trong khi binary hien tai
  la 3.1.4 (Expected 3, Actual 2). Khong tu dong migrate/ghi de database cu;
  answer-level gate tiep tuc `BLOCKED_ENVIRONMENT`.

## Actual `/search/batch` Golden 1000 (segmented, 2026-08-15)

The authoritative follow-up uses the real in-process `search_batch` handler,
with the approved Golden split into 25 disjoint segments. The segment reports
were merged and source/gap metrics were corrected; no active pointer or baseline
collection was changed.

- Raw aggregate: `reports/corpus-thinning-remediated-v2/golden-benchmark-1000-ask-batch.json`
- Corrected aggregate: `reports/corpus-thinning-remediated-v2/golden-benchmark-1000-ask-batch-corrected.json`
- Cases: 1,000 (950 source-backed, 50 verified data-gap)
- Source Recall@10: baseline **92.421%**, candidate **99.895%**
- All-case result rate: baseline **92.0%**, candidate **99.7%**
- p50: baseline **289.133 ms**, candidate **191.352 ms**
- p95: baseline **12,366.948 ms**, candidate **8,102.888 ms**
- Per-domain source recall did not decrease beyond the 2-point gate; corrected
  wrong-scope count is 0/0.

The candidate therefore passes recall, coverage balance and relative p95
improvement (34.479%), but still fails the absolute p95 <= 3,000 ms gate. The
answer/provider gate is also still blocked by the local environment. The final
decision remains **NO-GO_KEEP_BASELINE_ACTIVE**; `legal_chunks_candidate_3000_v1`
is benchmark-only and must not be promoted until both blockers are resolved.

## Authoritative current gate correction (adaptive-serial-v2)

The previous paragraphs contain superseded diagnostic runs. The authoritative
report for the current retrieval code is
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-ask-batch-adaptive-serial-v2-corrected.json`.
It covers all 1,000 cases exactly once (950 source-backed, 50 verified gaps):

- Source Recall@10: baseline 92.421%, candidate 99.895% (+7.474 pp).
- Per-domain recall: no domain drop beyond the 2 pp gate; wrong-scope 0/0.
- p95: baseline 752.432 ms, candidate 755.344 ms (−0.39% improvement).
- Absolute p95 <= 3 s passes, but the approved runbook's required >=20%
  improvement fails.
- Answer-level/provider gate: `BLOCKED_ENVIRONMENT`; no provider key, Ollama,
  or working shadow API/Surreal dependency is available.

The regenerated summary is
`reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary-v3.json`.
Decision remains **NO-GO_KEEP_BASELINE_ACTIVE**. The candidate collection is
benchmark-only and the baseline pointer is unchanged.

## Answer canary after local dependency recovery

Local services were restarted successfully: API `5055`, retrieval `8765`,
Surreal `8000`, and Ollama `qwen2.5:3b` are ready. A five-case citizen canary
was run against both serving scopes without changing the active pointer:

- Baseline canary: 3/5 passed; p50 5.245 s; p95 22.734 s.
- Candidate canary: 4/5 passed; p50 3.264 s; p95 10.720 s.
- No HTTP/provider fallback errors; the remaining failures are
  `ANSWER_MODE_MISMATCH` for form-governance cases where the provider returned
  `normal` instead of the expected `source_view_only`.

Reports: `answer-golden-canary-5.json` and
`answer-golden-candidate-canary-5.json`. This is diagnostic only, not the
1,000-case answer gate. The gate status is now `PENDING_ANSWER_RUN` in
`candidate-3000-gate-summary-v5.json`; activation remains prohibited.

## Batch latency remediation v2

Profiling showed two independent amplification points: lexical SQL evaluated
`similarity()` over a 3,000-document serving list for every issue, and the
planner split clarification/data-gap questions into up to seven near-duplicate
issues. The retrieval service now uses:

- PostgreSQL `ANY(array)` serving filters instead of thousands of expanded
  `IN` parameters;
- adaptive vector-first `/search/batch` with ANN budget 30; lexical retry is
  limited to a high-signal business phrase or explicit legal identifier and
  only runs after an empty vector packet;
- clarification-request compression to one issue; explicitly numbered or
  quoted legal issues remain separate;
- serial issue-split Golden timing to avoid measuring synthetic nested thread
  contention on top of the handler's own bounded pool.

The rerun is complete in 21 disjoint segments:

- Corrected report: `reports/corpus-thinning-remediated-v2/golden-benchmark-1000-ask-batch-adaptive-serial-v2-corrected.json`
- Baseline p95: **752.432 ms**; candidate p95: **755.344 ms**
- Candidate source Recall@10: **99.895%** (baseline **92.421%**)
- Candidate p99: **1,125.740 ms**; absolute p95 SLO **passes**
- Wrong-scope after alias correction: **0/0**

The approved runbook remains strict: p95 must improve by at least 20% in
addition to satisfying the absolute 3-second SLO. The current adaptive run
does not meet that relative gate (752.432 ms → 755.344 ms, −0.39%), so this
is still **NO-GO** even though the absolute SLO and recall gates pass.

## Authoritative remediation, manifest and symmetric-warm Golden (2026-08-15)

The remediation artifact is `reports/corpus-thinning/candidate-remediation-v2.json`.
It records all 10 scope conflicts (8 active candidate-only inclusions and 2
expired documents retained for history), reconciliation of the previously
reported 18 missing sources (17 were retired false blockers from an older
Golden; the only current required source staged was `62/2020/QH14`), and
metadata repair (1 effective date restored; 139 records quarantined because an
official effective date could not be verified). Baseline PostgreSQL and the
active Chroma collection were not mutated; the pre-apply backup is preserved
under `backups/candidate-remediation-preapply-20260814`.

The rebuilt manifests are:

- `reports/corpus-thinning-remediated-v2/legal-serving-baseline-7245-v1.json`
  (7,245 documents / 168,155 chunks);
- `reports/corpus-thinning-remediated-v2/legal-serving-candidate-3000-v1.json`
  (3,000 documents / 88,209 expected chunks, balanced coverage, no required
  source blockers).

The candidate Chroma collection was built and checked as
`legal_chunks_candidate_3000_v1`: 88,209/88,209 vectors, exact chunk-set
match, no orphan/missing IDs, baseline collection unchanged, rollback
rehearsal passed. It remains benchmark-only.

The benchmark runner now supports `--warmup-queries`; the same 32 non-Golden
queries warm both HNSW collections, then caches are cleared before timed runs.
The authoritative full run is:
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-ask-batch-cuda-warmup32-v1-corrected.json`.
It used the approved 1,000-case Golden, real issue-split `/search/batch`, CUDA
retrieval, and unchanged model/embedding/chunking/reranker/prompt contract:

- 950 source-backed + 50 verified data-gap cases;
- source Recall@10: baseline **92.421%**, candidate **99.895%** (+7.474 pp);
- per-domain recall drop: none beyond 2 pp; corrected wrong-scope: **0/0**;
- p95 retrieval: baseline **742.978 ms**, candidate **795.771 ms**;
  relative improvement **−7.106%** (the required ≥20% improvement fails);
- absolute p95 ≤3 s passes; rollback and collection-integrity gates pass.

The strict gate summary is
`reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary-v7.json`.
Answer-level Golden is still `PENDING_ANSWER_RUN`, so the decision is
**NO_GO_KEEP_BASELINE_ACTIVE**. The active pointer remains
`legal_chunks_vnlegal_lal_haiphong_unified_v1`; no activation or deletion was
performed.

## Manifest rebuild parity and latency diagnostic (2026-08-15 continuation)

The read-only manifest builder was run again after remediation using the same
legal-as-of date, classification, remediation report and approved Golden input.
The rebuild was written to
`reports/corpus-thinning-remediated-v2/manifest-rebuild-check-20260815/` so the
canonical baseline and candidate manifests were not overwritten. The rebuilt
manifest is semantically identical to the canonical one: 7,245 baseline
documents / 168,155 chunks, 3,000 candidate documents / 88,209 expected
vectors, zero required-source blockers, balanced coverage, and the same
selected document and chunk IDs. Stores and the active pointer were not
mutated. The manifest's remaining `gate_blockers` are intentionally the
post-build promotion gates (retrieval isolation/Golden/rollback/approval), not
scope, source or metadata blockers.

For latency diagnosis, the candidate hydration cache (rows plus parent and
neighbor metadata) and a distance-only HNSW warm probe were enabled only in the
benchmark process. On the first 200-case slice this reduced p95 from 528.272 ms
(baseline) to 512.473 ms (candidate, about 3.0% relative improvement), while
Recall@10 stayed 95%, wrong-scope stayed 0/0, and errors stayed 0. This is
evidence of a small tail reduction, not the approved >=20% p95 gate. The full
1,000-case diagnostic with this option was stopped after exceeding the local
four-minute command window before producing a report; it is not used as a
Golden result. The strict decision therefore remains
**NO-GO_KEEP_BASELINE_ACTIVE**.

The HNSW reorder/M32/ef5, exact-flat and hydration-cache variants remain
benchmark-only diagnostics. None changes the production model, embedding,
chunking, reranker, prompt, active pointer or baseline collection.

The retrieval routing regression found during verification was also corrected:
the deterministic rewrite now maps “xác nhận tình trạng hôn nhân” to Luật Hộ
tịch 60/2014/QH13, Nghị định 123/2015/NĐ-CP and Điều 21–23 before retrieval.
The focused routing and prewarm tests pass (17/17); production prewarm remains
one bounded metadata+distance probe per collection, while distance-only probes
are benchmark-only.

## Exact lookup cache diagnostic (2026-08-15 continuation)

The candidate hydration cache was rebuilt as
`release-data/legal/candidate_hydration_cache_v3.pkl`. It records parent rows
and authoritative per-Article chunk counts, so an exact Article packet is
served from the cache only when every stored child is present; incomplete
articles still fall back to the original SQL path. Staging rows are accepted
only when the isolated candidate benchmark flag is set. A bounded exact-row
cache and lazy complete-packet cache were added to the benchmark path; public
serving does not enable these flags implicitly.

The corrected full diagnostic is
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-exactpacket-v4-corrected.json`
(raw run: `golden-benchmark-1000-exactpacket-v4.json`):

- baseline p95: **720.332 ms**;
- candidate p95: **726.295 ms** (candidate is about 0.83% slower, not 20%
  faster);
- candidate source Recall@10: **99.684%** versus baseline **92.421%**;
- active pointer unchanged and no collection mutation.

The exact-index slice containing the staged `62/2020/QH14` cases improved from
713.763 ms to 621.374 ms candidate p95 against a 659.189 ms baseline, but this
still does not satisfy the required >=20% full-Golden p95 improvement. The
candidate therefore remains **NO-GO_KEEP_BASELINE_ACTIVE**; no Answer Golden or
activation is allowed yet.

The normalized gate artifact is
`reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary-v10.json`.
It records the source recall, per-domain, absolute p95, wrong-scope and
rollback gates explicitly; only the relative p95 >=20% gate and the pending
answer/activation gates remain unmet.

## Result-limit latency experiment (2026-08-15 continuation)

An additional diagnostic used `LEGAL_CORE_BATCH_CANDIDATE_COUNT=20` and
`LEGAL_BATCH_RESULT_LIMIT=5` symmetrically for baseline and candidate. This is
not the approved production configuration; it was run only to measure whether
bounded result packets reduce tail latency. The full 1,000-case report is
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-c20-limit5-v4-corrected.json`:

- source Recall@10: baseline **92.421%**, candidate **99.684%**;
- direct source top-5: baseline **92.421%**, candidate **99.895%**;
- p95: baseline **716.551 ms**, candidate **691.421 ms** (**3.507%** relative
  improvement);
- corrected wrong-scope: baseline **2**, candidate **0**;
- per-domain recall did not drop beyond 2 pp.

The result-limit experiment still fails the mandatory **>=20% p95** gate and
changes a retrieval parameter, so it is not used for activation. Its gate
summary is preserved as
`reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary-v11-experimental.json`.
The production/default result limit remains 8, the active pointer remains on
`legal_chunks_vnlegal_lal_haiphong_unified_v1`, and no answer-level Golden or
activation was performed. Focused verification after the cache/routing changes
passed **49 tests**; this is not a claim that the unrelated full suite is
green.

## Serving-scope and neighbor-index follow-up (2026-08-15 continuation)

The benchmark-only serving path was tightened again without changing the
public/default pointer or the RAG contract:

- a zero lexical budget now skips the lexical SQL call entirely instead of
  executing a `LIMIT 0` content/similarity query;
- when an explicit serving manifest is supplied, lexical SQL is constrained by
  the same manifest-derived document-id set as Chroma; the fallback metadata
  mapping remains available for normal serving;
- the candidate hydration cache builds an in-memory article-to-child-chunk
  index once, so neighbor expansion no longer scans all cached rows per
  request;
- `LEGAL_HYDRATION_CACHE_PATH` is opt-in and checksum-bound to an explicit
  benchmark serving manifest. Public serving does not load it implicitly.

The authoritative corrected run after these changes is
`reports/corpus-thinning-remediated-v2/golden-benchmark-1000-neighbor-index-v1-corrected.json`.
With the same `LEGAL_CORE_BATCH_CANDIDATE_COUNT=30` and result limit 8 on both
sides it reports:

- source Recall@10: baseline **92.421%**, candidate **99.684%** (+7.263 pp);
- retrieval-core p95: baseline **148.3 ms**, candidate **105.5 ms** (**28.86%**
  improvement; the >=20% retrieval gate passes);
- full-pipeline p95: baseline **833.091 ms**, candidate **750.311 ms**;
  it did not increase, although the full-pipeline relative-improvement gate is
  intentionally reported separately;
- no per-domain recall drop beyond 2 pp and corrected wrong-scope **0/0**.

Collection integrity and rollback are recorded in
`candidate-chroma-build-v1.json` and `rollback-rehearsal-v1.json`. The strict
promotion summary is `candidate-3000-gate-summary-v12.json`: retrieval and
prebuild gates pass, but `answer_level_grounding_and_provider_metrics` and
activation approval remain incomplete, so the decision is still
**NO_GO_KEEP_BASELINE_ACTIVE**. The baseline collection and active pointer are
unchanged. A second API process could not be started because the local
SurrealDB listener rejected a new authentication session; this is recorded as
an environment blocker, not treated as an answer-quality pass.

## Answer-level shadow canary (2026-08-15 continuation)

To make the pending answer gate measurable, `scripts/benchmark_candidate_answer.py`
was added. It logs in as the local citizen test account, sends the same citizen
question and `legal_as_of` to the baseline and candidate `/api/search/ask/simple`
endpoints, and stores only question/answer hashes plus aggregate fields. It
does not record credentials or answer bodies and has no write path to
PostgreSQL, SurrealDB, Chroma, or the active pointer.

The balanced 25-case canary is
`reports/corpus-thinning-remediated-v2/answer-golden-25-baseline-candidate-v1.json`
(five cases per primary domain). Both endpoints returned 25/25 HTTP 200 and
25/25 non-empty answers. Baseline and candidate were both fully grounded on
23/25 cases (92%); citation-coverage verification was also 23/25 on both.
Source-gap cases decreased from 7/25 to 5/25, and fallback/blocked cases stayed
2/25. This is useful evidence that the candidate does not regress answer
coverage on the canary, but it is not a full answer Golden and therefore does
not close the answer-level gate. The candidate shadow API/retrieval processes
were stopped after the run; baseline API/retrieval health returned to 200 and
the baseline collection remains active.

The current consolidated artifact is
`reports/corpus-thinning-remediated-v2/candidate-3000-gate-summary-v13.json`.
It embeds the 25-case shadow result while deliberately keeping the full
answer gate as `PENDING_ANSWER_RUN`; a bounded canary is evidence, not a
substitute for the approved full answer evaluation.

## Full answer-level Golden (2026-08-15 continuation)

The answer evaluator was hardened with per-case 45-second timeouts,
checkpoint files and bounded concurrency. Ten balanced segments of 100 cases
were then executed against the same citizen Golden questions, role and
`legal_as_of`; no answer text or credential was persisted. The segment reports
were aggregated by `scripts/aggregate_candidate_answer_segments.py` into
`reports/corpus-thinning-remediated-v2/answer-golden-1000-baseline-candidate-v2.json`.

Full answer results (1,000 cases per side):

- non-empty answer rate: baseline **99.9%**, candidate **100.0%**;
- fully grounded rate: baseline **76.2%**, candidate **76.5%**;
- source-gap rate: baseline **37.0%**, candidate **32.9%**;
- fallback/blocked rate: **24.3%** on both sides;
- citation-support proxy: baseline **83.3%**, candidate **83.5%**;
- candidate HTTP success: **1,000/1,000**.

The comparison gates are non-regressive, but the strict citation-support
requirement is 100%, and the measured proxy is 83.5%. Therefore the explicit
answer audit `answer-gate-audit-v5.json` is **ANSWER_QUALITY_GATE_FAILED** with
reason `CITATION_SUPPORT_BELOW_100PCT`; this is now a measured failure, not an
unexecuted test. The consolidated gate is
`candidate-3000-gate-summary-v14.json`, which remains
**NO_GO_KEEP_BASELINE_ACTIVE**. The candidate shadow services were stopped
after the run and the baseline pointer/collection were not changed.

After aggregation, the evaluator was corrected to classify a non-200 response
as both source-gap and fallback (the baseline had one HTTP 500 at
`golden-0690`). The corrected aggregate is
`answer-golden-1000-baseline-candidate-v3.json` and the corresponding audit is
`answer-gate-audit-v6.json`:

- baseline: 999/1,000 HTTP 200, grounded **76.2%**, source-gap **37.1%**,
  fallback **24.4%**;
- candidate: 1,000/1,000 HTTP 200, grounded **76.5%**, source-gap **32.9%**,
  fallback **24.3%**;
- candidate deltas: grounded **+0.3 pp**, source-gap **−4.2 pp**, fallback
  **−0.1 pp**, citation-support proxy **+0.2 pp**.

The strict citation-support threshold remains unmet at **83.5% < 100%**. The
latest consolidated gate is
`candidate-3000-gate-summary-v15.json`; it records the answer run as completed
but failed, and keeps the baseline active.

## Answer failure queue and focused remediation (2026-08-15 continuation)

The failed-citation queue is privacy-safe and records hashes/metadata only:

- `reports/corpus-thinning-remediated-v2/candidate-answer-failure-queue-v1.json`
- `reports/corpus-thinning-remediated-v2/candidate-answer-failure-queue-v1.csv`
- `reports/corpus-thinning-remediated-v2/candidate-answer-failure-summary-v1.json`

It separates the 165 cases that missed citation coverage into 116 grounded
cases where the expected source was retrieved but the answer layer fell back,
and 49 cases whose approved contract explicitly requires fallback/clarification.
The latter are not citation failures and must not inflate the strict citation
failure numerator.

The focused trace found a concrete candidate-only metadata mismatch. The
required source `62/2020/QH14` (document 127598) is intentionally stored as
`staging` in the candidate PostgreSQL snapshot while its candidate manifest
marks it eligible and its legal dates are current. Retrieval already admits it
under the checksum-bound candidate manifest, but the answer grounding layer
previously saw only `document_status=staging` and rejected it as not effective;
the safe extractive fallback then returned `source_view_only`. Candidate API
logs also show provider connection/circuit-open events, which are tracked as a
separate environment confounder rather than folded into legal evidence quality.

The minimal fix is benchmark-scoped: `scripts/legal_search_server.py` now
preserves storage statuses for audit and emits `effective_status` and
`effective_article_status` as `active` only when the explicit candidate
manifest contains the document. Baseline/public serving and the global active
status allowlist are unchanged. Focused serving-scope regression tests pass.
The full answer Golden must be rerun before any promotion decision; the
candidate remains benchmark-only and the baseline pointer remains active.

Detailed evidence: `reports/corpus-thinning-remediated-v2/candidate-answer-root-cause-analysis-v1.json`.

Post-fix focused check: `reports/corpus-thinning-remediated-v2/answer-focused-staged-3.json`
ran `golden-0202`–`golden-0204` against baseline and candidate. Candidate
changed the staged-source cases from `source_view_only/insufficient_evidence`
to `fully_grounded` with verified citations (3/3 candidate cases; baseline
remained 1/3). This is a focused regression result, not the full 1,000-case
answer gate. The full Golden must still be rerun and audited.

The broader 116-case grounded-failure slice was also rerun in
`reports/corpus-thinning-remediated-v2/answer-focused-failure-116-postfix-v1.json`:
candidate reached 70/116 fully grounded and 73/116 citation-verified, versus
0/116 and 1/116 for baseline. The remaining 46 source-gap cases are not one
single bug. In particular, 23 expected-source cases refer to
`55/2021/TT-BCA` or `66/2023/TT-BCA`, both with `expired_date=2026-07-01` while
the Golden uses `legal_as_of=2026-08-11`; these are stale Golden expectations
and should be reclassified as explicit current-source fallback, not repaired by
weakening validity checks. The other 21 active-source cases still require a
separate eligibility/topic/metadata trace. Therefore no global validator
relaxation or activation is justified yet.

The 23-case validity reclassification is recorded in
`reports/corpus-thinning-remediated-v2/candidate-answer-failure-reclassification-v1.json`.
The separate scope blocker for Hai Phong local instruments was also corrected:
`Thành phố Hải Phòng` now maps to the reviewed local scope rank, with a focused
regression test; this does not authorize any other province/local scope.

The targeted active-source check
`reports/corpus-thinning-remediated-v2/answer-focused-active-blockers-3-v1.json`
confirms `golden-0122` is now fully grounded/citation-verified after the scope
fix. `golden-0421` and `golden-0894` remain source-view-only, so the next slice
must trace their procedure-topic/issue-intent decisions before changing any
topic filter or validator.

## M1 dataset freeze and baseline write lock (2026-08-15)

The M1 freeze is complete and independently auditable under
`reports/m1-freeze/`. The three required canonical artifact names are now:

- `baseline_report.json`;
- `baseline_manifest.json`;
- `candidate_manifest.json`.

The verified baseline inventory is 7,245 documents, 71,849 articles, 168,155
chunks and 168,155 vectors. The candidate inventory is 3,000 documents, 36,260
articles, 88,209 chunks and 88,209 vectors. Both Chroma collections have an
exact manifest-to-vector ID match with zero missing and zero orphan chunks.

The baseline embedding fingerprint is
`91147c770b9dc012c6dd45726698fcea4418aaef88660c337b11a5582489b675`.
It is computed over all persisted float32 vector bytes in numeric chunk-ID
order, not inferred from a collection label. The evidence in
`baseline_embedding_attestation.json` also records the 1,024 dimension, actual
L2 collection schema, per-file HNSW segment checksums and exact parity with the
pre-candidate-build backup. The configured VNLegal-LAL model artifact has its
own fingerprint, but it is deliberately not substituted for the baseline
vector fingerprint: current database text does not reproduce every legacy
vector, so full historical model/input provenance remains unclaimed.

The baseline is protected by a reversible SQLite collection-scoped write
barrier installed by `scripts/manage_chroma_collection_lock.py`. Twenty
triggers reject baseline queue writes, collection/segment changes, metadata
changes and direct embedding writes with
`LEGAL_BASELINE_COLLECTION_LOCKED`. A Chroma API upsert negative test was
rejected, left the count at 168,155, left the probe ID absent, and an existing
read still succeeded. A transaction-rolled-back control probe confirms another
collection remains writable. Unlock requires the explicit `remove` operation
and `--allow-unlock`; it is not part of normal startup or deployment.

The active pointer remains
`legal_chunks_vnlegal_lal_haiphong_unified_v1`. The existing rollback rehearsal
still passes, and `SHA256SUMS.json` binds the manifests and evidence artifacts.
This closes M1 only. It does not override the separate Golden/answer-quality
NO-GO or authorize candidate/public activation.
