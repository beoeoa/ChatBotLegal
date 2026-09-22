"""Local Vietnamese OCR shared by uploads; never silently use a cloud model."""
from io import BytesIO
import hashlib
from threading import BoundedSemaphore

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
_slots = BoundedSemaphore(2)


def extract_image(content: bytes) -> dict:
    """Read one Vietnamese document image with the single local OCR engine."""
    from api.crawlers import ocr_extractor as ocr
    from PIL import Image, ImageOps

    ready, reason = ocr._ocr_runtime_readiness()
    if not ready:
        raise ValueError(f'OCR cục bộ chưa sẵn sàng ({reason}). Cần Tesseract và dữ liệu tiếng Việt.')
    if not _slots.acquire(timeout=5):
        raise ValueError('OCR đang bận xử lý tài liệu khác. Vui lòng thử lại sau.')
    try:
        with Image.open(BytesIO(content)) as original:
            if getattr(original, 'n_frames', 1) != 1:
                raise ValueError('Ảnh nhiều trang: hãy chuyển thành PDF để đọc đủ tất cả các trang.')
            if original.width * original.height > 25_000_000:
                raise ValueError('Ảnh vượt 25 triệu điểm ảnh. Vui lòng giảm kích thước ảnh.')
            with ImageOps.exif_transpose(original) as oriented:
                with oriented.convert('RGB') as image:
                    data = ocr.pytesseract.image_to_data(
                        image, lang='vie', config=ocr.TESSERACT_VIE_CONFIG, timeout=45,
                        output_type=ocr.pytesseract.Output.DICT,
                    )
        digest = hashlib.sha256(content).hexdigest()
        blocks, text = ocr._ocr_line_blocks(data, page_number=1, source_asset_sha256=digest)
        if not text.strip():
            raise ValueError('Không nhận diện được chữ trong ảnh. Hãy gửi ảnh tài liệu rõ hơn; OCR không mô tả cảnh vật hoặc xác thực chữ ký.')
        scores = [float(b['confidence']) for b in blocks if b.get('confidence') is not None]
        return dict(text=text, status='ok', extractor_used='tesseract-vie',
                    fallback_reason='', ocr_status='ok',
                    language='vie', page_count=1, complete=True,
                    ocr_confidence=sum(scores)/len(scores) if scores else None,
                    extraction_blocks=blocks, file_fingerprint=digest,
                    text_fingerprint=hashlib.sha256(text.encode()).hexdigest(),
                    layout_status='available', requires_manual_review=True)
    except (OSError, RuntimeError, ocr.pytesseract.TesseractError) as exc:
        raise ValueError('Không thể OCR ảnh: tệp hỏng hoặc nhận dạng quá thời gian. Hãy gửi ảnh rõ hơn.') from exc
    finally:
        _slots.release()
