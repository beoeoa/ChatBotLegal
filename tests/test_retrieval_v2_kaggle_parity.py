from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from scripts.verify_retrieval_v2_kaggle_parity import (
    _cosine,
    _deterministic_sample,
    _load_persisted_sample,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_deterministic_sample_is_bounded_and_stable(tmp_path: Path) -> None:
    shard = tmp_path / "input-00000.jsonl"
    rows = []
    for index in range(20):
        text = f"passage {index}"
        rows.append(
            {
                "chunk_revision_id": f"chunk-{index:02d}",
                "embedding_text": text,
                "embedding_text_sha256": hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                "token_count": 3,
            }
        )
    shard.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    manifest = {
        "chunk_count": len(rows),
        "shards": [{"path": shard.name, "sha256": _sha(shard)}],
    }

    first = _deterministic_sample(manifest, tmp_path, 5)
    second = _deterministic_sample(manifest, tmp_path, 5)

    assert len(first) == 5
    assert [row["chunk_revision_id"] for row in first] == [
        row["chunk_revision_id"] for row in second
    ]


def test_load_persisted_sample_reads_only_requested_ids(tmp_path: Path) -> None:
    shard = tmp_path / "vectors-00000.npz"
    np.savez(
        shard,
        ids=np.asarray(["chunk-a", "chunk-b", "chunk-c"]),
        vectors=np.asarray(
            [[1.0] + [0.0] * 1023, [0.0, 1.0] + [0.0] * 1022, [0.0] * 1023 + [1.0]],
            dtype=np.float32,
        ),
    )
    output = {
        "shards": [{"path": shard.name, "sha256": _sha(shard)}],
    }

    observed = _load_persisted_sample(output, tmp_path, {"chunk-a", "chunk-c"})

    assert set(observed) == {"chunk-a", "chunk-c"}
    assert observed["chunk-a"].dtype == np.float32
    assert _cosine(observed["chunk-a"], observed["chunk-a"]) >= 0.999999
