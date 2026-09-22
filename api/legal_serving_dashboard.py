"""Read-only projections of the active Retrieval Release V2.

The management UI must describe the same immutable release that serves search.
This module deliberately has no mutation path and never changes a pointer or a
vector collection.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from api.retrieval_release_contracts import file_sha256
from api.retrieval_serving_manifest_v3 import V3ServingManifest, load_v3_serving_manifest
from api.legal_serving_scope import serving_scope_from_environment


ROOT = Path(__file__).resolve().parents[1]
POINTER_PATH = ROOT / "release-data" / "legal" / "serving_manifests" / "active_retrieval_v2_pointer.json"


@dataclass(frozen=True)
class ScopedDashboardManifest:
    """Read-model adapter for the configured integer-chunk serving contract.

    This is not a V3 manifest and never rewrites or activates a release.
    SQL still restricts eligibility according to current lifecycle metadata.
    """
    release_id: str
    manifest_sha256: str
    current_collection: str
    temporal_collection: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ActiveServingRelease:
    """The document scope and provenance needed by read-only management views."""

    pointer_path: Path
    manifest: V3ServingManifest | ScopedDashboardManifest
    document_states: dict[str, tuple[int, ...]]

    @property
    def release_id(self) -> str:
        return self.manifest.release_id

    @property
    def legal_as_of(self) -> str:
        return str(self.manifest.payload.get("legal_as_of") or "")

    @property
    def manifest_sha256(self) -> str:
        return self.manifest.manifest_sha256

    @property
    def retrievable_document_ids(self) -> tuple[int, ...]:
        return tuple(sorted((*self.document_states.get("current_retrievable", ()), *self.document_states.get("historical_only", ()))))

    @property
    def current_document_ids(self) -> tuple[int, ...]:
        return self.document_states.get("current_retrievable", ())

    @property
    def temporal_document_ids(self) -> tuple[int, ...]:
        return self.retrievable_document_ids

    @property
    def future_document_ids(self) -> tuple[int, ...]:
        return self.document_states.get("future_effective", ())

    def state_for(self, document_id: int | str) -> str:
        """Return the manifest state without inferring legal status from SQL."""

        wanted = int(document_id)
        for state, document_ids in self.document_states.items():
            if wanted in document_ids:
                return state
        return "quarantined"

    def cards(self) -> dict[str, int | None]:
        """Return only counts that the manifest can prove by itself.

        Date-dependent cards must be reconciled with legal metadata by the
        management service. Returning ``None`` keeps an unavailable value from
        being presented as a real zero, and historical-only must never be
        mislabeled as confirmed-expired.
        """

        states = self.manifest.payload.get("document_state_counts") or {}
        return {
            "total_retrievable": len(self.retrievable_document_ids),
            "current_effective": None if isinstance(self.manifest, ScopedDashboardManifest) else int(states.get("current_retrievable") or 0),
            "expired_total": None,
            "expiring_30": None,
            "effective_30": None,
        }


def load_active_serving_release(
    *, pointer_path: str | Path | None = None,
) -> ActiveServingRelease:
    """Load the active pointer and its immutable V3 manifest read-only.

    ``load_v3_serving_manifest`` validates the release artifact itself. The
    activation pointer has a separate schema and is checked here so an old
    pointer or a mismatched manifest cannot silently feed the dashboard.
    """

    if pointer_path is None and (os.getenv("LEGAL_SERVING_MANIFEST") or os.getenv("LEGAL_SERVING_MANIFEST_POINTER") or os.getenv("LEGAL_SERVING_MANIFEST_REQUIRED")):
        scope = serving_scope_from_environment(configured_collection=os.getenv("LEGAL_CHROMA_COLLECTION", "").strip())
        if scope is not None:
            # The approved V2 scope is the exact same contract used by search.
            # Current/date eligibility is reconciled by inventory_projection.
            ids = tuple(sorted(scope.document_ids_for("admin")))
            raw_scope = json.loads(scope.path.read_text(encoding="utf-8-sig"))
            manifest = ScopedDashboardManifest(
                release_id=scope.dataset_version,
                manifest_sha256=scope.manifest_sha256,
                current_collection=scope.collection_name,
                temporal_collection=scope.collection_name,
                payload={
                    "schema_version": scope.schema_version,
                    "legal_as_of": scope.legal_as_of,
                    "document_state_counts": {"current_retrievable": len(ids)},
                    "current_collection": {"count": len(scope.chunk_ids)},
                    "temporal_collection": {"count": len(scope.chunk_ids)},
                    "eligibility_authority": "legal_documents+legal_search_scope",
                    "embedding_fingerprint": str(raw_scope.get("embedding_fingerprint") or ""),
                },
            )
            return ActiveServingRelease(scope.path, manifest, {"current_retrievable": ids})
    pointer = Path(pointer_path or POINTER_PATH).resolve()
    payload = json.loads(pointer.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("status") != "ACTIVE_QA_PRODUCTION_ROUTE":
        raise RuntimeError("active_retrieval_v2_pointer_invalid")
    manifest_rel = str(payload.get("manifest_path") or "").strip()
    manifest_path = (ROOT / manifest_rel).resolve()
    if ROOT.resolve() not in manifest_path.parents or not manifest_path.is_file():
        raise RuntimeError("active_retrieval_v2_manifest_missing")
    declared_file_sha = str(payload.get("manifest_file_sha256") or "").lower()
    if declared_file_sha and file_sha256(manifest_path) != declared_file_sha:
        raise RuntimeError("active_retrieval_v2_manifest_file_checksum_mismatch")
    manifest = load_v3_serving_manifest(manifest_path, project_root=ROOT)
    if str(payload.get("release_id") or "") != manifest.release_id:
        raise RuntimeError("active_retrieval_v2_release_mismatch")
    if str(payload.get("manifest_sha256") or "") != manifest.manifest_sha256:
        raise RuntimeError("active_retrieval_v2_manifest_checksum_mismatch")
    states: dict[str, list[int]] = {}
    for row in manifest.payload.get("documents") or []:
        state = str(row.get("serving_state") or "")
        states.setdefault(state, []).append(int(row["document_id"]))
    return ActiveServingRelease(
        pointer_path=pointer,
        manifest=manifest,
        document_states={key: tuple(sorted(value)) for key, value in states.items()},
    )


__all__ = ["ActiveServingRelease", "POINTER_PATH", "load_active_serving_release"]
