"""Local VNLegal-LAL retrieval service for the Hai Phong legal assistant."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from threading import Event, Lock
from time import perf_counter
from typing import Any

import chromadb
import numpy as np
import torch
import torch.nn.functional as F
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, create_engine, text
from transformers import AutoModel, AutoTokenizer

try:
    from scripts.legal_retrieval_cache import QueryVectorCache, query_vector_cache_key
except ModuleNotFoundError:  # Support ``python scripts/legal_search_server.py``.
    from legal_retrieval_cache import QueryVectorCache, query_vector_cache_key

try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover - optional dependency may be unavailable
    BM25Okapi = None


DATA_ROOT = Path(os.getenv("LEGAL_DATA_ROOT", r"D:\legal-chatbot-data"))
CHROMA_PATH = Path(os.getenv("LEGAL_CHROMA_PATH", str(DATA_ROOT / "chroma_store")))
CHROMA_COLLECTION = os.getenv(
    "LEGAL_CHROMA_COLLECTION",
    "legal_chunks_vnlegal_lal_haiphong",
)
CHROMA_SOURCE_COLLECTION = os.getenv(
    "LEGAL_CHROMA_SOURCE_COLLECTION",
    "legal_chunks_vnlegal_lal",
)
MODEL_PATH = Path(
    os.getenv(
        "VNLEGAL_LAL_MODEL_PATH",
        str(
            DATA_ROOT
            / "sentence_transformers"
            / "models--darklethelong--vnlegal-lal"
            / "snapshots"
            / "de759324ef931a2475ae8db97137b6a6cbb98aa0"
        ),
    )
)
OLD_ENV_PATH = Path(
    os.getenv(
        "LEGAL_OLD_ENV_PATH",
        r"J:\ChatBot\legal-chatbot\backend\.env",
    )
)
QUERY_PREFIX = (
    "Instruct: Given a Vietnamese legal question, retrieve relevant legal "
    "passages that answer the question\nQuery: "
)

# Noto Sans is distributed with this repository under the SIL Open Font License
# 1.1; see assets/fonts/noto-sans/OFL.txt. It contains Vietnamese glyphs and is
# embedded explicitly into every internally-generated PDF.
REPO_ROOT = Path(__file__).resolve().parents[1]
LEGAL_PDF_FONT_PATH = REPO_ROOT / "assets" / "fonts" / "noto-sans" / "NotoSans-VF.ttf"
LEGAL_PDF_ARTIFACT_DIR = Path(os.getenv("LEGAL_PDF_ARTIFACT_DIR", str(DATA_ROOT / "pdf_artifacts")))
LEGAL_PDF_CACHE_MAX_AGE_SECONDS = int(os.getenv("LEGAL_PDF_CACHE_MAX_AGE_SECONDS", "86400"))
MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00c6", "\u00c4", "\u00e1\u00ba", "\u00e1\u00bb")


def _rewrite_query(query: str) -> str:
    normalized = " ".join(_normalized_terms(query))
    if (
        any(marker in normalized for marker in ("giay tay", "so do", "quyen su dung dat"))
        and any(marker in normalized for marker in ("nguoi ban da mat", "thua ke", "quy hoach", "tranh chap"))
    ):
        return (
            query
            + " dat dai cap giay chung nhan quyen su dung dat chuyen nhuong "
            "giay tay thua ke quy hoach su dung dat"
        )
    if "dang ky khai tu" in normalized or normalized == "khai tu":
        return query + " ho tich tu phap ubnd cap xa khai tu"
    if "dang ky khai sinh" in normalized or normalized == "khai sinh":
        return query + " ho tich tu phap ubnd cap xa khai sinh"
    if (
        "xac nhan tinh trang hon nhan" in normalized
        or "giay xac nhan tinh trang hon nhan" in normalized
        or normalized == "tinh trang hon nhan"
    ):
        return query + " ho tich giay xac nhan tinh trang hon nhan ubnd cap xa"
    return query
LEXICAL_STOPWORDS = {
    "ai",
    "ban",
    "can",
    "cho",
    "co",
    "duoc",
    "hai",
    "khong",
    "la",
    "nao",
    "o",
    "phong",
    "phuong",
    "tai",
    "theo",
    "thi",
    "tu",
    "ubnd",
    "va",
    "xa",
}
LEXICAL_MATCH_LIMIT = 120
RERANK_WINDOW = 32
QUERY_VECTOR_CACHE_TTL_SECONDS = float(
    os.getenv("LEGAL_RETRIEVAL_CACHE_TTL_SECONDS", "300")
)
QUERY_VECTOR_CACHE_MAX_ENTRIES = int(
    os.getenv("LEGAL_RETRIEVAL_CACHE_MAX_ENTRIES", "1024")
)
EXPIRED_DOCUMENT_OVERRIDES = {
    # Corpus metadata incorrectly marks these superseded instruments active.
    "4/CP",
    "05/2012/NQ-HĐTP",
    "45/2013/QH13",
}


def _database_url() -> str:
    configured = os.getenv("LEGAL_DATABASE_URL", "").strip()
    if configured:
        return configured
    old_settings = dotenv_values(OLD_ENV_PATH)
    old_url = str(old_settings.get("DATABASE_URL") or "").strip()
    if old_url:
        return old_url
    return "postgresql+psycopg2://postgres:postgres@localhost:5432/legal_chatbot"


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=8, ge=1, le=30)
    candidate_count: int = Field(default=120, ge=20, le=500)
    as_of: date = Field(default_factory=date.today)
    domain: str | None = Field(default=None, max_length=80)
    # Client-side scope preference used for tracing/routing. The legal domain
    # filter remains authoritative; accepting this field keeps older/newer
    # clients compatible instead of failing validation with HTTP 422.
    scope_filter: str | None = Field(default=None, max_length=32)
    # ``core`` is the reviewed Hai Phong/commune index. ``expanded`` queries
    # the active source corpus only when the caller has insufficient evidence
    # from the core index. This is routing metadata, not a client safety bypass.
    retrieval_tier: str = Field(default="core", pattern="^(core|expanded)$")
    include_trace: bool = False
    # Feature 005 request-scoped provenance. These opaque identifiers are
    # returned with each candidate so the Ask service can reject evidence from
    # another request or another issue before generation. They intentionally
    # accept no free-form text, which keeps them safe for metrics and traces.
    request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    issue_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    issue_domain: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9_]+$",
    )


def bind_request_provenance(
    candidate: dict[str, Any], request: SearchRequest
) -> dict[str, Any]:
    """Return a copy of one candidate bound to the current Ask issue.

    Retrieval does not persist these identifiers and never derives them from
    question text.  The receiving API uses them as a strict equality boundary
    before allowing a candidate to support an answer section.
    """

    bound = dict(candidate)
    if request.request_id:
        bound["request_id"] = request.request_id
    if request.issue_id:
        bound["issue_id"] = request.issue_id
    issue_domain = request.issue_domain or request.domain
    if issue_domain:
        bound["issue_domain"] = issue_domain
    return bound


# The UI routes a resident question into five broad commune domains, while the
# reviewed legal scope stores a few of those domains at a more useful level of
# detail.  Resolve the UI labels here rather than weakening the domain filter.
DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    # The reviewed source scope and the older core index use different slugs
    # for the same UI domains.  Keep both generations eligible; otherwise a
    # selected domain can accidentally hide its own framework law.
    "ho_tich_chung_thuc": (
        "ho_tich_chung_thuc",
        "tu_phap_ho_tich",
    ),
    "dat_dai_xay_dung": (
        "dat_dai_xay_dung",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
    ),
    "an_sinh_y_te_giao_duc": ("an_sinh_y_te", "giao_duc_van_hoa"),
    "cu_tru_an_ninh": ("cu_tru_an_ninh",),
}


def _model_revision_fingerprint(model_path: Path) -> str:
    """Return a stable model revision without hashing the 1+ GB weight file."""

    parts = [str(model_path.resolve())]
    for filename in ("config.json", "model.safetensors", "tokenizer.json"):
        path = model_path / filename
        try:
            stat = path.stat()
            parts.append(f"{filename}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{filename}:missing")
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _domain_values(domain: str | None) -> tuple[str, ...]:
    """Return reviewed scope slugs covered by an API/UI domain value."""
    if not domain:
        return ()
    return DOMAIN_ALIASES.get(domain, (domain,))


def _domain_matches(selected_domain: str | None, row_domain: str | None) -> bool:
    """Keep a strict domain boundary while supporting broad UI labels."""
    if not selected_domain:
        return True
    return str(row_domain or "") in _domain_values(selected_domain)


class LegalImportRequest(BaseModel):
    title: str = Field(min_length=5, max_length=1000)
    law_number: str = Field(min_length=2, max_length=255)
    document_type: str = Field(min_length=2, max_length=255)
    issuing_agency: str = Field(min_length=2, max_length=1000)
    scope: str = Field(min_length=2, max_length=255)
    sector: str = Field(default="", max_length=255)
    field_id: int = Field(ge=1)
    issued_date: date | None = None
    effective_date: date
    expired_date: date | None = None
    source_url: str = Field(default="", max_length=4000)
    applicability_info: str = Field(default="", max_length=4000)
    content: str = Field(min_length=20)
    confirmed_official_source: bool = False
    # structured | unstructured | auto
    structure: str = Field(default="auto", max_length=32)


ARTICLE_PATTERN = re.compile(
    r"(?im)^[ \t]*Điều[ \t]+(?P<number>\d+[a-zA-Z]?)"
    r"(?:[ \t]*[.:])?[ \t]*(?P<title>[^\r\n]*)"
)
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
SPLIT_THRESHOLD = 900


def _parse_articles(content: str) -> list[dict[str, str]]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    matches = list(ARTICLE_PATTERN.finditer(normalized))
    articles: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        body = normalized[match.end():end].strip()
        number = match.group("number").strip()
        short_title = match.group("title").strip()
        # Some short decisions and notices put the entire operative text on
        # the same ``Điều N.`` line. Preserve it as content instead of wrongly
        # classifying the document as unstructured merely because no later line
        # follows the heading.
        if not body and not short_title:
            continue
        articles.append(
            {
                "article_number": number,
                "title": f"Điều {number}. {short_title}".strip(),
                "content": body or short_title,
            }
        )
    return articles


def _recursive_split(value: str) -> list[str]:
    if len(value) <= CHUNK_SIZE:
        return [value.strip()]
    chunks: list[str] = []
    start = 0
    while start < len(value):
        hard_end = min(start + CHUNK_SIZE, len(value))
        end = hard_end
        if hard_end < len(value):
            candidates = [
                value.rfind("\n", start, hard_end),
                value.rfind(". ", start, hard_end),
                value.rfind("; ", start, hard_end),
                value.rfind(" ", start, hard_end),
            ]
            split_at = max(candidates)
            if split_at > start + CHUNK_SIZE // 2:
                end = split_at + 1
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _split_article(article: dict[str, str]) -> list[dict[str, Any]]:
    content = article["content"].strip()
    parts = [content] if len(content) <= SPLIT_THRESHOLD else _recursive_split(content)
    heading = article["title"]
    return [
        {"chunk_index": index, "heading": heading, "content": part}
        for index, part in enumerate(parts)
        if part
    ]


def _detect_structure(content: str, preferred: str = "auto") -> str:
    preferred = (preferred or "auto").strip().lower()
    if preferred in {"structured", "unstructured"}:
        return preferred
    return "structured" if _parse_articles(content) else "unstructured"


def _split_unstructured_content(content: str, title: str = "") -> list[dict[str, Any]]:
    """Chunk non-article documents by paragraph/section, not by 'Dieu N.'."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    # Prefer double-newline paragraphs; fallback to single lines when needed.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [p.strip() for p in normalized.split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    chunks: list[dict[str, Any]] = []
    for para in paragraphs:
        parts = [para] if len(para) <= SPLIT_THRESHOLD else _recursive_split(para)
        for part in parts:
            if not part:
                continue
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "heading": (title or "Van ban khong co Dieu").strip()[:255],
                    "content": part,
                }
            )
    return chunks


