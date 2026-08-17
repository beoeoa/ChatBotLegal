from pathlib import Path
import json

import pytest

from scripts.build_retrieval_release_v2_lexical_index import build


def _manifest(tmp_path: Path, *, approved: bool) -> Path:
    content = "Nội dung Điều 1"
    payload = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "release_id": "release-v2",
        "source_snapshot_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "approved": approved,
        "legal_review_attestation": approved,
        "approval_blocker": None if approved else "official_source_and_metadata_review_required",
        "chunks": [{
            "chunk_revision_id": "r-1",
            "document_id": 1,
            "article_id": 1,
            "chunk_index": 0,
            "release_id": "release-v2",
            "document_serving_state": "current_retrievable",
            "eligible": True,
            "serving_state": "retrievable",
            "content": content,
            "structural_path": "Điều 1",
            "passage_sha256": "c" * 64,
            "content_sha256": "d" * 64,
            "token_count": 10,
            "metadata": {
                "law_number": "01/2026/NĐ-CP",
                "article_number": "1",
                "source_url": "https://vbpl.vn/example",
            },
        }],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_lexical_builder_rejects_provisional_without_explicit_flag(tmp_path: Path):
    with pytest.raises(RuntimeError, match="approved_v2_manifest_required"):
        build(
            manifest_path=_manifest(tmp_path, approved=False),
            output=tmp_path / "index-provisional.sqlite3",
        )


def test_lexical_builder_marks_provisional_output_not_release_eligible(tmp_path: Path):
    report = build(
        manifest_path=_manifest(tmp_path, approved=False),
        output=tmp_path / "index-provisional.sqlite3",
        allow_provisional_staging=True,
    )
    assert report["status"] == "PROVISIONAL_BUILT"
    assert report["release_eligible"] is False
    metadata = report["counts"]
    assert metadata["chunk_count"] == 1
