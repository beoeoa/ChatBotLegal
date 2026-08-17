import json
import hashlib

from scripts.prepare_retrieval_release_v2_reranker_manifests import _manifest


def test_release_reranker_manifest_is_checksum_bound_and_local_only(tmp_path):
    model = tmp_path / "model"
    code = tmp_path / "code"
    model.mkdir()
    code.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")
    (code / "configuration.py").write_text("config", encoding="utf-8")
    (code / "modeling.py").write_text("model", encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "model_id": "test/gte",
                "revision": "model-revision",
                "license": "apache-2.0",
                "files": {
                    "config.json": hashlib.sha256(b"{}").hexdigest(),
                    "model.safetensors": hashlib.sha256(b"weights").hexdigest(),
                },
                "custom_code": {
                    "repository": "test/code",
                    "revision": "code-revision",
                    "files": {
                        "configuration.py": hashlib.sha256(b"config").hexdigest(),
                        "modeling.py": hashlib.sha256(b"model").hexdigest(),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "release-manifest.json"
    manifest = _manifest(
        source_manifest_path=source,
        model_path=model,
        custom_code_path=code,
        output_path=output,
    )
    assert manifest["schema_version"] == "legal-retrieval-v2-reranker-manifest-v1"
    assert manifest["runtime_download_allowed"] is False
    assert manifest["benchmark_only"] is True
    assert manifest["files"]["model.safetensors"]["size_bytes"] == len(b"weights")
    assert manifest["custom_code"]["files"]["modeling.py"]["size_bytes"] == len("model")
