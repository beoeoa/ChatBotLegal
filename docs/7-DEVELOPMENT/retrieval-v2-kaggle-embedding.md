# Retrieval V2 embedding trên Kaggle

## Mục đích và ranh giới an toàn

Kaggle chỉ là GPU worker tạo vector V2 từ passage đã đóng băng. Kaggle không
kết nối trực tiếp PostgreSQL hoặc Chroma trên máy triển khai, không nhận active
pointer và không có quyền kích hoạt release.

Luồng dữ liệu bắt buộc:

1. Máy local xuất JSONL có checksum từ chunk manifest V2.
2. Hai Kaggle Dataset **private** chứa input và model VNLegal-LAL đã pin.
3. Kaggle Kernel **private**, tắt Internet, tạo các shard NPZ float32.
4. Máy local tải output, xác minh SHA-256, ID, dimension, finite value, norm và
   toàn bộ fingerprint.
5. Chỉ sau khi PASS mới được dùng `--apply` để nhập vào hai collection Kaggle
   staging tách biệt. Script không có chức năng đổi active pointer.

Không đưa `kaggle.json`, `.env`, mật khẩu, PostgreSQL dump hoặc Chroma store lên
Kaggle. Không trộn vector của một job local dở dang với vector Kaggle.

## Trạng thái hiện tại

- Active pointer phải tiếp tục là
  `legal_chunks_vnlegal_lal_haiphong_unified_v1`.
- Stage C chính thức đã PASS với approved manifest V6R1, không dùng lại
  provisional passage-v3. Logical job là
  `retrieval-v2-20260816-v6r1-job1`; input private Dataset là
  `phconc/legal-retrieval-v2-input-v6r1-20260816` và private Kernel là
  `phconc/legal-retrieval-v2-embedding-v6r1-20260816`.
- Input V6R1 có đúng 639.129 chunk trong 26 shard: 391.588
  `current_retrievable` và 247.541 `historical_only`. Manifest input có
  SHA-256 `6c94ebe68dbf43dc44cd0729709f744fab1513458ef87b63cc118939b9707857`;
  toàn bộ 26 shard đã được đối chiếu checksum trước khi upload.
- Kaggle model Dataset tiếp tục dùng
  `phconc/vnlegal-lal-pinned-29020c`. Worker trên Kernel phải tự xác minh model
  fingerprint `29020cd8...19b9aa`, tokenizer fingerprint
  `79e4a228...50a05` và worker fingerprint
  `80d5f776...1ed0` trước khi chạy.
- Sau khi download, ngoài verifier ID/dimension/norm/checksum, bắt buộc chạy
  `verify_retrieval_v2_kaggle_parity.py`: replay 500 passage trên CUDA và so
  tối thiểu 100 passage CPU/GPU. Stage C V6R1 đã đạt replay minimum cosine
  `0.9999951124` và CPU/GPU parity minimum cosine `0.9999966621`.
- Artifact chốt là `reports/retrieval-release-v2/stage-c-final-report-v6r1.json`
  cùng SHA-256 sidecar; structural verification và parity verification đều
  PASS. Không import Chroma và active pointer vẫn giữ baseline M2.
- Chunk manifest passage-v3 hiện là draft chưa có legal-review attestation;
  vì vậy mọi artifact sinh từ nó chỉ là `provisional_staging`, chưa đủ điều kiện
  release.
- Collection local
  `legal_chunks_retrieval_release_v2_current_m512_provisional` hiện mới có
  25.856 vector và không được coi là collection hoàn chỉnh.
- Export provisional đầy đủ đã được tạo ở
  `kaggle/retrieval-v2/input-provisional`: 392.075 chunk duy nhất, 16 shard,
  391.208 current và 867 historical, không có checksum lỗi. Exporter đọc
  streaming nên không nạp manifest 1,25 GB vào RAM.
- Smoke 100 passage bằng đúng worker đã PASS; cosine so với 100 vector local
  tương ứng có min 0,999996 và mean 0,999998. Hai collection rehearsal
  `*_kaggle_smoke_provisional` có đúng 100 vector và active pointer không đổi.
- Snapshot mới sau khi sửa URL đã phân loại đủ 12.236 văn bản: 7.224
  `current_retrievable`, 18 `historical_only`, 3 `future_effective` và 4.991
  `quarantined`. Các trạng thái này không được suy ra chỉ từ HTTP status; mỗi
  bản ghi vẫn cần legal-review attestation. Artifact mới là
  `reports/retrieval-release-v2/source-inventory-reconciliation-v4.json` và
  `source-snapshot-12236-v4.json`; các artifact cũ không bị ghi đè.
