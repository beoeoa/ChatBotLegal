"""Manifest-bound Retrieval Release V2 shadow service.

This process may receive canary reads, but it never mutates Chroma or changes
the M2 active pointer. All release artifacts are loaded through one
checksum-bound Manifest V3.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Mapping

from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()
load_dotenv(".env.v2-production")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_answer_router import route_legal_answer
from api.legal_admin_overlay_snapshot import (
    default_overlay_snapshot_path,
    load_overlay_snapshot,
)
from api.legal_domains import canonicalize_legal_domain
from api.legal_document_serving_state import (
    DocumentServingStateStore,
    document_id as canonical_document_id,
    serving_projection,
    state_revision,
)
from api.legal_exact_article import (
    attach_exact_article_packet,
    build_exact_article_serving_packet,
    public_exact_article_packet,
)
from api.legal_exact_retrieval import (
    normalize_exact_identifier,
    plan_exact_lookup,
)
from api.legal_learned_reranker import OptionalCrossEncoderReranker
from api.legal_query_understanding import classify_legal_query
from api.legal_retrieval_quality import normalize_vietnamese_search_text
from api.legal_structural_chunking import parse_structural_path
from api.retrieval_release_v2_runtime import (
    V2RuntimeError,
    V2ServingRuntime,
    _derive_issue_subqueries,
    normalize_exact,
)
from api.retrieval_candidate_r26 import R26_CANDIDATE_PROFILE
from api.retrieval_candidate_r27 import R27_CANDIDATE_PROFILE
from api.retrieval_candidate_r28 import R28_CANDIDATE_PROFILE
from api.retrieval_release_contracts import file_sha256
from api.retrieval_vnext_shadow import (
    RERANK_WINDOW as VNEXT_RERANK_WINDOW,
    apply_vnext_to_search_output,
    vnext_may_run_on_collection,
    vnext_shadow_enabled,
)
from api.retrieval_serving_manifest_v3 import (
    V3ServingManifest,
    V3ServingManifestError,
    load_v3_serving_manifest,
)


BASELINE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
DEFAULT_PORT = 8766
FROZEN_M5_PROFILE = "v6r26"
SERVER_MODE = str(os.getenv("LEGAL_RETRIEVAL_V2_MODE") or "v2-shadow").strip().casefold()
IS_PRODUCTION = SERVER_MODE == "v2-production"
PRODUCTION_PROFILE = str(
    os.getenv("LEGAL_RETRIEVAL_PRODUCTION_PROFILE") or "r26"
).strip().casefold()
FROZEN_PRODUCTION_SETTINGS = {
    "LEGAL_RETRIEVAL_V2_SELECTED_M5": FROZEN_M5_PROFILE,
    "LEGAL_RETRIEVAL_VECTOR_TOP_K": "80",
    "LEGAL_RETRIEVAL_LEXICAL_TOP_K": "80",
    "LEGAL_RETRIEVAL_VECTOR_WEIGHT": "0.7",
    "LEGAL_RETRIEVAL_LEXICAL_WEIGHT": "0.3",
    "LEGAL_RETRIEVAL_V2_VECTOR_BACKEND": "stage_c_exact",
    "LEGAL_RETRIEVAL_V2_EXACT_SOURCE": "verified_chroma",
    "LEGAL_RETRIEVAL_V2_EXACT_DEVICE": "cuda",
    "LEGAL_RETRIEVAL_V2_CANDIDATE_PROFILE": R26_CANDIDATE_PROFILE,
    # Benchmark prewarmed only its 436 known query terms. Prewarming the full
    # production FTS vocabulary is not equivalent and exhausts a 16 GB host.
    "LEGAL_RETRIEVAL_V2_PREWARM_FTS_FREQUENCY": "false",
    # Full law-cache prewarm consumed >10 GB on the 16 GB local production
    # host. Keep it lazy; this changes execution latency only, never ranking.
    "LEGAL_RETRIEVAL_V2_PREWARM_LAW_CACHE": "false",
}
R28_PRODUCTION_PROFILE = "r28-reconciled-v2"
R28_PRODUCTION_SETTINGS = {
    "LEGAL_RETRIEVAL_V2_SELECTED_M5": FROZEN_M5_PROFILE,
    "LEGAL_RETRIEVAL_VECTOR_TOP_K": "80",
    "LEGAL_RETRIEVAL_LEXICAL_TOP_K": "80",
    "LEGAL_RETRIEVAL_VECTOR_WEIGHT": "0.7",
    "LEGAL_RETRIEVAL_LEXICAL_WEIGHT": "0.3",
    "LEGAL_RETRIEVAL_V2_VECTOR_BACKEND": "stage_c_exact",
    "LEGAL_RETRIEVAL_V2_EXACT_SOURCE": "offline_mmap",
    "LEGAL_RETRIEVAL_V2_EXACT_DEVICE": "cpu",
    "LEGAL_RETRIEVAL_V2_EXACT_QUERY_ONLY": "false",
    "LEGAL_RETRIEVAL_EXACT_ARTICLE_FASTPATH": "true",
    "LEGAL_RETRIEVAL_R28_PHRASE_FTS": "true",
    "LEGAL_RETRIEVAL_V2_CANDIDATE_PROFILE": R28_CANDIDATE_PROFILE,
    "LEGAL_RETRIEVAL_V2_PREWARM_FTS_FREQUENCY": "true",
    # R28's checksum-bound offline mmap is authoritative for every vector
    # query. Chroma ANN is not part of this portable release topology.
    "LEGAL_RETRIEVAL_V2_REQUIRE_CHROMA_COLLECTIONS": "false",
    "LEGAL_RETRIEVAL_VERIFY_COLLECTION_COUNTS": "false",
    "LEGAL_RETRIEVAL_V2_PREWARM_ANN": "false",
    "LEGAL_RETRIEVAL_V2_PREWARM_LAW_CACHE": "false",
}
if IS_PRODUCTION and PRODUCTION_PROFILE not in {"r26", R28_PRODUCTION_PROFILE}:
    raise V2RuntimeError(f"unsupported_production_profile:{PRODUCTION_PROFILE}")
ACTIVE_PRODUCTION_SETTINGS = (
    R28_PRODUCTION_SETTINGS
    if PRODUCTION_PROFILE == R28_PRODUCTION_PROFILE
    else FROZEN_PRODUCTION_SETTINGS
)
SELECTED_M5 = (
    ACTIVE_PRODUCTION_SETTINGS["LEGAL_RETRIEVAL_V2_SELECTED_M5"]
    if IS_PRODUCTION
    else str(os.getenv("LEGAL_RETRIEVAL_V2_SELECTED_M5") or "").strip() or None
)
CANDIDATE_PROFILE = (
    ACTIVE_PRODUCTION_SETTINGS["LEGAL_RETRIEVAL_V2_CANDIDATE_PROFILE"]
    if IS_PRODUCTION
    else str(
        os.getenv("LEGAL_RETRIEVAL_V2_CANDIDATE_PROFILE")
        or R26_CANDIDATE_PROFILE
    ).strip()
)
if CANDIDATE_PROFILE not in {
    "v6r20",
    "r21-shadow",
    "r22-shadow",
    R26_CANDIDATE_PROFILE,
    R27_CANDIDATE_PROFILE,
    R28_CANDIDATE_PROFILE,
}:
    raise V2RuntimeError(f"unsupported_candidate_profile:{CANDIDATE_PROFILE}")
DEFAULT_VECTOR_TOP_K = 80 if IS_PRODUCTION else int(os.getenv("LEGAL_RETRIEVAL_VECTOR_TOP_K") or 50)
DEFAULT_LEXICAL_TOP_K = 80 if IS_PRODUCTION else int(os.getenv("LEGAL_RETRIEVAL_LEXICAL_TOP_K") or 30)
HYBRID_RRF_V3_ENABLED = str(
    os.getenv("LEGAL_RETRIEVAL_HYBRID_RRF_V3_ENABLED", "false")
).strip().casefold() in {"1", "true", "yes", "on"}
BATCH_RERANK_CANDIDATE_POOL = min(
    100,
    max(10, int(os.getenv("LEGAL_RETRIEVAL_BATCH_RERANK_CANDIDATES", "50"))),
)
# Keep the full frozen v6r26 branch windows available to fusion. The serving
# response is still reduced to ten (or twelve for exact Article) below; this
# value is only the post-branch work window and is not sent to the LLM.
RAW_RETRIEVAL_CANDIDATE_POOL = 80
RAW_RETRIEVAL_FINAL_COUNT = min(
    12,
    max(1, int(os.getenv("LEGAL_RAW_RETRIEVAL_FINAL_COUNT", "10"))),
)
RAW_RETRIEVAL_EXACT_FINAL_COUNT = min(
    12,
    max(
        RAW_RETRIEVAL_FINAL_COUNT,
        int(os.getenv("LEGAL_RAW_RETRIEVAL_EXACT_FINAL_COUNT", "12")),
    ),
)
RRF_RANK_CONSTANT = 60.0
INCREMENTAL_COLLECTION = str(
    os.getenv("LEGAL_CHROMA_INCREMENTAL_COLLECTION") or ""
).strip()
# Admin approvals are written by the management process while Retrieval V2
# keeps the immutable release open in another process.  Some Chroma versions
# refresh SQLite metadata/counts across processes but keep an old HNSW graph in
# memory.  The additive collection is intentionally small, so rank it exactly
# from the persisted embeddings instead of allowing that stale graph to hide a
# freshly approved document.  Above this bounded size we retain Chroma ANN.
OVERLAY_EXACT_SEARCH_MAX_VECTORS = max(
    1, int(os.getenv("LEGAL_ADMIN_OVERLAY_EXACT_MAX_VECTORS") or "5000")
)
OVERLAY_SNAPSHOT_PATH = default_overlay_snapshot_path(
    Path(
        os.getenv(
            "LEGAL_CHROMA_PATH",
            str(ROOT / "release-data/legal/chroma_store"),
        )
    )
)
# The mutable admin overlay is intentionally additive.  ANN similarity alone
# is not a sufficient serving signal: a small overlay can contain a highly
# generic passage (or a stale embedding) which outranks a materially relevant
# immutable release result.  Keep only rows with lexical support from the
# question unless the question names the document exactly.
_OVERLAY_STOPWORDS = frozenset(
    {
        "anh", "bao", "ban", "bi", "cach", "can", "cho", "co", "cua",
        "da", "de", "den", "duoc", "gi", "hay", "hien", "hoi", "la",
        "lam", "neu", "nay", "ngay", "nhu", "noi", "o", "phai", "the",
        "theo", "thi", "toi", "trong", "tu", "ve", "voi", "vua", "va",
        "xem", "xin", "cho", "mot", "nhung", "nhat", "nha", "nay",
        "quy", "dinh", "van", "ban", "noi", "dung", "truong", "hop",
        "dieu", "khoan", "diem", "giay", "to", "hay", "co", "quan",
    }
)
_OVERLAY_AMBIGUOUS_TERMS = frozenset(
    {
        # These words occur in many unrelated legal passages (for example
        # ``hy sinh`` versus ``khai sinh``).  They only count when they are
        # part of a substantive phrase shared by the question and row.
        "sinh", "chinh", "hanh", "dia", "phuong", "nam", "ngay", "thang",
        "nguoi", "truong", "hop", "thuc", "tuc", "thoi", "muc", "dieu",
        "kien", "to", "cao", "ho", "so",
    }
)
_OVERLAY_GENERIC_TERMS = frozenset(
    {
        # Procedural words are useful for recall in the frozen index but are
        # too common to admit an additive overlay row by themselves.
        "dang", "ky", "lai", "mat", "muon", "nop", "dau", "lam", "con",
        "khong", "duoc", "can", "phai", "muon", "hien", "nay", "tren",
        "nay", "cu", "moi", "mot", "nhieu", "nguon", "noi", "dung",
        "thuc", "ap", "dung", "xem", "tiep", "nhan", "giai", "quyet",
        "quy", "dinh", "van", "ban", "trong", "theo", "thu", "tuc",
    }
)
app = FastAPI(title="Legal Retrieval V2 Production Server" if IS_PRODUCTION else "Legal Retrieval V2 Shadow Server")


def _apply_frozen_production_profile(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve the explicitly selected production profile and reject drift."""

    if not IS_PRODUCTION:
        return {}
    source = os.environ if environment is None else environment
    resolved: dict[str, str] = {}
    for name, expected in ACTIVE_PRODUCTION_SETTINGS.items():
        configured = str(source.get(name) or "").strip()
        if configured and configured != expected:
            profile_label = (
                FROZEN_M5_PROFILE if PRODUCTION_PROFILE == "r26" else PRODUCTION_PROFILE
            )
            raise V2RuntimeError(
                f"frozen_{profile_label}_profile_mismatch:{name}"
            )
        resolved[name] = expected
        if environment is None:
            os.environ.setdefault(name, expected)
    return resolved


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=8, ge=1, le=30)
    top_k: int | None = None
    candidate_count: int = Field(default=DEFAULT_VECTOR_TOP_K, ge=10, le=500)
    lexical_candidate_count: int = Field(default=DEFAULT_LEXICAL_TOP_K, ge=0, le=120)
    as_of: date = Field(default_factory=date.today)
    as_of_explicit: bool = False
    temporal_scope: str | None = None
    domain: str | None = None
    audience: str = Field(default="citizen", pattern="^(citizen|officer|admin)$")
    organization_unit_id: str | None = Field(default=None, max_length=120)
    organization_routing_mode: str = Field(
        default="legacy", pattern="^(legacy|shadow|hybrid|unit_primary)$"
    )
    # The immutable release benchmark can explicitly exclude the additive
    # admin overlay. Existing callers retain the production-safe default.
    include_overlay: bool = True
    # Optional evaluator/router contract. Normal UI requests omit this field;
    # a false value is authoritative and produces a refusal without retrieval.
    answer_required: bool | None = None
    # The API router computes LegalQueryDecisionV1 once.  Retrieval accepts
    # the serialized decision so it does not independently reclassify the
    # request's domain or temporal scope.
    query_decision: dict[str, Any] | None = None
    # The router may provide a bounded, user-derived issue plan so each legal
    # question is retrieved independently.  Empty keeps the legacy single-
    # issue path backward compatible.
    issue_groups: list[dict[str, Any]] = Field(default_factory=list)


def _manifest_path_from_environment() -> Path:
    raw = str(
        os.getenv("LEGAL_RETRIEVAL_V2_MANIFEST")
        or os.getenv("LEGAL_SERVING_MANIFEST_POINTER")
        or ""
    ).strip()
    if not raw:
        raise V2RuntimeError("retrieval_v2_manifest_required")
    return Path(raw).resolve()


def _load_release_pointer() -> V3ServingManifest:
    expected_sha = str(os.getenv("LEGAL_RETRIEVAL_V2_MANIFEST_FILE_SHA256") or "").strip()
    return load_v3_serving_manifest(
        _manifest_path_from_environment(),
        expected_file_sha256=expected_sha or None,
        project_root=ROOT,
        verify_file_artifacts=False,
    )


def _optional_exact_match(configured: str | None, expected: str, error: str) -> str:
    value = str(configured or "").strip()
    if value and value != expected:
        raise V2RuntimeError(error)
    return expected


def _verify_query_encoder_provenance(
    serving: V3ServingManifest, encoder: Any
) -> None:
    provenance = serving.payload.get("provenance") or {}
    expected_recipe = str(provenance.get("embedding_recipe_fingerprint") or "")
    active_recipe = str(
        getattr(encoder, "embedding_recipe_fingerprint", "") or ""
    )
    if not expected_recipe or active_recipe != expected_recipe:
        raise V2RuntimeError("query_embedding_recipe_mismatch")

    expected_model = str(provenance.get("model_artifact_fingerprint") or "")
    active_model = str(getattr(encoder, "model_fingerprint", "") or "")
    if not expected_model or active_model != expected_model:
        raise V2RuntimeError("query_embedding_model_mismatch")


