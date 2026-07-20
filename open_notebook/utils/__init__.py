"""Public utility exports without importing optional heavy dependencies.

Importing a Python submodule executes its package ``__init__`` first.  The old
initializer eagerly imported ``chunking``, which imports
``langchain_text_splitters`` and ``transformers``.  Consequently even the API
authentication module's small encryption import delayed process startup by
tens of seconds.  PEP 562 lazy attributes preserve the existing public imports
while keeping lightweight paths (auth, health and readiness) lightweight.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Chunking
    "CHUNK_SIZE": (".chunking", "CHUNK_SIZE"),
    "ContentType": (".chunking", "ContentType"),
    "chunk_text": (".chunking", "chunk_text"),
    "detect_content_type": (".chunking", "detect_content_type"),
    "detect_content_type_from_extension": (
        ".chunking",
        "detect_content_type_from_extension",
    ),
    "detect_content_type_from_heuristics": (
        ".chunking",
        "detect_content_type_from_heuristics",
    ),
    # Embedding
    "generate_embedding": (".embedding", "generate_embedding"),
    "generate_embeddings": (".embedding", "generate_embeddings"),
    "mean_pool_embeddings": (".embedding", "mean_pool_embeddings"),
    # Encryption
    "decrypt_value": (".encryption", "decrypt_value"),
    "encrypt_value": (".encryption", "encrypt_value"),
    # Text
    "remove_non_ascii": (".text_utils", "remove_non_ascii"),
    "remove_non_printable": (".text_utils", "remove_non_printable"),
    "parse_thinking_content": (".text_utils", "parse_thinking_content"),
    "clean_thinking_content": (".text_utils", "clean_thinking_content"),
    # Token
    "token_count": (".token_utils", "token_count"),
    "token_cost": (".token_utils", "token_cost"),
    # Version
    "compare_versions": (".version_utils", "compare_versions"),
    "get_installed_version": (".version_utils", "get_installed_version"),
    "get_version_from_github": (".version_utils", "get_version_from_github"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve and cache a legacy public utility only when first requested."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