def _build_import_units(request: LegalImportRequest) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    """Return structure, article-like units, and chunk records for import."""
    structure = _detect_structure(request.content, request.structure)
    if structure == "structured":
        articles = _parse_articles(request.content)
        chunks = [chunk for article in articles for chunk in _split_article(article)]
        return structure, articles, chunks

    # Unstructured path: synthesize one pseudo-article so existing DB shape remains usable.
    units = [
        {
            "article_number": "0",
            "title": f"{request.title.strip()} (unstructured)"[:500],
            "content": request.content.strip(),
        }
    ]
    chunks = _split_unstructured_content(request.content, title=request.title)
    return structure, units, chunks



def _contains_probable_mojibake(value: str) -> bool:
    # ``Â`` and ``Ã`` are valid standalone Vietnamese letters (for example
    # "DÂN", "XÃ"). Treat only encoding-only sequences/control characters as
    # corruption after the repair pass, otherwise valid legal text is blocked
    # from PDF export.
    strong_markers = ("\u00c6", "\u00c4", "\u00e1\u00ba", "\u00e1\u00bb", "\ufffd")
    suspicious_pair = re.search(r"[\u00c3\u00c2][\u0080-\u00bf\u00e0-\u00ff]", value)
    return bool(suspicious_pair) or any(marker in value for marker in strong_markers) or any(
        "\u0080" <= char <= "\u009f" for char in value
    )