def _load_r28_catalog_artifacts(
    serving: V3ServingManifest,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if CANDIDATE_PROFILE != R28_CANDIDATE_PROFILE:
        return None, None, None
    policy = serving.payload.get("candidate_policy")
    if not isinstance(policy, Mapping) or str(policy.get("profile") or "") != R28_CANDIDATE_PROFILE:
        raise V2RuntimeError("r28_candidate_policy_manifest_required")
    artifacts = policy.get("artifacts")
    required = ("article_mapping", "owner_acceptance", "blocked_resolution")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(required):
        raise V2RuntimeError("r28_candidate_policy_artifacts_required")
    loaded: list[dict[str, Any]] = []
    root = ROOT.resolve()
    for name in required:
        descriptor = artifacts.get(name)
        if not isinstance(descriptor, Mapping):
            raise V2RuntimeError(f"r28_candidate_policy_{name}_invalid")
        raw = Path(str(descriptor.get("path") or ""))
        if raw.is_absolute() or ".." in raw.parts:
            raise V2RuntimeError(f"r28_candidate_policy_{name}_path_invalid")
        path = (root / raw).resolve()
        if root not in path.parents or not path.is_file():
            raise V2RuntimeError(f"r28_candidate_policy_{name}_missing")
        expected_sha = str(descriptor.get("sha256") or "").strip().lower()
        if not expected_sha or file_sha256(path) != expected_sha:
            raise V2RuntimeError(f"r28_candidate_policy_{name}_checksum_mismatch")
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise V2RuntimeError(f"r28_candidate_policy_{name}_unreadable") from exc
        if not isinstance(payload, dict):
            raise V2RuntimeError(f"r28_candidate_policy_{name}_object_required")
        loaded.append(payload)
    return loaded[0], loaded[1], loaded[2]


def build_runtime() -> V2ServingRuntime:
    _apply_frozen_production_profile()
    serving = _load_release_pointer()
    chroma_path = Path(
        os.getenv("LEGAL_CHROMA_PATH", str(ROOT / "release-data/legal/chroma_store"))
    ).resolve()
    current_collection = _optional_exact_match(
        os.getenv("LEGAL_CHROMA_COLLECTION"),
        serving.current_collection,
        "configured_current_collection_mismatch",
    )
    temporal_collection = _optional_exact_match(
        os.getenv("LEGAL_CHROMA_TEMPORAL_COLLECTION"),
        serving.temporal_collection,
        "configured_temporal_collection_mismatch",
    )
    source_collection = str(os.getenv("LEGAL_CHROMA_SOURCE_COLLECTION") or "").strip()
    if source_collection and source_collection != current_collection:
        raise V2RuntimeError("configured_source_collection_mismatch")

    model_path = str(os.getenv("VNLEGAL_LAL_MODEL_PATH") or "").strip()
    if not model_path:
        raise V2RuntimeError("vnlegal_lal_model_path_required")
    from scripts.simple_encoder import load_encoder

    encoder = load_encoder(model_path)
    _verify_query_encoder_provenance(serving, encoder)
    r28_mapping, r28_acceptance, r28_blocked = _load_r28_catalog_artifacts(serving)
    chunk_manifest = serving.declared_file_path("chunk_manifest_path", project_root=ROOT)
    lexical_index = serving.artifact_path("exact_lexical_index", project_root=ROOT)
    runtime = V2ServingRuntime(
        manifest_path=chunk_manifest,
        lexical_index_path=lexical_index,
        chroma_path=chroma_path,
        current_collection=current_collection,
        temporal_collection=temporal_collection,
        query_encoder=encoder,
        serving_manifest_path=serving.path,
        sqlite_check_same_thread=False,
        allowlist_backend="verified_sqlite",
        verify_declared_manifest_artifacts=False,
        verify_collection_counts=str(
            os.getenv("LEGAL_RETRIEVAL_VERIFY_COLLECTION_COUNTS") or "true"
        ).strip().casefold() not in {"0", "false", "no", "off"},
        require_chroma_collections=str(
            os.getenv("LEGAL_RETRIEVAL_V2_REQUIRE_CHROMA_COLLECTIONS") or "true"
        ).strip().casefold() not in {"0", "false", "no", "off"},
        r28_catalog_mapping=r28_mapping,
        r28_catalog_acceptance=r28_acceptance,
        r28_blocked_resolution=r28_blocked,
    )
    runtime.warmup()
    return runtime


_runtime: V2ServingRuntime | None = None
_batch_reranker: OptionalCrossEncoderReranker | None = None
_overlay_collection: Any | None = None
_overlay_client: Any | None = None
_overlay_engine: Any | None = None
_overlay_error: str | None = None
_overlay_snapshot: dict[str, Any] | None = None
_overlay_snapshot_generation_ns: int | None = None
_overlay_snapshot_error: str | None = None


def _get_overlay_snapshot() -> dict[str, Any] | None:
    global _overlay_snapshot, _overlay_snapshot_generation_ns, _overlay_snapshot_error
    if not INCREMENTAL_COLLECTION:
        return None
    try:
        generation = OVERLAY_SNAPSHOT_PATH.stat().st_mtime_ns
        if _overlay_snapshot is None or generation != _overlay_snapshot_generation_ns:
            _overlay_snapshot = load_overlay_snapshot(
                OVERLAY_SNAPSHOT_PATH,
                expected_collection=INCREMENTAL_COLLECTION,
            )
            _overlay_snapshot_generation_ns = generation
        _overlay_snapshot_error = None
        return _overlay_snapshot
    except Exception as exc:
        _overlay_snapshot_error = f"{type(exc).__name__}: {exc}"
        return None


def get_runtime() -> V2ServingRuntime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def get_batch_reranker() -> OptionalCrossEncoderReranker:
    """Return the optional Vietnamese cross-encoder used after v6r26 fusion.

    Construction is lazy so a disabled or missing local model has zero startup
    cost.  The adapter itself is local-only and degrades to the deterministic
    v6r26 order when the approved model artifact is unavailable.
    """

    global _batch_reranker
    if _batch_reranker is None:
        _batch_reranker = OptionalCrossEncoderReranker.from_environment()
    return _batch_reranker


def _database_url() -> str:
    direct = str(os.getenv("LEGAL_DATABASE_URL") or "").strip()
    if direct:
        return direct.replace("host.docker.internal", "127.0.0.1")
    values = dotenv_values(ROOT / ".env")
    configured = str(values.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    return (
        configured.replace("host.docker.internal", "127.0.0.1")
        if configured
        else "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/legal_chatbot"
    )


def _get_overlay_collection(
    runtime: V2ServingRuntime, *, refresh: bool = False
) -> Any | None:
    """Return the additive collection, refreshing handles after admin imports.

    The management service and Retrieval V2 are separate processes but share
    the same persistent Chroma store. Caching the collection handle forever
    makes V2 keep an old view of the overlay after an admin import, so the
    post-activation smoke test reports a false negative and compensates the
    otherwise valid document. Reopening this small collection is cheap and
    keeps SQL state plus vector membership observable across process
    boundaries. The large frozen collections remain fully cached.
    """

    global _overlay_collection, _overlay_client, _overlay_error
    if not INCREMENTAL_COLLECTION:
        return None
    # Lightweight runtime probes and offline diagnostics intentionally expose
    # only ``search``.  An optional staging overlay must stay neutral when the
    # frozen runtime does not expose collection handles; it must never turn a
    # normal baseline search into an AttributeError.
    current_name = getattr(getattr(runtime, "current_collection", None), "name", None)
    temporal_name = getattr(getattr(runtime, "temporal_collection", None), "name", None)
    if INCREMENTAL_COLLECTION in {
        current_name,
        temporal_name,
        BASELINE_COLLECTION,
    }:
        _overlay_error = "incremental_collection_must_not_be_a_serving_baseline"
        return None
    # Lightweight test/diagnostic runtimes may intentionally expose only the
    # frozen ``search`` contract.  The overlay requires the production query
    # encoder, so keep it neutral instead of opening Chroma and changing the
    # behavior of those callers.
    if not callable(getattr(runtime, "_encode_query_cached", None)):
        _overlay_error = "incremental_runtime_encoder_unavailable"
        return None
    if refresh or _overlay_collection is None:
        try:
            if refresh:
                # The management service writes the persistent collection in a
                # different process.  A cached PersistentClient can retain the
                # old HNSW view even after get_collection() is called again,
                # which makes a freshly imported document look unavailable to
                # semantic search.  Reopen only the small additive overlay;
                # keep the large frozen serving collections cached in runtime.
                import chromadb

                if _overlay_client is not None:
                    try:
                        _overlay_client.close()
                    except Exception:
                        pass
                    _overlay_client = None
                # Chroma shares a System between PersistentClient instances
                # for the same path.  Clearing that registry is required here:
                # otherwise the "fresh" overlay client silently reuses the
                # runtime's old HNSW component and still cannot see vectors
                # written by the management process after startup.
                try:
                    chromadb.PersistentClient.clear_system_cache()
                except Exception:
                    pass
                chroma_path = Path(
                    os.getenv(
                        "LEGAL_CHROMA_PATH",
                        str(ROOT / "release-data/legal/chroma_store"),
                    )
                ).resolve()
                _overlay_client = chromadb.PersistentClient(path=str(chroma_path))
                client = _overlay_client
            else:
                client = runtime.chroma_client
            _overlay_collection = client.get_collection(INCREMENTAL_COLLECTION)
            _overlay_error = None
        except Exception as exc:
            _overlay_error = f"{exc.__class__.__name__}: {exc}"
            return None
    return _overlay_collection


def _get_overlay_engine() -> Any:
    global _overlay_engine
    if _overlay_engine is None:
        url = _database_url()
        _overlay_engine = create_engine(
            url, future=True, pool_pre_ping=True, pool_timeout=3,
            connect_args=(
                {"connect_timeout": 3, "options": "-c statement_timeout=3000"}
                if url.startswith("postgresql") else {}
            ),
        )
    return _overlay_engine


ORGANIZATION_UNIT_SOFT_BOOST = 0.035


def _apply_organization_unit_soft_boost(
    rows: list[dict[str, Any]],
    *,
    query: str,
    audience: str | None,
    organization_unit_id: str | None,
    routing_mode: str = "hybrid",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enforce the officer's confirmed document assignment as an ACL.

    The public name is retained because older evaluators import this helper,
    but it is no longer a ranking-only boost.  An officer must never receive
    a candidate outside their organization unit, including an exact document
    lookup.  If the authoritative assignment projection is unavailable the
    safe result is an empty evidence set, not a cross-unit fallback.
    """

    normalized_audience = str(audience or "").strip().casefold()
    unit_id = str(organization_unit_id or "").strip()
    status: dict[str, Any] = {
        "mode": "not_applicable",
        "boost": 0.0,
        "matched_count": 0,
        "candidate_count": len(rows),
        "rejected_count": 0,
    }
    if normalized_audience != "officer":
        return rows, status
    if not unit_id:
        status.update(
            mode="missing_unit_fail_closed",
            rejected_count=len(rows),
        )
        return [], status
    if not rows:
        status["mode"] = "hard_filter"
        return [], status

    document_ids = sorted(
        {
            int(str(row.get("document_id") or "").strip())
            for row in rows
            if str(row.get("document_id") or "").strip().isdigit()
        }
    )
    if not document_ids:
        status.update(
            mode="missing_document_identity_fail_closed",
            rejected_count=len(rows),
        )
        return [], status
    try:
        statement = text(
            """
            SELECT document_id
            FROM legal_document_organization_assignment
            WHERE document_id IN :document_ids
              AND assignment_state = 'assigned'
              AND (
                    primary_organization_unit_id = :organization_unit_id
                    OR organization_unit_ids @> CAST(:organization_unit_json AS JSONB)
                  )
            """
        ).bindparams(bindparam("document_ids", expanding=True))
        with _get_overlay_engine().connect() as connection:
            matched_ids = {
                int(value)
                for value in connection.execute(
                    statement,
                    {
                        "document_ids": document_ids,
                        "organization_unit_id": unit_id,
                        "organization_unit_json": json.dumps([unit_id]),
                    },
                ).scalars()
            }
    except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
        status.update(
            mode="projection_unavailable_fail_closed",
            rejected_count=len(rows),
            error_class=type(exc).__name__,
        )
        return [], status

    scoped = [
        dict(row)
        for row in rows
        if str(row.get("document_id") or "").strip().isdigit()
        and int(str(row.get("document_id")).strip()) in matched_ids
    ]
    status.update(
        mode="hard_filter",
        matched_count=len(scoped),
        rejected_count=max(len(rows) - len(scoped), 0),
        routing_mode=str(routing_mode or "legacy").strip().casefold(),
        exact_lookup=bool(plan_exact_lookup(query).law_number),
    )
    return scoped, status


def _live_document_vector_membership(doc_id: str) -> dict[str, Any]:
    """Verify exact SQL chunk IDs against the collections serving Q&A.

    This endpoint is intentionally read-only.  It lets the SQL-only
    management process make restore/history decisions without opening a
    second copy of the large Chroma indexes or guessing from collection
    counts.
    """

    document_id = canonical_document_id(doc_id)
    engine = _get_overlay_engine()
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM legal_documents WHERE id = :document_id"),
            {"document_id": document_id},
        ).scalar_one_or_none()
        if exists is None:
            raise LookupError("legal_document_not_found")
        chunk_ids = [
            int(value)
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
    vector_ids = [f"chunk-{value}" for value in chunk_ids]
    fingerprint = hashlib.sha256(
        "\n".join(vector_ids).encode("utf-8")
    ).hexdigest()
    runtime = get_runtime()
    collections: dict[str, Any] = {
        "current": getattr(runtime, "current_collection", None),
        "temporal": getattr(runtime, "temporal_collection", None),
    }
    if INCREMENTAL_COLLECTION:
        collections["incremental"] = _get_overlay_collection(runtime, refresh=True)

    reports: dict[str, dict[str, Any]] = {}
    degraded = False
    for name, collection in collections.items():
        if collection is None:
            degraded = True
            reports[name] = {
                "present": 0,
                "missing": len(vector_ids),
                "reason_code": "vector_collection_unavailable",
            }
            continue
        try:
            payload = (
                collection.get(ids=vector_ids, include=[])
                if vector_ids
                else {"ids": []}
            )
            raw_ids = payload.get("ids", []) if isinstance(payload, Mapping) else []
            if raw_ids and isinstance(raw_ids[0], list):
                raw_ids = [item for group in raw_ids for item in group]
            present = len({str(item) for item in raw_ids if str(item) in vector_ids})
            reports[name] = {
                "present": present,
                "missing": max(len(vector_ids) - present, 0),
            }
        except Exception:
            degraded = True
            reports[name] = {
                "present": 0,
                "missing": len(vector_ids),
                "reason_code": "vector_collection_unavailable",
            }

    complete_collections = [
        name
        for name, report in reports.items()
        if vector_ids and int(report.get("missing") or 0) == 0
    ]
    current_ready = bool(
        set(complete_collections) & {"current", "incremental"}
    )
    historical_ready = bool(
        set(complete_collections) & {"temporal", "incremental"}
    )
    state = DocumentServingStateStore(engine).read(document_id)
    return {
        "status": "degraded" if degraded else "available",
        "reason_code": "vector_collection_unavailable" if degraded else None,
        "message": (
            "Không thể đọc một hoặc nhiều kho vector đang phục vụ."
            if degraded
            else None
        ),
        "document_id": str(document_id),
        "state_revision": state_revision(state),
        "expected": len(vector_ids),
        "chunk_ids_sha256": fingerprint,
        "collections": reports,
        "complete_collections": complete_collections,
        "retrieval_ready": bool(current_ready or historical_ready),
        "current_retrieval_ready": current_ready,
        "historical_retrieval_ready": historical_ready,
        "verification_source": "retrieval_v2_live",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_document_serving_states(ids: list[int]) -> dict[str, dict[str, Any]]:
    """Read only returned IDs, with no stale allow-cache and no corpus scan."""
    try:
        return DocumentServingStateStore(_get_overlay_engine()).read_many(ids)
    except Exception as exc:
        # A cached 'allowed' result could resurrect a document excluded by an
        # Admin while SQL was unavailable. Never return it as legal evidence.
        raise V2RuntimeError("admin_search_state_unavailable") from exc


def _apply_document_serving_state(
    output: dict[str, Any], *, temporal_scope: str, as_of: date,
    known_states: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply the same lifecycle contract to frozen and additive evidence."""
    before = list(output.get("results") or [])
    identities: dict[int, int] = {}
    for index, item in enumerate(before):
        try:
            identities[index] = canonical_document_id(item.get("document_id"))
        except ValueError:
            continue
    states = known_states if known_states is not None else (
        _load_document_serving_states(list(set(identities.values()))) if identities else {}
    )
    filtered: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    for index, item in enumerate(before):
        state = states.get(str(identities.get(index)))
        decision = serving_projection(state, temporal_scope=temporal_scope, as_of=as_of)
        if not decision["allowed"]:
            reason = decision["reason_code"]
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        projected = {**item, "serving_state_check": decision}
        if decision["serving_state"] == "historical_only":
            projected["document_serving_state"] = "historical_only"
            if "document_status" in projected:
                projected["document_status"] = "historical"
        filtered.append(projected)
    trace = dict(output.get("trace") or {})
    trace["admin_search_state"] = {
        "status": "checked",
        "reason_code": None,
        "filtered": len(before) - len(filtered),
        "checked_document_count": len(states),
        "filtered_reasons": rejected,
        "temporal_scope": temporal_scope,
        "authority": "postgresql_document_and_scope",
    }
    output["results"] = filtered
    trace["final_evidence"] = filtered
    output["trace"] = trace
    return output


_NO_DOMAIN_FILTER = frozenset({"", "unknown", "all", "general"})


def _normalized_domain_filter(value: Any) -> str | None:
    """Return a real domain filter, or ``None`` for an unscoped request."""

    canonical = str(canonicalize_legal_domain(value) or "").strip().casefold()
    return None if canonical in _NO_DOMAIN_FILTER else canonical


def _requested_overlay_law_numbers(query: str) -> tuple[str, ...]:
    """Extract exact legal identifiers from the whole question."""

    plan = plan_exact_lookup(query)
    return tuple(
        dict.fromkeys(
            normalized
            for value in (plan.law_numbers or ((plan.law_number,) if plan.law_number else ()))
            if (normalized := normalize_exact_identifier(value))
        )
    )


def _overlay_vector_search_allowed(query: str) -> bool:
    """Return False for exact law/article requests.

    Exact identifiers are resolved by the SQL authority below. Encoding such
    a query first is both wasteful and unsafe: an ANN funnel can omit the
    requested document before the deterministic identity check runs.
    """

    plan = plan_exact_lookup(query)
    return not bool(
        getattr(plan, "law_number", None)
        or getattr(plan, "law_numbers", ())
    )


def _overlay_law_candidate_pattern(law_number: str) -> str | None:
    """Build an index-friendly SQL prefilter; Python performs exact identity.

    The previous ``%number%year%`` predicate forced a sequential scan over the
    imported overlay and routinely hit PostgreSQL's three-second statement
    timeout under concurrent Direct RAG traffic.  A prefix keeps the same
    suffix/OCR tolerance while allowing the law-number index to participate.
    """

    parts = normalize_exact_identifier(law_number).split("/")
    if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return f"{parts[0]}/{parts[1]}/%"


def _query_overlay_vectors(
    runtime: V2ServingRuntime,
    collection: Any,
    *,
    query: str,
    count: int,
    n_results: int,
) -> dict[str, list[list[Any]]]:
    """Query the mutable Admin overlay without trusting a stale HNSW view.

    PostgreSQL remains the serving-state authority in ``_overlay_rows``.  This
    helper only produces vector candidates.  Exact cosine is bounded to the
    small additive collection and uses the same manifest-verified query encoder
    as the frozen release.  If the collection cannot expose persisted
    embeddings, fall back to its normal ANN contract rather than failing the
    whole retrieval service.
    """

    vector, _ = runtime._encode_query_cached(query)
    requested = min(max(1, int(n_results)), max(1, int(count)))
    if 0 < count <= OVERLAY_EXACT_SEARCH_MAX_VECTORS:
        try:
            snapshot = _get_overlay_snapshot()
            if snapshot is not None and int(snapshot.get("count") or 0) == count:
                raw_embeddings = snapshot.get("embeddings")
                raw_metadatas = snapshot.get("metadatas") or []
            else:
                payload = collection.get(include=["embeddings", "metadatas"])
                raw_embeddings = payload.get("embeddings")
                raw_metadatas = payload.get("metadatas") or []
            if raw_embeddings is not None:
                matrix = np.asarray(raw_embeddings, dtype=np.float32)
                query_vector = np.asarray(vector, dtype=np.float32).reshape(-1)
                if (
                    matrix.ndim == 2
                    and matrix.shape[0] == len(raw_metadatas)
                    and matrix.shape[1] == query_vector.shape[0]
                    and matrix.shape[0] > 0
                ):
                    matrix_norms = np.linalg.norm(matrix, axis=1)
                    query_norm = float(np.linalg.norm(query_vector))
                    denominator = np.maximum(
                        matrix_norms * max(query_norm, 1e-12), 1e-12
                    )
                    similarities = (matrix @ query_vector) / denominator
                    order = np.argsort(-similarities, kind="stable")[:requested]
                    return {
                        "metadatas": [
                            [dict(raw_metadatas[int(index)] or {}) for index in order]
                        ],
                        "distances": [
                            [float(1.0 - similarities[int(index)]) for index in order]
                        ],
                    }
        except Exception:
            # The immutable release must remain available even when an older
            # Chroma adapter cannot expose embeddings through ``get``.
            pass
    return collection.query(
        query_embeddings=[np.asarray(vector, dtype=np.float32).tolist()],
        n_results=requested,
        include=["metadatas", "distances"],
    )


def _overlay_relevance_terms(value: Any) -> set[str]:
    """Return accent-insensitive, substantive terms for overlay gating."""

    return {
        token
        for token in normalize_vietnamese_search_text(value).split()
        if len(token) >= 3 and token not in _OVERLAY_STOPWORDS
    }


def _overlay_law_identity(value: Any) -> str:
    """Normalize an imported 'Số:' label for identity, preserving metadata."""

    return re.sub(r"^SO:?(?=\d)", "", normalize_exact_identifier(value))


def _overlay_has_lexical_support(query: str, row: Mapping[str, Any]) -> bool:
    """Reject semantically unrelated overlay rows before final fusion.

    The immutable R28 release already fuses vector and phrase-FTS evidence.
    Overlay rows are a small mutable additive set, so requiring two
    substantive query terms (or one strong legal identifier/domain term) is a
    cheap guard against generic admin passages drowning out the frozen index.
    Exact law-number requests are handled by the deterministic SQL path and
    are always allowed here.
    """

    requested_laws = _requested_overlay_law_numbers(query)
    if requested_laws:
        return _overlay_law_identity(row.get("law_number")) in requested_laws
    query_normalized = _overlay_topic_query(query)
    query_terms = _overlay_relevance_terms(query_normalized)
    if not query_terms:
        return False
    row_text = " ".join(
        str(row.get(key) or "")
        for key in ("content", "document_title", "article_number", "domain_slug", "law_number")
    )
    row_normalized = normalize_vietnamese_search_text(row_text)
    row_terms = _overlay_relevance_terms(row_text)
    overlap = query_terms & row_terms
    meaningful_overlap = overlap - _OVERLAY_AMBIGUOUS_TERMS - _OVERLAY_GENERIC_TERMS
    if len(meaningful_overlap) >= 2:
        return True
    # Preserve a real multi-word legal subject (``khai sinh``, ``mai táng``,
    # ``tách thửa``) even when one component is common in legal prose.
    query_tokens = query_normalized.split()
    for left, right in zip(query_tokens, query_tokens[1:]):
        if (
            left in _OVERLAY_STOPWORDS
            or right in _OVERLAY_STOPWORDS
            or left in _OVERLAY_GENERIC_TERMS
            or right in _OVERLAY_GENERIC_TERMS
        ):
            continue
        if left in _OVERLAY_AMBIGUOUS_TERMS and right in _OVERLAY_AMBIGUOUS_TERMS:
            continue
        if f"{left} {right}" in row_normalized:
            return True
    # A single distinctive term is enough for a short, explicit request (for
    # example ``CT01`` or ``mai táng``); generic administrative words are not.
    distinctive = query_terms - _OVERLAY_AMBIGUOUS_TERMS - _OVERLAY_GENERIC_TERMS
    return bool(distinctive & overlap and len(query_terms) <= 3)


def _overlay_topic_query(query: str) -> str:
    """Remove a location qualifier before looking for a legal subject match.

    ``tại Hải Phòng`` and ``tại Bình Dương`` locate a procedure; their words
    alone do not support its paperwork, fee or authority. Keep references to
    legal provisions (``tại khoản ...``), which are not locality qualifiers.
    """

    # Parse before folding: ``tài sản`` is a legal subject, not ``tại`` a
    # location. Preserve punctuation so a leading locality cannot swallow
    # the procedure in the next clause.
    pattern = re.compile(
        r"\b(?P<preposition>tại|trên địa bàn|tai|tren dia ban)\s+(?P<place>.+?)"
        r"(?=\s+(?:thì|thi|cần|can|phải|phai|đăng|dang|được|duoc|có|co|là|la|"
        r"theo|như|nhu|muốn|muon|xin|nộp|nop|thực hiện|thuc hien|thủ tục|thu tuc|"
        r"hồ sơ|ho so|quy định|quy dinh)\b|[,;:?!]|$)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        raw_place = match.group("place").strip()
        place = normalize_vietnamese_search_text(raw_place)
        first = place.split()[0] if place else ""
        if first in {"dieu", "khoan", "diem"}:
            return match.group(0)
        # Accent-free text cannot distinguish tài/tại/tai. These ordinary
        # legal noun phrases are never locations.
        if match.group("preposition").casefold() == "tai" and first in {
            "san", "lieu", "chinh", "nan",
        }:
            return match.group(0)
        words = raw_place.split()
        folded_words = place.split()
        prefix = 2 if folded_words[:2] == ["thanh", "pho"] else (
            1 if first in {"tinh", "phuong", "xa", "quan", "huyen"} else 0
        )
        end = prefix
        while end < len(words) and words[end][:1].isupper():
            end += 1
        if end > prefix and end - prefix <= 4:
            # A following lower-case procedure stays in the query even when
            # the user omitted the comma after a capitalized place name.
            return " " + " ".join(words[end:])
        if len(words) <= 3:
            # A short, delimited lower-case location supports accent-free
            # questions. Do not guess the end of a longer unpunctuated span.
            return " "
        return match.group(0)

    return normalize_vietnamese_search_text(pattern.sub(replace, str(query or "")))


def _overlay_request_rejection(
    query: str, row: Mapping[str, Any], classification: Mapping[str, Any]
) -> str | None:
    """Validate the request before an overlay row receives a fusion vote."""

    if not query:
        # Compatibility for internal callers without request context. The
        # public search path always supplies its classified original query.
        return None
    plan = plan_exact_lookup(query)
    requested_laws = set(_requested_overlay_law_numbers(query))
    law = _overlay_law_identity(row.get("law_number"))
    if requested_laws:
        if law not in requested_laws:
            return "overlay_outside_authored_document"
        requested_pairs = {
            (normalize_exact_identifier(law_number), normalize_exact(article))
            for law_number, article in plan.article_law_pairs
        }
        requested_articles = {article for requested_law, article in requested_pairs if requested_law == law}
        if requested_articles and normalize_exact(row.get("article_number")) not in requested_articles:
            return "overlay_outside_authored_article"
        return None
    if not _overlay_has_lexical_support(query, row):
        return "overlay_missing_query_topic"
    intents = {
        str(classification.get("intent") or "").upper(),
        *(str(value).upper() for value in classification.get("secondary_intents") or []),
    }
    if "REQUIRED_DOCUMENTS" in intents:
        from api.retrieval_candidate_r28 import required_documents_recovery_r28

        _accepted, rejected = required_documents_recovery_r28(
            query, [{**row, "retrieval_source": "admin_overlay"}]
        )
        if rejected:
            return str(rejected[0]["reason"])
    return None


def _requires_dossier_selection(classification: Mapping[str, Any]) -> bool:
    return "REQUIRED_DOCUMENTS" in {
        str(classification.get("intent") or "").upper(),
        *(str(value).upper() for value in classification.get("secondary_intents") or []),
    }


def _overlay_rows(
    runtime: V2ServingRuntime,
    *,
    query: str,
    domain: str | None,
    as_of: date,
    limit: int,
    temporal_scope: str = "current",
) -> list[dict[str, Any]]:
    """Retrieve active Admin imports without mutating the frozen V2 release.

    Chroma supplies only candidate IDs and similarity. PostgreSQL remains the
    legal authority for active status, effective dates, content and sources.
    A missing overlay is neutral; any invalid row fails closed.
    """

    global _overlay_error

    # Chroma is an optional acceleration layer for the small admin overlay.
    # Older local Chroma stores can be opened by the legacy service but not by
    # the frozen V2 client (for example after a Chroma schema upgrade).  Do
    # not hide an already-approved document in that case: the SQL authority
    # below can still expose deterministic exact law-number matches.
    vector_search_allowed = _overlay_vector_search_allowed(query)
    collection = (
        _get_overlay_collection(runtime, refresh=True)
        if vector_search_allowed
        else None
    )
    count = int(collection.count()) if collection is not None else 0
    normalized_query = normalize_exact(query)
    candidate_scores: dict[int, float] = {}
    normalized_domain = _normalized_domain_filter(domain)
    if collection is not None and count > 0:
        response = _query_overlay_vectors(
            runtime,
            collection,
            query=query,
            count=count,
            # The overlay is an additive admin corpus. Fetch a wider candidate
            # funnel than the final answer limit so a newly approved document
            # is not hidden simply because baseline documents score higher.
            n_results=min(count, max(200, int(limit) * 8)),
        )
        metadatas = list((response.get("metadatas") or [[]])[0])
        distances = list((response.get("distances") or [[]])[0])
        for metadata, distance in zip(metadatas, distances):
            values = dict(metadata or {})
            # Vector tags may be stale after exclusion/restore. SQL below is
            # the authority, so tags are never allowed to grant or deny use.
            row_domain = _normalized_domain_filter(values.get("domain_slug"))
            if normalized_domain and row_domain and row_domain != normalized_domain:
                continue
            try:
                chunk_id = int(values.get("chunk_id"))
            except (TypeError, ValueError):
                continue
            candidate_scores[chunk_id] = max(
                candidate_scores.get(chunk_id, -1.0), 1.0 - float(distance)
            )
    statement = text(
        """
        SELECT c.id AS chunk_id,
               c.chunk_index,
               c.heading AS structural_path,
               c.content,
               a.id AS article_id,
               a.article_number,
               d.id AS document_id,
               d.title AS document_title,
               d.law_number,
               d.issuing_agency,
               d.source_url,
               d.status AS stored_status,
               d.effective_date AS effective_from,
               d.expired_date AS effective_to,
               COALESCE(s.domain, '') AS domain_slug
        FROM legal_article_chunks c
        JOIN legal_articles a ON a.id = c.article_id
        JOIN legal_documents d ON d.id = a.document_id
        LEFT JOIN legal_search_scope s ON s.document_id = d.id
        WHERE c.id IN :chunk_ids
          AND (d.status = 'active' OR (:historical AND d.status IN ('archived', 'expired', 'historical', 'replaced')))
          AND a.status = 'active'
          AND (d.effective_date IS NULL OR d.effective_date <= :as_of)
          AND (d.expired_date IS NULL OR d.expired_date > :as_of)
        """
    ).bindparams(bindparam("chunk_ids", expanding=True))
    with _get_overlay_engine().connect() as connection:
        rows = []
        if candidate_scores:
            try:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        statement,
                        {"chunk_ids": list(candidate_scores), "as_of": as_of, "historical": temporal_scope == "historical"},
                    ).mappings()
                ]
            except SQLAlchemyError as exc:
                # The additive overlay is optional. A transient PostgreSQL
                # timeout/connection error must not turn a valid immutable
                # R28 result into a retrieval 500; baseline candidates remain
                # available and readiness exposes the degraded overlay.
                _overlay_error = f"{type(exc).__name__}: {exc}"
                logger.warning("overlay_candidate_query_skipped reason={}", type(exc).__name__)

        # Exact law-number searches must remain deterministic even when the
        # new overlay contains only a handful of vectors and the frozen V2
        # baseline dominates the ANN funnel. The SQL row is still subject to
        # the same active/effective-date guards before it is exposed.
        requested_laws = _requested_overlay_law_numbers(query)
        if requested_laws:
            # The mutable overlay is additive. Keep its exact probe bounded so
            # an unindexed/temporarily busy PostgreSQL projection cannot hold
            # up the immutable SQLite/mmap exact result for several seconds.
            # A timeout simply leaves the baseline result authoritative.
            try:
                timeout_ms = max(
                    25,
                    min(
                        1_000,
                        int(os.getenv("LEGAL_OVERLAY_EXACT_TIMEOUT_MS", "250")),
                    ),
                )
                connection.exec_driver_sql(
                    f"SET LOCAL statement_timeout = '{timeout_ms}ms'"
                )
            except Exception:
                # Lightweight test doubles and older SQLAlchemy adapters may
                # not expose exec_driver_sql; the guarded query below still
                # remains fail-closed.
                pass
        for requested_law in requested_laws:
            # Use the parsed number/year only to bound the SQL candidate set;
            # exact normalized identity is enforced again below. This remains
            # robust when OCR split a suffix (``TT-BTC`` -> ``T T-BTC``) and
            # avoids the former bug that used the first two words of the
            # natural-language question (for example ``Quyết định``).
            law_pattern = _overlay_law_candidate_pattern(requested_law)
            if not law_pattern:
                continue
            exact_statement = text(
                """
                WITH target_documents AS MATERIALIZED (
                    SELECT id
                    FROM legal_documents
                    -- The number/year prefix is deliberately kept as a plain
                    -- text predicate.  The table is small and this avoids
                    -- invoking the Python-backed normalizer for every row
                    -- under PostgreSQL's three-second serving timeout.  The
                    -- exact normalized identity is still enforced below.
                    WHERE law_number LIKE :law_pattern
                      AND collection_source LIKE 'manual_vnlegal_lal_import%'
                      AND (status = 'active' OR (:historical AND status IN ('archived', 'expired', 'historical', 'replaced')))
                      AND (effective_date IS NULL OR effective_date <= :as_of)
                      AND (expired_date IS NULL OR expired_date > :as_of)
                )
                SELECT c.id AS chunk_id,
                       c.chunk_index,
                       c.heading AS structural_path,
                       c.content,
                       a.id AS article_id,
                       a.article_number,
                       d.id AS document_id,
                       d.title AS document_title,
                       d.law_number,
                       d.issuing_agency,
                       d.source_url,
                       d.status AS stored_status,
                       d.effective_date AS effective_from,
                       d.expired_date AS effective_to,
                       COALESCE(s.domain, '') AS domain_slug
                FROM target_documents td
                JOIN legal_articles a ON a.document_id = td.id
                JOIN legal_article_chunks c ON c.article_id = a.id
                JOIN legal_documents d ON d.id = td.id
                LEFT JOIN legal_search_scope s ON s.document_id = d.id
                WHERE a.status = 'active'
                """
            )
            try:
                exact_rows = [
                    dict(row)
                    for row in connection.execute(
                        exact_statement,
                        {"law_pattern": law_pattern, "as_of": as_of, "historical": temporal_scope == "historical"},
                    ).mappings()
                    if normalize_exact_identifier(row.get("law_number"))
                    == requested_law
                ]
            except SQLAlchemyError as exc:
                # Exact overlay rows are an additive source. If the mutable
                # PostgreSQL projection is slow or unavailable, preserve the
                # immutable SQLite/mmap exact path and let the API continue.
                _overlay_error = f"{type(exc).__name__}: {exc}"
                logger.warning("overlay_exact_query_skipped law={} reason={}", requested_law, type(exc).__name__)
                exact_rows = []
            known_chunks = {int(row["chunk_id"]) for row in rows}
            for row in exact_rows:
                chunk_id = int(row["chunk_id"])
                candidate_scores[chunk_id] = 1.0
                if chunk_id not in known_chunks:
                    rows.append(row)

    requested_laws = set(_requested_overlay_law_numbers(query))
    filtered_rows: list[dict[str, Any]] = []
    rejected_irrelevant = 0
    for row in rows:
        exact_row = normalize_exact_identifier(row.get("law_number")) in requested_laws
        if not exact_row and not _overlay_has_lexical_support(query, row):
            rejected_irrelevant += 1
            continue
        filtered_rows.append(row)
    rows = filtered_rows

    hydrated: dict[int, dict[str, Any]] = {}
    for row in rows:
        chunk_id = int(row["chunk_id"])
        law_number = str(row.get("law_number") or "").strip()
        score = 0.7 * max(0.0, candidate_scores.get(chunk_id, 0.0))
        if law_number and normalize_exact(law_number) in normalized_query:
            score = max(score, 1.0)
        hydrated.setdefault(
            chunk_id,
            {
                **row,
                "chunk_revision_id": f"admin-overlay:{chunk_id}",
                "document_serving_state": "current_retrievable" if row.get("stored_status") == "active" else "historical_only",
                "score": score,
                "retrieval_source": "admin_overlay",
                "retrieval_sources": ["admin_overlay"],
                "release_id": f"admin-overlay:{INCREMENTAL_COLLECTION}",
            },
        )
    return sorted(
        hydrated.values(),
        key=lambda item: (-float(item.get("score") or 0.0), int(item["chunk_id"])),
    )


def _merge_overlay(
    output: dict[str, Any],
    overlay: list[dict[str, Any]],
    *,
    limit: int,
    temporal_scope: str = "current",
    query: str | None = None,
    classification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse eligible branch ranks, never raw ANN and RRF score magnitudes."""

    trace = dict(output.get("trace") or {})
    resolved_query = str(query if query is not None else trace.get("raw_query") or "")
    resolved_classification = dict(classification or trace.get("query_classification") or {})
    rejected: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    for item in overlay:
        reason = _overlay_request_rejection(resolved_query, item, resolved_classification)
        if reason:
            rejected.append({
                "chunk_revision_id": str(item.get("chunk_revision_id") or item.get("source_id") or ""),
                "reason": reason,
            })
        else:
            eligible.append(dict(item))
    baseline = [dict(item) for item in output.get("results") or []]
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    ranks: dict[tuple[str, str, str], dict[str, int]] = {}

    def document_id(item: Mapping[str, Any]) -> int:
        try:
            return int(item.get("document_id") or 0)
        except (ValueError, TypeError):
            return 0

    for lane, rows in (("baseline", baseline), ("admin_overlay", eligible)):
        lane_seen: set[tuple[str, str, str]] = set()
        for item in rows:
            identity = (
                _overlay_law_identity(item.get("law_number"))
                or f"document:{item.get('document_id') or item.get('chunk_revision_id') or ''}",
                f"{normalize_exact(item.get('article_number'))}:{normalize_exact(item.get('content'))}",
                str(item.get("document_id") or item.get("release_id") or "")
                if str(temporal_scope).casefold() == "historical" else "",
            )
            if identity not in lane_seen:
                lane_seen.add(identity)
                ranks.setdefault(identity, {})[lane] = len(lane_seen)
            current = merged.get(identity)
            # Equal official text may occur in both physical versions during
            # replacement. Metadata from the newer version wins independently
            # of incomparable lane scores. Historical identity stays separate.
            if current is None or document_id(item) > document_id(current):
                merged[identity] = dict(item)
    requested_laws = set(_requested_overlay_law_numbers(resolved_query))
    ordered: list[dict[str, Any]] = []
    for identity, item in merged.items():
        lane_ranks = ranks[identity]
        row = dict(item)
        row["pre_overlay_fusion_score"] = row.get("score")
        row["overlay_fusion_ranks"] = dict(lane_ranks)
        row["score"] = sum(1.0 / (RRF_RANK_CONSTANT + rank) for rank in lane_ranks.values())
        row["overlay_fusion_strategy"] = "equal_weight_rank_fusion_v1"
        ordered.append(row)
    ordered.sort(key=lambda item: (
        _overlay_law_identity(item.get("law_number")) not in requested_laws if requested_laws else False,
        -float(item["score"]),
        int(item["overlay_fusion_ranks"].get("baseline", 10**9)),
        int(item["overlay_fusion_ranks"].get("admin_overlay", 10**9)),
        str(item.get("chunk_revision_id") or ""),
    ))
    output["results"] = ordered[: max(1, int(limit))]
    trace["admin_overlay"] = {
        "collection": INCREMENTAL_COLLECTION,
        "candidate_count": len(overlay),
        "eligible_count": len(eligible),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "fusion_strategy": "equal_weight_rank_fusion_v1",
        "rank_constant": RRF_RANK_CONSTANT,
        "baseline_count": len(baseline),
        "fused_count": len(ordered),
    }
    trace["final_evidence"] = output["results"]
    output["trace"] = trace
    return output


def _content_free_retrieval_diagnostics(
    trace: Mapping[str, Any]
) -> dict[str, Any]:
    """Project branch identity/ranking without queries or legal passage text."""

    def candidates(values: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for raw in values if isinstance(values, list) else []:
            item = dict(raw) if isinstance(raw, Mapping) else {}
            identifier = str(item.get("chunk_revision_id") or "")
            if not identifier:
                continue
            output.append(
                {
                    "chunk_revision_id": identifier,
                    "score": round(float(item.get("score") or 0.0), 12),
                    "law_number": str(item.get("law_number") or ""),
                    "article_number": str(item.get("article_number") or ""),
                    "retrieval_sources": list(item.get("retrieval_sources") or []),
                }
            )
        return output

    per_issue: list[dict[str, Any]] = []
    for raw_trace in trace.get("per_issue_traces") or []:
        issue_trace = dict(raw_trace) if isinstance(raw_trace, Mapping) else {}
        classification = issue_trace.get("query_classification") or {}
        per_issue.append(
            {
                "issue_id": str(
                    classification.get("issue_id")
                    if isinstance(classification, Mapping)
                    else ""
                ),
                "exact_candidates": candidates(issue_trace.get("exact_candidates")),
                "vector_candidates": candidates(issue_trace.get("vector_candidates")),
                "lexical_candidates": candidates(issue_trace.get("lexical_candidates")),
                "fusion_candidates": candidates(issue_trace.get("fusion_candidates")),
                "final_evidence": candidates(issue_trace.get("final_evidence")),
            }
        )
    return {
        "schema_version": "legal-retrieval-content-free-diagnostics-v1",
        "admin_search_state": (
            dict(trace.get("admin_search_state"))
            if isinstance(trace.get("admin_search_state"), Mapping)
            else None
        ),
        "admin_overlay": (
            dict(trace.get("admin_overlay"))
            if isinstance(trace.get("admin_overlay"), Mapping) else None
        ),
        "dossier_selection": (
            dict(trace.get("dossier_selection"))
            if isinstance(trace.get("dossier_selection"), Mapping) else None
        ),
        "dossier_recovery_rejections": [
            {"chunk_revision_id": str(item.get("chunk_revision_id") or ""),
             "reason": str(item.get("reason") or "")}
            for item in trace.get("dossier_recovery_rejections") or []
            if isinstance(item, Mapping)
        ],
        "exact_candidates": candidates(trace.get("exact_candidates")),
        "vector_candidates": candidates(trace.get("vector_candidates")),
        "lexical_candidates": candidates(trace.get("lexical_candidates")),
        "fusion_candidates": candidates(trace.get("fusion_candidates")),
        "final_evidence": candidates(trace.get("final_evidence")),
        "per_issue": per_issue,
        "stage_latency_ms": {
            str(key): round(float(value or 0.0), 3)
            for key, value in (trace.get("stage_latency_ms") or {}).items()
        },
    }


def _active_pointer_value() -> str:
    pointer = ROOT / "release-data/legal/chroma_store/active_core_collection.txt"
    try:
        return pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _public_result(
    item: Mapping[str, Any],
    *,
    temporal_scope: str,
    request_id: str | None = None,
    issue_id: str | None = None,
) -> dict[str, Any]:
    """Project only release-backed fields; never guess agency or authority."""

    chunk_id = str(item.get("chunk_revision_id") or "")
    serving_state = str(item.get("document_serving_state") or "")
    status = "historical" if serving_state == "historical_only" else "effective"
    row = {
        "chunk_id": chunk_id,
        "source_id": chunk_id,
        "document_id": item.get("document_id"),
        "article_id": item.get("article_id"),
        "law_number": item.get("law_number"),
        "article_number": item.get("article_number"),
        "document_title": item.get("law_number") or "",
        "title": item.get("structural_path") or "",
        "structural_path": item.get("structural_path") or "",
        "chunk_heading": item.get("structural_path") or "",
        "content": item.get("content") or "",
        "matched_child_content": item.get("content") or "",
        "domain": item.get("domain_slug") or "unknown",
        "domain_slug": item.get("domain_slug") or "unknown",
        "effective_status": status,
        "document_status": status,
        "document_serving_state": serving_state,
        "official": True,
        "official_level": None,
        "issuing_agency": None,
        "scope": temporal_scope,
        "source_url": item.get("source_url") or "",
        "effective_from": item.get("effective_from"),
        "effective_to": item.get("effective_to"),
        "score": round(float(item.get("score") or 0.0), 6),
        "retrieval_sources": list(item.get("retrieval_sources") or []),
        "release_id": item.get("release_id"),
        "passage_sha256": item.get("passage_sha256"),
    }
    if request_id is not None:
        row["request_id"] = request_id
    if issue_id is not None:
        row["issue_id"] = issue_id
        row["query_id"] = f"{issue_id}-q"
    # Exact-Article integrity metadata is created by the release-scoped
    # hydrator below. Keep the projection explicit so arbitrary retrieval
    # internals still cannot leak into the public evidence contract.
    for key in (
        "chunk_index",
        "clause_number",
        "point_number",
        "dossier_amendment_refs",
        "dossier_application_status",
        "dossier_group_truncated",
        "dossier_available_sibling_count",
        "parent_context",
        "parent_context_chars",
        "parent_context_original_chars",
        "parent_context_truncated",
        "parent_context_reason",
        "parent_context_primary",
        "exact_article_packet_ref",
        "exact_article_packet_status",
        "exact_article_packet_reason_codes",
        "exact_article_loaded_chunk_count",
        "exact_article_expected_chunk_count",
        "exact_article_order",
        "exact_article_assembled_content",
        "exact_article_serving_mode",
        "exact_article_full_article_character_count",
        "exact_article_selected_chunk_ids",
        "exact_article_omitted_chunk_count",
        "exact_article_outline",
    ):
        if key in item:
            row[key] = item.get(key)
    return row


def _incomplete_release_exact_packet(
    *,
    law_number: str | None,
    article_number: str | None,
    reason_code: str,
    document_id: Any = None,
    article_id: Any = None,
) -> dict[str, Any]:
    """Create an auditable fail-closed packet for a V2 hydration failure."""

    return {
        "status": "incomplete",
        "reason_codes": [reason_code],
        "packet_ref": (
            f"document:{document_id}:article:{article_id}"
            if document_id is not None and article_id is not None
            else ""
        ),
        "document_id": document_id,
        "article_id": article_id,
        "law_number": law_number,
        "article_number": article_number,
        "ordered_chunk_ids": [],
        "loaded_chunk_count": 0,
        "expected_chunk_count": 0,
        "missing_chunk_indexes": [],
        "duplicate_chunk_indexes": [],
        "empty_chunk_ids": [],
        "missing_structural_units": [],
        "content_coverage_ratio": 0.0,
        "verification_basis": "release_scoped_complete_child_set",
    }


def _hydrate_release_exact_article_packet(
    runtime: V2ServingRuntime,
    rows: list[dict[str, Any]],
    *,
    query: str,
    temporal_scope: str,
    as_of: str,
    request_id: str,
    issue_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load and verify every release child for one explicit law + Article.

    The frozen V2 index stores child chunks but not the legacy PostgreSQL
    ``article_content`` parent. For an exact identifier request we therefore
    read the complete release-scoped child set from the manifest-bound SQLite
    database, verify identity/order/continuity/non-empty content, and only
    then expose one complete packet. Retrieval rank selects the legal identity;
    it never decides packet completeness.
    """

    plan = plan_exact_lookup(query)
    requested_pairs = tuple(plan.article_law_pairs or ())
    if not requested_pairs and plan.law_number and plan.article_number:
        requested_pairs = ((plan.law_number, plan.article_number),)
    if len(requested_pairs) != 1:
        packet = _incomplete_release_exact_packet(
            law_number=plan.law_number,
            article_number=plan.article_number,
            reason_code="exact_article_identity_not_unique",
        )
        return [], [packet]

    requested_law, requested_article = requested_pairs[0]
    normalized_law = normalize_exact_identifier(requested_law)
    normalized_article = normalize_exact_identifier(requested_article)
    matching = [
        dict(row)
        for row in rows
        if normalize_exact_identifier(row.get("law_number")) == normalized_law
        and normalize_exact_identifier(row.get("article_number"))
        == normalized_article
        and str(row.get("article_id") or "").strip()
    ]
    matching.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("chunk_id") or item.get("source_id") or ""),
        )
    )
    if not matching:
        packet = _incomplete_release_exact_packet(
            law_number=requested_law,
            article_number=requested_article,
            reason_code="exact_article_not_found_in_release_candidates",
        )
        return [], [packet]

    seed = matching[0]
    article_id = seed.get("article_id")
    document_id = seed.get("document_id")
    if str(seed.get("release_id") or "").startswith("admin-overlay:"):
        # Admin imports live in the additive Chroma overlay, not in the
        # manifest-bound SQLite release.  Treating every overlay exact-Article
        # request as unavailable made the public ask path return
        # ``cannot_verify`` even though the chunks and vectors were present.
        # PostgreSQL is the authoritative source for this mutable corpus, so
        # load the complete Article child set from the same SQL state used by
        # the serving-state checks.  The normal packet verifier still enforces
        # identity, order, parent coverage and non-empty chunks below.
        try:
            overlay_statement = text(
                """
                SELECT c.id AS chunk_id,
                       c.chunk_index,
                       c.heading AS structural_path,
                       c.heading AS chunk_heading,
                       c.content,
                       a.id AS article_id,
                       a.article_number,
                       a.content AS article_content,
                       d.id AS document_id,
                       d.title AS document_title,
                       d.law_number,
                       d.issuing_agency,
                       d.source_url,
                       d.status AS stored_status,
                       d.effective_date AS effective_from,
                       d.expired_date AS effective_to,
                       COALESCE(s.domain, '') AS domain_slug
                FROM legal_article_chunks c
                JOIN legal_articles a ON a.id = c.article_id
                JOIN legal_documents d ON d.id = a.document_id
                LEFT JOIN legal_search_scope s ON s.document_id = d.id
                WHERE d.id = :document_id
                  AND a.id = :article_id
                  AND a.status = 'active'
                  AND (
                      d.status = 'active'
                      OR (:historical AND d.status IN
                          ('archived', 'expired', 'historical', 'replaced'))
                  )
                  AND (d.effective_date IS NULL OR d.effective_date <= :as_of)
                  AND (d.expired_date IS NULL OR d.expired_date > :as_of)
                ORDER BY c.chunk_index, c.id
                """
            )
            with _get_overlay_engine().connect() as connection:
                overlay_children = [
                    dict(row)
                    for row in connection.execute(
                        overlay_statement,
                        {
                            "document_id": int(document_id),
                            "article_id": int(article_id),
                            "historical": temporal_scope == "historical",
                            "as_of": as_of,
                        },
                    ).mappings()
                ]
        except (TypeError, ValueError, SQLAlchemyError):
            overlay_children = []

        if not overlay_children:
            packet = _incomplete_release_exact_packet(
                law_number=requested_law,
                article_number=requested_article,
                reason_code="exact_article_overlay_children_not_found",
                document_id=document_id,
                article_id=article_id,
            )
            return [], [packet]

        overlay_count = len(overlay_children)
        children = [
            {
                **row,
                "chunk_revision_id": f"admin-overlay:{row['chunk_id']}",
                "exact_article_chunk_count": overlay_count,
                "document_serving_state": (
                    "current_retrievable"
                    if row.get("stored_status") == "active"
                    else "historical_only"
                ),
                "retrieval_source": "admin_overlay",
                "retrieval_sources": ["admin_overlay"],
                "release_id": f"admin-overlay:{INCREMENTAL_COLLECTION}",
            }
            for row in overlay_children
        ]
        packet = build_exact_article_serving_packet(children, query=query)
        if packet.get("status") != "complete":
            return [], [public_exact_article_packet(packet) or packet]
        attached = attach_exact_article_packet(
            list(packet.get("ordered_rows") or []), packet
        )
        output = [
            _public_result(
                row,
                temporal_scope=temporal_scope,
                request_id=request_id,
                issue_id=issue_id,
            )
            for row in attached
        ]
        for row in output:
            row["query_id"] = f"{issue_id}-exact-article"
            row["hydration_scope"] = "exact_article"
            row["structural_unit_status"] = "complete_article"
        return output, [public_exact_article_packet(packet) or packet]

    try:
        scope_sql, scope_params = runtime._scope_sql(
            temporal_scope=temporal_scope,
            as_of=as_of,
        )
        statement = (
            "SELECT * FROM chunks WHERE article_id = ? AND "
            f"{scope_sql} ORDER BY chunk_index, chunk_revision_id"
        )
        with runtime._db_guard():
            fetched = [
                dict(raw)
                for raw in runtime.db.execute(
                    statement,
                    [article_id, *scope_params],
                )
            ]
    except (AttributeError, TypeError, ValueError, sqlite3.Error):
        fetched = []

    allowlist_backend = str(
        getattr(runtime, "_allowlist_backend", "memory") or "memory"
    )
    allowed_ids = getattr(runtime, "_manifest_chunk_ids", None)
    children: list[dict[str, Any]] = []
    for raw in fetched:
        identifier = str(raw.get("chunk_revision_id") or "")
        if allowlist_backend == "memory" and (
            not allowed_ids or identifier not in allowed_ids
        ):
            continue
        if str(raw.get("document_id") or "") != str(document_id or ""):
            continue
        if normalize_exact_identifier(raw.get("law_number")) != normalized_law:
            continue
        if (
            normalize_exact_identifier(raw.get("article_number"))
            != normalized_article
        ):
            continue
        children.append(raw)

    if not children:
        packet = _incomplete_release_exact_packet(
            law_number=requested_law,
            article_number=requested_article,
            reason_code="exact_article_release_children_not_found",
            document_id=document_id,
            article_id=article_id,
        )
        return [], [packet]

    source_indexes: list[int] = []
    invalid_source_index = False
    for row in children:
        try:
            source_indexes.append(int(row.get("chunk_index")))
        except (TypeError, ValueError):
            invalid_source_index = True
    missing_source_indexes: list[int] = []
    if source_indexes and not invalid_source_index:
        missing_source_indexes = sorted(
            set(range(min(source_indexes), max(source_indexes) + 1))
            - set(source_indexes)
        )

    assembled_parent = "\n\n".join(
        str(row.get("content") or "").strip()
        for row in children
        if str(row.get("content") or "").strip()
    ).strip()
    expected_count = len(children)
    hydrated_children = [
        {
            **row,
            "chunk_id": row.get("chunk_revision_id"),
            # The frozen release stores a corpus-global source index. The
            # packet verifier requires Article-relative positions, so retain
            # the source value for audit and normalize only after checking
            # that the original source range is contiguous.
            "source_chunk_index": row.get("chunk_index"),
            "chunk_index": position,
            "article_content": assembled_parent,
            "exact_article_chunk_count": expected_count,
            "score": float(seed.get("score") or 0.0),
            "retrieval_sources": list(
                dict.fromkeys(
                    [
                        *list(seed.get("retrieval_sources") or []),
                        "release_exact_article",
                    ]
                )
            ),
        }
        for position, row in enumerate(children)
    ]
    packet = build_exact_article_serving_packet(
        hydrated_children,
        query=query,
    )
    if invalid_source_index or missing_source_indexes:
        packet["status"] = "incomplete"
        packet["reason_codes"] = list(
            dict.fromkeys(
                [
                    *list(packet.get("reason_codes") or []),
                    (
                        "invalid_source_chunk_index"
                        if invalid_source_index
                        else "missing_chunk_indexes"
                    ),
                ]
            )
        )
        packet["missing_chunk_indexes"] = missing_source_indexes
        packet["expected_chunk_count"] = (
            len(children) + len(missing_source_indexes)
        )
    packet["verification_basis"] = "release_scoped_complete_child_set"
    public_packet = public_exact_article_packet(packet) or {}
    public_packet["verification_basis"] = packet["verification_basis"]
    if packet.get("status") != "complete":
        return [], [public_packet]

    selected_ids = {
        str(value) for value in packet.get("selected_chunk_ids") or []
    }
    selected_children = [
        row
        for row in hydrated_children
        if not selected_ids or str(row.get("chunk_id") or "") in selected_ids
    ]
    attached = attach_exact_article_packet(selected_children, packet)
    output = [
        _public_result(
            row,
            temporal_scope=temporal_scope,
            request_id=request_id,
            issue_id=issue_id,
        )
        for row in attached
    ]
    for row in output:
        row["query_id"] = f"{issue_id}-exact-article"
        row["hydration_scope"] = "exact_article"
        row["structural_unit_status"] = "complete_article"
    return output, [public_packet]


