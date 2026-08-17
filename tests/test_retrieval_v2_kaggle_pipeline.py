from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from scripts.export_retrieval_v2_kaggle_shards import export_kaggle_input
from scripts.import_retrieval_v2_kaggle_shadow import (
    build_collection_metadata,
    build_vector_metadata,
    validate_target_names,
)
from scripts.kaggle_retrieval_v2_worker import discover_model, model_revision_fingerprint
from scripts.run_retrieval_v2_kaggle import build_dataset_metadata, build_kernel_metadata
from scripts.verify_retrieval_v2_kaggle_output import verify_kaggle_output


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest() -> dict:
    rows = []
    for index, state in enumerate(("current_retrievable", "historical_only"), start=1):
        text = f"passage {index}"
        rows.append(
            {
                "chunk_revision_id": f"release-1-{index}",
                "release_id": "release-1",
                "document_id": index,
                "article_id": index + 10,
                "embedding_text": text,
                "embedding_text_sha256": _sha(text),
                "token_count": 8,
                "eligible": True,
                "serving_state": "retrievable",
                "document_serving_state": state,
            }
        )
    return {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "release_id": "release-1",
        "dataset_version": "dataset-1",
        "approved": False,
        "legal_review_attestation": False,
        "approval_blocker": "legal_review_required",
        "source_snapshot_sha256": "a" * 64,
        "model_artifact_fingerprint": "b" * 64,
        "tokenizer_fingerprint": "c" * 64,
        "embedding_recipe_fingerprint": "d" * 64,
        "passage_recipe_fingerprint": "e" * 64,
        "splitter_fingerprint": "f" * 64,
        "dependency_lock_fingerprint": "1" * 64,
        "quality_policy_version": "legal-chunk-quality-v2",
        "manifest_sha256": "2" * 64,
        "chunks": rows,
    }


