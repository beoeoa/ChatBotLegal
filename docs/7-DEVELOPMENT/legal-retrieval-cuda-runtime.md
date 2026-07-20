# Runtime CUDA riêng cho legal retrieval

## Mục tiêu và ranh giới

Retrieval tiếp tục dùng VNLegal-LAL, Chroma và corpus hiện có. CUDA chỉ thay đổi
Python runtime thực thi embedding; quy trình này không ghi vào model, vector,
corpus, PostgreSQL hoặc SurrealDB. Môi trường mặc định nằm tại
`.venv-retrieval-cu126` và không thay thế Python toàn máy.

Runtime được coi là đủ điều kiện chỉ khi cùng một lần xác minh chứng minh được:

- Python 3.12 x64 và `torch==2.8.0+cu126`;
- CUDA khả dụng, VNLegal-LAL nạp cục bộ và prewarm bằng `float16`;
- cùng VNLegal-LAL nạp lại trên CPU và prewarm bằng `float32`;
- dependency manifest, model và corpus có fingerprint; corpus fingerprint chỉ dùng
  tên collection, số record và revision metadata của Chroma, không hash hay xuất
  nội dung luật;
- không có câu hỏi, câu trả lời, credential hoặc nội dung hồ sơ trong kết quả.

Nếu CUDA hết bộ nhớ hoặc bất kỳ bước nào thất bại, script trả mã lý do có cấu
trúc, không tạo marker hợp lệ và không tác động dịch vụ CPU đang chạy.

## Thiết lập và xác minh

Chạy từ thư mục gốc dự án:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  scripts/setup_legal_retrieval_cuda.ps1
```

Có thể chỉ định Python 3.12 x64 hoặc đích venv riêng:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  scripts/setup_legal_retrieval_cuda.ps1 `
  -PythonExecutable "C:\Path\Python312\python.exe" `
  -VenvPath ".venv-retrieval-cu126"
```

Lệnh có tính lặp lại an toàn: venv đã có sẽ được dùng lại, dependency pin được
đối chiếu/cài lại khi cần và marker cũ bị vô hiệu trước khi smoke test. Chế độ
chỉ xác minh không cài package:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  scripts/setup_legal_retrieval_cuda.ps1 -VerifyOnly
```

Marker `.legal-retrieval-cu126-verified.json` chỉ được ghi sau khi cả CUDA
`float16` và CPU `float32` prewarm thành công. Marker và stdout chỉ chứa phiên
bản, loại thiết bị, tên GPU tổng quát, `corpus_fingerprint`, fingerprint/hash và thời điểm UTC; không
chứa đường dẫn interpreter hoặc dữ liệu pháp lý.

## Chọn runtime và khởi động

Kiểm tra runtime sẽ được chọn mà không khởi động process:

```powershell
$env:LEGAL_EMBED_DEVICE = "cuda"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  scripts/start_legal_search.ps1 -PreflightOnly
```

Thứ tự lựa chọn là:

1. `LEGAL_SEARCH_PYTHON` do operator đặt và qua preflight;
2. `.venv-retrieval-cu126` có marker hợp lệ, dependency hash khớp và qua preflight;
3. `.venv` hiện có của dự án;
4. Python CPU hiện có.

`LEGAL_EMBED_DEVICE=cuda` là chế độ nghiêm ngặt: không có runtime CUDA đủ điều
kiện thì launcher dừng. `auto` có thể dùng CPU và health phải công bố mã fallback.
`LEGAL_EMBED_DEVICE=cpu` luôn dùng `float32`. Nếu dịch vụ đang chạy trên thiết bị khác yêu cầu,
launcher dừng để operator chủ động dừng riêng retrieval trước khi đổi runtime.

Sau khi khởi động, `http://127.0.0.1:8765/health` phải báo `ready=true`,
`embedding_device=cuda`, `embedding_dtype=float16` cho pilot CUDA. Health không
được lộ đường dẫn Python, database URL, cache key hoặc nội dung pháp lý.

## Rollback về CPU

Rollback không xóa venv CUDA, model, vector hoặc corpus:

```powershell
# Dừng riêng process retrieval đang chạy trước khi đổi thiết bị.
$env:LEGAL_SEARCH_PYTHON = "C:\Path\known-good-python312\python.exe"
$env:LEGAL_EMBED_DEVICE = "cpu"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  scripts/start_legal_search.ps1
```

Health sau rollback phải báo `embedding_device=cpu`,
`embedding_dtype=float32`, cùng `model_fingerprint` và cùng collection. Nếu
rollback không prewarm được, giữ rollout ở stage 0 và không chạy benchmark phát
hành.

## Tương thích database cho pilot sạch

Image phát hành được kiểm tra với SurrealDB 2.x trên database pilot mới. Migration
khởi tạo dùng `SEARCH ANALYZER` cho các full-text index vì đây là cú pháp 2.x;
không thay migration state hay dữ liệu của database đang chạy. Cú pháp
`FULLTEXT ANALYZER` chỉ dùng cho SurrealDB 3.x, nên nếu quay lại 3.x cần migration
mới có phê duyệt thay vì sửa corpus hoặc database production.

## Bằng chứng và giới hạn phát hành

T018 chỉ chứng minh runtime cục bộ. Kết quả này không thay thế T019–T022:
benchmark cold/warm và concurrency, 30 ca chuyên gia duyệt, quality gate,
PostgreSQL rehearsal, privacy scan và release-owner approval vẫn phải hoàn tất
riêng. Không dùng marker runtime để tự động mở feature flag hoặc rollout.
