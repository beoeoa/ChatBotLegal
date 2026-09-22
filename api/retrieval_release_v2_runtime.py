"""Manifest-bound retrieval runtime for Retrieval Release V2 staging.

This adapter deliberately does not reuse the integer-ID V1 SQL retrieval path.
V2 chunk revision IDs are strings and the SQLite exact/FTS index plus Chroma
collection are bound to the same release, source snapshot and date filter.
It is usable by staging benchmarks only; activation remains a separate gate.
"""

from __future__ import annotations

from contextlib import nullcontext
from concurrent.futures import Future
from datetime import date
import math
import json
import os
from queue import Empty, Queue
import re
import sqlite3
from pathlib import Path
from threading import Event, RLock, Thread
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence

import chromadb

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from api.legal_domains import canonicalize_legal_domain
from api.retrieval_candidate_r21 import (
    diversify_legal_identities,
    exact_candidates_for_r21,
    explicit_law_article_keys,
)
from api.retrieval_candidate_r22 import (
    candidate_law_priors_r22,
    expand_legal_query_r22,
    reserve_article_identities_before_top_k,
)
from api.retrieval_candidate_r26 import (
    R26_CANDIDATE_PROFILE,
    diversify_r26_candidates,
    expand_legal_query_r26,
)
from api.retrieval_candidate_r27 import (
    R27_CANDIDATE_PROFILE,
    explicit_law_article_keys_r27,
    rerank_candidates_r27 as rerank_candidates_r27_policy,
)
from api.retrieval_candidate_r28 import (
    R28_CANDIDATE_PROFILE,
    merge_recovery_r28,
    query_catalog_hints_r28,
    recover_candidates_r28,
)
from api.legal_exact_retrieval import plan_exact_lookup
from api.retrieval_serving_manifest_v3 import load_v3_serving_manifest
from api.retrieval_stage_c_exact_index import (
    MmapExactVectorIndex,
    StageCExactIndexError,
    StageCExactVectorIndex,
    VerifiedChromaExactVectorIndex,
)
from scripts.legal_retrieval_cache import QueryVectorCache, query_vector_cache_key


class V2RuntimeError(RuntimeError):
    """Raised when release fingerprints or serving boundaries do not match."""


class _AnnBatchRequest:
    __slots__ = ("collection", "vector", "n_results", "future")

    def __init__(self, collection: Any, vector: Any, n_results: int) -> None:
        self.collection = collection
        self.vector = vector
        self.n_results = int(n_results)
        self.future: Future = Future()


