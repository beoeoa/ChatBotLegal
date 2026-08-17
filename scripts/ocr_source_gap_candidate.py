"""Resume-safe OCR and queue synchronization for one official source-gap PDF."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import fitz  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.crawlers.ocr_extractor import (
    extract_ocr_from_pdf_bytes,
    merge_ocr_batches,
)
from api.source_gap_jobs import (
    DEFAULT_CANDIDATE_DIR,
    DEFAULT_STORE_PATH,
    _save_source_gap_jobs,
    load_source_gap_jobs,
    sync_downloaded_candidate_to_review_queue,
)

def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _candidate_path(job: dict[str, Any], candidate_dir: Path) -> Path:
    candidate = job.get("candidate") or {}
    raw_path = Path(str(candidate.get("local_path") or ""))
    path = raw_path if raw_path.is_absolute() else ROOT / raw_path
    resolved = path.resolve()
    allowed = candidate_dir.resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError("candidate_path_outside_source_gap_area")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


async def process_job(
    *,
    job_id: str,
    store_path: Path,
    candidate_dir: Path,
    output_dir: Path,
    batch_pages: int,
    dpi: int,
) -> dict[str, Any]:
    jobs = load_source_gap_jobs(store_path)
    job_index = next(
        (index for index, item in enumerate(jobs) if item.get("job_id") == job_id),
        None,
    )
    if job_index is None:
        raise ValueError("source_gap_job_not_found")
    job = jobs[job_index]
    if job.get("status") != "downloaded_candidate":
        raise ValueError("downloaded_candidate_required")
    if not isinstance(job.get("official_metadata"), dict):
        raise ValueError("official_metadata_required")

    pdf_path = _candidate_path(job, candidate_dir)
    pdf_bytes = pdf_path.read_bytes()
    checksum = _sha256_bytes(pdf_bytes)
    expected_checksum = str((job.get("candidate") or {}).get("sha256") or "").casefold()
    if checksum != expected_checksum:
        raise ValueError("candidate_checksum_drift")
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = int(document.page_count)
    document.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / f"{checksum}.ocr-checkpoint.json"
    final_text_path = output_dir / f"{checksum}.ocr.txt"
    if checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("sha256") != checksum
            or int(checkpoint.get("total_pages") or 0) != total_pages
        ):
            raise ValueError("ocr_checkpoint_identity_mismatch")
    else:
        checkpoint = {
            "schema_version": "source-gap-ocr-checkpoint-v1",
            "job_id": job_id,
            "sha256": checksum,
            "total_pages": total_pages,
            "next_page": 0,
            "parts": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    next_page = int(checkpoint.get("next_page") or 0)
    while next_page < total_pages:
        page_count = min(batch_pages, total_pages - next_page)
        result = extract_ocr_from_pdf_bytes(
            pdf_bytes,
            max_pages=page_count,
            resume_from_page=next_page,
            dpi=dpi,
        )
        part_path = output_dir / f"{checksum}.part-{next_page + 1:04d}.txt"
        _atomic_write_text(part_path, str(result.get("text") or ""))
        checkpoint["parts"].append({
            "start_page": next_page,
            "processed_pages": int(result.get("processed_pages") or 0),
            "failed_pages": list(result.get("failed_pages") or []),
            "ocr_confidence": result.get("ocr_confidence"),
            "path": str(part_path),
        })
        next_page += int(result.get("processed_pages") or 0)
        if next_page <= int(checkpoint.get("next_page") or 0):
            raise RuntimeError("ocr_made_no_progress")
        checkpoint["next_page"] = next_page
        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(checkpoint_path, checkpoint)

    batches = []
    for part in checkpoint.get("parts") or []:
        part_path = Path(str(part["path"]))
        batches.append({
            **part,
            "text": part_path.read_text(encoding="utf-8"),
        })
    merged = merge_ocr_batches(batches, total_pages=total_pages)
    merged_text = str(merged.pop("text") or "")
    _atomic_write_text(final_text_path, merged_text)
    merged.update({
        "preview": merged_text[:2000],
        "text_fingerprint": _sha256_bytes(merged_text.encode("utf-8")),
        "file_fingerprint": checksum,
        "filename": pdf_path.name,
        "extractor_used": "pymupdf+tesseract-vie-eng",
        "text_path": str(final_text_path),
    })
    job["extraction_result"] = merged
    job["extracted_text_path"] = str(final_text_path)
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    jobs[job_index] = job
    _save_source_gap_jobs(jobs, store_path=store_path)
    sync_result = await sync_downloaded_candidate_to_review_queue(
        job,
        extracted_text=merged_text,
    )
    return {
        "job_id": job_id,
        "status": merged["status"],
        "processed_pages": merged["processed_pages"],
        "total_pages": total_pages,
        "failed_page_count": len(merged["failed_pages"]),
        "complete": merged["complete"],
        "ocr_confidence": merged["ocr_confidence"],
        "file_sha256": checksum,
        "text_sha256": merged["text_fingerprint"],
        "queue_action": sync_result["action"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--store-path", type=Path, default=DEFAULT_STORE_PATH)
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_CANDIDATE_DIR / "ocr",
    )
    parser.add_argument("--batch-pages", type=int, default=5)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    if args.batch_pages < 1:
        raise ValueError("batch_pages_must_be_positive")
    result = asyncio.run(process_job(
        job_id=args.job_id,
        store_path=args.store_path,
        candidate_dir=args.candidate_dir,
        output_dir=args.output_dir,
        batch_pages=args.batch_pages,
        dpi=args.dpi,
    ))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
