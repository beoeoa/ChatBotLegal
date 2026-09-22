"""Strict loader for the Retrieval Release V2 serving pointer.

The V3 serving pointer is an atomic description of a release, not a list of
integer V1 chunk IDs.  It is intentionally separate from
``api.legal_serving_scope``: the legacy server cannot consume V3 string chunk
revision IDs or switch between current and temporal collections safely.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from api.retrieval_release_contracts import canonical_sha256, file_sha256


V3_SCHEMA = "legal-serving-manifest-v3"
V3_KIND = "release_pointer"
_SHA256_LEN = 64
_DOCUMENT_STATES = frozenset(
    {"current_retrievable", "historical_only", "future_effective", "quarantined"}
)
_CANDIDATE_POLICY_ARTIFACTS = frozenset(
    {"article_mapping", "owner_acceptance", "blocked_resolution"}
)


class V3ServingManifestError(RuntimeError):
    """Raised when a V3 pointer is incomplete, mutated or unsafe to consume."""


def _sha(value: Any, *, field: str) -> str:
    observed = str(value or "").strip().lower()
    if len(observed) != _SHA256_LEN or any(char not in "0123456789abcdef" for char in observed):
        raise V3ServingManifestError(f"v3_manifest_invalid_{field}")
    return observed


def _positive_or_zero(value: Any, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise V3ServingManifestError(f"v3_manifest_invalid_{field}") from exc
    if result < 0:
        raise V3ServingManifestError(f"v3_manifest_invalid_{field}")
    return result


def _artifact(payload: Mapping[str, Any], *, field: str, allow_chroma: bool) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise V3ServingManifestError(f"v3_manifest_{field}_required")
    path = str(value.get("path") or "").strip()
    if not path or (not allow_chroma and path.startswith("chroma://")):
        raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
    if allow_chroma and not path.startswith("chroma://"):
        raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
    release_id = str(value.get("release_id") or "")
    source_snapshot = str(value.get("source_snapshot_sha256") or "")
    if not release_id:
        raise V3ServingManifestError(f"v3_manifest_{field}_release_id_required")
    _sha(source_snapshot, field=f"{field}_source_snapshot_sha256")
    _sha(value.get("sha256"), field=f"{field}_sha256")
    _positive_or_zero(value.get("count"), field=f"{field}_count")
    return dict(value)


@dataclass(frozen=True)
class V3ServingManifest:
    """Validated, immutable view of an unactivated V3 release pointer."""

    path: Path
    file_sha256: str
    manifest_sha256: str
    payload: Mapping[str, Any]

    @property
    def release_id(self) -> str:
        return str(self.payload["release_id"])

    @property
    def source_snapshot_sha256(self) -> str:
        return str(self.payload["source_snapshot_sha256"])

    @property
    def current_collection(self) -> str:
        return str(self.payload["current_collection"]["path"])[len("chroma://"):]

    @property
    def temporal_collection(self) -> str:
        return str(self.payload["temporal_collection"]["path"])[len("chroma://"):]

    def artifact_path(self, field: str, *, project_root: Path) -> Path:
        artifact = self.payload.get(field)
        if not isinstance(artifact, Mapping):
            raise V3ServingManifestError(f"v3_manifest_{field}_required")
        raw = Path(str(artifact.get("path") or ""))
        if raw.is_absolute() or ".." in raw.parts:
            raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
        resolved = (project_root.resolve() / raw).resolve()
        if project_root.resolve() not in resolved.parents:
            raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
        return resolved

    def declared_file_path(self, field: str, *, project_root: Path) -> Path:
        raw_value = self.payload.get(field)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
        raw = Path(raw_value)
        if raw.is_absolute() or ".." in raw.parts:
            raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
        resolved = (project_root.resolve() / raw).resolve()
        if project_root.resolve() not in resolved.parents:
            raise V3ServingManifestError(f"v3_manifest_{field}_path_invalid")
        return resolved


def load_v3_serving_manifest(
    path: str | Path,
    *,
    expected_file_sha256: str | None = None,
    project_root: str | Path | None = None,
    verify_file_artifacts: bool = False,
    allow_provisional_staging: bool = False,
) -> V3ServingManifest:
    """Load and validate a V3 pointer without changing any active state."""

    pointer_path = Path(path).resolve()
    if not pointer_path.is_file():
        raise V3ServingManifestError("v3_manifest_missing")
    observed_file_sha = file_sha256(pointer_path)
    if expected_file_sha256 and observed_file_sha != str(expected_file_sha256).strip().lower():
        raise V3ServingManifestError("v3_manifest_file_checksum_mismatch")
    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3ServingManifestError("v3_manifest_unreadable") from exc
    if not isinstance(payload, Mapping):
        raise V3ServingManifestError("v3_manifest_object_required")
    if payload.get("schema_version") != V3_SCHEMA or payload.get("kind") != V3_KIND:
        raise V3ServingManifestError("v3_release_pointer_required")
    if payload.get("activation_performed") is not False or payload.get("active_pointer_changed") is not False:
        raise V3ServingManifestError("v3_manifest_activation_contract_failed")
    provisional_staging = payload.get("provisional_staging") is True
    release_eligible = payload.get("release_eligible", True) is True
    if provisional_staging or not release_eligible:
        if not allow_provisional_staging:
            raise V3ServingManifestError("v3_manifest_not_release_eligible")
        if not provisional_staging or release_eligible:
            raise V3ServingManifestError("v3_manifest_provisional_marker_invalid")
    release_id = str(payload.get("release_id") or "")
    if not release_id:
        raise V3ServingManifestError("v3_manifest_release_id_required")
    source_snapshot = _sha(payload.get("source_snapshot_sha256"), field="source_snapshot_sha256")
    _sha(payload.get("chunk_manifest_sha256"), field="chunk_manifest_sha256")
    _sha(payload.get("chunk_manifest_file_sha256"), field="chunk_manifest_file_sha256")
    manifest_sha = _sha(payload.get("manifest_sha256"), field="manifest_sha256")
    projection = dict(payload)
    projection.pop("manifest_sha256", None)
    if canonical_sha256(projection) != manifest_sha:
        raise V3ServingManifestError("v3_manifest_content_checksum_mismatch")

    if _positive_or_zero(payload.get("inventory_document_count"), field="inventory_document_count") != 12_236:
        raise V3ServingManifestError("v3_manifest_inventory_document_count_mismatch")
    states = payload.get("document_state_counts")
    if not isinstance(states, Mapping) or set(states) != _DOCUMENT_STATES:
        raise V3ServingManifestError("v3_manifest_document_states_required")
    state_counts = {state: _positive_or_zero(states.get(state), field=f"document_state_{state}") for state in _DOCUMENT_STATES}
    if sum(state_counts.values()) != 12_236:
        raise V3ServingManifestError("v3_manifest_document_state_partition_mismatch")
    documents = payload.get("documents")
    if not isinstance(documents, list) or len(documents) != 12_236:
        raise V3ServingManifestError("v3_manifest_documents_count_mismatch")
    seen: set[int] = set()
    observed_states = {state: 0 for state in _DOCUMENT_STATES}
    for row in documents:
        if not isinstance(row, Mapping):
            raise V3ServingManifestError("v3_manifest_document_row_invalid")
        try:
            document_id = int(row.get("document_id"))
        except (TypeError, ValueError) as exc:
            raise V3ServingManifestError("v3_manifest_document_id_invalid") from exc
        if document_id <= 0 or document_id in seen:
            raise V3ServingManifestError("v3_manifest_duplicate_document_id")
        seen.add(document_id)
        state = str(row.get("serving_state") or "")
        if state not in _DOCUMENT_STATES:
            raise V3ServingManifestError("v3_manifest_document_state_invalid")
        count = _positive_or_zero(row.get("chunk_count"), field="document_chunk_count")
        _sha(row.get("chunk_ids_sha256"), field="document_chunk_ids_sha256")
        if state in {"future_effective", "quarantined"} and count != 0:
            raise V3ServingManifestError(
                f"v3_manifest_{state}_document_has_chunks"
            )
        if state in {"current_retrievable", "historical_only"} and count == 0:
            raise V3ServingManifestError("v3_manifest_retrievable_document_has_no_chunks")
        observed_states[state] += 1
    if observed_states != state_counts:
        raise V3ServingManifestError("v3_manifest_document_state_count_mismatch")

    if payload.get("quality_policy_version") != "legal-chunk-quality-v2":
        raise V3ServingManifestError("v3_manifest_quality_policy_mismatch")
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("quality_policy_version") != "legal-chunk-quality-v2":
        raise V3ServingManifestError("v3_manifest_provenance_required")
    for field in (
        "model_artifact_fingerprint",
        "tokenizer_fingerprint",
        "embedding_recipe_fingerprint",
        "passage_recipe_fingerprint",
        "splitter_fingerprint",
        "dependency_lock_fingerprint",
    ):
        _sha(provenance.get(field), field=f"provenance_{field}")

    current = _artifact(payload, field="current_collection", allow_chroma=True)
    temporal = _artifact(payload, field="temporal_collection", allow_chroma=True)
    lexical = _artifact(payload, field="exact_lexical_index", allow_chroma=False)
    for field, artifact in (("current_collection", current), ("temporal_collection", temporal), ("exact_lexical_index", lexical)):
        if str(artifact["release_id"]) != release_id or str(artifact["source_snapshot_sha256"]) != source_snapshot:
            raise V3ServingManifestError(f"v3_manifest_{field}_fingerprint_mismatch")
    if not str(payload.get("manifest_version") or "") or not str(payload.get("dataset_version") or ""):
        raise V3ServingManifestError("v3_manifest_version_required")

    candidate_policy = payload.get("candidate_policy")
    if candidate_policy is not None:
        if not isinstance(candidate_policy, Mapping) or not str(
            candidate_policy.get("profile") or ""
        ).strip():
            raise V3ServingManifestError("v3_manifest_candidate_policy_invalid")
        candidate_artifacts = candidate_policy.get("artifacts")
        if (
            not isinstance(candidate_artifacts, Mapping)
            or set(candidate_artifacts) != _CANDIDATE_POLICY_ARTIFACTS
        ):
            raise V3ServingManifestError(
                "v3_manifest_candidate_policy_artifacts_required"
            )
        for name, descriptor in candidate_artifacts.items():
            if not isinstance(descriptor, Mapping):
                raise V3ServingManifestError(
                    f"v3_manifest_candidate_policy_{name}_invalid"
                )
            raw = Path(str(descriptor.get("path") or ""))
            if not str(raw) or raw.is_absolute() or ".." in raw.parts:
                raise V3ServingManifestError(
                    f"v3_manifest_candidate_policy_{name}_path_invalid"
                )
            _sha(
                descriptor.get("sha256"),
                field=f"candidate_policy_{name}_sha256",
            )

    root = Path(project_root).resolve() if project_root is not None else pointer_path.parents[2]
    if verify_file_artifacts:
        loaded = V3ServingManifest(
            path=pointer_path,
            file_sha256=observed_file_sha,
            manifest_sha256=manifest_sha,
            payload=payload,
        )
        artifact_file = loaded.artifact_path("exact_lexical_index", project_root=root)
        if not artifact_file.is_file() or file_sha256(artifact_file) != str(payload["exact_lexical_index"]["sha256"]):
            raise V3ServingManifestError("v3_manifest_exact_lexical_index_checksum_mismatch")
        declared_files = (
            ("chunk_manifest_path", "chunk_manifest_file_sha256"),
            ("source_inventory_path", "source_inventory_file_sha256"),
        )
        for path_field, checksum_field in declared_files:
            if payload.get(path_field) is None:
                continue
            declared_file = loaded.declared_file_path(path_field, project_root=root)
            if not declared_file.is_file() or file_sha256(declared_file) != str(payload.get(checksum_field) or ""):
                raise V3ServingManifestError(f"v3_manifest_{path_field}_checksum_mismatch")
        if isinstance(candidate_policy, Mapping):
            for name, descriptor in (
                candidate_policy.get("artifacts") or {}
            ).items():
                raw = Path(str(descriptor.get("path") or ""))
                candidate_path = (root / raw).resolve()
                if (
                    root not in candidate_path.parents
                    or not candidate_path.is_file()
                    or file_sha256(candidate_path)
                    != str(descriptor.get("sha256") or "")
                ):
                    raise V3ServingManifestError(
                        f"v3_manifest_candidate_policy_{name}_checksum_mismatch"
                    )
        reports = payload.get("vector_reports") or {}
        report_checksums = payload.get("vector_report_checksums") or {}
        if reports or report_checksums:
            if set(reports) != {"current", "temporal"} or set(report_checksums) != {"current", "temporal"}:
                raise V3ServingManifestError("v3_manifest_vector_reports_incomplete")
            for key in ("current", "temporal"):
                report_path = Path(str(reports[key]))
                if report_path.is_absolute() or ".." in report_path.parts:
                    raise V3ServingManifestError("v3_manifest_vector_report_path_invalid")
                resolved_report = (root / report_path).resolve()
                if root not in resolved_report.parents or not resolved_report.is_file() or file_sha256(resolved_report) != str(report_checksums[key]):
                    raise V3ServingManifestError(f"v3_manifest_vector_report_{key}_checksum_mismatch")

    return V3ServingManifest(
        path=pointer_path,
        file_sha256=observed_file_sha,
        manifest_sha256=manifest_sha,
        payload=payload,
    )


__all__ = ["V3ServingManifest", "V3ServingManifestError", "load_v3_serving_manifest"]
