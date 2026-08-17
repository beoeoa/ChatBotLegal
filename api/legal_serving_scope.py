"""Fail-closed, checksum-bound legal serving-manifest loader."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_SCHEMAS = {
    "legal-serving-baseline-v1",
    "legal-serving-baseline-v2",
    "legal-serving-candidate-v1",
    "legal-serving-manifest-v2",
}
VISIBILITY_AUDIENCES = {
    "public": frozenset({"citizen", "officer", "admin", "system"}),
    "officer": frozenset({"officer", "admin", "system"}),
    "admin": frozenset({"admin", "system"}),
    "internal": frozenset({"admin", "system"}),
}
VALID_AUDIENCES = frozenset({"citizen", "officer", "admin", "system"})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_manifest_sha256(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("manifest_sha256", None)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ServingManifestScope:
    path: Path
    file_sha256: str
    manifest_sha256: str
    schema_version: str
    collection_name: str
    legal_as_of: str
    document_ids: frozenset[int]
    chunk_ids: frozenset[int]
    manifest_version: str = "legacy"
    dataset_version: str = "legacy"
    benchmark_only: bool = True
    document_visibility: tuple[tuple[int, str], ...] = ()
    chunk_document_ids: tuple[tuple[int, int], ...] = ()

    def _audience(self, audience: str | None) -> str:
        value = str(audience or "citizen").strip().casefold()
        if value not in VALID_AUDIENCES:
            raise RuntimeError("serving_manifest_invalid_audience")
        return value

    def document_ids_for(self, audience: str | None) -> frozenset[int]:
        audience = self._audience(audience)
        if not self.document_visibility:
            return self.document_ids
        return frozenset(
            document_id
            for document_id, visibility in self.document_visibility
            if audience in VISIBILITY_AUDIENCES[visibility]
        )

    def chunk_ids_for(self, audience: str | None) -> frozenset[int]:
        allowed_documents = self.document_ids_for(audience)
        if not self.chunk_document_ids:
            return self.chunk_ids
        return frozenset(
            chunk_id
            for chunk_id, document_id in self.chunk_document_ids
            if document_id in allowed_documents
        )

    def allows_document(self, document_id: int, audience: str | None) -> bool:
        return int(document_id) in self.document_ids_for(audience)

    def allows_chunk(self, chunk_id: int, audience: str | None) -> bool:
        return int(chunk_id) in self.chunk_ids_for(audience)

    def public_status(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_version": self.manifest_version,
            "dataset_version": self.dataset_version,
            "manifest_sha256": self.manifest_sha256,
            "file_sha256": self.file_sha256,
            "collection_name": self.collection_name,
            "legal_as_of": self.legal_as_of,
            "document_count": len(self.document_ids),
            "chunk_count": len(self.chunk_ids),
            "benchmark_only": self.benchmark_only,
        }


def _positive_int(value: Any, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"serving_manifest_invalid_{field}") from exc
    if result <= 0:
        raise RuntimeError(f"serving_manifest_invalid_{field}")
    return result


def load_serving_manifest_scope(
    path: Path,
    *,
    expected_file_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    configured_collection: str | None = None,
) -> ServingManifestScope:
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"serving_manifest_missing:{path}")
    observed_file_sha256 = file_sha256(path)
    if expected_file_sha256 and observed_file_sha256 != expected_file_sha256:
        raise RuntimeError("serving_manifest_file_checksum_mismatch")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("serving_manifest_unreadable") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("serving_manifest_object_required")
    schema = str(payload.get("schema_version") or "")
    if schema == "legal-serving-manifest-v3":
        # V3 is an atomic release pointer with string chunk revision IDs and
        # current/temporal collections.  The legacy scope is integer-ID/V1
        # oriented; accepting the pointer here would silently apply the wrong
        # filtering contract to the live server.
        raise RuntimeError("v3_release_pointer_requires_v2_runtime")
    if schema not in SUPPORTED_SCHEMAS:
        raise RuntimeError("serving_manifest_schema_unsupported")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("serving_manifest_documents_required")

    runtime_schema = schema == "legal-serving-manifest-v2"
    manifest_sha256 = str(payload.get("manifest_sha256") or "").strip().lower()
    if runtime_schema:
        observed_manifest_sha256 = canonical_manifest_sha256(payload)
        if manifest_sha256 != observed_manifest_sha256:
            raise RuntimeError("serving_manifest_content_checksum_mismatch")
        if expected_manifest_sha256 and manifest_sha256 != expected_manifest_sha256:
            raise RuntimeError("serving_manifest_pointer_checksum_mismatch")

    document_ids: set[int] = set()
    chunk_ids: set[int] = set()
    duplicate_chunks: set[int] = set()
    visibility_rows: list[tuple[int, str]] = []
    chunk_document_rows: list[tuple[int, int]] = []
    for raw in documents:
        if not isinstance(raw, Mapping):
            raise RuntimeError("serving_manifest_document_object_required")
        document_id = _positive_int(raw.get("document_id"), field="document_id")
        if document_id in document_ids:
            raise RuntimeError("serving_manifest_duplicate_document_id")
        document_ids.add(document_id)
        raw_chunks = raw.get("chunk_ids" if runtime_schema else "expected_chunk_ids")
        if not isinstance(raw_chunks, list) or not raw_chunks:
            raise RuntimeError("serving_manifest_expected_chunks_required")
        if runtime_schema:
            visibility = str(raw.get("visibility") or "").strip().casefold()
            if visibility not in VISIBILITY_AUDIENCES:
                raise RuntimeError("serving_manifest_visibility_invalid")
            required_fields = {
                "law_number", "status", "domain", "reason_kept",
                "score_components", "required_procedures", "source_id",
                "validity_confidence",
            }
            if not required_fields.issubset(raw):
                raise RuntimeError("serving_manifest_document_schema_incomplete")
            visibility_rows.append((document_id, visibility))
        for value in raw_chunks:
            chunk_id = _positive_int(value, field="chunk_id")
            if chunk_id in chunk_ids:
                duplicate_chunks.add(chunk_id)
            chunk_ids.add(chunk_id)
            if runtime_schema:
                chunk_document_rows.append((chunk_id, document_id))
    if duplicate_chunks:
        raise RuntimeError("serving_manifest_duplicate_chunk_id")

    if runtime_schema:
        collection = str(payload.get("collection_name") or "").strip()
        expected_documents = int(payload.get("document_count") or 0)
        expected_chunks = int(payload.get("chunk_count") or 0)
        manifest_version = str(payload.get("manifest_version") or "").strip()
        dataset_version = str(payload.get("dataset_version") or "").strip()
        if not manifest_version or not dataset_version:
            raise RuntimeError("serving_manifest_version_required")
    elif schema == "legal-serving-candidate-v1":
        collection = str(payload.get("candidate_collection") or "").strip()
        expected_documents = int(payload.get("selected_document_count") or 0)
        expected_chunks = int(payload.get("expected_vector_count") or 0)
        manifest_version = dataset_version = "legacy-candidate-v1"
    else:
        collection = str((payload.get("collection") or {}).get("collection_name") or "").strip()
        expected_documents = int((payload.get("database") or {}).get("document_count") or 0)
        expected_chunks = int((payload.get("database") or {}).get("chunk_count") or 0)
        manifest_version = dataset_version = "legacy-baseline"
    if not collection:
        raise RuntimeError("serving_manifest_collection_required")
    if expected_documents != len(document_ids):
        raise RuntimeError("serving_manifest_document_count_mismatch")
    if expected_chunks != len(chunk_ids):
        raise RuntimeError("serving_manifest_chunk_count_mismatch")
    if configured_collection and collection != configured_collection:
        raise RuntimeError("serving_manifest_collection_mismatch")

    return ServingManifestScope(
        path=path,
        file_sha256=observed_file_sha256,
        manifest_sha256=manifest_sha256,
        schema_version=schema,
        collection_name=collection,
        legal_as_of=str(payload.get("legal_as_of") or ""),
        document_ids=frozenset(document_ids),
        chunk_ids=frozenset(chunk_ids),
        manifest_version=manifest_version,
        dataset_version=dataset_version,
        benchmark_only=not runtime_schema,
        document_visibility=tuple(visibility_rows),
        chunk_document_ids=tuple(chunk_document_rows),
    )


def load_serving_manifest_pointer(
    pointer_path: Path,
    *, configured_collection: str | None = None,
) -> ServingManifestScope:
    pointer_path = pointer_path.resolve()
    if not pointer_path.is_file():
        raise RuntimeError(f"serving_manifest_pointer_missing:{pointer_path}")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("serving_manifest_pointer_unreadable") from exc
    if not isinstance(pointer, Mapping) or pointer.get("schema_version") != "legal-serving-pointer-v1":
        raise RuntimeError("serving_manifest_pointer_schema_invalid")
    raw_target = str(pointer.get("manifest_path") or "").strip()
    target = Path(raw_target)
    if not raw_target or target.is_absolute() or ".." in target.parts:
        raise RuntimeError("serving_manifest_pointer_path_invalid")
    resolved_target = (pointer_path.parent / target).resolve()
    if pointer_path.parent not in resolved_target.parents:
        raise RuntimeError("serving_manifest_pointer_path_invalid")
    return load_serving_manifest_scope(
        resolved_target,
        expected_file_sha256=str(pointer.get("file_sha256") or "").strip().lower() or None,
        expected_manifest_sha256=str(pointer.get("manifest_sha256") or "").strip().lower() or None,
        configured_collection=configured_collection,
    )


def benchmark_scope_from_environment(*, configured_collection: str) -> ServingManifestScope | None:
    enabled = _truthy(os.getenv("LEGAL_BENCHMARK_MODE"))
    raw_path = str(os.getenv("LEGAL_BENCHMARK_SERVING_MANIFEST") or "").strip()
    if raw_path and not enabled:
        raise RuntimeError("serving_manifest_requires_benchmark_mode")
    if enabled and not raw_path:
        raise RuntimeError("benchmark_mode_requires_serving_manifest")
    if not enabled:
        return None
    return load_serving_manifest_scope(
        Path(raw_path),
        expected_file_sha256=str(os.getenv("LEGAL_BENCHMARK_SERVING_MANIFEST_FILE_SHA256") or "").strip() or None,
        configured_collection=configured_collection,
    )


def serving_scope_from_environment(*, configured_collection: str) -> ServingManifestScope | None:
    benchmark = benchmark_scope_from_environment(configured_collection=configured_collection)
    if benchmark is not None:
        return benchmark
    pointer = str(os.getenv("LEGAL_SERVING_MANIFEST_POINTER") or "").strip()
    direct = str(os.getenv("LEGAL_SERVING_MANIFEST") or "").strip()
    required = _truthy(os.getenv("LEGAL_SERVING_MANIFEST_REQUIRED"))
    if pointer and direct:
        raise RuntimeError("serving_manifest_configuration_ambiguous")
    if pointer:
        return load_serving_manifest_pointer(Path(pointer), configured_collection=configured_collection)
    if direct:
        return load_serving_manifest_scope(Path(direct), configured_collection=configured_collection)
    if required:
        raise RuntimeError("serving_manifest_required")
    return None


__all__ = [
    "SUPPORTED_SCHEMAS", "VALID_AUDIENCES", "VISIBILITY_AUDIENCES",
    "ServingManifestScope", "benchmark_scope_from_environment",
    "canonical_manifest_sha256", "file_sha256", "load_serving_manifest_pointer",
    "load_serving_manifest_scope", "serving_scope_from_environment",
]