def _apply_variant_fusion_scores(
    candidates: list[dict[str, Any]],
    *,
    raw_query_mode: bool,
    hybrid_rrf_enabled: bool,
) -> None:
    """Fuse query variants without changing any individual v6r26 ranking."""

    if not candidates or (not raw_query_mode and not hybrid_rrf_enabled):
        return
    max_rrf = max(float(item.get("variant_rrf_score") or 0.0) for item in candidates) or 1.0
    max_base = max(float(item.get("score") or 0.0) for item in candidates) or 1.0
    for item in candidates:
        normalized_rrf = float(item.get("variant_rrf_score") or 0.0) / max_rrf
        normalized_base = float(item.get("score") or 0.0) / max_base
        item["pre_variant_fusion_score"] = item.get("score")
        if raw_query_mode:
            item["score"] = round(0.80 * normalized_rrf + 0.20 * normalized_base, 6)
            item["ranking_strategy"] = "v6r26_weighted_variant_rrf"
        else:
            semantic_score = float(item.get("semantic_fallback_score") or 0.0)
            item["pre_batch_rerank_score"] = item.get("score")
            item["score"] = round(
                0.55 * normalized_rrf
                + 0.35 * semantic_score
                + 0.10 * normalized_base,
                6,
            )
            item["ranking_strategy"] = "hybrid_rrf_v3+legal_semantic_fallback_v1"