def _repair_mojibake_text(value: str) -> str:
    """Repair legacy display text at the retrieval boundary without rewriting DB."""
    current = value or ""
    for _ in range(2):
        current_score = sum(current.count(marker) for marker in MOJIBAKE_MARKERS)
        if current_score == 0:
            break
        best, best_score = current, current_score
        for encoding in ("latin-1", "cp1252"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            score = sum(candidate.count(marker) for marker in MOJIBAKE_MARKERS)
            if score < best_score:
                best, best_score = candidate, score
        if best == current:
            break
        current = best
    return current


def _repair_display_row(row: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(row)
    for key in (
        "law_number", "document_title", "document_type", "issuing_agency",
        "scope", "sector", "field_name", "domain_name", "article_title",
        "chunk_heading", "content",
    ):
        if isinstance(repaired.get(key), str):
            repaired[key] = _repair_mojibake_text(repaired[key])
    return repaired


def _assert_exportable_utf8(value: str, field_name: str) -> None:
    """Prevent corrupted Vietnamese strings from being silently exported to PDF."""
    if _contains_probable_mojibake(value):
        raise ValueError(f"{field_name} contains probable mojibake; export stopped for review.")


def _safe_pdf_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:80] or "legal-document"


class LegalRetriever:
    def __init__(self) -> None:
        self._load_lock = Lock()
        self._prewarm_lock = Lock()
        self._inference_lock = Lock()
        self._vector_compute_lock = Lock()
        self._query_lock = Lock()
        self._write_lock = Lock()
        self._tokenizer = None
        self._model = None
        self._collection = None
        self._source_collection = None
        self._requested_device = os.getenv("LEGAL_EMBED_DEVICE", "auto").strip().lower()
        self._embedding_device = torch.device("cpu")
        self._embedding_dtype = torch.float32
        self._embedding_fallback_reason: str | None = None
        self._ready = False
        self._model_fingerprint = _model_revision_fingerprint(MODEL_PATH)
        self._query_vector_cache = QueryVectorCache(
            max_entries=QUERY_VECTOR_CACHE_MAX_ENTRIES,
            ttl_seconds=QUERY_VECTOR_CACHE_TTL_SECONDS,
        )
        self._engine = create_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
        )

    def _resolve_embedding_device(
        self,
    ) -> tuple[torch.device, torch.dtype, str | None]:
        requested = self._requested_device
        if requested not in {"auto", "cuda", "cpu"}:
            raise ValueError(
                "LEGAL_EMBED_DEVICE must be one of: auto, cuda, cpu"
            )
        if requested == "cpu":
            return torch.device("cpu"), torch.float32, None
        if torch.cuda.is_available():
            return torch.device("cuda"), torch.float16, None
        if requested == "cuda":
            raise RuntimeError(
                "LEGAL_EMBED_DEVICE=cuda but CUDA is unavailable in this PyTorch runtime"
            )
        return torch.device("cpu"), torch.float32, "cuda_unavailable"

    def _load_model_for_device(
        self, device: torch.device, dtype: torch.dtype
    ) -> None:
        tokenizer = self._tokenizer or AutoTokenizer.from_pretrained(
            MODEL_PATH, local_files_only=True
        )
        model = AutoModel.from_pretrained(
            MODEL_PATH,
            local_files_only=True,
            dtype=dtype,
        )
        model.to(device)
        model.eval()
        self._tokenizer = tokenizer
        self._model = model
        self._embedding_device = device
        self._embedding_dtype = dtype

    def _load(self) -> None:
        if self._model is not None and self._collection is not None and self._source_collection is not None:
            return
        with self._load_lock:
            if self._model is None:
                device, dtype, fallback_reason = self._resolve_embedding_device()
                try:
                    self._load_model_for_device(device, dtype)
                    self._embedding_fallback_reason = fallback_reason
                except torch.cuda.OutOfMemoryError:
                    if self._requested_device != "auto" or device.type != "cuda":
                        raise
                    self._embedding_fallback_reason = "cuda_oom_during_load"
                    try:
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    self._load_model_for_device(torch.device("cpu"), torch.float32)
            if self._collection is None:
                client = chromadb.PersistentClient(path=str(CHROMA_PATH))
                self._collection = client.get_collection(CHROMA_COLLECTION)
                self._source_collection = client.get_collection(CHROMA_SOURCE_COLLECTION)

    def _compute_query_embedding(self, prepared_query: str) -> np.ndarray:
        self._load()
        inputs = self._tokenizer(
            [QUERY_PREFIX + prepared_query.strip()],
            padding=True,
            truncation=True,
            max_length=2048,
            return_tensors="pt",
        )
        inputs = {name: value.to(self._embedding_device) for name, value in inputs.items()}
        with self._inference_lock, torch.inference_mode():
            output = self._model(**inputs)
            last_token = inputs["attention_mask"].sum(dim=1) - 1
            embedding = output.last_hidden_state[
                torch.arange(len(inputs["input_ids"]), device=self._embedding_device), last_token
            ]
            embedding = F.normalize(embedding, p=2, dim=1)
        return embedding[0].cpu().numpy().astype(np.float32)

    def _activate_cpu_fallback(self, reason: str) -> None:
        """Replace a CUDA model after OOM while keeping callers serialized."""

        with self._load_lock:
            with self._inference_lock:
                old_model = self._model
                self._model = None
                del old_model
                gc.collect()
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
                self._load_model_for_device(torch.device("cpu"), torch.float32)
                self._embedding_fallback_reason = reason
                self._query_vector_cache.clear()

    def encode_query(self, query: str) -> np.ndarray:
        prepared_query = _rewrite_query(query).strip()
        cache_key = query_vector_cache_key(
            prepared_query,
            self._model_fingerprint,
        )
        cached = self._query_vector_cache.get(cache_key)
        if cached is not None:
            return cached

        # Avoid duplicate model work when the same question arrives in a burst.
        with self._vector_compute_lock:
            cached = self._query_vector_cache.get(cache_key)
            if cached is not None:
                return cached
            try:
                embedding = self._compute_query_embedding(prepared_query)
            except torch.cuda.OutOfMemoryError:
                if (
                    self._requested_device != "auto"
                    or self._embedding_device.type != "cuda"
                ):
                    raise
                self._activate_cpu_fallback("cuda_oom")
                embedding = self._compute_query_embedding(prepared_query)
            self._query_vector_cache.set(cache_key, embedding)
            return embedding.copy()

    def _compute_passage_embeddings(
        self, passages: list[str], batch_size: int
    ) -> list[list[float]]:
        self._load()
        vectors: list[list[float]] = []
        with self._inference_lock, torch.inference_mode():
            for start in range(0, len(passages), batch_size):
                batch = passages[start:start + batch_size]
                inputs = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=2048,
                    return_tensors="pt",
                )
                inputs = {name: value.to(self._embedding_device) for name, value in inputs.items()}
                output = self._model(**inputs)
                last_token = inputs["attention_mask"].sum(dim=1) - 1
                embeddings = output.last_hidden_state[
                    torch.arange(len(inputs["input_ids"]), device=self._embedding_device), last_token
                ]
                embeddings = F.normalize(embeddings, p=2, dim=1)
                vectors.extend(embeddings.cpu().numpy().astype(np.float32).tolist())
        return vectors

    def encode_passages(
        self, passages: list[str], batch_size: int = 12
    ) -> list[list[float]]:
        try:
            return self._compute_passage_embeddings(passages, batch_size)
        except torch.cuda.OutOfMemoryError:
            if (
                self._requested_device != "auto"
                or self._embedding_device.type != "cuda"
            ):
                raise
            self._activate_cpu_fallback("cuda_oom")
            return self._compute_passage_embeddings(passages, batch_size)

    def prewarm(self) -> None:
        if self._ready:
            return
        with self._prewarm_lock:
            if self._ready:
                return
            self._load()
            self.encode_query("tra cứu thủ tục hành chính cấp xã")
            self._ready = True

    def preview_import(self, request: LegalImportRequest) -> dict[str, Any]:
        structure, articles, chunks = _build_import_units(request)
        warnings: list[str] = []
        errors: list[str] = []
        today = date.today()
        if not request.confirmed_official_source:
            errors.append("Phải xác nhận nội dung lấy từ nguồn văn bản chính thức.")
        if request.effective_date > today:
            errors.append("Văn bản chưa có hiệu lực nên không được đưa vào chỉ mục.")
        if request.expired_date and request.expired_date <= today:
            errors.append("Văn bản đã hết hiệu lực nên không được đưa vào chỉ mục.")
        if request.issued_date and request.effective_date < request.issued_date:
            errors.append("Ngày có hiệu lực không thể trước ngày ban hành.")
        normalized_scope = " ".join(_normalized_terms(
            f"{request.scope} {request.issuing_agency} {request.title}"
        ))
        is_hai_phong = "hai phong" in normalized_scope
        is_central = any(
            marker in normalized_scope
            for marker in ("trung uong", "toan quoc", "ca nuoc", "quoc hoi", "chinh phu", "bo ")
        )
        if not is_hai_phong and not is_central:
            errors.append(
                "Chỉ nhận văn bản Trung ương hoặc văn bản áp dụng tại Hải Phòng."
            )
        if structure == "structured" and not articles:
            errors.append(
                "Không nhận diện được điều luật. Nội dung phải có tiêu đề dạng “Điều 1. ...”."
            )
        if structure == "unstructured" and not chunks:
            errors.append(
                "Văn bản không điều khoản nhưng không tách được đoạn/mục để nạp."
            )
        if structure == "unstructured":
            warnings.append(
                "Văn bản được nạp theo cấu trúc unstructured (không có 'Điều N.')."
            )
        if not request.source_url:
            warnings.append("Chưa có URL nguồn để cán bộ đối chiếu sau này.")
        with self._engine.connect() as connection:
            duplicate = connection.execute(
                text(
                    "SELECT id, title, status FROM legal_documents "
                    "WHERE lower(trim(law_number)) = lower(trim(:law_number)) LIMIT 1"
                ),
                {"law_number": request.law_number},
            ).mappings().first()
            field = connection.execute(
                text("SELECT id, name FROM legal_fields WHERE id = :field_id"),
                {"field_id": request.field_id},
            ).mappings().first()
        if duplicate:
            errors.append(
                f"Số hiệu đã tồn tại ở văn bản #{duplicate['id']}: {duplicate['title']}."
            )
        if not field:
            errors.append("Lĩnh vực pháp luật không tồn tại.")
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "structure": structure,
            "article_count": len(articles),
            "chunk_count": len(chunks),
            "field": dict(field) if field else None,
            "sample_articles": [
                {
                    "article_number": article["article_number"],
                    "title": article["title"],
                    "characters": len(article["content"]),
                }
                for article in articles[:8]
            ],
        }

    def import_document(self, request: LegalImportRequest) -> dict[str, Any]:
        preview = self.preview_import(request)
        if not preview["valid"]:
            raise ValueError(" | ".join(preview["errors"]))

        structure, articles, _prepared_chunks = _build_import_units(request)
        chunk_records: list[dict[str, Any]] = []
        document_id: int | None = None
        vector_ids: list[str] = []

        with self._write_lock:
            try:
                with self._engine.begin() as connection:
                    document_id = int(connection.execute(
                        text(
                            """
                            INSERT INTO legal_documents (
                                title, law_number, issued_date, effective_date,
                                expired_date, status, source_url, field_id,
                                document_type, issuing_agency, scope, sector,
                                collection_source, applicability_info
                            ) VALUES (
                                :title, :law_number, :issued_date, :effective_date,
                                :expired_date, 'staging', :source_url, :field_id,
                                :document_type, :issuing_agency, :scope, :sector,
                                CASE WHEN :structure = 'unstructured' THEN 'manual_vnlegal_lal_import_unstructured' ELSE 'manual_vnlegal_lal_import' END, :applicability_info
                            ) RETURNING id
                            """
                        ),
                        {
                            **request.model_dump(exclude={"content", "confirmed_official_source", "structure"}),
                            "structure": structure,
                        },
                    ).scalar_one())

                    connection.execute(
                        text(
                            """
                            INSERT INTO legal_search_scope (
                                document_id, included, reason, domain,
                                evaluated_as_of, evaluated_at
                            ) VALUES (
                                :document_id, FALSE, 'pending_embedding_activation',
                                :domain, :evaluated_as_of, :evaluated_at
                            )
                            """
                        ),
                        {
                            "document_id": document_id,
                            "domain": preview["field"]["name"][:80],
                            "evaluated_as_of": date.today(),
                            "evaluated_at": datetime.now(),
                        },
                    )

                    for article in articles:
                        article_id = int(connection.execute(
                            text(
                                """
                                INSERT INTO legal_articles (
                                    document_id, article_number, title, content,
                                    effective_from, effective_to, status
                                ) VALUES (
                                    :document_id, :article_number, :title, :content,
                                    :effective_from, :effective_to, 'staging'
                                ) RETURNING id
                                """
                            ),
                            {
                                "document_id": document_id,
                                **article,
                                "effective_from": request.effective_date,
                                "effective_to": request.expired_date,
                            },
                        ).scalar_one())
                        article_chunks = (
                            _prepared_chunks
                            if structure == "unstructured"
                            else _split_article(article)
                        )
                        for chunk in article_chunks:
                            row = connection.execute(
                                text(
                                    """
                                    INSERT INTO legal_article_chunks (
                                        article_id, chunk_index, heading, content
                                    ) VALUES (
                                        :article_id, :chunk_index, :heading, :content
                                    ) RETURNING id
                                    """
                                ),
                                {"article_id": article_id, **chunk},
                            ).mappings().one()
                            chunk_records.append(
                                {
                                    "chunk_id": int(row["id"]),
                                    "article_id": article_id,
                                    "article_number": article["article_number"],
                                    "article_title": article["title"],
                                    **chunk,
                                }
                            )

                field_name = preview["field"]["name"]
                passage_texts = [
                    "\n".join(
                        value for value in (
                            request.title,
                            request.law_number,
                            request.document_type,
                            request.issuing_agency,
                            request.scope,
                            request.sector,
                            field_name,
                            record["article_title"],
                            record["content"],
                        ) if value
                    )
                    for record in chunk_records
                ]
                embeddings = self.encode_passages(passage_texts)
                vector_ids = [f"chunk-{record['chunk_id']}" for record in chunk_records]
                metadatas = [
                    {
                        "chunk_id": record["chunk_id"],
                        "article_id": record["article_id"],
                        "chunk_index": record["chunk_index"],
                        "document_id": document_id,
                        "document_title": request.title,
                        "law_number": request.law_number,
                        "document_type": request.document_type,
                        "issuing_agency": request.issuing_agency,
                        "scope": request.scope,
                        "sector": request.sector,
                        "field_id": request.field_id,
                        "field_name": field_name,
                        "article_number": record["article_number"],
                        "article_title": record["article_title"],
                        "source_url": request.source_url,
                        "effective_date": request.effective_date.isoformat(),
                        "doc_status": "staging",
                        "status": "staging",
                        "structure": structure,
                    }
                    for record in chunk_records
                ]
                self._load()
                with self._query_lock:
                    self._collection.upsert(
                        ids=vector_ids, embeddings=embeddings, metadatas=metadatas
                    )
                    source_collection = chromadb.PersistentClient(
                        path=str(CHROMA_PATH)
                    ).get_collection(CHROMA_SOURCE_COLLECTION)
                    source_collection.upsert(
                        ids=vector_ids, embeddings=embeddings, metadatas=metadatas
                    )

                # Activate only after both Chroma collections accept vectors.
                with self._engine.begin() as connection:
                    connection.execute(
                        text("UPDATE legal_documents SET status = 'active' WHERE id = :document_id AND status = 'staging'"),
                        {"document_id": document_id},
                    )
                    connection.execute(
                        text("UPDATE legal_articles SET status = 'active' WHERE document_id = :document_id AND status = 'staging'"),
                        {"document_id": document_id},
                    )
                    connection.execute(
                        text("""
                            UPDATE legal_search_scope
                            SET included = TRUE,
                                reason = 'embedding_completed',
                                evaluated_at = :evaluated_at
                            WHERE document_id = :document_id
                        """),
                        {"document_id": document_id, "evaluated_at": datetime.now()},
                    )
                _invalidate_document_cache(str(document_id))
            except Exception:
                if vector_ids:
                    try:
                        with self._query_lock:
                            self._collection.delete(ids=vector_ids)
                            chromadb.PersistentClient(path=str(CHROMA_PATH)).get_collection(
                                CHROMA_SOURCE_COLLECTION
                            ).delete(ids=vector_ids)
                    except Exception:
                        pass
                if document_id is not None:
                    with self._engine.begin() as connection:
                        connection.execute(
                            text("DELETE FROM legal_documents WHERE id = :document_id"),
                            {"document_id": document_id},
                        )
                raise

        return {
            "status": "embedded_active",
            "activation_status": "active",
            "document_id": document_id,
            "law_number": request.law_number,
            "structure": structure,
            "article_count": len(articles),
            "chunk_count": len(chunk_records),
            "model": "VNLegal-LAL",
        }

    def fields(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            return [
                _repair_display_row(dict(row))
                for row in connection.execute(
                    text("SELECT id, name, description FROM legal_fields ORDER BY id")
                ).mappings()
            ]

    def list_documents(
        self,
        *,
        query: str = "",
        domain: str | None = None,
        tier: str = "all",
        as_of: date,
        limit: int = 30,
        offset: int = 0,
        sort_by: str = "effective_date",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """List effective documents available to the legal retrieval service.

        The fast index is represented by ``legal_search_scope.included``. Active
        documents outside that scope remain available through the expanded
        retrieval collection and are labelled accordingly for the UI.
        """
        cleaned_query = str(query or "").strip()
        cleaned_domain = str(domain or "").strip()
        tier = tier if tier in {"all", "core", "expanded"} else "all"
        sort_columns = {
            "effective_date": "d.effective_date",
            "issued_date": "d.issued_date",
            "title": "d.title",
            "law_number": "d.law_number",
        }
        sort_column = sort_columns.get(sort_by, sort_columns["effective_date"])
        sort_direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"

        filters = [
            "d.status = 'active'",
            "(d.effective_date IS NULL OR d.effective_date <= :as_of)",
            "(d.expired_date IS NULL OR d.expired_date > :as_of)",
        ]
        params: dict[str, Any] = {
            "as_of": as_of,
            "limit": max(1, min(int(limit), 100)),
            "offset": max(0, int(offset)),
        }
        if cleaned_query:
            filters.append(
                "(d.title ILIKE :query OR d.law_number ILIKE :query "
                "OR d.issuing_agency ILIKE :query OR d.document_type ILIKE :query)"
            )
            params["query"] = f"%{cleaned_query}%"
        if cleaned_domain:
            filters.append(
                "(" 
                "EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id "
                "AND s.included = TRUE AND s.domain = :domain) OR "
                "EXISTS (SELECT 1 FROM legal_commune_field_groups g WHERE g.field_id = d.field_id "
                "AND g.included = TRUE AND g.group_slug = :domain) OR "
                "EXISTS (SELECT 1 FROM legal_fields f2 WHERE f2.id = d.field_id "
                "AND lower(f2.name) = lower(:domain))"
                ")"
            )
            params["domain"] = cleaned_domain
        if tier == "core":
            filters.append(
                "EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE)"
            )
        elif tier == "expanded":
            filters.append(
                "NOT EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE)"
            )

        where_sql = " AND ".join(filters)
        count_statement = text(
            f"SELECT COUNT(*) FROM legal_documents d WHERE {where_sql}"
        )
        list_statement = text(
            f"""
            WITH page AS (
                SELECT d.*
                FROM legal_documents d
                WHERE {where_sql}
                ORDER BY {sort_column} {sort_direction} NULLS LAST, d.id DESC
                LIMIT :limit OFFSET :offset
            )
            SELECT
                d.id AS doc_id,
                d.title AS document_title,
                d.law_number,
                d.document_type,
                d.issuing_agency,
                d.scope,
                d.sector,
                d.status AS effective_status,
                d.issued_date,
                d.effective_date,
                d.expired_date,
                d.source_url,
                d.field_id,
                f.name AS field_name,
                COALESCE(scope_row.domain, field_group.group_slug, f.name) AS domain,
                COALESCE(field_group.group_name, f.name) AS domain_name,
                CASE WHEN scope_row.document_id IS NOT NULL THEN 'core' ELSE 'expanded' END AS retrieval_tier,
                (
                    SELECT COUNT(*)
                    FROM legal_articles a
                    WHERE a.document_id = d.id AND a.status = 'active'
                ) AS article_count
            FROM page d
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN LATERAL (
                SELECT s.document_id, s.domain
                FROM legal_search_scope s
                WHERE s.document_id = d.id AND s.included = TRUE
                ORDER BY s.evaluated_at DESC NULLS LAST
                LIMIT 1
            ) scope_row ON TRUE
            LEFT JOIN LATERAL (
                SELECT g.group_slug, g.group_name
                FROM legal_commune_field_groups g
                WHERE g.field_id = d.field_id AND g.included = TRUE
                ORDER BY g.evaluated_at DESC NULLS LAST
                LIMIT 1
            ) field_group ON TRUE
            ORDER BY {sort_column} {sort_direction} NULLS LAST, d.id DESC
            """
        )
        with self._engine.connect() as connection:
            total = int(connection.execute(count_statement, params).scalar_one())
            items = [
                _repair_display_row(dict(row))
                for row in connection.execute(list_statement, params).mappings()
            ]
        try:
            indexed_records = self._collection.count()
        except Exception:
            # Older persisted Chroma stores can expose a Rust binding without
            # the optional count helper; readiness must not fail solely on a
            # diagnostic counter while query operations remain available.
            indexed_records = None
        return {
            "items": items,
            "total": total,
            "limit": params["limit"],
            "offset": params["offset"],
            "as_of": as_of.isoformat(),
            "tier": tier,
        }

    def domains(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            group_slug AS slug,
                            group_name AS name,
                            count(*) AS field_count
                        FROM legal_commune_field_groups
                        WHERE included = TRUE
                          AND group_slug IS NOT NULL
                        GROUP BY group_slug, group_name
                        ORDER BY group_name
                        """
                    )
                ).mappings()
            ]

    def search(self, request: SearchRequest) -> dict[str, Any]:
        started = perf_counter()
        query_vector = self.encode_query(request.query)
        encoded_at = perf_counter()

        collection = (
            self._source_collection
            if request.retrieval_tier == "expanded"
            else self._collection
        )
        # Chroma's local HNSW client is not documented as thread-safe.
        # A commune domain contains many field IDs and older Chroma metadata can
        # be stale. The reviewed SQL legal scope is the authoritative filter.
        with self._query_lock:
            raw = collection.query(
                query_embeddings=[query_vector.tolist()],
                n_results=request.candidate_count,
                include=["metadatas", "distances"],
            )
        searched_at = perf_counter()

        candidates = []
        vector_candidate_count = 0
        for item_id, metadata, distance in zip(
            raw["ids"][0],
            raw["metadatas"][0],
            raw["distances"][0],
        ):
            metadata = metadata or {}
            chunk_id = _as_int(metadata.get("chunk_id"))
            if not chunk_id and item_id.startswith("chunk-"):
                chunk_id = _as_int(item_id[6:])
            if chunk_id:
                candidates.append(
                    {
                        "chunk_id": chunk_id,
                        "vector_score": 1.0 - float(distance),
                        "metadata": metadata,
                        "retrieval_source": "vector",
                    }
                )
                vector_candidate_count += 1

        lexical_rows = self._fetch_lexical_chunks(
            request.query,
            request.domain,
            request.as_of,
            request.retrieval_tier,
            exclude_chunk_ids=[item["chunk_id"] for item in candidates],
        )
        for row in lexical_rows:
            candidates.append(
                {
                    "chunk_id": int(row["chunk_id"]),
                    "vector_score": 0.15,
                    "metadata": row,
                    "retrieval_source": "lexical",
                }
            )

        lexical_candidate_count = len(lexical_rows)
        candidate_count_before_hydration = len(candidates)

        rows = self._fetch_chunks(
            [item["chunk_id"] for item in candidates], request.retrieval_tier
        )
        row_by_chunk = {int(row["chunk_id"]): row for row in rows}
        relationships_by_document = self._fetch_relationships(
            [int(row["document_id"]) for row in rows]
        )

        fallback_phrases, fallback_domain = _fallback_phrases(request.query)
        if fallback_phrases:
            fallback_rows = self._fetch_fallback_chunks(
                fallback_phrases,
                request.domain or fallback_domain,
                request.as_of,
                request.retrieval_tier,
                exclude_chunk_ids=list(row_by_chunk.keys()),
            )
            if fallback_rows:
                for row in fallback_rows:
                    row_by_chunk[int(row["chunk_id"])] = row
                    candidates.append(
                        {
                            "chunk_id": int(row["chunk_id"]),
                            "vector_score": 0.20,
                            "metadata": row,
                            "retrieval_source": "fallback",
                        }
                    )
                rows.extend(fallback_rows)
                relationships_by_document.update(
                    self._fetch_relationships([int(row["document_id"]) for row in fallback_rows])
                )

        # 1. Tính toán điểm BM25 Okapi động trên tập candidate
        corpus = []
        for cand in candidates:
            row = row_by_chunk.get(cand["chunk_id"])
            text_content = ""
            if row:
                text_content = " ".join([
                    str(row.get("content") or ""),
                    str(row.get("chunk_heading") or ""),
                    str(row.get("article_title") or ""),
                    str(row.get("document_title") or "")
                ])
            corpus.append(_normalized_terms(text_content))

        bm25 = None
        if corpus and BM25Okapi is not None:
            try:
                bm25 = BM25Okapi(corpus)
            except Exception:
                pass

        query_tokens = _normalized_terms(request.query)
        bm25_values: list[float] = []
        if bm25 and query_tokens:
            try:
                bm25_values = [float(value) for value in bm25.get_scores(query_tokens)]
            except Exception:
                bm25_values = []
        bm25_scores = []
        for idx, cand in enumerate(candidates):
            s_val = bm25_values[idx] if idx < len(bm25_values) else 0.0
            cand["bm25_score"] = s_val
            bm25_scores.append(s_val)

        max_bm25 = max(bm25_scores) if bm25_scores else 0.0
        for cand in candidates:
            norm_bm25 = (cand["bm25_score"] / max_bm25) if max_bm25 > 0.0 else 0.0
            cand["norm_bm25_score"] = norm_bm25

        # 2. Rerank / Lọc / Ưu tiên local & official QPPL
        ranked = []
        filtered_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            row = row_by_chunk.get(candidate["chunk_id"])
            if not row:
                filtered_candidates.append(
                    {
                        "chunk_id": candidate["chunk_id"],
                        "reason": "not_hydrated_or_out_of_scope",
                    }
                )
                continue
            if not _domain_matches(request.domain, row.get("domain_slug")):
                filtered_candidates.append(
                    {
                        "chunk_id": candidate["chunk_id"],
                        "reason": "outside_selected_domain",
                        "law_number": row.get("law_number"),
                        "document_title": row.get("document_title"),
                        "domain_slug": row.get("domain_slug"),
                    }
                )
                continue
            if not _is_current(row, request.as_of):
                filtered_candidates.append(
                    {
                        "chunk_id": candidate["chunk_id"],
                        "reason": "expired_or_not_yet_effective",
                        "law_number": row.get("law_number"),
                        "document_title": row.get("document_title"),
                        "effective_date": _iso_or_none(row.get("effective_date")),
                        "expired_date": _iso_or_none(row.get("expired_date")),
                    }
                )
                continue
            relationships = relationships_by_document.get(
                int(row["document_id"]), []
            )
            if _has_expired_legal_basis(relationships, request.as_of):
                filtered_candidates.append(
                    {
                        "chunk_id": candidate["chunk_id"],
                        "reason": "expired_legal_basis",
                        "law_number": row.get("law_number"),
                        "document_title": row.get("document_title"),
                    }
                )
                continue

            metadata = candidate.get("metadata") or {}
            priority_boost = _priority_boost(row, metadata)
            lexical_boost = _lexical_boost(request.query, row)
            topic_boost = _topic_boost(request.query, row)
            retrieval_source = candidate.get("retrieval_source", "vector")
            
            # Kết hợp điểm Vector (60%) và BM25 (40%)
            v_score = candidate["vector_score"]
            norm_b_score = candidate["norm_bm25_score"]
            hybrid_retrieval_score = (v_score * 0.6) + (norm_b_score * 0.4)

            score = (
                hybrid_retrieval_score
                + priority_boost
                + lexical_boost
                + topic_boost
            )
            
            ranked.append(
                {
                    "score": round(score, 6),
                    "vector_score": round(v_score, 6),
                    "bm25_score": round(candidate["bm25_score"], 6),
                    "metadata_score": round(priority_boost, 6),
                    "priority_boost": round(priority_boost, 6),
                    "lexical_boost": round(lexical_boost, 6),
                    "topic_boost": round(topic_boost, 6),
                    "retrieval_source": retrieval_source,
                    **row,
                    "relationships": relationships,
                    "source_url": row.get("source_url")
                    or metadata.get("source_url")
                    or None,
                }
            )

        current_count = len(ranked)
        filtered_counts: dict[str, int] = {}
        for item in filtered_candidates:
            reason = str(item.get("reason") or "unknown")
            filtered_counts[reason] = filtered_counts.get(reason, 0) + 1

        ranked = self._rerank_candidates(request.query, ranked)
        ranked.sort(key=lambda item: item["score"], reverse=True)
        primary_evidence: list[dict[str, Any]] = []
        complementary_evidence: list[dict[str, Any]] = []
        seen_articles: set[tuple[str, str]] = set()
        seen_content: set[tuple[str, str, str]] = set()
        for item in ranked:
            article_key = (
                str(item.get("document_id") or ""),
                str(item.get("article_id") or item.get("article_number") or ""),
            )
            normalized_content = " ".join(
                _normalized_terms(str(item.get("content") or ""))
            )
            content_fingerprint = hashlib.sha256(
                (
                    normalized_content
                    or f"chunk:{item.get('chunk_id') or ''}"
                ).encode("utf-8")
            ).hexdigest()
            content_key = (*article_key, content_fingerprint)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            if article_key in seen_articles:
                complementary_evidence.append(item)
                continue
            seen_articles.add(article_key)
            primary_evidence.append(item)

        # Prefer source/article diversity first.  If there are not enough
        # distinct articles, retain one complementary chunk per article rather
        # than silently discarding useful parts of a long provision.
        deduplicated = primary_evidence[: request.limit]
        article_counts: dict[tuple[str, str], int] = {
            (
                str(item.get("document_id") or ""),
                str(item.get("article_id") or item.get("article_number") or ""),
            ): 1
            for item in deduplicated
        }
        for item in complementary_evidence:
            if len(deduplicated) >= request.limit:
                break
            article_key = (
                str(item.get("document_id") or ""),
                str(item.get("article_id") or item.get("article_number") or ""),
            )
            if article_counts.get(article_key, 0) >= 2:
                continue
            deduplicated.append(item)
            article_counts[article_key] = article_counts.get(article_key, 0) + 1

        bound_results = [
            bind_request_provenance(item, request) for item in deduplicated
        ]
        finished_at = perf_counter()
        response = {
            "query": request.query,
            "as_of": request.as_of.isoformat(),
            "results": bound_results,
            "total_count": len(bound_results),
            "timing_ms": {
                "embedding": round((encoded_at - started) * 1000, 1),
                "ann_search": round((searched_at - encoded_at) * 1000, 1),
                "hydrate_filter_rank": round((finished_at - searched_at) * 1000, 1),
                "total": round((finished_at - started) * 1000, 1),
            },
        }
        if request.include_trace:
            detected = _detected_domain(deduplicated)
            response["trace"] = {
                "input_question": request.query,
                "selected_domain": request.domain,
                "scope_filter": request.scope_filter,
                "retrieval_tier": request.retrieval_tier,
                "request_id": request.request_id,
                "issue_id": request.issue_id,
                "issue_domain": request.issue_domain or request.domain,
                "collection": (
                    CHROMA_SOURCE_COLLECTION
                    if request.retrieval_tier == "expanded"
                    else CHROMA_COLLECTION
                ),
                "scope_filter_applied": request.retrieval_tier == "core",
                "scope_policy": (
                    "active_central_and_hai_phong_excluding_other_provinces"
                    if request.retrieval_tier == "expanded"
                    else "reviewed_hai_phong_commune_scope"
                ),
                "detected_domain": detected,
                "pipeline_counts": {
                    "vector_candidates": vector_candidate_count,
                    "lexical_candidates": lexical_candidate_count,
                    "candidates_before_hydration": candidate_count_before_hydration,
                    "hydrated_rows": len(row_by_chunk),
                    "current_candidates": current_count,
                    "filtered_candidates": len(filtered_candidates),
                    "filtered_by_reason": filtered_counts,
                    "deduplicated_results": len(bound_results),
                },
                "retrieved_chunks": [
                    _chunk_trace_item(item) for item in bound_results
                ],
                "filtered_candidates": filtered_candidates[:30],
                "llm_sources": [
                    _source_trace_item(item) for item in bound_results
                ],
            }
        return response

    def _rerank_candidates(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not candidates:
            return candidates
        ordered = sorted(candidates, key=lambda item: item["score"], reverse=True)
        if len(ordered) < 2:
            return ordered
        window = ordered[:RERANK_WINDOW]
        rescored: list[dict[str, Any]] = []
        for item in window:
            bm25_score = float(item.get("bm25_score") or 0.0)
            bm25_norm = float(item.get("norm_bm25_score") or 0.0)
            bonus = 0.12 * bm25_norm
            if item.get("retrieval_source") == "lexical":
                bonus += 0.03
            rescored.append({**item, "rerank_score": round(bm25_score, 6), "score": round(item["score"] + bonus, 6)})
        if len(ordered) > RERANK_WINDOW:
            rescored.extend(ordered[RERANK_WINDOW:])
        return rescored

    def _fetch_lexical_chunks(
        self,
        query: str,
        domain: str | None,
        as_of: date,
        retrieval_tier: str,
        exclude_chunk_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        terms = [
            term
            for term in _normalized_terms(query)
            if len(term) >= 3 and term not in LEXICAL_STOPWORDS
        ]
        if not terms:
            return []
        exclude_chunk_ids = exclude_chunk_ids or []
        params: dict[str, Any] = {"as_of": as_of, "limit": LEXICAL_MATCH_LIMIT}
        clauses = []
        for index, term in enumerate(terms[:12]):
            key = f"term_{index}"
            params[key] = f"%{term}%"
            clauses.append(
                f"LOWER(COALESCE(c.content, '')) LIKE :{key} OR "
                f"LOWER(COALESCE(c.heading, '')) LIKE :{key} OR "
                f"LOWER(COALESCE(a.title, '')) LIKE :{key} OR "
                f"LOWER(COALESCE(d.title, '')) LIKE :{key} OR "
                f"LOWER(COALESCE(d.law_number, '')) LIKE :{key}"
            )
        if not clauses:
            return []
        exclude_clause = ""
        if exclude_chunk_ids:
            params["exclude_chunk_ids"] = exclude_chunk_ids
            exclude_clause = "AND c.id NOT IN :exclude_chunk_ids"
        scope_join = (
            "JOIN legal_search_scope search_scope "
            "ON search_scope.document_id = d.id AND search_scope.included = TRUE"
            if retrieval_tier == "core"
            else "LEFT JOIN legal_search_scope search_scope ON search_scope.document_id = d.id"
        )
        expanded_scope_clause = (
            "AND COALESCE(search_scope.reason, '') <> 'other_province'"
            if retrieval_tier == "expanded"
            else ""
        )
        domain_clause = ""
        if domain:
            params["domains"] = list(_domain_values(domain))
            domain_clause = (
                "AND (g.group_slug IN :domains OR "
                "(g.group_slug IS NULL AND search_scope.domain IN :domains))"
            )
        statement = text(
            f"""
            SELECT DISTINCT
                c.id AS chunk_id,
                c.chunk_index,
                c.heading AS chunk_heading,
                c.content,
                a.id AS article_id,
                a.article_number,
                a.title AS article_title,
                a.status AS article_status,
                a.effective_from AS article_effective_from,
                a.effective_to AS article_effective_to,
                d.id AS document_id,
                d.title AS document_title,
                d.law_number,
                d.document_type,
                d.issuing_agency,
                d.scope,
                d.sector,
                d.status AS document_status,
                d.issued_date,
                d.effective_date,
                d.expired_date,
                d.source_url,
                d.field_id,
                f.name AS field_name,
                COALESCE(g.group_slug, search_scope.domain) AS domain_slug,
                g.group_name AS domain_name
            FROM legal_article_chunks c
            JOIN legal_articles a ON a.id = c.article_id
            JOIN legal_documents d ON d.id = a.document_id
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE ({' OR '.join(clauses)})
              AND d.status = 'active'
              AND a.status = 'active'
              {expanded_scope_clause}
              {domain_clause}
              {exclude_clause}
            ORDER BY d.effective_date DESC NULLS LAST, c.id DESC
            LIMIT :limit
            """
        )
        if exclude_chunk_ids:
            statement = statement.bindparams(bindparam("exclude_chunk_ids", expanding=True))
        if domain:
            statement = statement.bindparams(bindparam("domains", expanding=True))
        with self._engine.connect() as connection:
            rows = [_repair_display_row(dict(row)) for row in connection.execute(statement, params).mappings()]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not _is_current(row, as_of):
                continue
            haystack = " ".join(
                _normalized_terms(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("article_title", "chunk_heading", "content", "document_title")
                    )
                )
            )
            if any(term in haystack for term in terms):
                matched_rows.append(row)
        return matched_rows[:20]


    def document_detail(
        self,
        doc_id: str,
        article: str | None = None,
        include_content: bool = False,
    ) -> dict[str, Any]:
        """Return an approved document from the local index.

        The default response is metadata plus a lightweight article index. Article
        text is loaded only when ``article`` or ``include_content`` is requested,
        keeping the document viewer responsive for large instruments.
        """
        cleaned = str(doc_id or "").replace("legal:", "").strip()
        if not cleaned:
            raise ValueError("doc_id is required")
        
        # Use cache only for lightweight metadata/index requests.
        if not article and not include_content:
            cached = _get_cached_document(cleaned)
            if cached:
                return cached

        with self._engine.connect() as connection:
            document = connection.execute(
                text(
                    """
                    SELECT
                        d.id,
                        d.title AS document_title,
                        d.law_number,
                        d.document_type,
                        d.issuing_agency,
                        d.scope,
                        d.sector,
                        d.status AS effective_status,
                        d.issued_date,
                        d.effective_date,
                        d.expired_date,
                        d.source_url,
                        d.field_id,
                        f.name AS field_name
                    FROM legal_documents d
                    LEFT JOIN legal_fields f ON f.id = d.field_id
                    WHERE CAST(d.id AS TEXT) = :doc_id
                      AND d.status = 'active'
                    LIMIT 1
                    """
                ),
                {"doc_id": cleaned},
            ).mappings().first()

            # Citation code may pass a chunk/article id in older responses. Resolve it.
            if not document:
                resolved = connection.execute(
                    text(
                        """
                        SELECT d.id AS document_id
                        FROM legal_article_chunks c
                        JOIN legal_articles a ON a.id = c.article_id
                        JOIN legal_documents d ON d.id = a.document_id
                        WHERE CAST(c.id AS TEXT) = :doc_id
                        LIMIT 1
                        """
                    ),
                    {"doc_id": cleaned},
                ).mappings().first()
                if not resolved:
                    resolved = connection.execute(
                        text(
                            """
                            SELECT d.id AS document_id
                            FROM legal_articles a
                            JOIN legal_documents d ON d.id = a.document_id
                            WHERE CAST(a.id AS TEXT) = :doc_id
                            LIMIT 1
                            """
                        ),
                        {"doc_id": cleaned},
                    ).mappings().first()
                if resolved:
                    cleaned = str(resolved["document_id"])
                    document = connection.execute(
                        text(
                            """
                            SELECT
                                d.id,
                                d.title AS document_title,
                                d.law_number,
                                d.document_type,
                                d.issuing_agency,
                                d.scope,
                                d.sector,
                                d.status AS effective_status,
                                d.issued_date,
                                d.effective_date,
                                d.expired_date,
                                d.source_url,
                                d.field_id,
                                f.name AS field_name
                            FROM legal_documents d
                            LEFT JOIN legal_fields f ON f.id = d.field_id
                            WHERE CAST(d.id AS TEXT) = :doc_id
                          AND d.status = 'active'
                            LIMIT 1
                            """
                        ),
                        {"doc_id": cleaned},
                    ).mappings().first()

            if not document:
                raise LookupError("Document not found")

            # Repair legacy display encoding at the read boundary. The source
            # database remains untouched, while viewer/PDF consumers receive
            # valid Unicode and can safely validate the exported text.
            document = _repair_display_row(dict(document))

            params: dict[str, Any] = {"document_id": int(document["id"])}
            article_filter = ""
            if article:
                article_filter = "AND lower(trim(a.article_number)) = lower(trim(:article))"
                params["article"] = str(article).strip()

            # Metadata/index requests deliberately avoid transferring all chunk text.
            load_content = bool(article or include_content)
            chunk_select = """
                            c.id AS chunk_id,
                            c.chunk_index,
                            c.heading AS chunk_heading,
                            c.content
            """ if load_content else """
                            NULL::BIGINT AS chunk_id,
                            NULL::INTEGER AS chunk_index,
                            NULL::TEXT AS chunk_heading,
                            NULL::TEXT AS content
            """
            chunk_join = "LEFT JOIN legal_article_chunks c ON c.article_id = a.id" if load_content else ""

            rows = [
                _repair_display_row(dict(row))
                for row in connection.execute(
                    text(
                        f"""
                        SELECT
                            a.id AS article_id,
                            a.article_number,
                            a.title AS article_title,
                            a.status AS article_status,
                            a.effective_from AS article_effective_from,
                            a.effective_to AS article_effective_to,
                            {chunk_select}
                        FROM legal_articles a
                        {chunk_join}
                        WHERE a.document_id = :document_id
                          AND a.status = 'active'
                          {article_filter}
                        ORDER BY CAST(NULLIF(a.article_number, '') AS INTEGER) NULLS LAST,
                                 a.article_number,
                                 chunk_index,
                                 chunk_id
                        """
                    ),
                    params,
                ).mappings()
            ]

        chunks: list[dict[str, Any]] = []
        article_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            article_key = str(row.get("article_id"))
            if article_key not in article_map:
                article_map[article_key] = {
                    "article_id": row.get("article_id"),
                    "article_number": row.get("article_number"),
                    "article_title": row.get("article_title"),
                    "status": row.get("article_status"),
                    "effective_from": _iso_or_none(row.get("article_effective_from")),
                    "effective_to": _iso_or_none(row.get("article_effective_to")),
                    "chunks": [],
                }
            if row.get("chunk_id") is not None:
                chunk = {
                    "chunk_id": row.get("chunk_id"),
                    "chunk_index": row.get("chunk_index"),
                    "heading": row.get("chunk_heading"),
                    "content": row.get("content") or "",
                    "article_number": row.get("article_number"),
                    "article_title": row.get("article_title"),
                }
                chunks.append(chunk)
                article_map[article_key]["chunks"].append(chunk)

        content_parts: list[str] = []
        for art in article_map.values():
            heading = " ".join(
                part for part in [
                    f"Điều {art.get('article_number')}" if art.get("article_number") not in (None, "", "0") else "",
                    str(art.get("article_title") or ""),
                ] if part
            ).strip()
            if heading:
                content_parts.append(heading)
            for ch in art.get("chunks") or []:
                content = str(ch.get("content") or "").strip()
                if content:
                    content_parts.append(content)

        # Build lightweight article index for lazy-load TOC
        article_index = []
        for art in article_map.values():
            article_index.append({
                "article_number": art.get("article_number"),
                "article_title": art.get("article_title"),
                "has_content": bool(art.get("chunks")),
            })
        
        result = {
            "doc_id": str(document["id"]),
            "document_title": document.get("document_title"),
            "article_index": article_index,
            "source_file_available": Path("data/uploads/pdfs").joinpath(f'{document["id"]}.pdf').exists(),
            "law_number": document.get("law_number"),
            "document_type": document.get("document_type"),
            "issuing_agency": document.get("issuing_agency"),
            "scope": document.get("scope"),
            "sector": document.get("sector"),
            "source_url": document.get("source_url"),
            "effective_status": document.get("effective_status"),
            "issued_date": _iso_or_none(document.get("issued_date")),
            "effective_date": _iso_or_none(document.get("effective_date")),
            "expired_date": _iso_or_none(document.get("expired_date")),
            "field_id": document.get("field_id"),
            "field_name": document.get("field_name"),
            "article_filter": article,
            "articles": list(article_map.values()),
            "chunks": chunks,
            "content": "\n\n".join(content_parts),
        }
        
        # Cache metadata-only results (includes article_index + source_file_available)
        if not article and not include_content:
            _set_cached_document(cleaned, result)
        
        return result

    def document_pdf(self, doc_id: str, article: str | None = None) -> tuple[bytes, str]:
        """Generate a readable, fully embedded Unicode PDF from indexed content.

        This method intentionally generates only internal extracts. The API layer
        bypasses it completely when an approved original PDF exists.
        """
        started = perf_counter()
        detail = self.document_detail(doc_id, article=article, include_content=True)
        try:
            import fitz
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("PyMuPDF is required to generate PDFs") from exc

        if not LEGAL_PDF_FONT_PATH.is_file():
            raise RuntimeError(
                "Noto Sans font asset is unavailable; cannot safely export Vietnamese PDF."
            )

        title = str(detail.get("document_title") or "V\u0103n b\u1ea3n ph\u00e1p l\u00fd")
        law_number = str(detail.get("law_number") or "")
        source_url = str(detail.get("source_url") or "")
        _assert_exportable_utf8(title, "Document title")
        _assert_exportable_utf8(law_number, "Law number")
        _assert_exportable_utf8(source_url, "Source URL")

        pdf = fitz.open()
        margin = 48
        page_width, page_height = 595, 842
        content_width = page_width - (margin * 2)
        y = margin
        font_regular = "LegalNotoSans"
        font_bold = "LegalNotoSansBold"

        def add_page() -> Any:
            value = pdf.new_page(width=page_width, height=page_height)
            # Use the OFL font file as a real embedded font. No PDF core font is
            # used here because those fonts lack Vietnamese glyph coverage.
            value.insert_font(fontname=font_regular, fontfile=str(LEGAL_PDF_FONT_PATH))
            value.insert_font(fontname=font_bold, fontfile=str(LEGAL_PDF_FONT_PATH))
            return value

        page = add_page()

        def write_block(
            value: str,
            *,
            size: float = 10.5,
            bold: bool = False,
            highlight: bool = False,
            color: tuple[float, float, float] = (0.1, 0.12, 0.16),
            gap_after: float = 4,
        ) -> None:
            nonlocal page, y
            _assert_exportable_utf8(value, "Document text")
            # Use textbox to wrap on words. If it does not fit, start a page and
            # retry; this avoids clipping large Vietnamese paragraphs.
            line_height = size * 1.45
            available = page_height - y - margin
            estimated = max(line_height * 2, ((len(value) / 78) + 1) * line_height)
            height = min(max(estimated + 6, line_height + 6), available)
            rect = fitz.Rect(margin, y, margin + content_width, y + height)
            fontname = font_bold if bold else font_regular
            if highlight:
                page.draw_rect(
                    fitz.Rect(margin - 4, y - 3, margin + content_width + 4, y + height + 2),
                    color=(0.82, 0.63, 0.12),
                    fill=(1.0, 0.96, 0.72),
                    width=0.8,
                )
            remainder = page.insert_textbox(
                rect,
                value,
                fontname=fontname,
                fontsize=size,
                color=color,
                lineheight=1.25,
            )
            if remainder < 0:
                page = add_page()
                y = margin
                rect = fitz.Rect(margin, y, margin + content_width, page_height - margin)
                if highlight:
                    page.draw_rect(
                        fitz.Rect(margin - 4, y - 3, margin + content_width + 4, page_height - margin),
                        color=(0.82, 0.63, 0.12),
                        fill=(1.0, 0.96, 0.72),
                        width=0.8,
                    )
                remainder = page.insert_textbox(
                    rect,
                    value,
                    fontname=fontname,
                    fontsize=size,
                    color=color,
                    lineheight=1.25,
                )
                if remainder < 0:
                    raise ValueError("A document paragraph is too large for PDF export.")
                y = page_height - margin + gap_after
            else:
                y += height + gap_after

        # Minimal visual hierarchy; do not claim that this is an official gazette.
        write_block(title, size=15, bold=True, color=(0.05, 0.16, 0.34), gap_after=5)
        if law_number:
            write_block(f"S\u1ed1 hi\u1ec7u: {law_number}", size=11.5, bold=True, gap_after=3)
        write_block("B\u1ea2N TR\u00cdCH XU\u1ea4T T\u1eea KHO H\u1ec6 TH\u1ed0NG", size=9.5, bold=True, color=(0.45, 0.25, 0.02), gap_after=1)
        write_block("Kh\u00f4ng ph\u1ea3i b\u1ea3n C\u00f4ng b\u00e1o/b\u1ea3n PDF g\u1ed1c do c\u01a1 quan ban h\u00e0nh.", size=8.5, color=(0.35, 0.35, 0.35), gap_after=6)
        if source_url:
            write_block(f"Ngu\u1ed3n l\u01b0u trong kho: {source_url}", size=8.5, color=(0.35, 0.35, 0.35), gap_after=8)

        requested = str(article or "").strip().casefold()
        for item in detail.get("articles") or []:
            article_number = str(item.get("article_number") or "").strip()
            article_title = str(item.get("article_title") or "").strip()
            _assert_exportable_utf8(article_title, "Article title")
            heading = " ".join(
                part
                for part in [
                    f"\u0110i\u1ec1u {article_number}" if article_number and article_number != "0" else "",
                    article_title,
                ]
                if part
            ).strip()
            if heading:
                write_block(
                    heading,
                    size=12,
                    bold=True,
                    highlight=bool(requested and article_number.casefold() == requested),
                    gap_after=4,
                )
            for chunk in item.get("chunks") or []:
                chunk_text = str(chunk.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                _assert_exportable_utf8(chunk_text, "Article content")
                for paragraph in (part.strip() for part in re.split(r"\n\s*\n+", chunk_text) if part.strip()):
                    write_block(paragraph, size=10.5, gap_after=5)

        data = pdf.tobytes(deflate=True, garbage=4)
        pdf.close()
        filename = f"{_safe_pdf_filename(law_number or title)}.pdf"
        # Kept for future telemetry callers and local profiling.
        _ = round((perf_counter() - started) * 1000, 1)
        return data, filename

    def _fetch_chunks(self, chunk_ids: list[int], retrieval_tier: str) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        scope_join = (
            "JOIN legal_search_scope search_scope "
            "ON search_scope.document_id = d.id AND search_scope.included = TRUE"
            if retrieval_tier == "core"
            else "LEFT JOIN legal_search_scope search_scope ON search_scope.document_id = d.id"
        )
        expanded_scope_clause = (
            "AND COALESCE(search_scope.reason, '') <> 'other_province'"
            if retrieval_tier == "expanded"
            else ""
        )
        statement = text(
            f"""
            SELECT
                c.id AS chunk_id,
                c.chunk_index,
                c.heading AS chunk_heading,
                c.content,
                a.id AS article_id,
                a.article_number,
                a.title AS article_title,
                a.status AS article_status,
                a.effective_from AS article_effective_from,
                a.effective_to AS article_effective_to,
                d.id AS document_id,
                d.title AS document_title,
                d.law_number,
                d.document_type,
                d.issuing_agency,
                d.scope,
                d.sector,
                d.status AS document_status,
                d.issued_date,
                d.effective_date,
                d.expired_date,
                d.source_url,
                d.field_id,
                f.name AS field_name,
                COALESCE(g.group_slug, search_scope.domain) AS domain_slug,
                g.group_name AS domain_name
            FROM legal_article_chunks c
            JOIN legal_articles a ON a.id = c.article_id
            JOIN legal_documents d ON d.id = a.document_id
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE c.id IN :chunk_ids
              AND d.status = 'active'
              AND a.status = 'active'
              {expanded_scope_clause}
            """
        ).bindparams(bindparam("chunk_ids", expanding=True))
        with self._engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    statement, {"chunk_ids": chunk_ids}
                ).mappings()
            ]

    def _fetch_fallback_chunks(
        self,
        phrases: list[str],
        domain: str | None,
        as_of: date,
        retrieval_tier: str,
        exclude_chunk_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        if not phrases:
            return []
        exclude_chunk_ids = exclude_chunk_ids or []
        normalized_phrases = [" ".join(_normalized_terms(phrase)) for phrase in phrases]
        scope_join = (
            "JOIN legal_search_scope search_scope "
            "ON search_scope.document_id = d.id AND search_scope.included = TRUE"
            if retrieval_tier == "core"
            else "LEFT JOIN legal_search_scope search_scope ON search_scope.document_id = d.id"
        )
        expanded_scope_clause = (
            "AND COALESCE(search_scope.reason, '') <> 'other_province'"
            if retrieval_tier == "expanded"
            else ""
        )
        domain_clause = ""
        params: dict[str, Any] = {"exclude_chunk_ids": exclude_chunk_ids}
        if domain:
            domain_clause = (
                " AND (g.group_slug IN :domains OR "
                "(g.group_slug IS NULL AND search_scope.domain IN :domains))"
            )
            params["domains"] = list(_domain_values(domain))
        statement = text(
            f"""
            SELECT
                c.id AS chunk_id,
                c.chunk_index,
                c.heading AS chunk_heading,
                c.content,
                a.id AS article_id,
                a.article_number,
                a.title AS article_title,
                a.status AS article_status,
                a.effective_from AS article_effective_from,
                a.effective_to AS article_effective_to,
                d.id AS document_id,
                d.title AS document_title,
                d.law_number,
                d.document_type,
                d.issuing_agency,
                d.scope,
                d.sector,
                d.status AS document_status,
                d.issued_date,
                d.effective_date,
                d.expired_date,
                d.source_url,
                d.field_id,
                f.name AS field_name,
                COALESCE(g.group_slug, search_scope.domain) AS domain_slug,
                g.group_name AS domain_name
            FROM legal_article_chunks c
            JOIN legal_articles a ON a.id = c.article_id
            JOIN legal_documents d ON d.id = a.document_id
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE c.id NOT IN :exclude_chunk_ids
              AND d.status = 'active'
              AND a.status = 'active'
              {expanded_scope_clause}
              {domain_clause}
            ORDER BY d.effective_date DESC NULLS LAST, c.id DESC
            LIMIT 6000
            """
        )
        if exclude_chunk_ids:
            statement = statement.bindparams(bindparam("exclude_chunk_ids", expanding=True))
        if domain:
            statement = statement.bindparams(bindparam("domains", expanding=True))
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not _is_current(row, as_of):
                continue
            haystack = " ".join(
                _normalized_terms(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("article_title", "chunk_heading", "content", "document_title")
                    )
                )
            )
            if any(phrase and phrase in haystack for phrase in normalized_phrases):
                matched_rows.append(row)
        return matched_rows[:20]

    def _fetch_relationships(
        self, document_ids: list[int]
    ) -> dict[int, list[dict[str, Any]]]:
        if not document_ids:
            return {}
        statement = text(
            """
            SELECT
                r.source_document_id,
                r.target_document_id,
                r.relationship_type,
                source.law_number AS source_law_number,
                source.title AS source_title,
                source.status AS source_status,
                source.effective_date AS source_effective_date,
                source.expired_date AS source_expired_date,
                target.law_number AS target_law_number,
                target.title AS target_title,
                target.status AS target_status,
                target.effective_date AS target_effective_date,
                target.expired_date AS target_expired_date
            FROM legal_document_relationships r
            LEFT JOIN legal_documents source ON source.id = r.source_document_id
            LEFT JOIN legal_documents target ON target.id = r.target_document_id
            WHERE r.source_document_id IN :document_ids
               OR r.target_document_id IN :document_ids
            """
        ).bindparams(bindparam("document_ids", expanding=True))
        with self._engine.connect() as connection:
            rows = connection.execute(
                statement, {"document_ids": sorted(set(document_ids))}
            ).mappings()
            result: dict[int, list[dict[str, Any]]] = {}
            requested = set(document_ids)
            for row in rows:
                for document_id, direction in (
                    (row["source_document_id"], "outgoing"),
                    (row["target_document_id"], "incoming"),
                ):
                    if document_id not in requested:
                        continue
                    related_prefix = "target" if direction == "outgoing" else "source"
                    result.setdefault(int(document_id), []).append(
                        {
                            "direction": direction,
                            "relationship_type": row["relationship_type"],
                            "related_document_id": row[
                                f"{related_prefix}_document_id"
                            ],
                            "related_law_number": row[
                                f"{related_prefix}_law_number"
                            ],
                            "related_title": row[f"{related_prefix}_title"],
                            "related_status": row[f"{related_prefix}_status"],
                            "related_effective_date": row[
                                f"{related_prefix}_effective_date"
                            ],
                            "related_expired_date": row[
                                f"{related_prefix}_expired_date"
                            ],
                        }
                    )
            return {
                document_id: relationships[:12]
                for document_id, relationships in result.items()
            }

    def health(self) -> dict[str, Any]:
        self.prewarm()
        with self._engine.connect() as connection:
            chunk_count = connection.execute(
                text("SELECT count(*) FROM legal_article_chunks")
            ).scalar_one()
        return {
            "status": "healthy",
            "ready": self._ready,
            "embedding_device": self._embedding_device.type,
            "embedding_dtype": str(self._embedding_dtype).replace("torch.", ""),
            "cuda_available": bool(torch.cuda.is_available()),
            "fallback_reason": self._embedding_fallback_reason,
            "model_fingerprint": self._model_fingerprint,
            "query_vector_cache": self._query_vector_cache.stats(),
            "collection": CHROMA_COLLECTION,
            "indexed_records": indexed_records,
            "database_chunks": chunk_count,
        }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_current(row: dict[str, Any], as_of: date) -> bool:
    if str(row.get("law_number") or "").strip() in EXPIRED_DOCUMENT_OVERRIDES:
        return False
    if str(row.get("article_status") or "").lower() != "active":
        return False
    if str(row.get("document_status") or "").lower() != "active":
        return False

    effective_date = row.get("effective_date")
    expired_date = row.get("expired_date")
    article_from = row.get("article_effective_from")
    article_to = row.get("article_effective_to")
    if effective_date and effective_date > as_of:
        return False
    if expired_date and expired_date <= as_of:
        return False
    if article_from and article_from > as_of:
        return False
    if article_to and article_to <= as_of:
        return False
    return True


def _has_expired_legal_basis(
    relationships: list[dict[str, Any]], as_of: date
) -> bool:
    """Reject documents that still rely on an expired legal basis.

    The imported corpus contains legacy local decisions marked ``active`` even
    though the decrees and circulars they implement have expired. In strict
    retrieval mode these documents are unsafe to cite as current authority.
    """
    for relationship in relationships:
        relation = " ".join(
            _normalized_terms(str(relationship.get("relationship_type") or ""))
        )
        if relation != "van ban can cu":
            continue
        if str(relationship.get("related_status") or "").casefold() == "expired":
            return True
        expired_date = relationship.get("related_expired_date")
        if expired_date and expired_date <= as_of:
            return True
    return False


def _priority_boost(row: dict[str, Any], metadata: dict[str, Any]) -> float:
    scope = str(row.get("scope") or metadata.get("scope") or "").casefold()
    agency = str(row.get("issuing_agency") or "").casefold()
    title = str(row.get("document_title") or "").casefold()
    level = str(row.get("official_level") or metadata.get("official_level") or "").casefold()
    combined = " ".join((scope, agency, title, level))

    boost = 0.0
    # 1. Ưu tiên đúng Phường Lê Chân
    if "lê chân" in combined or "le chan" in combined:
        boost += 0.08
    # 2. Ưu tiên địa phương Hải Phòng
    elif "hải phòng" in combined or "hai phong" in combined or scope == "haiphong":
        boost += 0.05
    # 3. Ưu tiên local chung (cấp phường/xã)
    elif scope == "local" or "cấp xã" in combined or "phường" in combined:
        boost += 0.03

    # 4. Ưu tiên văn bản QPPL chính thức
    if level == "official" or any(t in combined for t in ("nghị định", "thông tư", "luật")):
        boost += 0.03
    elif level == "internal":
        boost -= 0.02

    # 5. Phạt các địa phương khác không phải Hải Phòng
    if any(t in combined for t in ("tỉnh ", "thành phố ")) and not ("hải phòng" in combined or "hai phong" in combined):
        boost -= 0.05

    return boost


def _lexical_boost(query: str, row: dict[str, Any]) -> float:
    query_terms = {
        term
        for term in _normalized_terms(query)
        if len(term) >= 3 and term not in LEXICAL_STOPWORDS
    }
    if not query_terms:
        return 0.0
    candidate_text = " ".join(
        str(row.get(key) or "")
        for key in (
            "article_title",
            "chunk_heading",
            "content",
            "document_title",
            "law_number",
        )
    )
    candidate_terms = set(_normalized_terms(candidate_text))
    overlap = len(query_terms & candidate_terms)
    return min(overlap * 0.014, 0.07)


def _topic_boost(query: str, row: dict[str, Any]) -> float:
    """Prefer the current framework law for common ward-level procedures."""
    terms = set(_normalized_terms(query))
    query_text = " ".join(_normalized_terms(query))
    law_number = str(row.get("law_number") or "").strip()
    article_number = re.sub(
        r"\D+", "", str(row.get("article_number") or "")
    )
    candidate = " ".join(
        _normalized_terms(
            " ".join(
                str(row.get(key) or "")
                for key in ("article_title", "chunk_heading", "document_title")
            )
        )
    )

    is_land_dispute = (
        "dat" in terms
        and (
            "tranh chap" in query_text
            or "ranh gioi" in query_text
            or "hoa giai" in query_text
        )
    )
    if is_land_dispute and law_number == "31/2024/QH15":
        if "hoa giai" in candidate and article_number == "235":
            return 0.65
        return 0.18
    if is_land_dispute and "hoa giai thuong mai" in candidate:
        return -0.18
    if is_land_dispute and law_number in {
        "35/2013/QH13",
        "15/2014/NĐ-CP",
    }:
        return -0.08
    if "chung" in terms and "thuc" in terms and law_number == "23/2015/NĐ-CP":
        return 0.12
    is_civil_correction = (
        (
            "cai chinh" in query_text
            or "sua ngay sinh" in query_text
            or "ghi sai ngay sinh" in query_text
        )
        and (
            "ho tich" in query_text
            or "ngay sinh" in query_text
            or "giay khai sinh" in query_text
        )
    )
    is_birth_registration = (
        "dang ky khai sinh" in query_text
        or (
            "chua co giay khai sinh" in query_text
            and ("con toi" in query_text or "tre" in terms)
        )
    )
    is_residence_registration = (
        "tam tru" in query_text
        or "dang ky cu tru" in query_text
        or "thong bao luu tru" in query_text
    )
    if is_residence_registration:
        # The Residence Law is the primary source for ward-level residence
        # procedures. Without this boost, broad 2025 administrative documents
        # can outrank the directly applicable law.
        if law_number == "14/2020/QH14":
            return 0.55
        if law_number in {"62/2021/NĐ-CP", "62/2021/ND-CP"}:
            return 0.22
        if "cu tru" in candidate or "tam tru" in candidate:
            return 0.08
        return -0.04
    is_death_registration = (
        "dang ky khai tu" in query_text
        or "khai tu" in query_text
    )
    is_marital_status_certificate = (
        "xac nhan tinh trang hon nhan" in query_text
        or "giay xac nhan tinh trang hon nhan" in query_text
    )
    if (
        is_civil_correction
        or is_birth_registration
        or is_death_registration
        or is_marital_status_certificate
    ):
        if law_number == "60/2014/QH13":
            if is_civil_correction and article_number in {"46", "47"}:
                return 0.28
            if is_birth_registration and article_number in {"13", "15", "16"}:
                # Direct authority/procedure provisions must outrank unrelated
                # documents when the question also mentions fees or forms.
                return 0.55
            if is_death_registration and article_number in {"49"}:
                return 0.35
            if is_marital_status_certificate and article_number in {"21", "22"}:
                return 0.22
            return 0.11
        if law_number == "123/2015/NĐ-CP":
            return 0.07
        if law_number == "04/2020/TT-BTP":
            return 0.06
        if law_number == "15/2015/TT-BTP":
            return 0.05
    if {"cu", "tru"} <= terms and law_number == "55/2021/TT-BCA":
        return 0.12
    return 0.0


def _normalized_terms(value: str) -> list[str]:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    ascii_like = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    ascii_like = ascii_like.replace("\u0111", "d")
    return re.findall(r"[a-z0-9]+", ascii_like)


def _fallback_phrases(query: str) -> tuple[list[str], str | None]:
    query_text = " ".join(_normalized_terms(query))
    if (
        "dang ky tam tru" in query_text
        or "tam tru" in query_text
        or "dang ky cu tru" in query_text
        or "thong bao luu tru" in query_text
    ):
        return [
            "đăng ký tạm trú",
            "tạm trú",
            "đăng ký cư trú",
            "Thông tư 55/2021/TT-BCA",
            "Luật Cư trú",
        ], "cu_tru_an_ninh"
    if "dang ky khai tu" in query_text or "khai tu" in query_text:
        return ["khai tu", "dang ky khai tu"], "tu_phap_ho_tich"
    if (
        "xac nhan tinh trang hon nhan" in query_text
        or "giay xac nhan tinh trang hon nhan" in query_text
        or "tinh trang hon nhan" in query_text
    ):
        return [
            "giay xac nhan tinh trang hon nhan",
            "xac nhan tinh trang hon nhan",
            "tinh trang hon nhan",
        ], "tu_phap_ho_tich"
    if "dang ky khai sinh" in query_text or "khai sinh" in query_text:
        return ["dang ky khai sinh", "khai sinh"], "tu_phap_ho_tich"
    return [], None


def _iso_or_none(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _detected_domain(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    counts: dict[str, dict[str, Any]] = {}
    for item in results:
        slug = item.get("domain_slug")
        if not slug:
            continue
        counts.setdefault(
            slug,
            {"slug": slug, "name": item.get("domain_name"), "count": 0},
        )
        counts[slug]["count"] += 1
    if not counts:
        return None
    return max(counts.values(), key=lambda item: item["count"])


def _chunk_trace_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": item.get("chunk_id"),
        "score": item.get("score"),
        "vector_score": item.get("vector_score"),
        "bm25_score": item.get("bm25_score"),
        "metadata_score": item.get("metadata_score"),
        "domain": item.get("domain_name"),
        "field": item.get("field_name"),
        "law_number": item.get("law_number"),
        "document_title": item.get("document_title"),
        "article_number": item.get("article_number"),
        "article_title": item.get("article_title"),
        "content_preview": str(item.get("content") or "")[:500],
    }


def _source_trace_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": f"legal:{item.get('chunk_id')}",
        "law_number": item.get("law_number"),
        "document_title": item.get("document_title"),
        "article_number": item.get("article_number"),
        "article_title": item.get("article_title"),
        "source_url": item.get("source_url"),
    }


app = FastAPI(
    title="VNLegal-LAL Hai Phong Retrieval Service",
    version="1.0.0",
)
retriever = LegalRetriever()

_pdf_artifact_lock = Lock()
_pdf_generation_events: dict[str, Event] = {}


def _pdf_artifact_path(doc_id: str, article: str | None) -> Path:
    safe_doc_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(doc_id)).strip("-") or "document"
    safe_article = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(article or "all")).strip("-") or "all"
    return LEGAL_PDF_ARTIFACT_DIR / f"{safe_doc_id}-{safe_article}.pdf"


def _cached_pdf_artifact(doc_id: str, article: str | None) -> tuple[Path, bool]:
    """Return an internal export artifact and whether it was cached."""
    artifact = _pdf_artifact_path(doc_id, article)
    try:
        if artifact.is_file() and (datetime.now().timestamp() - artifact.stat().st_mtime) < LEGAL_PDF_CACHE_MAX_AGE_SECONDS:
            return artifact, True
    except OSError:
        pass
    cache_key = str(artifact)
    with _pdf_artifact_lock:
        event = _pdf_generation_events.get(cache_key)
        if event is None:
            event = Event()
            _pdf_generation_events[cache_key] = event
            is_generator = True
        else:
            is_generator = False
    if not is_generator:
        event.wait(timeout=120)
        if artifact.is_file():
            return artifact, True
        raise RuntimeError("PDF artifact generation did not complete.")
    try:
        LEGAL_PDF_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        payload, _filename = retriever.document_pdf(doc_id, article=article)
        temporary = artifact.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(artifact)
        return artifact, False
    finally:
        with _pdf_artifact_lock:
            completed = _pdf_generation_events.pop(cache_key, None)
            if completed:
                completed.set()


# TTL cache for document metadata (active indexed docs only)
_document_cache: dict[str, dict] = {}
_document_cache_ttl: dict[str, float] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

def _invalidate_document_cache(doc_id: str) -> None:
    """Remove a document from cache when it changes."""
    _document_cache.pop(doc_id, None)
    _document_cache_ttl.pop(doc_id, None)

def _get_cached_document(doc_id: str) -> dict | None:
    """Return cached document if fresh."""
    import time
    if doc_id in _document_cache:
        ts = _document_cache_ttl.get(doc_id, 0)
        if time.time() - ts < _CACHE_TTL_SECONDS:
            return _document_cache[doc_id]
    return None

def _set_cached_document(doc_id: str, data: dict) -> None:
    """Cache document metadata."""
    import time
    _document_cache[doc_id] = data
    _document_cache_ttl[doc_id] = time.time()



@app.get("/health")
def health() -> dict[str, Any]:
    try:
        return retriever.health()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    try:
        return retriever.search(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/import/fields")
def import_fields() -> dict[str, Any]:
    try:
        return {"fields": retriever.fields()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/domains")
def domains() -> dict[str, Any]:
    try:
        return {"domains": retriever.domains()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/documents")
def list_documents(
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: date = Query(default_factory=date.today),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    try:
        return retriever.list_documents(
            query=q,
            domain=domain,
            tier=tier,
            as_of=as_of,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@app.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    response: Response,
    article: str | None = None,
    include_content: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        payload = {"document": retriever.document_detail(
            doc_id, article=article, include_content=include_content
        )}
        response.headers["X-Legal-View-Ms"] = str(round((perf_counter() - started) * 1000, 1))
        response.headers["Cache-Control"] = "private, max-age=300" if not include_content and not article else "no-store"
        return payload
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản pháp lý trong kho nội bộ.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/documents/{doc_id}/download.pdf")
async def download_document_pdf(doc_id: str, article: str | None = None) -> Response:
    """Serve a cached extract without blocking the async request loop."""
    started = perf_counter()
    try:
        from fastapi.responses import FileResponse

        artifact, cache_hit = await asyncio.to_thread(_cached_pdf_artifact, doc_id, article)
        duration_ms = round((perf_counter() - started) * 1000, 1)
        return FileResponse(
            path=str(artifact),
            media_type="application/pdf",
            filename=artifact.name,
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Legal-Pdf-Origin": "system-extract",
                "X-Legal-Pdf-Cache": "hit" if cache_hit else "miss",
                "X-Legal-Export-Ms": str(duration_ms),
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản pháp lý trong kho nội bộ.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/documents/lookup")
def lookup_document(law_number: str) -> dict[str, Any]:
    cleaned = str(law_number or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="law_number is required")
    try:
        with retriever._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        title,
                        law_number,
                        document_type,
                        issuing_agency,
                        scope,
                        source_url,
                        effective_date,
                        expired_date,
                        status,
                        field_id
                    FROM legal_documents
                    WHERE lower(trim(law_number)) = lower(trim(:law_number))
                    LIMIT 1
                    """
                ),
                {"law_number": cleaned},
            ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "document": {
                "id": row["id"],
                "title": row["title"],
                "law_number": row["law_number"],
                "document_type": row.get("document_type"),
                "issuing_agency": row.get("issuing_agency"),
                "scope": row.get("scope"),
                "source_url": row.get("source_url"),
                "effective_date": _iso_or_none(row.get("effective_date")),
                "expired_date": _iso_or_none(row.get("expired_date")),
                "status": row.get("status"),
                "field_id": row.get("field_id"),
            }
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/import/preview")
def preview_import(request: LegalImportRequest) -> dict[str, Any]:
    try:
        return retriever.preview_import(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/import/unstructured")
def import_unstructured_document(request: LegalImportRequest) -> dict[str, Any]:
    """Import documents without 'Dieu N.' by chunking paragraphs/sections."""
    forced = request.model_copy(update={"structure": "unstructured"})
    try:
        return retriever.import_document(forced)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/import")
def import_document(request: LegalImportRequest) -> dict[str, Any]:
    try:
        return retriever.import_document(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("LEGAL_SEARCH_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port)