def test_export_builds_checksum_bound_shards(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    output = tmp_path / "input"

    report = export_kaggle_input(
        manifest_path=manifest_path,
        output_dir=output,
        shard_size=1,
        allow_provisional_staging=True,
    )

    assert report["chunk_count"] == 2
    assert report["document_state_counts"] == {
        "current_retrievable": 1,
        "historical_only": 1,
    }
    assert report["provisional_staging"] is True
    assert report["release_eligible"] is False
    assert len(report["shards"]) == 2
    for shard in report["shards"]:
        path = output / shard["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == shard["sha256"]


def test_approved_export_uses_v2_logical_job_provenance(tmp_path: Path) -> None:
    payload = _manifest()
    payload["approved"] = True
    payload["legal_review_attestation"] = True
    manifest_path = tmp_path / "approved.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    report = export_kaggle_input(
        manifest_path=manifest_path,
        output_dir=tmp_path / "input-v2",
        shard_size=2,
        embedding_job_id="retrieval-v2-test-job",
    )

    assert report["schema_version"] == "legal-retrieval-kaggle-input-v2"
    assert report["embedding_job_id"] == "retrieval-v2-test-job"
    assert len(report["worker_fingerprint"]) == 64
    assert report["internet_enabled"] is False


def test_approved_export_requires_explicit_logical_job_id(tmp_path: Path) -> None:
    payload = _manifest()
    payload["approved"] = True
    payload["legal_review_attestation"] = True
    manifest_path = tmp_path / "approved.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="approved_embedding_job_id_required"):
        export_kaggle_input(
            manifest_path=manifest_path,
            output_dir=tmp_path / "input-v2",
            shard_size=2,
        )


def test_export_rejects_invalid_embedding_text_checksum(tmp_path: Path) -> None:
    payload = _manifest()
    payload["chunks"][0]["embedding_text_sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="embedding_text_checksum_mismatch"):
        export_kaggle_input(
            manifest_path=path,
            output_dir=tmp_path / "out",
            shard_size=10,
            allow_provisional_staging=True,
        )


def test_draft_manifest_requires_explicit_provisional_flag(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    with pytest.raises(RuntimeError, match="approved_v2_manifest_required"):
        export_kaggle_input(
            manifest_path=path,
            output_dir=tmp_path / "out",
            shard_size=10,
            allow_provisional_staging=False,
        )


def test_output_verifier_checks_ids_dimension_norm_and_fingerprints(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    input_dir = tmp_path / "input"
    input_manifest = export_kaggle_input(
        manifest_path=manifest_path,
        output_dir=input_dir,
        shard_size=2,
        allow_provisional_staging=True,
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    vectors_path = output_dir / "vectors-00000.npz"
    np.savez(
        vectors_path,
        ids=np.asarray(["release-1-1", "release-1-2"]),
        vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
    )
    output_manifest = {
        "schema_version": "legal-retrieval-kaggle-output-v1",
        "release_id": "release-1",
        "source_snapshot_sha256": "a" * 64,
        "model_artifact_fingerprint": "b" * 64,
        "tokenizer_fingerprint": "c" * 64,
        "embedding_recipe_fingerprint": "d" * 64,
        "passage_recipe_fingerprint": "e" * 64,
        "splitter_fingerprint": "f" * 64,
        "dependency_lock_fingerprint": "1" * 64,
        "embedding_dimension": 2,
        "vector_count": 2,
        "shards": [
            {
                "path": vectors_path.name,
                "count": 2,
                "sha256": hashlib.sha256(vectors_path.read_bytes()).hexdigest(),
            }
        ],
    }
    (output_dir / "embedding-output-manifest.json").write_text(
        json.dumps(output_manifest), encoding="utf-8"
    )

    report = verify_kaggle_output(
        input_manifest_path=input_dir / "embedding-input-manifest.json",
        output_manifest_path=output_dir / "embedding-output-manifest.json",
        expected_dimension=2,
    )

    assert report["valid"] is True
    assert report["missing_ids"] == []
    assert report["extra_ids"] == []
    assert report["zero_vectors"] == 0
    assert input_manifest["chunk_count"] == report["vector_count"]


def test_kernel_metadata_is_private_and_binds_datasets() -> None:
    metadata = build_kernel_metadata(
        kernel_id="owner/legal-v2-embedding",
        title="Legal V2 embedding",
        input_dataset="owner/legal-v2-input",
        model_dataset="owner/vnlegal-lal-pinned",
    )

    assert metadata["id"] == "owner/legal-v2-embedding"
    assert metadata["is_private"] is True
    assert metadata["enable_gpu"] is True
    assert metadata["dataset_sources"] == [
        "owner/legal-v2-input",
        "owner/vnlegal-lal-pinned",
    ]


def test_dataset_metadata_is_private_and_has_no_credentials() -> None:
    metadata = build_dataset_metadata(
        dataset_id="owner/legal-v2-input",
        title="Legal V2 input",
    )
    assert metadata["id"] == "owner/legal-v2-input"
    assert metadata["isPrivate"] is True
    assert metadata["licenses"] == [{"name": "other"}]
    assert "token" not in json.dumps(metadata).casefold()


def test_worker_discovers_safely_zipped_model_dataset(tmp_path: Path) -> None:
    source = tmp_path / "source-model"
    source.mkdir()
    (source / "config.json").write_text('{"model_type":"bert"}', encoding="utf-8")
    (source / "model.safetensors").write_bytes(b"pinned-weights")
    expected = model_revision_fingerprint(source)
    input_root = tmp_path / "input"
    input_root.mkdir()
    with zipfile.ZipFile(input_root / "model.zip", "w") as archive:
        archive.write(source / "config.json", "model/config.json")
        archive.write(source / "model.safetensors", "model/model.safetensors")

    observed = discover_model(
        input_root,
        expected,
        extraction_root=tmp_path / "model-cache",
    )

    assert observed.name == "model"
    assert model_revision_fingerprint(observed) == expected


def test_kaggle_import_targets_remain_isolated_staging() -> None:
    validate_target_names(
        current="legal_chunks_retrieval_v2_current_kaggle_provisional",
        temporal="legal_chunks_retrieval_v2_temporal_kaggle_provisional",
        active="legal_chunks_vnlegal_lal_haiphong_unified_v1",
        provisional=True,
    )
    with pytest.raises(RuntimeError, match="kaggle_staging_target_required"):
        validate_target_names(
            current="legal_chunks_retrieval_v2_current",
            temporal="legal_chunks_retrieval_v2_temporal",
            active="legal_chunks_vnlegal_lal_haiphong_unified_v1",
            provisional=True,
        )


def test_kaggle_import_metadata_binds_release_and_temporal_state() -> None:
    source = {
        **_manifest(),
        "provisional_staging": True,
        "release_eligible": False,
        "embedding_max_length": 512,
    }
    collection = build_collection_metadata(source, document_state="all")
    assert collection["hnsw:space"] == "cosine"
    assert collection["document_state"] == "all"
    assert collection["provisional_staging"] == "true"
    row = {
        **source["chunks"][1],
        "metadata": {
            "law_number": "01/2020/QD",
            "source_url": "https://vbpl.vn/example",
            "article_effective_from": "2020-01-01",
            "article_effective_to": "2021-01-01",
        },
    }
    metadata = build_vector_metadata(row, source)
    assert metadata["release_id"] == "release-1"
    assert metadata["document_serving_state"] == "historical_only"
    assert metadata["effective_from"] == "2020-01-01"
    assert metadata["effective_to"] == "2021-01-01"