def _select_raw_structural_diversity(
    candidates: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    """Keep the strongest chunk per Article and at most three Articles/document."""

    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("chunk_id") or item.get("source_id") or ""),
        ),
    )
    selected: list[dict[str, Any]] = []
    seen_units: set[tuple[str, str]] = set()
    per_document: dict[str, int] = {}
    deferred: list[dict[str, Any]] = []
    for item in ordered:
        document_id = str(
            item.get("document_id") or item.get("law_number") or "unknown"
        )
        article_id = str(
            item.get("article_id") or item.get("article_number") or item.get("chunk_id")
        )
        unit = (document_id, article_id)
        if unit in seen_units:
            continue
        seen_units.add(unit)
        if per_document.get(document_id, 0) >= 3:
            deferred.append(item)
            continue
        selected.append(item)
        per_document[document_id] = per_document.get(document_id, 0) + 1
        if len(selected) >= limit:
            return selected
    for item in deferred:
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


_REQUESTED_ARTICLE_RE = re.compile(r"\bdieu\s+([0-9]+[a-z]?)\b", re.IGNORECASE)
_REQUESTED_CLAUSE_RE = re.compile(r"\bkhoan\s+([0-9]+[a-z]?)\b", re.IGNORECASE)
_REQUESTED_POINT_RE = re.compile(r"\bdiem\s+([a-zd])\b", re.IGNORECASE)


