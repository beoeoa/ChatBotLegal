# Bước 11 - Voice input cho Search/Ask

## Quyết định triển khai

Voice input được triển khai giống upload file ở Bước 10: tách thành 2 giai đoạn để không phá pipeline hỏi đáp/RAG hiện có.

1. Frontend ghi âm từ microphone bằng `MediaRecorder`.
2. Frontend gửi audio tới `POST /api/media/transcribe-voice`.
3. Backend chỉ chuyển speech-to-text thành transcript.
4. Frontend chèn transcript vào ô hỏi.
5. Người dùng bấm hỏi và transcript đi qua ask pipeline hiện có: role, domain, retrieval, grounding, citation verifier.

## Ranh giới chức năng

- Chỉ xử lý âm thanh cho mục đích chuyển thành text.
- Không xử lý video.
- Không dùng voice để bỏ qua luồng ask hiện có.
- Nếu speech-to-text model chưa cấu hình, backend trả lỗi rõ `503` để UI hiển thị cho người dùng.

## Endpoint

`POST /api/media/transcribe-voice`

Input: multipart file audio.

Hỗ trợ MIME:

- `audio/webm`
- `audio/wav`, `audio/x-wav`
- `audio/mpeg`, `audio/mp3`
- `audio/ogg`
- `audio/mp4`, `audio/m4a`
- `audio/aac`
- `audio/flac`

Không hỗ trợ:

- mọi MIME `video/*`.

## Giới hạn an toàn

- Dung lượng tối đa: 15 MB.
- STT dùng model mặc định `default_speech_to_text_model` trong cấu hình model hiện có.
- Lỗi cấu hình STT được trả rõ để admin biết cần vào Cài đặt → API Keys/Mô hình AI.
