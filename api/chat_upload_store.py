"""Owner-scoped original uploads kept alongside persisted chat context."""
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from api.auth import get_request_user_id
from api.data_paths import notebook_data_dir
from api.document_contracts import DocumentAsset

router = APIRouter()
_UPLOAD_METADATA_LOCK = threading.RLock()


def owner_directory(owner: str) -> Path:
    if not owner:
        raise HTTPException(401, 'Cần đăng nhập để truy cập tệp.')
    return notebook_data_dir() / 'chat_uploads' / hashlib.sha256(owner.encode()).hexdigest()


def _metadata_path(directory: Path, identity: str) -> Path:
    return directory / (identity + '.json')


def find_upload_by_sha256(owner: str, sha256: str) -> tuple[str, dict] | None:
    """Return an owner-scoped existing asset without exposing other owners."""
    if not re.fullmatch(r'[a-f0-9]{64}', str(sha256 or '').casefold()):
        return None
    directory = owner_directory(owner)
    if not directory.is_dir():
        return None
    for metadata_path in directory.glob('*.json'):
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        identity = metadata_path.stem
        if (
            str(metadata.get('sha256') or '').casefold() == sha256.casefold()
            and (directory / f'{identity}.bin').is_file()
        ):
            return identity, metadata
    return None


def store_upload(
    owner: str,
    name: str,
    mime: str,
    content: bytes,
    *,
    asset: DocumentAsset | None = None,
) -> str:
    with _UPLOAD_METADATA_LOCK:
        directory = owner_directory(owner)
        directory.mkdir(parents=True, exist_ok=True)
        asset = asset or DocumentAsset.from_bytes(
            content, filename=name, mime_type=mime, owner_id=owner
        )
        if asset.owner_id not in {None, owner} or asset.sha256 != hashlib.sha256(content).hexdigest():
            raise ValueError('upload_asset_mismatch')
        existing = find_upload_by_sha256(owner, asset.sha256)
        if existing is not None:
            return existing[0]
        identity = asset.asset_id
        target = directory / (identity + '.bin')
        target.write_bytes(content)
        try:
            _metadata_path(directory, identity).write_text(json.dumps({
                'name': asset.filename, 'mime':asset.mime_type, 'size':asset.size_bytes,
                'sha256':asset.sha256, 'asset_id': asset.asset_id,
                'created':asset.stored_at.isoformat(),
            }),encoding='utf-8')
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return identity


def update_upload_metadata(owner: str, identity: str, values: dict) -> dict:
    """Atomically add extraction state to one validated owner asset."""
    with _UPLOAD_METADATA_LOCK:
        target, metadata = stored_upload(owner, identity)
        _ = target
        allowed = {
            'extraction_job_id', 'extraction_status', 'extractor',
            'extractor_version', 'warnings', 'char_count',
        }
        metadata.update({key: value for key, value in values.items() if key in allowed})
        path = _metadata_path(owner_directory(owner), identity)
        # A fixed ``.tmp`` path allowed concurrent same-SHA uploads to replace
        # each other's temporary file on Windows. A unique sibling plus the
        # process lock preserves atomic replacement without crashing the API.
        temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return metadata


def stored_upload(owner: str, identity: str):
    if not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise HTTPException(404, 'Không tìm thấy tệp.')
    directory = owner_directory(owner)
    target = directory / (identity + '.bin')
    metadata = directory / (identity + '.json')
    if not target.is_file() or not metadata.is_file():
        raise HTTPException(404, 'Không tìm thấy tệp.')
    return target, json.loads(metadata.read_text(encoding='utf-8'))


@router.get('/media/files/{identity}')
async def download(identity: str, request: Request):
    target, metadata = stored_upload(str(get_request_user_id(request) or ''),identity)
    # Preserve the real MIME type so embedded PDF/image previews can render.
    # ``attachment``/octet-stream made Chromium's embedded PDF surface blank.
    media_type = str(metadata.get('mime') or 'application/octet-stream')
    return FileResponse(target,filename=metadata['name'],media_type=media_type,
                        content_disposition_type='inline',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/media/files/{identity}/preview')
async def preview(identity: str, request: Request, page: int = 1):
    """Render a reliable first-page preview for PDF uploads.

    The browser's built-in PDF plugin is unavailable in some embedded
    Chromium surfaces.  A small raster preview keeps review usable while the
    original PDF remains available through the inline/download endpoint.
    """
    target, metadata = stored_upload(str(get_request_user_id(request) or ''), identity)
    if str(metadata.get('mime') or '').casefold() != 'application/pdf' and target.suffix.casefold() != '.pdf':
        raise HTTPException(415, 'Chỉ hỗ trợ xem trước PDF.')
    try:
        import fitz
        document = fitz.open(target)
        try:
            if page < 1 or page > document.page_count:
                raise HTTPException(404, 'Không tìm thấy trang PDF.')
            pixmap = document.load_page(page - 1).get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            payload = pixmap.tobytes('png')
        finally:
            document.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, 'Không thể dựng ảnh xem trước PDF.') from exc
    return Response(content=payload, media_type='image/png', headers={
        'Cache-Control': 'private, no-store',
        'X-Preview-Page': str(page),
    })


@router.delete('/media/files/{identity}', status_code=204)
async def delete(identity: str, request: Request):
    target, _ = stored_upload(str(get_request_user_id(request) or ''),identity)
    target.unlink()
    target.with_suffix('.json').unlink(missing_ok=True)