def _fold_structural(value: Any) -> str:
    return normalize_exact(str(value or "")).casefold().strip()


def _requested_structural_scope(
    query: str,
    *,
    article_number: Any,
) -> tuple[str, str | None, str | None]:
    """Resolve only explicit structural references in the current question.

    The resolver is deliberately conservative.  A bare article reference is
    an exact-Article request; Khoản/Điểm narrow it further.  If the retrieved
    row belongs to another Article, no structural scope is inferred for it.
    """

    folded_query = _fold_structural(query)
    article_match = _REQUESTED_ARTICLE_RE.search(folded_query)
    requested_article = _fold_structural(article_match.group(1)) if article_match else None
    if not requested_article or requested_article != _fold_structural(article_number):
        return "targeted_unit", None, None
    clause_match = _REQUESTED_CLAUSE_RE.search(folded_query)
    clause = _fold_structural(clause_match.group(1)) if clause_match else None
    point_match = _REQUESTED_POINT_RE.search(folded_query)
    point = _fold_structural(point_match.group(1)) if point_match else None
    if point:
        return "exact_point", clause, point
    if clause:
        return "exact_clause", clause, None
    return "exact_article", None, None


def _child_structure(child: Mapping[str, Any]) -> tuple[str | None, str | None, str]:
    heading = str(
        child.get("structural_path")
        or child.get("heading_full")
        or child.get("heading")
        or ""
    ).strip()
    parsed = parse_structural_path(heading)
    clause = _fold_structural(parsed.get("clause_number")) or None
    point = _fold_structural(parsed.get("point_number")) or None
    return clause, point, heading


def _relative_structural_heading(
    heading: str,
    *,
    article_number: Any,
    clause: str | None,
    point: str | None,
) -> str:
    """Render a path once, without repeating ``Điều → Chương → Mục``."""

    text = str(heading or "").strip()
    article = str(article_number or "").strip()
    # Persisted paths are normally ``Điều X > Khoản Y > Điểm z``.  Strip the
    # article prefix because the capsule emits the root once for the unit.
    if ">" in text:
        pieces = [part.strip() for part in text.split(">") if part.strip()]
        if pieces and _fold_structural(pieces[0]).startswith("dieu "):
            pieces = pieces[1:]
        text = " > ".join(pieces)
    if not text:
        if point:
            return f"Khoản {clause} > Điểm {point}" if clause else f"Điểm {point}"
        if clause:
            return f"Khoản {clause}"
        return f"Điều {article}" if article else ""
    return text


def _render_targeted_children(
    children: Sequence[Mapping[str, Any]],
    *,
    article_number: Any,
) -> tuple[str, int]:
    """Render selected children with one heading per structural unit."""

    ordered = sorted(
        (dict(child) for child in children),
        key=lambda item: (
            int(item.get("chunk_index") or 0),
            str(item.get("chunk_revision_id") or ""),
        ),
    )
    parts: list[str] = []
    seen_content: set[str] = set()
    seen_headings: set[str] = set()
    root = f"Điều {str(article_number).strip()}" if str(article_number or "").strip() else ""
    if root:
        parts.append(root)
    for child in ordered:
        clause, point, heading = _child_structure(child)
        relative = _relative_structural_heading(
            heading,
            article_number=article_number,
            clause=clause,
            point=point,
        )
        content = str(child.get("content") or "").strip()
        if not content:
            continue
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if checksum in seen_content:
            continue
        seen_content.add(checksum)
        block_parts: list[str] = []
        if relative and relative != root and relative not in seen_headings:
            block_parts.append(relative)
            seen_headings.add(relative)
        block_parts.append(content)
        parts.append("\n".join(block_parts))
    rendered = "\n\n".join(part for part in parts if part).strip()
    return rendered, len(rendered)


def _hydrate_targeted_structural_units(
    runtime: V2ServingRuntime,
    rows: list[dict[str, Any]],
    *,
    query: str,
    temporal_scope: str,
    as_of: str,
) -> list[dict[str, Any]]:
    """Hydrate only the matched legal unit and the minimum useful ancestors.

    Exact Article/Khoản/Điểm requests deliberately widen their scope.  Normal
    questions keep the matched clause/point (including chunks split within
    that unit) and an Article lead, never unrelated sibling clauses/points.
    """

    article_ids = sorted(
        {
            int(item.get("article_id"))
            for item in rows
            if str(item.get("article_id") or "").isdigit()
        }
    )
    if not article_ids:
        output: list[dict[str, Any]] = []
        for raw in rows:
            item = dict(raw)
            item.update(
                {
                    "hydration_scope": "raw_chunk",
                    "matched_unit": None,
                    "included_ancestors": [],
                    "unit_chars": len(str(item.get("content") or "")),
                    "structural_unit_status": "raw_chunk",
                }
            )
            output.append(item)
        return output
    scope_sql, scope_params = runtime._scope_sql(
        temporal_scope=temporal_scope,
        as_of=as_of,
    )
    placeholders = ",".join("?" for _ in article_ids)
    statement = (
        f"SELECT * FROM chunks WHERE article_id IN ({placeholders}) "
        f"AND {scope_sql} ORDER BY article_id, chunk_index"
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    with runtime._db_guard():
        for raw in runtime.db.execute(statement, [*article_ids, *scope_params]):
            item = dict(raw)
            identifier = str(item.get("chunk_revision_id") or "")
            if (
                runtime._allowlist_backend == "memory"
                and identifier not in runtime._manifest_chunk_ids
            ):
                continue
            grouped.setdefault(int(item.get("article_id") or 0), []).append(item)
    output: list[dict[str, Any]] = []
    emitted: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in rows:
        item = dict(raw)
        try:
            article_id = int(item.get("article_id") or 0)
        except (TypeError, ValueError):
            article_id = 0
        children = grouped.get(article_id, [])
        scope, target_clause, target_point = _requested_structural_scope(
            query,
            article_number=item.get("article_number"),
        )
        selected: list[dict[str, Any]] = []
        matched_clause: str | None = None
        matched_point: str | None = None
        matched_chunk_id = str(
            item.get("chunk_revision_id") or item.get("chunk_id") or ""
        )
        if children:
            parsed_children = [
                (child, *_child_structure(child)) for child in children
            ]
            if scope == "exact_article":
                selected = [child for child, _clause, _point, _heading in parsed_children]
            elif scope == "exact_clause":
                selected = [
                    child
                    for child, clause, _point, _heading in parsed_children
                    if clause == target_clause or (clause is None and _point is None)
                ]
            elif scope == "exact_point":
                selected = [
                    child
                    for child, clause, point, _heading in parsed_children
                    if point == target_point
                    and (target_clause is None or clause == target_clause)
                ]
                # Point chunks carry the clause introduction by contract; the
                # Article lead is the only additional ancestor needed.
                selected.extend(
                    child
                    for child, clause, point, _heading in parsed_children
                    if clause is None and point is None
                )
            else:
                matched = next(
                    (
                        (child, clause, point)
                        for child, clause, point, _heading in parsed_children
                        if str(child.get("chunk_revision_id") or "") == matched_chunk_id
                    ),
                    None,
                )
                if matched is None:
                    matched = next(
                        (
                            (child, clause, point)
                            for child, clause, point, _heading in parsed_children
                            if str(child.get("content") or "").strip()
                            == str(item.get("content") or "").strip()
                        ),
                        None,
                    )
                if matched is not None:
                    matched_child, matched_clause, matched_point = matched
                    # Include every shard of the same matched unit, not its
                    # siblings. This keeps a split Point/Khoản complete.
                    selected = [
                        child
                        for child, clause, point, _heading in parsed_children
                        if (
                            clause == matched_clause
                            and point == matched_point
                            and (matched_clause is not None or matched_point is not None)
                        )
                    ]
                    if not selected:
                        selected = [matched_child]
                    selected.extend(
                        child
                        for child, clause, point, _heading in parsed_children
                        if clause is None and point is None
                    )
        if not selected:
            # Legacy/unstructured chunks remain exactly as retrieved.
            item.update(
                {
                    "hydration_scope": "raw_chunk",
                    "matched_unit": None,
                    "included_ancestors": [],
                    "unit_chars": len(str(item.get("content") or "")),
                    "structural_unit_status": "raw_chunk",
                }
            )
            key = ("raw", matched_chunk_id or hashlib.sha256(str(item.get("content") or "").encode("utf-8")).hexdigest())
            emitted.setdefault(key, item)
            continue

        assembled, unit_chars = _render_targeted_children(
            selected,
            article_number=item.get("article_number"),
        )
        if not assembled:
            item.update(
                {
                    "hydration_scope": "raw_chunk",
                    "matched_unit": None,
                    "included_ancestors": [],
                    "unit_chars": len(str(item.get("content") or "")),
                    "structural_unit_status": "raw_chunk",
                }
            )
            emitted.setdefault(("raw", matched_chunk_id), item)
            continue
        if scope == "exact_article":
            matched_unit = f"Điều {item.get('article_number')}"
            status = "complete_article"
            ancestors: list[str] = []
        elif scope == "exact_clause":
            matched_unit = f"Điều {item.get('article_number')} > Khoản {target_clause}"
            status = "complete_targeted_clause"
            ancestors = [f"Điều {item.get('article_number')}"]
        elif scope == "exact_point":
            matched_unit = (
                f"Điều {item.get('article_number')} > Khoản {target_clause} > Điểm {target_point}"
                if target_clause
                else f"Điều {item.get('article_number')} > Điểm {target_point}"
            )
            status = "complete_targeted_point"
            ancestors = [f"Điều {item.get('article_number')}"] + (
                [f"Khoản {target_clause}"] if target_clause else []
            )
        else:
            matched_unit = (
                f"Điều {item.get('article_number')} > Khoản {matched_clause} > Điểm {matched_point}"
                if matched_point and matched_clause
                else f"Điều {item.get('article_number')} > Khoản {matched_clause}"
                if matched_clause
                else f"Điều {item.get('article_number')}"
            )
            status = "complete_targeted_unit"
            ancestors = [f"Điều {item.get('article_number')}"] + (
                [f"Khoản {matched_clause}"] if matched_clause else []
            )
        item.update(
            {
                "parent_context": assembled,
                "evidence_capsule": assembled,
                "parent_context_chars": unit_chars,
                "parent_context_original_chars": unit_chars,
                "parent_context_truncated": False,
                "parent_context_reason": "targeted_structural_hydration",
                "structural_unit_status": status,
                "hydration_scope": scope,
                "matched_unit": matched_unit,
                "included_ancestors": ancestors,
                "unit_chars": unit_chars,
            }
        )
        key = (
            str(item.get("document_id") or item.get("law_number") or ""),
            matched_unit,
        )
        existing = emitted.get(key)
        if existing is None:
            emitted[key] = item
        else:
            existing["score"] = max(
                float(existing.get("score") or 0.0),
                float(item.get("score") or 0.0),
            )
            existing["retrieval_sources"] = list(
                dict.fromkeys(
                    [
                        *list(existing.get("retrieval_sources") or []),
                        *list(item.get("retrieval_sources") or []),
                    ]
                )
            )
    output = list(emitted.values())
    output.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("chunk_revision_id") or item.get("chunk_id") or "")))
    return output


def _hydrate_complete_articles(
    runtime: V2ServingRuntime,
    rows: list[dict[str, Any]],
    *,
    temporal_scope: str,
    as_of: str,
) -> list[dict[str, Any]]:
    """Rollback-compatible exact-Article hydration wrapper."""

    return _hydrate_targeted_structural_units(
        runtime,
        rows,
        query="Điều " + str(rows[0].get("article_number") or "") if rows else "",
        temporal_scope=temporal_scope,
        as_of=as_of,
    )
    return output


def _hydrate_governing_procedure_articles(runtime, rows, *, facets, query, temporal_scope, as_of, request_id, issue_id):
    """Read at most two already-matched governing articles from the release.

    Conditions, exceptions to authorization, and dossier steps must not be
    reduced to a single child clause. This is local hydration inside the same
    batch, with the existing exact integrity/eligibility checks.
    """
    if not set(facets) & {"condition", "documents", "consent", "process", "deadline"}:
        return rows
    selected, attempted = {}, set()
    for row in rows:
        title = _context_fold(row.get("article_title") or row.get("chunk_heading") or "")
        if not any(term in title for term in ("dieu kien", "uy quyen", "trach nhiem dang ky", "ho so thu tuc")):
            continue
        key = (str(row.get("document_id") or ""), str(row.get("law_number") or ""), str(row.get("article_number") or ""))
        if not all(key) or key in attempted or len(attempted) >= 2:
            continue
        attempted.add(key)
        try:
            hydrated, packets = _hydrate_release_exact_article_packet(
                runtime, [row], query=f"Điều {key[2]} văn bản số {key[1]}",
                temporal_scope=temporal_scope, as_of=as_of, request_id=request_id, issue_id=issue_id,
            )
        except Exception as exc:
            logger.warning("procedure_article_hydration_unavailable type={}", type(exc).__name__)
            continue
        if (hydrated and any(p.get("status") == "complete" for p in packets)
                and len(str(hydrated[0].get("parent_context") or hydrated[0].get("content") or "")) <= 12000):
            selected[key] = {**hydrated[0], "clause_number": None, "point_number": None,
                             "hydration_reason": "governing_procedure_article"}
    output, emitted = [], set()
    for row in rows:
        key = (str(row.get("document_id") or ""), str(row.get("law_number") or ""), str(row.get("article_number") or ""))
        if key not in selected:
            output.append(row)
        elif key not in emitted:
            output.append(selected[key])
            emitted.add(key)
    return output


def _context_fold(value: Any) -> str:
    return " ".join(normalize_exact(str(value or "")).casefold().split())


def _request_context_hard_negative_v2(
    *, query: str, row: Mapping[str, Any], facets: list[str], subject_anchor: str | None
) -> str | None:
    """Apply request-local actor/topic contradictions after v6r26 retrieval."""

    folded_query = _context_fold(f"{query} {subject_anchor or ''}")
    content = _context_fold(
        " ".join(
            str(row.get(field) or "")
            for field in ("content", "chunk_heading", "article_title", "document_title", "issuing_agency")
        )
    )
    # A sanctions provision mentioning a registration office is not evidence
    # for the ordinary application procedure. Keep it when sanctions are
    # actually requested, including mixed compliance questions.
    penalty_terms = ("xu phat", "tien phat", "vi pham", "che tai")
    heading = _context_fold(" ".join(str(row.get(k) or "") for k in
                                      ("document_title", "article_title", "chunk_heading")))
    if (set(facets) & {"documents", "authority", "deadline", "process"}
            and "xu phat" in heading
            and not any(term in folded_query for term in penalty_terms)):
        return "sanction_not_application_procedure"
    if "chu tich ubnd phuong" in folded_query and "cong an" in content and (
        "khieu nai" in folded_query or "quyet dinh" in folded_query
    ):
        return "wrong_actor_authority"
    pension = "huu tri xa hoi" in folded_query
    death = any(marker in folded_query for marker in ("qua doi", "tu vong", "mai tang", "chet"))
    if pension and not death and "mai tang" in content:
        return "wrong_procedure_living_subject"
    residence_query = any(marker in folded_query for marker in ("csdl dan cu", "du lieu dan cu", "xac nhan cu tru"))
    residence_source = any(marker in content for marker in ("cu tru", "tam tru", "thuong tru", "dan cu", "cong an"))
    pension_source = any(marker in content for marker in ("huu tri", "an sinh", "bao tro"))
    if pension and residence_query and residence_source and not pension_source and any(
        facet in {"condition", "documents", "authority", "verification"} for facet in facets
    ):
        return "wrong_supporting_topic"
    return None


