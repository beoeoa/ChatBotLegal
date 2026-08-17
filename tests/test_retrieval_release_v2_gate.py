import hashlib
import json

from scripts.check_retrieval_release_v2_gates import _reranker_manifest_artifact_pass


def _write_manifest(path, *, custom_code):
    payload = {
        "schema_version": "legal-retrieval-v2-reranker-manifest-v1",
        "release_scope": "retrieval-release-v2",
        "model_id": "test/model",
        "revision": "model-revision",
        "runtime_download_allowed": False,
        "benchmark_only": True,
        "files": {"model.safetensors": {"sha256": "a" * 64}},
        "custom_code": custom_code,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )


def test_gate_requires_gte_custom_code_and_detached_checksum(tmp_path):
    bge = tmp_path / "bge.json"
    gte = tmp_path / "gte.json"
    _write_manifest(bge, custom_code=None)
    _write_manifest(
        gte,
        custom_code={
            "revision": "code-revision",
            "files": {
                "configuration.py": {"sha256": "b" * 64},
                "modeling.py": {"sha256": "c" * 64},
            },
        },
    )
    assert _reranker_manifest_artifact_pass(bge, requires_custom_code=False)
    assert _reranker_manifest_artifact_pass(gte, requires_custom_code=True)
    gte.with_suffix(gte.suffix + ".sha256").write_text("0" * 64, encoding="ascii")
    assert not _reranker_manifest_artifact_pass(gte, requires_custom_code=True)