- Audit nội dung HTTP mới đã kiểm tra lại cả 12.236 URL (12.236 HTTP 200),
  nhưng static HTML chỉ xác minh được 20, có 21 bản cần review và 12.195 là
  shell/identity chưa xác minh. Nhánh Chromium render động đang tạo evidence
  version riêng; HTTP 200 không được coi là legal approval.

## 1. Cài Kaggle CLI tách biệt

```powershell
py -3.12 -m venv .venv-kaggle-cli
& .\.venv-kaggle-cli\Scripts\python.exe -m pip install -r requirements\kaggle-cli.txt
```

Đăng nhập bằng Kaggle API token của chính tài khoản người vận hành. Lưu token
ngoài repository theo hướng dẫn chính thức của Kaggle; không commit token. Có
thể dùng token mới trong `C:\Users\beoeoa\.kaggle\access_token`, biến môi trường
`KAGGLE_API_TOKEN`, hoặc legacy key trong
`C:\Users\beoeoa\.kaggle\kaggle.json`.

```powershell
& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py preflight
```

Chỉ tiếp tục khi `ready=true`.

## 2. Xuất input từ chunk manifest V2

Smoke test trước:

```powershell
& .\.venv-retrieval-cu126\Scripts\python.exe scripts\export_retrieval_v2_kaggle_shards.py `
  --manifest reports\retrieval-release-v2\legal-retrieval-chunk-manifest-v2-draft-passage-v3.json `
  --output-dir kaggle\retrieval-v2\input-smoke `
  --shard-size 100 `
  --limit 100 `
  --allow-provisional-staging
```

Sau khi legal review phê duyệt manifest, bỏ `--allow-provisional-staging` và
`--limit` để xuất toàn bộ. Không dùng draft provisional làm production release.

```powershell
& .\.venv-retrieval-cu126\Scripts\python.exe scripts\export_retrieval_v2_kaggle_shards.py `
  --manifest <approved-chunk-manifest-v2.json> `
  --output-dir kaggle\retrieval-v2\input-final `
  --shard-size 25000 `
  --embedding-job-id retrieval-v2-<release-id>
```

Export approved không chấp nhận thiếu `embedding_job_id`. Input manifest V2
gắn source snapshot, approved chunk manifest, model/tokenizer/recipe/splitter,
worker SHA-256, kernel bundle version, accelerator và `internet_enabled=false`.
Mỗi shard và `embedding-input-manifest.json` đều có SHA-256. Việc sửa một ký tự
trong passage sau bước này làm verifier fail. Không dùng lại `input-provisional`
sau khi snapshot URL thay đổi.

## 3. Tạo hai Kaggle Dataset private

Thay `OWNER` bằng username Kaggle. Dataset input:

```powershell
& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py dataset-metadata `
  --data-dir kaggle\retrieval-v2\input `
  --dataset-id OWNER/legal-retrieval-v2-input `
  --title "Legal Retrieval V2 input"

& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py publish-dataset `
  --data-dir kaggle\retrieval-v2\input
```

Dataset model phải được đóng gói sao cho `dataset-metadata.json` nằm ngoài thư
mục `model`; nếu đặt metadata vào trong model thì model fingerprint sẽ đổi.

```powershell
& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py stage-model-dataset `
  --model-path release-data\legal\vnlegal-lal-model `
  --bundle-dir kaggle\retrieval-v2\model-dataset `
  --dataset-id OWNER/vnlegal-lal-pinned `
  --title "VNLegal-LAL pinned artifacts"

& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py publish-dataset `
  --data-dir kaggle\retrieval-v2\model-dataset
```

Nếu cập nhật dataset đã tồn tại, dùng `publish-dataset --version --message
"<release-id>"`. Không đổi model/tokenizer sau khi input manifest đã được tạo.

## 4. Chuẩn bị và chạy Kernel private

```powershell
& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py prepare `
  --bundle-dir kaggle\retrieval-v2\kernel-bundle `
  --kernel-id OWNER/legal-retrieval-v2-embedding `
  --input-dataset OWNER/legal-retrieval-v2-input `
  --model-dataset OWNER/vnlegal-lal-pinned

& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py submit `
  --bundle-dir kaggle\retrieval-v2\kernel-bundle `
  --accelerator NvidiaTeslaT4

& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py status `
  --kernel-id OWNER/legal-retrieval-v2-embedding