def _request_context_bonus_v2(
    *, query: str, row: Mapping[str, Any], facets: list[str], procedure_id: str | None, subject_anchor: str | None
) -> float:
    haystack = _context_fold(
        " ".join(
            str(row.get(field) or "")
            for field in ("content", "chunk_heading", "article_title", "document_title", "law_number", "procedure_id")
        )
    )
    bonus = 0.0
    if procedure_id and str(procedure_id).casefold() in haystack:
        bonus += 1.0
    anchor_terms = {term for term in _context_fold(subject_anchor).split() if len(term) > 2}
    bonus += min(0.35, 0.07 * len(anchor_terms & set(haystack.split())))
    facet_markers = {
        "documents": ("ho so", "giay to"), "authority": ("tham quyen", "co quan", "ubnd"),
        "deadline": ("thoi han", "ngay lam viec"), "condition": ("dieu kien",),
        "form": ("bieu mau", "to khai"), "legal_basis": ("can cu", "nghi dinh", "dieu"),
        "procedure": ("thu tuc", "trinh tu"), "complaint": ("khieu nai", "quyet dinh"),
    }
    bonus += 0.08 * sum(
        1 for facet in facets if any(marker in haystack for marker in facet_markers.get(facet, ()))
    )
    return min(1.6, bonus)


_RERANK_STOPWORDS = {
    "anh", "chi", "ban", "cua", "cho", "cac", "co", "duoc", "gi",
    "khong", "la", "mot", "nay", "thi", "toi", "va", "ve", "voi",
    "theo", "phai", "muon", "can", "hoi", "tai", "o", "khi",
}


def _rerank_terms(value: Any) -> list[str]:
    return [
        term
        for term in _context_fold(value).split()
        if len(term) >= 2 and term not in _RERANK_STOPWORDS
    ]


def _legal_semantic_score_v1(
    *,
    query: str,
    row: Mapping[str, Any],
    facets: list[str],
    subject_anchor: str | None,
) -> float:
    """Deterministic fallback rerank over the broad RRF candidate window.

    This is deliberately not labelled a learned semantic ranker. It supplies
    passage coverage, phrase, exact legal-identifier and facet signals when no
    checksum-approved local cross encoder is configured.
    """

    query_text = _context_fold(f"{query} {subject_anchor or ''}")
    passage = _context_fold(
        " ".join(
            str(row.get(field) or "")
            for field in (
                "content",
                "chunk_heading",
                "structural_path",
                "document_title",
                "law_number",
                "article_number",
            )
        )
    )
    query_terms = _rerank_terms(query_text)
    if not query_terms or not passage:
        return 0.0
    passage_terms = set(_rerank_terms(passage))
    unique_query_terms = list(dict.fromkeys(query_terms))
    coverage = sum(term in passage_terms for term in unique_query_terms) / max(
        1, len(unique_query_terms)
    )
    query_bigrams = {
        f"{left} {right}" for left, right in zip(query_terms, query_terms[1:])
    }
    phrase_coverage = (
        sum(phrase in passage for phrase in query_bigrams) / len(query_bigrams)
        if query_bigrams
        else 0.0
    )
    legal_identifiers = set(
        re.findall(r"\b\d{1,4}/\d{4}/[a-z0-9-]+\b", query_text)
    )
    exact_identifier = 1.0 if legal_identifiers.intersection(
        set(re.findall(r"\b\d{1,4}/\d{4}/[a-z0-9-]+\b", passage))
    ) else 0.0
    requested_articles = set(re.findall(r"\bdieu\s+(\d+[a-z]?)\b", query_text))
    passage_articles = set(re.findall(r"\bdieu\s+(\d+[a-z]?)\b", passage))
    projected_article = _context_fold(row.get("article_number"))
    if projected_article:
        passage_articles.add(projected_article)
    exact_article = 1.0 if requested_articles.intersection(passage_articles) else 0.0
    facet_markers = {
        "documents": ("ho so", "giay to", "thanh phan"),
        "authority": ("tham quyen", "co quan", "ubnd", "tiep nhan"),
        "deadline": ("thoi han", "ngay lam viec"),
        "processing_time": ("thoi han", "ngay lam viec"),
        "condition": ("dieu kien", "doi tuong"),
        "form": ("bieu mau", "mau so", "to khai"),
        "legal_basis": ("can cu", "nghi dinh", "thong tu", "dieu"),
        "procedure": ("thu tuc", "trinh tu", "ho so"),
        "next_action": ("chuyen", "tiep nhan", "xu ly"),
    }
    applicable = [facet for facet in facets if facet in facet_markers]
    facet_coverage = (
        sum(
            any(marker in passage for marker in facet_markers[facet])
            for facet in applicable
        )
        / len(applicable)
        if applicable
        else 0.0
    )
    return round(
        0.40 * coverage
        + 0.15 * phrase_coverage
        + 0.30 * exact_identifier
        + 0.10 * exact_article
        + 0.05 * facet_coverage,
        6,
    )


def _classify(
    query: str,
    *,
    domain: str | None,
    as_of: date,
    as_of_explicit: bool,
) -> dict[str, Any]:
    return classify_legal_query(
        query,
        requested_domain=domain,
        as_of=as_of,
        as_of_explicit=as_of_explicit,
        today=date.today(),
    )


def _apply_temporal_scope_contract(
    classification: Mapping[str, Any], requested_scope: str | None
) -> dict[str, Any]:
    """Apply an explicit benchmark/planner scope without weakening date gates."""

    output = dict(classification)
    scope = str(requested_scope or "").strip().casefold()
    if not scope:
        return output
    if scope not in {"current", "historical"}:
        raise ValueError("invalid_temporal_scope")
    output["temporal_scope"] = scope
    signals = dict(output.get("signals") or {})
    signals["temporal_scope_contract_explicit"] = True
    output["signals"] = signals
    return output


def _apply_retrieval_requirement_contract(
    classification: Mapping[str, Any], answer_required: bool | None, *, as_of: date
) -> dict[str, Any]:
    """Mirror the approved Kaggle case contract for the batch evaluator only."""

    output = dict(classification)
    if answer_required is None:
        return output
    output["retrieval_allowed"] = bool(answer_required)
    if answer_required:
        output["retrieval_as_of"] = as_of.isoformat()
    signals = dict(output.get("signals") or {})
    signals["answer_required_contract_explicit"] = True
    output["signals"] = signals
    return output


def _apply_shared_query_decision(
    classification: Mapping[str, Any],
    decision: Mapping[str, Any] | None,
    *,
    request_as_of: date,
    answer_required: bool | None = None,
) -> dict[str, Any]:
    """Bind retrieval to the already-computed LegalQueryDecisionV1.

    M4 is still used for intent/facet signals, but routing-critical fields are
    authoritative from the shared decision.  This prevents a facet query with
    an incidental keyword (or a different request ``as_of``) from changing the
    domain/time selected by the API router.
    """

    output = dict(classification)
    if not isinstance(decision, Mapping) or not decision:
        return output

    canonical_domain = str(decision.get("canonical_domain") or "").strip()
    if canonical_domain:
        output["domain"] = canonical_domain

    scope = str(decision.get("temporal_scope") or "").strip().casefold()
    if scope not in {"current", "historical", "unknown"}:
        raise ValueError("invalid_shared_temporal_scope")
    output["temporal_scope"] = scope

    legal_as_of = str(decision.get("legal_as_of") or "").strip()
    if legal_as_of:
        try:
            bound_as_of = date.fromisoformat(legal_as_of[:10])
        except ValueError as exc:
            raise ValueError("invalid_shared_legal_as_of") from exc
        output["temporal_reference"] = bound_as_of.isoformat()
        output["retrieval_as_of"] = bound_as_of.isoformat()
    elif scope == "current":
        output["temporal_reference"] = request_as_of.isoformat()
        output["retrieval_as_of"] = request_as_of.isoformat()
    else:
        output["temporal_reference"] = None
        output["retrieval_as_of"] = None

    # Unknown scope is deliberately fail-closed.  A shared current/historical
    # decision may override incidental temporal words in a facet query.
    output["retrieval_allowed"] = scope in {"current", "historical"} and bool(
        output.get("retrieval_as_of")
    )
    # Route/domain/time were already computed once by the API. A surface-form
    # classifier may still label a natural question as clarification (for
    # example "có làm tại phường được không?"). It may contribute intent
    # signals, but must not veto the shared legal-query decision.
    if output["retrieval_allowed"]:
        # Clarification belongs to the answer/procedure layer. It must not
        # suppress generally applicable legal retrieval for this issue. The
        # caller still retains clarifying_questions for the final response.
        output["requires_clarification"] = False
        if str(output.get("answer_type") or "").casefold() in {
            "clarification",
            "clarification_required",
        }:
            output["answer_type"] = "instructional"
        # The shared route is authoritative for retrieval.  M4 can classify a
        # cross-domain question as OUT_OF_SCOPE merely because its surface
        # vocabulary is a list of legal fields (for example an exact Article
        # request spanning several statutes).  A valid legal route must not be
        # vetoed by that secondary classifier; only an explicit out-of-scope
        # route remains a refusal.
        if str(decision.get("answer_route") or "").casefold() in {
            "exact_article",
            "procedure_form",
            "general_legal",
            "historical",
        }:
            output["intent"] = "LEGAL_QUERY"
            if str(output.get("answer_type") or "").casefold() == "refusal":
                output["answer_type"] = "instructional"
    # Benchmark/refusal contracts remain an explicit caller constraint; they
    # cannot be turned into retrieval merely because the shared route is
    # otherwise searchable.
    if answer_required is False:
        output["retrieval_allowed"] = False
        output["retrieval_as_of"] = None
        output["temporal_error_code"] = "ANSWER_RETRIEVAL_DISABLED"
    output["temporal_error_code"] = None if output["retrieval_allowed"] else (
        "SHARED_TEMPORAL_SCOPE_UNKNOWN"
        if scope == "unknown" and answer_required is not False
        else output.get("temporal_error_code")
    )
    signals = dict(output.get("signals") or {})
    signals["shared_query_decision"] = True
    signals["shared_decision_version"] = str(decision.get("version") or "")
    signals["temporal_reason"] = str(
        decision.get("temporal_reason") or signals.get("temporal_reason") or "shared_decision"
    )
    output["signals"] = signals
    return output


def _apply_raw_query_retrieval_contract(
    classification: Mapping[str, Any],
    *,
    requested_domain: str | None,
    request_as_of: date,
    as_of_explicit: bool,
) -> dict[str, Any]:
    """Make the raw user question searchable without a classifier veto.

    The M4 projection remains in diagnostics only. It cannot rewrite the
    query, infer a citizen domain, request clarification or disable retrieval.
    Explicit request dates still select the current/historical collection and
    an officer domain supplied by the authenticated API remains visible for
    downstream authorization.
    """

    output = dict(classification)
    output["domain"] = str(requested_domain or "unknown")
    output["intent"] = "RAW_RETRIEVAL"
    # A dossier facet is an evidence-shape hint, not a classifier veto. Keep
    # it so raw Direct RAG cannot silently bypass recovery validation and
    # drop sibling list points. Routing, query text and date remain raw.
    output["secondary_intents"] = (
        ["REQUIRED_DOCUMENTS"] if _requires_dossier_selection(classification) else []
    )
    output["answer_type"] = "instructional"
    output["requires_clarification"] = False
    output["retrieval_allowed"] = True
    output["temporal_scope"] = (
        "historical"
        if as_of_explicit and request_as_of < date.today()
        else "current"
    )
    output["temporal_reference"] = request_as_of.isoformat()
    output["retrieval_as_of"] = request_as_of.isoformat()
    output["temporal_error_code"] = None
    signals = dict(output.get("signals") or {})
    signals["raw_query_mode"] = True
    signals["classification_advisory_only"] = True
    signals["dossier_facet_preserved"] = bool(output["secondary_intents"])
    output["signals"] = signals
    return output


def _filter_results_by_domain(
    output: Mapping[str, Any],
    requested_domain: str | None,
) -> dict[str, Any]:
    """Fail closed for candidates carrying a conflicting legal domain.

    Rows without domain metadata remain eligible, but the trace reports them
    as unverifiable. This avoids treating missing metadata as proof while
    preserving compatibility with older approved corpus rows.
    """

    result = dict(output)
    expected = canonicalize_legal_domain(requested_domain)
    rows = [dict(item) for item in result.get("results") or []]
    if not expected or expected in {"unknown", "all", "general"}:
        return result
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    missing_domain_count = 0
    for row in rows:
        row_domain = canonicalize_legal_domain(
            row.get("canonical_domain")
            or row.get("domain_slug")
            or row.get("domain")
        )
        if row_domain and row_domain != expected:
            rejected.append(
                {
                    "source_id": str(
                        row.get("chunk_revision_id")
                        or row.get("chunk_id")
                        or row.get("source_id")
                        or ""
                    ),
                    "source_domain": row_domain,
                    "reason": "DOMAIN_SCOPE_MISMATCH",
                }
            )
            continue
        if not row_domain:
            missing_domain_count += 1
        kept.append(row)
    result["results"] = kept
    trace = dict(result.get("trace") or {})
    trace["domain_scope"] = {
        "requested_domain": expected,
        "input_count": len(rows),
        "kept_count": len(kept),
        "rejected_count": len(rejected),
        "missing_domain_count": missing_domain_count,
        "rejected": rejected[:20],
    }
    trace["final_evidence"] = kept
    result["trace"] = trace
    return result


