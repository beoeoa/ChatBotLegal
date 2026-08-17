from pathlib import Path

import pytest

from api.legal_vector_cleanup import (
    VectorCleanupManifestStore,
    build_vector_cleanup_manifest,
    execute_exact_vector_cleanup,
)


def _snapshot(*, action: str = "historical_only", status: str = "replaced"):
    return {
        "schema_version": "legal-validity-serving-v1",
        "document_ids": {"42": "12/2020/NĐ-CP"},
        "documents": {
            "12/2020/NĐ-CP": {
                "document_id": "42",
                "normalized_status": status,
                "serving_action": action,
                "identity_status": "exact",
                "evidence_status": "sufficient",
                "fingerprint": "official-fingerprint",
                "source_url": "https://vbpl.vn/van-ban/example",
            }
        },
    }


class FakeCollection:
    def __init__(self, ids, *, fail_delete: bool = False):
        self.ids = set(ids)
        self.fail_delete = fail_delete
        self.deleted = []

    def get(self, *, ids, include):
        return {"ids": [item for item in ids if item in self.ids]}

    def delete(self, *, ids):
        if self.fail_delete:
            raise RuntimeError("collection unavailable")
        self.deleted.append(list(ids))
        self.ids.difference_update(ids)


def test_manifest_requires_verified_full_document_block():
    manifest = build_vector_cleanup_manifest(
        document_id="42",
        law_number="12/2020/NĐ-CP",
        chunk_ids=[9, 7, 9],
        snapshot=_snapshot(),
        requested_by="user:admin",
        reason="Dọn vector sau khi đã chặn văn bản hết hiệu lực.",
    )

    assert manifest["state"] == "blocking_applied"
    assert manifest["blocking_applied"] is True
    assert manifest["vector_ids"] == ["chunk-7", "chunk-9"]
    assert manifest["source_fingerprint"] == "official-fingerprint"
    assert manifest["legal_source_url"].startswith("https://vbpl.vn/")

    with pytest.raises(ValueError, match="verified_full_document_block_required"):
        build_vector_cleanup_manifest(
            document_id="42",
            law_number="12/2020/NĐ-CP",
            chunk_ids=[7],
            snapshot=_snapshot(action="block_provisions", status="expired_partial"),
            requested_by="user:admin",
            reason="Không được xóa toàn văn khi chỉ hết hiệu lực một phần.",
        )

    with pytest.raises(ValueError, match="verified_full_document_block_required"):
        build_vector_cleanup_manifest(
            document_id="42",
            law_number="12/2020/NĐ-CP",
            chunk_ids=[7],
            snapshot=_snapshot(action="block_document", status="unknown"),
            requested_by="user:admin",
            reason="Không xóa vector chỉ vì trạng thái chưa rõ.",
        )


def test_cleanup_deletes_exact_ids_in_both_collections_and_is_idempotent():
    collections = {
        "fast": FakeCollection(["chunk-7", "chunk-9", "chunk-100"]),
        "expanded": FakeCollection(["chunk-7", "chunk-9", "chunk-200"]),
    }

    first = execute_exact_vector_cleanup(collections, ["chunk-7", "chunk-9"])
    second = execute_exact_vector_cleanup(collections, ["chunk-7", "chunk-9"])

    assert first["state"] == "vector_cleanup_completed"
    assert first["collections"]["fast"]["before"] == 2
    assert first["collections"]["expanded"]["after"] == 0
    assert collections["fast"].ids == {"chunk-100"}
    assert collections["expanded"].ids == {"chunk-200"}
    assert second["state"] == "vector_cleanup_completed"
    assert second["already_absent"] is True


def test_cleanup_failure_stays_partial_and_never_reports_success():
    report = execute_exact_vector_cleanup(
        {
            "fast": FakeCollection(["chunk-7"]),
            "expanded": FakeCollection(["chunk-7"], fail_delete=True),
        },
        ["chunk-7"],
    )

    assert report["state"] == "vector_cleanup_partial"
    assert report["collections"]["fast"]["after"] == 0
    assert report["collections"]["expanded"]["error_code"] == "vector_delete_failed"


def test_cleanup_with_no_manifest_ids_is_a_verified_noop():
    report = execute_exact_vector_cleanup(
        {"fast": FakeCollection([]), "expanded": FakeCollection([])}, []
    )

    assert report["state"] == "vector_cleanup_completed"
    assert report["already_absent"] is True


def test_manifest_store_writes_atomically_and_rejects_state_regression(tmp_path: Path):
    store = VectorCleanupManifestStore(tmp_path)
    manifest = build_vector_cleanup_manifest(
        document_id="42",
        law_number="12/2020/NĐ-CP",
        chunk_ids=[7],
        snapshot=_snapshot(),
        requested_by="user:admin",
        reason="Dọn vector sau khi đã chặn văn bản hết hiệu lực.",
    )

    stored = store.save(manifest)
    running = store.transition(stored["job_id"], "vector_cleanup_running")

    assert running["state"] == "vector_cleanup_running"
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError, match="invalid_vector_cleanup_transition"):
        store.transition(stored["job_id"], "blocking_applied")
