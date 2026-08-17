"""Lazy command exports for Open Notebook.

Importing a single command module must not initialize every optional parser,
podcast or reranker dependency. Each API route imports the command module it
actually uses; these lazy exports preserve the old ``from commands import ...``
interface without the previous global side effects.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "embed_note_command": "commands.embedding_commands",
    "embed_insight_command": "commands.embedding_commands",
    "embed_source_command": "commands.embedding_commands",
    "rebuild_embeddings_command": "commands.embedding_commands",
    "process_source_command": "commands.source_commands",
    "process_text_command": "commands.example_commands",
    "analyze_data_command": "commands.example_commands",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if not module_name:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
