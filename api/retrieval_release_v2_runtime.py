"""Manifest-bound retrieval runtime for Retrieval Release V2 staging.

This adapter deliberately does not reuse the integer-ID V1 SQL retrieval path.
V2 chunk revision IDs are strings and the SQLite exact/FTS index plus Chroma
collection are bound to the same release, source snapshot and date filter.
It is usable by staging benchmarks only; activation remains a separate gate.
"""

from __future__ import annotations

from datetime import date
import json
import re
import sqlite3
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence

import chromadb

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from api.retrieval_serving_manifest_v3 import load_v3_serving_manifest


class V2RuntimeError(RuntimeError):
    """Raised when release fingerprints or serving boundaries do not match."""


_LAW_RE = re.compile(r"\b\d{1,5}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*(?:\s*-[A-ZĐ0-9-]+)*\b", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"\b(?:điều|dieu)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)
_PARAGRAPH_RE = re.compile(r"\b(?:khoản|khoan)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)


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


def _as_date(value: date | str | None) -> str:
    if value is None:
        return date.today().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _safe_fts_query(query: str) -> str:
    tokens = re.findall(r"[\wÀ-ỹĐđ]+", str(query or ""), flags=re.UNICODE)
    tokens = [token for token in tokens if len(token) > 1]
    return " OR ".join('"' + token.replace('"', ' ') + '"' for token in tokens[:32])


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
    ) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.lexical_index_path = Path(lexical_index_path).resolve()
        self.query_encoder = query_encoder
        if not self.manifest_path.is_file() or not self.lexical_index_path.is_file():
            raise V2RuntimeError("release_artifact_missing")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
        if self.manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
            raise V2RuntimeError("v2_manifest_required")
        if self.manifest.get("approved") is not True or self.manifest.get("legal_review_attestation") is not True:
            raise V2RuntimeError("approved_v2_manifest_required")
        self._manifest_chunk_ids = _manifest_chunk_allowlist(self.manifest)
        self.serving_manifest = None
        if serving_manifest_path is not None:
            try:
                loaded_serving = load_v3_serving_manifest(
                    serving_manifest_path,
                    project_root=Path(__file__).resolve().parents[1],
                    verify_file_artifacts=True,
                )
            except RuntimeError as exc:
                raise V2RuntimeError(str(exc)) from exc
            serving = dict(loaded_serving.payload)
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
        self.db = sqlite3.connect(f"file:{self.lexical_index_path.as_posix()}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        metadata = {
            str(row["key"]): str(row["value"])
            for row in self.db.execute("SELECT key,value FROM release_metadata")
        }
        for key, expected in (
            ("release_id", self.manifest.get("release_id")),
            ("source_snapshot_sha256", self.manifest.get("source_snapshot_sha256")),
            ("manifest_sha256", self.manifest.get("manifest_sha256")),
        ):
            if expected and metadata.get(key) != str(expected):
                raise V2RuntimeError(f"lexical_index_{key}_mismatch")
        self.release_id = str(self.manifest["release_id"])
        self.source_snapshot_sha256 = str(self.manifest["source_snapshot_sha256"])
        client = chromadb.PersistentClient(path=str(Path(chroma_path).resolve()))
        self.current_collection = client.get_collection(current_collection)
        self.temporal_collection = client.get_collection(temporal_collection)
        if self.serving_manifest is not None:
            expected_current = str((self.serving_manifest.get("current_collection") or {}).get("path") or "")
            expected_temporal = str((self.serving_manifest.get("temporal_collection") or {}).get("path") or "")
            if expected_current != f"chroma://{current_collection}" or expected_temporal != f"chroma://{temporal_collection}":
                raise V2RuntimeError("serving_manifest_collection_mismatch")
            if self.current_collection.count() != int((self.serving_manifest.get("current_collection") or {}).get("count") or -1):
                raise V2RuntimeError("current_collection_count_mismatch")
            if self.temporal_collection.count() != int((self.serving_manifest.get("temporal_collection") or {}).get("count") or -1):
                raise V2RuntimeError("temporal_collection_count_mismatch")
        for collection in (self.current_collection, self.temporal_collection):
            values = dict(collection.metadata or {})
            if values.get("release_id") != self.release_id:
                raise V2RuntimeError(f"collection_release_id_mismatch:{collection.name}")
            if values.get("source_snapshot_sha256") != self.source_snapshot_sha256:
                raise V2RuntimeError(f"collection_source_snapshot_mismatch:{collection.name}")
            if str(values.get("hnsw:space") or "") != "cosine":
                raise V2RuntimeError(f"collection_metric_mismatch:{collection.name}")

    def close(self) -> None:
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

    def _rows_for_ids(self, ids: Sequence[str], *, temporal_scope: str, as_of: str) -> dict[str, dict[str, Any]]:
        allowed = getattr(self, "_manifest_chunk_ids", None)
        if not allowed:
            raise V2RuntimeError("v2_manifest_eligible_chunk_allowlist_required")
        unique = list(
            dict.fromkeys(
                str(value)
                for value in ids
                if str(value) and str(value) in allowed
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
        return {str(row["chunk_revision_id"]): dict(row) for row in self.db.execute(statement, params)}

    def _exact_candidates(self, query: str, *, temporal_scope: str, as_of: str) -> list[dict[str, Any]]:
        law_numbers = [normalize_exact(match.group(0)) for match in _LAW_RE.finditer(query)]
        article_numbers = [normalize_exact(match.group(1)) for match in _ARTICLE_RE.finditer(query)]
        paragraph_numbers = [normalize_exact(match.group(1)) for match in _PARAGRAPH_RE.finditer(query)]
        keys: list[tuple[str, str]] = []
        for law in law_numbers:
            keys.append(("law_number", law))
            for article in article_numbers:
                keys.append(("law_article", f"{law}|{article}"))
        for article in article_numbers:
            keys.append(("article_number", article))
        for paragraph in paragraph_numbers:
            keys.append(("paragraph_number", paragraph))
        if not keys:
            return []
        clauses = " OR ".join("(key_kind = ? AND normalized_key = ?)" for _ in keys)
        key_params = [item for pair in keys for item in pair]
        statement = (
            "SELECT DISTINCT e.chunk_revision_id FROM exact_lookup e "
            f"WHERE {clauses} LIMIT 200"
        )
        ids = [str(row["chunk_revision_id"]) for row in self.db.execute(statement, key_params)]
        rows = self._rows_for_ids(ids, temporal_scope=temporal_scope, as_of=as_of)
        output = []
        for identifier in ids:
            row = rows.get(identifier)
            if row is not None:
                output.append({**row, "score": 1.0, "retrieval_source": "exact", "retrieval_sources": ["exact"]})
        return output

    def _lexical_candidates(self, query: str, *, top_k: int, temporal_scope: str, as_of: str) -> list[dict[str, Any]]:
        fts_query = _safe_fts_query(query)
        if not fts_query:
            return []
        scope_sql, scope_params = self._scope_sql(temporal_scope=temporal_scope, as_of=as_of)
        statement = (
            "SELECT c.*, bm25(chunk_fts) AS lexical_rank FROM chunk_fts "
            "JOIN chunks c ON c.chunk_revision_id = chunk_fts.chunk_revision_id "
            "WHERE chunk_fts MATCH ? AND " + scope_sql + " ORDER BY lexical_rank LIMIT ?"
        )
        rows = self.db.execute(statement, [fts_query, *scope_params, max(1, int(top_k))])
        output = []
        for row in rows:
            item = dict(row)
            rank = float(item.pop("lexical_rank") or 0.0)
            item.update({"score": 1.0 / (1.0 + max(0.0, rank)), "retrieval_source": "lexical", "retrieval_sources": ["lexical"]})
            output.append(item)
        return output

    def _vector_candidates(self, query: str, *, top_k: int, temporal_scope: str, as_of: str) -> list[dict[str, Any]]:
        collection = self.current_collection if temporal_scope == "current" else self.temporal_collection
        vector = self.query_encoder.encode_query(query)
        count = int(collection.count())
        if count <= 0:
            return []
        response = collection.query(
            query_embeddings=[vector.tolist()],
            # Keep the experiment variable honest: M5's vector Top-K is the
            # ANN request itself, not a fixed 100-result query followed by a
            # Python slice.  Date/manifest hydration still performs a second
            # fail-closed check before a result can be served.
            n_results=min(count, max(1, int(top_k))),
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
        return output[: max(1, int(top_k))]

    @staticmethod
    def _merge_candidates(*branches: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for branch in branches:
            for item in branch:
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
        return merged

    def _fuse(self, exact: list[dict[str, Any]], vector: list[dict[str, Any]], lexical: list[dict[str, Any]], *, strategy: str, vector_weight: float, lexical_weight: float) -> list[dict[str, Any]]:
        merged = self._merge_candidates(exact, vector, lexical)
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
            for item in merged.values():
                scores = item.get("branch_scores") or {}
                item["score"] = (
                    vector_weight * float(scores.get("vector") or 0.0)
                    + lexical_weight * float(scores.get("lexical") or 0.0)
                    + (1.0 if "exact" in (item.get("retrieval_sources") or []) else 0.0)
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
            for row in self.db.execute(statement, params):
                item = dict(row)
                identifier = str(item["chunk_revision_id"])
                if identifier not in self._manifest_chunk_ids:
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
        query_classification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        as_of = _as_date(legal_as_of)
        normalized_query = normalize_query(query)
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
            self._exact_candidates(normalized_query, temporal_scope=temporal_scope, as_of=as_of)
            if exact_lookup_enabled
            else []
        )
        exact_ms = (perf_counter() - exact_started) * 1000
        vector_started = perf_counter()
        vector = self._vector_candidates(normalized_query, top_k=vector_top_k, temporal_scope=temporal_scope, as_of=as_of)
        vector_ms = (perf_counter() - vector_started) * 1000
        lexical_started = perf_counter()
        lexical = self._lexical_candidates(normalized_query, top_k=lexical_top_k, temporal_scope=temporal_scope, as_of=as_of)
        lexical_ms = (perf_counter() - lexical_started) * 1000
        union_started = perf_counter()
        # Diagnostic-only union before ranking. Fusion still receives the
        # original branches below, so this does not alter retrieval ordering.
        candidate_union = list(self._merge_candidates(exact, vector, lexical).values())
        union_ms = (perf_counter() - union_started) * 1000
        fusion_started = perf_counter()
        fused = self._fuse(exact, vector, lexical, strategy=fusion_strategy, vector_weight=vector_weight, lexical_weight=lexical_weight)
        fusion_ms = (perf_counter() - fusion_started) * 1000
        reranker_candidates = list(fused)
        rerank_status: dict[str, Any] = {"mode": "disabled", "input": fused[:rerank_top_n], "output": fused[:rerank_top_n]}
        rerank_ms = 0.0
        if reranker is not None:
            rerank_started = perf_counter()
            outcome = reranker.rerank(normalized_query, fused, top_n=rerank_top_n)
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
            "release_id": self.release_id,
            "source_snapshot_sha256": self.source_snapshot_sha256,
        }
        return {"status": "ok", "results": final, "trace": trace, "timing_ms": stage_latency, "release_id": self.release_id}

    def search(
        self,
        query: str,
        *,
        legal_as_of: date | str,
        temporal_scope: str,
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
                query_classification=issue_classification,
            )
            issue_id = issue_classification["issue_id"]
            issue_response["results"] = [
                {**dict(item), "issue_ids": sorted(set([*(item.get("issue_ids") or []), issue_id]))}
                for item in issue_response.get("results") or []
            ]
            issue_responses.append(issue_response)

        issue_ids_by_chunk: dict[str, set[str]] = {}
        for response in issue_responses:
            for item in response.get("results") or []:
                identifier = str(item.get("chunk_revision_id") or "")
                if identifier:
                    issue_ids_by_chunk.setdefault(identifier, set()).update(
                        str(value) for value in item.get("issue_ids") or []
                    )
        merged_results = self._merge_candidates(
            *(response.get("results") or [] for response in issue_responses)
        )
        for identifier, item in merged_results.items():
            item["issue_ids"] = sorted(issue_ids_by_chunk.get(identifier, set()))
        final = sorted(
            merged_results.values(),
            key=lambda item: (-float(item.get("score") or 0.0), str(item.get("chunk_revision_id") or "")),
        )[: max(1, int(final_evidence))]
        traces = [dict(response.get("trace") or {}) for response in issue_responses]
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
                "final_evidence": final,
                "stage_latency_ms": {key: round(value, 3) for key, value in stage_latency.items()},
                "release_id": self.release_id,
                "source_snapshot_sha256": self.source_snapshot_sha256,
            },
        }