def _search_one(
    runtime: V2ServingRuntime,
    *,
    query: str,
    domain: str | None,
    as_of: date,
    as_of_explicit: bool,
    temporal_scope: str | None = None,
    answer_required: bool | None = None,
    issue_groups: list[Mapping[str, Any]] | None = None,
    include_overlay: bool = True,
    direct_rag: bool = False,
    decision: Mapping[str, Any] | None = None,
    raw_query_mode: bool = False,
    audience: str | None = None,
    organization_unit_id: str | None = None,
    organization_routing_mode: str = "legacy",
    vector_top_k: int,
    lexical_top_k: int,
    final_evidence: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    classification = _classify(
        query,
        domain=domain,
        as_of=as_of,
        as_of_explicit=as_of_explicit,
    )
    classification = _apply_temporal_scope_contract(
        classification, temporal_scope
    )
    classification = _apply_retrieval_requirement_contract(
        classification, answer_required, as_of=as_of
    )
    classification = _apply_shared_query_decision(
        classification,
        decision,
        request_as_of=as_of,
        answer_required=answer_required,
    )
    if raw_query_mode:
        classification = _apply_raw_query_retrieval_contract(
            classification,
            requested_domain=domain,
            request_as_of=as_of,
            as_of_explicit=as_of_explicit,
        )
    groups = [dict(item) for item in (issue_groups or []) if isinstance(item, Mapping)]
    if len(groups) > 1:
        derived = _derive_issue_subqueries(query, len(groups))
        classification["issues"] = [
            {
                "issue_id": str(
                    group.get("group_id")
                    or group.get("issue_id")
                    or f"issue-{index + 1}"
                ),
                "query_text": str(group.get("sub_question") or derived[index] or query),
            }
            for index, group in enumerate(groups)
        ]
    if not classification["retrieval_allowed"]:
        return (
            {
                "status": "clarification_required",
                "results": [],
                "trace": {"query_classification": classification, "final_evidence": []},
            },
            classification,
        )
    # A citizen/admin domain is a routing hint, not an authorization boundary:
    # one question can legitimately require a central or cross-domain source
    # (and the corpus historically carries such rows under another slug).
    # Officer requests retain the strict domain boundary; their unit scope is
    # applied separately by the organization-routing layer.
    # A query's inferred domain is not an authorization boundary.  The
    # canonical unit assignment is the only trusted officer scope; when an
    # account has no assigned unit, keep central/shared and cross-domain
    # retrieval visible rather than accidentally filtering it by a stale
    # ``department``/legacy domain hint.  Upstream account authorization still
    # controls whether the officer may manage or receive work.
    enforced_domain = (
        str(classification.get("domain") or domain or "") or None
        if str(audience or "").casefold() == "officer" and organization_unit_id
        else None
    )
    if IS_PRODUCTION:
        # The approved r26 serving contract is k80/k80. Keep the public
        # request shape backward compatible, but do not let an older caller
        # silently downgrade the frozen production candidate window.
        vector_top_k = max(vector_top_k, DEFAULT_VECTOR_TOP_K)
        lexical_top_k = max(lexical_top_k, DEFAULT_LEXICAL_TOP_K)
    output = runtime.search(
        query=query,
        legal_as_of=classification["retrieval_as_of"],
        temporal_scope=classification["temporal_scope"],
        domain=enforced_domain,
        vector_top_k=vector_top_k,
        lexical_top_k=lexical_top_k,
        # Preserve the frozen k80/k80 candidate window until mutable serving
        # exclusions have been checked. Otherwise one excluded top hit can
        # crowd out a valid hit before the SQL check ever sees it.
        final_evidence=max(final_evidence, vector_top_k, lexical_top_k),
        rerank_top_n=final_evidence,
        fusion_strategy="rrf" if HYBRID_RRF_V3_ENABLED else "weighted",
        vector_weight=0.7,
        lexical_weight=0.3,
        reranker=None,
        parent_expansion=False,
        neighbor_expansion=False,
        candidate_profile=CANDIDATE_PROFILE,
        query_classification=classification,
    )
    output = _apply_document_serving_state(
        output, temporal_scope=classification["temporal_scope"],
        as_of=date.fromisoformat(classification["retrieval_as_of"]),
    )
    output = _filter_results_by_domain(output, enforced_domain)
    # Preserve the bounded ranked candidate window before the public evidence
    # limit/overlay is applied.  The evaluator and audit trace need to
    # distinguish candidate recall (Top-50) from the answer evidence (Top-10).
    # This is still after lifecycle and domain filtering, so excluded or
    # conflicting rows can never count as retrievable candidates.
    trace = dict(output.get("trace") or {})
    candidate_window = max(50, int(vector_top_k), int(lexical_top_k), int(final_evidence))
    # Runtime was asked for the full candidate window above, so at this point
    # ``output.results`` is the ranked pool *after* PostgreSQL lifecycle and
    # domain checks, not yet the public answer window.  Candidate@50 must use
    # this checked pool.  Reading ``trace.fusion_candidates`` here would count
    # staging/excluded documents that the authoritative serving projection
    # has already removed and make the gate optimistically wrong.
    # ``results`` is authoritative even when lifecycle filtering removed
    # every row.  Do not use truthiness here: falling back to the pre-check
    # fusion pool would re-introduce staging/historical documents into
    # Candidate@50 and could expose a forbidden source to the evaluator.
    if "results" in output:
        raw_candidate_pool = output.get("results") or []
    else:
        raw_candidate_pool = (
            trace.get("fusion_candidates")
            or trace.get("candidate_union")
            or []
        )
    trace["candidate_pool"] = [
        dict(item) for item in list(raw_candidate_pool)[:candidate_window]
    ]
    output["trace"] = trace
    if include_overlay:
        overlay = _overlay_rows(
            runtime,
            query=query,
            domain=enforced_domain,
            as_of=date.fromisoformat(classification["retrieval_as_of"]),
            limit=max(vector_top_k, final_evidence),
            temporal_scope=classification["temporal_scope"],
        )
        # Remove denied rows before top-k truncation so an excluded high-score
        # overlay cannot crowd out valid baseline candidates.
        overlay = _apply_document_serving_state(
            {"results": overlay}, temporal_scope=classification["temporal_scope"],
            as_of=date.fromisoformat(classification["retrieval_as_of"]),
        )["results"]
        output = _merge_overlay(
            output,
            overlay,
            # Dossier grouping needs the checked candidate window: cutting
            # ten rows here could discard point a before its sibling point b
            # identifies the required clause. The final selector below owns
            # the public bound after the repeated lifecycle/scope checks.
            limit=(
                len(output.get("results") or []) + len(overlay)
                if _requires_dossier_selection(classification)
                else final_evidence
            ),
            temporal_scope=classification["temporal_scope"],
            query=query,
            classification=classification,
        )
    output = _apply_document_serving_state(
        output, temporal_scope=classification["temporal_scope"],
        as_of=date.fromisoformat(classification["retrieval_as_of"]),
    )
    output = _filter_results_by_domain(output, enforced_domain)
    scoped_rows, organization_ranking = _apply_organization_unit_soft_boost(
        [dict(item) for item in output.get("results") or []],
        query=query,
        audience=audience,
        organization_unit_id=organization_unit_id,
        routing_mode=organization_routing_mode,
    )
    output["results"] = scoped_rows
    output.setdefault("trace", {})["organization_ranking"] = organization_ranking
    if _requires_dossier_selection(classification):
        from api.legal_dossier_evidence import select_dossier_evidence

        selected, dossier_trace = select_dossier_evidence(
            query,
            scoped_rows,
            candidate_rows=scoped_rows,
            limit=final_evidence,
            classification=classification,
        )
        output["results"] = selected
        output.setdefault("trace", {})["dossier_selection"] = dossier_trace
        output["trace"]["final_evidence"] = selected
    collection_name = str(
        getattr(getattr(runtime, "current_collection", None), "name", "") or ""
    )
    if (
        not direct_rag
        and vnext_shadow_enabled()
        and vnext_may_run_on_collection(collection_name)
    ):
        expand_used = {"done": False}

        def _expand_once(missing: tuple[str, ...]) -> list[dict[str, Any]]:
            if expand_used["done"]:
                return []
            expand_used["done"] = True
            facet_query = " ".join(str(item) for item in missing if item).strip() or query
            extra = runtime.search(
                query=facet_query,
                legal_as_of=classification["retrieval_as_of"],
                temporal_scope=classification["temporal_scope"],
                domain=enforced_domain,
                vector_top_k=min(vector_top_k, VNEXT_RERANK_WINDOW),
                lexical_top_k=min(lexical_top_k, VNEXT_RERANK_WINDOW),
                final_evidence=VNEXT_RERANK_WINDOW,
                rerank_top_n=VNEXT_RERANK_WINDOW,
                fusion_strategy="rrf" if HYBRID_RRF_V3_ENABLED else "weighted",
                vector_weight=0.7,
                lexical_weight=0.3,
                reranker=None,
                parent_expansion=False,
                neighbor_expansion=False,
                candidate_profile=CANDIDATE_PROFILE,
                query_classification=classification,
            )
            return list(extra.get("results") or [])

        output = apply_vnext_to_search_output(
            output,
            query=query,
            facets=tuple(str(item) for item in (classification.get("facets") or ())),
            organization_unit_id=organization_unit_id,
            audience=audience,
            as_of=classification["retrieval_as_of"],
            collection_name=collection_name,
            expand_fn=_expand_once,
        )
        # A vNext facet expansion can introduce rows that were not in the
        # first candidate set. Re-apply the same authoritative ACL after that
        # expansion so no post-processor can widen an officer's scope.
        post_vnext_rows, post_vnext_scope = _apply_organization_unit_soft_boost(
            [dict(item) for item in output.get("results") or []],
            query=query,
            audience=audience,
            organization_unit_id=organization_unit_id,
            routing_mode=organization_routing_mode,
        )
        output["results"] = post_vnext_rows
        output.setdefault("trace", {})["organization_scope_post_vnext"] = (
            post_vnext_scope
        )
    else:
        if vnext_shadow_enabled():
            output.setdefault("trace", {})
            if not isinstance(output["trace"], dict):
                output["trace"] = {}
            output["trace"]["vnext_shadow"] = {
                "applied": False,
                "reason": "refuses_live_or_pointer_collection",
                "collection": collection_name,
                "live_pointer_written": False,
            }
        output["results"] = list(output.get("results") or [])[:max(1, final_evidence)]
    output.setdefault("trace", {})["final_evidence"] = output["results"]
    # Keep candidate_pool stable: final_evidence is intentionally truncated
    # to the answer window and must not erase the Top-50 retrieval diagnostic.
    return output, classification


@app.on_event("startup")
def startup_event() -> None:
    get_runtime()


@app.on_event("shutdown")
def shutdown_event() -> None:
    global _runtime, _batch_reranker, _overlay_engine, _overlay_collection, _overlay_client
    global _overlay_snapshot, _overlay_snapshot_generation_ns
    if _runtime is not None:
        _runtime.close()
        _runtime = None
    _batch_reranker = None
    if _overlay_engine is not None:
        _overlay_engine.dispose()
        _overlay_engine = None
    _overlay_collection = None
    _overlay_client = None
    _overlay_snapshot = None
    _overlay_snapshot_generation_ns = None


@app.get("/health", response_model=None)
def health() -> Any:
    try:
        try:
            DocumentServingStateStore(_get_overlay_engine()).check_availability()
        except Exception as state_error:
            raise V2RuntimeError("admin_search_state_unavailable") from state_error
        runtime = get_runtime()
        serving = runtime.serving_manifest or {}
        active_pointer = _active_pointer_value()
        overlay = _get_overlay_collection(runtime, refresh=True)
        overlay_count = int(overlay.count()) if overlay is not None else 0
        return {
            "status": "healthy",
            "ready": True,
            "document_state_authority": "postgresql_document_and_scope",
            "mode": SERVER_MODE,
            "release_id": runtime.release_id,
            "manifest_version": serving.get("manifest_version"),
            "manifest_sha256": serving.get("manifest_sha256"),
            "current_collection": runtime.current_collection_name,
            "current_count": runtime.current_collection_count,
            "temporal_collection": runtime.temporal_collection_name,
            "temporal_count": runtime.temporal_collection_count,
            "exact_lexical_index": str(runtime.lexical_index_path),
            "active_pointer": active_pointer,
            "baseline_pointer_preserved": active_pointer == BASELINE_COLLECTION,
            "activation_performed": IS_PRODUCTION,
            "selected_m5": SELECTED_M5,
            "candidate_profile": CANDIDATE_PROFILE,
            "candidate_policy_manifest_bound": bool(
                CANDIDATE_PROFILE != R28_CANDIDATE_PROFILE
                or (
                    isinstance(serving.get("candidate_policy"), Mapping)
                    and str((serving.get("candidate_policy") or {}).get("profile") or "")
                    == R28_CANDIDATE_PROFILE
                )
            ),
            "retrieval_profile_frozen": IS_PRODUCTION,
            "retrieval_profile_change_policy": (
                "new_version_and_owner_approval_required" if IS_PRODUCTION else None
            ),
            "m5_quality_gate_waived_by_owner": IS_PRODUCTION,
            "quality_gate_status": (
                "owner_approved_with_documented_limitations"
                if IS_PRODUCTION
                else "not_applicable"
            ),
            "quality_assurance": {
                "independent_human_legal_qa_complete": False,
                "owner_approved_quality_deviation": IS_PRODUCTION,
                "freeze_contract": (
                    "config/retrieval-r26-production-freeze.json"
                    if IS_PRODUCTION
                    else None
                ),
            },
            "incremental_collection": INCREMENTAL_COLLECTION or None,
            "incremental_count": overlay_count,
            "merged_search_ready": bool(
                not INCREMENTAL_COLLECTION or overlay is not None
            ),
            "incremental_error": _overlay_error,
            "incremental_snapshot": {
                "path": str(OVERLAY_SNAPSHOT_PATH),
                "count": int((_get_overlay_snapshot() or {}).get("count") or 0),
                "generation_ns": _overlay_snapshot_generation_ns,
                "error": _overlay_snapshot_error,
            },
            "collection_counts_verified": runtime.collection_counts_verified,
            "chroma_collections_required": runtime.chroma_collections_required,
            "chroma_collections_available": runtime.chroma_collections_available,
            "embedding_device": runtime.embedding_device,
            "embedding_dtype": runtime.embedding_dtype,
            "model_fingerprint": runtime.model_fingerprint,
            "embedding_recipe_fingerprint": runtime.embedding_recipe_fingerprint,
            "embedding_requested_device": runtime.embedding_requested_device,
            "embedding_fallback_reason": runtime.embedding_fallback_reason,
            "embedding_encode_concurrency": int(
                getattr(runtime.query_encoder, "encode_concurrency", 1)
            ),
            "embedding_torch_threads": int(
                getattr(__import__("torch"), "get_num_threads")()
            ),
            "embedding_batching": (
                runtime.query_encoder.stats().get("batching", {"enabled": False})
                if callable(getattr(runtime.query_encoder, "stats", None))
                else {"enabled": False}
            ),
            "warmup_ms": runtime.warmup_ms,
            "ann_prewarm_enabled": bool(
                getattr(runtime, "_ann_prewarm_enabled", False)
            ),
            "ann_prewarm_ms": runtime.ann_prewarm_ms,
            "ann_prewarm_queries": runtime.ann_prewarm_queries,
            "vector_cache": runtime.vector_cache_stats(),
            "execution_cache": runtime.execution_cache_stats(),
            "vector_backend": runtime.vector_backend,
            "exact_query_only": bool(getattr(runtime, "exact_query_only", False)),
            "exact_vector_index": (
                runtime.exact_vector_index.stats()
                if runtime.exact_vector_index is not None
                else None
            ),
            "ann_fetch_multiplier": runtime.ann_fetch_multiplier,
            "ann_batching": runtime.ann_batch_stats(),
            "vnext_shadow_enabled": vnext_shadow_enabled(),
            "vnext_shadow_applied": bool(
                vnext_shadow_enabled()
                and vnext_may_run_on_collection(
                    runtime.current_collection_name
                )
            ),
            "live_pointer_written": False,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "ready": False, "reason": str(exc)},
        )


@app.get("/management/documents/{doc_id}/vector-membership")
def live_document_vector_membership(doc_id: str) -> dict[str, Any]:
    """Return content-free vector membership from the live serving process."""

    try:
        return _live_document_vector_membership(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_document_id"},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "document_not_found"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "live_vector_membership_unavailable"},
        ) from exc


@app.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    try:
        output, classification = _search_one(
            get_runtime(),
            query=request.query,
            domain=request.domain,
            as_of=request.as_of,
            as_of_explicit=request.as_of_explicit,
            temporal_scope=request.temporal_scope,
            decision=request.query_decision,
            include_overlay=request.include_overlay,
            answer_required=request.answer_required,
            issue_groups=request.issue_groups,
            audience=request.audience,
            organization_unit_id=request.organization_unit_id,
            organization_routing_mode=request.organization_routing_mode,
            vector_top_k=request.candidate_count,
            lexical_top_k=request.lexical_candidate_count,
            final_evidence=request.top_k or request.limit,
        )
    except (V2RuntimeError, V3ServingManifestError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    results = [
        _public_result(item, temporal_scope=classification["temporal_scope"])
        for item in output.get("results") or []
    ]
    decision_route = (
        request.query_decision.get("answer_route")
        if isinstance(request.query_decision, Mapping)
        else None
    )
    packet = output.get("evidence_packet") if isinstance(output.get("evidence_packet"), dict) else None
    return {
        "status": output.get("status", "ok"),
        "query": request.query,
        "query_route": decision_route or route_legal_answer(request.query).answer_route,
        "query_classification": classification,
        "temporal_scope": classification["temporal_scope"],
        "results_count": len(results),
        "results": results,
        "evidence_packet": packet,
        "trace": output.get("trace") or {},
        # Content-free observability metadata; the legal answer/evidence
        # payload remains backward compatible.
        "timing_ms": output.get("timing_ms")
        or (output.get("trace") or {}).get("stage_latency_ms")
        or {},
        "release_id": output.get("release_id") or get_runtime().release_id,
    }


_DIRECT_SEARCH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="direct-retrieval")


def _direct_batch_outputs(runtime, raw_issues, *, payload, query_decision,
                          decision_domain, decision_scope, request_as_of, as_of_explicit):
    """Submit independent searches together, so the existing encoder batcher
    can batch them. Merge order and final eligibility passes remain unchanged.
    Return completed work at the budget boundary; cancel queued work.
    """
    pending = {}
    keys = {}
    work = []
    for index, raw in enumerate(raw_issues, 1):
        issue = dict(raw) if isinstance(raw, Mapping) else {}
        specs = [dict(q) for q in (issue.get("queries") or [])[:4]
                 if isinstance(q, Mapping) and str(q.get("query") or "").strip()]
        if not specs and str(issue.get("query") or "").strip():
            specs = [{"query": issue["query"]}]
        domain = str(issue.get("domain") or "").strip() or decision_domain
        decision = dict(query_decision) if isinstance(query_decision, Mapping) else None
        if decision is not None and domain:
            decision["canonical_domain"] = canonicalize_legal_domain(domain) or domain
        for variant, spec in enumerate(specs, 1):
            kwargs = dict(
                query=str(spec["query"]).strip(), domain=domain,
                as_of=request_as_of, as_of_explicit=as_of_explicit,
                temporal_scope=decision_scope or str(issue.get("temporal_scope") or "") or None,
                answer_required=bool(issue["answer_required"]) if "answer_required" in issue else None,
                issue_groups=list(issue.get("issue_groups") or []) if isinstance(issue.get("issue_groups"), list) else None,
                include_overlay=bool(payload.get("include_overlay", True)), direct_rag=True,
                decision=decision, raw_query_mode=bool(payload.get("raw_query_mode")),
                vector_top_k=DEFAULT_VECTOR_TOP_K, lexical_top_k=DEFAULT_LEXICAL_TOP_K,
                final_evidence=RAW_RETRIEVAL_CANDIDATE_POOL if payload.get("raw_query_mode") else BATCH_RERANK_CANDIDATE_POOL if HYBRID_RRF_V3_ENABLED else 10,
            )
            identity = json.dumps(kwargs, sort_keys=True, default=str)
            keys[(index, variant)] = identity
            work.append((variant, index, identity, kwargs))
    # Reserve each issue's original query before spending slots on variants.
    for _, _, identity, kwargs in sorted(work, key=lambda item: item[:2]):
        if identity not in pending and len(pending) < max(16, len(raw_issues)):
            pending[identity] = _DIRECT_SEARCH_POOL.submit(_search_one, runtime, **kwargs)
    done, unfinished = wait(list(pending.values()), timeout=14.0) if pending else (set(), set())
    for future in unfinished:
        future.cancel()
    results = {}
    for key, identity in keys.items():
        future = pending.get(identity)
        if future in done:
            try:
                results[key] = future.result()
                continue
            except Exception as exc:
                logger.warning("direct_batch_variant_failed type={}", type(exc).__name__)
        results[key] = ({"status": "partial_timeout", "results": [], "trace": {}}, {})
    return results