```

Worker dùng max_length 512, last-token pooling, L2 normalization, output float32
1.024 chiều và tự hạ batch `128 → 96 → 64 → 32 → 16 → 8 → 4 → 1` khi CUDA
OOM. Internet của Kernel bị tắt. Worker ghi checkpoint sau mỗi shard; resume
chỉ được phép khi logical job ID, input checksum, model, worker và toàn bộ
recipe fingerprints trùng tuyệt đối.

## 5. Tải xuống và xác minh

```powershell
& .\.venv-kaggle-cli\Scripts\python.exe scripts\run_retrieval_v2_kaggle.py download `
  --kernel-id OWNER/legal-retrieval-v2-embedding `
  --output-dir kaggle\retrieval-v2\output

& .\.venv-retrieval-cu126\Scripts\python.exe scripts\verify_retrieval_v2_kaggle_output.py `
  --input-manifest kaggle\retrieval-v2\input\embedding-input-manifest.json `
  --output-manifest kaggle\retrieval-v2\output\embedding-output-manifest.json `
  --report reports\retrieval-release-v2\kaggle-vector-verification.json
```

Verifier phải PASS toàn bộ: đúng release/job/fingerprint, đủ ID, không ID
thừa/trùng, dimension 1.024, float32, finite 100%, zero vector bằng 0, norm
trong `[0,999; 1,001]`, vector-content SHA-256 và runtime GPU evidence. Output
V2 fail không được import.

## 6. Dry-run và nhập collection staging

Dry-run không ghi Chroma:

```powershell
& .\.venv-retrieval-cu126\Scripts\python.exe scripts\import_retrieval_v2_kaggle_shadow.py `
  --input-manifest kaggle\retrieval-v2\input\embedding-input-manifest.json `
  --output-manifest kaggle\retrieval-v2\output\embedding-output-manifest.json `
  --report reports\retrieval-release-v2\kaggle-shadow-import-dry-run.json
```

Chỉ thêm `--apply` sau khi verifier PASS và target đã được review. Với draft,
hai target phải chứa cả `kaggle` và `provisional`:

```powershell
& .\.venv-retrieval-cu126\Scripts\python.exe scripts\import_retrieval_v2_kaggle_shadow.py `
  --input-manifest kaggle\retrieval-v2\input\embedding-input-manifest.json `
  --output-manifest kaggle\retrieval-v2\output\embedding-output-manifest.json `
  --current-target legal_chunks_retrieval_v2_current_kaggle_provisional `
  --temporal-target legal_chunks_retrieval_v2_temporal_kaggle_provisional `
  --report reports\retrieval-release-v2\kaggle-shadow-import.json `
  --apply
```

Nếu job import bị ngắt, chạy lại với `--apply --resume`; collection metadata phải
khớp tuyệt đối. Sau import phải chứng minh count/ID đúng, current là tập con của
temporal và active pointer trước/sau không đổi.

## 7. Gate sau embedding

Kaggle hoàn thành chỉ đóng phần tạo vector. Release vẫn chưa được kích hoạt cho
đến khi vector replay/parity, exact/lexical index cùng release, M5, M6, Golden
2.000, citation/temporal safety và rollback rehearsal đều PASS.

Tham khảo cú pháp CLI: [Kaggle datasets commands](https://github.com/Kaggle/kaggle-cli/blob/main/docs/datasets.md) và [Kaggle kernels commands](https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md).

## 8. Stage A V6 và chunk coverage chốt ngày 16/08/2026

Stage A được đóng bằng `stage-a-final-report-v6.json`, gắn attestation của chủ
dự án và không sửa PostgreSQL, Chroma hoặc active pointer. Inventory V6 gồm
12.236 văn bản: 7.192 `current_retrievable`, 4.994 `historical_only`, 3
`future_effective` và 47 `quarantined` do toàn bộ article/chunk nguồn đang
rỗng. Như vậy tập đủ điều kiện chunk/embedding là 12.186 văn bản.

Lần build coverage đã tạo 639.129 chunk hợp lệ từ 121.826 article của đúng
12.186 văn bản; token lớn nhất là 512 và không có chunk rỗng/invalid. Draft V5
ban đầu ghi header theo 12.233 văn bản trước khi phát hiện 47 content gap nên
đã bị thay thế.

Stage B được đóng bằng approved manifest V6R1 và
`stage-b-final-report-v6r1.json`: structural gate và release gate đều PASS,
attestation có đủ 12.236 review row, checksum khớp và active pointer M2 không
đổi. Trường `official_source_sha256` trong attestation V6R1 là checksum gắn
source-snapshot record bất biến (ID, số hiệu, URL và snapshot), không được diễn
giải là checksum của HTML tĩnh hoặc toàn văn đã render. Chỉ approved manifest
V6R1 được phép dùng để xuất input Kaggle của Giai đoạn C.
