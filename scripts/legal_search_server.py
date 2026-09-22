"""Local VNLegal-LAL retrieval service for the Hai Phong legal assistant."""

from __future__ import annotations

import asyncio
import copy
import gc
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event, Lock, RLock, local
from time import perf_counter
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlparse

# Direct execution sets ``sys.path[0]`` to ``scripts`` rather than the
# repository root.  Batch retrieval imports its deterministic quality policy
# from ``api`` at request time, so keep the supported ``python scripts/...``
# launcher able to resolve the same modules as test/module execution.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import chromadb
import httpx
import numpy as np
import torch
import torch.nn.functional as F
from dotenv import dotenv_values, load_dotenv
load_dotenv()
from fastapi import FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import ARRAY, Integer, bindparam, create_engine, text
from transformers import AutoConfig, AutoModel, AutoTokenizer

from api.data_paths import notebook_data_dir
from api.legal_admin_overlay_snapshot import (
    OverlaySnapshotCollection,
    default_overlay_snapshot_path,
    reconcile_collection_from_snapshot,
    write_overlay_snapshot,
)
from api.legal_document_identity import compare_legal_documents, number_search_key
from api.legal_document_serving_state import (
    inventory_projection,
    DocumentServingStateStore,
    ReplacementActivationError,
    rollback_action_for_transition,
    serving_projection,
    state_revision,
    transition_verification,
)
from api.legal_post_activation_smoke import verify_post_activation_retrieval
from api.legal_exact_article import (
    attach_exact_article_packet,
    build_exact_article_serving_packet,
    is_single_exact_article_plan,
    public_exact_article_packet,
)
from api.legal_determinism import build_runtime_version_trace
from api.legal_hierarchy import hierarchy_summary, rank_legal_evidence
from api.legal_domains import CANONICAL_DOMAIN_ALIASES, canonicalize_legal_domain, legal_domain_values
from api.legal_learned_reranker import OptionalCrossEncoderReranker
from api.legal_parent_context import hydrate_parent_context
from api.legal_query_understanding import (
    classify_legal_query,
    validate_m4_query_classification,
)
from api.legal_retrieval_trace import M3_TRACE_SCHEMA_VERSION
from api.legal_serving_scope import serving_scope_from_environment
from api.legal_structural_chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SPLIT_THRESHOLD,
    parse_structural_parents,
    parse_structural_path,
    split_parent_children,
)
from api.legal_validity_registry import (
    apply_validity_overlay,
    default_snapshot_cache,
    project_validity_for_row,
)
from api.legal_validity_models import vietnam_legal_date
from api.legal_vector_cleanup import (
    VectorCleanupManifestStore,
    build_vector_cleanup_manifest,
    execute_exact_vector_cleanup,
)
from api.legal_serving_dashboard import load_active_serving_release
from api.retrieval_release_contracts import require_staging_collection_target

logger = logging.getLogger(__name__)

try:
    from scripts.legal_retrieval_cache import (
        ExactRowsCache,
        QueryVectorCache,
        exact_rows_cache_key,
        query_vector_cache_key,
    )
except ModuleNotFoundError:  # Support ``python scripts/legal_search_server.py``.
    from legal_retrieval_cache import (
        ExactRowsCache,
        QueryVectorCache,
        exact_rows_cache_key,
        query_vector_cache_key,
    )

try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover - optional dependency may be unavailable
    BM25Okapi = None


def _resolve_data_root() -> Path:
    configured = os.getenv("LEGAL_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    repo_release_data = REPO_ROOT / "release-data" / "legal"
    if (repo_release_data / "chroma_store").is_dir():
        return repo_release_data
    return Path(r"D:\legal-chatbot-data")


def _resolve_model_path(data_root: Path) -> Path:
    configured = os.getenv("VNLEGAL_LAL_MODEL_PATH", "").strip()
    if configured:
        return Path(configured)
    if (data_root / "vnlegal-lal-model").is_dir():
        return data_root / "vnlegal-lal-model"
    if (REPO_ROOT / "release-data" / "legal" / "vnlegal-lal-model").is_dir():
        return REPO_ROOT / "release-data" / "legal" / "vnlegal-lal-model"
    return (
        data_root
        / "sentence_transformers"
        / "models--darklethelong--vnlegal-lal"
        / "snapshots"
        / "de759324ef931a2475ae8db97137b6a6cbb98aa0"
    )


DATA_ROOT = _resolve_data_root()
CHROMA_PATH = Path(os.getenv("LEGAL_CHROMA_PATH", str(DATA_ROOT / "chroma_store")))
DEFAULT_CHROMA_COLLECTION = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
CHROMA_ACTIVE_COLLECTION_FILE = CHROMA_PATH / "active_core_collection.txt"
CHROMA_TEMPORAL_COLLECTION = os.getenv("LEGAL_CHROMA_TEMPORAL_COLLECTION", "").strip()
CHROMA_INCREMENTAL_COLLECTION = os.getenv(
    "LEGAL_CHROMA_INCREMENTAL_COLLECTION", ""
).strip()
CHROMA_INCREMENTAL_METADATA = {
    "hnsw:space": "cosine",
    # Local Chroma must flush small Admin-approved batches. The default
    # threshold (1000) leaves a one-document approval only in the WAL and it
    # disappears after a retrieval restart.
    "hnsw:sync_threshold": 3,
}
ADMIN_OVERLAY_SNAPSHOT_PATH = default_overlay_snapshot_path(CHROMA_PATH)


def _overlay_cache_revision() -> str:
    """Return a cheap cross-process revision for mutable overlay cache keys."""

    try:
        stat = ADMIN_OVERLAY_SNAPSHOT_PATH.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return "snapshot-missing"


def _publish_incremental_overlay_snapshot(collection: Any) -> dict[str, Any]:
    if collection is None or not CHROMA_INCREMENTAL_COLLECTION:
        raise RuntimeError("incremental_collection_unavailable")
    # Some compatible collection adapters (and narrow unit-test doubles) can
    # accept add/update operations but cannot enumerate the full collection.
    # The snapshot is a cross-process freshness accelerator, not the source of
    # truth for import. Production Chroma implements ``get``; adapters that do
    # not must keep the import path usable and report the skipped publication.
    if not callable(getattr(collection, "get", None)):
        return {
            "status": "skipped",
            "reason": "collection_snapshot_read_unavailable",
            "collection": CHROMA_INCREMENTAL_COLLECTION,
        }
    return write_overlay_snapshot(
        collection,
        path=ADMIN_OVERLAY_SNAPSHOT_PATH,
        collection_name=CHROMA_INCREMENTAL_COLLECTION,
    )


def _active_chroma_collection_name() -> str:
    configured = os.getenv("LEGAL_CHROMA_COLLECTION", "").strip()
    if configured:
        return configured
    try:
        pointed = CHROMA_ACTIVE_COLLECTION_FILE.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", pointed):
            return pointed
    except OSError:
        pass
    return DEFAULT_CHROMA_COLLECTION


CHROMA_COLLECTION = _active_chroma_collection_name()
CHROMA_SOURCE_COLLECTION = os.getenv(
    "LEGAL_CHROMA_SOURCE_COLLECTION",
    CHROMA_COLLECTION,
)


def _legacy_direct_import_enabled() -> bool:
    """Return whether the pre-V3 direct active-collection import is allowed.

    New corpus changes must go through an immutable staging release.  The
    environment switch exists only for isolated legacy compatibility tests or
    an explicitly approved rollback operation; it is deliberately disabled by
    default and is not part of the live ``.env``.
    """

    return os.getenv("LEGAL_ALLOW_LEGACY_DIRECT_IMPORT", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _incremental_import_enabled() -> bool:
    """Allow reviewed local imports only into a non-active collection."""

    enabled = os.getenv("LEGAL_ALLOW_INCREMENTAL_IMPORT", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not enabled:
        return False
    if not CHROMA_INCREMENTAL_COLLECTION:
        raise RuntimeError("incremental_collection_not_configured")
    require_staging_collection_target(CHROMA_INCREMENTAL_COLLECTION, CHROMA_PATH)
    if CHROMA_INCREMENTAL_COLLECTION in {CHROMA_COLLECTION, CHROMA_SOURCE_COLLECTION}:
        raise RuntimeError("incremental_collection_must_differ_from_serving_collections")
    return True


def _configure_incremental_collection(collection: Any) -> Any:
    """Apply a small persistence threshold without touching the baseline."""
    if collection is None:
        return collection
    try:
        # Chroma Rust stores HNSW tuning in the collection configuration, not
        # only in the legacy metadata map. Updating configuration is supported
        # for existing collections and makes small one-document approvals
        # durable across a retrieval restart.
        collection.modify(
            configuration={
                "hnsw": {
                    "sync_threshold": CHROMA_INCREMENTAL_METADATA["hnsw:sync_threshold"],
                }
            }
        )
    except Exception as exc:
        logger.warning("Unable to configure local overlay persistence threshold: %s", exc)
    return collection


def _merge_vector_query_payloads(
    payloads: Sequence[Mapping[str, Any]], *, n_results: int
) -> dict[str, list[list[Any]]]:
    """Merge Chroma query rows by distance while removing duplicate chunk ids."""

    row_count = max((len(payload.get("ids") or []) for payload in payloads), default=0)
    merged = {"ids": [], "metadatas": [], "distances": []}
    for row_index in range(row_count):
        best: dict[str, tuple[float, dict[str, Any]]] = {}
        for payload in payloads:
            ids_rows = payload.get("ids") or []
            metadata_rows = payload.get("metadatas") or []
            distance_rows = payload.get("distances") or []
            if row_index >= len(ids_rows):
                continue
            ids = list(ids_rows[row_index] or [])
            metadatas = list(metadata_rows[row_index] or []) if row_index < len(metadata_rows) else []
            distances = list(distance_rows[row_index] or []) if row_index < len(distance_rows) else []
            for offset, item_id in enumerate(ids):
                distance = float(distances[offset]) if offset < len(distances) else 1.0
                metadata = dict(metadatas[offset] or {}) if offset < len(metadatas) else {}
                key = str(item_id)
                current = best.get(key)
                if current is None or distance < current[0]:
                    best[key] = (distance, metadata)
        ordered = sorted(best.items(), key=lambda item: (item[1][0], item[0]))[:n_results]
        merged["ids"].append([item_id for item_id, _ in ordered])
        merged["metadatas"].append([value[1] for _, value in ordered])
        merged["distances"].append([value[0] for _, value in ordered])
    return merged
LEGAL_VECTOR_CLEANUP_DIR = (
    notebook_data_dir() / "operations" / "legal-vector-cleanup"
)
MODEL_PATH = _resolve_model_path(DATA_ROOT)
OLD_ENV_PATH = Path(
    os.getenv(
        "LEGAL_OLD_ENV_PATH",
        r"J:\ChatBot\legal-chatbot\backend\.env",
    )
)
_batch_vector_context = local()
REPO_ENV_PATH = REPO_ROOT / ".env"
QUERY_PREFIX = (
    "Instruct: Given a Vietnamese legal question, retrieve relevant legal "
    "passages that answer the question\nQuery: "
)

# Noto Sans is distributed with this repository under the SIL Open Font License
# 1.1; see assets/fonts/noto-sans/OFL.txt. It contains Vietnamese glyphs and is
# embedded explicitly into every internally-generated PDF.
LEGAL_PDF_FONT_PATH = REPO_ROOT / "assets" / "fonts" / "noto-sans" / "NotoSans-VF.ttf"
LEGAL_PDF_ARTIFACT_DIR = Path(os.getenv("LEGAL_PDF_ARTIFACT_DIR", str(DATA_ROOT / "pdf_artifacts")))
LEGAL_PDF_CACHE_MAX_AGE_SECONDS = int(os.getenv("LEGAL_PDF_CACHE_MAX_AGE_SECONDS", "86400"))
PARENT_CONTEXT_PER_PARENT_CHAR_LIMIT = int(
    os.getenv("LEGAL_PARENT_CONTEXT_MAX_CHARS", "12000")
)
PARENT_CONTEXT_TOTAL_CHAR_LIMIT = int(
    os.getenv("LEGAL_PARENT_CONTEXT_TOTAL_CHARS", "36000")
)
CORE_BATCH_CANDIDATE_COUNT = int(
    os.getenv("LEGAL_CORE_BATCH_CANDIDATE_COUNT", "30")
)
EXPANDED_BATCH_CANDIDATE_COUNT = int(
    os.getenv("LEGAL_EXPANDED_BATCH_CANDIDATE_COUNT", "32")
)
BATCH_RESULT_LIMIT = max(1, int(os.getenv("LEGAL_BATCH_RESULT_LIMIT", "8")))
BATCH_LEXICAL_MODE = os.getenv("LEGAL_BATCH_LEXICAL_MODE", "adaptive").strip().lower()
MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00c6", "\u00c4", "\u00e1\u00ba", "\u00e1\u00bb")


def _rewrite_query(query: str) -> str:
    # Some legacy fixtures/imported questions still contain double-encoded
    # Vietnamese. Repair only the request text before deterministic routing;
    # corpus metadata remains untouched.
    query = _repair_mojibake_text(str(query or ""))
    normalized = " ".join(_normalized_terms(query))
    if (
        (
            "khong co bien ban" in normalized
            or "khong lap bien ban" in normalized
        )
        and "phat" in normalized
        and "hanh chinh" in normalized
    ):
        return (
            query
            + " dieu 56 15/2012/QH13 118/2021/ND-CP 02/2011/QH13"
        )
    if (
        "khieu nai" in normalized
        and (
            "do xe" in normalized
            or "dau xe" in normalized
            or ("xe" in normalized and "dau" in normalized)
        )
        and "phat" in normalized
    ):
        return query + " 100/2019/ND-CP 02/2011/QH13"
    if (
        ("do xe" in normalized or "dau xe" in normalized)
        and "via he" in normalized
    ):
        return query + " 36/2024/QH15 168/2024/ND-CP"
    if "khieu nai" in normalized and "phat hanh chinh" in normalized:
        return query + " 02/2011/QH13 15/2012/QH13"
    if (
        "khieu nai" in normalized
        and "lan dau" in normalized
        and any(
            marker in normalized
            for marker in ("quyet dinh hanh chinh", "hanh vi hanh chinh")
        )
    ):
        # Keep the lookup deterministic and bounded to the current framework
        # law and the first-instance competence provisions.  This does not
        # synthesize an authority; the returned passage remains subject to the
        # normal active-source and grounding gates.
        return (
            query
            + " 02/2011/QH13 dieu 17 dieu 18 tham quyen "
            "quyet dinh hanh chinh hanh vi hanh chinh"
        )
    if (
        "lan ra via he" in normalized
        or "chiem dung he pho" in normalized
        or "khung sat" in normalized
    ):
        return query + " 167/2013/ND-CP 123/2021/ND-CP"
    if "ban hang rong" in normalized and "via he" in normalized:
        return query + " 100/2019/ND-CP"
    if (
        "sang ten so do" in normalized
        or "sang ten nha dat" in normalized
        or (
            "sang ten" in normalized
            and any(
                marker in normalized
                for marker in ("nha dat", "giay chung nhan", "quyen su dung dat")
            )
        )
        or (
            "chuyen nhuong" in normalized
            and "giay chung nhan" in normalized
        )
        or "dang ky bien dong dat dai" in normalized
        or ("the chap" in normalized and "chua sang ten" in normalized)
    ):
        return (
            query
            + " chuyen nhuong quyen su dung dat dang ky bien dong ho so "
            "31/2024/QH15 101/2024/ND-CP"
        )
    if (
        any(
            marker in normalized
            for marker in ("xay dung", "dang xay", "xay nha")
        )
        and (
            "khong phep" in normalized
            or "khong co giay phep" in normalized
            or "xay trai phep" in normalized
        )
    ):
        return query + " dieu 16 15/2012/QH13 16/2022/ND-CP 50/2014/QH13"
    if (
        any(
            marker in normalized
            for marker in (
                "giay phep xay dung",
                "xin phep xay dung",
                "cap phep xay dung",
                "xin giay phep xay dung",
                "cap giay phep xay dung",
            )
        )
        or (
            any(marker in normalized for marker in ("xay dung", "xay nha", "xay nha o"))
            and any(marker in normalized for marker in ("nha o", "nha o rieng le", "do thi"))
            and any(marker in normalized for marker in ("xin phep", "giay phep", "nop o dau", "co can"))
        )
    ):
        return query + " 50/2014/QH13 175/2024/ND-CP dieu 89 dieu 93 dieu 102 tham quyen ubnd cap xa ubnd cap huyen"
    if (
        "lien thong" in normalized
        and any(
            marker in normalized
            for marker in ("khai sinh", "thuong tru", "bao hiem y te", "bhyt", "tre em", "moi sinh")
        )
    ):
        return (
            query
            + " lien thong thu tuc hanh chinh dang ky khai sinh dang ky thuong tru "
            "cap the bao hiem y te 60/2014/QH13 68/2020/QH14 26/2023/QH15"
        )
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
    if (
        "dang ky khai sinh" in normalized
        or normalized == "khai sinh"
        or (
            "tre sinh" in normalized
            and any(
                marker in normalized
                for marker in ("nuoc ngoai", "dieu 13", "dieu 35")
            )
        )
    ):
        if "nuoc ngoai" in normalized or "sinh o nuoc ngoai" in normalized:
            return (
                query
                + " dieu 13 dieu 35 luat ho tich 60/2014/QH13 "
                "nghi dinh 123/2015/ND-CP"
            )
        return (
            query
            + " ho tich tu phap ubnd cap xa khai sinh "
            "dieu 13 dieu 16 60/2014/QH13"
        )
    if (
        "xac nhan tinh trang hon nhan" in normalized
        or "giay xac nhan tinh trang hon nhan" in normalized
        or "tinh trang hon nhan" in normalized
    ):
        return (
            query
            + " ho tich tu phap ubnd cap xa xac nhan tinh trang hon nhan "
            "dieu 21 dieu 22 dieu 23 60/2014/QH13 123/2015/ND-CP"
        )
    if "dang ky ket hon" in normalized or normalized == "ket hon":
        if (
            "nuoc ngoai" in normalized
            or "nguoi nuoc ngoai" in normalized
            or "yeu to nuoc ngoai" in normalized
        ):
            return (
                query
                + " ho tich tu phap dang ky ket hon co yeu to nuoc ngoai "
                "dieu 37 dieu 38 60/2014/QH13 123/2015/ND-CP"
            )
        return (
            query
            + " ho tich tu phap ubnd cap xa dang ky ket hon "
            "dieu 17 dieu 18 60/2014/QH13 123/2015/ND-CP"
        )
    if "chung thuc ban sao" in normalized or (
        "chung thuc" in normalized and "ban sao" in normalized
    ):
        return query + " chung thuc ban sao ubnd cap xa 23/2015/ND-CP"
    if "cai chinh" in normalized and any(
        marker in normalized for marker in ("ho tich", "ngay sinh", "khai sinh")
    ):
        return (
            query
            + " cai chinh ho tich ngay sinh 60/2014/QH13 "
            "123/2015/ND-CP"
        )
    if (
        "cccd" in normalized
        or "the can cuoc" in normalized
        or "cap can cuoc" in normalized
    ):
        return query + " 26/2023/QH15 17/2024/TT-BCA"
    if (
        "trich luc" in normalized
        or "ban sao trich luc" in normalized
        or ("trich luc" in normalized and "khai sinh" in normalized)
    ):
        return (
            query
            + " cap ban sao trich luc ho tich khai sinh le phi thoi han "
            "dieu 63 dieu 64 60/2014/QH13 123/2015/ND-CP 23/2015/ND-CP"
        )
    if (
        any(marker in normalized for marker in ("chua co so", "chua co giay chung nhan", "lan dau", "lau nam"))
        and any(marker in normalized for marker in ("cap giay", "so do", "quyen su dung dat", "dat dai"))
    ):
        return (
            query
            + " cap giay chung nhan lan dau dieu 137 dieu 138 dieu 148 "
            "31/2024/QH15 101/2024/ND-CP ho so mau don"
        )
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
    # High-frequency legal boilerplate. These words made the trigram OR
    # predicate scan a large fraction of the corpus for generated/planner
    # scaffolding (for example ``ngÆ°á»i ... Äiá»u ... quyáº¿t Äá»nh``),
    # while the vector and exact-identity paths already preserve their signal.
    "chung",
    "dieu",
    "dinh",
    "dung",
    "hoi",
    "luat",
    "nguoi",
    "phan",
    "quyet",
    "thong",
}
LEXICAL_MATCH_LIMIT = 60
LEXICAL_TERM_LIMIT = int(os.getenv("LEGAL_LEXICAL_TERM_LIMIT", "4"))
LEXICAL_GENERIC_TERMS = {
    "chuong",
    "cong",
    "dung",
    "dieu",
    "dinh",
    "nghiep",
    "nguoi",
    "nhiem",
    "quyet",
    "thong",
    "trinh",
}
RERANK_WINDOW = 40
QUERY_VECTOR_CACHE_TTL_SECONDS = float(
    os.getenv("LEGAL_RETRIEVAL_CACHE_TTL_SECONDS", "300")
)
QUERY_VECTOR_CACHE_MAX_ENTRIES = int(
    os.getenv("LEGAL_RETRIEVAL_CACHE_MAX_ENTRIES", "1024")
)
EXACT_ROWS_CACHE_TTL_SECONDS = float(
    os.getenv("LEGAL_EXACT_ROWS_CACHE_TTL_SECONDS", "300")
)
EXACT_ROWS_CACHE_MAX_ENTRIES = int(
    os.getenv("LEGAL_EXACT_ROWS_CACHE_MAX_ENTRIES", "256")
)
EXPIRED_DOCUMENT_OVERRIDES = {
    # Corpus metadata incorrectly marks these superseded instruments active.
    "4/CP",
    "05/2012/NQ-HĐTP",
    "45/2013/QH13",
}


def _database_url() -> str:
    direct = os.getenv("LEGAL_DATABASE_URL", "").strip()
    if direct:
        return direct.replace("host.docker.internal", "127.0.0.1")

    # Read the repository release configuration on every startup.  This keeps
    # the retrieval service aligned with the project-local deployment config
    # and avoids silently falling back to a hard-coded connection when the
    # process inherited an older environment.
    repo_values = dotenv_values(REPO_ENV_PATH) if REPO_ENV_PATH.is_file() else {}
    configured = str(repo_values.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    if not configured:
        legacy_values = (
            dotenv_values(OLD_ENV_PATH) if OLD_ENV_PATH.is_file() else {}
        )
        configured = str(
            legacy_values.get("LEGAL_RELEASE_DATABASE_URL") or ""
        ).strip()
    if not configured:
        configured = os.getenv("LEGAL_RELEASE_DATABASE_URL", "").strip()
    if configured:
        return configured.replace("host.docker.internal", "127.0.0.1")
    return "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/legal_chatbot"


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=8, ge=1, le=30)
    candidate_count: int = Field(default=150, ge=10, le=500)
    lexical_candidate_count: int = Field(default=60, ge=0, le=120)
    as_of: date = Field(default_factory=date.today)
    temporal_scope: Literal["current", "historical"] | None = None
    document_id: int | None = Field(default=None, ge=1)
    full_document: bool = False
    # Internal propagation hint for batch requests. Direct requests derive
    # explicitness from Pydantic's model_fields_set; this field does not relax
    # the temporal guard and is excluded from serialized contracts.
    as_of_explicit: bool | None = Field(default=None, exclude=True)
    # Retrieval benchmarks replay a legally frozen dataset at its own clock.
    # This hint is honored only in LEGAL_BENCHMARK_MODE and is never part of
    # the public serialized contract.
    benchmark_today: date | None = Field(default=None, exclude=True)
    domain: str | None = Field(default=None, max_length=80)
    # Client-side scope preference used for tracing/routing. The legal domain
    # filter remains authoritative; accepting this field keeps older/newer
    # clients compatible instead of failing validation with HTTP 422.
    scope_filter: str | None = Field(default=None, max_length=32)
    # ``core`` is the reviewed Hai Phong/commune index. ``expanded`` queries
    # the active source corpus only when the caller has insufficient evidence
    # from the core index. This is routing metadata, not a client safety bypass.
    retrieval_tier: str = Field(default="core", pattern="^(core|expanded)$")
    audience: str = Field(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    )
    organization_unit_id: str | None = Field(default=None, max_length=120)
    # Backend-computed scope reused by batch workers. These fields never cross
    # the public wire contract and avoid repeating the same assignment query
    # for every query variant in one chatbot turn.
    organization_scope_document_ids: set[int] | None = Field(
        default=None, exclude=True
    )
    organization_scope_chunk_ids: set[int] | None = Field(
        default=None, exclude=True
    )
    include_trace: bool = False
    allow_broad_fallback: bool = True
    ranking_strategy: str = Field(
        default="legacy_stack", pattern="^(legacy_stack|rrf_v2)$"
    )
    fusion_strategy: str = Field(
        default="legacy_stack", pattern="^(legacy_stack|rrf|weighted)$"
    )
    vector_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    lexical_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    enable_learned_reranker: bool = True
    rerank_top_n: int = Field(default=40, ge=1, le=100)
    enable_parent_expansion: bool = True
    enable_neighbor_expansion: bool = True
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
    query_id: str | None = Field(
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
    facets: list[str] = Field(default_factory=list, max_length=12)
    subject_anchor: str | None = Field(default=None, max_length=1000)
    procedure_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_fusion_weights(self) -> "SearchRequest":
        if self.fusion_strategy == "weighted" and abs(
            self.vector_weight + self.lexical_weight - 1.0
        ) > 1e-9:
            raise ValueError("fusion_weights_must_sum_to_one")
        return self


class BatchSearchQuery(BaseModel):
    query_id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    query_type: str = Field(default="semantic", max_length=40)
    query: str = Field(min_length=2, max_length=2000)
    scope: str | None = Field(default=None, max_length=32)
    expected_article: str | None = Field(default=None, max_length=80)


class BatchSearchIssue(BaseModel):
    issue_id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    query: str | None = Field(default=None, min_length=2, max_length=2000)
    # One request-local query is generated for each requested facet.  Keep the
    # wire contract aligned with LegalQueryDecisionV1 (documents, authority,
    # deadline, condition, form, legal basis, process and next action).
    queries: list[BatchSearchQuery] = Field(default_factory=list, max_length=8)
    domain: str | None = Field(default=None, max_length=80)
    intent: str = Field(default="unknown", max_length=40)
    facets: list[str] = Field(default_factory=list, max_length=12)
    subject_anchor: str | None = Field(default=None, max_length=1000)
    procedure_id: str | None = Field(default=None, max_length=120)
    document_id: int | None = Field(default=None, ge=1)
    full_document: bool = False

    @model_validator(mode="after")
    def require_query(self) -> "BatchSearchIssue":
        if not self.query and not self.queries:
            raise ValueError("each issue requires query or queries")
        return self

    def expanded_queries(self) -> list[BatchSearchQuery]:
        if self.queries:
            return list(self.queries)
        return [
            BatchSearchQuery(
                query_id=f"{self.issue_id}-q1",
                query_type="semantic",
                query=str(self.query or ""),
            )
        ]


class BatchSearchRequest(BaseModel):
    request_id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    as_of: date = Field(default_factory=date.today)
    # The API may carry its computed current date for downstream consistency.
    # This flag distinguishes that default from a date the user actually set.
    as_of_explicit: bool | None = Field(default=None, exclude=True)
    benchmark_today: date | None = Field(default=None, exclude=True)
    issues: list[BatchSearchIssue] = Field(min_length=1, max_length=8)
    retrieval_tier: str = Field(default="core", pattern="^(core|expanded)$")
    audience: str = Field(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    )
    organization_unit_id: str | None = Field(default=None, max_length=120)
    include_trace: bool = False
    ranking_strategy: str = Field(
        default="legacy_stack", pattern="^(legacy_stack|rrf_v2)$"
    )
    fusion_strategy: str = Field(
        default="legacy_stack", pattern="^(legacy_stack|rrf|weighted)$"
    )
    vector_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    lexical_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    enable_learned_reranker: bool = True
    rerank_top_n: int = Field(default=40, ge=1, le=100)
    enable_parent_expansion: bool = True
    enable_neighbor_expansion: bool = True
    query_decision: dict[str, Any] | None = None

    @model_validator(mode="after")
    def bound_total_queries(self) -> "BatchSearchRequest":
        total = sum(len(issue.expanded_queries()) for issue in self.issues)
        if total > 16:
            raise ValueError("batch search supports at most 16 queries")
        if self.fusion_strategy == "weighted" and abs(
            self.vector_weight + self.lexical_weight - 1.0
        ) > 1e-9:
            raise ValueError("fusion_weights_must_sum_to_one")
        return self


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
    if request.query_id:
        bound["query_id"] = request.query_id
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
    "tu_phap_ho_tich": (
        "ho_tich_chung_thuc",
        "tu_phap_ho_tich",
    ),
    "dat_dai_xay_dung": (
        "dat_dai_xay_dung",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
    ),
    "an_sinh_y_te_giao_duc": (
        "an_sinh_y_te_giao_duc",
        "an_sinh_y_te",
        "giao_duc_van_hoa",
        "lao_dong",
    ),
    "lao_dong": (
        "lao_dong",
        "an_sinh_y_te_giao_duc",
    ),
    "cu_tru_an_ninh": ("cu_tru_an_ninh", "cu_tru", "an_ninh"),
    "cu_tru": ("cu_tru_an_ninh", "cu_tru", "an_ninh"),
    "an_ninh": ("cu_tru_an_ninh", "cu_tru", "an_ninh"),
    "noi_vu_hanh_chinh": (
        "noi_vu_hanh_chinh",
        "khieu_nai_to_cao_xu_phat",
        "hanh_chinh_cong",
    ),
    "trat_tu_do_thi": (
        "trat_tu_do_thi",
        "xay_dung_do_thi",
        "dat_dai_xay_dung",
        "dat_dai_moi_truong",
        "khieu_nai_to_cao_xu_phat",
    ),
}

LAW_DOMAIN_OVERRIDES: dict[str, tuple[str, ...]] = {
    # Hai Phong's certification-fee support resolution was imported with the
    # legacy labour field even though its approved search scope is civil
    # status/certification. Keep the original metadata for audit and repair
    # retrieval only for this exact local instrument identity.
    "43/2025/NQ-HĐND": ("ho_tich_chung_thuc",),
    # The imported field mapping for the cross-cutting administrative-
    # sanctions law is empty.  Its legal subject is deterministic from the
    # verified document identity, so keep it inside the administrative domain.
    "15/2012/QH13": (
        "khieu_nai_to_cao_xu_phat",
        "dat_dai_xay_dung",
    ),
    # These reviewed national construction instruments have missing or
    # demonstrably wrong imported domain metadata. Document identity is used
    # as the deterministic repair; no corpus row is rewritten.
    "16/2022/NĐ-CP": ("dat_dai_xay_dung",),
    "175/2024/NĐ-CP": ("dat_dai_xay_dung",),
    # Article 4 of this cross-cutting two-tier decentralisation instrument is
    # the current operative source for construction-permit authority. Preserve
    # its administrative classification while making that reviewed identity
    # available to construction retrieval without rewriting corpus metadata.
    "140/2025/NĐ-CP": (
        "hanh_chinh_cong",
        "dat_dai_xay_dung",
    ),
    # Expert-reviewed urban-order cases require this exact instrument when
    # distinguishing public-order conduct from road/sidewalk violations.  Its
    # imported metadata only says security/residence, so preserve both scopes
    # for this document identity without broadening either domain generally.
    "167/2013/NĐ-CP": (
        "cu_tru_an_ninh",
        "trat_tu_do_thi",
    ),
}


def _apply_law_domain_override(row: dict[str, Any]) -> dict[str, Any]:
    repaired = _repair_display_row(dict(row))
    override_domains = LAW_DOMAIN_OVERRIDES.get(
        str(repaired.get("law_number") or "").strip()
    )
    if override_domains:
        repaired["domain_slug"] = (
            override_domains[0]
            if len(override_domains) == 1
            else ",".join(override_domains)
        )
    return repaired


def _model_revision_fingerprint(model_path: Path) -> str:
    """Return a path-independent SHA-256 over model/tokenizer artifacts."""

    digest = hashlib.sha256()
    patterns = (
        "*.json",
        "*.safetensors",
        "*.bin",
        "*.model",
        "*.txt",
    )
    files = sorted(
        {
            path
            for pattern in patterns
            for path in model_path.rglob(pattern)
            if path.is_file()
        },
        key=lambda path: path.relative_to(model_path).as_posix(),
    )
    if not files:
        digest.update(b"model-artifacts-missing")
    for path in files:
        relative = path.relative_to(model_path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _validate_model_fingerprint(actual: str) -> str:
    expected = str(os.getenv("VNLEGAL_LAL_MODEL_FINGERPRINT") or "").strip().casefold()
    if not expected:
        return actual
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise RuntimeError("vnlegal_lal_fingerprint_invalid")
    if expected != str(actual).casefold():
        raise RuntimeError("vnlegal_lal_fingerprint_mismatch")
    return actual


def _domain_values(domain: str | None) -> tuple[str, ...]:
    """Return reviewed scope slugs covered by an API/UI domain value."""
    if not domain:
        return ()
    shared = legal_domain_values(domain)
    return tuple(dict.fromkeys((*shared, *DOMAIN_ALIASES.get(domain, (domain,)))))


def _domain_matches(selected_domain: str | None, row_domain: Any) -> bool:
    """Keep a strict domain boundary while supporting broad UI labels."""
    if not selected_domain:
        return True
    row_values = {
        value.strip()
        for value in (
            [str(item) for item in row_domain]
            if isinstance(row_domain, (list, tuple, set))
            else str(row_domain or "").split(",")
        )
        if value.strip()
    }
    return bool(row_values.intersection(_domain_values(selected_domain)))


def _reviewed_domain_override_laws(domain: str | None) -> list[str]:
    """Return normalized, reviewed identities that repair one domain only."""

    values = _domain_values(domain)
    return [
        law_number
        .replace("NĐ", "ND")
        .replace("Đ", "D")
        .replace("đ", "d")
        for law_number, reviewed_domains in LAW_DOMAIN_OVERRIDES.items()
        if any(reviewed_domain in values for reviewed_domain in reviewed_domains)
    ]


def _exact_override_scope_join(plan: Any, domain: str | None) -> bool:
    """Allow a reviewed exact instrument past a stale legacy scope row.

    ``legal_search_scope`` predates reviewed domain overrides and may mark a
    national instrument as ``out_of_commune_scope``. An exact law lookup is
    already bounded by normalized law number, so a LEFT JOIN is safe only when
    that identity is in the reviewed override set.
    """

    requested = {
        str(value)
        .strip()
        .replace("NĐ", "ND")
        .replace("Đ", "D")
        .replace("đ", "d")
        for value in (
            getattr(plan, "law_numbers", ())
            or (
                (getattr(plan, "law_number", None),)
                if getattr(plan, "law_number", None)
                else ()
            )
        )
        if str(value).strip()
    }
    if not requested:
        return False
    if domain:
        allowed = set(_reviewed_domain_override_laws(domain))
    else:
        allowed = {
            str(law_number)
            .strip()
            .replace("NĐ", "ND")
            .replace("Đ", "D")
            .replace("đ", "d")
            for law_number in LAW_DOMAIN_OVERRIDES
        }
    return bool(requested.intersection(allowed))


def _needs_lexical_retrieval(exact_rows: list[dict[str, Any]]) -> bool:
    """Avoid a redundant metadata wildcard scan after an exact hit."""

    return not bool(exact_rows)


def _reserve_explicit_exact_results(
    ranked: list[dict[str, Any]],
    *,
    explicit_law_number: str | None,
) -> list[dict[str, Any]]:
    """Reserve bounded exact-document rows before applying the result limit.

    This is a retrieval relevance rule, not a legal-authority override.  When
    the user names an instrument, its own evidence must be visible before
    broader higher-authority context.  The remaining evidence keeps the legal
    hierarchy order established by ``rank_legal_evidence``.
    """

    if not explicit_law_number:
        return list(ranked)
    exact = [
        item
        for item in ranked
        if item.get("retrieval_source") == "exact_metadata"
    ]
    exact_ids = {int(item.get("chunk_id") or 0) for item in exact}
    return [
        *exact,
        *[
            item
            for item in ranked
            if int(item.get("chunk_id") or 0) not in exact_ids
        ],
    ]


def _exact_article_chunk_limit(
    article_number: str,
    requested_articles: set[str],
) -> int:
    """Keep enough same-article chunks for a document-only exact lookup."""

    if not requested_articles:
        return 3
    return 2 if article_number.strip().casefold() in requested_articles else 1


def _build_exact_document_outlines(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_chars: int = 24000,
) -> list[dict[str, Any]]:
    """Build a deterministic multi-level map for an explicitly named document.

    The packet uses only reviewed database metadata and source text already
    returned by the exact lookup. It has two deterministic levels: the complete
    article/section map and one balanced extractive digest for every physical
    source chunk. This keeps beginning, middle and end coverage without paying
    for one LLM call per article or silently reducing each Article to its first
    few hundred characters.
    """

    def balanced_excerpt(value: Any, limit: int) -> str:
        text_value = re.sub(r"\s+", " ", str(value or "")).strip()
        if limit <= 0 or not text_value:
            return ""
        if len(text_value) <= limit:
            return text_value
        if limit < 24:
            return text_value[:limit]
        separator = " … "
        usable = max(3, limit - (2 * len(separator)))
        first_size = usable // 3
        middle_size = usable // 3
        last_size = usable - first_size - middle_size
        middle_start = max(0, (len(text_value) // 2) - (middle_size // 2))
        return (
            text_value[:first_size].rstrip()
            + separator
            + text_value[middle_start : middle_start + middle_size].strip()
            + separator
            + text_value[-last_size:].lstrip()
        )[:limit]

    documents: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = str(row.get("document_id") or row.get("law_number") or "").strip()
        if key:
            documents.setdefault(key, []).append(row)

    packets: list[dict[str, Any]] = []
    for document_rows in documents.values():
        ordered = sorted(
            document_rows,
            key=lambda row: (
                int(row.get("article_id") or 0),
                int(row.get("chunk_index") or 0),
                int(row.get("chunk_id") or 0),
            ),
        )
        first = ordered[0]
        article_groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in ordered:
            article_key = str(
                row.get("article_id")
                or row.get("article_number")
                or "whole-source"
            )
            article_groups.setdefault(article_key, []).append(row)

        header_lines = [
            "BẢN ĐỒ TOÀN VĂN ĐƯỢC TỔNG HỢP XÁC ĐỊNH TỪ KHO NGUỒN",
            f"Văn bản: {first.get('document_title') or first.get('law_number') or 'Nguồn pháp luật'}",
            f"Số hiệu: {first.get('law_number') or 'không có'}",
            f"Số nhóm Điều/phần trong kho: {len(article_groups)}; số đoạn nguồn: {len(ordered)}.",
            "Phương pháp phủ: mỗi đoạn nguồn đều có trích đoạn cân bằng ở đầu, giữa và cuối; đây là bản tóm lược, không phải nguyên văn.",
            "Cấp 1 — bản đồ đầy đủ các Điều/phần theo thứ tự nguồn:",
        ]
        article_labels: list[tuple[str, list[Mapping[str, Any]]]] = []
        for position, group in enumerate(article_groups.values(), start=1):
            lead = group[0]
            number = str(lead.get("article_number") or "").strip()
            title = re.sub(
                r"\s+",
                " ",
                str(lead.get("article_title") or lead.get("chunk_heading") or ""),
            ).strip()[:240]
            label = f"Điều {number}" if number and number != "0" else f"Phần {position}"
            if title and title.casefold() not in label.casefold():
                label = f"{label}: {title}"
            article_labels.append((label, group))

        level_one = [
            f"- {label} ({len(group)} đoạn nguồn)"
            for label, group in article_labels
        ]
        level_two_header = [
            "Cấp 2 — tóm lược trích xuất phủ từng đoạn của từng Điều/phần:"
        ]
        level_two_structure: list[tuple[str, Mapping[str, Any] | None]] = []
        for label, group in article_labels:
            level_two_structure.append((f"- {label}", None))
            for chunk_position, row in enumerate(group, start=1):
                level_two_structure.append(
                    (f"  - Đoạn {chunk_position}/{len(group)}: ", row)
                )

        structural_lines = [
            *header_lines,
            *level_one,
            *level_two_header,
            *(prefix for prefix, _ in level_two_structure),
        ]
        structural_chars = len("\n".join(structural_lines))
        excerpt_rows = [row for _, row in level_two_structure if row is not None]
        excerpt_budget = max(0, max_chars - structural_chars)
        per_chunk_chars = min(
            220,
            excerpt_budget // max(1, len(excerpt_rows)),
        )
        level_two: list[str] = []
        covered_chunks = 0
        source_chars = 0
        for prefix, row in level_two_structure:
            if row is None:
                level_two.append(prefix)
                continue
            raw_content = str(row.get("content") or "")
            source_chars += len(raw_content)
            excerpt = balanced_excerpt(raw_content, per_chunk_chars)
            if excerpt:
                covered_chunks += 1
            level_two.append(prefix + excerpt)

        content = "\n".join(
            [*header_lines, *level_one, *level_two_header, *level_two]
        )[:max_chars]
        coverage_ratio = covered_chunks / max(1, len(ordered))
        packets.append(
            {
                "status": "complete" if covered_chunks == len(ordered) else "partial",
                "serving_mode": "multi_level_document_digest_v2",
                "document_id": first.get("document_id"),
                "law_number": first.get("law_number"),
                "document_title": first.get("document_title"),
                "source_url": first.get("source_url"),
                "effective_status": first.get("effective_status")
                or first.get("document_status"),
                "article_count": len(article_groups),
                "chunk_count": len(ordered),
                "covered_article_count": len(article_groups) if covered_chunks == len(ordered) else 0,
                "covered_chunk_count": covered_chunks,
                "coverage_ratio": round(coverage_ratio, 6),
                "source_char_count": source_chars,
                "digest_char_count": len(content),
                "content_truncated": covered_chunks != len(ordered),
                "content": content,
            }
        )
    return packets


def _exact_packet_serving_chunk_ids(packet: Mapping[str, Any]) -> list[int]:
    """Return the verified rows actually allowed into answer context."""

    values = (
        packet.get("selected_chunk_ids")
        if packet.get("serving_mode") == "bounded_long_article_window"
        else packet.get("ordered_chunk_ids")
    ) or []
    return [int(value) for value in values if _as_int(value) is not None]


def _select_exact_lookup_plan(request_query: str, retrieval_query: str) -> Any:
    """Keep caller-supplied legal identifiers authoritative.

    ``_rewrite_query`` may append reviewed instruments to improve recall for a
    natural-language question.  Those additions are useful to ANN/lexical
    retrieval, but they must not widen an already explicit document+article
    lookup.  Otherwise a route such as ``140/2025/NĐ-CP Điều 4`` becomes a
    multi-document lookup and the article constraint is intentionally relaxed,
    allowing an unrelated provision to displace the requested one.
    """

    from api.legal_exact_retrieval import plan_exact_lookup

    explicit = plan_exact_lookup(request_query)
    if explicit.law_number:
        return explicit
    return plan_exact_lookup(retrieval_query)


class LegalImportRequest(BaseModel):
    activation_version_key: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
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
    uploaded_pdf_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    applicability_info: str = Field(default="", max_length=4000)
    content: str = Field(min_length=20)
    confirmed_official_source: bool = False
    domain_slug: str | None = Field(
        default=None,
        pattern="^[a-z0-9][a-z0-9_-]*$",
        max_length=80,
    )
    domain_codes: list[str] = Field(default_factory=list, max_length=20)
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
    organization_unit_ids: list[str] = Field(default_factory=list, max_length=50)
    organization_assignment_state: str | None = Field(
        default=None, pattern="^(assigned|shared|unassigned)$"
    )
    # Explicitly reviewed replacement flow.  This is intentionally optional
    # so ordinary imports keep the existing duplicate protection.
    replacement_of_document_id: int | None = Field(default=None, ge=1)
    replacement_action: str = Field(default="exclude", pattern="^(exclude|historical)$")
    # structured | unstructured | auto
    structure: str = Field(default="auto", max_length=32)


IMPORT_FIELD_DOMAINS = {
    6: "khieu_nai_to_cao_xu_phat",
    7: "ho_tich_chung_thuc",
    8: "dat_dai_xay_dung",
    9: "cu_tru_an_ninh",
    10: "khieu_nai_to_cao_xu_phat",
}
IMPORT_DOMAIN_SLUGS = frozenset(
    {
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "an_sinh_y_te_giao_duc",
        "hanh_chinh_cong",
        "noi_vu_hanh_chinh",
        "trat_tu_do_thi",
        "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat",
        "xay_dung_do_thi",
    }
)


# Keep accepted import domains aligned with the approved commune catalog.
from api.commune_catalog import commune_catalog
IMPORT_DOMAIN_SLUGS = IMPORT_DOMAIN_SLUGS | frozenset(domain for group in commune_catalog() for domain in group["domains"])


def _is_official_legal_source_url(value: str) -> bool:
    """Accept only HTTPS URLs on Vietnamese government legal-source hosts."""

    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        return False
    return (
        host == "vbpl.vn"
        or host.endswith(".vbpl.vn")
        or host == "chinhphu.vn"
        or host.endswith(".chinhphu.vn")
        or host.endswith(".gov.vn")
    )


def _normalized_identity(value: Any) -> str:
    return " ".join(_normalized_terms(str(value or "")))


def _same_legal_document_identity(
    existing: dict[str, Any],
    requested: dict[str, Any],
) -> bool:
    """Distinguish instruments that legally share a printed number."""

    comparison = compare_legal_documents(existing, requested)
    kind = comparison["kind"]
    if kind in {"duplicate_content", "identity_match", "content_changed"}:
        return True
    if kind != "metadata_conflict":
        return False

    # A printed number is not globally unique: unrelated document types (and
    # sometimes different issuing bodies) can legitimately share it.  Treat a
    # metadata conflict as the same legal instrument only when another strong
    # identity anchor agrees.  This keeps true replacements in the review
    # workflow without blocking an unrelated instrument merely because its
    # number is equal.
    matched = set(comparison.get("matched_fields") or [])
    return bool(
        matched & {"source_url", "content_sha256"}
        or {"number", "agency", "issued_date"}.issubset(matched)
    )


def _duplicate_document_rows(connection: Any, law_numbers: list[str]) -> list[dict[str, Any]]:
    """Metadata-only lookup across ALL lifecycle states, not the serving view.

    The loose SQL key is a prefilter; the full legal identity comparison is the
    authority. No DISTINCT ON(number): two agencies may share that number.
    """
    keys = sorted({number_search_key(value) for value in law_numbers} - {""})
    if not keys:
        return []
    statement = text(
        "SELECT id, title, law_number, document_type, issuing_agency, issued_date, "
        "effective_date, expired_date, source_url, status "
        "FROM legal_documents WHERE LTRIM(REGEXP_REPLACE("
        "REPLACE(legal_normalize_identifier(law_number), CHR(272), 'D'), "
        "'[^A-Z0-9]', '', 'g'), '0') IN :number_keys ORDER BY id DESC LIMIT 1001"
    ).bindparams(bindparam("number_keys", expanding=True))
    return [dict(row) for row in connection.execute(statement, {"number_keys": keys}).mappings().all()]


def _assert_no_import_duplicate(connection: Any, request: LegalImportRequest) -> None:
    # A transaction-scoped advisory lock serializes the short staging INSERT
    # boundary across processes. The expensive embedding work stays outside it.
    key = number_search_key(request.law_number)
    if getattr(getattr(connection, "dialect", None), "name", "postgresql") == "postgresql":
        lock_key = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big", signed=True)
        connection.execute(text("SELECT pg_advisory_xact_lock(:identity_lock)"), {"identity_lock": lock_key})
    rows = _duplicate_document_rows(connection, [request.law_number])
    from api.legal_replacement_lineage import retired_ancestor_ids
    ancestors = retired_ancestor_ids(connection, request.replacement_of_document_id)
    if len(rows) > 1000:
        raise ValueError("Có quá nhiều bản cùng số hiệu; cần đối chiếu trước khi nhập kho.")
    for row in rows:
        if request.replacement_of_document_id is not None and int(row["id"]) == int(request.replacement_of_document_id):
            continue
        if int(row['id']) in ancestors and row.get('status') in {'archived', 'blocked', 'expired'}:
            continue
        if _same_legal_document_identity(row, request.model_dump()):
            raise ValueError(f"Định danh pháp lý xung đột với văn bản #{row['id']}; cần đối chiếu hoặc dùng luồng thay thế.")


def _import_domain_slug(
    requested_domain: str | None,
    field_id: int,
    field_name: str,
) -> str:
    requested = str(requested_domain or "").strip()
    # The public API validates this value against the active administrator-
    # managed directory. Preserve a valid custom code instead of silently
    # replacing it with a legacy field name in the retrieval projection.
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", requested):
        return requested
    mapped = IMPORT_FIELD_DOMAINS.get(int(field_id))
    if mapped:
        return mapped
    normalized_field = "_".join(_normalized_terms(field_name))
    return normalized_field[:80] or "hanh_chinh_cong"


CHUNK_SIZE = DEFAULT_CHUNK_SIZE
CHUNK_OVERLAP = DEFAULT_CHUNK_OVERLAP
SPLIT_THRESHOLD = DEFAULT_SPLIT_THRESHOLD


def _parse_articles(content: str) -> list[dict[str, str]]:
    """Compatibility wrapper for deterministic Điều/Phụ lục parents."""

    return parse_structural_parents(content)


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
    """Compatibility wrapper returning persisted child fields plus path data."""

    return split_parent_children(
        article,
        chunk_size=CHUNK_SIZE,
        overlap=CHUNK_OVERLAP,
        split_threshold=SPLIT_THRESHOLD,
    )


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


DOCUMENT_EXPORT_MAX_ROWS = 10_000
DOCUMENT_EXPORT_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _excel_safe_text(value: Any) -> str:
    """Return text that Excel cannot reinterpret as an executable formula."""

    rendered = str(value or "")
    if rendered.startswith(("=", "+", "-", "@")):
        return f"'{rendered}"
    return rendered


def _excel_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        return date.fromisoformat(rendered[:10])
    except ValueError:
        return None


def _document_export_workbook(items: Sequence[Mapping[str, Any]]) -> bytes:
    """Build the filtered document workbook without trusting cell text."""

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:  # pragma: no cover - dependency is release-pinned
        raise RuntimeError("document_export_dependency_unavailable") from exc

    headers = (
        "STT",
        "Mã văn bản",
        "Số hiệu",
        "Tên văn bản",
        "Loại văn bản",
        "Cơ quan ban hành",
        "Lĩnh vực",
        "Tầng truy xuất",
        "Ngày ban hành",
        "Ngày hiệu lực",
        "Ngày hết hiệu lực",
        "Trạng thái",
        "Số điều/mục",
        "URL nguồn",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Van ban phap luat"
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for index, item in enumerate(items, start=1):
        validity = item.get("validity_sync")
        validity_label = (
            validity.get("display_label")
            if isinstance(validity, Mapping)
            else None
        )
        sheet.append(
            [
                index,
                _excel_safe_text(item.get("doc_id")),
                _excel_safe_text(item.get("law_number")),
                _excel_safe_text(item.get("document_title")),
                _excel_safe_text(item.get("document_type")),
                _excel_safe_text(item.get("issuing_agency")),
                _excel_safe_text(item.get("domain_name") or item.get("field_name")),
                _excel_safe_text(item.get("retrieval_tier")),
                _excel_date(item.get("issued_date")),
                _excel_date(item.get("effective_date")),
                _excel_date(item.get("expired_date")),
                _excel_safe_text(
                    validity_label
                    or item.get("validity_status")
                    or item.get("effective_status")
                    or item.get("as_of_status")
                    or item.get("stored_status")
                ),
                int(item.get("article_count") or 0),
                _excel_safe_text(item.get("source_url")),
            ]
        )

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in ("I", "J", "K"):
        for cell in sheet[column][1:]:
            if cell.value is not None:
                cell.number_format = "dd/mm/yyyy"
    widths = (8, 15, 24, 70, 22, 38, 28, 18, 16, 16, 18, 16, 14, 60)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width

    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


_MANAGEMENT_KNOWN_STATUSES = {
    "active",
    "approved",
    "archived",
    "expired",
    "historical",
    "replaced",
    "blocked",
    "draft",
    "indexing",
    "rejected",
    "staging",
    "submitted",
}


def _management_as_of_status(row: Mapping[str, Any], as_of: date) -> str:
    """Project applicability for one date without changing stored legal state."""

    stored_status = str(
        row.get("stored_status") or row.get("status") or "unknown"
    ).strip().lower()
    if stored_status != "active":
        return stored_status if stored_status in _MANAGEMENT_KNOWN_STATUSES else "unknown"
    effective_date = row.get("effective_date")
    expired_date = row.get("expired_date")
    if isinstance(effective_date, datetime):
        effective_date = effective_date.date()
    elif isinstance(effective_date, str):
        try:
            effective_date = date.fromisoformat(effective_date[:10])
        except ValueError:
            effective_date = None
    if isinstance(expired_date, datetime):
        expired_date = expired_date.date()
    elif isinstance(expired_date, str):
        try:
            expired_date = date.fromisoformat(expired_date[:10])
        except ValueError:
            expired_date = None
    if isinstance(effective_date, date) and effective_date > as_of:
        return "not_yet_effective"
    if isinstance(expired_date, date) and expired_date <= as_of:
        return "expired"
    return "active"


def _management_quality_flags(row: Mapping[str, Any]) -> list[str]:
    """Return deterministic metadata-quality flags; never synthesize a value."""

    flags: list[str] = []
    for key, code in (
        ("document_title", "missing_title"),
        ("law_number", "missing_law_number"),
        ("issuing_agency", "missing_issuing_agency"),
        ("source_url", "missing_source_url"),
    ):
        if not str(row.get(key) or "").strip():
            flags.append(code)
    if int(row.get("chunk_count") or 0) <= 0:
        flags.append("zero_chunks")
    stored_status = str(
        row.get("stored_status") or row.get("status") or ""
    ).strip().lower()
    if stored_status not in _MANAGEMENT_KNOWN_STATUSES:
        flags.append("unknown_status")

    normalized_dates: dict[str, date | None] = {}
    for key in ("issued_date", "effective_date", "expired_date"):
        value = row.get(key)
        if isinstance(value, datetime):
            normalized_dates[key] = value.date()
        elif isinstance(value, date):
            normalized_dates[key] = value
        elif isinstance(value, str):
            try:
                normalized_dates[key] = date.fromisoformat(value[:10])
            except ValueError:
                normalized_dates[key] = None
        else:
            normalized_dates[key] = None

    issued = normalized_dates["issued_date"]
    effective = normalized_dates["effective_date"]
    expired = normalized_dates["expired_date"]
    if issued and effective:
        if effective < issued:
            flags.append("effective_before_issued")
        elif (effective - issued).days > 36_525:
            # Flag, but never repair, a century-scale delay as a likely source
            # metadata error requiring human review.
            flags.append("implausible_effective_date_gap")
    if issued and expired and expired < issued:
        flags.append("expired_before_issued")
    if effective and expired and expired < effective:
        flags.append("expired_before_effective")
    return flags


_MANAGEMENT_EDITABLE_METADATA_FIELDS = (
    "issued_date",
    "effective_date",
    "expired_date",
    "source_url",
    "gazette_date",
    "signer_title",
    "signer_name",
    "applicability_info",
    "version",
)


def _management_metadata_revision(row: Mapping[str, Any]) -> str:
    """Fingerprint mutable metadata so concurrent Admin edits cannot overwrite."""

    payload = {
        field: _iso_or_none(row.get(field))
        if field.endswith("_date")
        else (str(row.get(field)).strip() if row.get(field) is not None else None)
        for field in _MANAGEMENT_EDITABLE_METADATA_FIELDS
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_CURRENT_VALIDITY_STATUSES = {
    "active",
    "amended",
    "expired_partial",
    "suspended_partial",
}
_EXPIRED_VALIDITY_STATUSES = {
    "expired",
    "replaced",
    "repealed",
    "suspended",
}


def _matches_management_validity_filter(
    projection: Mapping[str, Any], requested: str, *, as_of: date
) -> bool:
    """Match one public/admin filter against the canonical validity projection."""

    status = str(projection.get("status") or "unknown")
    if requested == "active":
        return status in _CURRENT_VALIDITY_STATUSES
    if requested == "expired":
        return status in _EXPIRED_VALIDITY_STATUSES
    if requested == "expiring_30":
        if status not in _CURRENT_VALIDITY_STATUSES:
            return False
        try:
            effective_to = date.fromisoformat(str(projection.get("effective_to") or ""))
        except ValueError:
            return False
        return as_of < effective_to <= as_of + timedelta(days=30)
    return status == requested


def _management_validity_cards(
    *,
    rows: Mapping[int, Mapping[str, Any]],
    retrievable_document_ids: Sequence[int],
    snapshot: Mapping[str, Any] | None,
    as_of: date,
) -> dict[str, int]:
    """Count the same document-id validity projection used by list filters.

    The serving manifest decides which documents are retrievable. Legal
    effectivity is a separate observation-backed projection, so manifest
    membership must not be presented as proof that a document is effective.
    """

    counts = {
        "total_retrievable": len(retrievable_document_ids),
        "current_effective": 0,
        "expired_total": 0,
        "unknown_total": 0,
        "not_yet_effective_total": 0,
        "expiring_30": 0,
        "effective_30": 0,
    }
    thirty_days = as_of + timedelta(days=30)
    for document_id in retrievable_document_ids:
        row = rows.get(int(document_id)) or rows.get(str(document_id)) or {
            "doc_id": document_id
        }
        projection = project_validity_for_row(
            row,
            snapshot=snapshot,
            as_of=as_of,
            mode="protect",
        )
        status = str(projection.get("status") or "unknown")
        if status in _CURRENT_VALIDITY_STATUSES:
            counts["current_effective"] += 1
            effective_to = projection.get("effective_to")
            try:
                effective_to_date = date.fromisoformat(str(effective_to))
            except (TypeError, ValueError):
                effective_to_date = None
            if effective_to_date and as_of < effective_to_date <= thirty_days:
                counts["expiring_30"] += 1
        elif status in _EXPIRED_VALIDITY_STATUSES:
            counts["expired_total"] += 1
        elif status == "not_yet_effective":
            counts["not_yet_effective_total"] += 1
            effective_from = projection.get("effective_from")
            try:
                effective_from_date = date.fromisoformat(str(effective_from))
            except (TypeError, ValueError):
                effective_from_date = None
            if effective_from_date and as_of < effective_from_date <= thirty_days:
                counts["effective_30"] += 1
        else:
            counts["unknown_total"] += 1
    return counts


def _vector_membership_projection(
    collections: Mapping[str, Any], chunk_ids: Sequence[int]
) -> dict[str, Any]:
    """Read exact vector IDs from configured collections without mutating them."""

    normalized_chunks = sorted({int(value) for value in chunk_ids if int(value) > 0})
    vector_ids = [f"chunk-{value}" for value in normalized_chunks]
    chunk_ids_sha256 = hashlib.sha256(
        "\n".join(vector_ids).encode("utf-8")
    ).hexdigest()
    if not collections:
        return {
            "status": "unavailable",
            "reason_code": "vector_store_not_ready",
            "message": "Kho vector chưa sẵn sàng để đối chiếu chỉ đọc.",
            "expected": len(vector_ids),
            "chunk_ids_sha256": chunk_ids_sha256,
            "collections": {},
            "complete_collections": [],
            "retrieval_ready": False,
            "current_retrieval_ready": False,
            "historical_retrieval_ready": False,
        }
    reports: dict[str, dict[str, int | str]] = {}
    degraded = False
    for name, collection in collections.items():
        if collection is None:
            degraded = True
            reports[str(name)] = {
                "present": 0,
                "missing": len(vector_ids),
                "reason_code": "vector_collection_unavailable",
            }
            continue
        try:
            from api.vector_coverage import present_chunk_ids
            present = len(present_chunk_ids(collection, normalized_chunks))
            reports[str(name)] = {
                "present": present,
                "missing": max(len(vector_ids) - present, 0),
            }
        except Exception:
            degraded = True
            reports[str(name)] = {
                "present": 0,
                "missing": len(vector_ids),
                "reason_code": "vector_collection_unavailable",
            }
    complete_collections = [
        name
        for name, report in reports.items()
        if vector_ids and int(report.get("missing") or 0) == 0
    ]
    retrieval_ready = bool(vector_ids and complete_collections)
    current_retrieval_ready = bool(
        set(complete_collections) & {"fast", "expanded", "current", "incremental"}
    )
    historical_retrieval_ready = bool(
        set(complete_collections) & {"temporal", "historical", "incremental"}
    )
    return {
        "status": "degraded" if degraded else "available",
        "reason_code": "vector_collection_unavailable" if degraded else None,
        "message": (
            "Không thể đọc một hoặc nhiều kho vector."
            if degraded
            else None
        ),
        "expected": len(vector_ids),
        "chunk_ids_sha256": chunk_ids_sha256,
        "collections": reports,
        "complete_collections": complete_collections,
        "retrieval_ready": retrieval_ready,
        "current_retrieval_ready": current_retrieval_ready,
        "historical_retrieval_ready": historical_retrieval_ready,
    }


_MANAGEMENT_LIST_FIELDS = {
    "doc_id",
    "document_title",
    "law_number",
    "document_type",
    "issuing_agency",
    "scope",
    "sector",
    "stored_status",
    "issued_date",
    "effective_date",
    "expired_date",
    "source_url",
    "version",
    "field_id",
    "field_name",
    "domain",
    "domain_name",
    "retrieval_tier",
    "article_count",
    "chunk_count",
}


def _management_list_item(
    row: Mapping[str, Any],
    as_of: date,
    *,
    serving_state: str = "quarantined",
    serving_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    repaired = _repair_display_row(dict(row))
    item = {key: repaired.get(key) for key in _MANAGEMENT_LIST_FIELDS}
    for key in ("issued_date", "effective_date", "expired_date"):
        item[key] = _iso_or_none(item.get(key))
    item["as_of_status"] = _management_as_of_status(repaired, as_of)
    item["serving_state"] = serving_state
    item["current_answer_eligible"] = serving_state == "current_retrievable"
    item["historical_lookup_allowed"] = serving_state == "historical_only"
    item["serving_status"] = (
        "current_retrievable"
        if serving_state == "current_retrievable"
        else "historical_only"
        if serving_state == "historical_only"
        else "not_served"
    )
    item["quality_flags"] = _management_quality_flags(repaired)
    item["article_count"] = int(item.get("article_count") or 0)
    item["chunk_count"] = int(item.get("chunk_count") or 0)
    if serving_decision is not None:
        item.update(serving_decision)
    return item


def _assert_exportable_utf8(value: str, field_name: str) -> None:
    """Prevent corrupted Vietnamese strings from being silently exported to PDF."""
    if _contains_probable_mojibake(value):
        raise ValueError(f"{field_name} contains probable mojibake; export stopped for review.")


def _safe_pdf_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:80] or "legal-document"


class _VectorUnavailableCollection:
    """Neutral Chroma-compatible surface for the SQL-only fallback."""

    def query(self, **_: Any) -> dict[str, list[list[Any]]]:
        return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    def count(self) -> int:
        return 0


class LegalRetriever:
    def __init__(self) -> None:
        self._load_lock = Lock()
        self._prewarm_lock = Lock()
        self._inference_lock = Lock()
        self._vector_compute_lock = Lock()
        self._query_lock = Lock()
        self._write_lock = Lock()
        # The inventory projection reads every document in the active serving
        # release. The Admin landing page requests summary + first page in
        # parallel, so without a single-flight cache both worker threads repeat
        # the same 12k-row state read. Keep this deliberately short-lived and
        # invalidate it synchronously after every mutation performed here.
        self._management_inventory_lock = Lock()
        self._management_inventory_cache: dict[
            tuple[str, str, str], tuple[float, dict[str, Any]]
        ] = {}
        self._tokenizer = None
        self._model = None
        self._collection = None
        self._source_collection = None
        self._temporal_collection = None
        self._incremental_collection = None
        self._vector_index_available = True
        self._requested_device = os.getenv("LEGAL_EMBED_DEVICE", "auto").strip().lower()
        self._embedding_device = torch.device("cpu")
        self._embedding_dtype = torch.float32
        self._embedding_fallback_reason: str | None = None
        self._ready = False
        self._quality_sidecar_available: bool | None = None
        self._serving_request_context = local()
        # Runtime and benchmark manifests are checksum/collection validated
        # before any vector or SQL serving path can start.
        self._serving_scope = serving_scope_from_environment(
            configured_collection=CHROMA_COLLECTION
        )
        self._serving_allowed_document_ids: set[int] | None = (
            set(self._serving_scope.document_ids) if self._serving_scope else None
        )
        # Candidate-only benchmark may include documents staged in PostgreSQL
        # so they can be evaluated before activation.  This flag is never set
        # by runtime traffic; the benchmark harness opts in explicitly after
        # validating an isolated candidate manifest.
        self._benchmark_allow_staging = bool(
            self._serving_scope
            and self._serving_scope.schema_version == "legal-serving-candidate-v1"
        )
        self._shadow_allowed_chunk_ids: dict[str, set[int]] | None = (
            {
                "core": set(self._serving_scope.chunk_ids),
                "expanded": set(self._serving_scope.chunk_ids),
            }
            if self._serving_scope
            else None
        )
        self._serving_law_document_ids: dict[str, set[int]] = {}
        # Optional manifest-derived domain -> document acceleration. The
        # benchmark harness populates this map from the immutable serving
        # manifest; public serving leaves it unset and retains the SQL domain
        # join as the compatibility path.
        self._serving_domain_document_ids: dict[str, set[int]] | None = None
        self._model_fingerprint = _validate_model_fingerprint(
            _model_revision_fingerprint(MODEL_PATH)
        )
        self._learned_reranker = OptionalCrossEncoderReranker.from_environment()
        self._query_vector_cache = QueryVectorCache(
            max_entries=QUERY_VECTOR_CACHE_MAX_ENTRIES,
            ttl_seconds=QUERY_VECTOR_CACHE_TTL_SECONDS,
        )
        self._exact_rows_cache = ExactRowsCache(
            max_entries=EXACT_ROWS_CACHE_MAX_ENTRIES,
            ttl_seconds=EXACT_ROWS_CACHE_TTL_SECONDS,
        )
        self._last_vector_telemetry: dict[str, Any] = {}
        # Optional exact-flat index used for controlled benchmark/diagnostic
        # runs. It preserves the embedding, candidate count and ranking
        # contract while avoiding HNSW page-fault tails on small candidate
        # collections. Production keeps this disabled unless explicitly
        # opted in after the benchmark gate.
        self._exact_vector_search_enabled = False
        self._exact_vector_matrix: np.ndarray | None = None
        self._exact_vector_ids: list[str] = []
        self._exact_vector_metadatas: list[dict[str, Any]] = []
        self._exact_vector_collection_name: str | None = None
        self._exact_vector_lock = RLock()
        self._hydration_cache_rows: dict[int, dict[str, Any]] | None = None
        self._hydration_cache_parents: dict[int, dict[str, Any]] | None = None
        self._hydration_cache_exact_index: dict[tuple[str, str], list[dict[str, Any]]] | None = None
        self._hydration_cache_article_chunk_counts: dict[int, int] | None = None
        self._hydration_cache_article_chunks: dict[int, list[tuple[int, int]]] | None = None
        self._hydration_cache_exact_packet_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._hydration_cache_manifest_sha256: str | None = None
        self._engine = create_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            pool_timeout=float(os.getenv("LEGAL_DB_POOL_TIMEOUT_SECONDS", "5")),
        )
        # Schema creation is an explicit migration/deployment concern, not a
        # constructor side effect.  Instantiating a retriever is also used by
        # offline/unit paths where opening the primary DB would make routing
        # and retrieval initialization fail before a request is served.  Keep
        # the flag lazy; mutating operations still call the ensure helper when
        # they actually need the projection.
        auto_init_projection = str(
            os.getenv("LEGAL_ORGANIZATION_PROJECTION_AUTO_INIT", "false")
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self._organization_projection_schema_available = bool(
            auto_init_projection and self._ensure_organization_projection_schema()
        )
        # A checksum-bound hydration cache may be supplied by an isolated
        # serving-manifest process (for example the candidate shadow server).
        # Public/default serving does not load one implicitly, so baseline
        # startup and rollback remain unchanged.
        configured_hydration_cache = str(
            os.getenv("LEGAL_HYDRATION_CACHE_PATH") or ""
        ).strip()
        if configured_hydration_cache and self._serving_scope:
            self.load_hydration_cache(
                configured_hydration_cache,
                manifest_sha256=self._serving_scope.manifest_sha256,
            )
        if self._serving_scope:
            try:
                scope_ids = sorted(self._serving_scope.document_ids)
                statement = text(
                    "SELECT id, law_number FROM legal_documents "
                    "WHERE id = ANY(:serving_scope_ids)"
                ).bindparams(
                    bindparam("serving_scope_ids", type_=ARRAY(Integer))
                )
                with self._engine.connect() as connection:
                    for row in connection.execute(
                        statement, {"serving_scope_ids": scope_ids}
                    ).mappings():
                        key = _normalized_identity(row.get("law_number"))
                        if key:
                            self._serving_law_document_ids.setdefault(key, set()).add(
                                int(row["id"])
                            )
                try:
                    manifest_payload = json.loads(
                        self._serving_scope.path.read_text(encoding="utf-8-sig")
                    )
                    domain_map: dict[str, set[int]] = {}
                    for raw in manifest_payload.get("documents") or []:
                        slug = str(raw.get("domain") or "").strip()
                        document_id = _as_int(raw.get("document_id"))
                        if slug and document_id is not None:
                            domain_map.setdefault(slug, set()).add(document_id)
                    self._serving_domain_document_ids = domain_map
                except Exception:
                    # The manifest ID scope remains authoritative; the domain
                    # map is only a latency acceleration and may be absent in
                    # older manifests.
                    self._serving_domain_document_ids = None
            except Exception:
                # The manifest id scope remains authoritative if the optional
                # law-number acceleration cannot be prepared.
                self._serving_law_document_ids = {}

    def _ensure_organization_projection_schema(self) -> bool:
        """Create the rebuildable SQL serving projection without touching corpus text."""

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS legal_document_organization_assignment (
                            document_id BIGINT PRIMARY KEY REFERENCES legal_documents(id) ON DELETE CASCADE,
                            assignment_state TEXT NOT NULL CHECK (assignment_state IN ('assigned', 'shared', 'unassigned')),
                            primary_organization_unit_id TEXT,
                            organization_unit_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            assignment_source TEXT NOT NULL DEFAULT 'admin',
                            confirmation_status TEXT NOT NULL DEFAULT 'confirmed',
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            CHECK (
                                (assignment_state = 'assigned' AND primary_organization_unit_id IS NOT NULL)
                                OR (assignment_state IN ('shared', 'unassigned') AND primary_organization_unit_id IS NULL)
                            )
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_legal_document_org_assignment_units_gin
                        ON legal_document_organization_assignment
                        USING GIN (organization_unit_ids)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_legal_document_org_assignment_unit
                        ON legal_document_organization_assignment(
                            primary_organization_unit_id, assignment_state
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO legal_document_organization_assignment (
                            document_id, assignment_state, organization_unit_ids,
                            assignment_source, confirmation_status
                        )
                        SELECT id, 'unassigned', '[]'::jsonb,
                               'baseline_backfill', 'confirmed'
                        FROM legal_documents
                        ON CONFLICT (document_id) DO NOTHING
                        """
                    )
                )
            return True
        except Exception as exc:
            logger.warning(
                "Organization assignment SQL projection unavailable: %s",
                type(exc).__name__,
            )
            return False

    def document_organization_identity(self, doc_id: str) -> dict[str, Any]:
        """Validate a metadata-only assignment without probing content/vectors."""
        document_id = int(str(doc_id).strip())
        if document_id <= 0:
            raise ValueError("invalid_document_id")
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, law_number FROM legal_documents WHERE id = :document_id"),
                {"document_id": document_id},
            ).mappings().first()
        if row is None:
            raise LookupError("document_not_found")
        return {"document_id": str(row["id"]), "law_number": row["law_number"]}

    def update_document_organization_assignment(
        self,
        doc_id: str,
        *,
        assignment_state: str,
        primary_organization_unit_id: str | None,
        organization_unit_ids: Sequence[str],
        assignment_source: str,
        confirmation_status: str = "confirmed",
    ) -> dict[str, Any]:
        """Upsert the rebuildable SQL projection consumed by Retrieval V2."""

        document_id = int(str(doc_id).strip())
        state = str(assignment_state or "").strip()
        primary = str(primary_organization_unit_id or "").strip() or None
        unit_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in organization_unit_ids
                if str(item or "").strip()
            )
        )
        if state not in {"assigned", "shared", "unassigned"}:
            raise ValueError("document_assignment_state_invalid")
        if state == "assigned" and not primary:
            raise ValueError("document_primary_organization_unit_required")
        if state != "assigned" and (primary or unit_ids):
            raise ValueError("document_nonassigned_state_has_unit_relations")
        if primary and primary not in unit_ids:
            unit_ids.insert(0, primary)
        if not self._organization_projection_schema_available:
            self._organization_projection_schema_available = (
                self._ensure_organization_projection_schema()
            )
        if not self._organization_projection_schema_available:
            raise RuntimeError("organization_projection_schema_unavailable")
        with self._engine.begin() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM legal_documents WHERE id = :document_id FOR UPDATE"),
                {"document_id": document_id},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError("legal_document_not_found")
            connection.execute(
                text(
                    """
                    INSERT INTO legal_document_organization_assignment (
                        document_id, assignment_state,
                        primary_organization_unit_id, organization_unit_ids,
                        assignment_source, confirmation_status, updated_at
                    ) VALUES (
                        :document_id, :assignment_state,
                        :primary_organization_unit_id, CAST(:organization_unit_ids AS JSONB),
                        :assignment_source, :confirmation_status, now()
                    )
                    ON CONFLICT (document_id) DO UPDATE SET
                        assignment_state = EXCLUDED.assignment_state,
                        primary_organization_unit_id = EXCLUDED.primary_organization_unit_id,
                        organization_unit_ids = EXCLUDED.organization_unit_ids,
                        assignment_source = EXCLUDED.assignment_source,
                        confirmation_status = EXCLUDED.confirmation_status,
                        updated_at = now()
                    """
                ),
                {
                    "document_id": document_id,
                    "assignment_state": state,
                    "primary_organization_unit_id": primary,
                    "organization_unit_ids": json.dumps(unit_ids),
                    "assignment_source": str(assignment_source or "admin")[:120],
                    "confirmation_status": str(confirmation_status or "confirmed")[:80],
                },
            )
        return {
            "document_id": str(document_id),
            "assignment_state": state,
            "primary_organization_unit_id": primary,
            "organization_unit_ids": unit_ids,
            "confirmation_status": confirmation_status,
            "projection_updated": True,
        }

    def update_document_metadata(
        self,
        doc_id: str,
        *,
        metadata: Mapping[str, Any],
        expected_revision: str,
    ) -> dict[str, Any]:
        """Update reviewed metadata for a mutable incremental document.

        Frozen release members must use the replacement workflow. This keeps
        a manual correction from silently invalidating release checksums.
        """

        try:
            document_id = int(str(doc_id).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_document_id") from exc
        if document_id <= 0:
            raise ValueError("invalid_document_id")
        if document_id not in self._local_overlay_document_ids(include_inactive=True):
            raise ValueError("immutable_release_document_requires_replacement")

        values = {field: metadata.get(field) for field in _MANAGEMENT_EDITABLE_METADATA_FIELDS}
        effective = values.get("effective_date")
        expired = values.get("expired_date")
        if effective and expired and expired < effective:
            raise ValueError("expired_date_before_effective_date")

        with self._write_lock:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT issued_date, effective_date, expired_date, source_url,
                               gazette_date, signer_title, signer_name,
                               applicability_info, version
                        FROM legal_documents
                        WHERE id = :document_id
                        FOR UPDATE
                        """
                    ),
                    {"document_id": document_id},
                ).mappings().first()
                if row is None:
                    raise LookupError("legal_document_not_found")
                before_revision = _management_metadata_revision(row)
                if str(expected_revision or "") != before_revision:
                    raise ValueError("document_metadata_changed")

                connection.execute(
                    text(
                        """
                        UPDATE legal_documents
                        SET issued_date = :issued_date,
                            effective_date = :effective_date,
                            expired_date = :expired_date,
                            source_url = :source_url,
                            gazette_date = :gazette_date,
                            signer_title = :signer_title,
                            signer_name = :signer_name,
                            applicability_info = :applicability_info,
                            version = :version
                        WHERE id = :document_id
                        """
                    ),
                    {"document_id": document_id, **values},
                )

                chunk_ids = [
                    f"chunk-{int(value)}"
                    for value in connection.execute(
                        text(
                            """
                            SELECT c.id
                            FROM legal_article_chunks c
                            JOIN legal_articles a ON a.id = c.article_id
                            WHERE a.document_id = :document_id
                            ORDER BY c.id
                            """
                        ),
                        {"document_id": document_id},
                    ).scalars()
                ]

            # Saving reviewed document metadata must not depend on optional
            # retrieval vectors.  Some imported records legitimately have no
            # vectors in the mutable overlay; the old all-or-nothing flow
            # rolled the SQL change back and made the Admin form unusable.
            # Update matching vectors when present and report the projection
            # state separately.
            projection_updated = not bool(chunk_ids)
            projection_warning: str | None = None
            if chunk_ids:
                try:
                    if not CHROMA_INCREMENTAL_COLLECTION:
                        raise RuntimeError("incremental_collection_unavailable")
                    collection = chromadb.PersistentClient(path=str(CHROMA_PATH)).get_collection(
                        CHROMA_INCREMENTAL_COLLECTION
                    )
                    existing = collection.get(ids=chunk_ids, include=["metadatas"])
                    existing_ids = [str(value) for value in (existing.get("ids") or [])]
                    existing_metadatas = existing.get("metadatas") or []
                    vector_patch = {
                        "source_url": str(values.get("source_url") or ""),
                        "issued_date": _iso_or_none(values.get("issued_date")) or "",
                        "effective_date": _iso_or_none(values.get("effective_date")) or "",
                        "expired_date": _iso_or_none(values.get("expired_date")) or "",
                    }
                    if existing_ids:
                        collection.update(
                            ids=existing_ids,
                            metadatas=[
                                {**dict(item or {}), **vector_patch}
                                for item in existing_metadatas
                            ],
                        )
                        _publish_incremental_overlay_snapshot(collection)
                    projection_updated = len(existing_ids) == len(chunk_ids)
                    if not projection_updated:
                        projection_warning = "retrieval_vectors_missing"
                except Exception as exc:
                    projection_updated = False
                    projection_warning = "retrieval_projection_unavailable"
                    logger.warning(
                        "Document %s metadata saved; optional vector metadata sync skipped: %s",
                        document_id,
                        type(exc).__name__,
                    )

        updated = self.management_document_detail(str(document_id))
        return {
            "document_id": str(document_id),
            "metadata_revision": updated["document"].get("metadata_revision"),
            "document": updated["document"],
            "projection_updated": projection_updated,
            "projection_warning": projection_warning,
        }

    def _serving_document_clause(
        self,
        params: dict[str, Any],
        *,
        alias: str = "d",
        parameter: str = "serving_document_ids",
        allowed_ids: Sequence[int] | None = None,
    ) -> str:
        allowed = (
            list(allowed_ids)
            if allowed_ids is not None
            else self._request_serving_document_ids()
        )
        if allowed is None:
            return ""
        if not allowed:
            if self._request_organization_scope_enforced():
                return "AND FALSE"
            raise RuntimeError("serving_manifest_has_no_documents")
        params[parameter] = sorted(allowed)
        # A candidate manifest can contain thousands of document ids. Expanding
        # them into ``IN (:id_1, :id_2, ...)`` makes PostgreSQL repeatedly parse
        # and bind a multi-thousand-parameter statement for every issue/query.
        # PostgreSQL's array operator keeps the same exact serving scope while
        # reducing parse/bind overhead substantially.
        return f"AND {alias}.id = ANY(:{parameter})"

    def _bind_serving_document_clause(
        self,
        statement: Any,
        *,
        parameter: str = "serving_document_ids",
    ) -> Any:
        if not self._request_serving_document_ids():
            return statement
        return statement.bindparams(
            bindparam(parameter, type_=ARRAY(Integer))
        )

    def _local_overlay_scope_clause(
        self,
        params: dict[str, Any],
        *,
        alias: str = "d",
    ) -> str:
        """Allow approved local-overlay documents through the core scope gate."""
        params["local_overlay_document_ids"] = sorted(
            self._local_overlay_document_ids()
        )
        return f"{alias}.id = ANY(:local_overlay_document_ids)"

    def _bind_local_overlay_scope_clause(self, statement: Any) -> Any:
        return statement.bindparams(
            bindparam("local_overlay_document_ids", type_=ARRAY(Integer))
        )

    def _request_audience(self) -> str:
        context = getattr(self, "_serving_request_context", None)
        return str(getattr(context, "audience", "citizen") or "citizen")

    def _local_overlay_document_ids(self, *, include_inactive: bool = False) -> set[int]:
        """Return active documents imported into the local admin overlay.

        The release manifest remains the boundary for the immutable baseline.
        Local admin-approved documents are additive and are deliberately
        identified by their import provenance rather than by a guessed title
        or law number.
        """
        # A SQL-only management process need not load a GPU/model/Chroma
        # object to see approved overlay records. The explicit overlay flag
        # and provenance remain mandatory; frozen release membership is not
        # broadened to unrelated manual data.
        if not CHROMA_INCREMENTAL_COLLECTION:
            return set()
        if not _incremental_import_enabled():
            return set()
        engine = getattr(self, "_engine", None)
        if engine is None:
            # Read-only/unit and management projections may intentionally use
            # a retriever without opening PostgreSQL.  In that case the frozen
            # serving scope remains authoritative and there is no local SQL
            # overlay to merge.
            return set()
        eligibility = "" if include_inactive else """
            AND (d.status IN ('archived','expired','historical','replaced') OR
                 (d.status = 'active' AND EXISTS (
                     SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE
                 )))
        """
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT d.id FROM legal_documents d
                WHERE d.collection_source LIKE 'manual_vnlegal_lal_import%'
                  AND d.status IN ('active','archived','expired','historical','replaced','blocked')
            """ + eligibility)).scalars().all()
        return {int(value) for value in rows if value is not None}

    def _local_overlay_chunk_ids(self) -> set[int]:
        document_ids = sorted(self._local_overlay_document_ids())
        if not document_ids:
            return set()
        with self._engine.connect() as connection:
            rows = connection.execute(
                text("SELECT c.id FROM legal_article_chunks c "
                     "JOIN legal_articles a ON a.id = c.article_id "
                     "WHERE a.status = 'active' AND a.document_id = ANY(:document_ids)").bindparams(
                         bindparam("document_ids", type_=ARRAY(Integer))
                     ), {"document_ids": document_ids},
            ).scalars().all()
        return {int(value) for value in rows if value is not None}

    def _set_request_audience(self, audience: str | None) -> None:
        context = getattr(self, "_serving_request_context", None)
        if context is None:
            context = local()
            self._serving_request_context = context
        context.audience = str(audience or "citizen").strip().casefold()

    def _load_request_organization_scope(
        self,
        audience: str | None,
        organization_unit_id: str | None,
    ) -> tuple[set[int], set[int]] | None:
        """Load the hard document boundary for one authenticated officer."""

        if str(audience or "citizen").strip().casefold() != "officer":
            return None
        unit_id = str(organization_unit_id or "").strip()
        engine = getattr(self, "_engine", None)
        if not unit_id or engine is None:
            return set(), set()
        try:
            with engine.connect() as connection:
                document_ids = {
                    int(value)
                    for value in connection.execute(
                        text(
                            """
                            SELECT oa.document_id
                            FROM legal_document_organization_assignment oa
                            WHERE oa.confirmation_status = 'confirmed'
                              AND (
                                oa.assignment_state = 'shared'
                                OR (
                                  oa.assignment_state = 'assigned'
                                  AND (
                                    oa.primary_organization_unit_id = :organization_unit_id
                                    OR COALESCE(oa.organization_unit_ids, '[]'::jsonb)
                                       @> CAST(:organization_unit_json AS jsonb)
                                  )
                                )
                              )
                            """
                        ),
                        {
                            "organization_unit_id": unit_id,
                            "organization_unit_json": json.dumps([unit_id]),
                        },
                    ).scalars().all()
                    if value is not None
                }
                if not document_ids:
                    return set(), set()
                chunk_ids = {
                    int(value)
                    for value in connection.execute(
                        text(
                            "SELECT c.id FROM legal_article_chunks c "
                            "JOIN legal_articles a ON a.id = c.article_id "
                            "WHERE a.document_id = ANY(:organization_document_ids)"
                        ).bindparams(
                            bindparam(
                                "organization_document_ids", type_=ARRAY(Integer)
                            )
                        ),
                        {"organization_document_ids": sorted(document_ids)},
                    ).scalars().all()
                    if value is not None
                }
            return document_ids, chunk_ids
        except Exception as exc:
            # Authorization projection failure must never broaden an officer
            # request to the citizen corpus.
            logger.error(
                "Officer organization scope unavailable for unit {}: {}",
                unit_id,
                type(exc).__name__,
            )
            return set(), set()

    def _set_request_organization_scope(
        self,
        audience: str | None,
        organization_unit_id: str | None,
        *,
        document_ids: set[int] | None = None,
        chunk_ids: set[int] | None = None,
    ) -> tuple[set[int], set[int]] | None:
        context = getattr(self, "_serving_request_context", None)
        if context is None:
            context = local()
            self._serving_request_context = context
        is_officer = str(audience or "citizen").strip().casefold() == "officer"
        context.organization_scope_enforced = is_officer
        context.organization_unit_id = (
            str(organization_unit_id or "").strip() or None
        )
        if not is_officer:
            context.organization_document_ids = None
            context.organization_chunk_ids = None
            return None
        loaded = (
            (set(document_ids or set()), set(chunk_ids or set()))
            if document_ids is not None or chunk_ids is not None
            else self._load_request_organization_scope(
                audience, organization_unit_id
            )
        ) or (set(), set())
        context.organization_document_ids = set(loaded[0])
        context.organization_chunk_ids = set(loaded[1])
        return set(loaded[0]), set(loaded[1])

    def _request_organization_scope_enforced(self) -> bool:
        context = getattr(self, "_serving_request_context", None)
        return bool(getattr(context, "organization_scope_enforced", False))

    def _request_serving_document_ids(self) -> set[int] | None:
        overlay_ids = self._local_overlay_document_ids()
        scope = getattr(self, "_serving_scope", None)
        if scope is not None:
            allowed = set(scope.document_ids_for(self._request_audience())) | overlay_ids
        else:
            configured = getattr(self, "_serving_allowed_document_ids", None)
            allowed = (set(configured) | overlay_ids) if configured is not None else (overlay_ids or None)
        if self._request_organization_scope_enforced():
            context = getattr(self, "_serving_request_context", None)
            organization_ids = set(
                getattr(context, "organization_document_ids", set()) or set()
            )
            return organization_ids if allowed is None else set(allowed) & organization_ids
        return allowed

    def _request_serving_chunk_ids(self) -> set[int] | None:
        overlay_ids = self._local_overlay_chunk_ids()
        scope = getattr(self, "_serving_scope", None)
        if scope is not None:
            allowed = set(scope.chunk_ids_for(self._request_audience())) | overlay_ids
        else:
            configured = getattr(self, "_shadow_allowed_chunk_ids", None)
            if not configured:
                allowed = overlay_ids or None
            else:
                allowed = set(configured.get("core") or configured.get("expanded") or set()) | overlay_ids
        if self._request_organization_scope_enforced():
            context = getattr(self, "_serving_request_context", None)
            organization_ids = set(
                getattr(context, "organization_chunk_ids", set()) or set()
            )
            return organization_ids if allowed is None else set(allowed) & organization_ids
        return allowed

    def _vector_collection_for(
        self,
        query_classification: Mapping[str, Any],
        retrieval_tier: str,
    ) -> Any:
        """Select current versus temporal ANN collection after M4 routing.

        Historical/as-of queries use the explicitly configured temporal
        collection when one exists.  With no V2 temporal collection configured
        the legacy active collection remains the compatibility fallback; V2
        release validation must configure a separate temporal collection.
        Unknown/conflict queries never reach this method because M4 blocks
        them before retrieval.
        """

        base = self._source_collection if retrieval_tier == "expanded" else self._collection
        temporal_scope = str(query_classification.get("temporal_scope") or "").strip().casefold()
        if temporal_scope == "historical" and self._temporal_collection is not None:
            return self._temporal_collection
        return base

    def _require_serving_scope(self) -> None:
        if str(os.getenv("LEGAL_SERVING_MANIFEST_REQUIRED") or "").strip().casefold() in {
            "1", "true", "yes", "on"
        } and getattr(self, "_serving_scope", None) is None:
            raise RuntimeError("serving_manifest_required")

    def assert_document_access(
        self, document_id: int, audience: str | None = None
    ) -> None:
        self._set_request_audience(audience)
        self._require_serving_scope()
        allowed = self._request_serving_document_ids()
        if allowed is not None and int(document_id) not in allowed:
            raise PermissionError("document_outside_serving_manifest")

    def _document_status_predicate(
        self, alias: str = "d", temporal_scope: str = "current"
    ) -> str:
        """Return a status gate, with staging allowed only in candidate benchmark."""

        if (
            getattr(self, "_benchmark_allow_staging", False)
            and getattr(self, "_serving_allowed_document_ids", None)
        ):
            return (
                f"({alias}.status = 'active' OR "
                f"({alias}.status = 'staging' AND {alias}.id = ANY(:serving_document_ids)))"
            )
        if str(temporal_scope).casefold() == "historical":
            statuses = "'active','archived','expired','historical','replaced'"
            return f"{alias}.status IN ({statuses})"
        return f"{alias}.status = 'active'"

    def _article_status_predicate(self, alias: str = "a") -> str:
        """Return an article status gate paired with the benchmark document scope."""

        if (
            getattr(self, "_benchmark_allow_staging", False)
            and getattr(self, "_serving_allowed_document_ids", None)
        ):
            return (
                f"({alias}.status = 'active' OR "
                f"({alias}.status = 'staging' AND {alias}.document_id = ANY(:serving_document_ids)))"
            )
        return f"{alias}.status = 'active'"

    def _is_current_for_serving(
        self,
        row: dict[str, Any],
        as_of: date,
        *,
        temporal_scope: str = "current",
    ) -> bool:
        candidate = row
        if str(temporal_scope).casefold() == "historical" and str(
            row.get("document_status") or ""
        ).casefold() in {"archived", "expired", "historical", "replaced"}:
            # `_is_current` owns the date/article checks. Historical mode only
            # broadens the accepted storage state; it does not bypass dates.
            candidate = {**row, "document_status": "active"}
        return _is_current(
            candidate,
            as_of,
            allow_staging=bool(getattr(self, "_benchmark_allow_staging", False)),
        )

    def _normalize_candidate_effective_status(
        self, row: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Expose legal effectivity separately from candidate storage state.

        Candidate-only manifests may intentionally select a document that is
        still ``staging`` in PostgreSQL.  The retrieval status gate already
        admits that row only when the immutable candidate manifest contains its
        document id and its dates/articles are current.  Preserve the storage
        state for audit, but project an ``effective_status`` of ``active`` so
        downstream answer grounding does not confuse approval state with legal
        effectivity.  Public/baseline serving is unchanged and remains
        fail-closed for staging rows.
        """

        normalized = dict(row)
        if normalized.get("effective_status"):
            return normalized
        status = str(normalized.get("document_status") or "").strip().casefold()
        document_id = _as_int(normalized.get("document_id"))
        serving_ids = getattr(self, "_serving_allowed_document_ids", None)
        if (
            status == "staging"
            and bool(getattr(self, "_benchmark_allow_staging", False))
            and serving_ids
            and document_id in serving_ids
        ):
            normalized["effective_status"] = "active"
            if str(normalized.get("article_status") or "").strip().casefold() == "staging":
                normalized["effective_article_status"] = "active"
        return normalized

    def _quality_sql(
        self, *, allow_exact_missing_domain: bool = False
    ) -> tuple[str, str, str]:
        """Return optional sidecar SQL without making the migration mandatory."""

        if self._quality_sidecar_available is None:
            try:
                with self._engine.connect() as connection:
                    self._quality_sidecar_available = bool(
                        connection.execute(
                            text("SELECT to_regclass('legal_chunk_quality') IS NOT NULL")
                        ).scalar_one()
                    )
            except Exception:
                self._quality_sidecar_available = False
        if not self._quality_sidecar_available:
            return "", "", "a.title"
        exact_missing_domain_clause = (
            """
                OR (
                    quality.quality_reasons = ARRAY['missing_domain']::TEXT[]
                    AND NULLIF(quality.cleaned_article_title, '') IS NOT NULL
                    AND NULLIF(BTRIM(c.content), '') IS NOT NULL
                )
            """
            if allow_exact_missing_domain
            else ""
        )
        return (
            "LEFT JOIN legal_chunk_quality quality ON quality.chunk_id = c.id "
            "AND quality.quality_version = 'legal-chunk-quality-v1'",
            """
            AND (
                COALESCE(quality.eligible, TRUE) = TRUE
                OR (
                    legal_normalize_identifier(d.law_number) IN (
                        '15/2012/QH13',
                        '16/2022/ND-CP'
                    )
                    AND quality.quality_reasons <@
                        ARRAY['missing_domain', 'noisy_article_title']::TEXT[]
                    AND NULLIF(quality.cleaned_article_title, '') IS NOT NULL
                    AND NULLIF(BTRIM(c.content), '') IS NOT NULL
                )
                {exact_missing_domain_clause}
            )
            """.format(
                exact_missing_domain_clause=exact_missing_domain_clause
            ),
            "COALESCE(NULLIF(quality.cleaned_article_title, ''), a.title)",
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
        config = AutoConfig.from_pretrained(MODEL_PATH, local_files_only=True)
        tokenizer = self._tokenizer or AutoTokenizer.from_pretrained(
            MODEL_PATH, local_files_only=True
        )
        try:
            model = AutoModel.from_pretrained(
                MODEL_PATH,
                config=config,
                local_files_only=True,
                torch_dtype=dtype,
            )
        except TypeError:
            model = AutoModel.from_pretrained(
                MODEL_PATH,
                config=config,
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
        if (
            self._model is not None
            and self._collection is not None
            and self._source_collection is not None
            and self._temporal_collection is not None
            and (
                not CHROMA_INCREMENTAL_COLLECTION
                or self._incremental_collection is not None
            )
        ):
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
                try:
                    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
                    serving_collection = (
                        self._serving_scope.collection_name
                        if self._serving_scope
                        else CHROMA_COLLECTION
                    )
                    self._collection = client.get_collection(serving_collection)
                    # A serving manifest is the complete vector boundary. The
                    # expanded tier may change ranking policy, but it may not
                    # query a source-wide collection outside that manifest.
                    self._source_collection = (
                        self._collection
                        if self._serving_scope
                        else client.get_collection(CHROMA_SOURCE_COLLECTION)
                    )
                    if CHROMA_TEMPORAL_COLLECTION:
                        self._temporal_collection = (
                            self._collection
                            if CHROMA_TEMPORAL_COLLECTION == serving_collection
                            else client.get_collection(CHROMA_TEMPORAL_COLLECTION)
                        )
                    else:
                        # Compatibility fallback for the immutable M2
                        # baseline. V2 must set the dedicated collection.
                        self._temporal_collection = self._collection
                    if CHROMA_INCREMENTAL_COLLECTION:
                        # The local overlay is optional for read-only serving.
                        # A malformed/missing overlay must never hide the
                        # immutable baseline; import mode validates the target
                        # again before writing.
                        try:
                            require_staging_collection_target(
                                CHROMA_INCREMENTAL_COLLECTION, CHROMA_PATH
                            )
                            try:
                                persistent_incremental = _configure_incremental_collection(
                                    client.get_collection(CHROMA_INCREMENTAL_COLLECTION)
                                )
                                if MANAGEMENT_ONLY:
                                    reconcile_collection_from_snapshot(
                                        persistent_incremental,
                                        path=ADMIN_OVERLAY_SNAPSHOT_PATH,
                                        collection_name=CHROMA_INCREMENTAL_COLLECTION,
                                    )
                                    self._incremental_collection = persistent_incremental
                                else:
                                    self._incremental_collection = OverlaySnapshotCollection(
                                        ADMIN_OVERLAY_SNAPSHOT_PATH,
                                        collection_name=CHROMA_INCREMENTAL_COLLECTION,
                                        fallback=persistent_incremental,
                                    )
                            except Exception:
                                if _incremental_import_enabled():
                                    self._incremental_collection = _configure_incremental_collection(
                                        client.get_or_create_collection(
                                            CHROMA_INCREMENTAL_COLLECTION,
                                            metadata=CHROMA_INCREMENTAL_METADATA,
                                        )
                                    )
                                else:
                                    self._incremental_collection = None
                        except Exception as exc:
                            logger.warning("Local legal overlay unavailable; baseline remains authoritative: %s", exc)
                            self._incremental_collection = None
                    if self._serving_scope:
                        observed = int(self._collection.count())
                        if observed != len(self._serving_scope.chunk_ids):
                            raise RuntimeError(
                                "serving_manifest_collection_count_mismatch"
                            )
                    self._vector_index_available = True
                except Exception as exc:
                    if str(exc).startswith("serving_manifest_"):
                        raise
                    # A persisted index may have been written by an older
                    # Chroma runtime. Keep legal search available through the
                    # reviewed SQL lexical path rather than serving a 503 or
                    # treating unrelated evidence as a substitute.
                    self._collection = _VectorUnavailableCollection()
                    self._source_collection = self._collection
                    self._temporal_collection = self._collection
                    self._incremental_collection = None
                    self._vector_index_available = False

    def _query_with_incremental(
        self,
        primary: Any,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
    ) -> dict[str, list[list[Any]]]:
        payloads = [
            primary.query(
                query_embeddings=query_embeddings,
                n_results=n_results,
                include=["metadatas", "distances"],
            )
        ]
        incremental = getattr(self, "_incremental_collection", None)
        if incremental is not None and incremental is not primary:
            try:
                if int(incremental.count()) > 0:
                    payloads.append(
                        incremental.query(
                            query_embeddings=query_embeddings,
                            n_results=n_results,
                            include=["metadatas", "distances"],
                        )
                    )
            except Exception:
                # The immutable primary remains available when the optional
                # local incremental index is temporarily unavailable.
                pass
        return _merge_vector_query_payloads(payloads, n_results=n_results)

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

    def encode_queries(self, queries: list[str]) -> list[np.ndarray]:
        """Batch missing query embeddings and populate the shared query cache."""

        if not queries:
            return []
        prepared = [_rewrite_query(query).strip() for query in queries]
        keys = [
            query_vector_cache_key(query, self._model_fingerprint)
            for query in prepared
        ]
        resolved: dict[str, np.ndarray] = {}
        missing_by_key: dict[str, str] = {}
        for key, query in zip(keys, prepared):
            cached = self._query_vector_cache.get(key)
            if cached is None:
                missing_by_key.setdefault(key, query)
            else:
                resolved[key] = cached
        if missing_by_key:
            with self._vector_compute_lock:
                still_missing: list[tuple[str, str]] = []
                for key, query in missing_by_key.items():
                    cached = self._query_vector_cache.get(key)
                    if cached is None:
                        still_missing.append((key, query))
                    else:
                        resolved[key] = cached
                if still_missing:
                    vectors = self._compute_passage_embeddings(
                        [QUERY_PREFIX + query for _, query in still_missing],
                        batch_size=6,
                    )
                    for (key, _), vector in zip(still_missing, vectors):
                        embedding = np.asarray(vector, dtype=np.float32)
                        self._query_vector_cache.set(key, embedding)
                        resolved[key] = embedding
        return [resolved[key].copy() for key in keys]

    def prepare_exact_vector_index(self) -> None:
        """Load the active serving collection into a normalized flat matrix.

        This is intentionally opt-in and benchmark-safe. Chroma remains the
        source of IDs/metadata; the matrix is only an alternative ANN backend
        for the same collection and exact cosine scores.
        """

        if not self._exact_vector_search_enabled or self._collection is None:
            return
        collection_name = str(getattr(self._collection, "name", ""))
        if self._exact_vector_matrix is not None and self._exact_vector_collection_name == collection_name:
            return
        with self._exact_vector_lock:
            if self._exact_vector_matrix is not None and self._exact_vector_collection_name == collection_name:
                return
            # Chroma's SQLite metadata layer rejects a single get() for large
            # collections on Windows (too many SQL variables). Page it in
            # bounded slices while retaining one contiguous float32 matrix.
            page_size = 5000
            ids: list[str] = []
            embedding_pages: list[np.ndarray] = []
            metadata: list[dict[str, Any]] = []
            offset = 0
            while True:
                payload = self._collection.get(
                    include=["embeddings", "metadatas"],
                    limit=page_size,
                    offset=offset,
                )
                page_ids = [str(value) for value in (payload.get("ids") or [])]
                if not page_ids:
                    break
                page_embeddings = payload.get("embeddings")
                if page_embeddings is None:
                    raise RuntimeError("exact_vector_index_embeddings_missing")
                ids.extend(page_ids)
                embedding_pages.append(np.asarray(page_embeddings, dtype=np.float32))
                metadata.extend(dict(item or {}) for item in (payload.get("metadatas") or []))
                offset += len(page_ids)
                if len(page_ids) < page_size:
                    break
            if not ids or not embedding_pages:
                raise RuntimeError("exact_vector_index_empty")
            matrix = np.concatenate(embedding_pages, axis=0)
            if matrix.ndim != 2 or matrix.shape[0] != len(ids):
                raise RuntimeError("exact_vector_index_shape_mismatch")
            # Chroma stores the same normalized VNLegal-LAL vectors used by
            # HNSW. Normalize defensively so cosine distance is identical even
            # if a future import contains small floating-point drift.
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.maximum(norms, 1e-12)
            self._exact_vector_ids = ids
            self._exact_vector_metadatas = metadata
            self._exact_vector_matrix = np.ascontiguousarray(matrix, dtype=np.float32)
            self._exact_vector_collection_name = collection_name

    def load_hydration_cache(self, path: str | Path, *, manifest_sha256: str) -> None:
        """Load a checksum-bound candidate row cache for isolated benchmarks."""

        import pickle

        with Path(path).expanduser().resolve().open("rb") as stream:
            payload = pickle.load(stream)
        if payload.get("schema_version") not in {
            "legal-candidate-hydration-cache-v1",
            "legal-candidate-hydration-cache-v2",
        }:
            raise RuntimeError("hydration_cache_schema_mismatch")
        if str(payload.get("manifest_sha256") or "") != str(manifest_sha256):
            raise RuntimeError("hydration_cache_manifest_mismatch")
        rows = payload.get("rows") or {}
        self._hydration_cache_rows = {int(key): dict(value) for key, value in rows.items()}
        self._hydration_cache_parents = {
            int(key): dict(value)
            for key, value in (payload.get("parents") or {}).items()
        }
        article_chunk_counts = {
            int(key): int(value or 0)
            for key, value in (payload.get("article_chunk_counts") or {}).items()
        }
        article_chunks: dict[int, list[tuple[int, int]]] = {}
        # Build a law/article index only for articles whose complete chunk set
        # is present in the cache. This permits exact Article lookups to avoid
        # repeated SQL while preserving the existing fail-closed completeness
        # check for partially cached articles.
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        cached_counts: dict[int, int] = {}
        for row in self._hydration_cache_rows.values():
            article_id = _as_int(row.get("article_id"))
            if article_id is None:
                continue
            chunk_id = _as_int(row.get("chunk_id"))
            chunk_index = _as_int(row.get("chunk_index"))
            if chunk_id is not None and chunk_index is not None:
                article_chunks.setdefault(article_id, []).append((chunk_index, chunk_id))
            cached_counts[article_id] = cached_counts.get(article_id, 0) + 1
            parent = self._hydration_cache_parents.get(article_id) or {}
            enriched = dict(row)
            if parent.get("parent_content"):
                enriched["article_content"] = parent.get("parent_content")
            if parent.get("parent_heading"):
                enriched["article_title"] = parent.get("parent_heading")
            law_key = _normalized_identity(enriched.get("law_number"))
            article_key = str(enriched.get("article_number") or "").strip().casefold()
            if law_key and article_key:
                grouped.setdefault((law_key, article_key), []).append(enriched)
        complete_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for key, values in grouped.items():
            article_id = _as_int(values[0].get("article_id"))
            expected = article_chunk_counts.get(article_id or 0)
            if expected and cached_counts.get(article_id or 0) == expected:
                for value in values:
                    value["exact_article_chunk_count"] = expected
                complete_index[key] = values
        self._hydration_cache_exact_index = complete_index
        self._hydration_cache_article_chunk_counts = article_chunk_counts
        for values in article_chunks.values():
            values.sort(key=lambda item: (item[0], item[1]))
        self._hydration_cache_article_chunks = article_chunks
        self._hydration_cache_manifest_sha256 = str(manifest_sha256)

    def _exact_vector_query(self, vectors: list[np.ndarray], candidate_count: int) -> list[dict[str, list[list[Any]]]]:
        self.prepare_exact_vector_index()
        matrix = self._exact_vector_matrix
        if matrix is None:
            raise RuntimeError("exact_vector_index_not_ready")
        query_matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(query_matrix, axis=1, keepdims=True)
        query_matrix = query_matrix / np.maximum(norms, 1e-12)
        scores = query_matrix @ matrix.T
        limit = min(max(1, int(candidate_count)), matrix.shape[0])
        output: list[dict[str, list[list[Any]]]] = []
        for row in scores:
            indexes = np.argpartition(-row, limit - 1)[:limit]
            indexes = indexes[np.argsort(-row[indexes], kind="stable")]
            output.append(
                {
                    "ids": [[self._exact_vector_ids[int(index)] for index in indexes]],
                    "metadatas": [[self._exact_vector_metadatas[int(index)] for index in indexes]],
                    "distances": [[float(1.0 - row[int(index)]) for index in indexes]],
                }
            )
        return output

    def prefetch_batch_vectors(
        self,
        queries: list[str],
        *,
        retrieval_tier: str,
        candidate_count: int,
        telemetry_out: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute all vector variants in one Chroma call.

        Local Chroma serializes collection queries behind ``_query_lock``.
        Calling it once per sub-query made a six-query legal question pay the
        persistent-index startup/IPC cost six times. Chroma accepts multiple
        query embeddings natively, so keep independent result rows while
        sharing one bounded collection call.
        """

        if not queries:
            return []
        embedding_started = perf_counter()
        vectors = self.encode_queries(queries)
        embedding_ms = (perf_counter() - embedding_started) * 1000
        collection = (
            self._source_collection
            if retrieval_tier == "expanded"
            else self._collection
        )
        ann_started = perf_counter()
        if self._exact_vector_search_enabled:
            raw_rows = self._exact_vector_query(vectors, candidate_count)
            raw = {
                "ids": [item["ids"][0] for item in raw_rows],
                "metadatas": [item["metadatas"][0] for item in raw_rows],
                "distances": [item["distances"][0] for item in raw_rows],
            }
        else:
            with self._query_lock:
                raw = self._query_with_incremental(
                    collection,
                    query_embeddings=[vector.tolist() for vector in vectors],
                    n_results=candidate_count,
                )
        ann_ms = (perf_counter() - ann_started) * 1000
        if telemetry_out is not None:
            telemetry_out.update(
                {
                    "batch_size": len(queries),
                    "embedding_ms": round(embedding_ms, 1),
                    "ann_ms": round(ann_ms, 1),
                    "backend": "exact_flat" if self._exact_vector_search_enabled else "hnsw",
                }
            )
        self._last_vector_telemetry = {
            "batch_size": len(queries),
            "embedding_ms": round(embedding_ms, 1),
            "ann_ms": round(ann_ms, 1),
            "backend": "exact_flat" if self._exact_vector_search_enabled else "hnsw",
        }
        allowed = self._request_serving_chunk_ids()
        output: list[dict[str, Any]] = []
        for index in range(len(queries)):
            ids = list(raw.get("ids", [])[index])
            metadatas = list(raw.get("metadatas", [])[index])
            distances = list(raw.get("distances", [])[index])
            if allowed is not None:
                kept = []
                for item_id, metadata, distance in zip(ids, metadatas, distances):
                    metadata = metadata or {}
                    chunk_id = _as_int(metadata.get("chunk_id"))
                    if chunk_id is None and str(item_id).startswith("chunk-"):
                        chunk_id = _as_int(str(item_id)[6:])
                    if chunk_id in allowed:
                        kept.append((item_id, metadata, distance))
                ids = [item[0] for item in kept]
                metadatas = [item[1] for item in kept]
                distances = [item[2] for item in kept]
            output.append(
                {"ids": [ids], "metadatas": [metadatas], "distances": [distances]}
            )
        return output

    def _compute_passage_embeddings(
        self, passages: list[str], batch_size: int, max_length: int = 2048
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
                    max_length=max(1, int(max_length)),
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
        self,
        passages: list[str],
        batch_size: int = 12,
        *,
        max_length: int = 2048,
    ) -> list[list[float]]:
        try:
            return self._compute_passage_embeddings(passages, batch_size, max_length)
        except torch.cuda.OutOfMemoryError:
            if (
                self._requested_device != "auto"
                or self._embedding_device.type != "cuda"
            ):
                raise
            self._activate_cpu_fallback("cuda_oom")
            return self._compute_passage_embeddings(passages, batch_size, max_length)

    def prewarm(self) -> None:
        if self._ready:
            return
        with self._prewarm_lock:
            if self._ready:
                return
            self._load()
            vector = self.encode_query("tra cứu thủ tục hành chính cấp xã")
            # Loading a persisted HNSW graph is the expensive part of the
            # first request. Warm both the reviewed core and the source-wide
            # supplemental collection before readiness becomes true so the
            # first citizen question does not pay two cold-index penalties.
            try:
                with self._query_lock:
                    warmed_collection_ids: set[int] = set()
                    for collection in (
                        self._collection,
                        self._source_collection,
                        self._incremental_collection,
                    ):
                        if collection is None:
                            continue
                        collection_identity = id(collection)
                        if collection_identity in warmed_collection_ids:
                            continue
                        warmed_collection_ids.add(collection_identity)
                        # Keep one metadata+distance probe per collection so
                        # readiness remains a bounded, deterministic startup
                        # operation. The benchmark-only warmup path may add a
                        # separate distance-only probe when measuring HNSW
                        # cold-tail behavior; production startup must not do
                        # two Chroma queries per collection.
                        collection.query(
                            query_embeddings=[vector.tolist()],
                            n_results=1,
                            include=["metadatas", "distances"],
                        )
            except Exception:
                # Preserve the SQL-only fallback when a persisted Chroma
                # index cannot be queried by this local runtime.
                self._collection = _VectorUnavailableCollection()
                self._source_collection = self._collection
                self._vector_index_available = False
            self._ready = True

    def preview_import(self, request: LegalImportRequest, *, for_review: bool = False) -> dict[str, Any]:
        structure, articles, chunks = _build_import_units(request)
        warnings: list[str] = []
        errors: list[str] = []
        today = date.today()
        review_issues = warnings if for_review else errors
        if not request.confirmed_official_source:
            review_issues.append("Người duyệt cần đối chiếu nội dung với nguồn hoặc tệp gốc trước khi kích hoạt tra cứu.")
        from api.legal_source_input import valid_source_reference
        if not request.uploaded_pdf_sha256 and not valid_source_reference(request.source_url):
            review_issues.append("Bổ sung liên kết HTTP/HTTPS hoặc tệp gốc để đối chiếu trước khi kích hoạt tra cứu.")
        if request.effective_date > today:
            review_issues.append("Văn bản chưa có hiệu lực; có thể gửi duyệt, chưa dùng để trả lời pháp luật hiện hành.")
        if request.expired_date and request.expired_date <= today:
            review_issues.append("Văn bản đã hết hiệu lực; có thể gửi đối chiếu, chưa dùng để trả lời pháp luật hiện hành.")
        if request.issued_date and request.effective_date < request.issued_date:
            errors.append("Ngày có hiệu lực không thể trước ngày ban hành.")
        assignment_state = str(
            request.organization_assignment_state
            or ("assigned" if request.primary_organization_unit_id else "unassigned")
        )
        assignment_units = [
            str(item).strip()
            for item in [
                request.primary_organization_unit_id,
                *request.organization_unit_ids,
            ]
            if str(item or "").strip()
        ]
        if assignment_state == "assigned" and not request.primary_organization_unit_id:
            errors.append("Văn bản đã phân công phải có phòng ban chủ trì.")
        if assignment_state != "assigned" and assignment_units:
            errors.append("Văn bản dùng chung hoặc chưa phân công không được gắn phòng ban.")
        normalized_scope = " ".join(_normalized_terms(
            f"{request.scope} {request.issuing_agency} {request.title}"
        ))
        explicit_local_scope = str(request.scope or "").strip().casefold() in {
            "local",
            "phuong xa",
            "phuong/xã",
            "cap xa",
            "cấp xã",
        }
        is_hai_phong = "hai phong" in normalized_scope
        is_central = any(
            marker in normalized_scope
            for marker in ("trung uong", "toan quoc", "ca nuoc", "quoc hoi", "chinh phu", "bo ")
        )
        if not is_hai_phong and not is_central and not explicit_local_scope:
            errors.append(
                "Chỉ nhận văn bản Trung ương, văn bản áp dụng tại Hải Phòng hoặc văn bản cấp phường/xã đã được admin xác nhận."
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
            duplicate_rows = _duplicate_document_rows(connection, [request.law_number])
            from api.legal_replacement_lineage import retired_ancestor_ids
            ancestors = retired_ancestor_ids(connection, request.replacement_of_document_id)
            field = connection.execute(
                text("SELECT id, name FROM legal_fields WHERE id = :field_id"),
                {"field_id": request.field_id},
            ).mappings().first()
        replacement_id = request.replacement_of_document_id
        duplicate = next(
            (
                row
                for row in duplicate_rows
                if not (
                    replacement_id is not None
                    and int(row.get("id") or 0) == int(replacement_id)
                )
                and _same_legal_document_identity(
                    dict(row),
                    request.model_dump(),
                )
                and not (int(row.get('id') or 0) in ancestors and row.get('status') in {'archived', 'blocked', 'expired'})
            ),
            None,
        )
        if duplicate:
            errors.append(
                f"Định danh pháp lý xung đột với văn bản #{duplicate['id']}: {duplicate['title']}."
            )
        if len(duplicate_rows) > 1000:
            errors.append("Chưa kiểm tra hết các bản cùng số hiệu; cần đối chiếu trước khi nhập.")
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
        incremental_import = _incremental_import_enabled()
        legacy_direct_import = _legacy_direct_import_enabled()
        if not incremental_import and not legacy_direct_import:
            raise RuntimeError(
                "direct_active_collection_mutation_disabled; "
                "configure_an_incremental_collection_or_use_staging_release_import"
            )
        preview = self.preview_import(request)
        if not preview["valid"]:
            raise ValueError(" | ".join(preview["errors"]))

        structure, articles, _prepared_chunks = _build_import_units(request)
        chunk_records: list[dict[str, Any]] = []
        document_id: int | None = None
        vector_ids: list[str] = []
        activation_receipt: dict[str, Any] | None = None

        with self._write_lock:
            try:
                with self._engine.begin() as connection:
                    _assert_no_import_duplicate(connection, request)
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
                            **request.model_dump(
                                exclude={
                                    "content",
                                    "confirmed_official_source",
                                    "domain_slug",
                                    "replacement_of_document_id",
                                    "structure",
                                }
                            ),
                            "structure": structure,
                        },
                    ).scalar_one())
                    from api.legal_activation_journal import bind_activation_document
                    bind_activation_document(connection, request.activation_version_key, document_id)

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
                            "domain": _import_domain_slug(
                                request.domain_slug,
                                request.field_id,
                                preview["field"]["name"],
                            ),
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
                                "article_number": article["article_number"],
                                "title": article["title"],
                                "content": article["content"],
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
                                {
                                    "article_id": article_id,
                                    "chunk_index": chunk["chunk_index"],
                                    "heading": chunk["heading"],
                                    "content": chunk["content"],
                                },
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
                            record.get("heading"),
                            record["content"],
                        ) if value
                    )
                    for record in chunk_records
                ]
                try:
                    import_batch_size = max(1, min(32, int(os.getenv("LEGAL_IMPORT_EMBED_BATCH_SIZE", "12"))))
                except ValueError:
                    import_batch_size = 12
                embeddings = self.encode_passages(passage_texts, batch_size=import_batch_size)
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
                        "domain_slug": _import_domain_slug(
                            request.domain_slug,
                            request.field_id,
                            field_name,
                        ),
                        "domain_codes": ",".join(
                            dict.fromkeys(
                                str(item).strip()
                                for item in (
                                    request.domain_codes
                                    or ([request.domain_slug] if request.domain_slug else [])
                                )
                                if str(item).strip()
                            )
                        ),
                        "primary_organization_unit_id": str(
                            request.primary_organization_unit_id or ""
                        ),
                        "organization_unit_ids": ",".join(
                            dict.fromkeys(
                                str(item).strip()
                                for item in request.organization_unit_ids
                                if str(item).strip()
                            )
                        ),
                        "organization_assignment_state": str(
                            request.organization_assignment_state or "unassigned"
                        ),
                        "article_number": record["article_number"],
                        "article_title": record["article_title"],
                        "parent_kind": str(record.get("parent_kind") or structure),
                        "child_kind": str(record.get("child_kind") or "fallback"),
                        "clause_number": str(record.get("clause_number") or ""),
                        "point_number": str(record.get("point_number") or ""),
                        "structural_heading": str(record.get("heading") or ""),
                        "source_url": request.source_url,
                        "effective_date": request.effective_date.isoformat(),
                        "doc_status": "staging",
                        "status": "staging",
                        "structure": structure,
                    }
                    for record in chunk_records
                ]
                self._load()
                if incremental_import:
                    if self._incremental_collection is None:
                        # Loading the optional overlay is best-effort for
                        # read-only serving, but an approved import must be
                        # able to create it lazily after the baseline has
                        # loaded. This keeps a transient startup race from
                        # becoming a misleading "collection unavailable".
                        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
                        require_staging_collection_target(
                            CHROMA_INCREMENTAL_COLLECTION, CHROMA_PATH
                        )
                        self._incremental_collection = _configure_incremental_collection(
                            client.get_or_create_collection(
                                CHROMA_INCREMENTAL_COLLECTION,
                                metadata=CHROMA_INCREMENTAL_METADATA,
                            )
                        )
                    target_collections = [self._incremental_collection]
                else:
                    target_collections = [
                        self._collection,
                        chromadb.PersistentClient(path=str(CHROMA_PATH)).get_collection(
                            CHROMA_SOURCE_COLLECTION
                        ),
                    ]
                target_collections = [
                    collection
                    for index, collection in enumerate(target_collections)
                    if collection is not None
                    and collection not in target_collections[:index]
                ]
                if not target_collections:
                    raise RuntimeError("incremental_collection_unavailable")
                with self._query_lock:
                    for target_collection in target_collections:
                        target_collection.upsert(
                            ids=vector_ids,
                            embeddings=embeddings,
                            metadatas=metadatas,
                        )

                    # A staged vector must never remain labelled as staged
                    # after the guarded import succeeds.  SQL remains the
                    # authority for activation, but aligned vector metadata is
                    # required for deterministic domain/status filtering and
                    # management audits.
                    active_metadatas = [
                        {**metadata, "doc_status": "active", "status": "active"}
                        for metadata in metadatas
                    ]
                    for target_collection in target_collections:
                        target_collection.update(
                            ids=vector_ids, metadatas=active_metadatas
                        )
                    if incremental_import:
                        # Publish from the writer process before SQL activation
                        # so Retrieval V2 never depends on a stale cross-process
                        # Chroma HNSW view for the newly approved document.
                        _publish_incremental_overlay_snapshot(
                            self._incremental_collection
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
                    assignment_state = str(
                        request.organization_assignment_state
                        or (
                            "assigned"
                            if request.primary_organization_unit_id
                            else "unassigned"
                        )
                    )
                    assignment_units = list(
                        dict.fromkeys(
                            str(item).strip()
                            for item in [
                                request.primary_organization_unit_id,
                                *request.organization_unit_ids,
                            ]
                            if str(item or "").strip()
                        )
                    )
                    connection.execute(
                        text(
                            """
                            INSERT INTO legal_document_organization_assignment (
                                document_id, assignment_state,
                                primary_organization_unit_id, organization_unit_ids,
                                assignment_source, confirmation_status, updated_at
                            ) VALUES (
                                :document_id, :assignment_state,
                                :primary_organization_unit_id,
                                CAST(:organization_unit_ids AS JSONB),
                                'import', 'confirmed', now()
                            )
                            ON CONFLICT (document_id) DO UPDATE SET
                                assignment_state = EXCLUDED.assignment_state,
                                primary_organization_unit_id = EXCLUDED.primary_organization_unit_id,
                                organization_unit_ids = EXCLUDED.organization_unit_ids,
                                assignment_source = EXCLUDED.assignment_source,
                                confirmation_status = EXCLUDED.confirmation_status,
                                updated_at = now()
                            """
                        ),
                        {
                            "document_id": document_id,
                            "assignment_state": assignment_state,
                            "primary_organization_unit_id": request.primary_organization_unit_id,
                            "organization_unit_ids": json.dumps(assignment_units),
                        },
                    )
                activated_state = DocumentServingStateStore(self._engine).read(
                    document_id
                )
                activated_projection = serving_projection(
                    activated_state,
                    temporal_scope="current",
                    as_of=vietnam_legal_date(),
                )
                activation_committed = (
                    str(activated_state.get("status") or "").casefold()
                    == "active"
                    and bool(activated_state.get("search_included"))
                    and bool(vector_ids)
                    and bool(target_collections)
                )
                if not activation_committed:
                    raise RuntimeError("import_activation_postcondition_failed")
                activation_receipt = {
                    "status": "passed",
                    "passed": True,
                    "document_status": activated_state.get("status"),
                    "search_included": bool(
                        activated_state.get("search_included")
                    ),
                    "serving_state": activated_projection["serving_state"],
                    "current_answer_eligible": bool(
                        activated_projection["allowed"]
                    ),
                    "serving_reason_code": activated_projection.get(
                        "reason_code"
                    ),
                    "state_revision": state_revision(activated_state),
                    "vector_count": len(vector_ids),
                    "vector_collections": [
                        str(getattr(collection, "name", "") or "configured")
                        for collection in target_collections
                    ],
                }
                _invalidate_document_cache(str(document_id))
                if request.replacement_of_document_id is None:
                    self._refresh_management_inventory_cache()
                else:
                    # The replacement saga still has to retire the old row.
                    # Rebuild once after that atomic transition, not twice.
                    self._invalidate_management_inventory_cache()
            except Exception:
                if vector_ids:
                    try:
                        with self._query_lock:
                            for target_collection in locals().get(
                                "target_collections", []
                            ):
                                target_collection.delete(ids=vector_ids)
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
            "vector_collection": (
                CHROMA_INCREMENTAL_COLLECTION
                if incremental_import
                else CHROMA_COLLECTION
            ),
            "activation_receipt": activation_receipt,
        }

    def replace_document(
        self,
        old_doc_id: str,
        request: LegalImportRequest,
        *,
        requested_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Shared replacement saga: index, verify, then switch the old state."""

        try:
            old_document_id = int(str(old_doc_id).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_document_id") from exc
        if old_document_id <= 0:
            raise ValueError("invalid_document_id")
        if request.replacement_of_document_id not in {None, old_document_id}:
            raise ValueError("replacement_document_mismatch")

        states = DocumentServingStateStore(self._engine)
        old = states.read(old_document_id)
        old_revision = state_revision(old)

        reviewed_request = request.model_copy(
            update={"replacement_of_document_id": old_document_id}
        )
        imported = self.import_document(reviewed_request)
        new_document_id = int(imported.get("document_id") or 0)
        stage = "import_activation"
        smoke: dict[str, Any] = {}
        old_result: dict[str, Any] | None = None
        try:
            if (
                new_document_id <= 0 or new_document_id == old_document_id
                or imported.get("activation_status") != "active"
                or int(imported.get("chunk_count") or 0) < 1
                or not imported.get("vector_collection")
                or not (imported.get("activation_receipt") or {}).get("passed")
            ):
                raise RuntimeError("replacement_import_not_active")
            # Before switching the old version, prove that the new physical
            # row and every expected vector are visible to the live retrieval
            # process. Public current-search deliberately collapses duplicate
            # law numbers, so it cannot be used as an identity proof while the
            # old and new versions are both active. The full public exact and
            # semantic smoke test runs immediately after the atomic switch.
            stage = "retrieval_precheck"
            vector_probe = self._document_vector_membership(str(new_document_id))
            smoke = {
                "status": "passed" if vector_probe.get("current_retrieval_ready") else "failed",
                "passed": bool(vector_probe.get("current_retrieval_ready")),
                "document_id": str(new_document_id),
                "check": "live_vector_membership_before_switch",
                "vector_membership": vector_probe,
            }
            if not vector_probe.get("current_retrieval_ready"):
                raise RuntimeError("replacement_retrieval_precheck_failed")
            stage = "old_document_transition"
            old_result = self.set_document_search_state(
                str(old_document_id), action=reviewed_request.replacement_action,
                requested_by=requested_by, reason=reason,
                expected_revision=old_revision,
                required_active_document_id=new_document_id,
            )
            # Once the old row is historical, the public current-search path
            # must resolve both exact and semantic probes to the new version.
            # This is the end-to-end proof that Q&A can actually retrieve it.
            stage = "post_switch_retrieval_smoke"
            public_smoke = self._verify_replacement_retrieval(
                new_document_id, reviewed_request
            )
            required_checks_passed = bool(
                public_smoke.get("exact_match")
                and str(public_smoke.get("document_id") or "")
                == str(new_document_id)
                and vector_probe.get("current_retrieval_ready")
            )
            smoke = {
                **public_smoke,
                "status": (
                    "passed"
                    if required_checks_passed and public_smoke.get("semantic_match")
                    else "passed_with_semantic_warning"
                    if required_checks_passed
                    else "failed"
                ),
                "passed": required_checks_passed,
                "required_checks_passed": required_checks_passed,
                "semantic_probe_passed": bool(public_smoke.get("semantic_match")),
                "vector_membership": vector_probe,
            }
            if not required_checks_passed:
                raise RuntimeError("replacement_post_switch_retrieval_smoke_failed")
        except Exception as exc:
            compensation: dict[str, Any] = {"status": "not_required"}
            if old_result is not None:
                try:
                    rollback_action = rollback_action_for_transition(old_result)
                    compensation["old_restore"] = self.set_document_search_state(
                        str(old_document_id),
                        action=rollback_action,
                        requested_by=requested_by,
                        reason=(
                            "Khôi phục đúng trạng thái trước đó vì bản thay thế không vượt qua "
                            "kiểm tra retrieval sau chuyển đổi."
                        ),
                        expected_revision=str(old_result.get("state_revision") or ""),
                    )
                except Exception as restore_error:
                    compensation["old_restore"] = {
                        "status": "failed",
                        "error_class": type(restore_error).__name__,
                    }
            if new_document_id > 0 and new_document_id != old_document_id:
                try:
                    new_exclusion = self.set_document_search_state(
                        str(new_document_id), action="exclude", requested_by=requested_by,
                        reason="Loại bản mới vì kiểm tra hoặc chuyển trạng thái bản cũ thất bại.",
                    )
                    compensation = {**compensation, **new_exclusion}
                except Exception as rollback_error:
                    compensation = {
                        **compensation,
                        "status": "failed",
                        "error_class": type(rollback_error).__name__,
                    }
            raise ReplacementActivationError(
                stage=stage,
                document_id=new_document_id,
                compensation=compensation,
                verification=smoke,
            ) from exc
        _invalidate_document_cache(str(old_document_id))
        return {
            **imported,
            "status": "replaced",
            "old_document": {
                "document_id": str(old_document_id),
                "law_number": str(old.get("law_number") or ""),
                "title": str(old.get("title") or ""),
                "previous_status": str(old.get("status") or ""),
                "stored_status": old_result["stored_status"],
                "search_included": False,
                "serving_action": old_result["serving_action"],
                "state_revision": old_result["state_revision"],
                "verification": old_result.get("verification"),
            },
            "new_document": {"document_id": str(new_document_id), "search_included": True},
            "retrieval_smoke": smoke,
            "chatbot_ready": True,
            "requested_by": requested_by,
            "reason": reason,
            "vectors_preserved_for_audit": True,
        }

    def _verify_replacement_retrieval(
        self, document_id: int, request: LegalImportRequest,
    ) -> dict[str, Any]:
        # This synchronous service method runs in a FastAPI worker thread.
        # Probe the same V2 endpoint as Q&A before touching the old record.
        return asyncio.run(verify_post_activation_retrieval(
            document_id=document_id, law_number=request.law_number,
            title=request.title, content=request.content, domain=request.domain_slug,
            base_url=os.getenv("LEGAL_RETRIEVAL_V2_URL") or os.getenv("LEGAL_SEARCH_URL") or "http://127.0.0.1:8766",
        ))

    def set_document_search_state(
        self,
        doc_id: str,
        *,
        action: str,
        requested_by: str,
        reason: str,
        expected_revision: str | None = None,
        required_active_document_id: int | None = None,
        verify_vectors: bool = False,
    ) -> dict[str, Any]:
        """Apply the shared document/scope transaction, preserving all vectors."""

        normalized_action = str(action or "").strip().casefold()
        vector_projection: dict[str, Any] | None = None
        if normalized_action in {"restore", "historical"}:
            vector_projection = self._document_vector_membership(doc_id)
            readiness_key = (
                "current_retrieval_ready"
                if normalized_action == "restore"
                else "historical_retrieval_ready"
            )
            if not vector_projection.get(readiness_key):
                if normalized_action == "historical":
                    raise ValueError("document_vectors_not_ready_for_history")
                raise ValueError("document_vectors_not_ready_for_restore")
        with self._write_lock:
            result = DocumentServingStateStore(self._engine).transition(
                doc_id, action=action, requested_by=requested_by, reason=reason,
                expected_revision=expected_revision,
                required_active_document_id=required_active_document_id,
            )
        vector_required = normalized_action in {"restore", "historical"}
        if verify_vectors and vector_required and vector_projection is None:
            vector_projection = self._document_vector_membership(doc_id)
        readiness_key = (
            "current_retrieval_ready"
            if normalized_action == "restore"
            else "historical_retrieval_ready"
        )
        vector_ready = bool(
            vector_projection and vector_projection.get(readiness_key)
        )
        state_verified = bool((result.get("verification") or {}).get("passed"))
        operation_verified = state_verified and (
            vector_ready if vector_required else True
        )
        result["operation_verification"] = {
            "status": "passed" if operation_verified else "degraded",
            "passed": operation_verified,
            "state_authority_verified": state_verified,
            "vector_verification_required": vector_required,
            "vector_ready": vector_ready if vector_required else None,
            "vectors": vector_projection,
        }
        result["chatbot_ready"] = operation_verified
        result["message"] = {
            "exclude": "Đã loại văn bản khỏi tìm kiếm hiện hành và lịch sử; dữ liệu gốc vẫn được giữ.",
            "quarantine": "Đã cách ly văn bản khỏi mọi phạm vi tìm kiếm; dữ liệu gốc vẫn được giữ.",
            "historical": (
                "Đã chuyển văn bản sang tra cứu lịch sử."
                if operation_verified
                else "Đã chặn văn bản khỏi tra cứu hiện hành; kho vector lịch sử cần được kiểm tra lại."
            ),
            "restore": "Đã kiểm tra chunk/vector và đưa văn bản trở lại tìm kiếm hiện hành.",
        }.get(normalized_action)
        _invalidate_document_cache(result["document_id"])
        self._refresh_management_inventory_cache()
        return result

    def _document_vector_membership_for_chunks(
        self, doc_id: str, chunk_ids: Sequence[int]
    ) -> dict[str, Any]:
        """Read vector truth from the process that actually serves Q&A.

        The management-only process starts without loading Chroma.  Validate
        the live V2 response against the exact SQL chunk fingerprint before it
        can authorize restore/history.  A failed or mismatched remote check is
        fail-closed even if a stale local handle happens to be available.
        """

        # Local serving processes load their collections lazily. A restore may
        # be the first operation after restart, before any search warms them.
        # Management-only processes must keep using the remote verification.
        if not MANAGEMENT_ONLY and getattr(self, "_collection", None) is None:
            self._load()

        collections: dict[str, Any] = {
            "fast": getattr(self, "_collection", None),
            "expanded": getattr(self, "_source_collection", None),
            "temporal": getattr(self, "_temporal_collection", None),
        }
        if CHROMA_INCREMENTAL_COLLECTION:
            collections["incremental"] = getattr(
                self, "_incremental_collection", None
            )
        local_projection = _vector_membership_projection(collections, chunk_ids)
        if not bool(globals().get("MANAGEMENT_ONLY", False)):
            return {**local_projection, "verification_source": "local_process"}

        base_url = str(
            os.getenv("LEGAL_RETRIEVAL_V2_URL")
            or os.getenv("LEGAL_SEARCH_URL")
            or ""
        ).strip().rstrip("/")
        if not base_url:
            return {
                **local_projection,
                "status": "degraded",
                "reason_code": "live_vector_verification_unavailable",
                "message": "Chưa cấu hình endpoint Retrieval V2 để đối chiếu vector.",
                "retrieval_ready": False,
                "current_retrieval_ready": False,
                "historical_retrieval_ready": False,
                "verification_source": "management_local_fallback",
            }
        try:
            canonical_id = str(int(str(doc_id).strip()))
            response = httpx.get(
                f"{base_url}/management/documents/{canonical_id}/vector-membership",
                timeout=5.0,
            )
            response.raise_for_status()
            remote = response.json()
            if not isinstance(remote, dict):
                raise ValueError("invalid_live_vector_projection")
            if str(remote.get("document_id") or "") != canonical_id:
                raise ValueError("live_vector_document_mismatch")
            if int(remote.get("expected") or -1) != int(local_projection["expected"]):
                raise ValueError("live_vector_count_mismatch")
            if str(remote.get("chunk_ids_sha256") or "") != str(
                local_projection["chunk_ids_sha256"]
            ):
                raise ValueError("live_vector_chunk_fingerprint_mismatch")
            return {**remote, "verification_source": "retrieval_v2_live"}
        except Exception as exc:
            return {
                **local_projection,
                "status": "degraded",
                "reason_code": "live_vector_verification_unavailable",
                "message": "Không thể xác minh vector trên Retrieval V2 đang phục vụ chatbot.",
                "retrieval_ready": False,
                "current_retrieval_ready": False,
                "historical_retrieval_ready": False,
                "verification_source": "management_local_fallback",
                "verification_error_class": type(exc).__name__,
            }

    def _document_vector_membership(self, doc_id: str) -> dict[str, Any]:
        inventory = self._vector_cleanup_inventory(doc_id)
        return self._document_vector_membership_for_chunks(
            str(inventory["document_id"]), inventory["chunk_ids"]
        )

    def fields(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            return [
                _repair_display_row(dict(row))
                for row in connection.execute(
                    text("SELECT id, name, description FROM legal_fields ORDER BY id")
                ).mappings()
            ]

    def _document_browse_context(
        self,
        *,
        query: str,
        domain: str | None,
        tier: str,
        as_of: date,
        audience: str,
        issued_from: date | None = None,
        issued_to: date | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        expired_from: date | None = None,
        expired_to: date | None = None,
    ) -> tuple[str, dict[str, Any], str, set[int] | None]:
        """Build the single manifest-bound filter used by list and export."""

        for date_from, date_to, label in (
            (issued_from, issued_to, "issued"),
            (effective_from, effective_to, "effective"),
            (expired_from, expired_to, "expired"),
        ):
            if date_from is not None and date_to is not None and date_from > date_to:
                raise ValueError(f"invalid_{label}_date_range")

        cleaned_query = str(query or "").strip()
        cleaned_domain = str(domain or "").strip()
        normalized_tier = tier if tier in {"all", "core", "expanded"} else "all"
        filters = [
            "d.status = 'active'",
            "(d.effective_date IS NULL OR d.effective_date <= :as_of)",
            "(d.expired_date IS NULL OR d.expired_date > :as_of)",
        ]
        params: dict[str, Any] = {"as_of": as_of}

        self._set_request_audience(audience)
        self._require_serving_scope()
        serving_ids = self._request_serving_document_ids()
        if serving_ids is not None:
            if serving_ids:
                filters.append("d.id = ANY(:serving_document_ids)")
                params["serving_document_ids"] = sorted(serving_ids)
            else:
                filters.append("FALSE")
                serving_ids = None

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
        if normalized_tier == "core":
            filters.append(
                "EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE)"
            )
        elif normalized_tier == "expanded":
            filters.append(
                "NOT EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE)"
            )

        for value, key, column, operator in (
            (issued_from, "issued_from", "d.issued_date", ">="),
            (issued_to, "issued_to", "d.issued_date", "<="),
            (effective_from, "effective_from", "d.effective_date", ">="),
            (effective_to, "effective_to", "d.effective_date", "<="),
            (expired_from, "expired_from", "d.expired_date", ">="),
            (expired_to, "expired_to", "d.expired_date", "<="),
        ):
            if value is not None:
                filters.append(f"{column} {operator} :{key}")
                params[key] = value

        return " AND ".join(filters), params, normalized_tier, serving_ids

    def _document_browse_result(
        self,
        *,
        query: str = "",
        domain: str | None = None,
        tier: str = "all",
        as_of: date,
        limit: int,
        offset: int,
        sort_by: str,
        sort_order: str,
        audience: str,
        issued_from: date | None = None,
        issued_to: date | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        expired_from: date | None = None,
        expired_to: date | None = None,
        max_total: int | None = None,
    ) -> dict[str, Any]:
        where_sql, params, normalized_tier, serving_ids = self._document_browse_context(
            query=query,
            domain=domain,
            tier=tier,
            as_of=as_of,
            audience=audience,
            issued_from=issued_from,
            issued_to=issued_to,
            effective_from=effective_from,
            effective_to=effective_to,
            expired_from=expired_from,
            expired_to=expired_to,
        )
        sort_columns = {
            "effective_date": "d.effective_date",
            "issued_date": "d.issued_date",
            "title": "d.title",
            "law_number": "d.law_number",
        }
        sort_column = sort_columns.get(sort_by, sort_columns["effective_date"])
        sort_direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
        params.update({"limit": max(1, int(limit)), "offset": max(0, int(offset))})

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
        if serving_ids is not None:
            count_statement = count_statement.bindparams(
                bindparam("serving_document_ids", type_=ARRAY(Integer))
            )
            list_statement = list_statement.bindparams(
                bindparam("serving_document_ids", type_=ARRAY(Integer))
            )
        with self._engine.connect() as connection:
            total = int(connection.execute(count_statement, params).scalar_one())
            if max_total is not None and total > max_total:
                raise OverflowError(f"document_export_exceeds_{max_total}")
            items = [
                _repair_display_row(dict(row))
                for row in connection.execute(list_statement, params).mappings()
            ]
        return {
            "items": items,
            "total": total,
            "limit": params["limit"],
            "offset": params["offset"],
            "as_of": as_of.isoformat(),
            "tier": normalized_tier,
        }

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
        audience: str = "citizen",
        issued_from: date | None = None,
        issued_to: date | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        expired_from: date | None = None,
        expired_to: date | None = None,
    ) -> dict[str, Any]:
        """List effective documents available to the legal retrieval service."""

        return self._document_browse_result(
            query=query,
            domain=domain,
            tier=tier,
            as_of=as_of,
            limit=max(1, min(int(limit), 100)),
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            audience=audience,
            issued_from=issued_from,
            issued_to=issued_to,
            effective_from=effective_from,
            effective_to=effective_to,
            expired_from=expired_from,
            expired_to=expired_to,
        )

    def export_documents(
        self,
        *,
        query: str = "",
        domain: str | None = None,
        tier: str = "all",
        as_of: date,
        sort_by: str = "effective_date",
        sort_order: str = "desc",
        audience: str = "citizen",
        issued_from: date | None = None,
        issued_to: date | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        expired_from: date | None = None,
        expired_to: date | None = None,
    ) -> dict[str, Any]:
        return self._document_browse_result(
            query=query,
            domain=domain,
            tier=tier,
            as_of=as_of,
            limit=DOCUMENT_EXPORT_MAX_ROWS,
            offset=0,
            sort_by=sort_by,
            sort_order=sort_order,
            audience=audience,
            issued_from=issued_from,
            issued_to=issued_to,
            effective_from=effective_from,
            effective_to=effective_to,
            expired_from=expired_from,
            expired_to=expired_to,
            max_total=DOCUMENT_EXPORT_MAX_ROWS,
        )

    def _management_serving_inventory(self, serving: Any, as_of: date) -> dict[str, Any]:
        """One live inventory for cards, filters and detail projections."""
        release_id = str(getattr(serving, "release_id", "") or "")
        manifest_sha = str(getattr(serving, "manifest_sha256", "") or "")
        cache_key = (
            (release_id, manifest_sha, as_of.isoformat())
            if release_id and manifest_sha
            else None
        )

        def build_inventory() -> dict[str, Any]:
            overlay_ids = self._local_overlay_document_ids(include_inactive=True)
            inventory_ids = (
                set(serving.retrievable_document_ids)
                | set(serving.future_document_ids)
                | overlay_ids
            )
            rows = DocumentServingStateStore(self._engine).read_many(
                sorted(inventory_ids)
            )
            projections = {
                int(identity): inventory_projection(
                    row,
                    release_state=(
                        "current_retrievable"
                        if int(identity) in overlay_ids
                        else serving.state_for(int(identity))
                    ),
                    as_of=as_of,
                )
                for identity, row in rows.items()
            }
            groups: dict[str, list[int]] = {}
            for identity, projection in projections.items():
                groups.setdefault(projection["serving_state"], []).append(identity)
            return {
                "ids": sorted(projections),
                "validity_rows": rows,
                "overlay_ids": overlay_ids,
                "projections": projections,
                "groups": groups,
            }

        if cache_key is None:
            return build_inventory()
        with self._management_inventory_lock:
            cached = self._management_inventory_cache.get(cache_key)
            if cached:
                return copy.deepcopy(cached[1])
            inventory = build_inventory()
            self._management_inventory_cache = {
                cache_key: (perf_counter(), copy.deepcopy(inventory))
            }
            return inventory

    def _invalidate_management_inventory_cache(self) -> None:
        # Some isolated unit fixtures construct the retriever with
        # ``object.__new__`` to avoid loading the embedding stack. They do not
        # own a management cache, so invalidation is intentionally a no-op.
        lock = getattr(self, "_management_inventory_lock", None)
        cache = getattr(self, "_management_inventory_cache", None)
        if lock is None or cache is None:
            return
        with lock:
            cache.clear()

    def _refresh_management_inventory_cache(self) -> None:
        """Refresh the projection after an app-owned corpus mutation.

        The cache key already includes the active release fingerprint and the
        legal date. Supported imports and lifecycle writes all pass through
        this process and call this method. A process restart prewarms the same
        projection through the launcher, so Admin reads never depend on a
        stale time-based cache while avoiding a periodic cold miss.
        """

        if getattr(self, "_management_inventory_lock", None) is None:
            return
        self._invalidate_management_inventory_cache()
        try:
            self._management_serving_inventory(
                load_active_serving_release(), vietnam_legal_date()
            )
        except Exception as exc:
            # The mutation already committed and its response must remain
            # truthful. A failed prewarm only affects the next Admin read.
            logger.warning(
                "Management inventory prewarm failed after mutation: %s",
                type(exc).__name__,
            )

    def management_summary(self, *, as_of: date) -> dict[str, Any]:
        """Return a manifest-bound, availability-aware repository summary."""

        serving = load_active_serving_release()
        inventory = self._management_serving_inventory(serving, as_of)
        retrievable_ids = inventory["ids"]
        current_ids = inventory["groups"].get("current_retrievable", [])
        historical_ids = inventory["groups"].get("historical_only", [])
        if not inventory["ids"]:
            raise RuntimeError("active_retrieval_v2_empty_document_scope")

        statement = text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE d.status = 'active') AS active,
                COUNT(*) FILTER (WHERE COALESCE(d.status, '') <> 'active') AS inactive,
                COUNT(*) FILTER (
                    WHERE d.status = 'active'
                      AND d.effective_date IS NOT NULL
                      AND d.effective_date > :as_of
                ) AS not_yet_effective,
                COUNT(*) FILTER (
                    WHERE d.status = 'active'
                      AND d.expired_date IS NOT NULL
                      AND d.expired_date <= :as_of
                ) AS expired_by_date,
                COUNT(*) FILTER (WHERE COALESCE(trim(d.source_url), '') = '') AS missing_source,
                COUNT(*) FILTER (
                    WHERE COALESCE(trim(d.title), '') = ''
                       OR COALESCE(trim(d.law_number), '') = ''
                       OR COALESCE(trim(d.issuing_agency), '') = ''
                       OR COALESCE(trim(d.source_url), '') = ''
                ) AS missing_metadata,
                (SELECT COUNT(*) FROM legal_articles a WHERE a.document_id = ANY(:serving_document_ids)) AS articles,
                (SELECT COUNT(*) FROM legal_article_chunks c JOIN legal_articles a ON a.id = c.article_id WHERE a.document_id = ANY(:serving_document_ids)) AS chunks,
                (
                    SELECT COUNT(DISTINCT s.document_id)
                    FROM legal_search_scope s
                    WHERE s.included = TRUE
                ) AS core_documents
            FROM legal_documents d
            WHERE d.id = ANY(:serving_document_ids)
            """
        ).bindparams(bindparam("serving_document_ids", type_=ARRAY(Integer)))
        params = {
            "as_of": as_of,
            "serving_document_ids": retrievable_ids,
        }
        with self._engine.connect() as connection:
            row = connection.execute(
                statement, params
            ).mappings().first()
            try:
                # Domain coverage is a corpus-metadata measure, not a vector
                # release measure.  Join to the legal corpus directly so a
                # stale serving manifest ID cannot make an otherwise valid
                # document appear "unclassified" on the admin dashboard.
                domain_rows = connection.execute(
                    text(
                        """
                        SELECT s.domain, COUNT(DISTINCT s.document_id) AS count
                        FROM legal_search_scope s
                        JOIN legal_documents d ON d.id = s.document_id
                        GROUP BY s.domain
                        """
                    )
                ).mappings().all()
                domain_total = int(
                    connection.execute(text("SELECT COUNT(*) FROM legal_documents")).scalar_one()
                    or 0
                )
            except Exception:
                domain_rows = []
                domain_total = total
            organization_rows: list[dict[str, Any]] = []
            organization_projection_available = True
            try:
                organization_rows = list(connection.execute(
                    text("""
                        SELECT assignment_state,primary_organization_unit_id,
                               confirmation_status,COUNT(*) AS count
                        FROM legal_documents d
                        LEFT JOIN legal_document_organization_assignment oa
                          ON oa.document_id = d.id
                        WHERE d.id = ANY(:serving_document_ids)
                        GROUP BY assignment_state,primary_organization_unit_id,
                                 confirmation_status
                    """).bindparams(
                        bindparam("serving_document_ids", type_=ARRAY(Integer))
                    ),
                    {"serving_document_ids": retrievable_ids or [-1]},
                ).mappings().all())
            except Exception:
                organization_projection_available = False
        if row is None:
            raise RuntimeError("management_summary_unavailable")
        values = dict(row)
        total = len(retrievable_ids)
        chunks = int(values.get("chunks") or 0)
        core = min(int(values.get("core_documents") or 0), total)
        state_counts = {key: len(values) for key, values in inventory["groups"].items()}
        by_primary_domain: dict[str, int] = {}
        for domain_row in domain_rows:
            canonical = canonicalize_legal_domain(domain_row.get("domain"))
            if canonical not in CANONICAL_DOMAIN_ALIASES:
                canonical = "unclassified"
            by_primary_domain[canonical] = by_primary_domain.get(canonical, 0) + int(domain_row.get("count") or 0)
        domain_denominator = domain_total or total
        classified_total = min(
            sum(value for key, value in by_primary_domain.items() if key != "unclassified"),
            domain_denominator,
        )
        unclassified = max(domain_denominator - classified_total, 0)
        by_primary_organization_unit: dict[str, int] = {}
        organization_assignment_states = {
            "assigned": 0,
            "shared": 0,
            "unassigned": 0,
            "pending_sync": 0,
            "needs_confirmation": 0,
        }
        for organization_row in organization_rows:
            count = int(organization_row.get("count") or 0)
            state = str(organization_row.get("assignment_state") or "unassigned")
            if state in organization_assignment_states:
                organization_assignment_states[state] += count
            confirmation_status = str(
                organization_row.get("confirmation_status") or ""
            )
            if confirmation_status == "projection_pending":
                organization_assignment_states["pending_sync"] += count
            elif confirmation_status == "needs_confirmation":
                organization_assignment_states["needs_confirmation"] += count
            unit_id = str(
                organization_row.get("primary_organization_unit_id") or ""
            ).strip()
            if state == "assigned" and unit_id:
                by_primary_organization_unit[unit_id] = (
                    by_primary_organization_unit.get(unit_id, 0) + count
                )
        vector_projection: dict[str, Any] = {
            "status": "available",
            "reason_code": None,
            "message": None,
            "database_chunks": chunks,
            "collections": {
                "current_retrievable": int((serving.manifest.payload.get("current_collection") or {}).get("count") or 0),
                "temporal_retrievable": int((serving.manifest.payload.get("temporal_collection") or {}).get("count") or 0),
            },
        }
        try:
            base_url = str(os.getenv("LEGAL_RETRIEVAL_V2_URL") or os.getenv("LEGAL_SEARCH_URL") or "").rstrip("/")
            if MANAGEMENT_ONLY:
                # The first exact Chroma membership scan can take several
                # seconds. CoverageSnapshots makes concurrent callers return
                # immediately, while this budget lets the one real probe finish.
                probe_response = httpx.get(f"{base_url}/management/vector-coverage", timeout=8.0)
                probe_response.raise_for_status()
                probe = probe_response.json()
            else:
                probe = serving_vector_coverage()
            if probe.get("manifest_sha256") != serving.manifest_sha256:
                raise ValueError("coverage_release_mismatch")
            vector_projection.update(probe)
        except Exception:
            vector_projection.update(coverage_percent=None, status="unavailable", reason_code="live_coverage_unavailable")
        validity_cards = {
            **_management_validity_cards(
                rows=inventory["validity_rows"],
                retrievable_document_ids=[*current_ids, *historical_ids],
                snapshot=default_snapshot_cache.load(),
                as_of=as_of,
            ),
            "inventory_total": total,
            "excluded_total": state_counts.get("excluded", 0),
        }
        return {
            "observed_at": datetime.now().astimezone().isoformat(),
            "as_of": as_of.isoformat(),
            "documents": {
                "total": total,
                "active": int(state_counts.get("current_retrievable") or 0),
                "inactive": int(values.get("inactive") or 0),
                "not_yet_effective": int(values.get("not_yet_effective") or 0),
                "expired_by_date": int(values.get("expired_by_date") or 0),
                "missing_source": int(values.get("missing_source") or 0),
                "missing_metadata": int(values.get("missing_metadata") or 0),
                "by_primary_domain": dict(sorted(by_primary_domain.items())),
                "classified_total": classified_total,
                "unclassified": unclassified,
                "classification_coverage_percent": round((classified_total / domain_denominator) * 100, 1) if domain_denominator else 0.0,
                "domain_denominator": domain_denominator,
                "by_primary_organization_unit": dict(
                    sorted(by_primary_organization_unit.items())
                ),
                "organization_assignment_states": organization_assignment_states,
                "organization_projection_available": organization_projection_available,
            },
            "structure": {
                "articles": int(values.get("articles") or 0),
                "chunks": chunks,
            },
            "tiers": {"core": core, "expanded": max(total - core, 0)},
            "vectors": vector_projection,
            "serving_release": {
                "release_id": serving.release_id,
                "legal_as_of": serving.legal_as_of,
                "manifest_sha256": serving.manifest_sha256,
                "current_collection": serving.manifest.current_collection,
                "temporal_collection": serving.manifest.temporal_collection,
                "exact_lexical_index": str((serving.manifest.payload.get("exact_lexical_index") or {}).get("path") or ""),
                "state_counts": state_counts,
                "release_state_counts": dict(serving.manifest.payload.get("document_state_counts") or {}),
                "mutable_state_authority": "legal_documents+legal_search_scope",
                "overlay_document_count": len(inventory["overlay_ids"]),
                "cards": validity_cards,
            },
        }

    def management_documents(
        self,
        *,
        query: str = "",
        document_type: str | None = None,
        issuing_agency: str | None = None,
        scope: str | None = None,
        domain: str | None = None,
        stored_status: str | None = None,
        validity_status: str | None = None,
        tier: str = "all",
        data_quality: str | None = None,
        source_presence: str = "all",
        issued_from: date | None = None,
        issued_to: date | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        expired_from: date | None = None,
        expired_to: date | None = None,
        include_expired_history: bool = False,
        as_of: date,
        limit: int = 30,
        offset: int = 0,
        sort_by: str = "effective_date",
        sort_order: str = "desc",
        include_document_ids: list[int] | None = None,
        exclude_document_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """List all corpus metadata for Admin without transferring legal text."""

        serving = load_active_serving_release()
        inventory = self._management_serving_inventory(serving, as_of)
        overlay_document_ids = inventory["overlay_ids"]

        filters = ["TRUE"]
        scoped_document_ids = inventory["ids"]
        if validity_status:
            # Filter the same observation-backed status shown by the API,
            # before SQL count/pagination. Inventory membership is not proof
            # of legal effectivity. Reload the snapshot outside the inventory
            # cache so a new observation immediately changes the filter.
            snapshot = default_snapshot_cache.load()
            scoped_document_ids = [
                int(identity)
                for identity, row in inventory["validity_rows"].items()
                if _matches_management_validity_filter(
                    project_validity_for_row(
                        row, snapshot=snapshot, as_of=as_of, mode="protect"
                    ),
                    validity_status,
                    as_of=as_of,
                )
            ]
        params: dict[str, Any] = {
            "as_of": as_of,
            "limit": max(1, min(int(limit), 100)),
            "offset": max(0, int(offset)),
            "serving_document_ids": scoped_document_ids or [-1],
            "overlay_document_ids": sorted(overlay_document_ids) or [-1],
            "history_cutoff": as_of - timedelta(days=365),
        }
        filters.append("d.id = ANY(:serving_document_ids)")
        if include_document_ids is not None:
            normalized_include_ids = sorted(
                {int(item) for item in include_document_ids if int(item) > 0}
            )
            filters.append("d.id = ANY(:organization_document_ids)")
            params["organization_document_ids"] = normalized_include_ids or [-1]
        elif exclude_document_ids:
            normalized_exclude_ids = sorted(
                {int(item) for item in exclude_document_ids if int(item) > 0}
            )
            if normalized_exclude_ids:
                filters.append("NOT d.id = ANY(:organization_document_ids)")
                params["organization_document_ids"] = normalized_exclude_ids
        if validity_status == "expired" and not include_expired_history:
            filters.append("(d.expired_date IS NULL OR d.expired_date >= :history_cutoff)")
        cleaned_query = str(query or "").strip()
        if cleaned_query:
            filters.append(
                "(lower(trim(COALESCE(d.law_number, ''))) = lower(trim(:exact_query)) "
                "OR d.law_number ILIKE :query_prefix "
                "OR d.title ILIKE :query_prefix OR d.issuing_agency ILIKE :query_prefix "
                "OR d.document_type ILIKE :query_prefix)"
            )
            params["exact_query"] = cleaned_query
            params["query_prefix"] = f"{cleaned_query}%"
        for value, key, column in (
            (document_type, "document_type", "d.document_type"),
            (issuing_agency, "issuing_agency", "d.issuing_agency"),
            (scope, "scope", "d.scope"),
            (stored_status, "stored_status", "d.status"),
        ):
            cleaned = str(value or "").strip()
            if cleaned:
                filters.append(f"lower(trim(COALESCE({column}, ''))) = lower(trim(:{key}))")
                params[key] = cleaned

        cleaned_domain = canonicalize_legal_domain(domain) if str(domain or "").strip() else ""
        if cleaned_domain:
            filters.append(
                "(EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id "
                "AND s.included = TRUE AND s.domain = :domain) OR "
                "EXISTS (SELECT 1 FROM legal_commune_field_groups g WHERE g.field_id = d.field_id "
                "AND g.included = TRUE AND g.group_slug = :domain) OR "
                "EXISTS (SELECT 1 FROM legal_fields f2 WHERE f2.id = d.field_id "
                "AND lower(f2.name) = lower(:domain)))"
            )
            params["domain"] = cleaned_domain

        if tier == "core":
            filters.append(
                "(EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE) "
                "OR d.id = ANY(:overlay_document_ids))"
            )
        elif tier == "expanded":
            filters.append(
                "NOT EXISTS (SELECT 1 FROM legal_search_scope s WHERE s.document_id = d.id AND s.included = TRUE) "
                "AND NOT d.id = ANY(:overlay_document_ids)"
            )

        if source_presence == "present":
            filters.append("COALESCE(trim(d.source_url), '') <> ''")
        elif source_presence == "missing":
            filters.append("COALESCE(trim(d.source_url), '') = ''")

        date_filters = (
            (issued_from, "issued_from", "d.issued_date", ">="),
            (issued_to, "issued_to", "d.issued_date", "<="),
            (effective_from, "effective_from", "d.effective_date", ">="),
            (effective_to, "effective_to", "d.effective_date", "<="),
            (expired_from, "expired_from", "d.expired_date", ">="),
            (expired_to, "expired_to", "d.expired_date", "<="),
        )
        for value, key, column, operator in date_filters:
            if value is not None:
                filters.append(f"{column} {operator} :{key}")
                params[key] = value

        if data_quality == "missing_source":
            filters.append("COALESCE(trim(d.source_url), '') = ''")
        elif data_quality == "missing_metadata":
            filters.append(
                "(COALESCE(trim(d.title), '') = '' OR COALESCE(trim(d.law_number), '') = '' "
                "OR COALESCE(trim(d.issuing_agency), '') = '' OR COALESCE(trim(d.source_url), '') = '')"
            )
        elif data_quality == "zero_chunks":
            filters.append(
                "NOT EXISTS (SELECT 1 FROM legal_articles qa JOIN legal_article_chunks qc ON qc.article_id = qa.id WHERE qa.document_id = d.id)"
            )
        elif data_quality == "unknown_status":
            filters.append(
                "(d.status IS NULL OR d.status NOT IN ('active','approved','archived','blocked','draft','indexing','rejected','staging','submitted'))"
            )
        elif data_quality == "unclassified":
            # Classification is a metadata property, independent from whether
            # a historical record is currently eligible for live retrieval.
            # Otherwise a correctly classified historical document was shown
            # as unclassified solely because its search scope is inactive.
            filters.append(
                "NOT EXISTS (SELECT 1 FROM legal_search_scope us WHERE us.document_id = d.id AND COALESCE(trim(us.domain), '') <> '')"
            )

        sort_columns = {
            "effective_date": "d.effective_date",
            "issued_date": "d.issued_date",
            "expired_date": "d.expired_date",
            "title": "d.title",
            "law_number": "d.law_number",
            "stored_status": "d.status",
            "article_count": "(SELECT COUNT(*) FROM legal_articles sa WHERE sa.document_id = d.id)",
            "chunk_count": "(SELECT COUNT(*) FROM legal_articles sa JOIN legal_article_chunks sc ON sc.article_id = sa.id WHERE sa.document_id = d.id)",
        }
        sort_column = sort_columns.get(sort_by, sort_columns["effective_date"])
        sort_direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
        where_sql = " AND ".join(filters)
        count_statement = text(
            f"SELECT COUNT(*) FROM legal_documents d WHERE {where_sql}"
        ).bindparams(bindparam("serving_document_ids", type_=ARRAY(Integer)))
        if ":overlay_document_ids" in where_sql:
            count_statement = count_statement.bindparams(
                bindparam("overlay_document_ids", type_=ARRAY(Integer))
            )
        if ":organization_document_ids" in where_sql:
            count_statement = count_statement.bindparams(
                bindparam("organization_document_ids", type_=ARRAY(Integer))
            )
        list_statement = text(
            f"""
            SELECT
                d.id AS doc_id,
                d.title AS document_title,
                d.law_number,
                d.document_type,
                d.issuing_agency,
                d.scope,
                d.sector,
                d.status AS stored_status,
                d.issued_date,
                d.effective_date,
                d.expired_date,
                d.source_url,
                d.version,
                d.field_id,
                f.name AS field_name,
                COALESCE(scope_row.domain, field_group.group_slug, f.name) AS domain,
                COALESCE(field_group.group_name, f.name) AS domain_name,
                CASE WHEN scope_row.document_id IS NOT NULL OR d.id = ANY(:overlay_document_ids)
                     THEN 'core' ELSE 'expanded' END AS retrieval_tier,
                (SELECT COUNT(*) FROM legal_articles sa WHERE sa.document_id = d.id) AS article_count,
                (
                    SELECT COUNT(*)
                    FROM legal_articles sa
                    JOIN legal_article_chunks sc ON sc.article_id = sa.id
                    WHERE sa.document_id = d.id
                ) AS chunk_count
            FROM legal_documents d
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
            WHERE {where_sql}
            ORDER BY {sort_column} {sort_direction} NULLS LAST, d.id DESC
            LIMIT :limit OFFSET :offset
            """
        ).bindparams(
            bindparam("serving_document_ids", type_=ARRAY(Integer)),
            bindparam("overlay_document_ids", type_=ARRAY(Integer)),
        )
        if ":organization_document_ids" in where_sql:
            list_statement = list_statement.bindparams(
                bindparam("organization_document_ids", type_=ARRAY(Integer))
            )
        with self._engine.connect() as connection:
            total = int(connection.execute(count_statement, params).scalar_one())
            rows = connection.execute(list_statement, params).mappings()
            items = [
                _management_list_item(
                    dict(row),
                    as_of,
                    serving_state=(
                        "current_retrievable"
                        if int(row["doc_id"]) in overlay_document_ids
                        else serving.state_for(row["doc_id"])
                    ),
                    serving_decision=inventory["projections"].get(int(row["doc_id"])),
                )
                for row in rows
            ]
        return {
            "items": items,
            "total": total,
            "limit": params["limit"],
            "offset": params["offset"],
            "as_of": as_of.isoformat(),
            "observed_at": datetime.now().astimezone().isoformat(),
            "serving_release": {
                "release_id": serving.release_id,
                "legal_as_of": serving.legal_as_of,
                "manifest_sha256": serving.manifest_sha256,
            },
        }

    def management_document_detail(self, doc_id: str) -> dict[str, Any]:
        """Return metadata/evidence counts for Admin; never return legal body text."""

        try:
            document_id = int(str(doc_id).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_document_id") from exc
        if document_id <= 0:
            raise ValueError("invalid_document_id")
        serving = load_active_serving_release()
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        d.id AS doc_id,
                        d.title AS document_title,
                        d.law_number,
                        d.document_type,
                        d.issuing_agency,
                        d.scope,
                        d.sector,
                        d.status AS stored_status,
                        d.issued_date,
                        d.effective_date,
                        d.expired_date,
                        d.source_url,
                        d.version,
                        d.created_at,
                        d.field_id,
                        d.source_id,
                        d.collection_source,
                        d.gazette_date,
                        d.signer_title,
                        d.signer_name,
                        d.applicability_info,
                        f.name AS field_name,
                        (SELECT COUNT(*) FROM legal_articles a WHERE a.document_id = d.id) AS article_count,
                        (
                            SELECT COUNT(*)
                            FROM legal_articles a
                            JOIN legal_article_chunks c ON c.article_id = a.id
                            WHERE a.document_id = d.id
                        ) AS chunk_count
                    FROM legal_documents d
                    LEFT JOIN legal_fields f ON f.id = d.field_id
                    WHERE d.id = :document_id
                    LIMIT 1
                    """
                ),
                {"document_id": document_id},
            ).mappings().first()
            if row is None:
                raise LookupError("legal_document_not_found")
            document = _repair_display_row(dict(row))
            for key in (
                "issued_date",
                "effective_date",
                "expired_date",
                "created_at",
                "gazette_date",
            ):
                document[key] = _iso_or_none(document.get(key))
            document["article_count"] = int(document.get("article_count") or 0)
            document["chunk_count"] = int(document.get("chunk_count") or 0)
            document["metadata_revision"] = _management_metadata_revision(document)
            document["metadata_editable"] = document_id in self._local_overlay_document_ids(
                include_inactive=True
            )
            document["as_of_status"] = _management_as_of_status(
                document, vietnam_legal_date()
            )
            serving_row = DocumentServingStateStore(self._engine).read(document_id)
            overlay_ids = self._local_overlay_document_ids(include_inactive=True)
            document.update(inventory_projection(
                serving_row,
                release_state="current_retrievable" if document_id in overlay_ids else serving.state_for(document_id),
                as_of=vietnam_legal_date(),
            ))
            document["quality_flags"] = _management_quality_flags(document)
            articles = [
                {
                    "article_id": item.get("article_id"),
                    "article_number": item.get("article_number"),
                    "article_title": item.get("article_title"),
                    "status": item.get("status"),
                    "effective_from": _iso_or_none(item.get("effective_from")),
                    "effective_to": _iso_or_none(item.get("effective_to")),
                    "chunk_count": int(item.get("chunk_count") or 0),
                }
                for item in connection.execute(
                    text(
                        """
                        SELECT
                            a.id AS article_id,
                            a.article_number,
                            a.title AS article_title,
                            a.status,
                            a.effective_from,
                            a.effective_to,
                            COUNT(c.id) AS chunk_count
                        FROM legal_articles a
                        LEFT JOIN legal_article_chunks c ON c.article_id = a.id
                        WHERE a.document_id = :document_id
                        GROUP BY a.id
                        ORDER BY a.id
                        LIMIT 5000
                        """
                    ),
                    {"document_id": document_id},
                ).mappings()
            ]
            chunk_ids = [
                int(item["chunk_id"])
                for item in connection.execute(
                    text(
                        """
                        SELECT c.id AS chunk_id
                        FROM legal_article_chunks c
                        JOIN legal_articles a ON a.id = c.article_id
                        WHERE a.document_id = :document_id
                        ORDER BY c.id
                        """
                    ),
                    {"document_id": document_id},
                ).mappings()
            ]
            relationship_table = bool(
                connection.execute(
                    text("SELECT to_regclass('public.legal_document_relationships') IS NOT NULL")
                ).scalar_one()
            )
            if relationship_table:
                relationships = {
                    "status": "available",
                    "items": self._fetch_relationships([document_id]).get(document_id, []),
                }
            else:
                relationships = {
                    "status": "unavailable",
                    "reason_code": "relationship_table_missing",
                    "message": "Kho quan hệ văn bản chưa được phê duyệt trong schema hiện tại.",
                    "items": [],
                }
        vector_projection = self._document_vector_membership_for_chunks(
            str(document_id), chunk_ids
        )
        return {
            "observed_at": datetime.now().astimezone().isoformat(),
            "as_of": vietnam_legal_date().isoformat(),
            "serving_release": {
                "release_id": serving.release_id,
                "legal_as_of": serving.legal_as_of,
                "manifest_sha256": serving.manifest_sha256,
            },
            "document": document,
            "structure": {
                "article_count": document["article_count"],
                "chunk_count": document["chunk_count"],
                "articles": articles,
            },
            "relationships": relationships,
            "vectors": vector_projection,
            "faq_impacts": {
                "status": "unavailable",
                "reason_code": "faq_dependency_not_configured",
                "message": "Hệ thống chưa cấu hình bảng liên kết FAQ với văn bản pháp luật; đây không phải lỗi riêng của văn bản này.",
                "items": [],
            },
            "versions": {
                "status": "unavailable",
                "reason_code": "schema_not_approved",
                "message": "Kho chưa có bảng lịch sử phiên bản bất biến. Quan hệ thay thế đã ghi nhận vẫn được hiển thị riêng khi có.",
                "legacy_version": document.get("version"),
                "items": [],
            },
        }

    def _vector_cleanup_inventory(self, doc_id: str) -> dict[str, Any]:
        """Resolve one SQL document and its exact vector IDs without mutation."""

        try:
            document_id = int(str(doc_id).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_document_id") from exc
        if document_id <= 0:
            raise ValueError("invalid_document_id")
        with self._engine.connect() as connection:
            document = connection.execute(
                text(
                    """
                    SELECT id, law_number, title
                    FROM legal_documents
                    WHERE id = :document_id
                    LIMIT 1
                    """
                ),
                {"document_id": document_id},
            ).mappings().first()
            if not document:
                raise LookupError("legal_document_not_found")
            chunk_rows = connection.execute(
                text(
                    """
                    SELECT c.id AS chunk_id
                    FROM legal_article_chunks c
                    JOIN legal_articles a ON a.id = c.article_id
                    WHERE a.document_id = :document_id
                    ORDER BY c.id
                    """
                ),
                {"document_id": document_id},
            ).mappings().all()
            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM legal_articles WHERE document_id=:document_id) AS article_count,
                        (SELECT count(*) FROM legal_search_scope WHERE document_id=:document_id) AS scope_count,
                        (SELECT count(*) FROM legal_document_relationships
                         WHERE source_document_id=:document_id OR target_document_id=:document_id) AS relationship_count,
                        (SELECT count(*) FROM workspace_documents WHERE document_id=:document_id) AS workspace_link_count,
                        (SELECT count(*) FROM legal_chunk_quality q
                         JOIN legal_article_chunks c ON c.id=q.chunk_id OR c.id=q.canonical_chunk_id
                         JOIN legal_articles a ON a.id=c.article_id
                         WHERE a.document_id=:document_id) AS quality_count
                    """
                ),
                {"document_id": document_id},
            ).mappings().one()
        return {
            "document_id": str(document_id),
            "law_number": str(document.get("law_number") or ""),
            "document_title": str(document.get("title") or ""),
            "chunk_ids": [int(row["chunk_id"]) for row in chunk_rows],
            "article_count": int(counts["article_count"]),
            "scope_count": int(counts["scope_count"]),
            "relationship_count": int(counts["relationship_count"]),
            "workspace_link_count": int(counts["workspace_link_count"]),
            "quality_count": int(counts["quality_count"]),
        }

    def preview_vector_cleanup(self, doc_id: str) -> dict[str, Any]:
        inventory = self._vector_cleanup_inventory(doc_id)
        manifest = build_vector_cleanup_manifest(
            document_id=inventory["document_id"],
            law_number=inventory["law_number"],
            chunk_ids=inventory["chunk_ids"],
            snapshot=default_snapshot_cache.load(),
            requested_by="admin-preview",
            reason="Xem trước dọn vector sau khi đã chặn logic.",
        )
        return {
            **manifest,
            "document_title": inventory["document_title"],
            "dry_run": True,
        }

    @staticmethod
    def _vector_cleanup_collections(client: Any) -> dict[str, Any]:
        """Return every configured serving collection that may hold a chunk.

        Approved incremental imports are written outside the immutable release
        collections.  Omitting that collection would report a successful
        cleanup while leaving the newest Admin-imported vectors orphaned.
        """

        collections = {
            "fast": client.get_collection(CHROMA_COLLECTION),
            "expanded": client.get_collection(CHROMA_SOURCE_COLLECTION),
        }
        if CHROMA_TEMPORAL_COLLECTION:
            collections["temporal"] = client.get_collection(
                CHROMA_TEMPORAL_COLLECTION
            )
        if CHROMA_INCREMENTAL_COLLECTION:
            collections["incremental"] = client.get_collection(
                CHROMA_INCREMENTAL_COLLECTION
            )
        return collections

    def preview_hard_delete(self, doc_id: str) -> dict[str, Any]:
        """Prove that a document can be removed without corrupting a release.

        Immutable release collections are checksum-bound and loaded into the
        production exact index.  Deleting one of those rows in place would
        break the serving manifest.  Permanent deletion is therefore limited
        to Admin-approved incremental documents that are outside the frozen
        release and have no vectors in its current/temporal collections.
        """

        inventory = self._vector_cleanup_inventory(doc_id)
        identity = int(inventory["document_id"])
        state = DocumentServingStateStore(self._engine).read(identity)
        vectors = self._document_vector_membership_for_chunks(
            inventory["document_id"], inventory["chunk_ids"]
        )
        collections = vectors.get("collections") or {}
        immutable_present = sum(
            int((collections.get(name) or {}).get("present") or 0)
            for name in ("current", "fast", "expanded", "temporal")
        )
        release_member = bool(
            self._serving_allowed_document_ids is not None
            and identity in self._serving_allowed_document_ids
        )
        live_verified = vectors.get("verification_source") in {
            "retrieval_v2_live",
            "local_process",
        }
        eligible = bool(live_verified and not release_member and immutable_present == 0)
        reason_code = None
        if not live_verified:
            reason_code = "live_vector_verification_required"
        elif release_member or immutable_present:
            reason_code = "immutable_release_document_cannot_be_hard_deleted"
        return {
            "status": "eligible" if eligible else "blocked",
            "eligible": eligible,
            "reason_code": reason_code,
            "document_id": inventory["document_id"],
            "law_number": inventory["law_number"],
            "document_title": inventory["document_title"],
            "state_revision": state_revision(state),
            "stored_status": state.get("status"),
            "search_included": bool(state.get("search_included")),
            "release_member": release_member,
            "database": {
                "articles": inventory["article_count"],
                "chunks": len(inventory["chunk_ids"]),
                "scope_rows": inventory["scope_count"],
                "relationships": inventory["relationship_count"],
                "workspace_links": inventory["workspace_link_count"],
                "quality_rows": inventory["quality_count"],
            },
            "vectors": vectors,
            "confirmation_text": inventory["law_number"],
            "audit_preserved": True,
        }

    def hard_delete_document(
        self,
        doc_id: str,
        *,
        requested_by: str,
        reason: str,
        expected_revision: str,
        confirmation_text: str,
    ) -> dict[str, Any]:
        """Permanently remove one incremental document with verified cleanup."""

        explanation = str(reason or "").strip()
        if not str(requested_by or "").strip() or len(explanation) < 10:
            raise ValueError("document_hard_delete_reason_required")
        with self._write_lock:
            preview = self.preview_hard_delete(doc_id)
            if not preview["eligible"]:
                raise ValueError(str(preview.get("reason_code") or "hard_delete_blocked"))
            if str(confirmation_text or "").strip() != str(preview["law_number"]).strip():
                raise ValueError("document_hard_delete_confirmation_mismatch")
            if str(expected_revision or "") != str(preview["state_revision"]):
                raise ValueError("document_serving_state_changed")

            # Fail safe: if vector or SQL cleanup fails, the document remains
            # quarantined and cannot be served by current or historical search.
            quarantine = DocumentServingStateStore(self._engine).transition(
                preview["document_id"],
                action="quarantine",
                requested_by=requested_by,
                reason=explanation,
                expected_revision=expected_revision,
            )
            chunk_ids = [int(value) for value in self._vector_cleanup_inventory(doc_id)["chunk_ids"]]
            vector_ids = [f"chunk-{value}" for value in chunk_ids]
            client = chromadb.PersistentClient(path=str(CHROMA_PATH))
            incremental = client.get_collection(CHROMA_INCREMENTAL_COLLECTION)
            reconcile_collection_from_snapshot(
                incremental,
                path=ADMIN_OVERLAY_SNAPSHOT_PATH,
                collection_name=CHROMA_INCREMENTAL_COLLECTION,
            )
            vector_report = execute_exact_vector_cleanup(
                {"incremental": incremental}, vector_ids
            )
            if vector_report.get("state") != "vector_cleanup_completed":
                raise RuntimeError("incremental_vector_cleanup_failed")
            _publish_incremental_overlay_snapshot(incremental)

            identity = int(preview["document_id"])
            with self._engine.begin() as connection:
                locked = connection.execute(
                    text("SELECT id FROM legal_documents WHERE id=:document_id FOR UPDATE"),
                    {"document_id": identity},
                ).scalar_one_or_none()
                if locked is None:
                    raise LookupError("legal_document_not_found")
                connection.execute(
                    text(
                        """
                        DELETE FROM legal_chunk_quality q
                        USING legal_article_chunks c, legal_articles a
                        WHERE (q.chunk_id = c.id OR q.canonical_chunk_id = c.id)
                          AND c.article_id = a.id
                          AND a.document_id = :document_id
                        """
                    ),
                    {"document_id": identity},
                )
                deleted = connection.execute(
                    text("DELETE FROM legal_documents WHERE id=:document_id"),
                    {"document_id": identity},
                ).rowcount
                if deleted != 1:
                    raise RuntimeError("document_hard_delete_sql_failed")

            remaining_vectors = execute_exact_vector_cleanup(
                {"incremental": incremental}, vector_ids
            )
            with self._engine.connect() as connection:
                remaining_document = int(
                    connection.execute(
                        text("SELECT count(*) FROM legal_documents WHERE id=:document_id"),
                        {"document_id": identity},
                    ).scalar_one()
                )
            if remaining_document or not remaining_vectors.get("already_absent"):
                raise RuntimeError("document_hard_delete_postcondition_failed")
            _invalidate_document_cache(preview["document_id"])
            self._refresh_management_inventory_cache()
            return {
                "status": "deleted",
                "document_id": preview["document_id"],
                "law_number": preview["law_number"],
                "document_title": preview["document_title"],
                "database_deleted": True,
                "vectors_deleted": True,
                "deleted_counts": preview["database"],
                "vector_cleanup": vector_report,
                "quarantine_revision": quarantine.get("state_revision"),
                "audit_preserved": True,
                "requested_by": requested_by,
                "reason": explanation,
            }

    def cleanup_document_vectors(
        self, doc_id: str, *, requested_by: str, reason: str
    ) -> dict[str, Any]:
        """Delete manifest IDs only after the serving snapshot blocks the document."""

        with self._write_lock:
            inventory = self._vector_cleanup_inventory(doc_id)
            manifest = build_vector_cleanup_manifest(
                document_id=inventory["document_id"],
                law_number=inventory["law_number"],
                chunk_ids=inventory["chunk_ids"],
                snapshot=default_snapshot_cache.load(),
                requested_by=requested_by,
                reason=reason,
            )
            store = VectorCleanupManifestStore(LEGAL_VECTOR_CLEANUP_DIR)
            store.save(manifest)
            store.transition(manifest["job_id"], "vector_cleanup_running")
            try:
                client = chromadb.PersistentClient(path=str(CHROMA_PATH))
                report = execute_exact_vector_cleanup(
                    self._vector_cleanup_collections(client),
                    manifest["vector_ids"],
                )
            except Exception:
                failed = store.transition(
                    manifest["job_id"], "vector_cleanup_failed"
                )
                return {**failed, "error_code": "vector_cleanup_unavailable"}
            finished = store.transition(
                manifest["job_id"],
                report["state"],
                collections=report["collections"],
            )
            _invalidate_document_cache(inventory["document_id"])
            return {**finished, "already_absent": report["already_absent"]}

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
        self._set_request_audience(request.audience)
        self._set_request_organization_scope(
            request.audience,
            request.organization_unit_id,
            document_ids=request.organization_scope_document_ids,
            chunk_ids=request.organization_scope_chunk_ids,
        )
        self._require_serving_scope()
        from api.legal_exact_retrieval import plan_exact_lookup

        explicit_as_of = (
            request.as_of_explicit
            if request.as_of_explicit is not None
            else "as_of" in request.model_fields_set
        )
        query_classification = classify_legal_query(
            request.query,
            requested_domain=request.domain,
            requested_scope=request.scope_filter,
            as_of=request.as_of,
            as_of_explicit=bool(explicit_as_of),
            today=(
                request.benchmark_today
                if os.getenv("LEGAL_BENCHMARK_MODE") == "1"
                else None
            ),
        )
        if request.temporal_scope is not None:
            if request.temporal_scope == "historical" and not explicit_as_of:
                raise ValueError("historical_scope_requires_explicit_as_of")
            query_classification["temporal_scope"] = request.temporal_scope
            if request.temporal_scope == "historical":
                query_classification["intent"] = "HISTORICAL"
        validate_m4_query_classification(query_classification)
        if not query_classification["retrieval_allowed"]:
            timing_ms = {
                "query_understanding": round((perf_counter() - started) * 1000, 1),
                "exact_lookup": 0.0,
                "embedding": 0.0,
                "ann_search": 0.0,
                "lexical_sql": 0.0,
                "hydrate_chunks": 0.0,
                "neighbors": 0.0,
                "relationships": 0.0,
                "rank_filter": 0.0,
                "validity_overlay": 0.0,
                "parent_hydration": 0.0,
                "hydrate_filter_rank": 0.0,
                "total": round((perf_counter() - started) * 1000, 1),
            }
            response: dict[str, Any] = {
                "status": "clarification_required",
                "error_code": query_classification["temporal_error_code"],
                "query": request.query,
                "as_of": None,
                "query_classification": query_classification,
                "results": [],
                "total_count": 0,
                "timing_ms": timing_ms,
            }
            if request.include_trace:
                response["trace"] = {
                    "schema_version": M3_TRACE_SCHEMA_VERSION,
                    "raw_query": request.query,
                    "normalized_query": query_classification["normalized_query"],
                    "query_classification": query_classification,
                    "vector_candidates": [],
                    "lexical_candidates": [],
                    "fusion_candidates": [],
                    "reranker_candidates": {
                        "input": [],
                        "output": [],
                        "status": {
                            "mode": "blocked",
                            "reason_code": query_classification["temporal_error_code"],
                        },
                    },
                    "expanded_evidence": {
                        "neighbor_candidates": [],
                        "fallback_candidates": [],
                        "parent_contexts": [],
                    },
                    "final_evidence": [],
                    "stage_latency_ms": timing_ms,
                }
            return response

        effective_as_of = date.fromisoformat(query_classification["retrieval_as_of"])
        retrieval_query = _rewrite_query(query_classification["normalized_query"])
        explicit_exact_plan = plan_exact_lookup(request.query)
        exact_plan = _select_exact_lookup_plan(
            request.query,
            retrieval_query,
        )
        exact_rows = self._fetch_exact_chunks(
            exact_plan,
            request.domain,
            effective_as_of,
            request.retrieval_tier,
            temporal_scope=query_classification["temporal_scope"],
            document_id=request.document_id,
        )
        exact_rows = self._shadow_filter_rows(exact_rows, request.retrieval_tier)
        exact_document_outlines = (
            _build_exact_document_outlines(exact_rows)
            if request.full_document
            and bool(explicit_exact_plan.law_numbers)
            and not bool(explicit_exact_plan.article_numbers)
            else []
        )
        exact_article_mode = is_single_exact_article_plan(explicit_exact_plan)
        exact_article_packet: dict[str, Any] | None = None
        if exact_article_mode:
            packet_cache_key: tuple[str, str] | None = None
            if (
                exact_rows
                and bool(getattr(self, "_benchmark_cache_exact_rows", False))
            ):
                first_exact = exact_rows[0]
                packet_cache_key = (
                    _normalized_identity(first_exact.get("law_number")),
                    str(first_exact.get("article_number") or "").strip().casefold(),
                )
                exact_article_packet = copy.deepcopy(
                    getattr(self, "_hydration_cache_exact_packet_cache", {}).get(
                        packet_cache_key
                    )
                )
            if exact_article_packet is None:
                exact_article_packet = build_exact_article_serving_packet(
                    exact_rows,
                    query=request.query,
                    max_chars=PARENT_CONTEXT_PER_PARENT_CHAR_LIMIT,
                )
                if (
                    packet_cache_key is not None
                    and exact_article_packet.get("status") == "complete"
                ):
                    getattr(self, "_hydration_cache_exact_packet_cache", {})[
                        packet_cache_key
                    ] = copy.deepcopy(exact_article_packet)
            if exact_article_packet["status"] == "complete":
                serving_ids = set(
                    _exact_packet_serving_chunk_ids(exact_article_packet)
                )
                exact_rows = [
                    self._normalize_candidate_effective_status(dict(row))
                    for row in exact_article_packet["ordered_rows"]
                    if int(row.get("chunk_id") or 0) in serving_ids
                ]
            else:
                exact_rows = []
        else:
            # A document-only exact lookup can return hundreds of chunks. Rank
            # those bounded rows against the actual question before applying
            # the ordinary diversity cap. A named Article never enters this
            # branch: it is assembled and checked above as one complete packet.
            exact_rows.sort(
                key=lambda row: _exact_metadata_relevance(retrieval_query, row),
                reverse=True,
            )
        exact_limited: list[dict[str, Any]] = []
        exact_selected_ids: set[int] = set()
        exact_document_counts: dict[int, int] = {}
        exact_article_counts: dict[tuple[int, str], int] = {}

        def add_exact_row(row: dict[str, Any]) -> None:
            chunk_id = int(row.get("chunk_id") or 0)
            if chunk_id in exact_selected_ids:
                return
            document_id = int(row.get("document_id") or 0)
            article_key = (
                document_id,
                str(row.get("article_number") or ""),
            )
            article_limit = _exact_article_chunk_limit(
                article_key[1],
                requested_articles,
            )
            if (
                exact_document_counts.get(document_id, 0) >= 3
                or exact_article_counts.get(article_key, 0) >= article_limit
            ):
                return
            exact_limited.append(row)
            exact_selected_ids.add(chunk_id)
            exact_document_counts[document_id] = (
                exact_document_counts.get(document_id, 0) + 1
            )
            exact_article_counts[article_key] = (
                exact_article_counts.get(article_key, 0) + 1
            )

        # Reserve one passage for every provision explicitly named by the
        # caller before filling the remaining per-document budget. Without
        # this pass, a multi-provision comparison such as Điều 13/35 can lose
        # one side even though both provisions were found exactly.
        requested_articles = {
            str(number).strip().casefold()
            for number in (exact_plan.article_numbers or ())
        }
        if not exact_article_mode:
            if requested_articles:
                for row in exact_rows:
                    if (
                        str(row.get("article_number") or "").strip().casefold()
                        in requested_articles
                    ):
                        add_exact_row(row)
            for row in exact_rows:
                add_exact_row(row)
            exact_rows = exact_limited[
                : max(
                    3,
                    3
                    * len(
                        exact_plan.law_numbers
                        or (
                            (exact_plan.law_number,)
                            if exact_plan.law_number
                            else ()
                        )
                    ),
                )
            ]
        exact_finished_at = perf_counter()
        # A bare article/clause number is ambiguous across the corpus. Only
        # skip ANN when the exact index has an unambiguous identity to bind
        # (law, law/article pair, reviewed procedure or form).
        explicit_exact_identity = bool(
            explicit_exact_plan.law_number
            or explicit_exact_plan.law_numbers
            or explicit_exact_plan.article_law_pairs
            or explicit_exact_plan.procedure_id
            or explicit_exact_plan.form_codes
        )
        exact_provision_resolved = explicit_exact_identity
        if exact_provision_resolved:
            # An exact document+article packet is already authoritative and
            # sufficient for this issue. Avoid serializing a redundant HNSW
            # query behind the local Chroma lock; any missing facet can still
            # trigger the single bounded support pass in the Ask layer.
            raw = {"ids": [[]], "metadatas": [[]], "distances": [[]]}
            encoded_at = exact_finished_at
            searched_at = exact_finished_at
        else:
            prefetched_raw = getattr(_batch_vector_context, "raw", None)
            if prefetched_raw is not None:
                raw = prefetched_raw
                encoded_at = exact_finished_at
                searched_at = perf_counter()
            else:
                query_vector = self.encode_query(request.query)
                encoded_at = perf_counter()

                collection = self._vector_collection_for(
                    query_classification,
                    request.retrieval_tier,
                )
                # Chroma's local HNSW client is not documented as thread-safe.
                # A commune domain contains many field IDs and older Chroma metadata can
                # be stale. The reviewed SQL legal scope is the authoritative filter.
                with self._query_lock:
                    raw = self._query_with_incremental(
                        collection,
                        query_embeddings=[query_vector.tolist()],
                        n_results=request.candidate_count,
                    )
                searched_at = perf_counter()

        from api.legal_retrieval_quality import reciprocal_rank_fusion

        vector_ranked: list[dict[str, Any]] = []
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
                vector_ranked.append(
                    {
                        "chunk_id": chunk_id,
                        "vector_score": 1.0 - float(distance),
                        "metadata": metadata,
                        "retrieval_source": "vector",
                    }
                )
                vector_candidate_count += 1
        allowed_vector_ids = self._request_serving_chunk_ids()
        if allowed_vector_ids is not None:
            vector_ranked = [
                row for row in vector_ranked
                if int(row["chunk_id"]) in allowed_vector_ids
            ]
            vector_candidate_count = len(vector_ranked)
        vector_trace_candidates = _retrieval_stage_trace_items(vector_ranked)

        # In vector-first batch mode the caller deliberately sets the lexical
        # budget to zero. Do not invoke SQL with ``LIMIT 0``: PostgreSQL still
        # has to evaluate the similarity/order expressions before returning an
        # empty page, which was the source of multi-second p95 outliers. A
        # positive budget keeps the existing hybrid/direct-search behaviour.
        if not exact_article_mode and request.lexical_candidate_count > 0:
            lexical_rows = self._fetch_lexical_chunks(
                retrieval_query,
                request.domain,
                effective_as_of,
                request.retrieval_tier,
                limit=request.lexical_candidate_count,
                # Keep overlaps: RRF needs the same chunk's independent vector
                # and lexical ranks. Exact law/document metadata hits are not
                # a complete lexical search and must not suppress this pass.
                exclude_chunk_ids=[],
                temporal_scope=query_classification["temporal_scope"],
            )
        else:
            lexical_rows = []
        lexical_rows = self._shadow_filter_rows(
            lexical_rows,
            request.retrieval_tier,
        )
        lexical_trace_candidates = _retrieval_stage_trace_items(lexical_rows)
        lexical_finished_at = perf_counter()
        lexical_ranked = [
            {
                "chunk_id": int(row["chunk_id"]),
                "vector_score": 0.15,
                "metadata": row,
                "retrieval_source": "lexical",
            }
            for row in lexical_rows
        ]
        candidates = reciprocal_rank_fusion(
            vector_ranked=vector_ranked,
            lexical_ranked=lexical_ranked,
            key="chunk_id",
        )
        for candidate in candidates:
            sources = candidate.get("retrieval_sources") or []
            candidate["retrieval_source"] = (
                "hybrid" if len(sources) > 1 else sources[0] if sources else "vector"
            )
            candidate.setdefault("vector_score", 0.15)
            candidate.setdefault("metadata", {})

        # Exact identifiers are authoritative routing constraints. Put exact
        # metadata matches ahead of ANN/RRF candidates while retaining the
        # latter for other facets requested in the same question.
        exact_candidates = [
            {
                "chunk_id": int(row["chunk_id"]),
                "vector_score": 0.5,
                "metadata": row,
                "retrieval_source": "exact_metadata",
                "retrieval_sources": ["exact_metadata"],
                "rrf_score": 0.25,
            }
            for row in exact_rows
        ]
        exact_ids = {int(row["chunk_id"]) for row in exact_rows}
        candidates = exact_candidates + [
            candidate
            for candidate in candidates
            if int(candidate["chunk_id"]) not in exact_ids
        ]
        fusion_trace_candidates = _retrieval_stage_trace_items(candidates)

        lexical_candidate_count = len(lexical_rows)
        candidate_count_before_hydration = len(candidates)

        exact_hydration_laws = (
            list(
                exact_plan.law_numbers
                or (
                    (exact_plan.law_number,)
                    if exact_plan.law_number
                    else ()
                )
            )
            if (
                exact_article_mode
                or _exact_override_scope_join(exact_plan, request.domain)
            )
            else []
        )
        rows = self._fetch_chunks(
            [item["chunk_id"] for item in candidates],
            request.retrieval_tier,
            exact_law_numbers=exact_hydration_laws,
            allow_exact_missing_domain=exact_article_mode,
            temporal_scope=query_classification["temporal_scope"],
        )
        chunks_finished_at = perf_counter()
        neighbor_ids: list[int] = []
        if request.enable_neighbor_expansion:
            neighbor_ids = self._fetch_neighbor_chunk_ids(rows)
            neighbor_ids = self._shadow_filter_ids(
                neighbor_ids,
                request.retrieval_tier,
            )
        neighbor_rows: list[dict[str, Any]] = []
        if neighbor_ids:
            neighbor_rows = self._fetch_chunks(
                neighbor_ids,
                request.retrieval_tier,
                temporal_scope=query_classification["temporal_scope"],
            )
            existing_chunk_ids = {int(row["chunk_id"]) for row in rows}
            for row in neighbor_rows:
                chunk_id = int(row["chunk_id"])
                if chunk_id in existing_chunk_ids:
                    continue
                existing_chunk_ids.add(chunk_id)
                rows.append(row)
                candidates.append(
                    {
                        "chunk_id": chunk_id,
                        "vector_score": 0.14,
                        "metadata": row,
                        "retrieval_source": "article_neighbor",
                        "rrf_score": 0.0,
                    }
                )
        neighbors_finished_at = perf_counter()
        row_by_chunk = {int(row["chunk_id"]): row for row in rows}
        relationships_by_document = self._fetch_relationships(
            [int(row["document_id"]) for row in rows]
        )
        relationships_finished_at = perf_counter()

        fallback_phrases, fallback_domain = _fallback_phrases(request.query)
        fallback_rows: list[dict[str, Any]] = []
        if (
            not exact_article_mode
            and request.allow_broad_fallback
            and fallback_phrases
        ):
            fallback_rows = self._fetch_fallback_chunks(
                fallback_phrases,
                request.domain or fallback_domain,
                effective_as_of,
                request.retrieval_tier,
                exclude_chunk_ids=list(row_by_chunk.keys()),
                temporal_scope=query_classification["temporal_scope"],
            )
            fallback_rows = self._shadow_filter_rows(
                fallback_rows,
                request.retrieval_tier,
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
            exact_identity_domain_allowed = bool(
                candidate.get("retrieval_source") == "exact_metadata"
                and explicit_exact_plan.law_number
            )
            if (
                not exact_identity_domain_allowed
                and not _domain_matches(request.domain, row.get("domain_slug"))
            ):
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
            if not self._is_current_for_serving(
                row,
                effective_as_of,
                temporal_scope=query_classification["temporal_scope"],
            ):
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
            if _has_expired_legal_basis(
                relationships,
                effective_as_of,
                candidate_scope=row.get("scope"),
                candidate_document_type=row.get("document_type"),
                candidate_issuing_agency=row.get("issuing_agency"),
            ):
                filtered_candidates.append(
                    {
                        "chunk_id": candidate["chunk_id"],
                        "reason": "expired_legal_basis",
                        "law_number": row.get("law_number"),
                        "document_title": row.get("document_title"),
                    }
                )
                continue

            hard_negative_reason = _request_context_hard_negative(
                query=request.query,
                row=row,
                facets=request.facets,
                subject_anchor=request.subject_anchor,
            )
            if hard_negative_reason:
                filtered_candidates.append(
                    {
                        "chunk_id": candidate["chunk_id"],
                        "reason": hard_negative_reason,
                        "law_number": row.get("law_number"),
                        "document_title": row.get("document_title"),
                        "domain_slug": row.get("domain_slug"),
                    }
                )
                continue

            metadata = candidate.get("metadata") or {}
            lexical_boost = _lexical_boost(request.query, row)
            topic_boost = _topic_boost(request.query, row)
            retrieval_source = candidate.get("retrieval_source", "vector")
            
            # The legacy stack remains byte-for-byte compatible. M5's RRF and
            # weighted branches are explicit and never mix one another's score.
            v_score = candidate["vector_score"]
            norm_b_score = candidate["norm_bm25_score"]
            raw_rrf_score = float(candidate.get("rrf_score") or 0.0)
            if request.fusion_strategy == "legacy_stack":
                hybrid_retrieval_score = (v_score * 0.6) + (norm_b_score * 0.4)
                rrf_boost = min(raw_rrf_score * 4.0, 0.15)
            else:
                from api.legal_retrieval_quality import hybrid_fusion_score

                hybrid_retrieval_score = hybrid_fusion_score(
                    strategy=request.fusion_strategy,
                    vector_score=v_score,
                    normalized_lexical_score=norm_b_score,
                    rrf_score=raw_rrf_score,
                    vector_weight=request.vector_weight,
                    lexical_weight=request.lexical_weight,
                )
                lexical_boost = 0.0
                topic_boost = 0.0
                rrf_boost = 0.0
            score = (
                hybrid_retrieval_score
                + lexical_boost
                + topic_boost
                + rrf_boost
                + (2.0 if retrieval_source == "exact_metadata" else 0.0)
                + (
                    3.0
                    if (
                        retrieval_source == "exact_metadata"
                        and str(row.get("article_number") or "")
                        .strip()
                        .casefold()
                        in requested_articles
                    )
                    else 0.0
                )
            )
            
            ranked.append(
                {
                    "score": round(score, 6),
                    "vector_score": round(v_score, 6),
                    "bm25_score": round(candidate["bm25_score"], 6),
                    # Kept as a zero-valued compatibility field.  Legal
                    # authority is a hard ordering key, never a score boost.
                    "metadata_score": 0.0,
                    "lexical_boost": round(lexical_boost, 6),
                    "topic_boost": round(topic_boost, 6),
                    "rrf_score": round(float(candidate.get("rrf_score") or 0.0), 6),
                    "retrieval_source": retrieval_source,
                    "retrieval_sources": list(
                        candidate.get("retrieval_sources") or [retrieval_source]
                    ),
                    **row,
                    **parse_structural_path(str(row.get("chunk_heading") or "")),
                    "relationships": relationships,
                    "source_url": row.get("source_url")
                    or metadata.get("source_url")
                    or None,
                    "primary_organization_unit_id": metadata.get(
                        "primary_organization_unit_id"
                    )
                    or row.get("primary_organization_unit_id"),
                    "organization_unit_ids": metadata.get(
                        "organization_unit_ids"
                    )
                    or row.get("organization_unit_ids")
                    or "",
                    "organization_assignment_state": metadata.get(
                        "organization_assignment_state"
                    )
                    or row.get("organization_assignment_state"),
                }
            )

        ranked = self._shadow_filter_rows(ranked, request.retrieval_tier)
        if request.ranking_strategy == "rrf_v2":
            from api.legal_retrieval_quality import rank_candidates_rrf_v2

            ranked = rank_candidates_rrf_v2(ranked)

        current_count = len(ranked)
        filtered_counts: dict[str, int] = {}
        for item in filtered_candidates:
            reason = str(item.get("reason") or "unknown")
            filtered_counts[reason] = filtered_counts.get(reason, 0) + 1

        reranker_status: dict[str, Any] = {}
        reranker_input_trace = _retrieval_stage_trace_items(ranked)
        if exact_article_mode and exact_article_packet is not None:
            # Exact-article integrity has already validated the complete
            # parent and selected an ordered answer window. A relevance
            # reranker must not reorder or drop that deterministic packet.
            serving_ids = _exact_packet_serving_chunk_ids(exact_article_packet)
            ranked_by_id = {
                int(item.get("chunk_id") or 0): item
                for item in ranked
            }
            ranked = [
                ranked_by_id[chunk_id]
                for chunk_id in serving_ids
                if chunk_id in ranked_by_id
            ]
            reranker_status = {
                "mode": "exact_article_bypass",
                "reason_code": "deterministic_exact_article_packet",
                "version": "exact-article-v1",
                "degraded": False,
                "candidate_count": len(ranked),
                "scored_count": 0,
                "latency_ms": 0.0,
            }
        else:
            ranked = self._rerank_candidates(
                request.query,
                ranked,
                status_out=reranker_status,
                ranking_strategy=request.ranking_strategy,
                allow_learned=request.enable_learned_reranker,
                rerank_top_n=request.rerank_top_n,
                facets=request.facets,
                procedure_id=request.procedure_id,
                subject_anchor=request.subject_anchor,
            )
            if (
                request.audience == "officer"
                and request.organization_unit_id
                and not explicit_exact_plan.law_number
            ):
                target_unit = request.organization_unit_id
                for item in ranked:
                    unit_ids = {
                        value.strip()
                        for value in str(
                            item.get("organization_unit_ids") or ""
                        ).split(",")
                        if value.strip()
                    }
                    primary_unit = str(
                        item.get("primary_organization_unit_id") or ""
                    ).strip()
                    same_unit = target_unit == primary_unit or target_unit in unit_ids
                    item["organization_unit_boost"] = 0.035 if same_unit else 0.0
                    if same_unit:
                        item["score"] = round(float(item.get("score") or 0.0) + 0.035, 6)
            ranked = rank_legal_evidence(
                ranked,
                scope_filter=request.scope_filter,
            )
        reranker_output_trace = _retrieval_stage_trace_items(ranked)
        # Preserve the bounded passages of an explicitly requested provision
        # before unrelated-but-diverse articles consume the result budget.
        # This matters when clause 1 and clause 2 of the same article are
        # separate chunks: both can be required to answer a single dossier
        # question and are still limited by the later 2-chunk/article cap.
        requested_exact_ids = {
            int(item.get("chunk_id") or 0)
            for item in ranked
            if (
                item.get("retrieval_source") == "exact_metadata"
                and bool(explicit_exact_plan.law_number)
            )
        }
        ranked = _reserve_explicit_exact_results(
            ranked,
            explicit_law_number=explicit_exact_plan.law_number,
        )
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
            if int(item.get("chunk_id") or 0) in requested_exact_ids:
                # Preserve every bounded chunk of the explicitly requested
                # provision at its hierarchy position.  Do not prepend it
                # ahead of a superior legal authority.
                primary_evidence.append(item)
                seen_articles.add(article_key)
                continue
            if article_key in seen_articles:
                complementary_evidence.append(item)
                continue
            seen_articles.add(article_key)
            if int(item.get("chunk_id") or 0) not in requested_exact_ids:
                primary_evidence.append(item)

        # Prefer source/article diversity first.  If there are not enough
        # distinct articles, retain one complementary chunk per article rather
        # than silently discarding useful parts of a long provision.
        deduplicated: list[dict[str, Any]] = []
        article_counts: dict[tuple[str, str], int] = {}
        document_counts: dict[str, int] = {}
        for item in primary_evidence:
            if not exact_article_mode and len(deduplicated) >= request.limit:
                break
            document_key = str(item.get("document_id") or "")
            if (
                not exact_article_mode
                and document_counts.get(document_key, 0) >= 3
            ):
                continue
            article_key = (
                document_key,
                str(item.get("article_id") or item.get("article_number") or ""),
            )
            deduplicated.append(item)
            article_counts[article_key] = 1
            document_counts[document_key] = (
                document_counts.get(document_key, 0) + 1
            )
        for item in complementary_evidence:
            if not exact_article_mode and len(deduplicated) >= request.limit:
                break
            article_key = (
                str(item.get("document_id") or ""),
                str(item.get("article_id") or item.get("article_number") or ""),
            )
            document_key = article_key[0]
            if (
                not exact_article_mode
                and (
                    article_counts.get(article_key, 0) >= 2
                    or document_counts.get(document_key, 0) >= 3
                )
            ):
                continue
            deduplicated.append(item)
            article_counts[article_key] = article_counts.get(article_key, 0) + 1
            document_counts[document_key] = (
                document_counts.get(document_key, 0) + 1
            )

        # Diversity selection may interleave complementary passages after
        # lower-authority primary passages. Re-assert the hard hierarchy on
        # the final bounded set, then put an explicitly named instrument back
        # at the front.  Authority still orders the supporting documents; it
        # must not hide the document that the citizen actually asked about.
        deduplicated = rank_legal_evidence(
            deduplicated,
            scope_filter=request.scope_filter,
        )
        deduplicated = _reserve_explicit_exact_results(
            deduplicated,
            explicit_law_number=explicit_exact_plan.law_number,
        )
        validity_overlay_started_at = perf_counter()
        validity_snapshot = default_snapshot_cache.load()
        validity_projection = apply_validity_overlay(
            {"results": deduplicated},
            snapshot=validity_snapshot,
            as_of=effective_as_of,
        )
        deduplicated = validity_projection.get("results") or []
        validity_summary = validity_projection.get("validity_sync") or {}
        if exact_article_mode and exact_article_packet is not None:
            expected_ids = _exact_packet_serving_chunk_ids(exact_article_packet)
            surviving_by_id = {
                int(item.get("chunk_id")): item
                for item in deduplicated
                if _as_int(item.get("chunk_id")) is not None
            }
            packet_was_complete = exact_article_packet.get("status") == "complete"
            if not packet_was_complete or set(surviving_by_id) != set(expected_ids):
                reasons = list(exact_article_packet.get("reason_codes") or [])
                if packet_was_complete and set(surviving_by_id) != set(expected_ids):
                    reasons.append("exact_article_filtered_after_integrity_check")
                exact_article_packet = {
                    **exact_article_packet,
                    "status": "incomplete",
                    "reason_codes": list(dict.fromkeys(reasons)),
                }
                deduplicated = []
            else:
                deduplicated = attach_exact_article_packet(
                    [surviving_by_id[chunk_id] for chunk_id in expected_ids],
                    exact_article_packet,
                )
        validity_overlay_finished_at = perf_counter()
        parent_hydration_started_at = perf_counter()
        parent_lookup_count = 0
        parent_summary = {
            "unique_parent_count": 0,
            "hydrated_parent_count": 0,
            "truncated_parent_count": 0,
            "parent_fallback_count": 0,
        }
        try:
            if exact_article_mode:
                # The verified packet already carries the complete parent once
                # and every child in source order. Ordinary adaptive hydration
                # would truncate it back to a relevance-centred excerpt.
                parent_summary = {
                    "unique_parent_count": 1 if deduplicated else 0,
                    "hydrated_parent_count": 1 if deduplicated else 0,
                    "truncated_parent_count": 0,
                    "parent_fallback_count": 0 if deduplicated else 1,
                }
                parent_lookup_count = 0
            elif request.enable_parent_expansion:
                selected_article_ids = [
                    article_id
                    for item in deduplicated
                    if (article_id := _as_int(item.get("article_id"))) is not None
                ]
                parent_lookup_count = 1 if selected_article_ids else 0
                parent_rows = self._fetch_parent_contexts(selected_article_ids)
                deduplicated, parent_summary = hydrate_parent_context(
                    deduplicated,
                    parent_rows,
                    per_parent_char_limit=PARENT_CONTEXT_PER_PARENT_CHAR_LIMIT,
                    total_char_limit=PARENT_CONTEXT_TOTAL_CHAR_LIMIT,
                )
        except Exception:
            # Parent expansion is optional context enrichment. Preserve the
            # already filtered/ranked child evidence and fail explicitly.
            deduplicated, parent_summary = hydrate_parent_context(
                deduplicated,
                [],
                per_parent_char_limit=PARENT_CONTEXT_PER_PARENT_CHAR_LIMIT,
                total_char_limit=PARENT_CONTEXT_TOTAL_CHAR_LIMIT,
            )
            for item in deduplicated:
                item["parent_context_reason"] = "projection_error"
            parent_summary["parent_fallback_count"] = parent_summary[
                "unique_parent_count"
            ]
        parent_hydration_finished_at = perf_counter()
        bound_results = [
            bind_request_provenance(item, request) for item in deduplicated
        ]
        finished_at = perf_counter()
        response = {
            "query": request.query,
            "as_of": effective_as_of.isoformat(),
            "query_classification": query_classification,
            "results": bound_results,
            "total_count": len(bound_results),
            "validity_sync": validity_summary,
            "reranker": reranker_status,
            "expansion": {
                "parent_enabled": request.enable_parent_expansion,
                "neighbor_enabled": request.enable_neighbor_expansion,
                "neighbor_candidate_count": len(neighbor_rows),
                "parent_lookup_count": parent_lookup_count,
                **parent_summary,
            },
            "exact_article_packet": public_exact_article_packet(
                exact_article_packet
            ),
            "exact_document_outlines": exact_document_outlines,
            "versions": build_runtime_version_trace(
                app_version="1.0.0",
                index_collection=(
                    CHROMA_SOURCE_COLLECTION
                    if request.retrieval_tier == "expanded"
                    else CHROMA_COLLECTION
                ),
                embedding_fingerprint=self._model_fingerprint,
                validity_snapshot=validity_snapshot,
                reranker_version=str(
                    reranker_status.get("version") or "heuristic-v1"
                ),
            ),
            "timing_ms": {
                "exact_lookup": round((exact_finished_at - started) * 1000, 1),
                "embedding": round((encoded_at - exact_finished_at) * 1000, 1),
                "ann_search": round((searched_at - encoded_at) * 1000, 1),
                "lexical_sql": round((lexical_finished_at - searched_at) * 1000, 1),
                "hydrate_chunks": round((chunks_finished_at - lexical_finished_at) * 1000, 1),
                "neighbors": round((neighbors_finished_at - chunks_finished_at) * 1000, 1),
                "relationships": round((relationships_finished_at - neighbors_finished_at) * 1000, 1),
                "rank_filter": round((validity_overlay_started_at - relationships_finished_at) * 1000, 1),
                "validity_overlay": round((validity_overlay_finished_at - validity_overlay_started_at) * 1000, 1),
                "parent_hydration": round((parent_hydration_finished_at - parent_hydration_started_at) * 1000, 1),
                "hydrate_filter_rank": round((validity_overlay_started_at - searched_at) * 1000, 1),
                "total": round((finished_at - started) * 1000, 1),
            },
        }
        if request.include_trace:
            detected = _detected_domain(deduplicated)
            response["trace"] = {
                "schema_version": M3_TRACE_SCHEMA_VERSION,
                "raw_query": request.query,
                "normalized_query": query_classification["normalized_query"],
                "query_classification": {
                    **query_classification,
                    "requested_domain": request.domain,
                    "detected_domain": detected,
                    "retrieval_tier": request.retrieval_tier,
                    "scope_filter": request.scope_filter,
                    "exact_metadata_lookup": exact_plan.requires_exact_metadata_lookup,
                    "exact_article_mode": exact_article_mode,
                    "law_numbers": list(exact_plan.law_numbers),
                    "article_numbers": list(exact_plan.article_numbers),
                    "clause_number": exact_plan.clause_number,
                    "procedure_id": exact_plan.procedure_id,
                    "request_procedure_id": request.procedure_id,
                    "request_facets": list(request.facets),
                    "subject_anchor": request.subject_anchor,
                    "form_codes": list(exact_plan.form_codes),
                    "fallback_phrases": list(fallback_phrases),
                    "fallback_domain": fallback_domain,
                    "retrieval_query": retrieval_query,
                },
                "vector_candidates": vector_trace_candidates,
                "lexical_candidates": lexical_trace_candidates,
                "fusion_candidates": fusion_trace_candidates,
                "reranker_candidates": {
                    "input": reranker_input_trace,
                    "output": reranker_output_trace,
                    "status": reranker_status,
                },
                "expanded_evidence": {
                    "neighbor_candidates": _retrieval_stage_trace_items(
                        neighbor_rows, default_source="article_neighbor"
                    ),
                    "fallback_candidates": _retrieval_stage_trace_items(
                        fallback_rows, default_source="fallback"
                    ),
                    "parent_contexts": [
                        _retrieval_candidate_trace_item(item, rank=index + 1)
                        for index, item in enumerate(bound_results)
                        if item.get("parent_context_ref")
                    ],
                },
                "final_evidence": _retrieval_stage_trace_items(bound_results),
                "stage_latency_ms": dict(response["timing_ms"]),
                "input_question": request.query,
                "selected_domain": request.domain,
                "scope_filter": request.scope_filter,
                "retrieval_tier": request.retrieval_tier,
                "request_id": request.request_id,
                "issue_id": request.issue_id,
                "issue_domain": request.issue_domain or request.domain,
                "collection": (
                    self._serving_scope.collection_name
                    if self._serving_scope is not None
                    else (
                        CHROMA_SOURCE_COLLECTION
                        if request.retrieval_tier == "expanded"
                        else CHROMA_COLLECTION
                    )
                ),
                "versions": response["versions"],
                "reranker": reranker_status,
                "ranking": {
                    "strategy": request.ranking_strategy,
                    "vector_bm25_fusion": request.fusion_strategy,
                    "vector_weight": request.vector_weight,
                    "lexical_weight": request.lexical_weight,
                    "learned_reranker_requested": request.enable_learned_reranker,
                    "rerank_top_n": request.rerank_top_n,
                    "parent_expansion_enabled": request.enable_parent_expansion,
                    "neighbor_expansion_enabled": request.enable_neighbor_expansion,
                    "request_context_rerank": bool(
                        request.facets or request.procedure_id or request.subject_anchor
                    ),
                },
                "scope_filter_applied": request.retrieval_tier == "core",
                "scope_policy": (
                    "active_central_and_hai_phong_excluding_other_provinces"
                    if request.retrieval_tier == "expanded"
                    else "reviewed_hai_phong_commune_scope"
                ),
                "detected_domain": detected,
                "pipeline_counts": {
                    "ann_skipped_for_exact_provision": exact_provision_resolved,
                    "exact_candidates": len(exact_rows),
                    "vector_candidates": vector_candidate_count,
                    "lexical_candidates": lexical_candidate_count,
                    "candidates_before_hydration": candidate_count_before_hydration,
                    "hydrated_rows": len(row_by_chunk),
                    "current_candidates": current_count,
                    "filtered_candidates": len(filtered_candidates),
                    "filtered_by_reason": filtered_counts,
                    "deduplicated_results": len(bound_results),
                    "validity_filtered_results": int(
                        validity_summary.get("filtered_count") or 0
                    ),
                    "parent_lookup_count": parent_lookup_count,
                    **parent_summary,
                },
                "exact_article_packet": public_exact_article_packet(
                    exact_article_packet
                ),
                "exact_document_outlines": exact_document_outlines,
                "retrieved_chunks": [
                    _chunk_trace_item(item) for item in bound_results
                ],
                "filtered_candidates": filtered_candidates[:30],
                "hierarchy": hierarchy_summary(bound_results),
                "llm_sources": [
                    _source_trace_item(item) for item in bound_results
                ],
            }
        return response

    def _shadow_filter_ids(
        self,
        chunk_ids: list[int],
        retrieval_tier: str,
    ) -> list[int]:
        scope = getattr(self, "_serving_scope", None)
        if scope is not None or self._request_organization_scope_enforced():
            allowed = self._request_serving_chunk_ids()
        else:
            allowed_by_tier = getattr(self, "_shadow_allowed_chunk_ids", None)
            if not allowed_by_tier:
                return chunk_ids
            allowed = allowed_by_tier.get(retrieval_tier)
        if allowed is None:
            return []
        return [
            int(chunk_id)
            for chunk_id in chunk_ids
            if int(chunk_id) in allowed
        ]

    def _shadow_filter_rows(
        self,
        rows: list[dict[str, Any]],
        retrieval_tier: str,
    ) -> list[dict[str, Any]]:
        scope = getattr(self, "_serving_scope", None)
        if scope is not None or self._request_organization_scope_enforced():
            allowed = self._request_serving_chunk_ids()
        else:
            allowed_by_tier = getattr(self, "_shadow_allowed_chunk_ids", None)
            if not allowed_by_tier:
                return rows
            allowed = allowed_by_tier.get(retrieval_tier)
        if allowed is None:
            return []
        return [
            row
            for row in rows
            if _as_int(row.get("chunk_id")) in allowed
        ]

    def _fetch_exact_chunks(
        self,
        plan: Any,
        domain: str | None,
        as_of: date,
        retrieval_tier: str,
        temporal_scope: str = "current",
        document_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Look up exact legal identifiers using metadata only, before ANN.

        The WHERE clause deliberately contains no chunk-content predicate and
        no broad LIKE. A clause number can only boost an exact normalized chunk
        heading; article hydration remains bounded by the exact law/article.
        """

        if not plan.requires_exact_metadata_lookup:
            return []
        # Procedure IDs and form codes are resolved by the approved procedure
        # catalog in the Ask layer. They must never be fuzzily matched against
        # arbitrary legal chunk content.
        if not plan.law_number:
            return []

        article_law_pairs = tuple(
            getattr(plan, "article_law_pairs", ()) or ()
        )
        paired_law_number = (
            article_law_pairs[0][0] if len(article_law_pairs) == 1 else None
        )
        paired_article_number = (
            article_law_pairs[0][1] if len(article_law_pairs) == 1 else None
        )
        lookup_law_numbers = (
            (paired_law_number,)
            if paired_law_number
            else (
                plan.law_numbers
                or ((plan.law_number,) if plan.law_number else ())
            )
        )
        lookup_article_numbers = (
            (paired_article_number,)
            if paired_article_number
            else (
                plan.article_numbers
                or ((plan.article_number,) if plan.article_number else ())
            )
        )
        cached_exact_index = getattr(self, "_hydration_cache_exact_index", None)
        if (
            cached_exact_index
            and retrieval_tier == "core"
            and bool(getattr(self, "_benchmark_cache_exact_rows", False))
        ):
            exact_identity_domain_bypass = bool(plan.law_number)
            cached_candidates: list[dict[str, Any]] = []
            for law_number in lookup_law_numbers:
                law_key = _normalized_identity(law_number)
                if lookup_article_numbers:
                    article_keys = [
                        str(value).strip().casefold()
                        for value in lookup_article_numbers
                    ]
                else:
                    article_keys = [
                        article_key
                        for candidate_law, article_key in cached_exact_index
                        if candidate_law == law_key
                    ]
                for article_key in article_keys:
                    cached_candidates.extend(
                        self._normalize_candidate_effective_status(dict(row))
                        for row in cached_exact_index.get((law_key, article_key), [])
                    )
            if cached_candidates:
                as_of_text = as_of.isoformat()
                filtered_cached: list[dict[str, Any]] = []
                seen_cached_ids: set[int] = set()
                for row in cached_candidates:
                    chunk_id = _as_int(row.get("chunk_id"))
                    if chunk_id is None or chunk_id in seen_cached_ids:
                        continue
                    allowed_statuses = {"active"}
                    if temporal_scope == "historical":
                        allowed_statuses.update(
                            {"archived", "expired", "historical", "replaced"}
                        )
                    if bool(getattr(self, "_benchmark_allow_staging", False)):
                        allowed_statuses.add("staging")
                    if str(row.get("document_status") or "active").casefold() not in allowed_statuses:
                        continue
                    if str(row.get("article_status") or "active").casefold() not in allowed_statuses:
                        continue
                    effective = str(row.get("effective_date") or "")[:10]
                    expired = str(row.get("expired_date") or "")[:10]
                    article_effective = str(row.get("article_effective_from") or "")[:10]
                    article_expired = str(row.get("article_effective_to") or "")[:10]
                    if effective and effective > as_of_text:
                        continue
                    if expired and expired <= as_of_text:
                        continue
                    if article_effective and article_effective > as_of_text:
                        continue
                    if article_expired and article_expired <= as_of_text:
                        continue
                    if (
                        domain
                        and not exact_identity_domain_bypass
                        and not _domain_matches(domain, row.get("domain_slug"))
                        and not _domain_matches(domain, row.get("domain_name"))
                    ):
                        continue
                    if plan.clause_number:
                        heading = str(row.get("chunk_heading") or "").casefold()
                        clause = str(plan.clause_number).casefold()
                        if f"khoan {clause}" not in heading and f"{clause}." not in heading:
                            continue
                    seen_cached_ids.add(chunk_id)
                    filtered_cached.append(row)
                if filtered_cached:
                    filtered_cached.sort(
                        key=lambda row: (
                            int(row.get("chunk_index") or 0),
                            int(row.get("chunk_id") or 0),
                        )
                    )
                    limit = 1_000 if is_single_exact_article_plan(plan) else 300
                    return filtered_cached[:limit]
        exact_scope_ids: list[int] | None = None
        local_overlay_document_ids = sorted(self._local_overlay_document_ids())
        if document_id is not None:
            serving_ids = self._request_serving_document_ids()
            if serving_ids is not None and int(document_id) not in serving_ids:
                return []
            exact_scope_ids = [int(document_id)]
        law_scope = getattr(self, "_serving_law_document_ids", None) or {}
        if law_scope and exact_scope_ids is None:
            exact_scope_ids = sorted(
                {
                    document_id
                    for law_number in lookup_law_numbers
                    for document_id in law_scope.get(
                        _normalized_identity(law_number), set()
                    )
                }
            )
            if not exact_scope_ids and local_overlay_document_ids:
                # The immutable manifest cannot contain a document approved
                # after its release. Keep exact lookup bounded to the local
                # overlay, then apply the exact law-number predicate below.
                exact_scope_ids = local_overlay_document_ids
            if not exact_scope_ids:
                # The exact instrument is not in this serving manifest. Let
                # the normal ANN path decide whether a related current source
                # can answer, but never run a broad exact SQL scan.
                return []
        params: dict[str, Any] = {
            "as_of": as_of,
            "limit": max(
                1_000 if is_single_exact_article_plan(plan) else 300,
                (1_000 if is_single_exact_article_plan(plan) else 300)
                * len(
                    lookup_law_numbers
                ),
            ),
            "law_numbers": list(lookup_law_numbers),
            "article_numbers": (
                [
                    value
                    for number in lookup_article_numbers
                    # ``legal_normalize_text`` returns case-folded text while
                    # exact identifier parsing intentionally returns upper-
                    # case identifiers.  Numeric provisions hid this mismatch;
                    # amended provisions such as 18a/37a did not.  Bind the
                    # SQL parameters in the same normalized text form so an
                    # explicit alphanumeric Article remains an exact lookup.
                    for value in (
                        str(number).casefold(),
                        f"dieu {str(number).casefold()}",
                    )
                ]
                if lookup_article_numbers
                else []
            ),
            "clause_headings": (
                [
                    str(plan.clause_number).casefold(),
                    f"khoan {str(plan.clause_number).casefold()}",
                ]
                if plan.clause_number
                else []
            ),
        }
        shadow_scope = bool(
            getattr(self, "_shadow_allowed_chunk_ids", None)
        )
        cache_key: str | None = None
        exact_rows_cache = getattr(self, "_exact_rows_cache", None)
        # Benchmark runs use the same bounded exact-row cache as production,
        # even though their manifest shadow filter is enabled. This preserves
        # exact identifier semantics while preventing the candidate-only
        # staging scope from paying a repeated SQL lookup for the same law /
        # article. The flag is opt-in and never enabled by the public server.
        exact_cache_allowed = (
            exact_rows_cache is not None
            and (not shadow_scope or bool(getattr(self, "_benchmark_cache_exact_rows", False)))
        )
        if exact_cache_allowed:
            cache_key = exact_rows_cache_key(
                law_numbers=lookup_law_numbers,
                article_numbers=lookup_article_numbers,
                clause_number=plan.clause_number,
                domain=domain,
                legal_as_of=as_of.isoformat(),
                retrieval_tier=retrieval_tier,
                temporal_scope=temporal_scope,
                corpus_revision=f"{_overlay_cache_revision()}:{document_id or ''}",
            )
            cached_rows = exact_rows_cache.get(cache_key)
            if cached_rows is not None:
                return [
                    self._normalize_candidate_effective_status(dict(row))
                    for row in cached_rows
                ]
        exact_override_scope = _exact_override_scope_join(plan, domain)
        scope_join = (
            "LEFT JOIN legal_search_scope search_scope "
            "ON search_scope.document_id = d.id"
            if exact_override_scope
            else (
                "LEFT JOIN legal_search_scope search_scope "
                "ON search_scope.document_id = d.id"
                if retrieval_tier == "core" and not shadow_scope
                else "LEFT JOIN legal_search_scope search_scope ON search_scope.document_id = d.id"
            )
        )
        local_overlay_scope_clause = ""
        if retrieval_tier == "core" and not shadow_scope and not exact_override_scope:
            params["local_overlay_document_ids"] = local_overlay_document_ids
            local_overlay_scope_clause = (
                "AND (search_scope.included = TRUE OR "
                "d.id = ANY(:local_overlay_document_ids))"
            )
        expanded_scope_clause = (
            "AND COALESCE(search_scope.reason, '') <> 'other_province'"
            if retrieval_tier == "expanded"
            else ""
        )
        domain_clause = ""
        # The caller explicitly supplied an instrument number. Topic/domain
        # classification is inferred (and can be wrong even for a document-
        # only lookup), so it must not override exact legal identity. Scope,
        # effectivity, hierarchy and role gates remain in force. Bare Article
        # requests never reach this bypass because exact lookup requires a law
        # number.
        exact_identity_domain_bypass = bool(plan.law_number)
        if domain and not shadow_scope and not exact_identity_domain_bypass:
            params["domains"] = list(_domain_values(domain))
            override_laws = _reviewed_domain_override_laws(domain)
            exact_missing_domain = (
                " OR (g.group_slug IS NULL AND search_scope.domain IS NULL)"
                if is_single_exact_article_plan(plan)
                else ""
            )
            if override_laws:
                params["domain_override_laws"] = override_laws
                domain_clause = (
                    "AND (g.group_slug IN :domains OR "
                    "(g.group_slug IS NULL AND search_scope.domain IN :domains) OR "
                    "legal_normalize_identifier(d.law_number) "
                    f"IN :domain_override_laws{exact_missing_domain})"
                )
            else:
                domain_clause = (
                    "AND (g.group_slug IN :domains OR "
                    "(g.group_slug IS NULL AND search_scope.domain IN :domains)"
                    f"{exact_missing_domain})"
                )
        law_clause = ""
        if plan.law_numbers or plan.law_number:
            law_clause = (
                "AND REPLACE(legal_normalize_identifier(d.law_number), CHR(272), 'D') "
                "IN :law_numbers"
            )
        article_clause = ""
        primary_article_clause = ""
        # When several instruments and several provisions are named, keep the
        # bounded cross-product (each named document, each named article).
        # This may retrieve an extra same-numbered provision, but it guarantees
        # that an explicit comparison such as Điều 13/35 is not displaced by
        # unrelated articles before ranking.
        law_count = len(lookup_law_numbers)
        if lookup_article_numbers and law_count <= 1:
            article_clause = (
                "AND legal_normalize_text(a.article_number) IN :article_numbers"
            )
            if is_single_exact_article_plan(plan):
                # Official documents may contain annex forms whose labels also
                # start with “Điều 1”, “Điều 2”, etc.  For an explicit query
                # about an Article of the instrument, bind to the first source-
                # order parent with that number; do not merge a later form row
                # into the legal Article packet.
                primary_article_clause = (
                    "AND a.id = ("
                    "SELECT MIN(primary_article.id) "
                    "FROM legal_articles primary_article "
                    "WHERE primary_article.document_id = d.id "
                f"AND {self._article_status_predicate('primary_article')} "
                    "AND legal_normalize_text(primary_article.article_number) "
                    "IN :article_numbers)"
                )
        clause_score = "0"
        if plan.clause_number:
            clause_score = (
                "CASE WHEN legal_normalize_text(c.heading) IN :clause_headings "
                "THEN 1 ELSE 0 END"
            )
        quality_join, quality_predicate, article_title_expression = self._quality_sql(
            allow_exact_missing_domain=is_single_exact_article_plan(plan)
        )
        serving_document_clause = self._serving_document_clause(
            params,
            allowed_ids=exact_scope_ids,
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
                a.content AS article_content,
                (
                    SELECT COUNT(*)
                    FROM legal_article_chunks expected_chunk
                    WHERE expected_chunk.article_id = a.id
                ) AS exact_article_chunk_count,
                {article_title_expression} AS article_title,
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
                g.group_name AS domain_name,
                {clause_score} AS exact_clause_score
            FROM legal_article_chunks c
            JOIN legal_articles a ON a.id = c.article_id
            JOIN legal_documents d ON d.id = a.document_id
            {quality_join}
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE {self._document_status_predicate(temporal_scope=temporal_scope)}
              AND {self._article_status_predicate()}
              {serving_document_clause}
              {local_overlay_scope_clause}
              {quality_predicate}
              {law_clause}
              {article_clause}
              {primary_article_clause}
              {expanded_scope_clause}
              {domain_clause}
              AND (d.effective_date IS NULL OR d.effective_date <= :as_of)
              AND (d.expired_date IS NULL OR d.expired_date > :as_of)
              AND (a.effective_from IS NULL OR a.effective_from <= :as_of)
              AND (a.effective_to IS NULL OR a.effective_to > :as_of)
            ORDER BY c.chunk_index ASC, c.id ASC
            LIMIT :limit
            """
        )
        if article_clause:
            statement = statement.bindparams(
                bindparam("article_numbers", expanding=True)
            )
        if plan.law_numbers or plan.law_number:
            statement = statement.bindparams(
                bindparam("law_numbers", expanding=True)
            )
        if plan.clause_number:
            statement = statement.bindparams(
                bindparam("clause_headings", expanding=True)
            )
        statement = self._bind_serving_document_clause(statement)
        if local_overlay_scope_clause:
            statement = self._bind_local_overlay_scope_clause(statement)
        if domain and not shadow_scope and not exact_identity_domain_bypass:
            bindings = [bindparam("domains", expanding=True)]
            if params.get("domain_override_laws"):
                bindings.append(
                    bindparam("domain_override_laws", expanding=True)
                )
            statement = statement.bindparams(*bindings)
        with self._engine.connect() as connection:
            rows = [
                self._normalize_candidate_effective_status(
                    _apply_law_domain_override(dict(row))
                )
                for row in connection.execute(statement, params).mappings()
            ]
        if cache_key is not None and exact_cache_allowed and exact_rows_cache is not None:
            exact_rows_cache.set(cache_key, rows)
        return rows

    def _rerank_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        status_out: dict[str, Any] | None = None,
        ranking_strategy: str = "legacy_stack",
        allow_learned: bool = True,
        rerank_top_n: int = 40,
        facets: Sequence[str] = (),
        procedure_id: str | None = None,
        subject_anchor: str | None = None,
    ) -> list[dict[str, Any]]:
        if not candidates:
            if status_out is not None:
                status_out.update(
                    {
                        "mode": "heuristic",
                        "reason_code": None,
                        "version": "heuristic-v1",
                        "degraded": False,
                        "candidate_count": 0,
                        "scored_count": 0,
                        "latency_ms": 0.0,
                    }
                )
            return candidates
        ordered = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("chunk_id") or item.get("source_id") or ""),
            ),
        )
        if facets or procedure_id or subject_anchor:
            ordered = self._apply_request_context_bonus(
                query,
                ordered,
                facets=facets,
                procedure_id=procedure_id,
                subject_anchor=subject_anchor,
            )
        window = ordered[:RERANK_WINDOW]
        if ranking_strategy == "rrf_v2":
            if not allow_learned:
                if status_out is not None:
                    status_out.update(
                        {
                            "mode": "disabled",
                            "reason_code": "activation_gate_not_approved",
                            "version": "disabled",
                            "degraded": False,
                            "candidate_count": len(ordered),
                            "scored_count": 0,
                            "latency_ms": 0.0,
                        }
                    )
                return ordered
            outcome = self._learned_reranker.rerank(
                query, ordered, top_n=rerank_top_n
            )
            if status_out is not None:
                status_out.update(outcome.public_status())
            return outcome.candidates
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
        if not allow_learned:
            if status_out is not None:
                status_out.update(
                    {
                        "mode": "disabled",
                        "reason_code": "disabled_by_request",
                        "version": "disabled",
                        "degraded": False,
                        "candidate_count": len(rescored),
                        "scored_count": 0,
                        "latency_ms": 0.0,
                    }
                )
            return rescored
        outcome = self._learned_reranker.rerank(
            query, rescored, top_n=rerank_top_n
        )
        if status_out is not None:
            status_out.update(outcome.public_status())
        return outcome.candidates

    @staticmethod
    def _apply_request_context_bonus(
        query: str,
        candidates: list[dict[str, Any]],
        *,
        facets: Sequence[str],
        procedure_id: str | None,
        subject_anchor: str | None,
    ) -> list[dict[str, Any]]:
        """Rerank only from request-local identity/facet signals.

        This is deliberately additive and activates only when the caller has
        supplied the remediation contract. It never creates or replaces legal
        metadata; it only prefers a candidate whose existing metadata/content
        matches the issue anchor.
        """

        folded_subject = _fold_for_query(subject_anchor or "")
        subject_terms = {
            token for token in folded_subject.split() if len(token) > 2
        }
        facet_markers = {
            "documents": ("ho so", "giay to", "tai lieu"),
            "authority": ("tham quyen", "co quan", "noi nop", "ubnd"),
            "deadline": ("thoi han", "bao lau", "ngay lam viec"),
            "condition": ("dieu kien", "truong hop", "doi tuong"),
            "form": ("mau", "bieu mau", "to khai"),
            "legal_basis": ("dieu", "khoan", "can cu", "nghi dinh", "luat"),
            "procedure": ("thu tuc", "trinh tu", "buoc"),
            "next_action": ("tiep theo", "thuc hien", "nop"),
            "complaint": ("khieu nai", "giai quyet", "quyet dinh"),
        }
        scored: list[dict[str, Any]] = []
        for item in candidates:
            haystack = _fold_for_query(
                " ".join(
                    str(item.get(key) or "")
                    for key in (
                        "content",
                        "clean_content",
                        "chunk_heading",
                        "document_title",
                        "law_number",
                        "issuing_agency",
                        "procedure_id",
                        "procedure_code",
                    )
                )
            )
            bonus = 0.0
            reasons: list[str] = []
            if procedure_id and str(procedure_id).casefold() in haystack:
                bonus += 2.0
                reasons.append("procedure_identity")
            if subject_terms:
                overlap = len(subject_terms & set(haystack.split()))
                if overlap:
                    bonus += min(0.45, overlap * 0.08)
                    reasons.append("subject_anchor")
            for facet in facets:
                markers = facet_markers.get(str(facet), ())
                if markers and any(marker in haystack for marker in markers):
                    bonus += 0.12
                    reasons.append(f"facet:{facet}")
            scored.append(
                {
                    **item,
                    "request_context_bonus": round(bonus, 6),
                    "request_context_match_reasons": reasons,
                    "score": round(float(item.get("score") or 0.0) + bonus, 6),
                }
            )
        return sorted(
            scored,
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("chunk_id") or item.get("source_id") or ""),
            ),
        )

    def _fetch_lexical_chunks(
        self,
        query: str,
        domain: str | None,
        as_of: date,
        retrieval_tier: str,
        limit: int = LEXICAL_MATCH_LIMIT,
        exclude_chunk_ids: list[int] | None = None,
        temporal_scope: str = "current",
    ) -> list[dict[str, Any]]:
        # Natural user questions are often long, but disabling BM25 for them
        # removes the strongest procedural phrase (for example "dang ky tam
        # tru"). Condense to a bounded business query instead of scanning every
        # conversational token.
        terms = list(_lexical_query_terms(query))
        if not terms:
            return []
        exclude_chunk_ids = exclude_chunk_ids or []
        if limit <= 0:
            return []
        params: dict[str, Any] = {
            "as_of": as_of,
            "limit": min(limit, LEXICAL_MATCH_LIMIT),
        }
        clauses = []
        for index, term in enumerate(terms[:LEXICAL_TERM_LIMIT]):
            key = f"term_{index}"
            params[key] = f"%{term}%"
            clauses.append(
                f"legal_normalize_text(c.content) LIKE :{key} OR "
                f"legal_normalize_text(c.heading) LIKE :{key} OR "
                f"legal_normalize_text(a.title) LIKE :{key} OR "
                f"legal_normalize_text(d.title) LIKE :{key} OR "
                f"legal_normalize_text(d.law_number) LIKE :{key}"
            )
        if not clauses:
            return []
        exclude_clause = ""
        if exclude_chunk_ids:
            params["exclude_chunk_ids"] = exclude_chunk_ids
            exclude_clause = "AND c.id NOT IN :exclude_chunk_ids"
        scope_join = "LEFT JOIN legal_search_scope search_scope ON search_scope.document_id = d.id"
        local_overlay_scope_clause = ""
        if retrieval_tier == "core":
            local_overlay_scope_clause = (
                "AND (search_scope.included = TRUE OR "
                f"{self._local_overlay_scope_clause(params)})"
            )
        expanded_scope_clause = (
            "AND COALESCE(search_scope.reason, '') <> 'other_province'"
            if retrieval_tier == "expanded"
            else ""
        )
        domain_clause = ""
        if domain:
            serving_domain_ids = set()
            serving_domain_map = getattr(self, "_serving_domain_document_ids", None)
            if serving_domain_map:
                for value in _domain_values(domain):
                    serving_domain_ids.update(serving_domain_map.get(value, ()))
            if serving_domain_ids:
                # The manifest already resolved the reviewed domain mapping.
                # Filtering by document IDs lets PostgreSQL use the serving
                # scope index before evaluating content/similarity expressions.
                # The SQL join remains for metadata projection and the map is
                # only an acceleration, never an expansion of serving scope.
                params["domain_document_ids"] = sorted(serving_domain_ids)
                domain_clause = "AND d.id = ANY(:domain_document_ids)"
            else:
                params["domains"] = list(_domain_values(domain))
                domain_clause = (
                    "AND (g.group_slug IN :domains OR "
                    "(g.group_slug IS NULL AND search_scope.domain IN :domains))"
                )
        serving_document_clause = self._serving_document_clause(params)
        quality_join, quality_predicate, article_title_expression = self._quality_sql()
        statement = text(
            f"""
            SELECT DISTINCT
                c.id AS chunk_id,
                c.chunk_index,
                c.heading AS chunk_heading,
                c.content,
                a.id AS article_id,
                a.article_number,
                {article_title_expression} AS article_title,
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
                g.group_name AS domain_name,
                (
                    similarity(legal_normalize_text(c.content), :normalized_query)
                  + similarity(legal_normalize_text({article_title_expression}), :normalized_query)
                  + similarity(legal_normalize_text(d.title), :normalized_query)
                ) AS lexical_score
            FROM legal_article_chunks c
            JOIN legal_articles a ON a.id = c.article_id
            JOIN legal_documents d ON d.id = a.document_id
            {quality_join}
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE ({' OR '.join(clauses)})
              AND {self._document_status_predicate(temporal_scope=temporal_scope)}
              AND {self._article_status_predicate()}
              {serving_document_clause}
              {local_overlay_scope_clause}
              {quality_predicate}
              {expanded_scope_clause}
              {domain_clause}
              {exclude_clause}
            ORDER BY
                lexical_score DESC,
                c.id DESC
            LIMIT :limit
            """
        )
        if exclude_chunk_ids:
            statement = statement.bindparams(bindparam("exclude_chunk_ids", expanding=True))
        if domain:
            if "domain_document_ids" in params:
                statement = statement.bindparams(
                    bindparam("domain_document_ids", type_=ARRAY(Integer))
                )
            else:
                statement = statement.bindparams(bindparam("domains", expanding=True))
        statement = self._bind_serving_document_clause(statement)
        if retrieval_tier == "core":
            statement = self._bind_local_overlay_scope_clause(statement)
        params["normalized_query"] = " ".join(_normalized_terms(query))
        with self._engine.connect() as connection:
            rows = [
                self._normalize_candidate_effective_status(_repair_display_row(dict(row)))
                for row in connection.execute(statement, params).mappings()
            ]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not self._is_current_for_serving(
                row, as_of, temporal_scope=temporal_scope
            ):
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
        return matched_rows[: min(limit, LEXICAL_MATCH_LIMIT)]


    def document_detail(
        self,
        doc_id: str,
        article: str | None = None,
        include_content: bool = False,
        audience: str = "citizen",
    ) -> dict[str, Any]:
        """Return an approved document from the local index.

        The default response is metadata plus a lightweight article index. Article
        text is loaded only when ``article`` or ``include_content`` is requested,
        keeping the document viewer responsive for large instruments.
        """
        cleaned = str(doc_id or "").replace("legal:", "").strip()
        if not cleaned:
            raise ValueError("doc_id is required")
        self._set_request_audience(audience)
        self._require_serving_scope()
        cache_key = f"{self._request_audience()}:{cleaned}"
        canonical_state_verified = False
        # Recheck mutable SQL state before serving a cached page. Management and
        # retrieval may be different processes, so local invalidation is not enough.
        if cleaned.isdigit():
            state = DocumentServingStateStore(self._engine).read(int(cleaned))
            if state and not serving_projection(
                state, temporal_scope="current", as_of=vietnam_legal_date()
            )["allowed"]:
                raise LookupError("Document not available for current answers")
            if state:
                self.assert_document_access(int(cleaned), audience)
                canonical_state_verified = True
        
        # Use cache only for lightweight metadata/index requests.
        if canonical_state_verified and not article and not include_content:
            cached = _get_cached_document(cache_key)
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

            resolved_state = DocumentServingStateStore(self._engine).read(int(document["id"]))
            if not resolved_state or not serving_projection(
                resolved_state, temporal_scope="current", as_of=vietnam_legal_date()
            )["allowed"]:
                raise LookupError("Document not available for current answers")

            try:
                self.assert_document_access(int(document["id"]), audience)
            except PermissionError as exc:
                raise LookupError("Document not found") from exc

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
            serving_chunk_clause = ""
            allowed_chunks = self._request_serving_chunk_ids()
            if load_content and allowed_chunks is not None:
                if not allowed_chunks:
                    raise LookupError("Document not found")
                serving_chunk_clause = "AND c.id = ANY(:serving_chunk_ids)"
                params["serving_chunk_ids"] = sorted(allowed_chunks)

            article_statement = text(
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
                      {serving_chunk_clause}
                    ORDER BY CAST(NULLIF(regexp_replace(a.article_number, '\\D', '', 'g'), '') AS INTEGER) NULLS LAST,
                             a.article_number,
                             chunk_index,
                             chunk_id
                    """
                )
            if load_content and allowed_chunks is not None:
                article_statement = article_statement.bindparams(
                    bindparam("serving_chunk_ids", type_=ARRAY(Integer))
                )
            rows = [
                _repair_display_row(dict(row))
                for row in connection.execute(
                    article_statement,
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
            _set_cached_document(cache_key, result)
        
        return result

    def document_pdf(
        self, doc_id: str, article: str | None = None, audience: str = "citizen"
    ) -> tuple[bytes, str]:
        """Generate a readable, fully embedded Unicode PDF from indexed content.

        This method intentionally generates only internal extracts. The API layer
        bypasses it completely when an approved original PDF exists.
        """
        started = perf_counter()
        if audience == "citizen":
            # Keep compatibility with lightweight viewer/PDF test adapters.
            detail = self.document_detail(
                doc_id, article=article, include_content=True
            )
        else:
            detail = self.document_detail(
                doc_id, article=article, include_content=True, audience=audience
            )
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

    def _fetch_chunks(
        self,
        chunk_ids: list[int],
        retrieval_tier: str,
        *,
        exact_law_numbers: list[str] | None = None,
        allow_exact_missing_domain: bool = False,
        temporal_scope: str = "current",
    ) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        exact_law_numbers = [
            str(value).strip()
            for value in (exact_law_numbers or [])
            if str(value).strip()
        ]
        shadow_scope = bool(
            getattr(self, "_shadow_allowed_chunk_ids", None)
        )
        # Candidate-only immutable cache: return the exact rows already
        # selected by the production SQL and query PostgreSQL only for IDs that
        # were intentionally not cacheable (for example quality-filtered rows).
        # This keeps the same status/scope/quality semantics while removing
        # repeated hydration joins from the candidate latency path.
        cached_rows: list[dict[str, Any]] = []
        cache = getattr(self, "_hydration_cache_rows", None)
        if cache and retrieval_tier == "core" and not exact_law_numbers:
            cached_rows = [
                self._normalize_candidate_effective_status(dict(cache[int(chunk_id)]))
                for chunk_id in chunk_ids
                if int(chunk_id) in cache
            ]
            chunk_ids = [int(chunk_id) for chunk_id in chunk_ids if int(chunk_id) not in cache]
            if not chunk_ids:
                return cached_rows
        params: dict[str, Any] = {"chunk_ids": chunk_ids}
        scope_join = "LEFT JOIN legal_search_scope search_scope ON search_scope.document_id = d.id"
        core_scope_clause = ""
        if exact_law_numbers and retrieval_tier == "core" and not shadow_scope:
            core_scope_clause = (
                "AND (search_scope.included = TRUE OR "
                "REPLACE(legal_normalize_identifier(d.law_number), CHR(272), 'D') "
                "IN :exact_law_numbers)"
            )
        elif retrieval_tier == "core":
            core_scope_clause = (
                "AND (search_scope.included = TRUE OR "
                f"{self._local_overlay_scope_clause(params)})"
            )
        expanded_scope_clause = (
            "AND COALESCE(search_scope.reason, '') <> 'other_province'"
            if retrieval_tier == "expanded"
            else ""
        )
        quality_join, quality_predicate, article_title_expression = self._quality_sql(
            allow_exact_missing_domain=allow_exact_missing_domain
        )
        serving_document_clause = self._serving_document_clause(params)
        statement = text(
            f"""
            SELECT
                c.id AS chunk_id,
                c.chunk_index,
                c.heading AS chunk_heading,
                c.content,
                a.id AS article_id,
                a.article_number,
                {article_title_expression} AS article_title,
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
            {quality_join}
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE c.id IN :chunk_ids
              AND {self._document_status_predicate(temporal_scope=temporal_scope)}
              AND {self._article_status_predicate()}
              {serving_document_clause}
              {quality_predicate}
              {core_scope_clause}
              {expanded_scope_clause}
            """
        ).bindparams(bindparam("chunk_ids", expanding=True))
        if exact_law_numbers and retrieval_tier == "core" and not shadow_scope:
            statement = statement.bindparams(
                bindparam("exact_law_numbers", expanding=True)
            )
        statement = self._bind_serving_document_clause(statement)
        if retrieval_tier == "core" and not (exact_law_numbers and not shadow_scope):
            statement = self._bind_local_overlay_scope_clause(statement)
        if exact_law_numbers and retrieval_tier == "core" and not shadow_scope:
            params["exact_law_numbers"] = exact_law_numbers
        with self._engine.connect() as connection:
            fetched_rows = [
                self._normalize_candidate_effective_status(
                    _apply_law_domain_override(dict(row))
                )
                for row in connection.execute(
                    statement, params
                ).mappings()
            ]
        return cached_rows + fetched_rows

    def _fetch_parent_contexts(
        self,
        article_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Load selected parent bodies in one bounded post-ranking query."""

        selected_ids = sorted({int(value) for value in article_ids if value})
        if not selected_ids:
            return []
        cached_parents = getattr(self, "_hydration_cache_parents", None)
        if cached_parents:
            cached = [dict(cached_parents[item]) for item in selected_ids if item in cached_parents]
            missing = [item for item in selected_ids if item not in cached_parents]
            if not missing:
                return cached
            selected_ids = missing
        else:
            cached = []
        params: dict[str, Any] = {"article_ids": selected_ids}
        serving_document_clause = self._serving_document_clause(params)
        statement = text(
            f"""
            SELECT
                a.id AS article_id,
                a.document_id,
                a.article_number,
                a.title AS parent_heading,
                a.content AS parent_content
            FROM legal_articles a
            JOIN legal_documents d ON d.id = a.document_id
            WHERE a.id IN :article_ids
              AND {self._article_status_predicate()}
              {serving_document_clause}
            ORDER BY a.id
            """
        ).bindparams(bindparam("article_ids", expanding=True))
        statement = self._bind_serving_document_clause(statement)
        with self._engine.connect() as connection:
            fetched = [
                dict(row)
                for row in connection.execute(
                    statement,
                    params,
                ).mappings()
            ]
        return cached + fetched

    def _fetch_neighbor_chunk_ids(
        self,
        rows: list[dict[str, Any]],
        *,
        max_articles: int = 4,
        max_neighbors: int = 12,
    ) -> list[int]:
        """Return only adjacent chunks for a bounded set of retrieved articles."""

        allowed_chunks = self._request_serving_chunk_ids()
        seeds: dict[int, set[int]] = {}
        existing_ids = {
            int(row["chunk_id"])
            for row in rows
            if row.get("chunk_id") is not None
        }
        for row in rows:
            article_id = _as_int(row.get("article_id"))
            chunk_index = _as_int(row.get("chunk_index"))
            if article_id is None or chunk_index is None:
                continue
            if article_id not in seeds and len(seeds) >= max_articles:
                continue
            seeds.setdefault(article_id, set()).add(chunk_index)
        if not seeds:
            return []

        cached = getattr(self, "_hydration_cache_rows", None)
        if cached and getattr(self, "_benchmark_allow_staging", False):
            article_chunks = getattr(self, "_hydration_cache_article_chunks", None)
            if article_chunks:
                candidates = [
                    (article_id, chunk_index, chunk_id)
                    for article_id in seeds
                    for chunk_index, chunk_id in article_chunks.get(article_id, ())
                ]
            else:
                # Compatibility with a v1 cache that predates the article
                # index. New v2 caches never take this O(N) fallback.
                candidates = [
                    (
                        _as_int(row.get("article_id")),
                        _as_int(row.get("chunk_index")),
                        _as_int(row.get("chunk_id")),
                    )
                    for row in cached.values()
                    if _as_int(row.get("article_id")) in seeds
                ]
            neighbor_ids: list[int] = []
            for article_id, chunk_index, chunk_id in candidates:
                if chunk_id is None or article_id not in seeds or chunk_index is None or chunk_id in existing_ids:
                    continue
                if allowed_chunks is not None and chunk_id not in allowed_chunks:
                    continue
                if min(abs(chunk_index - seed_index) for seed_index in seeds[article_id]) != 1:
                    continue
                neighbor_ids.append(chunk_id)
                if len(neighbor_ids) >= max_neighbors:
                    return neighbor_ids
            return neighbor_ids

        statement = text(
            """
            SELECT id AS chunk_id, article_id, chunk_index
            FROM legal_article_chunks
            WHERE article_id IN :article_ids
            ORDER BY article_id, chunk_index
            """
        ).bindparams(bindparam("article_ids", expanding=True))
        with self._engine.connect() as connection:
            candidates = [
                dict(row)
                for row in connection.execute(
                    statement, {"article_ids": list(seeds)}
                ).mappings()
            ]

        neighbor_ids: list[int] = []
        for row in candidates:
            chunk_id = _as_int(row.get("chunk_id"))
            article_id = _as_int(row.get("article_id"))
            chunk_index = _as_int(row.get("chunk_index"))
            if chunk_id is None or article_id not in seeds or chunk_index is None:
                continue
            if chunk_id in existing_ids:
                continue
            if allowed_chunks is not None and chunk_id not in allowed_chunks:
                continue
            if min(
                abs(chunk_index - seed_index)
                for seed_index in seeds[article_id]
            ) != 1:
                continue
            neighbor_ids.append(chunk_id)
            if len(neighbor_ids) >= max_neighbors:
                break
        return neighbor_ids

    def _fetch_fallback_chunks(
        self,
        phrases: list[str],
        domain: str | None,
        as_of: date,
        retrieval_tier: str,
        exclude_chunk_ids: list[int] | None = None,
        temporal_scope: str = "current",
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
        params: dict[str, Any] = {}
        exclude_clause = "AND c.id NOT IN :exclude_chunk_ids" if exclude_chunk_ids else ""
        if exclude_chunk_ids:
            params["exclude_chunk_ids"] = exclude_chunk_ids
        if domain:
            domain_clause = (
                " AND (g.group_slug IN :domains OR "
                "(g.group_slug IS NULL AND search_scope.domain IN :domains))"
            )
            params["domains"] = list(_domain_values(domain))
        serving_document_clause = self._serving_document_clause(params)
        quality_join, quality_predicate, article_title_expression = self._quality_sql()
        statement = text(
            f"""
            SELECT
                c.id AS chunk_id,
                c.chunk_index,
                c.heading AS chunk_heading,
                c.content,
                a.id AS article_id,
                a.article_number,
                {article_title_expression} AS article_title,
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
            {quality_join}
            LEFT JOIN legal_fields f ON f.id = d.field_id
            LEFT JOIN legal_commune_field_groups g
              ON g.field_id = d.field_id
             AND g.included = TRUE
            {scope_join}
            WHERE {self._document_status_predicate(temporal_scope=temporal_scope)}
              AND {self._article_status_predicate()}
              {serving_document_clause}
              {exclude_clause}
              {quality_predicate}
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
        statement = self._bind_serving_document_clause(statement)
        with self._engine.connect() as connection:
            rows = [
                self._normalize_candidate_effective_status(dict(row))
                for row in connection.execute(statement, params).mappings()
            ]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not self._is_current_for_serving(
                row, as_of, temporal_scope=temporal_scope
            ):
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
            try:
                rows = connection.execute(
                    statement, {"document_ids": sorted(set(document_ids))}
                ).mappings()
            except Exception:
                return {}
            result: dict[int, list[dict[str, Any]]] = {}
            requested = set(document_ids)
            serving_documents = self._request_serving_document_ids()
            for row in rows:
                for document_id, direction in (
                    (row["source_document_id"], "outgoing"),
                    (row["target_document_id"], "incoming"),
                ):
                    if document_id not in requested:
                        continue
                    related_prefix = "target" if direction == "outgoing" else "source"
                    related_document_id = row[f"{related_prefix}_document_id"]
                    if (
                        serving_documents is not None
                        and int(related_document_id or 0) not in serving_documents
                    ):
                        continue
                    result.setdefault(int(document_id), []).append(
                        {
                            "direction": direction,
                            "relationship_type": row["relationship_type"],
                            "related_document_id": related_document_id,
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
        try:
            indexed_records = self._collection.count()
        except Exception:
            indexed_records = None
        try:
            incremental_records = (
                int(self._incremental_collection.count())
                if self._incremental_collection is not None
                else 0
            )
        except Exception:
            incremental_records = None
        incremental_configured = bool(CHROMA_INCREMENTAL_COLLECTION)
        merged_search_ready = bool(
            self._ready
            and self._vector_index_available
            and (
                not incremental_configured
                or self._incremental_collection is not None
            )
        )
        return {
            "status": "healthy",
            "ready": self._ready,
            "embedding_device": self._embedding_device.type,
            "embedding_dtype": str(self._embedding_dtype).replace("torch.", ""),
            "cuda_available": bool(torch.cuda.is_available()),
            "fallback_reason": self._embedding_fallback_reason,
            "model_fingerprint": self._model_fingerprint,
            "query_vector_cache": self._query_vector_cache.stats(),
            "exact_rows_cache": (
                self._exact_rows_cache.stats()
                if getattr(self, "_exact_rows_cache", None) is not None
                else {"status": "unavailable"}
            ),
            "collection": CHROMA_COLLECTION,
            "indexed_records": indexed_records,
            "incremental_collection": CHROMA_INCREMENTAL_COLLECTION or None,
            "incremental_indexed_records": incremental_records,
            "incremental_import_enabled": _incremental_import_enabled(),
            "legacy_direct_import_enabled": _legacy_direct_import_enabled(),
            "merged_search_ready": merged_search_ready,
            "serving_manifest": (
                self._serving_scope.public_status()
                if self._serving_scope is not None
                else {"status": "not_configured", "required": False}
            ),
            "vector_index_available": self._vector_index_available,
            "database_chunks": chunk_count,
        }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_current(
    row: dict[str, Any], as_of: date, *, allow_staging: bool = False
) -> bool:
    if str(row.get("law_number") or "").strip() in EXPIRED_DOCUMENT_OVERRIDES:
        return False
    article_status = str(row.get("article_status") or "").lower()
    if article_status != "active" and not (allow_staging and article_status == "staging"):
        return False
    document_status = str(row.get("document_status") or "").lower()
    if document_status != "active" and not (allow_staging and document_status == "staging"):
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
    relationships: list[dict[str, Any]],
    as_of: date,
    *,
    candidate_scope: Any = None,
    candidate_document_type: Any = None,
    candidate_issuing_agency: Any = None,
) -> bool:
    """Reject reviewed local instruments that still rely on an expired basis.

    The imported corpus contains legacy local decisions marked ``active`` even
    though the decrees and circulars they implement have expired. In strict
    retrieval mode these documents are unsafe to cite as current authority.

    This inference is deliberately limited to local instruments. An active
    national law or decree does not become invalid merely because one item in
    its recital later expires; national effectivity remains controlled by its
    own metadata, explicit amendment/repeal relations, and the synchronized
    validity overlay applied later in this pipeline.
    """
    has_candidate_metadata = any(
        value not in (None, "")
        for value in (
            candidate_scope,
            candidate_document_type,
            candidate_issuing_agency,
        )
    )
    if has_candidate_metadata:
        scope = " ".join(_normalized_terms(str(candidate_scope or "")))
        document_type = " ".join(
            _normalized_terms(str(candidate_document_type or ""))
        )
        issuing_agency = " ".join(
            _normalized_terms(str(candidate_issuing_agency or ""))
        )
        local_instrument = (
            any(
                marker in scope
                for marker in (
                    "hai phong",
                    "dia phuong",
                    "cap tinh",
                    "cap huyen",
                    "cap xa",
                )
            )
            or "uy ban nhan dan" in issuing_agency
            or "hoi dong nhan dan" in issuing_agency
            or (
                "quyet dinh" in document_type
                and "toan quoc" not in scope
            )
        )
        if not local_instrument:
            return False
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


def _exact_metadata_relevance(
    query: str,
    row: dict[str, Any],
) -> tuple[int, int, int, int, int]:
    """Order rows within exact documents without widening corpus search."""

    query_terms = {
        term
        for term in _normalized_terms(query)
        if len(term) >= 3 and term not in LEXICAL_STOPWORDS
    }
    title_terms = set(
        _normalized_terms(
            " ".join(
                str(row.get(key) or "")
                for key in (
                    "article_title",
                    "chunk_heading",
                    "document_title",
                )
            )
        )
    )
    content_terms = set(_normalized_terms(str(row.get("content") or "")))
    title_overlap = len(query_terms & title_terms)
    content_overlap = len(query_terms & content_terms)
    query_sequence = _normalized_terms(query)
    content_text = " ".join(_normalized_terms(str(row.get("content") or "")))
    # Adjacent phrases carry more intent than isolated common legal words.
    # This keeps an exact document lookup bounded while preferring a passage
    # containing "diện tích tối thiểu" / "xem xét" over a generic preamble.
    query_phrases = {
        " ".join(query_sequence[index : index + 2])
        for index in range(max(0, len(query_sequence) - 1))
        if any(
            len(term) >= 3 and term not in LEXICAL_STOPWORDS
            for term in query_sequence[index : index + 2]
        )
    }
    phrase_overlap = sum(phrase in content_text for phrase in query_phrases)
    normalized_query = " ".join(_normalized_terms(query))
    normalized_article = " ".join(
        _normalized_terms(str(row.get("article_number") or ""))
    )
    exact_article = int(
        bool(
            normalized_article
            and f"dieu {normalized_article}" in normalized_query
        )
    )
    # Lower chunk IDs provide a stable tie-breaker but never outrank relevance.
    chunk_id = _as_int(row.get("chunk_id")) or 0
    return (
        exact_article,
        phrase_overlap,
        title_overlap,
        content_overlap,
        -chunk_id,
    )


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
    is_marriage_registration = (
        "dang ky ket hon" in query_text
        or query_text == "ket hon"
    )
    if (
        is_civil_correction
        or is_birth_registration
        or is_death_registration
        or is_marital_status_certificate
        or is_marriage_registration
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
            if is_marriage_registration:
                foreign = (
                    "nuoc ngoai" in query_text
                    or "nguoi nuoc ngoai" in query_text
                    or "yeu to nuoc ngoai" in query_text
                )
                if foreign and article_number in {"35", "38"}:
                    return 0.55
                if not foreign and article_number in {"17", "18"}:
                    return 0.55
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


def _fold_for_query(value: str) -> str:
    return " ".join(_normalized_terms(str(value or "")))


def _request_context_hard_negative(
    *,
    query: str,
    row: Mapping[str, Any],
    facets: Sequence[str],
    subject_anchor: str | None,
) -> str | None:
    """Reject explicit cross-actor/topic contradictions from a request."""

    folded = _fold_for_query(" ".join((query, subject_anchor or "")))
    content = _fold_for_query(
        " ".join(
            str(row.get(field) or "")
            for field in (
                "content",
                "chunk_heading",
                "article_title",
                "document_title",
                "issuing_agency",
            )
        )
    )
    if (
        "chu tich ubnd phuong" in folded
        and "cong an" in content
        and ("khieu nai" in folded or "quyet dinh" in folded)
    ):
        return "wrong_actor_authority"
    pension = "tro cap huu tri xa hoi" in folded or "huu tri xa hoi" in folded
    death = any(marker in folded for marker in ("qua doi", "tu vong", "chet", "mai tang"))
    if pension and not death and "mai tang" in content:
        return "wrong_procedure_living_subject"
    residence_query = any(
        marker in folded
        for marker in ("csdl dan cu", "du lieu dan cu", "giay xac nhan cu tru", "xac nhan cu tru")
    )
    residence_metadata = _fold_for_query(
        " ".join(
            str(row.get(field) or "")
            for field in ("chunk_heading", "article_title", "document_title", "issuing_agency")
        )
    )
    residence_only_source = any(
        marker in residence_metadata for marker in ("cu tru", "tam tru", "thuong tru", "dan cu", "cong an")
    ) and not any(marker in residence_metadata for marker in ("huu tri", "an sinh", "bao tro"))
    if pension and residence_query and residence_only_source:
        if any(value in {"condition", "documents", "authority", "verification"} for value in facets):
            return "wrong_supporting_topic"
    return None


_LEXICAL_BUSINESS_PHRASES = (
    "dang ky tam tru",
    "dang ky thuong tru",
    "xoa dang ky thuong tru",
    "thong bao luu tru",
    "dang ky khai sinh",
    "dang ky khai tu",
    "xac nhan tinh trang hon nhan",
    "chung thuc ban sao",
    "cap giay chung nhan quyen su dung dat",
    "chuyen muc dich su dung dat",
    "khieu nai",
    "to cao",
)


def _lexical_query_terms(query: str) -> tuple[str, ...]:
    """Return a deterministic, bounded BM25 query for conversational input."""

    normalized = " ".join(_normalized_terms(str(query or "")))
    if not normalized:
        return ()
    phrases = [phrase for phrase in _LEXICAL_BUSINESS_PHRASES if phrase in normalized]
    tokens = [
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in LEXICAL_STOPWORDS
    ]
    # Procedure phrases carry more signal than conversational words. Remaining
    # terms are longest-first with a stable lexical tie-break.
    remainder = sorted(dict.fromkeys(tokens), key=lambda value: (-len(value), value))
    selected = list(dict.fromkeys([*phrases, *remainder]))[:LEXICAL_TERM_LIMIT]
    # Planner-generated legal excerpts can contain only structural boilerplate
    # ("ChÆ°Æ¡ng", "Äiá»u", "ngÆ°á»i", "quyáº¿t Ä‘á»‹nh"). A trigram LIKE scan for
    # those words is low-signal and expensive; ANN retrieval already covers
    # the semantic evidence. Keep lexical retrieval for named procedures,
    # identifiers and substantive terms.
    if not phrases and selected and set(selected).issubset(LEXICAL_GENERIC_TERMS):
        return ()
    return tuple(selected)


def _lexical_query_text(query: str) -> str:
    """Public/testable projection of the bounded lexical query."""

    return " ".join(_lexical_query_terms(query))[:160]


def _batch_lexical_retry_allowed(query: str) -> bool:
    """Allow the expensive lexical retry only for high-signal queries."""

    normalized = " ".join(_normalized_terms(query))
    if any(phrase in normalized for phrase in _LEXICAL_BUSINESS_PHRASES):
        return True
    # Law/article identifiers are deterministic lexical anchors even when the
    # surrounding conversational text is incomplete.
    return bool(re.search(r"\b\d{1,4}/\d{4}/(?:qh\d+|nd[- ]cp|tt[- ]\w+|q[đd]-ubnd)\b", normalized))


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


def _retrieval_candidate_trace_item(
    item: Mapping[str, Any], *, rank: int, default_source: str | None = None
) -> dict[str, Any]:
    """Project one content-free stage candidate for the M3 audit trail."""

    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}

    def value(name: str) -> Any:
        return item.get(name) if item.get(name) is not None else metadata.get(name)

    sources = item.get("retrieval_sources") or []
    source = str(item.get("retrieval_source") or default_source or "")
    if not sources and source:
        sources = [source]
    projected = {
        "rank": rank,
        "chunk_id": _as_int(value("chunk_id")),
        "document_id": _as_int(value("document_id")),
        "article_id": _as_int(value("article_id")),
        "law_number": value("law_number"),
        "article_number": value("article_number"),
        "domain": value("domain_slug") or value("domain"),
        "retrieval_source": source or None,
        "retrieval_sources": list(sources),
        "vector_score": value("vector_score"),
        "lexical_score": value("lexical_score"),
        "rrf_score": value("rrf_score"),
        "bm25_score": value("bm25_score"),
        "score": value("score"),
        "parent_context_ref": value("parent_context_ref"),
    }
    return projected


def _retrieval_stage_trace_items(
    items: Sequence[Mapping[str, Any]], *, default_source: str | None = None
) -> list[dict[str, Any]]:
    return [
        _retrieval_candidate_trace_item(
            item, rank=index + 1, default_source=default_source
        )
        for index, item in enumerate(items)
    ]


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
        "authority_level": item.get("authority_level"),
        "authority_label": item.get("authority_label"),
        "authority_rank": item.get("authority_rank"),
        "authority_confidence": item.get("authority_confidence"),
        "authority_reason_code": item.get("authority_reason_code"),
        "authority_warning_code": item.get("authority_warning_code"),
        "hierarchy_position": item.get("hierarchy_position"),
        "hierarchy_rule": item.get("hierarchy_rule"),
        "parent_context_ref": item.get("parent_context_ref"),
        "parent_context_kind": item.get("parent_context_kind"),
        "parent_context_chars": item.get("parent_context_chars"),
        "parent_context_original_chars": item.get(
            "parent_context_original_chars"
        ),
        "parent_context_truncated": item.get("parent_context_truncated"),
        "parent_context_reason": item.get("parent_context_reason"),
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
        "authority_level": item.get("authority_level"),
        "authority_label": item.get("authority_label"),
    }


app = FastAPI(
    title="VNLegal-LAL Hai Phong Retrieval Service",
    version="1.0.0",
)
MANAGEMENT_ONLY = str(os.getenv("LEGAL_MANAGEMENT_ONLY") or "").strip().casefold() in {
    "1",
    "true",
    "yes",
    "on",
}


def configure_standalone_service_role(port: int) -> bool:
    """Choose a safe role when this module is launched as a script.

    Port 8765 is the metadata-management process and must ask the live
    Retrieval V2 process on 8766 for vector truth.  Historically, launching
    ``python scripts/legal_search_server.py`` left MANAGEMENT_ONLY false,
    causing restore/history/replacement checks to inspect unloaded local
    collections and fail even though the live vectors were complete.
    An explicit LEGAL_MANAGEMENT_ONLY value still takes precedence.
    """

    global MANAGEMENT_ONLY
    configured = str(os.getenv("LEGAL_MANAGEMENT_ONLY") or "").strip()
    if configured:
        MANAGEMENT_ONLY = configured.casefold() in {"1", "true", "yes", "on"}
    else:
        MANAGEMENT_ONLY = int(port) == 8765
        os.environ["LEGAL_MANAGEMENT_ONLY"] = "true" if MANAGEMENT_ONLY else "false"
    return MANAGEMENT_ONLY


class _LazyLegalRetriever:
    """Delay database and vector initialization until the first real request.

    Importing this module is required by document-export helpers, route discovery,
    health tooling, and unit tests.  Constructing ``LegalRetriever`` at import time
    opened the production SQL database and initialized vector state even when none
    of those callers needed retrieval.  The proxy preserves the existing global
    interface while moving persistent I/O to the first attribute access.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_instance", None)
        object.__setattr__(self, "_lock", Lock())

    def _get(self) -> LegalRetriever:
        instance = object.__getattribute__(self, "_instance")
        if instance is not None:
            return instance
        lock = object.__getattribute__(self, "_lock")
        with lock:
            instance = object.__getattribute__(self, "_instance")
            if instance is None:
                instance = LegalRetriever()
                object.__setattr__(self, "_instance", instance)
        return instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_instance", "_lock"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._get(), name, value)

    def __delattr__(self, name: str) -> None:
        """Delegate attribute cleanup to the materialized retriever.

        ``unittest.mock.patch`` restores a patched dotted attribute with
        ``delattr`` when the target did not expose a proxy-level attribute.
        Without forwarding that operation, tests (and other temporary runtime
        instrumentation) fail even though the underlying ``LegalRetriever``
        was correctly initialized.
        """

        if name in {"_instance", "_lock"}:
            object.__delattr__(self, name)
            return
        delattr(self._get(), name)


retriever = _LazyLegalRetriever()

_pdf_artifact_lock = Lock()
_pdf_generation_events: dict[str, Event] = {}


def _pdf_artifact_path(doc_id: str, article: str | None) -> Path:
    safe_doc_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(doc_id)).strip("-") or "document"
    safe_article = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(article or "all")).strip("-") or "all"
    return LEGAL_PDF_ARTIFACT_DIR / f"{safe_doc_id}-{safe_article}.pdf"


def _cached_pdf_artifact(
    doc_id: str, article: str | None, audience: str = "citizen"
) -> tuple[Path, bool]:
    """Return an internal export artifact and whether it was cached."""
    # Authorize every request before returning an existing cache artifact.
    retriever.document_detail(doc_id, audience=audience)
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
        payload, _filename = retriever.document_pdf(
            doc_id, article=article, audience=audience
        )
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
_SEARCH_CACHE_TTL_SECONDS = float(os.getenv("LEGAL_SEARCH_CACHE_TTL_SECONDS", "600"))
_search_result_cache: dict[
    tuple[str, ...],
    tuple[float, dict[str, Any]],
] = {}
_search_result_cache_lock = Lock()


def _cached_batch_key(
    issue: BatchSearchIssue,
    as_of: date,
    tier: str,
    *,
    include_trace: bool,
    query_text: str | None = None,
    ranking_strategy: str = "legacy_stack",
    enable_learned_reranker: bool = True,
    audience: str = "citizen",
    as_of_explicit: bool = False,
    fusion_strategy: str = "legacy_stack",
    vector_weight: float = 0.6,
    lexical_weight: float = 0.4,
    rerank_top_n: int = 40,
    enable_parent_expansion: bool = True,
    enable_neighbor_expansion: bool = True,
    organization_unit_id: str | None = None,
    organization_scope_revision: str = "",
    temporal_scope: str = "current",
) -> tuple[str, ...]:
    normalized = unicodedata.normalize(
        "NFKC", str(query_text if query_text is not None else issue.query or "")
    ).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    query_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    # Cache entries are collection-scoped.  This prevents a candidate or
    # shadow benchmark result from being reused after a collection swap, and
    # also protects an in-process rollback rehearsal from cross-contaminating
    # the baseline run.  The service object is available at call time even
    # though this helper is defined before its module-level initialization.
    service = globals().get("retriever")
    collection = getattr(getattr(service, "_collection", None), "name", None)
    collection_namespace = str(collection or os.getenv("LEGAL_CHROMA_COLLECTION", ""))
    manifest_namespace = str(
        getattr(getattr(service, "_serving_scope", None), "manifest_sha256", "")
    )
    return (
        query_digest,
        issue.domain or "",
        # Ranking hints are request-local inputs.  They must participate in
        # the cache namespace; otherwise a plain semantic hit can be reused
        # for a later procedure/facet query with the same text but a different
        # legal plan.
        hashlib.sha256(
            json.dumps(
                {
                    "facets": list(issue.facets or []),
                    "subject_anchor": issue.subject_anchor or "",
                    "procedure_id": issue.procedure_id or "",
                    "document_id": issue.document_id,
                    # A whole-document request carries a deterministic
                    # multi-level outline in addition to the ordinary ranked
                    # rows.  Never reuse a cached plain lookup for that shape
                    # (or leak the larger outline into a later plain lookup).
                    "full_document": bool(issue.full_document),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        as_of.isoformat(),
        "as_of-explicit" if as_of_explicit else "as_of-default",
        tier,
        "trace" if include_trace else "public",
        ranking_strategy,
        fusion_strategy,
        f"{vector_weight:.6f}:{lexical_weight:.6f}",
        "learned" if enable_learned_reranker else "no-learned",
        f"rerank-top-{rerank_top_n}",
        "parent-on" if enable_parent_expansion else "parent-off",
        "neighbor-on" if enable_neighbor_expansion else "neighbor-off",
        collection_namespace,
        str(audience or "citizen"),
        str(organization_unit_id or ""),
        str(organization_scope_revision or ""),
        manifest_namespace,
        str(temporal_scope or "current"),
        _overlay_cache_revision(),
    )


def _get_cached_search_result(
    key: tuple[str, ...],
) -> dict[str, Any] | None:
    import time
    now = time.time()
    with _search_result_cache_lock:
        cached = _search_result_cache.get(key)
        if not cached:
            return None
        created_at, result = cached
        if now - created_at > _SEARCH_CACHE_TTL_SECONDS or not result.get("results"):
            _search_result_cache.pop(key, None)
            return None
        return copy.deepcopy(result)


def _revalidate_cached_search_result(
    result: Mapping[str, Any],
    *,
    temporal_scope: str,
    as_of: date,
    organization_document_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Recheck mutable SQL lifecycle state before serving cached candidates."""

    output = copy.deepcopy(dict(result))
    rows = [dict(row) for row in output.get("results") or []]
    identities = sorted(
        {
            int(row["document_id"])
            for row in rows
            if str(row.get("document_id") or "").isdigit()
        }
    )
    states = DocumentServingStateStore(retriever._engine).read_many(identities)
    kept: list[dict[str, Any]] = []
    for row in rows:
        identity = str(row.get("document_id") or "")
        if organization_document_ids is not None and (
            not identity.isdigit()
            or int(identity) not in organization_document_ids
        ):
            continue
        if not identity.isdigit():
            # Synthetic/unit adapters and non-document sources have no SQL
            # lifecycle identity. Preserve their original pipeline decision;
            # the API source binder still validates them before generation.
            kept.append(row)
            continue
        decision = serving_projection(
            states.get(identity), temporal_scope=temporal_scope, as_of=as_of
        )
        if decision["allowed"]:
            row["serving_state_check"] = decision
            kept.append(row)
    output["results"] = kept
    trace = output.get("trace")
    if isinstance(trace, dict):
        trace["cache_serving_state_recheck"] = {
            "checked": len(rows),
            "kept": len(kept),
            "temporal_scope": temporal_scope,
            "authority": "postgresql_document_and_scope",
        }
        if organization_document_ids is not None:
            trace["cache_organization_scope_recheck"] = {
                "allowed_document_count": len(organization_document_ids),
                "kept": len(kept),
                "authority": "legal_document_organization_assignment",
            }
    return output


def _set_cached_search_result(
    key: tuple[str, ...],
    result: dict[str, Any],
) -> None:
    if not result.get("results"):
        return
    import time
    with _search_result_cache_lock:
        _search_result_cache[key] = (time.time(), copy.deepcopy(result))

def _invalidate_document_cache(doc_id: str) -> None:
    """Remove a document from cache when it changes."""
    for key in list(_document_cache):
        if key == str(doc_id) or key.endswith(":" + str(doc_id)):
            _document_cache.pop(key, None)
            _document_cache_ttl.pop(key, None)

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



from api.vector_coverage import CoverageSnapshots

_coverage_snapshots = CoverageSnapshots()


@app.get("/management/vector-coverage")
def serving_vector_coverage() -> dict[str, Any]:
    if MANAGEMENT_ONLY:
        raise HTTPException(503, "Coverage must be read from the retrieval process")
    retriever._load()
    serving = load_active_serving_release()
    inventory = retriever._management_serving_inventory(serving, vietnam_legal_date())
    with retriever._engine.connect() as connection:
        ids = connection.execute(text("SELECT c.id FROM legal_article_chunks c JOIN legal_articles a ON a.id=c.article_id WHERE a.document_id=ANY(:ids)").bindparams(bindparam("ids", type_=ARRAY(Integer))), {"ids": inventory["ids"] or [-1]}).scalars().all()
    return _coverage_snapshots.read(
        [retriever._collection, retriever._temporal_collection, retriever._incremental_collection],
        ids, serving.manifest_sha256)


@app.get("/management/documents/{doc_id}/vector-membership")
def serving_document_vector_membership(doc_id: str) -> dict[str, Any]:
    """Return an exact, read-only vector receipt from the Q&A process.

    The management-only process uses this receipt to authorize lifecycle
    transitions and incremental cleanup.  Keep the endpoint on the retrieval
    process so a successful response always describes the collections that
    actually serve chatbot queries.
    """

    if MANAGEMENT_ONLY:
        raise HTTPException(
            status_code=503,
            detail="Vector membership must be read from the retrieval process",
        )
    try:
        projection = retriever._document_vector_membership(doc_id)
        return {"document_id": str(int(str(doc_id).strip())), **projection}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="document_vector_membership_unavailable",
        ) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        if MANAGEMENT_ONLY:
            with retriever._engine.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
            return {
                "status": "healthy",
                "ready": True,
                "mode": "management-only",
                "management_only": True,
                "embedding_device": "not_loaded",
                "embedding_dtype": "not_loaded",
                "collection": CHROMA_COLLECTION,
                "indexed_records": None,
                "serving_manifest": (
                    retriever._serving_scope.public_status()
                    if retriever._serving_scope is not None
                    else None
                ),
                "vector_index_available": False,
            }
        return retriever.health()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    try:
        if MANAGEMENT_ONLY:
            raise HTTPException(
                status_code=503,
                detail="Management-only service does not serve user Q&A.",
            )
        return retriever.search(request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "single_search_failed audience=%s domain=%s",
            request.audience,
            request.domain,
        )
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


@app.post("/search/batch")
def search_batch(request: BatchSearchRequest) -> dict[str, Any]:
    """Retrieve up to eight issue-bound packets and sixteen query variants."""

    if MANAGEMENT_ONLY:
        raise HTTPException(status_code=503, detail="Management-only service does not serve user Q&A.")

    from api.legal_exact_retrieval import plan_exact_lookup
    from api.legal_retrieval_quality import diversify_ranked_candidates

    started = perf_counter()
    retriever._set_request_audience(request.audience)
    organization_scope = retriever._set_request_organization_scope(
        request.audience,
        request.organization_unit_id,
    )
    organization_scope_revision = (
        hashlib.sha256(
            json.dumps(
                sorted(organization_scope[0]),
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if organization_scope is not None
        else ""
    )
    retriever._require_serving_scope()
    try:
        query_tasks = [
            (issue, query)
            for issue in request.issues
            for query in issue.expanded_queries()
        ]
        batch_as_of_explicit = (
            request.as_of_explicit
            if request.as_of_explicit is not None
            else "as_of" in request.model_fields_set
        )
        query_classifications = {
            index: classify_legal_query(
                query.query,
                requested_domain=issue.domain,
                requested_scope=query.scope,
                as_of=request.as_of,
                as_of_explicit=bool(batch_as_of_explicit),
                today=(
                    request.benchmark_today
                    if os.getenv("LEGAL_BENCHMARK_MODE") == "1"
                    else None
                ),
            )
            for index, (issue, query) in enumerate(query_tasks)
        }
        candidate_count = (
            EXPANDED_BATCH_CANDIDATE_COUNT
            if request.retrieval_tier == "expanded"
            else CORE_BATCH_CANDIDATE_COUNT
        )
        # The Ask batch path already pays one ANN query per planned issue. The
        # old path then repeated a 3k-document lexical SQL scan for every
        # issue, which dominated p95 and exhausted the small PostgreSQL pool.
        # In adaptive mode use bounded vector-first retrieval for the batch and
        # retry lexical only when the vector packet is empty. The direct
        # ``/search`` endpoint remains hybrid, so BM25 is still available for
        # explicit single-query retrieval and the bounded retry is auditable.
        vector_first_batch = BATCH_LEXICAL_MODE == "adaptive"
        batch_lexical_candidate_count = (
            0 if vector_first_batch else (40 if request.retrieval_tier == "expanded" else 60)
        )
        cached_by_index: dict[int, dict[str, Any]] = {}
        cache_keys_by_index: dict[
            int, tuple[str, str, str, str, str]
        ] = {}
        missing_indexes: list[int] = []
        for index, (issue, query) in enumerate(query_tasks):
            cache_key = _cached_batch_key(
                issue,
                request.as_of,
                request.retrieval_tier,
                include_trace=request.include_trace,
                query_text=query.query,
                ranking_strategy=request.ranking_strategy,
                fusion_strategy=request.fusion_strategy,
                vector_weight=request.vector_weight,
                lexical_weight=request.lexical_weight,
                enable_learned_reranker=request.enable_learned_reranker,
                rerank_top_n=request.rerank_top_n,
                enable_parent_expansion=request.enable_parent_expansion,
                enable_neighbor_expansion=request.enable_neighbor_expansion,
                audience=request.audience,
                organization_unit_id=request.organization_unit_id,
                organization_scope_revision=organization_scope_revision,
                as_of_explicit=bool(batch_as_of_explicit),
                temporal_scope=str(
                    query_classifications[index].get("temporal_scope") or "current"
                ),
            )
            cache_keys_by_index[index] = cache_key
            cached = _get_cached_search_result(cache_key)
            if cached is None:
                missing_indexes.append(index)
            else:
                cached_by_index[index] = _revalidate_cached_search_result(
                    cached,
                    temporal_scope=str(
                        query_classifications[index].get("temporal_scope") or "current"
                    ),
                    as_of=date.fromisoformat(
                        str(query_classifications[index]["retrieval_as_of"])
                    ),
                    organization_document_ids=(
                        set(organization_scope[0])
                        if organization_scope is not None
                        else None
                    ),
                )

        vector_prefetch_started = perf_counter()
        prefetched_by_index: dict[int, dict[str, Any]] = {}
        vector_telemetry: dict[str, Any] = {}

        def exact_index_required(query: str) -> bool:
            """Skip ANN only when the query carries an unambiguous identity.

            A bare ``Điều 1`` is not unique across the corpus and should still
            use semantic retrieval. A law number, law/article pair, reviewed
            procedure ID or form code, on the other hand, has a deterministic
            SQL/index path and must not pay for an embedding.
            """

            exact = plan_exact_lookup(query)
            return bool(
                exact.law_number
                or exact.law_numbers
                or exact.article_law_pairs
                or exact.procedure_id
                or exact.form_codes
            )

        vector_indexes = [
            index
            for index in missing_indexes
            if query_classifications[index]["retrieval_allowed"]
            # An unambiguous legal identity is served by the exact SQL/index
            # path. Do not encode a query that already names a law, a
            # law/article pair, procedure or form; a bare article number is
            # still ambiguous and remains eligible for semantic retrieval.
            if not exact_index_required(query_tasks[index][1].query)
        ]
        if vector_indexes:
            prefetched = retriever.prefetch_batch_vectors(
                [query_tasks[index][1].query for index in vector_indexes],
                retrieval_tier=request.retrieval_tier,
                candidate_count=candidate_count,
            )
            prefetched_by_index = dict(zip(vector_indexes, prefetched))
            vector_telemetry = dict(getattr(retriever, "_last_vector_telemetry", {}) or {})
        vector_prefetch_ms = (
            perf_counter() - vector_prefetch_started
        ) * 1000

        def retrieve_query(
            indexed_task: tuple[int, tuple[BatchSearchIssue, BatchSearchQuery]],
        ) -> tuple[BatchSearchIssue, BatchSearchQuery, dict[str, Any]]:
            index, task = indexed_task
            issue, query = task
            classification = query_classifications[index]
            if not classification["retrieval_allowed"]:
                timing = {
                    "query_understanding": 0.0,
                    "exact_lookup": 0.0,
                    "embedding": 0.0,
                    "ann_search": 0.0,
                    "lexical_sql": 0.0,
                    "hydrate_chunks": 0.0,
                    "neighbors": 0.0,
                    "relationships": 0.0,
                    "rank_filter": 0.0,
                    "validity_overlay": 0.0,
                    "parent_hydration": 0.0,
                    "hydrate_filter_rank": 0.0,
                    "total": 0.0,
                }
                blocked: dict[str, Any] = {
                    "status": "clarification_required",
                    "error_code": classification["temporal_error_code"],
                    "query": query.query,
                    "as_of": None,
                    "query_classification": classification,
                    "results": [],
                    "total_count": 0,
                    "timing_ms": timing,
                }
                if request.include_trace:
                    blocked["trace"] = {
                        "schema_version": M3_TRACE_SCHEMA_VERSION,
                        "raw_query": query.query,
                        "normalized_query": classification["normalized_query"],
                        "query_classification": classification,
                        "vector_candidates": [],
                        "lexical_candidates": [],
                        "fusion_candidates": [],
                        "final_evidence": [],
                        "stage_latency_ms": timing,
                    }
                return issue, query, blocked
            search_request = SearchRequest(
                query=query.query,
                limit=BATCH_RESULT_LIMIT,
                candidate_count=candidate_count,
                lexical_candidate_count=batch_lexical_candidate_count,
                as_of=request.as_of,
                as_of_explicit=bool(batch_as_of_explicit),
                temporal_scope=str(
                    query_classifications[index].get("temporal_scope") or "current"
                ),
                document_id=issue.document_id,
                full_document=issue.full_document,
                benchmark_today=request.benchmark_today,
                domain=issue.domain,
                retrieval_tier=request.retrieval_tier,
                include_trace=request.include_trace,
                request_id=request.request_id,
                issue_id=issue.issue_id,
                query_id=query.query_id,
                issue_domain=issue.domain,
                facets=list(issue.facets),
                subject_anchor=issue.subject_anchor,
                procedure_id=issue.procedure_id,
                allow_broad_fallback=False,
                ranking_strategy=request.ranking_strategy,
                fusion_strategy=request.fusion_strategy,
                vector_weight=request.vector_weight,
                lexical_weight=request.lexical_weight,
                enable_learned_reranker=request.enable_learned_reranker,
                rerank_top_n=request.rerank_top_n,
                enable_parent_expansion=request.enable_parent_expansion,
                enable_neighbor_expansion=request.enable_neighbor_expansion,
                audience=request.audience,
                organization_unit_id=request.organization_unit_id,
                organization_scope_document_ids=(
                    set(organization_scope[0]) if organization_scope is not None else None
                ),
                organization_scope_chunk_ids=(
                    set(organization_scope[1]) if organization_scope is not None else None
                ),
            )
            result = cached_by_index.get(index)
            if result is None:
                _batch_vector_context.raw = prefetched_by_index.get(index)
                try:
                    result = retriever.search(search_request)
                finally:
                    if hasattr(_batch_vector_context, "raw"):
                        delattr(_batch_vector_context, "raw")
                if (
                    vector_first_batch
                    and not (result.get("results") or [])
                    and _batch_lexical_retry_allowed(query.query)
                ):
                    # A lexical retry is bounded to a small packet and is only
                    # paid by a vector miss. It is intentionally not a broad
                    # fallback for low scores, because that would recreate the
                    # p95 explosion this mode is designed to prevent.
                    search_request.lexical_candidate_count = (
                        20 if request.retrieval_tier == "core" else 12
                    )
                    _batch_vector_context.raw = prefetched_by_index.get(index)
                    try:
                        result = retriever.search(search_request)
                    finally:
                        if hasattr(_batch_vector_context, "raw"):
                            delattr(_batch_vector_context, "raw")
                _set_cached_search_result(cache_keys_by_index[index], result)
            else:
                for row in result.get("results") or []:
                    row["request_id"] = request.request_id
                    row["issue_id"] = issue.issue_id
                    row["query_id"] = query.query_id
                trace = result.get("trace")
                if isinstance(trace, dict):
                    trace["request_id"] = request.request_id
                    trace["issue_id"] = issue.issue_id
                    trace["query_id"] = query.query_id
            return issue, query, result

        # Candidate retrieval is bounded per issue and the Chroma query itself
        # is protected by the retriever lock. Running the independent SQL
        # hydration/ranking work in a pool prevents a multi-issue question
        # from multiplying latency linearly. The default matches the local
        # engine's three pooled connections plus two overflow slots; operators
        # can lower it for constrained deployments without changing ranking.
        try:
            batch_workers = max(
                1,
                min(8, int(os.getenv("LEGAL_BATCH_MAX_WORKERS", "5"))),
            )
        except ValueError:
            batch_workers = 5
        with ThreadPoolExecutor(
            max_workers=min(batch_workers, max(1, len(query_tasks))),
            thread_name_prefix="legal-batch",
        ) as pool:
            retrieved = list(pool.map(retrieve_query, enumerate(query_tasks)))

        issue_payloads: list[dict[str, Any]] = []
        per_issue_results: list[list[dict[str, Any]]] = []
        batch_validity_filtered_reasons: dict[str, int] = {}
        batch_validity_filtered_count = 0
        batch_validity_warning_count = 0
        retrieved_by_issue: dict[
            str, list[tuple[BatchSearchQuery, dict[str, Any]]]
        ] = {issue.issue_id: [] for issue in request.issues}
        for issue, query, result in retrieved:
            retrieved_by_issue[issue.issue_id].append((query, result))

        for issue in request.issues:
            merged_rows: list[dict[str, Any]] = []
            row_indexes: dict[str, int] = {}
            query_payloads: list[dict[str, Any]] = []
            exact_article_packets: list[dict[str, Any]] = []
            exact_document_outlines: list[dict[str, Any]] = []
            seen_packet_refs: set[str] = set()
            seen_document_outlines: set[str] = set()
            timing_total = 0.0
            issue_validity_filtered_reasons: dict[str, int] = {}
            issue_validity_filtered_count = 0
            issue_validity_warning_count = 0
            issue_validity_mode = "protect"
            for query, result in retrieved_by_issue[issue.issue_id]:
                query_rows = [dict(row) for row in result.get("results") or []]
                for row in query_rows:
                    row["query_id"] = query.query_id
                    identity = str(
                        row.get("chunk_id")
                        or row.get("canonical_chunk_id")
                        or row.get("source_id")
                        or ""
                    )
                    if identity and identity in row_indexes:
                        existing = merged_rows[row_indexes[identity]]
                        query_ids = list(existing.get("query_ids") or [existing.get("query_id")])
                        if query.query_id not in query_ids:
                            query_ids.append(query.query_id)
                        existing["query_ids"] = [item for item in query_ids if item]
                        continue
                    if identity:
                        row_indexes[identity] = len(merged_rows)
                    row["query_ids"] = [query.query_id]
                    merged_rows.append(row)
                timing = result.get("timing_ms") or {}
                timing_total += float(timing.get("total") or 0.0)
                validity = result.get("validity_sync") or {}
                if isinstance(validity, Mapping):
                    issue_validity_mode = str(
                        validity.get("mode") or issue_validity_mode
                    )
                    issue_validity_filtered_count += int(
                        validity.get("filtered_count") or 0
                    )
                    issue_validity_warning_count += int(
                        validity.get("warning_count") or 0
                    )
                    for reason, count in (
                        validity.get("filtered_reasons") or {}
                    ).items():
                        key = str(reason or "unknown")
                        issue_validity_filtered_reasons[key] = (
                            issue_validity_filtered_reasons.get(key, 0)
                            + int(count or 0)
                        )
                packet = result.get("exact_article_packet")
                if isinstance(packet, Mapping):
                    packet_ref = str(packet.get("packet_ref") or "")
                    packet_identity = packet_ref or repr(
                        (
                            packet.get("law_number"),
                            packet.get("article_number"),
                            packet.get("status"),
                        )
                    )
                    if packet_identity not in seen_packet_refs:
                        seen_packet_refs.add(packet_identity)
                        exact_article_packets.append(dict(packet))
                for outline in result.get("exact_document_outlines") or []:
                    if not isinstance(outline, Mapping):
                        continue
                    outline_identity = str(
                        outline.get("document_id") or outline.get("law_number") or ""
                    )
                    if outline_identity and outline_identity not in seen_document_outlines:
                        seen_document_outlines.add(outline_identity)
                        exact_document_outlines.append(dict(outline))
                query_payloads.append(
                    {
                        **query.model_dump(exclude_none=True),
                        "result_count": len(query_rows),
                        "status": result.get("status") or "ok",
                        "error_code": result.get("error_code"),
                        "as_of": result.get("as_of"),
                        "query_classification": result.get(
                            "query_classification"
                        ),
                        "timing_ms": timing,
                        **(
                            {"exact_article_packet": dict(packet)}
                            if isinstance(packet, Mapping)
                            else {}
                        ),
                    }
                )
            rows = merged_rows
            per_issue_results.append(rows)
            batch_validity_filtered_count += issue_validity_filtered_count
            batch_validity_warning_count += issue_validity_warning_count
            for reason, count in issue_validity_filtered_reasons.items():
                batch_validity_filtered_reasons[reason] = (
                    batch_validity_filtered_reasons.get(reason, 0) + count
                )
            issue_payloads.append(
                {
                    "issue_id": issue.issue_id,
                    "query": issue.query or issue.expanded_queries()[0].query,
                    "queries": query_payloads,
                    "domain": issue.domain,
                    "intent": issue.intent,
                    "facets": list(issue.facets),
                    "subject_anchor": issue.subject_anchor,
                    "procedure_id": issue.procedure_id,
                    "results": rows,
                    "exact_article_packets": exact_article_packets,
                    "exact_document_outlines": exact_document_outlines,
                    "validity_sync": {
                        "mode": issue_validity_mode,
                        "filtered_count": issue_validity_filtered_count,
                        "filtered_reasons": issue_validity_filtered_reasons,
                        "warning_count": issue_validity_warning_count,
                    },
                    "timing_ms": {"query_total": round(timing_total, 1)},
                    **(
                        {
                            "trace": [
                                result.get("trace")
                                for _, result in retrieved_by_issue[issue.issue_id]
                                if result.get("trace")
                            ]
                        }
                        if request.include_trace
                        else {}
                    ),
                }
            )

        # Round-robin before diversity selection ensures one issue cannot
        # consume the entire multi-issue context merely because it ran first.
        interleaved: list[dict[str, Any]] = []
        max_rows = max((len(rows) for rows in per_issue_results), default=0)
        for rank in range(max_rows):
            for rows in per_issue_results:
                if rank < len(rows):
                    item = dict(rows[rank])
                    item["batch_rank"] = rank
                    item["score"] = float(item.get("score") or 0) - rank * 0.000001
                    interleaved.append(item)
        context_results = diversify_ranked_candidates(
            interleaved,
            limit=12,
            max_per_article=2,
            max_per_document=3,
        )
        version_records = [
            dict(result.get("versions") or {})
            for _issue, _query, result in retrieved
            if isinstance(result.get("versions"), Mapping)
        ]
        effective_as_of_values = sorted(
            {
                str(result.get("as_of"))
                for _issue, _query, result in retrieved
                if result.get("as_of")
            }
        )
        return {
            "request_id": request.request_id,
            "as_of": request.as_of.isoformat(),
            "effective_as_of": (
                effective_as_of_values[0]
                if len(effective_as_of_values) == 1
                else None
            ),
            "effective_as_of_values": effective_as_of_values,
            "issues": issue_payloads,
            "context_results": context_results,
            "retrieval_decision": {
                "ranking_strategy": request.ranking_strategy,
                "fusion_strategy": request.fusion_strategy,
                "vector_weight": request.vector_weight,
                "lexical_weight": request.lexical_weight,
                "learned_reranker_enabled": request.enable_learned_reranker,
                "query_decision": request.query_decision,
            },
            "versions": version_records[0] if version_records else {},
            "validity_sync": {
                "mode": "protect",
                "filtered_count": batch_validity_filtered_count,
                "filtered_reasons": batch_validity_filtered_reasons,
                "warning_count": batch_validity_warning_count,
            },
            "timing_ms": {
                "total": round((perf_counter() - started) * 1000, 1),
                "vector_prefetch": round(vector_prefetch_ms, 1),
                "embedding_ms": float(vector_telemetry.get("embedding_ms") or 0.0),
                "ann_ms": float(vector_telemetry.get("ann_ms") or 0.0),
            },
            "batch_telemetry": {
                "batch_size": len(query_tasks),
                "embedding_batch_size": int(vector_telemetry.get("batch_size") or 0),
                "cache_hits": len(cached_by_index),
                "cache_misses": len(missing_indexes),
                "lexical_mode": "vector_first" if vector_first_batch else "hybrid",
                "lexical_candidate_count": batch_lexical_candidate_count,
                "candidate_count": candidate_count,
            },
        }
    except Exception as exc:
        logger.exception(
            "batch_search_failed request_id=%s audience=%s issues=%s query_variants=%s",
            request.request_id,
            request.audience,
            len(request.issues),
            sum(len(issue.expanded_queries()) for issue in request.issues),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/management/summary")
def management_summary(
    as_of: date = Query(default_factory=vietnam_legal_date),
) -> dict[str, Any]:
    try:
        return retriever.management_summary(as_of=as_of)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "management_sql_unavailable",
                "message": "Không thể đọc tổng quan kho văn bản.",
            },
        ) from exc


class ManagementDocumentsQuery(BaseModel):
    """Internal inventory query used when the API has resolved cross-store scope."""

    q: str = Field(default="", max_length=500)
    document_type: str | None = Field(default=None, max_length=255)
    issuing_agency: str | None = Field(default=None, max_length=255)
    scope: str | None = Field(default=None, max_length=120)
    domain: str | None = Field(default=None, max_length=120)
    stored_status: str | None = Field(default=None, max_length=80)
    validity_status: Literal[
        "active", "expiring_30", "not_yet_effective", "expired", "unknown"
    ] | None = None
    tier: Literal["all", "core", "expanded"] = "all"
    data_quality: Literal[
        "missing_source", "missing_metadata", "zero_chunks", "unknown_status", "unclassified"
    ] | None = None
    source_presence: Literal["all", "present", "missing"] = "all"
    issued_from: date | None = None
    issued_to: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    expired_from: date | None = None
    expired_to: date | None = None
    include_expired_history: bool = False
    as_of: date = Field(default_factory=vietnam_legal_date)
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_by: Literal[
        "effective_date",
        "issued_date",
        "expired_date",
        "title",
        "law_number",
        "stored_status",
        "article_count",
        "chunk_count",
    ] = "effective_date"
    sort_order: Literal["asc", "desc"] = "desc"
    include_document_ids: list[int] | None = Field(default=None, max_length=100_000)
    exclude_document_ids: list[int] | None = Field(default=None, max_length=100_000)


class ManagementDocumentOrganizationAssignment(BaseModel):
    """Content-free department projection maintained by the API authority."""

    assignment_state: Literal["assigned", "shared", "unassigned"]
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
    organization_unit_ids: list[str] = Field(default_factory=list, max_length=50)
    assignment_source: str = Field(default="admin", min_length=2, max_length=120)
    confirmation_status: str = Field(default="confirmed", min_length=2, max_length=80)


class ManagementDocumentMetadataUpdate(BaseModel):
    issued_date: date | None = None
    effective_date: date | None = None
    expired_date: date | None = None
    source_url: str | None = Field(default=None, max_length=4000)
    gazette_date: date | None = None
    signer_title: str | None = Field(default=None, max_length=500)
    signer_name: str | None = Field(default=None, max_length=500)
    applicability_info: str | None = Field(default=None, max_length=4000)
    version: str | None = Field(default=None, max_length=255)
    expected_revision: str = Field(pattern="^[0-9a-f]{64}$")


@app.get("/management/documents/{doc_id}/organization-assignment")
def management_document_organization_identity(doc_id: str) -> dict[str, Any]:
    try:
        return retriever.document_organization_identity(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_document_id"}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "document_not_found"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "management_sql_unavailable"}) from exc


@app.post("/management/documents/{doc_id}/organization-assignment")
def management_document_organization_assignment(
    doc_id: str,
    request: ManagementDocumentOrganizationAssignment,
) -> dict[str, Any]:
    try:
        return retriever.update_document_organization_assignment(
            doc_id,
            assignment_state=request.assignment_state,
            primary_organization_unit_id=request.primary_organization_unit_id,
            organization_unit_ids=request.organization_unit_ids,
            assignment_source=request.assignment_source,
            confirmation_status=request.confirmation_status,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "legal_document_not_found"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "organization_assignment_projection_unavailable",
                "message": "Chưa thể cập nhật phòng ban quản lý văn bản.",
            },
        ) from exc


def _management_documents_from_query(request: ManagementDocumentsQuery) -> dict[str, Any]:
    return retriever.management_documents(
        query=request.q,
        document_type=request.document_type,
        issuing_agency=request.issuing_agency,
        scope=request.scope,
        domain=request.domain,
        stored_status=request.stored_status,
        validity_status=request.validity_status,
        tier=request.tier,
        data_quality=request.data_quality,
        source_presence=request.source_presence,
        issued_from=request.issued_from,
        issued_to=request.issued_to,
        effective_from=request.effective_from,
        effective_to=request.effective_to,
        expired_from=request.expired_from,
        expired_to=request.expired_to,
        include_expired_history=request.include_expired_history,
        as_of=request.as_of,
        limit=request.limit,
        offset=request.offset,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        include_document_ids=request.include_document_ids,
        exclude_document_ids=request.exclude_document_ids,
    )


@app.post("/management/documents/query")
def management_documents_query(request: ManagementDocumentsQuery) -> dict[str, Any]:
    """Query the SQL inventory after the API resolves normalized unit relations."""

    try:
        return _management_documents_from_query(request)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "management_sql_unavailable",
                "message": "Không thể đọc danh sách kho văn bản.",
            },
        ) from exc


@app.get("/management/documents")
def management_documents(
    q: str = Query(default="", max_length=500),
    document_type: str | None = Query(default=None, max_length=255),
    issuing_agency: str | None = Query(default=None, max_length=255),
    scope: str | None = Query(default=None, max_length=120),
    domain: str | None = Query(default=None, max_length=120),
    stored_status: str | None = Query(default=None, max_length=80),
    validity_status: str | None = Query(
        default=None,
        pattern="^(active|expiring_30|not_yet_effective|expired|unknown)$",
    ),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    data_quality: str | None = Query(
        default=None,
        pattern="^(missing_source|missing_metadata|zero_chunks|unknown_status|unclassified)$",
    ),
    source_presence: str = Query(default="all", pattern="^(all|present|missing)$"),
    issued_from: date | None = None,
    issued_to: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expired_from: date | None = None,
    expired_to: date | None = None,
    include_expired_history: bool = Query(default=False),
    as_of: date = Query(default_factory=vietnam_legal_date),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|expired_date|title|law_number|stored_status|article_count|chunk_count)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    try:
        return retriever.management_documents(
            query=q,
            document_type=document_type,
            issuing_agency=issuing_agency,
            scope=scope,
            domain=domain,
            stored_status=stored_status,
            validity_status=validity_status,
            tier=tier,
            data_quality=data_quality,
            source_presence=source_presence,
            issued_from=issued_from,
            issued_to=issued_to,
            effective_from=effective_from,
            effective_to=effective_to,
            expired_from=expired_from,
            expired_to=expired_to,
            include_expired_history=include_expired_history,
            as_of=as_of,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "management_sql_unavailable",
                "message": "Không thể đọc danh sách kho văn bản.",
            },
        ) from exc


@app.get("/management/documents/export.xlsx")
def export_management_documents_xlsx(
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    validity_status: str | None = Query(
        default=None,
        pattern="^(active|expiring_30|not_yet_effective|expired|unknown)$",
    ),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: date = Query(default_factory=vietnam_legal_date),
    issued_from: date | None = None,
    issued_to: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expired_from: date | None = None,
    expired_to: date | None = None,
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|expired_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Response:
    """Export the exact canonical inventory subset shown by the public list."""

    common = {
        "query": q,
        "domain": domain,
        "validity_status": validity_status,
        "tier": tier,
        "issued_from": issued_from,
        "issued_to": issued_to,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "expired_from": expired_from,
        "expired_to": expired_to,
        "include_expired_history": True,
        "as_of": as_of,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }
    items: list[dict[str, Any]] = []
    offset = 0
    total = 0
    while True:
        result = retriever.management_documents(
            **common,
            limit=100,
            offset=offset,
        )
        total = int(result.get("total") or 0)
        if total > DOCUMENT_EXPORT_MAX_ROWS:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "document_export_too_large",
                    "message": "Có hơn 10.000 văn bản. Hãy thu hẹp bộ lọc trước khi xuất.",
                },
            )
        page_items = [item for item in result.get("items") or [] if isinstance(item, dict)]
        items.extend(page_items)
        offset += len(page_items)
        if not page_items or offset >= total:
            break

    payload = _document_export_workbook(items)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    status_suffix = validity_status or "tat-ca"
    return Response(
        content=payload,
        media_type=DOCUMENT_EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="van-ban-{status_suffix}-{timestamp}.xlsx"'
            ),
            "X-Record-Count": str(total),
        },
    )


@app.get("/management/documents/{doc_id}")
def management_document(doc_id: str) -> dict[str, Any]:
    try:
        return retriever.management_document_detail(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_document_id", "message": "Mã văn bản không hợp lệ."},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "document_not_found", "message": "Không tìm thấy văn bản."},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "management_sql_unavailable",
                "message": "Không thể đọc chi tiết kho văn bản.",
            },
        ) from exc


@app.get("/documents")
def list_documents(
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: date = Query(default_factory=date.today),
    issued_from: date | None = None,
    issued_to: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expired_from: date | None = None,
    expired_to: date | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    audience: str = Query(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    ),
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
            audience=audience,
            issued_from=issued_from,
            issued_to=issued_to,
            effective_from=effective_from,
            effective_to=effective_to,
            expired_from=expired_from,
            expired_to=expired_to,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_document_date_range",
                "message": "Ngày bắt đầu không được sau ngày kết thúc.",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/documents/export.xlsx")
def export_documents_xlsx(
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: date = Query(default_factory=date.today),
    issued_from: date | None = None,
    issued_to: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expired_from: date | None = None,
    expired_to: date | None = None,
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    audience: str = Query(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    ),
) -> Response:
    """Export every document matching the same manifest-bound browse filter."""

    try:
        result = retriever.export_documents(
            query=q,
            domain=domain,
            tier=tier,
            as_of=as_of,
            sort_by=sort_by,
            sort_order=sort_order,
            audience=audience,
            issued_from=issued_from,
            issued_to=issued_to,
            effective_from=effective_from,
            effective_to=effective_to,
            expired_from=expired_from,
            expired_to=expired_to,
        )
        payload = _document_export_workbook(result["items"])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_document_date_range",
                "message": "Ngày bắt đầu không được sau ngày kết thúc.",
            },
        ) from exc
    except OverflowError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "document_export_too_large",
                "message": "Có hơn 10.000 văn bản. Hãy thu hẹp bộ lọc trước khi xuất.",
            },
        ) from exc
    except RuntimeError as exc:
        if str(exc) == "document_export_dependency_unavailable":
            raise HTTPException(
                status_code=503,
                detail="Chưa cài thành phần xuất Excel.",
            ) from exc
        raise

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=payload,
        media_type=DOCUMENT_EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="danh-sach-van-ban-{timestamp}.xlsx"'
            ),
            "X-Record-Count": str(result["total"]),
        },
    )


@app.get("/documents/lookup")
def lookup_document(
    law_number: str,
    audience: str = Query(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    ),
) -> dict[str, Any]:
    """Resolve by law number before dynamic ``/documents/{doc_id}`` routes."""

    cleaned = str(law_number or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="law_number is required")
    try:
        retriever._set_request_audience(audience)
        retriever._require_serving_scope()
        from api.legal_document_serving_state import current_document_lookup_clause
        params: dict[str, Any] = {"law_number": cleaned, "lookup_as_of": vietnam_legal_date()}
        serving_clause = retriever._serving_document_clause(params)
        with retriever._engine.connect() as connection:
            row = connection.execute(
                retriever._bind_serving_document_clause(text(
                    f"""
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
                    FROM legal_documents d
                    WHERE lower(trim(d.law_number)) = lower(trim(:law_number))
                      {serving_clause}
                      {current_document_lookup_clause()}
                    ORDER BY d.id DESC
                    LIMIT 1
                    """
                ), parameter="serving_document_ids"),
                params,
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


class DocumentLookupBatchRequest(BaseModel):
    law_numbers: list[str] = Field(min_length=1, max_length=200)


class ServingDocumentLookupBatchRequest(BaseModel):
    """Exact identities to validate before evidence reaches a model.

    ``document_ids`` is authoritative. ``law_numbers`` only supports legacy
    retrieval rows that predate stable document IDs.
    """

    document_ids: list[str] = Field(default_factory=list, max_length=200)
    law_numbers: list[str] = Field(default_factory=list, max_length=200)


@app.post("/management/duplicates/check")
def check_document_duplicates(request: DocumentLookupBatchRequest) -> dict[str, Any]:
    """Internal admin preflight; excluded/historical records still exist."""
    try:
        with retriever._engine.connect() as connection:
            rows = _duplicate_document_rows(connection, request.law_numbers)
        return {
            "documents": [{key: _iso_or_none(value) if isinstance(value, (date, datetime)) else value
                           for key, value in row.items()} for row in rows[:1000]],
            "complete": len(rows) <= 1000,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail="duplicate_check_unavailable") from exc


@app.post("/documents/lookup-batch")
def lookup_documents_batch(
    request: ServingDocumentLookupBatchRequest,
    audience: str = Query(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    ),
    temporal_scope: str = Query(default="current", pattern="^(current|historical)$"),
    as_of: date | None = Query(default=None),
) -> dict[str, Any]:
    """Validate exact serving identities without relabelling retrieved text."""

    cleaned = list(dict.fromkeys(
        value.strip().casefold()
        for value in request.law_numbers
        if str(value or "").strip()
    ))
    cleaned_ids: list[int] = []
    for value in request.document_ids:
        raw = str(value or "").strip()
        for prefix in ("legal_document:", "legal_documents:", "legal:"):
            if raw.casefold().startswith(prefix):
                raw = raw[len(prefix):]
                break
        if raw.isdigit() and int(raw) > 0:
            cleaned_ids.append(int(raw))
    cleaned_ids = list(dict.fromkeys(cleaned_ids))
    if not cleaned and not cleaned_ids:
        return {"documents": []}
    try:
        retriever._set_request_audience(audience)
        retriever._require_serving_scope()
        from api.legal_document_serving_state import (
            DocumentServingStateStore,
            current_document_lookup_clause,
            serving_projection,
        )
        lookup_as_of = as_of or vietnam_legal_date()
        params: dict[str, Any] = {"lookup_as_of": lookup_as_of}
        serving_clause = retriever._serving_document_clause(params)
        current_clause = (
            current_document_lookup_clause()
            if temporal_scope == "current"
            else ""
        )
        identity_parts: list[str] = []
        if cleaned_ids:
            params["document_ids"] = cleaned_ids
            identity_parts.append("d.id IN :document_ids")
        if cleaned:
            params["law_numbers"] = cleaned
            identity_parts.append("lower(trim(d.law_number)) IN :law_numbers")
        statement = text(
            f"""
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
            FROM legal_documents d
            WHERE ({' OR '.join(identity_parts)})
              {serving_clause}
              {current_clause}
            ORDER BY id DESC
            """
        )
        if cleaned_ids:
            statement = statement.bindparams(bindparam("document_ids", expanding=True))
        if cleaned:
            statement = statement.bindparams(bindparam("law_numbers", expanding=True))
        statement = retriever._bind_serving_document_clause(
            statement,
            parameter="serving_document_ids",
        )
        with retriever._engine.connect() as connection:
            rows = connection.execute(statement, params).mappings().all()
        states = DocumentServingStateStore(retriever._engine).read_many(
            [row["id"] for row in rows]
        )
        eligible_rows = [
            row
            for row in rows
            if serving_projection(
                states.get(str(row["id"])),
                temporal_scope=temporal_scope,
                as_of=lookup_as_of,
            )["allowed"]
        ]
        return {
            "documents": [
                {
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
                for row in eligible_rows
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



class VectorCleanupRequest(BaseModel):
    requested_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=10, max_length=2000)


class LegalDocumentSearchStateRequest(BaseModel):
    action: str = Field(pattern="^(exclude|restore|historical|quarantine)$")
    requested_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=10, max_length=2000)
    expected_revision: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    verify_vectors: bool = False


class LegalDocumentHardDeleteRequest(BaseModel):
    requested_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=10, max_length=2000)
    expected_revision: str = Field(pattern="^[0-9a-f]{64}$")
    confirmation_text: str = Field(min_length=1, max_length=255)


@app.get("/documents/{doc_id}/vector-cleanup/preview")
def preview_document_vector_cleanup(
    doc_id: str,
    x_legal_operations_token: str | None = Header(
        default=None, alias="X-Legal-Operations-Token"
    ),
) -> dict[str, Any]:
    configured = os.getenv("LEGAL_VECTOR_CLEANUP_TOKEN", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="vector_cleanup_not_configured")
    if not x_legal_operations_token or not secrets.compare_digest(
        x_legal_operations_token, configured
    ):
        raise HTTPException(status_code=403, detail="vector_cleanup_forbidden")
    try:
        return retriever.preview_vector_cleanup(doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="vector_cleanup_preview_failed"
        ) from exc


@app.post("/management/documents/{doc_id}/metadata")
def management_document_metadata_update(
    doc_id: str,
    request: ManagementDocumentMetadataUpdate,
) -> dict[str, Any]:
    try:
        values = request.model_dump(exclude={"expected_revision"})
        values.update(
            {
                key: (value.strip() or None) if isinstance(value, str) else value
                for key, value in values.items()
            }
        )
        return retriever.update_document_metadata(
            doc_id,
            metadata=values,
            expected_revision=request.expected_revision,
        )
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if code == "document_metadata_changed" else 422
        raise HTTPException(status_code=status_code, detail={"code": code}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "legal_document_not_found"}) from exc
    except Exception as exc:
        logger.exception("Failed to update management metadata for document %s", doc_id)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "document_metadata_update_unavailable",
                "message": "Chưa thể cập nhật đồng bộ metadata và kho tra cứu.",
            },
        ) from exc


@app.post("/documents/{doc_id}/vector-cleanup")
def cleanup_document_vectors(
    doc_id: str,
    request: VectorCleanupRequest,
    x_legal_operations_token: str | None = Header(
        default=None, alias="X-Legal-Operations-Token"
    ),
) -> dict[str, Any]:
    configured = os.getenv("LEGAL_VECTOR_CLEANUP_TOKEN", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="vector_cleanup_not_configured")
    if not x_legal_operations_token or not secrets.compare_digest(
        x_legal_operations_token, configured
    ):
        raise HTTPException(status_code=403, detail="vector_cleanup_forbidden")
    try:
        return retriever.cleanup_document_vectors(
            doc_id,
            requested_by=request.requested_by,
            reason=request.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="vector_cleanup_failed") from exc


def _draft_base_fingerprint(doc_id: int) -> str:
    with retriever._engine.connect() as connection:
        document = connection.execute(text("SELECT * FROM legal_documents WHERE id=:id"), {"id":doc_id}).mappings().first()
        if not document:
            raise ValueError("lifecycle_base_document_missing")
        chunks = connection.execute(text("SELECT c.id,c.content FROM legal_article_chunks c JOIN legal_articles a ON a.id=c.article_id WHERE a.document_id=:id ORDER BY c.id"), {"id":doc_id}).mappings().all()
    state = DocumentServingStateStore(retriever._engine).read(doc_id)
    payload = {"document":dict(document), "chunks":[dict(row) for row in chunks], "serving":state_revision(state)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@app.get("/lifecycle/documents/{doc_id}/revision")
def draft_base_revision(doc_id: int):
    if doc_id < 1:
        raise HTTPException(status_code=400, detail="invalid_document_id")
    try:
        return {"fingerprint": _draft_base_fingerprint(doc_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class DraftActivationRequest(BaseModel):
    version_key: str = Field(pattern="^[0-9a-f]{64}$")
    base_fingerprint: str | None = None
    document: LegalImportRequest


@app.post("/lifecycle/activate")
def activate_reviewed_draft(request: DraftActivationRequest) -> dict[str, Any]:
    from api.legal_activation_journal import run_activation
    document = request.document.model_copy(update={"activation_version_key": request.version_key})
    old_id = document.replacement_of_document_id

    def activate():
        if old_id:
            old = DocumentServingStateStore(retriever._engine).read(old_id)
            if not request.base_fingerprint or _draft_base_fingerprint(old_id) != request.base_fingerprint:
                raise ValueError("lifecycle_base_fingerprint_conflict")
            result = retriever.replace_document(str(old_id), document, requested_by="approved-draft", reason="Approved replacement version " + request.version_key)
        else:
            result = retriever.import_document(document)
        if old_id:
            return {**result, "sql_verified": True, "collections_verified": [str(result["vector_collection"])]}
        try:
            return verify(result)
        except Exception:
            excluded = retriever.set_document_search_state(str(result["document_id"]), action="exclude", requested_by="approved-draft",
                reason="activation_pending_verification:" + request.version_key)
            from api.legal_activation_journal import record_activation_recovery_state
            record_activation_recovery_state(retriever._engine, request.version_key,
                int(result['document_id']), excluded['state_revision'])
            raise

    def verify(result):
        doc_id = int(result["document_id"])
        membership = retriever._document_vector_membership(str(doc_id))
        if not membership.get("current_retrieval_ready"):
            raise RuntimeError("activation_vectors_not_ready")
        public_smoke = retriever._verify_replacement_retrieval(doc_id, document)
        required_checks_passed = bool(
            public_smoke.get("exact_match")
            and str(public_smoke.get("document_id") or "") == str(doc_id)
            and membership.get("current_retrieval_ready")
        )
        smoke = {
            **public_smoke,
            "status": (
                "passed"
                if required_checks_passed and public_smoke.get("semantic_match")
                else "passed_with_semantic_warning"
                if required_checks_passed
                else "failed"
            ),
            "passed": required_checks_passed,
            "required_checks_passed": required_checks_passed,
            "semantic_probe_passed": bool(public_smoke.get("semantic_match")),
            "vector_membership": membership,
        }
        if not required_checks_passed:
            raise RuntimeError("activation_retrieval_not_verified")
        return {**result, "sql_verified": True, "collections_verified": [str(result["vector_collection"])], "retrieval_smoke": smoke}

    def recover(doc_id):
        with retriever._engine.begin() as conn:
            row = conn.execute(text("SELECT id,status FROM legal_documents WHERE id=:id"), {"id":doc_id}).mappings().first()
            if not row:
                conn.execute(text("UPDATE legal_activation_operation SET document_id=NULL WHERE version_key=:key"), {"key":request.version_key})
        if not row:
            return activate()
        # Only the staging row owned by this journal may be rebuilt after a crash.
        if row["status"] == "staging":
            with retriever._engine.connect() as conn:
                ids = conn.execute(text("SELECT c.id FROM legal_article_chunks c JOIN legal_articles a ON a.id=c.article_id WHERE a.document_id=:id"), {"id":doc_id}).scalars().all()
            if ids and retriever._incremental_collection is None:
                raise RuntimeError("activation_cleanup_vectors_unavailable")
            if ids:
                retriever._incremental_collection.delete(ids=[f"chunk-{value}" for value in ids])
            with retriever._engine.begin() as conn:
                conn.execute(text("DELETE FROM legal_documents WHERE id=:id AND status='staging'"), {"id":doc_id})
                conn.execute(text("UPDATE legal_activation_operation SET document_id=NULL WHERE version_key=:key"), {"key":request.version_key})
            return activate()
        if row["status"] != "active":
            state = DocumentServingStateStore(retriever._engine).read(doc_id)
            from api.legal_activation_journal import activation_recovery_revision
            retry_revision = activation_recovery_revision(retriever._engine, request.version_key, doc_id)
            if retry_revision and retry_revision == state_revision(state):
                retriever.set_document_search_state(str(doc_id), action="restore", requested_by="approved-draft-recovery",
                    reason="Retry verified activation " + request.version_key, expected_revision=retry_revision)
            else:
                raise RuntimeError("activation_document_state_requires_review")
        old_transition = None
        old_receipt = None
        if old_id:
            old = DocumentServingStateStore(retriever._engine).read(old_id)
            desired_old_state = transition_verification(
                old,
                action=document.replacement_action,
                as_of=vietnam_legal_date(),
            )
            if not desired_old_state.get("passed"):
                if _draft_base_fingerprint(old_id) != request.base_fingerprint:
                    raise ValueError("lifecycle_base_fingerprint_conflict")
                membership = retriever._document_vector_membership(str(doc_id))
                if not membership.get("current_retrieval_ready"):
                    raise RuntimeError("activation_recovery_precheck_failed")
                old_transition = retriever.set_document_search_state(str(old_id), action=document.replacement_action,
                    requested_by="approved-draft-recovery", reason="Resume approved replacement " + request.version_key,
                    expected_revision=state_revision(old), required_active_document_id=doc_id)
                old_receipt = old_transition
            else:
                old_receipt = {
                    "document_id": str(old_id),
                    "stored_status": str(old.get("status") or ""),
                    "search_included": bool(old.get("search_included")),
                    "state_revision": state_revision(old),
                    "verification": desired_old_state,
                    "idempotent_replay": True,
                }
        with retriever._engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM legal_article_chunks c JOIN legal_articles a ON a.id=c.article_id WHERE a.document_id=:id"), {"id":doc_id}).scalar_one()
        try:
            recovered = verify({"document_id":doc_id,"chunk_count":count,"vector_collection":CHROMA_INCREMENTAL_COLLECTION,
                                "activation_status":"active","status":"recovered"})
            if old_id:
                if not old_receipt:
                    raise RuntimeError("activation_recovery_old_state_unverified")
                recovered.update(status='replaced', chatbot_ready=True,
                    old_document=old_receipt,
                    new_document={'document_id': str(doc_id), 'search_included': True})
            return recovered
        except Exception:
            from api.legal_activation_compensation import compensate_failed_verification
            compensation = compensate_failed_verification(
                retriever.set_document_search_state, document_id=doc_id,
                version_key=request.version_key, old_document_id=old_id,
                old_transition=old_transition,
            )
            logger.warning("Activation recovery compensation: %s", compensation)
            excluded = compensation.get('new_exclude') or {}
            if excluded.get('state_revision'):
                from api.legal_activation_journal import record_activation_recovery_state
                record_activation_recovery_state(retriever._engine, request.version_key,
                    doc_id, excluded['state_revision'])
            raise
    try:
        retriever._load()
        return run_activation(retriever._engine, request.version_key, request.model_dump(mode="json"), activate, recover)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReplacementActivationError as exc:
        # The replacement saga has already restored the old document and
        # excluded the unverified new one. Persist that exact revision in the
        # activation journal so a retry with the same version key can safely
        # restore and re-verify the same physical row instead of importing a
        # duplicate. Return only the bounded compensation receipt.
        compensation = exc.detail.get("compensation") or {}
        revision = compensation.get("state_revision")
        new_document_id = exc.detail.get("new_document_id")
        if revision and str(new_document_id or "").isdigit():
            try:
                from api.legal_activation_journal import record_activation_recovery_state
                record_activation_recovery_state(
                    retriever._engine,
                    request.version_key,
                    int(new_document_id),
                    str(revision),
                )
            except Exception:
                logger.exception("Could not persist replacement recovery revision")
        logger.exception("Draft replacement activation failed")
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except Exception as exc:
        logger.exception("Draft activation failed")
        raise HTTPException(status_code=503, detail="draft_activation_pending_retry") from exc


@app.post("/documents/{doc_id}/replace")
def replace_legal_document(
    doc_id: str,
    request: LegalImportRequest,
) -> dict[str, Any]:
    """Import and activate a reviewed replacement, then exclude the old row."""

    try:
        return retriever.replace_document(
            doc_id,
            request,
            requested_by="admin-management",
            reason="reviewed legal-document replacement",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ReplacementActivationError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="document_replacement_failed") from exc


@app.post("/documents/{doc_id}/search-state")
def set_legal_document_search_state(
    doc_id: str,
    request: LegalDocumentSearchStateRequest,
) -> dict[str, Any]:
    """Exclude/restore a document from Q&A without deleting stored evidence."""

    try:
        return retriever.set_document_search_state(
            doc_id,
            action=request.action,
            requested_by=request.requested_by,
            reason=request.reason,
            expected_revision=request.expected_revision,
            verify_vectors=request.verify_vectors,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="document_search_state_failed") from exc


@app.get("/documents/{doc_id}/hard-delete/preview")
def preview_legal_document_hard_delete(doc_id: str) -> dict[str, Any]:
    try:
        return retriever.preview_hard_delete(doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="document_hard_delete_preview_failed") from exc


@app.post("/documents/{doc_id}/hard-delete")
def hard_delete_legal_document(
    doc_id: str,
    request: LegalDocumentHardDeleteRequest,
) -> dict[str, Any]:
    try:
        return retriever.hard_delete_document(
            doc_id,
            requested_by=request.requested_by,
            reason=request.reason,
            expected_revision=request.expected_revision,
            confirmation_text=request.confirmation_text,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="document_hard_delete_failed") from exc


@app.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    response: Response,
    article: str | None = None,
    include_content: bool = False,
    audience: str = Query(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    ),
) -> dict[str, Any]:
    started = perf_counter()
    try:
        payload = {"document": retriever.document_detail(
            doc_id, article=article, include_content=include_content,
            audience=audience,
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
async def download_document_pdf(
    doc_id: str,
    article: str | None = None,
    audience: str = Query(
        default="citizen", pattern="^(citizen|officer|admin|system)$"
    ),
) -> Response:
    """Serve a cached extract without blocking the async request loop."""
    started = perf_counter()
    try:
        from fastapi.responses import FileResponse

        artifact, cache_hit = await asyncio.to_thread(
            _cached_pdf_artifact, doc_id, article, audience
        )
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


@app.post("/import/preview")
def preview_import(request: LegalImportRequest, for_review: bool = False) -> dict[str, Any]:
    try:
        return retriever.preview_import(request, for_review=for_review)
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
    host = os.getenv("LEGAL_SEARCH_HOST", "127.0.0.1")
    configure_standalone_service_role(port)
    uvicorn.run(app, host=host, port=port)