@app.post("/search/batch")
def search_batch(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    runtime = get_runtime()
    request_id = str(payload.get("request_id") or "batch")
    try:
        request_as_of = date.fromisoformat(str(payload.get("as_of") or date.today().isoformat())[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_as_of") from exc
    as_of_explicit = bool(payload.get("as_of_explicit"))
    diagnostics_enabled = bool(payload.get("diagnostics"))
    benchmark_contract = bool(payload.get("benchmark_contract"))
    raw_query_mode = bool(payload.get("raw_query_mode"))
    audience = str(payload.get("audience") or "citizen").strip().casefold()
    if audience not in {"citizen", "officer", "admin"}:
        audience = "citizen"
    organization_unit_id = str(payload.get("organization_unit_id") or "").strip() or None
    organization_routing_mode = str(
        payload.get("organization_routing_mode") or "legacy"
    ).strip().casefold()
    if organization_routing_mode not in {"legacy", "shadow", "hybrid", "unit_primary"}:
        organization_routing_mode = "legacy"
    query_decision = payload.get("query_decision")
    decision_domain = (
        str(query_decision.get("canonical_domain") or "").strip() or None
        if isinstance(query_decision, Mapping)
        else None
    )
    decision_scope = (
        str(query_decision.get("temporal_scope") or "").strip() or None
        if isinstance(query_decision, Mapping)
        else None
    )
    decision_procedure_id = (
        str(query_decision.get("procedure_candidate") or "").strip() or None
        if isinstance(query_decision, Mapping)
        else None
    )
    raw_issues = payload.get("issues") or []
    if not isinstance(raw_issues, list):
        raise HTTPException(status_code=422, detail="issues_must_be_array")

    parallel_outputs = _direct_batch_outputs(
        runtime, raw_issues, payload=payload, query_decision=query_decision,
        decision_domain=decision_domain, decision_scope=decision_scope,
        request_as_of=request_as_of, as_of_explicit=as_of_explicit,
    ) if payload.get("direct_rag") and not benchmark_contract else None

    issue_payloads: list[dict[str, Any]] = []
    context_results: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    hydrate_total_ms = 0.0
    for index, raw_issue in enumerate(raw_issues, start=1):
        issue = dict(raw_issue) if isinstance(raw_issue, Mapping) else {}
        issue_id = str(issue.get("issue_id") or f"issue-{index}")
        query = str(issue.get("query") or "").strip()
        if not query:
            queries = issue.get("queries") or []
            if queries and isinstance(queries[0], Mapping):
                query = str(queries[0].get("query") or "").strip()
        if not query:
            # Keep the clarification-required stub shape aligned with the
            # successful issue payload so the post-batch serving-state pass
            # never KeyErrors on query_classification / ranking / packets.
            issue_payloads.append(
                {
                    "issue_id": issue_id,
                    "query": "",
                    "queries": [],
                    "results": [],
                    "status": "clarification_required",
                    "query_classification": {
                        "issue_id": issue_id,
                        "domain": decision_domain or "",
                        "intent": "CLARIFICATION_REQUIRED",
                        "temporal_scope": decision_scope or "current",
                        "retrieval_as_of": request_as_of.isoformat(),
                    },
                    "ranking": {"final_count": 0},
                    "exact_article_packets": [],
                }
            )
            continue

        issue_started = time.perf_counter()
        raw_queries = issue.get("queries") or []
        variant_specs = [
            dict(item)
            for item in raw_queries[:4]
            if isinstance(item, Mapping) and str(item.get("query") or "").strip()
        ]
        if not variant_specs:
            variant_specs = [{"query": query, "query_type": "raw", "weight": 1.0}]
        variant_queries = [str(item.get("query") or "").strip() for item in variant_specs]
        merged: dict[str, dict[str, Any]] = {}
        variant_diagnostics: list[dict[str, Any]] = []
        context_filtered: list[dict[str, str]] = []
        classifications: list[dict[str, Any]] = []
        output: dict[str, Any] = {"status": "ok", "results": [], "trace": {}}
        for variant_index, variant_spec in enumerate(variant_specs, start=1):
            variant_query = str(variant_spec.get("query") or "").strip()
            try:
                variant_weight = min(
                    1.0,
                    max(0.70, float(variant_spec.get("weight") or 1.0)),
                )
            except (TypeError, ValueError):
                variant_weight = 1.0
            issue_domain = str(issue.get("domain") or "").strip() or decision_domain
            issue_decision = (
                dict(query_decision)
                if isinstance(query_decision, Mapping)
                else None
            )
            if issue_decision is not None and issue_domain:
                issue_decision["canonical_domain"] = (
                    canonicalize_legal_domain(issue_domain) or issue_domain
                )
            variant_output, variant_classification = parallel_outputs[(index, variant_index)] if parallel_outputs is not None else _search_one(
                runtime,
                query=variant_query,
                domain=issue_domain,
                as_of=request_as_of,
                as_of_explicit=as_of_explicit,
                temporal_scope=decision_scope or str(issue.get("temporal_scope") or "") or None,
                answer_required=(
                    bool(issue.get("answer_required"))
                    if "answer_required" in issue
                    else None
                ),
                issue_groups=(
                    list(issue.get("issue_groups") or [])
                    if isinstance(issue.get("issue_groups"), list)
                    else None
                ),
                include_overlay=(
                    bool(payload.get("include_overlay", True))
                    and not benchmark_contract
                ),
                direct_rag=bool(payload.get("direct_rag")),
                decision=issue_decision,
                raw_query_mode=raw_query_mode,
                vector_top_k=DEFAULT_VECTOR_TOP_K,
                lexical_top_k=DEFAULT_LEXICAL_TOP_K,
                # Retrieve broadly before the batch-level semantic pass. The
                # raw path always keeps the frozen v6r26 Top 50 funnel even
                # when the optional batch semantic reranker is disabled.
                final_evidence=(
                    RAW_RETRIEVAL_CANDIDATE_POOL
                    if raw_query_mode
                    else BATCH_RERANK_CANDIDATE_POOL
                    if HYBRID_RRF_V3_ENABLED
                    else 10
                ),
            )
            classifications.append(dict(variant_classification))
            output["status"] = variant_output.get("status", output["status"])
            variant_results = list(variant_output.get("results") or [])
            for variant_rank, item in enumerate(variant_results, start=1):
                public = _public_result(
                    item,
                    temporal_scope=variant_classification["temporal_scope"],
                    request_id=request_id,
                    issue_id=issue_id,
                )
                public["query_id"] = f"{issue_id}-facet-{variant_index}"
                public["variant_query_type"] = str(
                    variant_spec.get("query_type") or "raw"
                )
                public["variant_weight"] = variant_weight
                hard_negative_reason = (
                    None
                    if raw_query_mode
                    else _request_context_hard_negative_v2(
                        query=variant_query,
                        row=public,
                        facets=[str(value) for value in issue.get("facets") or []],
                        subject_anchor=str(issue.get("subject_anchor") or "") or None,
                    )
                )
                if hard_negative_reason:
                    context_filtered.append({
                        "source_id": str(public.get("source_id") or public.get("chunk_id") or ""),
                        "reason": hard_negative_reason,
                    })
                    continue
                request_bonus = (
                    0.0
                    if raw_query_mode
                    else _request_context_bonus_v2(
                        query=variant_query,
                        row=public,
                        facets=[str(value) for value in issue.get("facets") or []],
                        procedure_id=(
                            str(issue.get("procedure_id") or "").strip()
                            or decision_procedure_id
                        ),
                        subject_anchor=str(issue.get("subject_anchor") or "") or None,
                    )
                )
                if request_bonus:
                    public["score"] = round(float(public.get("score") or 0.0) + request_bonus, 6)
                    public["request_context_bonus"] = round(request_bonus, 6)
                public["semantic_fallback_score"] = (
                    0.0
                    if raw_query_mode
                    else _legal_semantic_score_v1(
                        query=variant_query,
                        row=public,
                        facets=[str(value) for value in issue.get("facets") or []],
                        subject_anchor=str(issue.get("subject_anchor") or "") or None,
                    )
                )
                identity = str(public.get("chunk_id") or public.get("source_id") or "")
                if not identity:
                    continue
                previous = merged.get(identity)
                reciprocal_rank = variant_weight / (RRF_RANK_CONSTANT + variant_rank)
                if previous is None:
                    public["variant_rrf_score"] = reciprocal_rank
                    public["variant_hits"] = 1
                    public["variant_ranks"] = {
                        f"{issue_id}-facet-{variant_index}": variant_rank
                    }
                    merged[identity] = public
                else:
                    previous["variant_rrf_score"] = float(
                        previous.get("variant_rrf_score") or 0.0
                    ) + reciprocal_rank
                    previous["variant_hits"] = int(previous.get("variant_hits") or 1) + 1
                    previous.setdefault("variant_ranks", {})[
                        f"{issue_id}-facet-{variant_index}"
                    ] = variant_rank
                    if float(public.get("score") or 0) > float(previous.get("score") or 0):
                        for key, value in public.items():
                            if key not in {"variant_rrf_score", "variant_hits", "variant_ranks"}:
                                previous[key] = value
            if diagnostics_enabled and variant_output.get("trace"):
                variant_diagnostics.append({
                    "query_id": f"{issue_id}-facet-{variant_index}",
                    "query": variant_query,
                    "diagnostics": _content_free_retrieval_diagnostics(variant_output.get("trace") or {}),
                })
        candidates = list(merged.values())
        _apply_variant_fusion_scores(
            candidates,
            raw_query_mode=raw_query_mode,
            hybrid_rrf_enabled=HYBRID_RRF_V3_ENABLED,
        )
        # v6r26 supplies the broad hybrid candidate set.  An optional
        # Vietnamese cross-encoder only reorders that set; it never performs
        # a second retrieval, drops a source for policy reasons, or changes
        # the active release.  Exact-article requests retain their structural
        # order and therefore bypass semantic reranking.
        reranker_status: dict[str, Any]
        if bool(issue.get("exact_article")):
            reranker_status = {
                "mode": "exact_article_bypass",
                "reason_code": "structural_packet_order_preserved",
                "version": "exact-article-v1",
                "degraded": False,
                "candidate_count": len(candidates),
                "scored_count": 0,
                "latency_ms": 0.0,
            }
        else:
            batch_reranker = get_batch_reranker()
            rerank_outcome = batch_reranker.rerank(
                query,
                candidates,
                top_n=min(BATCH_RERANK_CANDIDATE_POOL, len(candidates))
                if candidates
                else 0,
            )
            candidates = list(rerank_outcome.candidates)
            reranker_status = rerank_outcome.public_status()
            reranker_status["model_label"] = str(
                getattr(batch_reranker, "model_label", "configured-reranker")
            )
        candidates, organization_ranking = _apply_organization_unit_soft_boost(
            [dict(item) for item in candidates],
            query=query,
            audience=audience,
            organization_unit_id=organization_unit_id,
            routing_mode=organization_routing_mode,
        )
        # A timed-out parallel variant has no classifier output. Preserve the
        # request contract and any completed sibling instead of failing the
        # entire batch while serializing classification['domain'].
        classification = {
            "temporal_scope": decision_scope or "current",
            "domain": issue.get("domain") or decision_domain or "unknown",
            "intent": issue.get("intent") or "unknown",
            "retrieval_as_of": request_as_of.isoformat(),
            **next((c for c in classifications if c), {}),
        }
        dossier_selection: dict[str, Any] = {"enabled": False}
        if _requires_dossier_selection(classification) and not bool(issue.get("exact_article")):
            from api.legal_dossier_evidence import select_dossier_evidence

            # Reranking may reorder points, but must not reduce an identified
            # dossier list to one chunk/article before the answer sees it.
            formatted, dossier_selection = select_dossier_evidence(
                query,
                candidates,
                candidate_rows=candidates,
                classification=classification,
                limit=RAW_RETRIEVAL_FINAL_COUNT if raw_query_mode else 10,
            )
        else:
            formatted = (
                _select_raw_structural_diversity(
                    candidates,
                    limit=(
                        RAW_RETRIEVAL_EXACT_FINAL_COUNT
                        if bool(issue.get("exact_article"))
                        else RAW_RETRIEVAL_FINAL_COUNT
                    ),
                )
                if raw_query_mode
                else sorted(
                    candidates,
                    key=lambda item: (-float(item.get("score") or 0), str(item.get("chunk_id") or "")),
                )[:10]
            )
        hydrate_started = time.perf_counter()
        exact_article_packets: list[dict[str, Any]] = []
        if bool(issue.get("exact_article")):
            formatted, exact_article_packets = _hydrate_release_exact_article_packet(
                runtime,
                formatted,
                query=query,
                temporal_scope=str(classification.get("temporal_scope") or "current"),
                as_of=str(
                    classification.get("retrieval_as_of")
                    or request_as_of.isoformat()
                ),
                request_id=request_id,
                issue_id=issue_id,
            )
        elif raw_query_mode:
            # Direct serving passes the ranked v6r26 chunks through unchanged.
            # Structural/article hydration belonged to the old evidence
            # renderer; it expanded unrelated siblings and made the model
            # packet noisy. The helper remains available for rollback tests,
            # but is not used by the active raw path.
            formatted = [
                {
                    **dict(row),
                    "hydration_scope": "raw_chunk",
                    "matched_unit": None,
                    "included_ancestors": [],
                    "unit_chars": len(str(row.get("content") or "")),
                    "structural_unit_status": "raw_chunk",
                }
                for row in formatted
            ]
        elif payload.get("direct_rag"):
            formatted = _hydrate_governing_procedure_articles(
                runtime, formatted, facets=issue.get("facets") or [], query=query,
                temporal_scope=str(classification.get("temporal_scope") or "current"),
                as_of=str(classification.get("retrieval_as_of") or request_as_of.isoformat()),
                request_id=request_id, issue_id=issue_id,
            )
        # Article expansion and a slow rerank may outlive an Admin decision.
        # Recheck the final hydrated evidence, not only its original seeds.
        formatted = _apply_document_serving_state(
            {"results": formatted},
            temporal_scope=str(classification.get("temporal_scope") or "current"),
            as_of=date.fromisoformat(str(classification.get("retrieval_as_of") or request_as_of.isoformat())),
        )["results"]
        formatted, post_hydration_scope = _apply_organization_unit_soft_boost(
            [dict(item) for item in formatted],
            query=query,
            audience=audience,
            organization_unit_id=organization_unit_id,
            routing_mode=organization_routing_mode,
        )
        organization_ranking["post_hydration_rejected_count"] = int(
            post_hydration_scope.get("rejected_count") or 0
        )
        kept_document_ids = {str(row.get("document_id")) for row in formatted}
        exact_article_packets = [
            packet for packet in exact_article_packets
            if str(packet.get("document_id")) in kept_document_ids or packet.get("status") != "complete"
        ]
        hydrate_ms = round((time.perf_counter() - hydrate_started) * 1000, 3)
        hydrate_total_ms += hydrate_ms
        for row in formatted:
            chunk_id = str(row.get("chunk_id") or "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                context_results.append(row)
        elapsed_ms = round((time.perf_counter() - issue_started) * 1000, 3)
        issue_payload = {
                "issue_id": issue_id,
                "query": query,
                "queries": [
                    {
                        "query_id": f"{issue_id}-facet-{query_index}",
                        "query": variant_query,
                        "result_count": sum(
                            1
                            for item in formatted
                            if item.get("query_id") == f"{issue_id}-facet-{query_index}"
                        ),
                        "status": output.get("status", "ok"),
                    }
                    for query_index, variant_query in enumerate(variant_queries, start=1)
                ],
                "domain": classification["domain"],
                "intent": classification["intent"],
                "query_classification": classification,
                "query_classifications": classifications,
                "query_decision": query_decision,
                # Content-free normalizer telemetry is carried through the
                # batch so the API can compare raw/variant retrieval without
                # logging either query text or conversation contents.
                "normalization_trace": (
                    dict(issue.get("normalization_trace"))
                    if isinstance(issue.get("normalization_trace"), Mapping)
                    else None
                ),
                "facets": list(issue.get("facets") or []),
                "subject_anchor": issue.get("subject_anchor"),
                "procedure_id": issue.get("procedure_id"),
                "results": formatted,
                "dossier_selection": dossier_selection,
                "request_context": {
                    "enabled": (
                        False
                        if raw_query_mode
                        else bool(issue.get("facets") or issue.get("procedure_id") or issue.get("subject_anchor"))
                    ),
                    "filtered_count": len(context_filtered),
                    "filtered_reasons": context_filtered[:20],
                },
                "ranking": {
                    "strategy": (
                        "v6r26_raw_hybrid_rrf"
                        if raw_query_mode
                        else "hybrid_rrf_v3+legal_semantic_fallback_v1"
                        if HYBRID_RRF_V3_ENABLED
                        else "v6r26_weighted_baseline"
                    ),
                    "candidate_pool": (
                        RAW_RETRIEVAL_CANDIDATE_POOL
                        if raw_query_mode
                        else BATCH_RERANK_CANDIDATE_POOL
                        if HYBRID_RRF_V3_ENABLED
                        else 10
                    ),
                    "organization_unit": organization_ranking,
                    "vector_top_k": DEFAULT_VECTOR_TOP_K,
                    "lexical_top_k": DEFAULT_LEXICAL_TOP_K,
                    "merged_limit": (
                        RAW_RETRIEVAL_EXACT_FINAL_COUNT
                        if raw_query_mode and bool(issue.get("exact_article"))
                        else RAW_RETRIEVAL_FINAL_COUNT
                        if raw_query_mode
                        else 10
                    ),
                    "final_count": len(formatted),
                    "reranker": reranker_status,
                },
                "exact_article_packets": exact_article_packets,
                "validity_sync": {
                    "mode": "manifest_v3",
                    "filtered_count": 0,
                    "filtered_reasons": {},
                    "warning_count": 0,
                },
                "timing_ms": {
                    "query_total": elapsed_ms,
                    "hydrate": hydrate_ms,
                },
                "status": output.get("status", "ok"),
                "raw_query_mode": raw_query_mode,
            }
        if diagnostics_enabled:
            issue_payload["retrieval_diagnostics"] = variant_diagnostics
        issue_payloads.append(issue_payload)

    # One final SQL snapshot covers all issues. A decision during a later
    # issue must not leave revoked evidence from an earlier issue in context.
    final_ids = sorted({canonical_document_id(row["document_id"]) for row in context_results})
    final_states = _load_document_serving_states(final_ids) if final_ids else {}
    context_results = []
    seen_chunks = set()
    for issue_payload in issue_payloads:
        # Defensive defaults: empty-query stubs (and any future sparse payload)
        # must never raise KeyError on query_classification.
        raw_classification = issue_payload.get("query_classification")
        if isinstance(raw_classification, Mapping):
            classification = dict(raw_classification)
        else:
            classification = {}
        issue_payload["query_classification"] = classification
        checked = _apply_document_serving_state(
            {"results": list(issue_payload.get("results") or [])},
            temporal_scope=str(classification.get("temporal_scope") or "current"),
            as_of=date.fromisoformat(str(classification.get("retrieval_as_of") or request_as_of.isoformat())),
            known_states=final_states,
        )
        issue_payload["results"] = checked["results"]
        ranking = issue_payload.get("ranking")
        if not isinstance(ranking, dict):
            ranking = {}
            issue_payload["ranking"] = ranking
        ranking["final_count"] = len(checked["results"])
        issue_payload["admin_search_state"] = checked["trace"]["admin_search_state"]
        kept_ids = {str(row["document_id"]) for row in checked["results"]}
        existing_packets = issue_payload.get("exact_article_packets")
        if not isinstance(existing_packets, list):
            existing_packets = []
        issue_payload["exact_article_packets"] = [
            packet for packet in existing_packets
            if packet.get("status") != "complete" or str(packet.get("document_id")) in kept_ids
        ]
        for row in checked["results"]:
            chunk_id = str(row.get("chunk_id") or "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                context_results.append(row)

    return {
        "status": "ok",
        "request_id": request_id,
        "query_decision": query_decision,
        "issues": issue_payloads,
        "results": context_results,
        "total_results": len(context_results),
        "timing_ms": {
            "total": round((time.perf_counter() - started) * 1000, 3),
            "hydrate": round(hydrate_total_ms, 3),
        },
        "release_id": runtime.release_id,
        # Keep the immutable serving-manifest identity on every batch
        # response. The API answer contract projects this value to clients;
        # relying only on the release id made ``manifest_hash`` disappear
        # from otherwise successful Direct RAG answers.
        "manifest_hash": str(
            (getattr(runtime, "serving_manifest", None) or {}).get(
                "manifest_sha256"
            )
            or ""
        ) or None,
    }


@app.exception_handler(V2RuntimeError)
def retrieval_state_error(_request: Any, exc: V2RuntimeError) -> JSONResponse:
    # Batch and single searches use the same explicit unavailable contract.
    code = "admin_search_state_unavailable" if str(exc) == "admin_search_state_unavailable" else "retrieval_unavailable"
    return JSONResponse(status_code=503, content={"detail": code})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("LEGAL_SEARCH_HOST", "127.0.0.1"),
        port=int(os.getenv("LEGAL_SEARCH_PORT", str(DEFAULT_PORT))),
    )