class _AnnQueryBatcher:
    """Bounded micro-batcher for read-only Chroma ANN queries.

    Chroma's local HNSW binding is efficient for one batched call but becomes
    heavily contended when many HTTP workers call ``query`` concurrently. The
    batcher is opt-in, keeps the release immutable, and returns one response
    slice per submitted request so the existing hydration and state guards are
    unchanged. A small window is used to avoid adding material latency to a
    single request.
    """

    def __init__(self, *, window_ms: float, max_batch: int, timeout_seconds: float) -> None:
        self.window_seconds = max(0.0, float(window_ms)) / 1000.0
        self.max_batch = max(1, int(max_batch))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._queue: Queue[_AnnBatchRequest | None] = Queue()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="legal-ann-batcher", daemon=True)
        self._thread.start()
        self._stats_lock = RLock()
        self._submitted = 0
        self._batched_queries = 0
        self._max_observed_batch = 0
        self._errors = 0

    @classmethod
    def from_environment(cls) -> "_AnnQueryBatcher | None":
        enabled = str(
            os.getenv("LEGAL_RETRIEVAL_V2_ANN_BATCHING") or "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        if not enabled:
            return None
        return cls(
            window_ms=float(os.getenv("LEGAL_RETRIEVAL_ANN_BATCH_WINDOW_MS") or 5),
            max_batch=int(os.getenv("LEGAL_RETRIEVAL_ANN_BATCH_MAX") or 32),
            timeout_seconds=float(
                os.getenv("LEGAL_RETRIEVAL_ANN_BATCH_TIMEOUT_SECONDS") or 60
            ),
        )

    def submit(self, collection: Any, vector: Any, n_results: int) -> dict[str, Any]:
        request = _AnnBatchRequest(collection, vector, n_results)
        with self._stats_lock:
            self._submitted += 1
        self._queue.put(request)
        return request.future.result(timeout=self.timeout_seconds)

    @staticmethod
    def _response_slice(response: Mapping[str, Any], index: int) -> dict[str, Any]:
        sliced: dict[str, Any] = {}
        for key, value in response.items():
            if isinstance(value, list):
                sliced[key] = [value[index]] if index < len(value) else [[]]
            else:
                sliced[key] = value
        return sliced

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if first is None:
                break
            batch = [first]
            deadline = perf_counter() + self.window_seconds
            while len(batch) < self.max_batch:
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except Empty:
                    break
                if item is None:
                    self._stop.set()
                    break
                batch.append(item)
            groups: dict[tuple[int, int], list[_AnnBatchRequest]] = {}
            for item in batch:
                groups.setdefault((id(item.collection), item.n_results), []).append(item)
            with self._stats_lock:
                self._batched_queries += len(groups)
                self._max_observed_batch = max(self._max_observed_batch, len(batch))
            for group in groups.values():
                try:
                    response = group[0].collection.query(
                        query_embeddings=[item.vector.tolist() for item in group],
                        n_results=group[0].n_results,
                        include=["metadatas", "distances"],
                    )
                    for index, item in enumerate(group):
                        item.future.set_result(self._response_slice(response, index))
                except Exception as exc:
                    with self._stats_lock:
                        self._errors += len(group)
                    for item in group:
                        item.future.set_exception(exc)

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "enabled": True,
                "window_ms": round(self.window_seconds * 1000.0, 3),
                "max_batch": self.max_batch,
                "submitted": self._submitted,
                "batched_queries": self._batched_queries,
                "max_observed_batch": self._max_observed_batch,
                "errors": self._errors,
            }

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def _rerank_r27_explicit_anchors(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    window: int = 60,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Reserve bounded slots for query-authored law/article anchors.

    This mirrors the accepted r27 worker policy. It only reads the current
    query and candidate metadata; it never consults benchmark labels.
    """

    base = [dict(item) for item in candidates]
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in base[:window]:
        identity = (
            normalize_exact(candidate.get("law_number")),
            normalize_exact(candidate.get("article_number")),
        )
        if identity != ("", "") and identity not in identities:
            identities[identity] = candidate
    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in explicit_law_article_keys_r27(query, maximum_distance=140):
        law, article = key.split("|", 1)
        candidate = identities.get((law, article))
        identifier = str((candidate or {}).get("chunk_revision_id") or "")
        if candidate is not None and identifier and identifier not in seen:
            anchors.append(candidate)
            seen.add(identifier)
    anchors = anchors[: max(1, top_k // 2)]
    if not anchors:
        # No explicit citation: the facet-only policy may still identify a
        # strong title/heading match within the bounded pool.
        return rerank_candidates_r27_policy(
            query,
            base,
            candidate_window=window,
            top_k=top_k,
        )
    ordered = [
        *anchors,
        *[
            candidate
            for candidate in base
            if str(candidate.get("chunk_revision_id") or "") not in seen
        ],
    ]
    # After explicit citation reservation, apply the bounded facet policy to
    # recover strong user-authored procedure/title matches that RRF can place
    # below the answer window.  The policy only reorders rows already in the
    # manifest-bound candidate pool; it never consults labels or creates
    # evidence.  This is important for queries such as a named procedure
    # whose authoritative article is semantically relevant but lexical rank
    # is diluted by a broader law title.
    return rerank_candidates_r27_policy(
        query,
        ordered,
        candidate_window=window,
        top_k=top_k,
    )


_LAW_RE = re.compile(r"\b\d{1,5}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*(?:\s*-[A-ZĐ0-9-]+)*\b", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"\b(?:điều|dieu)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)
_PARAGRAPH_RE = re.compile(r"\b(?:khoản|khoan)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)
LEXICAL_TOKEN_CAP = 12
LEXICAL_FALLBACK_TOKEN_CAP = 4
LEXICAL_QUERY_TIMEOUT_SECONDS = 5.0
R28_TOPIC_FTS_ENABLED = (
    os.getenv("LEGAL_RETRIEVAL_R28_TOPIC_FTS", "true").strip().casefold()
    in {"1", "true", "yes", "on"}
)
# The conjunctive topic lane and the phrase lane are independently gated.
# Phrase FTS is the expensive recovery path; keeping its switch separate lets
# a shadow process measure the latency/recall trade-off without disabling the
# safer topic-AND branch or changing the default release behaviour.
R28_PHRASE_FTS_ENABLED = (
    os.getenv("LEGAL_RETRIEVAL_R28_PHRASE_FTS", "true").strip().casefold()
    in {"1", "true", "yes", "on"}
)
# An exact law/article request has an identity-bearing FTS hit. In the r27
# shadow profile we can safely avoid encoding the same query for ANN and avoid
# a second broad FTS pass; production profiles remain byte-compatible unless
# this flag is explicitly enabled.
EXACT_ARTICLE_FASTPATH = (
    os.getenv("LEGAL_RETRIEVAL_EXACT_ARTICLE_FASTPATH", "false")
    .strip()
    .casefold()
    in {"1", "true", "yes", "on"}
)
LEGAL_QUERY_ALIASES: tuple[tuple[str, str], ...] = (
    ("tình trạng hôn nhân", "Giấy xác nhận tình trạng hôn nhân"),
    ("đăng ký kết hôn lại", "đăng ký lại kết hôn"),
    ("sổ đỏ", "Giấy chứng nhận quyền sử dụng đất quyền sở hữu tài sản gắn liền với đất"),
    ("cấp giấy chứng nhận quyền sử dụng đất lần đầu", "đăng ký đất đai tài sản gắn liền với đất cấp Giấy chứng nhận"),
    ("hợp thửa", "tách thửa đất hợp thửa đất"),
    ("nhà ở nhờ", "chỗ ở hợp pháp do mượn ở nhờ"),
    ("thay đổi chủ hộ", "điều chỉnh thông tin về cư trú thay đổi chủ hộ"),
    ("mất căn cước công dân", "cấp lại thẻ căn cước"),
    ("đất trồng cây lâu năm", "chuyển mục đích sử dụng đất nông nghiệp sang đất ở"),
    ("quyết định xử phạt vi phạm hành chính", "đơn khiếu nại quyết định hành chính"),
)


def normalize_exact(value: Any) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFD", str(value or "")).replace("Đ", "D").replace("đ", "d")
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn").upper()
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def normalize_query(value: Any) -> str:
    """Normalize transport noise without destroying legal wording."""

    import unicodedata

    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _domain_candidate_allowed(candidate: Mapping[str, Any], requested: str | None) -> bool:
    """Apply a soft domain boundary without hiding shared/legal rows.

    An explicit router domain narrows retrieval to the canonical domain and
    its legacy aliases. Rows with no domain metadata remain eligible because
    older approved central/inter-sector documents are intentionally shared;
    they are audited later by the serving projection.
    """

    expected = canonicalize_legal_domain(requested)
    if not expected or expected in {"unknown", "all", "general"}:
        return True
    raw = candidate.get("canonical_domain") or candidate.get("domain_slug") or candidate.get("domain")
    actual = canonicalize_legal_domain(raw)
    if not actual or actual in {"unknown", "all", "general"}:
        return True
    return actual == expected


def _filter_domain_candidates(
    candidates: Sequence[Mapping[str, Any]], requested: str | None
) -> list[dict[str, Any]]:
    return [dict(item) for item in candidates if _domain_candidate_allowed(item, requested)]


def _as_date(value: date | str | None) -> str:
    if value is None:
        return date.today().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _safe_fts_query(query: str) -> str:
    stop = {
        "cua", "cho", "theo", "toi", "can", "ve", "va", "la", "co", "duoc",
        "nhung", "nay", "trong", "mot", "cac", "xin", "hay", "neu", "thi",
        "quy", "dinh", "van", "ban", "dieu", "phan", "noi", "dung",
    }
    tokens = re.findall(r"[\wÀ-ỹĐđ]+", str(query or ""), flags=re.UNICODE)
    selected: list[str] = []
    selected_folded: set[str] = set()
    for token in tokens:
        normalized = normalize_exact(token).casefold()
        folded = token.casefold()
        if len(token) <= 1 or normalized in stop or folded in selected_folded:
            continue
        selected.append(token)
        selected_folded.add(folded)
        if len(selected) >= 24:
            break
    return " OR ".join('"' + token.replace('"', ' ') + '"' for token in selected)


def _fts_terms(query: str) -> list[str]:
    return re.findall(r'"([^"]+)"', _safe_fts_query(query))


def _expand_legal_query(query: str) -> str:
    normalized = normalize_exact(query).casefold()
    additions = [
        canonical for phrase, canonical in LEGAL_QUERY_ALIASES if phrase in normalized
    ]
    return query if not additions else query + " " + " ".join(dict.fromkeys(additions))


_TOPIC_PREFIX_RE = re.compile(
    r"^.*?(?:\bhai\s+vấn\s+đề\s*:\s*|\bliên\s+quan\s+đến\s+|\bvề\s+|\bthực\s+hiện\s+)",
    re.IGNORECASE | re.DOTALL,
)
_TOPIC_STOPWORDS = {
    "ai", "ban", "bao", "cach", "cho", "co", "cua", "duoc", "hay",
    "hien", "hoi", "khong", "la", "mot", "nao", "nhu", "noi", "quy",
    "tai", "theo", "thi", "toi", "trong", "va", "ve", "voi", "xin",
}


def _topic_fts_tokens(query: str, *, maximum: int = 10) -> list[str]:
    """Extract a bounded topic phrase for r27 conjunctive FTS recovery.

    User boilerplate ("pháp luật hiện hành...", "nội dung...", etc.) is
    removed before the conjunction is built. Original Vietnamese spelling is
    retained because the release FTS tokenizer stores accented terms. This is
    a query-only hint and never uses benchmark labels or expected sources.
    """

    text = normalize_query(query)
    text = _TOPIC_PREFIX_RE.sub("", text, count=1)
    text = re.split(
        r"\s*(?:;|\bđiều\s+luật\b|\bnội\s+dung\b|\bxác\s+định\s+bước\b|\bhãy\s+tách\b)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[\wÀ-ỹĐđ]+", text, flags=re.UNICODE):
        folded = normalize_exact(token).casefold()
        if len(token) <= 1 or folded in _TOPIC_STOPWORDS or folded in seen:
            continue
        seen.add(folded)
        tokens.append(token)
        if len(tokens) >= max(2, int(maximum)):
            break
    return tokens


def _topic_fts_phrase(query: str, *, minimum: int = 3, maximum: int = 14) -> str:
    """Return a short user-authored heading phrase for r27 FTS recovery.

    The conjunctive token lane intentionally drops stopwords, which is useful
    for broad recall but can still be crowded out when many instruments reuse
    the same legal boilerplate.  A bounded phrase lane keeps the original
    wording (including words such as ``của`` and ``không``) so an exact
    structural heading can be recovered.  It is only a query hint: no source
    identity, benchmark label, or answer text is consulted.
    """

    text = normalize_query(query)
    text = _TOPIC_PREFIX_RE.sub("", text, count=1)
    text = re.split(
        r"\s*(?:;|\bđiều\s+luật\b|\bnội\s+dung\b|\bxác\s+định\s+bước\b|\bhãy\s+tách\b)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(r"^\s*\d+[.)]\s*", "", text)
    words = re.findall(r"[\wÀ-ỹĐđ]+", text, flags=re.UNICODE)
    if not (int(minimum) <= len(words) <= int(maximum)):
        return ""
    return " ".join(words).strip()


def _derive_issue_subqueries(question: str, issue_count: int) -> list[str]:
    """Replay the frozen M5 deterministic issue-clause extraction."""

    quoted = [
        value.strip()
        for value in re.findall(r"[“\"]([^”\"]{12,})[”\"]", question)
        if value.strip()
    ]
    if len(quoted) >= issue_count:
        return quoted[:issue_count]
    return [question] * issue_count


def _round_robin_candidates(
    branches: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    maximum = max((len(branch) for branch in branches), default=0)
    for rank in range(maximum):
        for branch in branches:
            if rank >= len(branch):
                continue
            item = dict(branch[rank])
            identifier = str(item.get("chunk_revision_id") or "")
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            output.append(item)
    return output


def _candidate_law_priors(
    *branches: Sequence[Mapping[str, Any]], limit: int = 3
) -> list[str]:
    counts: dict[str, int] = {}
    first_rank: dict[str, int] = {}
    position = 0
    for branch in branches:
        for candidate in branch[:40]:
            position += 1
            law = str(candidate.get("law_number") or "").strip()
            if not law:
                continue
            counts[law] = counts.get(law, 0) + 1
            first_rank.setdefault(law, position)
    return sorted(
        counts,
        key=lambda law: (-counts[law], first_rank[law], law),
    )[:limit]


def _exact_lookup_keys(query: str) -> tuple[list[tuple[str, str]], str | None]:
    """Return only the exact keys used by the frozen v6r20 worker."""

    laws = list(
        dict.fromkeys(normalize_exact(match.group(0)) for match in _LAW_RE.finditer(query))
    )
    articles = list(
        dict.fromkeys(normalize_exact(match.group(1)) for match in _ARTICLE_RE.finditer(query))
    )
    if articles and not laws:
        return [], None
    if laws and articles:
        return (
            [("law_article", f"{law}|{article}") for law in laws for article in articles],
            "law_article",
        )
    if laws:
        return [("law_number", law) for law in laws], "law_number"
    return [], None


def _manifest_chunk_allowlist(payload: Mapping[str, Any]) -> frozenset[str]:
    """Validate and return the only chunk IDs a V2 runtime may serve."""

    chunk_rows = list(payload.get("chunks") or [])
    if not chunk_rows:
        raise V2RuntimeError("v2_manifest_eligible_chunk_allowlist_required")
    expected_release_id = str(payload.get("release_id") or "")
    if any(
        row.get("eligible") is not True
        or row.get("serving_state") != "retrievable"
        or str(row.get("release_id") or "") != expected_release_id
        or not str(row.get("chunk_revision_id") or "")
        for row in chunk_rows
    ):
        raise V2RuntimeError("v2_manifest_chunk_eligibility_contract_failed")
    allowlist = frozenset(str(row["chunk_revision_id"]) for row in chunk_rows)
    if len(allowlist) != len(chunk_rows):
        raise V2RuntimeError("v2_manifest_duplicate_chunk_revision_id")
    identity_rows = [
        {
            "chunk_revision_id": str(row["chunk_revision_id"]),
            "document_id": int(row["document_id"]),
            "article_id": int(row["article_id"]),
            "content_sha256": str(row.get("content_sha256") or ""),
            "embedding_text_sha256": str(row.get("embedding_text_sha256") or ""),
            "passage_sha256": str(row.get("passage_sha256") or ""),
            "token_count": int(row.get("token_count") or 0),
        }
        for row in chunk_rows
    ]
    declared_identity_sha = str(payload.get("chunk_identity_sha256") or "")
    if len(declared_identity_sha) != 64:
        raise V2RuntimeError("v2_manifest_chunk_identity_checksum_required")
    if canonical_sha256(identity_rows) != declared_identity_sha:
        raise V2RuntimeError("v2_manifest_chunk_identity_checksum_mismatch")
    return allowlist


def _validate_chroma_runtime_mode(
    *,
    require_chroma_collections: bool,
    verify_collection_counts: bool,
    vector_backend: str,
    exact_source: str,
    exact_query_only: bool,
    ann_prewarm_enabled: bool,
) -> None:
    """Keep Chroma-free serving explicit and fail-closed.

    The offline mmap artifact is independently bound to the serving release,
    model recipe and lexical SQLite identities. It may replace Chroma only
    when it is authoritative for every vector query; no ANN fallback or ANN
    warmup is permitted in that mode.
    """

    if require_chroma_collections:
        return
    if verify_collection_counts:
        raise V2RuntimeError("chroma_collection_count_verification_requires_chroma")
    if vector_backend != "stage_c_exact" or exact_source != "offline_mmap":
        raise V2RuntimeError("chroma_free_mode_requires_offline_mmap_exact")
    if exact_query_only:
        raise V2RuntimeError("chroma_free_mode_forbids_ann_fallback")
    if ann_prewarm_enabled:
        raise V2RuntimeError("chroma_free_mode_forbids_ann_prewarm")


class V2ServingRuntime:
    """One immutable V2 release bound to exact/lexical and vector artifacts."""

    def __init__(
        self,
        *,
        manifest_path: str | Path,
        lexical_index_path: str | Path,
        chroma_path: str | Path,
        current_collection: str,
        temporal_collection: str,
        query_encoder: Any,
        serving_manifest_path: str | Path | None = None,
        sqlite_check_same_thread: bool = True,
        allowlist_backend: str = "memory",
        verify_declared_manifest_artifacts: bool = True,
        verify_collection_counts: bool = True,
        require_chroma_collections: bool = True,
        r28_catalog_mapping: Mapping[str, Any] | None = None,
        r28_catalog_acceptance: Mapping[str, Any] | None = None,
        r28_blocked_resolution: Mapping[str, Any] | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.lexical_index_path = Path(lexical_index_path).resolve()
        self.query_encoder = query_encoder
        self.r28_catalog_mapping = (
            dict(r28_catalog_mapping) if r28_catalog_mapping is not None else None
        )
        self.r28_catalog_acceptance = (
            dict(r28_catalog_acceptance)
            if r28_catalog_acceptance is not None
            else None
        )
        self.r28_blocked_resolution = (
            dict(r28_blocked_resolution)
            if r28_blocked_resolution is not None
            else None
        )
        if (
            self.r28_catalog_mapping is not None
            and self.r28_catalog_acceptance is not None
            and self.r28_blocked_resolution is not None
        ):
            try:
                # Empty text yields no hints but validates acceptance and
                # mapping checksums at startup instead of on the first request.
                query_catalog_hints_r28(
                    "",
                    self.r28_catalog_mapping,
                    self.r28_catalog_acceptance,
                    blocked_resolution=self.r28_blocked_resolution,
                )
            except ValueError as exc:
                raise V2RuntimeError(str(exc)) from exc
        self._vector_cache = QueryVectorCache(
            max_entries=int(os.getenv("LEGAL_RETRIEVAL_V2_CACHE_MAX_ENTRIES") or 1024),
            ttl_seconds=float(os.getenv("LEGAL_RETRIEVAL_V2_CACHE_TTL_SECONDS") or 300),
        )
        # Multiple Direct RAG requests often arrive with the same short
        # question (for example a browser retry or a c4 load probe).  A plain
        # cache still lets every thread miss at the same time and run the
        # expensive local encoder in parallel.  Keep a per-key single-flight
        # gate so only the first caller computes a vector; callers for other
        # questions remain fully concurrent.
        self._vector_inflight: dict[str, Event] = {}
        self._vector_inflight_lock = RLock()
        self.ann_fetch_multiplier = max(
            1,
            int(os.getenv("LEGAL_RETRIEVAL_V2_ANN_FETCH_MULTIPLIER") or 10),
        )
        self._ann_batcher = _AnnQueryBatcher.from_environment()
        self.vector_backend = str(
            os.getenv("LEGAL_RETRIEVAL_V2_VECTOR_BACKEND") or "chroma_ann"
        ).strip().casefold()
        if self.vector_backend not in {"chroma_ann", "stage_c_exact"}:
            raise V2RuntimeError("retrieval_v2_vector_backend_invalid")
        self.exact_vector_index: StageCExactVectorIndex | MmapExactVectorIndex | None = None
        self.exact_vector_source: str | None = None
        # Exact mmap is authoritative for identity-bearing requests.  Broad
        # natural-language queries may use the immutable ANN collection to
        # avoid serializing a full 638k x 1024 matmul under concurrent load.
        self.exact_query_only = str(
            os.getenv("LEGAL_RETRIEVAL_V2_EXACT_QUERY_ONLY") or "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        configured_exact_source = str(
            os.getenv("LEGAL_RETRIEVAL_V2_EXACT_SOURCE") or "verified_chroma"
        ).strip().casefold()
        ann_prewarm_enabled = str(
            os.getenv("LEGAL_RETRIEVAL_V2_PREWARM_ANN") or "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self.chroma_collections_required = bool(require_chroma_collections)
        _validate_chroma_runtime_mode(
            require_chroma_collections=self.chroma_collections_required,
            verify_collection_counts=bool(verify_collection_counts),
            vector_backend=self.vector_backend,
            exact_source=configured_exact_source,
            exact_query_only=self.exact_query_only,
            ann_prewarm_enabled=ann_prewarm_enabled,
        )
        self._warmup_ms: float | None = None
        if not self.manifest_path.is_file() or not self.lexical_index_path.is_file():
            raise V2RuntimeError("release_artifact_missing")
        if allowlist_backend not in {"memory", "verified_sqlite"}:
            raise V2RuntimeError("v2_allowlist_backend_invalid")
        self._allowlist_backend = allowlist_backend
        if allowlist_backend == "memory":
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
            if self.manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
                raise V2RuntimeError("v2_manifest_required")
            if self.manifest.get("approved") is not True or self.manifest.get("legal_review_attestation") is not True:
                raise V2RuntimeError("approved_v2_manifest_required")
        else:
            self.manifest = {}
        self.serving_manifest = None
        if serving_manifest_path is not None:
            try:
                loaded_serving = load_v3_serving_manifest(
                    serving_manifest_path,
                    project_root=Path(__file__).resolve().parents[1],
                    verify_file_artifacts=verify_declared_manifest_artifacts,
                )
            except RuntimeError as exc:
                raise V2RuntimeError(str(exc)) from exc
            serving = dict(loaded_serving.payload)
            if allowlist_backend == "memory":
                if loaded_serving.release_id != self.manifest.get("release_id"):
                    raise V2RuntimeError("serving_manifest_release_id_mismatch")
                if loaded_serving.source_snapshot_sha256 != self.manifest.get("source_snapshot_sha256"):
                    raise V2RuntimeError("serving_manifest_source_snapshot_mismatch")
                if serving.get("chunk_manifest_sha256") != self.manifest.get("manifest_sha256"):
                    raise V2RuntimeError("serving_manifest_chunk_manifest_mismatch")
            index_from_pointer = loaded_serving.artifact_path(
                "exact_lexical_index", project_root=Path(__file__).resolve().parents[1]
            )
            if index_from_pointer != self.lexical_index_path:
                raise V2RuntimeError("serving_manifest_exact_index_mismatch")
            if file_sha256(self.lexical_index_path) != str((serving.get("exact_lexical_index") or {}).get("sha256") or ""):
                raise V2RuntimeError("serving_manifest_exact_index_checksum_mismatch")
            self.serving_manifest = serving
        if allowlist_backend == "memory":
            self._manifest_chunk_ids = _manifest_chunk_allowlist(self.manifest)
        else:
            if self.serving_manifest is None:
                raise V2RuntimeError("verified_sqlite_requires_serving_manifest_v3")
            # Manifest V3 verifies the immutable approved-manifest file checksum.
            # SQLite then acts as the indexed chunk-ID allowlist, avoiding a
            # multi-gigabyte Python set for 639k IDs while remaining fail-closed.
            self.manifest = {
                "schema_version": "legal-retrieval-chunk-manifest-v2",
                "release_id": self.serving_manifest["release_id"],
                "source_snapshot_sha256": self.serving_manifest["source_snapshot_sha256"],
                "manifest_sha256": self.serving_manifest["chunk_manifest_sha256"],
                "manifest_file_sha256": self.serving_manifest["chunk_manifest_file_sha256"],
                "approved": True,
                "legal_review_attestation": True,
            }
            self._manifest_chunk_ids = None
        self.db = sqlite3.connect(
            f"file:{self.lexical_index_path.as_posix()}?mode=ro",
            uri=True,
            check_same_thread=sqlite_check_same_thread,
            cached_statements=128,
        )
        self.db.row_factory = sqlite3.Row
        # Read-only, process-local SQLite tuning. It does not alter the FTS
        # index, BM25 scores, candidate window, or hydration predicates.
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA cache_size=-65536")
        self._lexical_lock = RLock()
        self._document_frequency_cache: dict[str, int] = {}
        self._execution_cache_stats: dict[str, Any] = {
            "fts_document_frequency": {
                "enabled": False,
                "entry_count": 0,
                "prewarm_ms": 0.0,
            },
            "law_chunks": {
                "enabled": False,
                "law_count": 0,
                "prewarm_ms": 0.0,
            },
        }
        prewarm_fts_frequency = str(
            os.getenv("LEGAL_RETRIEVAL_V2_PREWARM_FTS_FREQUENCY") or "0"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        prewarm_law_cache = str(
            os.getenv("LEGAL_RETRIEVAL_V2_PREWARM_LAW_CACHE") or "0"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        if prewarm_fts_frequency:
            # TEMP-only fts5vocab does not mutate the checksum-bound main DB.
            # It must be created before query_only is enabled.
            self.db.execute(
                "CREATE VIRTUAL TABLE temp.chunk_fts_vocab_r26 "
                "USING fts5vocab('main','chunk_fts','row')"
            )
        self.db.execute("PRAGMA query_only=ON")
        self._law_chunk_cache: dict[
            str, list[tuple[sqlite3.Row, str, str, int, int, str]]
        ] = {}
        self._law_rowids: dict[str, list[int]] = {}
        for row in self.db.execute("SELECT rowid,law_number FROM chunks"):
            self._law_rowids.setdefault(str(row["law_number"]), []).append(
                int(row["rowid"])
            )
        metadata = {
            str(row["key"]): str(row["value"])
            for row in self.db.execute("SELECT key,value FROM release_metadata")
        }
        if metadata.get("release_eligible", "").casefold() != "true" or metadata.get("provisional_staging", "").casefold() != "false":
            raise V2RuntimeError("lexical_index_not_release_eligible")
        if allowlist_backend == "memory":
            observed_manifest_file_sha256 = file_sha256(self.manifest_path)
        else:
            observed_manifest_file_sha256 = str(
                self.serving_manifest.get("chunk_manifest_file_sha256") or ""
            )
        if metadata.get("manifest_file_sha256") != observed_manifest_file_sha256:
            raise V2RuntimeError("lexical_index_manifest_file_sha256_mismatch")
        for key, expected in (
            ("release_id", self.manifest.get("release_id")),
            ("source_snapshot_sha256", self.manifest.get("source_snapshot_sha256")),
            ("manifest_sha256", self.manifest.get("manifest_sha256")),
        ):
            if expected and metadata.get(key) != str(expected):
                raise V2RuntimeError(f"lexical_index_{key}_mismatch")
        self.release_id = str(self.manifest["release_id"])
        self.source_snapshot_sha256 = str(self.manifest["source_snapshot_sha256"])
        if prewarm_fts_frequency:
            began = perf_counter()
            self._document_frequency_cache = {
                str(row["term"]): int(row["doc"])
                for row in self.db.execute(
                    "SELECT term,doc FROM temp.chunk_fts_vocab_r26"
                ).fetchall()
            }
            self._execution_cache_stats["fts_document_frequency"] = {
                "enabled": True,
                "entry_count": len(self._document_frequency_cache),
                "prewarm_ms": round((perf_counter() - began) * 1000, 3),
            }
        if prewarm_law_cache:
            began = perf_counter()
            for law in sorted(self._law_rowids):
                self._normalized_law_chunks(law)
            self._execution_cache_stats["law_chunks"] = {
                "enabled": True,
                "law_count": len(self._law_chunk_cache),
                "prewarm_ms": round((perf_counter() - began) * 1000, 3),
            }
        self.current_collection_name = str(current_collection)
        self.temporal_collection_name = str(temporal_collection)
        self.chroma_client = None
        self.current_collection = None
        self.temporal_collection = None
        self.current_collection_count: int | None = None
        self.temporal_collection_count: int | None = None
        # ANN graph paging is deliberately opt-in.  A cold Chroma HNSW query
        # can page hundreds of MB from disk and make the first user request
        # look like a retrieval timeout; prewarming is read-only and does not
        # alter the immutable collection or ranking policy.
        self._ann_prewarm_enabled = ann_prewarm_enabled
        self._ann_prewarm_ms: float | None = None
        self._ann_prewarm_queries = 0
        if self.serving_manifest is not None:
            expected_current = str((self.serving_manifest.get("current_collection") or {}).get("path") or "")
            expected_temporal = str((self.serving_manifest.get("temporal_collection") or {}).get("path") or "")
            if expected_current != f"chroma://{current_collection}" or expected_temporal != f"chroma://{temporal_collection}":
                raise V2RuntimeError("serving_manifest_collection_mismatch")
            self.current_collection_count = int(
                (self.serving_manifest.get("current_collection") or {}).get("count") or -1
            )
            self.temporal_collection_count = int(
                (self.serving_manifest.get("temporal_collection") or {}).get("count") or -1
            )
        if self.chroma_collections_required:
            client = chromadb.PersistentClient(path=str(Path(chroma_path).resolve()))
            # Keep the verified client available to the localhost serving
            # adapter. Optional admin overlays are opened separately.
            self.chroma_client = client
            self.current_collection = client.get_collection(current_collection)
            self.temporal_collection = client.get_collection(temporal_collection)
            if verify_collection_counts:
                if self.current_collection.count() != self.current_collection_count:
                    raise V2RuntimeError("current_collection_count_mismatch")
                if self.temporal_collection.count() != self.temporal_collection_count:
                    raise V2RuntimeError("temporal_collection_count_mismatch")
            for collection in (self.current_collection, self.temporal_collection):
                values = dict(collection.metadata or {})
                if values.get("release_id") != self.release_id:
                    raise V2RuntimeError(f"collection_release_id_mismatch:{collection.name}")
                if values.get("source_snapshot_sha256") != self.source_snapshot_sha256:
                    raise V2RuntimeError(f"collection_source_snapshot_mismatch:{collection.name}")
                if str(values.get("hnsw:space") or "") != "cosine":
                    raise V2RuntimeError(f"collection_metric_mismatch:{collection.name}")
        self.chroma_collections_available = bool(
            self.current_collection is not None and self.temporal_collection is not None
        )
        self.collection_counts_verified = bool(
            self.chroma_collections_available and verify_collection_counts
        )
        if self.vector_backend == "stage_c_exact":
            exact_source = configured_exact_source
            if exact_source not in {"verified_chroma", "stage_c_shards", "offline_mmap"}:
                raise V2RuntimeError("stage_c_exact_source_invalid")
            self.exact_vector_source = exact_source
            try:
                exact_device = str(
                    os.getenv("LEGAL_RETRIEVAL_V2_EXACT_DEVICE") or "cuda"
                )
                if exact_source == "verified_chroma":
                    self.exact_vector_index = VerifiedChromaExactVectorIndex(
                        collection=self.temporal_collection,
                        sqlite_connection=self.db,
                        expected_release_id=self.release_id,
                        expected_source_snapshot_sha256=self.source_snapshot_sha256,
                        expected_vector_count=int(
                            self.temporal_collection_count
                            if self.temporal_collection_count is not None
                            else self.temporal_collection.count()
                        ),
                        device=exact_device,
                    )
                elif exact_source == "stage_c_shards":
                    exact_manifest = str(
                        os.getenv("LEGAL_RETRIEVAL_V2_STAGE_C_VECTOR_MANIFEST")
                        or ""
                    ).strip()
                    if not exact_manifest:
                        raise V2RuntimeError("stage_c_vector_manifest_required")
                    provenance = (self.serving_manifest or {}).get("provenance") or {}
                    self.exact_vector_index = StageCExactVectorIndex(
                        manifest_path=exact_manifest,
                        sqlite_connection=self.db,
                        expected_release_id=self.release_id,
                        expected_source_snapshot_sha256=self.source_snapshot_sha256,
                        expected_model_fingerprint=str(
                            provenance.get("model_artifact_fingerprint") or ""
                        ),
                        expected_embedding_recipe_fingerprint=str(
                            provenance.get("embedding_recipe_fingerprint") or ""
                        ),
                        device=exact_device,
                    )
                else:
                    exact_manifest = str(
                        os.getenv("LEGAL_RETRIEVAL_V2_EXACT_MMAP_MANIFEST") or ""
                    ).strip()
                    if not exact_manifest:
                        raise V2RuntimeError("offline_mmap_manifest_required")
                    provenance = (self.serving_manifest or {}).get("provenance") or {}
                    self.exact_vector_index = MmapExactVectorIndex(
                        manifest_path=exact_manifest,
                        sqlite_connection=self.db,
                        expected_release_id=self.release_id,
                        expected_source_snapshot_sha256=self.source_snapshot_sha256,
                        expected_model_fingerprint=str(
                            provenance.get("model_artifact_fingerprint") or ""
                        ),
                        expected_embedding_recipe_fingerprint=str(
                            provenance.get("embedding_recipe_fingerprint") or ""
                        ),
                        device=exact_device,
                    )
            except StageCExactIndexError as exc:
                raise V2RuntimeError(str(exc)) from exc

    @property
    def embedding_device(self) -> str | None:
        return getattr(self.query_encoder, "device_name", None)

    @property
    def embedding_dtype(self) -> str | None:
        return getattr(self.query_encoder, "dtype_name", None)

    @property
    def model_fingerprint(self) -> str | None:
        return getattr(self.query_encoder, "model_fingerprint", None)

    @property
    def embedding_recipe_fingerprint(self) -> str | None:
        return getattr(self.query_encoder, "embedding_recipe_fingerprint", None)

    @property
    def embedding_requested_device(self) -> str | None:
        return getattr(self.query_encoder, "requested_device", None)

    @property
    def embedding_fallback_reason(self) -> str | None:
        return getattr(self.query_encoder, "fallback_reason", None)

    @property
    def warmup_ms(self) -> float | None:
        return self._warmup_ms

    def warmup(self) -> float | None:
        warmup = getattr(self.query_encoder, "warmup", None)
        if callable(warmup):
            self._warmup_ms = warmup()
        if self._ann_prewarm_enabled:
            self._prewarm_ann_graphs()
        return self._warmup_ms

    def _prewarm_ann_graphs(self) -> None:
        """Page immutable HNSW graphs before readiness is reported.

        This is intentionally a startup-only shadow optimization.  It uses a
        deterministic query embedding and asks for one result from each
        serving partition; no IDs, metadata or labels are persisted.
        """

        started = perf_counter()
        try:
            if not self.chroma_collections_available:
                raise V2RuntimeError("chroma_collections_unavailable_for_ann_prewarm")
            vector = self.query_encoder.encode_query(
                "Truy vấn kiểm tra nạp chỉ mục tìm kiếm pháp luật."
            )
            for collection in (self.current_collection, self.temporal_collection):
                collection.query(
                    query_embeddings=[vector.tolist()],
                    n_results=1,
                    include=["distances"],
                )
                self._ann_prewarm_queries += 1
            self._ann_prewarm_ms = round((perf_counter() - started) * 1000, 3)
        except Exception:
            # Readiness must remain fail-closed for the release itself, but an
            # optional performance warmup cannot make a valid index unusable.
            self._ann_prewarm_ms = round((perf_counter() - started) * 1000, 3)

    @property
    def ann_prewarm_ms(self) -> float | None:
        return self._ann_prewarm_ms

    @property
    def ann_prewarm_queries(self) -> int:
        return self._ann_prewarm_queries

    def vector_cache_stats(self) -> dict[str, int | float]:
        return self._vector_cache.stats()

    def execution_cache_stats(self) -> dict[str, Any]:
        return {
            name: dict(values)
            for name, values in self._execution_cache_stats.items()
        }

    def ann_batch_stats(self) -> dict[str, Any]:
        batcher = getattr(self, "_ann_batcher", None)
        return batcher.stats() if batcher is not None else {"enabled": False}

    def _encode_query_cached(self, query: str) -> tuple[Any, bool]:
        key = query_vector_cache_key(query, str(self.model_fingerprint or "unknown"))
        cached = self._vector_cache.get(key)
        if cached is not None:
            return cached, True
        with self._vector_inflight_lock:
            # A sibling may have populated the cache between the initial
            # lookup and acquiring the gate.
            cached = self._vector_cache.get(key)
            if cached is not None:
                return cached, True
            event = self._vector_inflight.get(key)
            if event is None:
                event = Event()
                self._vector_inflight[key] = event
                owner = True
            else:
                owner = False
        if not owner:
            # The encoder is local and bounded by the request timeout.  Do not
            # wait forever if an unexpected encoder exception leaves no value;
            # the waiting caller can then compute its own vector.
            event.wait(timeout=30.0)
            cached = self._vector_cache.get(key)
            if cached is not None:
                return cached, True
            with self._vector_inflight_lock:
                event = self._vector_inflight.get(key)
                if event is None:
                    event = Event()
                    self._vector_inflight[key] = event
                    owner = True
                else:
                    owner = False
            if not owner:
                # A second producer is still running after the bounded wait;
                # computing locally is preferable to turning a healthy search
                # into an artificial cache timeout.
                vector = self.query_encoder.encode_query(query)
                self._vector_cache.set(key, vector)
                return vector, False
        try:
            vector = self.query_encoder.encode_query(query)
            self._vector_cache.set(key, vector)
            return vector, False
        finally:
            with self._vector_inflight_lock:
                current = self._vector_inflight.pop(key, None)
                if current is not None:
                    current.set()

    def close(self) -> None:
        batcher = getattr(self, "_ann_batcher", None)
        if batcher is not None:
            batcher.close()
            self._ann_batcher = None
        encoder_close = getattr(self.query_encoder, "close", None)
        if callable(encoder_close):
            encoder_close()
        if self.exact_vector_index is not None:
            self.exact_vector_index.close()
            self.exact_vector_index = None
        self.db.close()

    def _scope_sql(self, *, temporal_scope: str, as_of: str) -> tuple[str, list[Any]]:
        if temporal_scope not in {"current", "historical"}:
            raise V2RuntimeError("temporal_scope_requires_clarification")
        state_sql = (
            "document_serving_state = 'current_retrievable'"
            if temporal_scope == "current"
            else "document_serving_state IN ('current_retrievable','historical_only')"
        )
        return (
            f"(release_id = ?) AND ({state_sql}) AND (NULLIF(effective_from,'') IS NULL OR effective_from <= ?) "
            "AND (NULLIF(effective_to,'') IS NULL OR effective_to > ?)",
            [self.release_id, as_of, as_of],
        )

    @staticmethod
    def _placeholders(values: Sequence[Any]) -> str:
        return ",".join("?" for _ in values) or "NULL"

    def _db_guard(self):
        """Serialize every operation on the one read-only SQLite connection."""

        return getattr(self, "_lexical_lock", nullcontext())

    def _rows_for_ids(self, ids: Sequence[str], *, temporal_scope: str, as_of: str) -> dict[str, dict[str, Any]]:
        allowed = getattr(self, "_manifest_chunk_ids", None)
        allowlist_backend = getattr(self, "_allowlist_backend", "memory")
        if allowlist_backend == "memory" and not allowed:
            raise V2RuntimeError("v2_manifest_eligible_chunk_allowlist_required")
        unique = list(
            dict.fromkeys(
                str(value)
                for value in ids
                if str(value)
                and (allowlist_backend == "verified_sqlite" or str(value) in allowed)
            )
        )
        if not unique:
            return {}
        scope_sql, scope_params = self._scope_sql(temporal_scope=temporal_scope, as_of=as_of)
        statement = (
            "SELECT chunk_revision_id,document_id,article_id,chunk_index,release_id,"
            "document_serving_state,law_number,article_number,domain_slug,source_url,"
            "effective_from,effective_to,content,structural_path,passage_sha256,content_sha256,token_count "
            f"FROM chunks WHERE chunk_revision_id IN ({self._placeholders(unique)}) AND {scope_sql}"
        )
        params = [*unique, *scope_params]
        with self._db_guard():
            return {
                str(row["chunk_revision_id"]): dict(row)
                for row in self.db.execute(statement, params)
            }

    def _exact_candidates(self, query: str, *, temporal_scope: str, as_of: str) -> list[dict[str, Any]]:
        keys, specificity = _exact_lookup_keys(query)
        if not keys:
            return []
        clauses = " OR ".join("(key_kind = ? AND normalized_key = ?)" for _ in keys)
        key_params = [item for pair in keys for item in pair]
        statement = (
            "SELECT DISTINCT e.chunk_revision_id FROM exact_lookup e "
            f"WHERE {clauses}"
        )
        with self._db_guard():
            exact_rows = self.db.execute(statement, key_params).fetchall()
            ids = [str(row["chunk_revision_id"]) for row in exact_rows]
            rows = self._rows_for_ids(
                ids, temporal_scope=temporal_scope, as_of=as_of
            )
        ordered = sorted(
            rows.values(),
            key=lambda row: (
                int(row.get("document_id") or 0),
                int(row.get("article_id") or 0),
                int(row.get("chunk_index") or 0),
            ),
        )[:200]
        output = []
        for index, row in enumerate(ordered):
            output.append({
                **row,
                "score": 1.0 / float(index + 1),
                "retrieval_source": "exact",
                "retrieval_sources": ["exact"],
                "exact_specificity": specificity,
            })
        return output

    @staticmethod
    def _exact_for_fusion(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Keep ambiguous article-only hits in audit trace, not ranking."""

        return [
            dict(item)
            for item in candidates
            if str(item.get("exact_specificity") or "") != "article_only"
        ]

    def _rank_fts_terms(self, query: str) -> list[tuple[int, int, str]]:
        ranked: list[tuple[int, int, str]] = []
        for position, term in enumerate(_fts_terms(query)):
            normalized = normalize_exact(term).casefold()
            if not normalized:
                continue
            frequency = self._document_frequency_cache.get(normalized)
            if frequency is None:
                expression = '"' + normalized.replace('"', " ") + '"'
                row = self.db.execute(
                    "SELECT count(*) AS document_frequency "
                    "FROM chunk_fts WHERE chunk_fts MATCH ?",
                    [expression],
                ).fetchone()
                frequency = int(row["document_frequency"] if row else 0)
                self._document_frequency_cache[normalized] = frequency
            if frequency > 0:
                ranked.append((frequency, position, normalized))
        ranked.sort(key=lambda value: (value[0], value[1], normalize_exact(value[2])))
        return ranked

    def _run_ranked_fts(
        self,
        *,
        terms: list[str],
        operator: str = "OR",
        temporal_scope: str,
        as_of: str,
        timeout_seconds: float,
        result_limit: int = 80,
    ) -> list[sqlite3.Row]:
        if timeout_seconds <= 0:
            raise sqlite3.OperationalError("interrupted")
        normalized_operator = str(operator).upper()
        if normalized_operator == "PHRASE":
            # ``terms`` contains one already ordered phrase.  Keep it as a
            # single FTS phrase instead of quoting each token independently.
            fts = '"' + " ".join(terms).replace('"', " ") + '"'
        else:
            joiner = " AND " if normalized_operator == "AND" else " OR "
            fts = joiner.join('"' + term.replace('"', " ") + '"' for term in terms)
        scope_sql, scope_params = self._scope_sql(
            temporal_scope=temporal_scope, as_of=as_of
        )
        deadline = perf_counter() + timeout_seconds
        self.db.set_progress_handler(
            lambda: 1 if perf_counter() >= deadline else 0,
            10_000,
        )
        try:
            return self.db.execute(
                "SELECT c.*,chunk_fts.rank AS lexical_rank FROM chunk_fts "
                "JOIN chunks c ON c.chunk_revision_id=chunk_fts.chunk_revision_id "
                f"WHERE chunk_fts MATCH ? AND {scope_sql} "
                "ORDER BY chunk_fts.rank LIMIT ?",
                [fts, *scope_params, max(1, int(result_limit))],
            ).fetchall()
        finally:
            self.db.set_progress_handler(None, 0)

    def _base_lexical_candidates(
        self,
        query: str,
        *,
        top_k: int,
        temporal_scope: str,
        as_of: str,
        domain: str | None = None,
        candidate_profile: str = R26_CANDIDATE_PROFILE,
    ) -> list[dict[str, Any]]:
        ranked = self._rank_fts_terms(query)
        if not ranked:
            return []
        terms = [term for _, _, term in ranked[:LEXICAL_TOKEN_CAP]]
        result_limit = (
            max(1, int(top_k)) * 4
            if candidate_profile == "r22-shadow"
            else 80
        )
        try:
            rows = self._run_ranked_fts(
                terms=terms,
                temporal_scope=temporal_scope,
                as_of=as_of,
                timeout_seconds=LEXICAL_QUERY_TIMEOUT_SECONDS,
                result_limit=result_limit,
            )
        except sqlite3.OperationalError as error:
            if "interrupted" not in str(error).casefold():
                raise
            fallback = [
                term for _, _, term in ranked[:LEXICAL_FALLBACK_TOKEN_CAP]
            ]
            try:
                rows = self._run_ranked_fts(
                    terms=fallback,
                    temporal_scope=temporal_scope,
                    as_of=as_of,
                    timeout_seconds=LEXICAL_QUERY_TIMEOUT_SECONDS,
                    result_limit=result_limit,
                )
            except sqlite3.OperationalError as fallback_error:
                if "interrupted" not in str(fallback_error).casefold():
                    raise
                rows = []
        if (
            candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}
            and R28_TOPIC_FTS_ENABLED
        ):
            # The release FTS table stores accented Vietnamese tokens. A
            # bounded AND query over the user-authored topic recovers a named
            # article whose generic OR query is crowded out by boilerplate.
            # It is additive and shadow-only; r26 production remains byte
            # compatible with its frozen lexical branch.
            topic_terms = _topic_fts_tokens(query)
            if len(topic_terms) >= 2:
                try:
                    topic_rows = self._run_ranked_fts(
                        terms=topic_terms,
                        operator="AND",
                        temporal_scope=temporal_scope,
                        as_of=as_of,
                        timeout_seconds=LEXICAL_QUERY_TIMEOUT_SECONDS,
                        result_limit=result_limit,
                    )
                except sqlite3.OperationalError as error:
                    if "interrupted" not in str(error).casefold():
                        raise
                    topic_rows = []
                # Topic rows lead only within the lexical branch. Fusion and
                # r27's bounded reranker still decide final evidence.
                if topic_rows:
                    topic_ids = {
                        str(row["chunk_revision_id"])
                        for row in topic_rows
                    }
                    rows = [
                        *topic_rows,
                        *[
                            row
                            for row in rows
                            if str(row["chunk_revision_id"]) not in topic_ids
                        ],
                    ]
            phrase = _topic_fts_phrase(query)
            if phrase and R28_PHRASE_FTS_ENABLED:
                try:
                    phrase_rows = self._run_ranked_fts(
                        terms=[phrase],
                        operator="PHRASE",
                        temporal_scope=temporal_scope,
                        as_of=as_of,
                        timeout_seconds=LEXICAL_QUERY_TIMEOUT_SECONDS,
                        # Phrase matches are collapsed to one representative
                        # per legal article below, so a larger read-only
                        # window is bounded and does not enlarge the public
                        # candidate window.
                        result_limit=max(result_limit, 256),
                    )
                except sqlite3.OperationalError as error:
                    if "interrupted" not in str(error).casefold():
                        raise
                    phrase_rows = []
                if phrase_rows:
                    phrase_norm = normalize_exact(phrase).casefold()
                    heading_rows = [
                        row
                        for row in phrase_rows
                        if phrase_norm
                        in normalize_exact(str(row["structural_path"] or "")).casefold()
                    ]
                    # Repeated chunks from one article can crowd out another
                    # article with the same heading.  Keep the first strong
                    # heading row for each immutable law/article identity,
                    # then fall back to the original phrase order.
                    phrase_representatives: list[sqlite3.Row] = []
                    phrase_seen: set[tuple[str, str]] = set()
                    for row in [*heading_rows, *phrase_rows]:
                        identity = (
                            normalize_exact(str(row["law_number"] or "")),
                            normalize_exact(str(row["article_number"] or "")),
                        )
                        if identity in phrase_seen:
                            continue
                        phrase_seen.add(identity)
                        phrase_representatives.append(row)
                    phrase_ids = {
                        str(row["chunk_revision_id"])
                        for row in phrase_representatives
                    }
                    rows = [
                        *phrase_representatives,
                        *[
                            row
                            for row in rows
                            if str(row["chunk_revision_id"]) not in phrase_ids
                        ],
                    ]
        output: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            item = dict(row)
            item.pop("lexical_rank", None)
            item.update(
                {
                    "score": 1.0 / float(index + 1),
                    "retrieval_source": "lexical",
                    "retrieval_sources": ["lexical"],
                }
            )
            output.append(item)
        if candidate_profile == "r22-shadow":
            return reserve_article_identities_before_top_k(
                output,
                limit=max(1, int(top_k)),
            )
        return _filter_domain_candidates(output, domain)[: max(1, int(top_k))]

    def _normalized_law_chunks(
        self, law: str
    ) -> list[tuple[sqlite3.Row, str, str, int, int, str]]:
        cached = self._law_chunk_cache.get(law)
        if cached is not None:
            return cached
        rows: list[sqlite3.Row] = []
        rowids = list(self._law_rowids.get(law) or [])
        for start in range(0, len(rowids), 800):
            part = rowids[start : start + 800]
            placeholders = ",".join("?" for _ in part)
            rows.extend(
                self.db.execute(
                    f"SELECT * FROM chunks WHERE rowid IN ({placeholders})",
                    part,
                ).fetchall()
            )
        cached = [
            (
                row,
                normalize_exact(str(row["structural_path"] or "")).casefold(),
                normalize_exact(str(row["content"] or "")).casefold(),
                date.fromisoformat(
                    str(row["effective_from"] or "0001-01-01")[:10]
                ).toordinal(),
                date.fromisoformat(
                    str(row["effective_to"] or "9999-12-31")[:10]
                ).toordinal(),
                str(row["document_serving_state"]),
            )
            for row in rows
        ]
        self._law_chunk_cache[law] = cached
        return cached

    def _law_routed_lexical_candidates(
        self,
        query: str,
        *,
        law_numbers: Sequence[str],
        top_k: int,
        temporal_scope: str,
        as_of: str,
        domain: str | None = None,
        candidate_profile: str = R26_CANDIDATE_PROFILE,
    ) -> list[dict[str, Any]]:
        ranked_terms = self._rank_fts_terms(query)[:LEXICAL_TOKEN_CAP]
        if not ranked_terms or not law_numbers:
            return []
        ordinal = date.fromisoformat(as_of[:10]).toordinal()
        scored: list[tuple[float, sqlite3.Row]] = []
        for law in law_numbers:
            for row, path, content, start, end, state in self._normalized_law_chunks(law):
                if temporal_scope == "current" and state != "current_retrievable":
                    continue
                if not (start <= ordinal < end):
                    continue
                score = 0.0
                for frequency, _, term in ranked_terms:
                    if term in path:
                        score += 3.0 / max(1.0, math.log2(float(frequency) + 2.0))
                    elif term in content:
                        score += 1.0 / max(1.0, math.log2(float(frequency) + 2.0))
                if score > 0.0:
                    scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], str(item[1]["chunk_revision_id"])))
        ranked_output: list[dict[str, Any]] = []
        for score, row in scored:
            ranked_output.append(
                {
                    **dict(row),
                    "score": float(score),
                    "retrieval_source": "lexical_law_routed",
                    "retrieval_sources": ["lexical_law_routed"],
                }
            )
        ranked_output = _filter_domain_candidates(ranked_output, domain)
        if candidate_profile == "r22-shadow":
            return reserve_article_identities_before_top_k(
                ranked_output,
                limit=max(1, int(top_k)),
            )
        return ranked_output[: max(1, int(top_k))]

    def _lexical_candidates(
        self,
        query: str,
        *,
        top_k: int,
        temporal_scope: str,
        as_of: str,
        vector_candidates: Sequence[Mapping[str, Any]] | None = None,
        domain: str | None = None,
        candidate_profile: str = R26_CANDIDATE_PROFILE,
    ) -> list[dict[str, Any]]:
        with self._lexical_lock:
            base = self._base_lexical_candidates(
                query,
                top_k=top_k,
                temporal_scope=temporal_scope,
                as_of=as_of,
                domain=domain,
                candidate_profile=candidate_profile,
            )
            law_priors = (
                candidate_law_priors_r22(vector_candidates or [], base)
                if candidate_profile == "r22-shadow"
                else _candidate_law_priors(vector_candidates or [], base)
            )
            routed = self._law_routed_lexical_candidates(
                query,
                law_numbers=law_priors,
                top_k=top_k,
                temporal_scope=temporal_scope,
                as_of=as_of,
                domain=domain,
                candidate_profile=candidate_profile,
            )
            # r27's topic-phrase branch is deliberately the first lexical
            # source.  The routed law-prior branch is still additive, but
            # placing it first can interleave generic prior rows ahead of an
            # exact user-authored heading (while preserving r26 ordering).
            lexical_branches = (
                [base, routed]
                if candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}
                else [routed, base]
            )
            return _round_robin_candidates(lexical_branches)[: max(1, int(top_k))]

    def _vector_candidates(
        self,
        query: str,
        *,
        top_k: int,
        temporal_scope: str,
        as_of: str,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        collection = self.current_collection if temporal_scope == "current" else self.temporal_collection
        vector, _ = self._encode_query_cached(query)
        exact_index = getattr(self, "exact_vector_index", None)
        exact_keys, _exact_specificity = _exact_lookup_keys(query)
        use_exact = exact_index is not None and (
            not self.exact_query_only or bool(exact_keys)
        )
        if use_exact:
            search_k = min(
                int(getattr(exact_index, "vector_count", top_k)),
                max(1, int(top_k)) * (4 if canonicalize_legal_domain(domain) else 1),
            )
            matches = exact_index.search(
                vector,
                top_k=search_k,
                temporal_scope=temporal_scope,
                as_of=as_of,
            )
            rows = self._rows_for_ids(
                [identifier for identifier, _score in matches],
                temporal_scope=temporal_scope,
                as_of=as_of,
            )
            output: list[dict[str, Any]] = []
            for identifier, score in matches:
                row = rows.get(identifier)
                if row is None:
                    continue
                item = dict(row)
                item.update(
                    {
                        "score": float(score),
                        "retrieval_source": "vector",
                        "retrieval_sources": ["vector"],
                    }
                )
                output.append(item)
            # Preserve torch.topk order exactly, as in the frozen M5 worker.
            return _filter_domain_candidates(output, domain)[: max(1, int(top_k))]
        if collection is None:
            raise V2RuntimeError("chroma_collection_unavailable_for_ann")
        configured_count = self.current_collection_count if temporal_scope == "current" else self.temporal_collection_count
        count = int(configured_count if configured_count is not None and configured_count >= 0 else collection.count())
        if count <= 0:
            return []
        ann_n_results = min(
            count,
            max(1, int(top_k))
            * int(getattr(self, "ann_fetch_multiplier", 1)),
        )
        # Keep the experiment variable honest: M5's vector Top-K is the ANN
        # request itself, not a fixed 100-result query followed by a Python
        # slice. Date/manifest hydration still performs a second fail-closed
        # check before a result can be served. The opt-in batcher only changes
        # how independent embeddings enter Chroma per call.
        batcher = getattr(self, "_ann_batcher", None)
        if batcher is not None:
            response = batcher.submit(collection, vector, ann_n_results)
        else:
            response = collection.query(
                query_embeddings=[vector.tolist()],
                n_results=ann_n_results,
                include=["metadatas", "distances"],
            )
        # The collection itself is an immutable manifest-bound partition.
        # Applying the same release/state predicate through Chroma's metadata
        # ``where`` scans hundreds of thousands of rows before every ANN query.
        # `_rows_for_ids` below remains the fail-closed authority: it checks the
        # manifest allowlist, release ID, serving state and effective interval
        # before any vector result can be returned.
        ids = list((response.get("ids") or [[]])[0])
        metadatas = list((response.get("metadatas") or [[]])[0])
        distances = list((response.get("distances") or [[]])[0])
        rows = self._rows_for_ids(ids, temporal_scope=temporal_scope, as_of=as_of)
        output = []
        for identifier, metadata, distance in zip(ids, metadatas, distances):
            row = rows.get(str(identifier))
            if row is None:
                continue
            item = {**row, **{key: value for key, value in (metadata or {}).items() if key not in row}}
            item.update({"score": 1.0 - float(distance), "retrieval_source": "vector", "retrieval_sources": ["vector"]})
            output.append(item)
        return _filter_domain_candidates(sorted(
            output,
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("chunk_revision_id") or ""),
            ),
        ), domain)[: max(1, int(top_k))]

    @staticmethod
    def _merge_candidates(*branches: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for branch in branches:
            for rank, item in enumerate(branch, start=1):
                identifier = str(item.get("chunk_revision_id") or "")
                if not identifier:
                    continue
                current = merged.setdefault(identifier, dict(item))
                current.setdefault("retrieval_sources", [])
                for source in item.get("retrieval_sources") or [item.get("retrieval_source")]:
                    if source and source not in current["retrieval_sources"]:
                        current["retrieval_sources"].append(source)
                current.setdefault("branch_scores", {})
                source = str(item.get("retrieval_source") or "unknown")
                current["branch_scores"][source] = float(item.get("score") or 0.0)
                current.setdefault("branch_ranks", {})[source] = rank
        return merged

    @staticmethod
    def _merge_named_candidates(
        *branches: tuple[str, Sequence[Mapping[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for branch_name, branch in branches:
            for rank, raw in enumerate(branch, start=1):
                item = dict(raw)
                identifier = str(item.get("chunk_revision_id") or "")
                if not identifier:
                    continue
                current = merged.setdefault(identifier, item)
                current.setdefault("retrieval_sources", [])
                if branch_name not in current["retrieval_sources"]:
                    current["retrieval_sources"].append(branch_name)
                current.setdefault("branch_ranks", {})[branch_name] = rank
                current.setdefault("branch_scores", {})[branch_name] = float(
                    item.get("score") or 0.0
                )
        return merged

    def _fuse(self, exact: list[dict[str, Any]], vector: list[dict[str, Any]], lexical: list[dict[str, Any]], *, strategy: str, vector_weight: float, lexical_weight: float) -> list[dict[str, Any]]:
        merged = self._merge_named_candidates(
            ("exact", exact),
            ("vector", vector),
            ("lexical", lexical),
        )
        if strategy == "legacy_stack":
            ordered: list[str] = []
            for branch in (exact, vector, lexical):
                for item in branch:
                    identifier = str(item.get("chunk_revision_id") or "")
                    if identifier and identifier not in ordered:
                        ordered.append(identifier)
            for rank, identifier in enumerate(ordered):
                merged[identifier]["score"] = 1.0 / (1.0 + rank)
        elif strategy == "weighted":
            vector_scores = [float(item.get("score") or 0.0) for item in vector]
            low = min(vector_scores) if vector_scores else 0.0
            high = max(vector_scores) if vector_scores else 1.0
            scale = max(high - low, 1e-9)
            for item in merged.values():
                scores = item.get("branch_scores") or {}
                ranks = item.get("branch_ranks") or {}
                vector_score = (
                    (float(scores.get("vector") or low) - low) / scale
                    if "vector" in ranks
                    else 0.0
                )
                lexical_score = (
                    1.0 / float(ranks["lexical"])
                    if "lexical" in ranks
                    else 0.0
                )
                item["score"] = (
                    vector_weight * vector_score
                    + lexical_weight * lexical_score
                    + (2.0 if "exact" in ranks else 0.0)
                )
        else:  # RRF
            score_by_id = {identifier: 0.0 for identifier in merged}
            for branch in (exact, vector, lexical):
                for rank, item in enumerate(branch, start=1):
                    identifier = str(item.get("chunk_revision_id") or "")
                    if identifier in score_by_id:
                        score_by_id[identifier] += 1.0 / (60.0 + rank)
            for identifier, score in score_by_id.items():
                merged[identifier]["score"] = score
        return sorted(merged.values(), key=lambda item: (-float(item.get("score") or 0.0), str(item["chunk_revision_id"])))

    def _expansion(self, seeds: Sequence[Mapping[str, Any]], *, kind: str, temporal_scope: str, as_of: str) -> list[dict[str, Any]]:
        if not seeds:
            return []
        scope_sql, scope_params = self._scope_sql(temporal_scope=temporal_scope, as_of=as_of)
        output: list[dict[str, Any]] = []
        seen = {str(item.get("chunk_revision_id") or "") for item in seeds}
        for seed in seeds:
            article_id = int(seed.get("article_id") or 0)
            chunk_index = int(seed.get("chunk_index") or 0)
            if kind == "parent":
                statement = "SELECT * FROM chunks WHERE article_id = ? AND " + scope_sql + " ORDER BY chunk_index LIMIT 20"
                params = [article_id, *scope_params]
            else:
                statement = "SELECT * FROM chunks WHERE article_id = ? AND chunk_index BETWEEN ? AND ? AND " + scope_sql + " ORDER BY chunk_index"
                params = [article_id, max(0, chunk_index - 1), chunk_index + 1, *scope_params]
            with self._db_guard():
                for row in self.db.execute(statement, params):
                    item = dict(row)
                    identifier = str(item["chunk_revision_id"])
                    if (
                        self._allowlist_backend == "memory"
                        and identifier not in self._manifest_chunk_ids
                    ):
                        continue
                    if identifier in seen:
                        continue
                    seen.add(identifier)
                    item.update({
                        "score": float(seed.get("score") or 0.0) * 0.5,
                        "retrieval_source": kind,
                        "retrieval_sources": [kind],
                        "expanded_from": str(seed.get("chunk_revision_id") or ""),
                    })
                    output.append(item)
        return output

    @classmethod
    def _select_final_evidence(
        cls,
        reranked: Sequence[Mapping[str, Any]],
        expanded: Sequence[Mapping[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Select from reranked and expanded evidence without making expansion a no-op.

        Expansion candidates are scored relative to their seed in
        ``_expansion``.  They may enter the final window when they outrank a
        low-scoring reranker result, but ties prefer the reranker result.  The
        selection is deterministic and de-duplicates by the manifest chunk
        revision ID.
        """

        reranked_ids = {
            str(item.get("chunk_revision_id") or "")
            for item in reranked
            if str(item.get("chunk_revision_id") or "")
        }
        merged = cls._merge_candidates(reranked, expanded)
        rerank_order = {
            str(item.get("chunk_revision_id") or ""): index
            for index, item in enumerate(reranked)
        }
        expanded_order = {
            str(item.get("chunk_revision_id") or ""): index
            for index, item in enumerate(expanded)
        }
        ordered = sorted(
            merged.values(),
            key=lambda item: (
                -float(item.get("score") or 0.0),
                0 if str(item.get("chunk_revision_id") or "") in reranked_ids else 1,
                rerank_order.get(str(item.get("chunk_revision_id") or ""), 10**9),
                expanded_order.get(str(item.get("chunk_revision_id") or ""), 10**9),
                str(item.get("chunk_revision_id") or ""),
            ),
        )
        return [dict(item) for item in ordered[: max(1, int(limit))]]

    def _search_single(
        self,
        query: str,
        *,
        legal_as_of: date | str,
        temporal_scope: str,
        domain: str | None = None,
        vector_top_k: int = 20,
        lexical_top_k: int = 20,
        fusion_strategy: str = "legacy_stack",
        vector_weight: float = 0.6,
        lexical_weight: float = 0.4,
        final_evidence: int = 10,
        reranker: Any | None = None,
        rerank_top_n: int = 20,
        parent_expansion: bool = False,
        neighbor_expansion: bool = False,
        exact_lookup_enabled: bool = True,
        candidate_profile: str = R26_CANDIDATE_PROFILE,
        query_classification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        as_of = _as_date(legal_as_of)
        normalized_query = normalize_query(query)
        if candidate_profile in {R26_CANDIDATE_PROFILE, R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}:
            retrieval_query = expand_legal_query_r26(
                normalized_query, aliases=LEGAL_QUERY_ALIASES
            )
        elif candidate_profile == "r22-shadow":
            retrieval_query = expand_legal_query_r22(
                normalized_query, aliases=LEGAL_QUERY_ALIASES
            )
        else:
            retrieval_query = _expand_legal_query(normalized_query)
        classification = dict(query_classification or {})
        if str(classification.get("intent") or "").upper() == "OUT_OF_SCOPE":
            elapsed = round((perf_counter() - started) * 1000, 3)
            return {
                "status": "refusal",
                "results": [],
                "trace": {
                    "schema_version": "legal-retrieval-trace-m3-v1",
                    "raw_query": query,
                    "normalized_query": normalized_query,
                    "query_classification": classification,
                    "final_evidence": [],
                    "stage_latency_ms": {"total": elapsed},
                    "release_id": self.release_id,
                    "source_snapshot_sha256": self.source_snapshot_sha256,
                },
            }
        if bool(classification.get("requires_clarification")) or str(classification.get("answer_type") or "").casefold() in {"clarification", "clarification_required"}:
            elapsed = round((perf_counter() - started) * 1000, 3)
            return {
                "status": "clarification_required",
                "results": [],
                "trace": {
                    "schema_version": "legal-retrieval-trace-m3-v1",
                    "raw_query": query,
                    "normalized_query": normalized_query,
                    "query_classification": classification,
                    "final_evidence": [],
                    "stage_latency_ms": {"total": elapsed},
                    "release_id": self.release_id,
                    "source_snapshot_sha256": self.source_snapshot_sha256,
                },
            }
        if temporal_scope not in {"current", "historical"}:
            return {
                "status": "clarification_required",
                "results": [],
                "trace": {"schema_version": "legal-retrieval-trace-m3-v1", "raw_query": query, "normalized_query": query, "query_classification": classification, "final_evidence": [], "stage_latency_ms": {"total": round((perf_counter() - started) * 1000, 3)}, "release_id": self.release_id, "source_snapshot_sha256": self.source_snapshot_sha256},
            }
        exact_started = perf_counter()
        exact = (
            self._exact_candidates(retrieval_query, temporal_scope=temporal_scope, as_of=as_of)
            if exact_lookup_enabled
            else []
        )
        if exact_lookup_enabled and candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}:
            # The r27 worker expands one user-authored law/article list into
            # independent exact keys. The legacy runtime parser accepts only
            # one article token, so add those bounded exact hits explicitly.
            r27_exact: list[dict[str, Any]] = []
            for key in explicit_law_article_keys_r27(query, maximum_distance=140):
                law, article = key.split("|", 1)
                # The legacy exact index searches references in chunk text as
                # well as the structured identity.  For r27 an explicit
                # citation is an identity constraint, so retain only rows
                # whose metadata is exactly the authored law/article pair.
                for candidate in self._exact_candidates(
                    f"Điều {article} của {law}",
                    temporal_scope=temporal_scope,
                    as_of=as_of,
                ):
                    if (
                        normalize_exact(candidate.get("law_number")) == law
                        and normalize_exact(candidate.get("article_number")) == article
                    ):
                        r27_exact.append(candidate)
            r27_ids = {str(row.get("chunk_revision_id") or "") for row in r27_exact}
            exact = [*r27_exact, *[item for item in exact if str(item.get("chunk_revision_id") or "") not in r27_ids]]
        exact_ms = (perf_counter() - exact_started) * 1000
        exact_plan = plan_exact_lookup(query)
        explicit_keys = explicit_law_article_keys_r27(query, maximum_distance=140)
        # An explicit citation is an authored identity constraint, not a
        # lexical hint.  The legacy exact index also matches references in
        # chunk text, so a question about Article 17 of 62/2020/QH14 could
        # otherwise surface Article 17 of a law merely mentioned nearby
        # (e.g. 65/2014/QH13).  Resolve the request to one or more exact
        # (law, article) pairs and apply the same filter to every branch
        # before fusion.  If the requested identity is absent from this
        # release, fail closed instead of substituting another law.
        requested_pairs: set[tuple[str, str]] = set()
        if explicit_keys:
            requested_pairs = {
                (normalize_exact(key.split("|", 1)[0]), normalize_exact(key.split("|", 1)[1]))
                for key in explicit_keys
                if "|" in key
            }
        elif (
            _LAW_RE.search(query) is not None
            and _ARTICLE_RE.search(query) is not None
            and len(exact_plan.article_law_pairs) == 1
        ):
            law_number, article_number = exact_plan.article_law_pairs[0]
            requested_pairs.add(
                (normalize_exact(law_number), normalize_exact(article_number))
            )

        exact_identity_filter: dict[str, Any] = {
            "enabled": bool(requested_pairs),
            "requested_pairs": [list(pair) for pair in sorted(requested_pairs)],
            "input_counts": {
                "exact": len(exact),
            },
        }

        def _keep_authored_identity(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
            if not requested_pairs:
                return [dict(item) for item in candidates]
            return [
                dict(item)
                for item in candidates
                if (
                    normalize_exact(item.get("law_number")),
                    normalize_exact(item.get("article_number")),
                ) in requested_pairs
            ]

        exact = _keep_authored_identity(exact)
        exact_identity_filter["output_counts"] = {"exact": len(exact)}
        exact_identity_missing = bool(requested_pairs) and not exact
        exact_article_fastpath = bool(
            EXACT_ARTICLE_FASTPATH
            # Exact identity lookup is safe for every supported release
            # profile.  Restricting it to the shadow profiles made the local
            # R26 serving path encode and run broad FTS for a citation that
            # the SQL exact index could already resolve.
            and candidate_profile in {
                R26_CANDIDATE_PROFILE,
                R27_CANDIDATE_PROFILE,
                R28_CANDIDATE_PROFILE,
            }
            and exact
            and bool(explicit_keys)
            and exact_plan.law_number
            and exact_plan.article_number
            and len(
                exact_plan.article_law_pairs
                or ((exact_plan.law_number, exact_plan.article_number),)
            )
            == 1
            and len(explicit_keys) <= 1
        )
        if exact_article_fastpath or exact_identity_missing:
            vector = []
            lexical = []
            vector_ms = 0.0
            lexical_ms = 0.0
        else:
            vector_started = perf_counter()
            vector = self._vector_candidates(
                retrieval_query,
                top_k=vector_top_k,
                temporal_scope=temporal_scope,
                as_of=as_of,
                domain=domain,
            )
            vector_ms = (perf_counter() - vector_started) * 1000
            lexical_started = perf_counter()
            lexical = self._lexical_candidates(
                retrieval_query,
                top_k=lexical_top_k,
                temporal_scope=temporal_scope,
                as_of=as_of,
                vector_candidates=vector,
                domain=domain,
                candidate_profile=candidate_profile,
            )
            lexical_ms = (perf_counter() - lexical_started) * 1000
            vector_before_filter = len(vector)
            lexical_before_filter = len(lexical)
            vector = _keep_authored_identity(vector)
            lexical = _keep_authored_identity(lexical)
            exact_identity_filter["input_counts"].update(
                {"vector": vector_before_filter, "lexical": lexical_before_filter}
            )
            exact_identity_filter["output_counts"].update(
                {"vector": len(vector), "lexical": len(lexical)}
            )
        union_started = perf_counter()
        # Diagnostic-only union before ranking. Fusion still receives the
        # original branches below, so this does not alter retrieval ordering.
        candidate_union = list(self._merge_candidates(exact, vector, lexical).values())
        union_ms = (perf_counter() - union_started) * 1000
        fusion_started = perf_counter()
        if candidate_profile not in {
            "v6r20",
            "r21-shadow",
            "r22-shadow",
            R26_CANDIDATE_PROFILE,
            R27_CANDIDATE_PROFILE,
            R28_CANDIDATE_PROFILE,
        }:
            raise V2RuntimeError(f"unsupported_candidate_profile:{candidate_profile}")
        # r20/v6r8 remains byte-compatible. r21 is shadow-only and removes a
        # law-number exact bonus unless another retrieval branch supports the
        # same immutable chunk.
        exact_for_fusion = (
            exact_candidates_for_r21(exact, vector, lexical)
            if candidate_profile in {
                "r21-shadow",
                "r22-shadow",
                R26_CANDIDATE_PROFILE,
                R27_CANDIDATE_PROFILE,
                R28_CANDIDATE_PROFILE,
            }
            else self._exact_for_fusion(exact)
        )
        fused = self._fuse(
            exact_for_fusion,
            vector,
            lexical,
            strategy=fusion_strategy,
            vector_weight=vector_weight,
            lexical_weight=lexical_weight,
        )
        dossier_recovery_rejections: list[dict[str, str]] = []
        if candidate_profile == R26_CANDIDATE_PROFILE:
            fused = diversify_r26_candidates(fused)
        elif candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}:
            # r27 keeps the r26 diversity windows, then reserves explicit
            # multi-article anchors before final evidence is truncated.
            fused = diversify_r26_candidates(fused)
            fused = _rerank_r27_explicit_anchors(query, fused)
            if candidate_profile == R28_CANDIDATE_PROFILE:
                if (
                    self.r28_catalog_mapping is None
                    or self.r28_catalog_acceptance is None
                    or self.r28_blocked_resolution is None
                ):
                    raise V2RuntimeError("r28_catalog_artifacts_required")
                classification_intents = {
                    str(classification.get("intent") or "").upper(),
                    *{
                        str(value or "").upper()
                        for value in (classification.get("secondary_intents") or [])
                    },
                }
                # Catalog aliases are designed for procedure/form discovery,
                # not as a generic synonym table for every legal question.
                # Restricting this exact-key lane prevents a broad authority
                # question from being hijacked by a similarly-worded catalog
                # entry while keeping procedure/form requests deterministic.
                catalog_alias_allowed = bool(
                    not classification
                    or classification_intents
                    & {"PROCEDURE", "PROCESS", "REQUIRED_DOCUMENTS", "FORM", "ELIGIBILITY"}
                    or str(classification.get("answer_type") or "").casefold()
                    in {"instructional", "form", "eligibility"}
                )
                hints = (
                    query_catalog_hints_r28(
                        query,
                        self.r28_catalog_mapping,
                        self.r28_catalog_acceptance,
                        blocked_resolution=self.r28_blocked_resolution,
                    )
                    if catalog_alias_allowed
                    else []
                )
                # Recovery is deliberately opt-in per query.  Applying broad
                # facet FTS to every ordinary one-issue question can promote
                # a generic heading and lower otherwise strong ANN/lexical
                # ranking.  Multi-facet prompts, approved catalog aliases and
                # authored citations are the bounded cases it is designed for.
                quoted_facets = len(
                    re.findall(r"[“\"][^”\"]{8,}[”\"]", query)
                )
                # Natural prompts often expose a legal heading after a cue
                # ("quy định về ...", "đối với ...") without quotation marks.
                # Only opt in when the extracted heading is title-like (at
                # least four meaningful tokens and an authored capital), so
                # ordinary conversational uses of "về" do not receive noisy
                # recovery rows.
                should_recover = bool(
                    hints
                    or quoted_facets > 1
                    or "đồng thời" in query.casefold()
                    or explicit_keys
                )
                if should_recover:
                    with self._db_guard():
                        recovery = recover_candidates_r28(
                            self.db,
                            query,
                            scope=temporal_scope,
                            as_of=as_of,
                            catalog_hints=hints,
                            maximum=30,
                        )
                    # Exact authored identities remain a hard constraint
                    # across every candidate branch, including recovery.
                    recovery = _keep_authored_identity(recovery)
                    required_documents = "REQUIRED_DOCUMENTS" in classification_intents
                    if required_documents:
                        from api.retrieval_candidate_r28 import required_documents_recovery_r28

                        recovery, dossier_recovery_rejections = required_documents_recovery_r28(
                            query, recovery
                        )
                    fused = merge_recovery_r28(
                        recovery,
                        fused,
                        slots=7,
                        preserve_article_chunks=required_documents,
                    )
        elif candidate_profile in {"r21-shadow", "r22-shadow"}:
            fused = diversify_legal_identities(fused)
        fusion_ms = (perf_counter() - fusion_started) * 1000
        reranker_candidates = list(fused)
        rerank_status: dict[str, Any] = {"mode": "disabled", "input": fused[:rerank_top_n], "output": fused[:rerank_top_n]}
        rerank_ms = 0.0
        if reranker is not None:
            rerank_started = perf_counter()
            outcome = reranker.rerank(retrieval_query, fused, top_n=rerank_top_n)
            reranker_candidates = list(outcome.candidates)
            rerank_status = outcome.public_status()
            rerank_status.update({"input": fused[:rerank_top_n], "output": reranker_candidates[:rerank_top_n]})
            rerank_ms = (perf_counter() - rerank_started) * 1000
        expansion_started = perf_counter()
        seeds = reranker_candidates[: max(1, int(final_evidence))]
        parent = self._expansion(seeds, kind="parent", temporal_scope=temporal_scope, as_of=as_of) if parent_expansion else []
        neighbor = self._expansion(seeds, kind="neighbor", temporal_scope=temporal_scope, as_of=as_of) if neighbor_expansion else []
        expanded = sorted(
            self._merge_candidates(parent, neighbor).values(),
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("chunk_revision_id") or ""),
            ),
        )
        expansion_ms = (perf_counter() - expansion_started) * 1000
        final = self._select_final_evidence(
            reranker_candidates,
            expanded,
            limit=final_evidence,
        )
        if candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE} and final:
            # r27/r28 anchor and recovery policies are ordering contracts. The
            # generic selector sorts by numeric score, which would undo the
            # bounded anchor reservation whenever RRF scores an exact row
            # below a high-frequency lexical hit. Re-apply only the
            # query-derived order; expansion rows remain after ranked seeds.
            order_by_id = {
                str(item.get("chunk_revision_id") or ""): index
                for index, item in enumerate(reranker_candidates)
            }
            final = sorted(
                final,
                key=lambda item: (
                    order_by_id.get(str(item.get("chunk_revision_id") or ""), 10**9),
                    -float(item.get("score") or 0.0),
                    str(item.get("chunk_revision_id") or ""),
                ),
            )[: max(1, int(final_evidence))]
        total_ms = (perf_counter() - started) * 1000
        stage_latency = {
            "exact_lookup": round(exact_ms, 3),
            "vector": round(vector_ms, 3),
            "lexical": round(lexical_ms, 3),
            "candidate_union": round(union_ms, 3),
            "fusion": round(fusion_ms, 3),
            "reranking": round(rerank_ms, 3),
            "expansion": round(expansion_ms, 3),
            "total": round(total_ms, 3),
        }
        trace = {
            "schema_version": "legal-retrieval-trace-m3-v1",
            "raw_query": query,
            "normalized_query": normalized_query,
            "query_classification": dict(query_classification or {}),
            "exact_candidates": exact,
            "vector_candidates": vector,
            "lexical_candidates": lexical,
            "candidate_union": candidate_union,
            "fusion_candidates": fused,
            "reranker_candidates": {"input": fused[:rerank_top_n], "output": reranker_candidates[:rerank_top_n], "status": rerank_status},
            "expanded_evidence": {"parent_contexts": parent, "neighbor_candidates": neighbor, "fallback_candidates": []},
            "final_evidence": final,
            "stage_latency_ms": stage_latency,
            "exact_article_fastpath": exact_article_fastpath,
            "exact_identity_filter": exact_identity_filter,
            "exact_identity_missing": exact_identity_missing,
            "release_id": self.release_id,
            "candidate_profile": candidate_profile,
            "dossier_recovery_rejections": dossier_recovery_rejections,
            "source_snapshot_sha256": self.source_snapshot_sha256,
        }
        return {"status": "ok", "results": final, "trace": trace, "timing_ms": stage_latency, "release_id": self.release_id}

    def search(
        self,
        query: str,
        *,
        legal_as_of: date | str,
        temporal_scope: str,
        domain: str | None = None,
        vector_top_k: int = 20,
        lexical_top_k: int = 20,
        fusion_strategy: str = "legacy_stack",
        vector_weight: float = 0.6,
        lexical_weight: float = 0.4,
        final_evidence: int = 10,
        reranker: Any | None = None,
        rerank_top_n: int = 20,
        parent_expansion: bool = False,
        neighbor_expansion: bool = False,
        exact_lookup_enabled: bool = True,
        issue_split_enabled: bool = True,
        candidate_profile: str = R26_CANDIDATE_PROFILE,
        query_classification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one query or independently retrieve explicitly planned issues.

        The planner remains the authority for deciding whether a query has
        multiple issues.  When it supplies ``issues`` (each with
        ``query_text``/``query``), every issue gets the same manifest, date and
        role filters, then results are merged by chunk revision ID.  A single
        issue follows the exact same path as before.
        """

        classification = dict(query_classification or {})
        raw_issues = (
            (classification.get("issues") or classification.get("issue_queries"))
            if issue_split_enabled
            else None
        )
        issues = [item for item in raw_issues if isinstance(item, Mapping)] if isinstance(raw_issues, list) else []
        if len(issues) <= 1:
            return self._search_single(
                query,
                legal_as_of=legal_as_of,
                temporal_scope=temporal_scope,
                domain=domain,
                vector_top_k=vector_top_k,
                lexical_top_k=lexical_top_k,
                fusion_strategy=fusion_strategy,
                vector_weight=vector_weight,
                lexical_weight=lexical_weight,
                final_evidence=final_evidence,
                reranker=reranker,
                rerank_top_n=rerank_top_n,
                parent_expansion=parent_expansion,
                neighbor_expansion=neighbor_expansion,
                exact_lookup_enabled=exact_lookup_enabled,
                candidate_profile=candidate_profile,
                query_classification=classification,
            )

        started = perf_counter()
        issue_responses: list[dict[str, Any]] = []
        for index, issue in enumerate(issues, start=1):
            issue_query = str(issue.get("query_text") or issue.get("query") or "").strip()
            if not issue_query:
                continue
            issue_classification = dict(issue.get("query_classification") or {})
            if not issue_classification:
                issue_classification = {
                    key: value
                    for key, value in classification.items()
                    if key not in {"issues", "issue_queries"}
                }
            issue_classification["issue_id"] = str(issue.get("issue_id") or f"issue-{index}")
            issue_response = self._search_single(
                issue_query,
                legal_as_of=legal_as_of,
                temporal_scope=temporal_scope,
                domain=domain,
                vector_top_k=vector_top_k,
                lexical_top_k=lexical_top_k,
                fusion_strategy=fusion_strategy,
                vector_weight=vector_weight,
                lexical_weight=lexical_weight,
                final_evidence=final_evidence,
                reranker=reranker,
                rerank_top_n=rerank_top_n,
                parent_expansion=parent_expansion,
                neighbor_expansion=neighbor_expansion,
                exact_lookup_enabled=exact_lookup_enabled,
                candidate_profile=candidate_profile,
                query_classification=issue_classification,
            )
            issue_id = issue_classification["issue_id"]
            issue_response["results"] = [
                {**dict(item), "issue_ids": sorted(set([*(item.get("issue_ids") or []), issue_id]))}
                for item in issue_response.get("results") or []
            ]
            issue_responses.append(issue_response)

        traces = [dict(response.get("trace") or {}) for response in issue_responses]
        exact = _round_robin_candidates(
            [list(trace.get("exact_candidates") or []) for trace in traces]
        )
        vector = _round_robin_candidates(
            [list(trace.get("vector_candidates") or []) for trace in traces]
        )
        lexical = _round_robin_candidates(
            [list(trace.get("lexical_candidates") or []) for trace in traces]
        )
        fused = _round_robin_candidates(
            [
                list(
                    trace.get("fusion_candidates")
                    or trace.get("final_evidence")
                    or []
                )
                for trace in traces
            ]
        )
        full_query_exact: list[dict[str, Any]] = []
        if candidate_profile in {
            "r21-shadow",
            "r22-shadow",
            R26_CANDIDATE_PROFILE,
            R27_CANDIDATE_PROFILE,
            R28_CANDIDATE_PROFILE,
        } and exact_lookup_enabled:
            explicit_keys = set(
                explicit_law_article_keys_r27(query, maximum_distance=140)
                if candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}
                else explicit_law_article_keys(query)
            )
            if explicit_keys:
                full_query_exact = [
                    item
                    for item in self._exact_candidates(
                        (
                            expand_legal_query_r26(
                                normalize_query(query), aliases=LEGAL_QUERY_ALIASES
                            )
                            if candidate_profile in {R26_CANDIDATE_PROFILE, R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}
                            else _expand_legal_query(normalize_query(query))
                        ),
                        temporal_scope=temporal_scope,
                        as_of=_as_date(legal_as_of),
                    )
                    if (
                        f"{normalize_exact(item.get('law_number'))}|"
                        f"{normalize_exact(item.get('article_number'))}"
                    )
                    in explicit_keys
                ]
                exact = _round_robin_candidates([full_query_exact, exact])
            anchored: list[dict[str, Any]] = []
            seen_anchor_ids: set[str] = set()
            for raw in [*full_query_exact, *fused]:
                item = dict(raw)
                identifier = str(item.get("chunk_revision_id") or "")
                if not identifier or identifier in seen_anchor_ids:
                    continue
                seen_anchor_ids.add(identifier)
                anchored.append(item)
            fused = (
                diversify_r26_candidates(anchored)
                if candidate_profile in {R26_CANDIDATE_PROFILE, R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}
                else diversify_legal_identities(anchored)
            )
            if candidate_profile in {R27_CANDIDATE_PROFILE, R28_CANDIDATE_PROFILE}:
                fused = _rerank_r27_explicit_anchors(query, fused)
        issue_ids_by_chunk: dict[str, set[str]] = {}
        for trace in traces:
            issue_id = str(
                (trace.get("query_classification") or {}).get("issue_id") or ""
            )
            for item in (
                trace.get("fusion_candidates")
                or trace.get("final_evidence")
                or []
            ):
                identifier = str(item.get("chunk_revision_id") or "")
                if identifier and issue_id:
                    issue_ids_by_chunk.setdefault(identifier, set()).add(issue_id)
        final = []
        for raw in fused[: max(1, int(final_evidence))]:
            item = dict(raw)
            item["issue_ids"] = sorted(
                issue_ids_by_chunk.get(str(item.get("chunk_revision_id") or ""), set())
            )
            final.append(item)
        stage_latency: dict[str, float] = {}
        for trace in traces:
            for key, value in (trace.get("stage_latency_ms") or {}).items():
                stage_latency[key] = stage_latency.get(key, 0.0) + float(value or 0.0)
        stage_latency["issue_orchestration"] = (perf_counter() - started) * 1000 - sum(
            value for key, value in stage_latency.items() if key != "total"
        )
        stage_latency["total"] = (perf_counter() - started) * 1000
        return {
            "status": "ok" if issue_responses else "clarification_required",
            "results": [dict(item) for item in final],
            "release_id": self.release_id,
            "timing_ms": stage_latency,
            "trace": {
                "schema_version": "legal-retrieval-trace-m3-v1",
                "raw_query": query,
                "normalized_query": normalize_query(query),
                "query_classification": classification,
                "candidate_profile": candidate_profile,
                "full_query_exact_anchor_count": len(full_query_exact),
                "issue_split": {
                    "enabled": True,
                    "issue_count": len(issue_responses),
                    "issues": [
                        {
                            "issue_id": str((item.get("query_classification") or {}).get("issue_id") or ""),
                            "raw_query": item.get("raw_query"),
                            "final_evidence": item.get("final_evidence") or [],
                        }
                        for item in traces
                    ],
                },
                "per_issue_traces": traces,
                "exact_candidates": exact,
                "vector_candidates": vector,
                "lexical_candidates": lexical,
                "candidate_union": list(
                    self._merge_candidates(exact, vector, lexical).values()
                ),
                "fusion_candidates": fused,
                "final_evidence": final,
                "stage_latency_ms": {key: round(value, 3) for key, value in stage_latency.items()},
                "release_id": self.release_id,
                "source_snapshot_sha256": self.source_snapshot_sha256,
            },
        }
