from __future__ import annotations

import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAG_ANYTHING_ROOT = PROJECT_ROOT / "external" / "RAG-Anything"


class RagAnythingUnavailable(RuntimeError):
    pass


def _flatten_block(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "").lower()
    parts: list[str] = []
    page_idx = block.get("page_idx")
    prefix = f"[Page {page_idx + 1}] " if isinstance(page_idx, int) else ""

    if block_type == "text":
        text = str(block.get("text") or "").strip()
        if text:
            parts.append(f"{prefix}{text}")
    elif block_type == "table":
        caption = " ".join(block.get("table_caption") or []).strip()
        body = str(block.get("table_body") or "").strip()
        footnote = " ".join(block.get("table_footnote") or []).strip()
        if caption:
            parts.append(f"{prefix}Bang: {caption}")
        if body:
            parts.append(body)
        if footnote:
            parts.append(f"Ghi chu bang: {footnote}")
    elif block_type == "equation":
        latex = str(block.get("latex") or "").strip()
        text = str(block.get("text") or "").strip()
        if latex:
            parts.append(f"{prefix}Cong thuc: {latex}")
        if text:
            parts.append(text)
    elif block_type == "image":
        caption = " ".join(block.get("image_caption") or block.get("img_caption") or []).strip()
        footnote = " ".join(block.get("image_footnote") or block.get("img_footnote") or []).strip()
        if caption:
            parts.append(f"{prefix}Hinh: {caption}")
        if footnote:
            parts.append(f"Mo ta hinh: {footnote}")
    else:
        text = str(block.get("text") or "").strip()
        if text:
            parts.append(f"{prefix}{text}")
    return "\n".join(part for part in parts if part).strip()


def flatten_content_list(content_list: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        block_text = _flatten_block(item)
        if block_text:
            chunks.append(block_text)
    return "\n\n".join(chunks).strip()


@lru_cache(maxsize=1)
def _load_mineru_parser_class():
    if not RAG_ANYTHING_ROOT.exists():
        raise RagAnythingUnavailable(
            "Khong tim thay repo external/RAG-Anything de dung parser nang cao."
        )
    root_str = str(RAG_ANYTHING_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        from raganything.parser import MineruParser  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise RagAnythingUnavailable(
            "Khong import duoc RAG-Anything. Hay cai dependency cua repo nay truoc."
        ) from exc
    return MineruParser


def extract_with_rag_anything(filename: str, content: bytes) -> tuple[str, dict[str, Any]]:
    mineru_parser_class = _load_mineru_parser_class()
    parser = mineru_parser_class()
    if not parser.check_installation():
        raise RagAnythingUnavailable(
            "RAG-Anything co mat nhung MinerU/LibreOffice chua san sang tren may."
        )

    suffix = Path(filename or "uploaded").suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="legal-rag-anything-") as tmp_dir:
        tmp_path = Path(tmp_dir) / f"source{suffix}"
        tmp_path.write_bytes(content)
        content_list = parser.parse_document(str(tmp_path), output_dir=tmp_dir, lang="vi")
        flattened = flatten_content_list(content_list)
        if not flattened:
            raise RagAnythingUnavailable(
                "RAG-Anything da chay nhung khong trich xuat duoc noi dung van ban."
            )
        return flattened, {
            "extractor_used": "rag_anything",
            "content_blocks": len(content_list),
            "repo_path": str(RAG_ANYTHING_ROOT),
        }
