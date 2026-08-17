"""Canonical helpers shared by legal generation, retrieval and QA logs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def semantic_claim_key(value: Any) -> str:
    """Normalize presentation-only differences without erasing legal numbers."""

    text = unicodedata.normalize("NFD", str(value or "").casefold()).replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    tokens = re.findall(r"[a-z0-9]+", text)
    normalized: list[str] = []
    for token in tokens:
        if token == "la":
            continue
        if token.isdigit():
            token = str(int(token))
        normalized.append(token)
    return " ".join(normalized)


def _snapshot_projection(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        return {}
    documents = snapshot.get("documents") if isinstance(snapshot.get("documents"), Mapping) else {}
    return {
        "schema_version": snapshot.get("schema_version"),
        "last_success_at": snapshot.get("last_success_at"),
        "coverage": snapshot.get("coverage") if isinstance(snapshot.get("coverage"), Mapping) else {},
        "documents": {
            str(key): {
                "fingerprint": value.get("fingerprint"),
                "normalized_status": value.get("normalized_status"),
                "affected_provisions": value.get("affected_provisions") or [],
            }
            for key, value in documents.items()
            if isinstance(value, Mapping)
        },
    }


def build_runtime_version_trace(
    *,
    app_version: str,
    index_collection: str,
    embedding_fingerprint: str,
    validity_snapshot: Mapping[str, Any] | None,
    reranker_version: str,
) -> dict[str, str | None]:
    projection = _snapshot_projection(validity_snapshot)
    canonical = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "app_version": str(app_version or "unknown"),
        "index_collection": str(index_collection or "unknown"),
        "embedding_fingerprint": str(embedding_fingerprint or "unknown"),
        "validity_snapshot_sha256": (
            hashlib.sha256(canonical).hexdigest() if projection else None
        ),
        "reranker_version": str(reranker_version or "disabled"),
    }


def build_data_release_id(versions: Mapping[str, Any] | None) -> str:
    """Return an opaque deterministic ID for the serving-data combination."""

    allowed = (
        "index_collection",
        "embedding_fingerprint",
        "validity_snapshot_sha256",
        "reranker_version",
    )
    payload = {
        key: (versions or {}).get(key)
        for key in allowed
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "data-release-" + hashlib.sha256(canonical).hexdigest()[:24]
