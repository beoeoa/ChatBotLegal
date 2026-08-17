from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.model_modality import (
    validate_embedding_output,
    validate_provider_model_modality,
)
from api.routers.models import validate_default_model_modalities
from open_notebook.ai.hf_embedding import resolve_hf_model_source
from scripts.legal_search_server import (
    _model_revision_fingerprint,
    _validate_model_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_retrieval_image_has_explicit_cpu_and_cuda_targets_and_pinned_manifests():
    dockerfile = _read("Dockerfile.retrieval")
    compose = _read("docker-compose.release.yml")
    cuda_override = _read("docker-compose.release.cuda.yml")

    assert "AS retrieval-cpu" in dockerfile
    assert "AS retrieval-cuda" in dockerfile
    assert "requirements/legal-retrieval-docker.txt" in dockerfile
    assert "requirements/legal-retrieval-cu126.txt" in dockerfile
    assert "target: retrieval-cpu" in compose
    assert "target: retrieval-cuda" in cuda_override
    assert "capabilities: [gpu]" in cuda_override
    assert "LEGAL_EMBED_DEVICE: cuda" in cuda_override


def test_release_model_path_and_fingerprint_are_environment_driven():
    compose = _read("docker-compose.release.yml")
    hf_source = _read("open_notebook/ai/hf_embedding.py")

    assert "${VNLEGAL_LAL_MODEL_PATH:-/data/legal/vnlegal-lal-model}" in compose
    assert "VNLEGAL_LAL_MODEL_FINGERPRINT" in compose
    assert r"D:\legal-chatbot-data" not in hf_source
    assert "HUGGINGFACE_EMBEDDING_MODEL_PATH" in hf_source


def test_model_fingerprint_is_path_independent_and_attestation_fails_closed(
    monkeypatch, tmp_path
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()
        (directory / "config.json").write_text('{"model_type":"bert"}', encoding="utf-8")
        (directory / "model.safetensors").write_bytes(b"fixture-weights")
    fingerprint = _model_revision_fingerprint(first)
    assert fingerprint == _model_revision_fingerprint(second)

    (second / "config.json").write_text('{"model_type":"other"}', encoding="utf-8")
    assert fingerprint != _model_revision_fingerprint(second)

    monkeypatch.setenv("VNLEGAL_LAL_MODEL_FINGERPRINT", "0" * 64)
    with pytest.raises(RuntimeError, match="vnlegal_lal_fingerprint_mismatch"):
        _validate_model_fingerprint(fingerprint)
    monkeypatch.setenv("VNLEGAL_LAL_MODEL_FINGERPRINT", fingerprint)
    assert _validate_model_fingerprint(fingerprint) == fingerprint


def test_hf_model_source_requires_configured_local_directory(monkeypatch, tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    monkeypatch.setenv("HUGGINGFACE_EMBEDDING_MODEL_PATH", str(model_dir))
    resolved, local_only = resolve_hf_model_source("darklethelong/vnlegal-lal")
    assert resolved == model_dir.resolve()
    assert local_only is True

    monkeypatch.setenv(
        "HUGGINGFACE_EMBEDDING_MODEL_PATH", str(tmp_path / "missing")
    )
    with pytest.raises(RuntimeError, match="embedding_model_path_missing"):
        resolve_hf_model_source("darklethelong/vnlegal-lal")


def test_embedding_output_modality_requires_finite_consistent_vectors():
    assert validate_embedding_output([[1, 2], [3.5, 4]], expected_count=2) == [
        [1.0, 2.0],
        [3.5, 4.0],
    ]
    with pytest.raises(ValueError, match="embedding_output_count_mismatch"):
        validate_embedding_output([[1.0]], expected_count=2)
    with pytest.raises(ValueError, match="embedding_output_dimension_mismatch"):
        validate_embedding_output([[1.0], [1.0, 2.0]], expected_count=2)
    with pytest.raises(ValueError, match="embedding_output_non_finite"):
        validate_embedding_output([[float("nan")]], expected_count=1)


def test_chat_model_cannot_be_registered_or_assigned_as_embedding():
    with pytest.raises(ValueError, match="model_modality_mismatch"):
        validate_provider_model_modality(
            provider="openrouter",
            model_name="nvidia/nemotron-3-nano-30b-a3b:free",
            requested_type="embedding",
        )
    assert (
        validate_provider_model_modality(
            provider="openrouter",
            model_name="nvidia/nemotron-3-nano-30b-a3b:free",
            requested_type="language",
        )
        == "language"
    )
    assert (
        validate_provider_model_modality(
            provider="openai",
            model_name="text-embedding-3-small",
            requested_type="embedding",
        )
        == "embedding"
    )


@pytest.mark.asyncio
async def test_default_slot_rejects_record_with_wrong_output_modality(monkeypatch):
    async def fake_get(model_id):
        return SimpleNamespace(id=model_id, type="language", name="chat-only")

    monkeypatch.setattr("api.routers.models.Model.get", fake_get)
    payload = SimpleNamespace(
        default_chat_model=None,
        large_context_model=None,
        default_text_to_speech_model=None,
        default_speech_to_text_model=None,
        default_embedding_model="model:chat-only",
        default_tools_model=None,
    )
    with pytest.raises(ValueError, match="default_model_modality_mismatch"):
        await validate_default_model_modalities(payload)


def test_frontend_preserves_discovered_modality_instead_of_global_override():
    source = _read("frontend/src/app/(dashboard)/settings/api-keys/page.tsx")
    setup_source = _read("frontend/src/lib/utils/model-setup.ts")
    registration = source[source.index("const handleRegister"):source.index("const totalSelected")]

    assert "model_type: m.model_type" in registration
    assert "model_type: selectedType" in registration  # custom model only
    assert "PROVIDER_MODALITIES" in source
    assert "openrouter: ['language']" in setup_source
    assert "model.model_type" in source
