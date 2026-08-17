"""Plan/evaluate a BGE-M3 shadow without touching the active vector pointer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


class ShadowIsolationError(RuntimeError):
    pass


def _safe_collection_component(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.casefold()).strip("-.")[:40]


def build_shadow_manifest(
    *,
    active_collection: str,
    model_id: str,
    model_fingerprint: str,
    dataset_sha256: str,
    model_path: str | Path | None,
    shadow_collection: str | None = None,
) -> dict[str, Any]:
    fingerprint = str(model_fingerprint).strip().casefold()
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("model_fingerprint_must_be_sha256")
    dataset_hash = str(dataset_sha256).strip().casefold()
    if not re.fullmatch(r"[a-f0-9]{64}", dataset_hash):
        raise ValueError("dataset_sha256_must_be_sha256")
    generated = (
        f"feature016-shadow-{_safe_collection_component(model_id)}-"
        f"{fingerprint[:12]}"
    )
    shadow = str(shadow_collection or generated).strip()
    active = str(active_collection).strip()
    if not active or not shadow:
        raise ShadowIsolationError("collection_name_required")
    if active == shadow:
        raise ShadowIsolationError("shadow_collection_must_differ_from_active")
    local_path = Path(model_path) if model_path else None
    available = bool(local_path and local_path.is_dir())
    return {
        "schema_version": "feature016-embedding-shadow-v1",
        "status": "planned" if available else "disabled",
        "reason_code": None if available else "model_path_missing",
        "model_id": str(model_id),
        "model_fingerprint": fingerprint,
        "dataset_sha256": dataset_hash,
        "shadow_collection": shadow,
        "active_collection_before": active,
        "active_collection_after": active,
        "activation_requested": False,
        "write_scope": "isolated_shadow_only",
        "real_corpus_reindex_performed": False,
        "active_pointer_mutated": False,
        "safety_regression_count": 0,
    }


def _metrics(
    expected: Mapping[str, set[str]],
    rankings: Mapping[str, Sequence[str]],
) -> dict[str, float]:
    recalls: list[float] = []
    reciprocals: list[float] = []
    top5: list[float] = []
    for query_id in sorted(expected):
        relevant = set(expected[query_id])
        ranked = list(rankings.get(query_id) or [])
        top10 = ranked[:10]
        recalls.append(len(relevant.intersection(top10)) / max(1, len(relevant)))
        rank = next((index for index, item in enumerate(ranked, 1) if item in relevant), None)
        reciprocals.append(1.0 / rank if rank else 0.0)
        top5.append(1.0 if relevant.intersection(ranked[:5]) else 0.0)
    denominator = max(1, len(recalls))
    return {
        "recall_at_10": round(sum(recalls) / denominator, 6),
        "mrr": round(sum(reciprocals) / denominator, 6),
        "top5_rate": round(sum(top5) / denominator, 6),
    }


def evaluate_rankings(
    expected: Mapping[str, set[str]],
    active_rankings: Mapping[str, Sequence[str]],
    shadow_rankings: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    active = _metrics(expected, active_rankings)
    shadow = _metrics(expected, shadow_rankings)
    safety_regressions = sum(
        bool(set(relevant).intersection((active_rankings.get(query_id) or [])[:10]))
        and not bool(set(relevant).intersection((shadow_rankings.get(query_id) or [])[:10]))
        for query_id, relevant in expected.items()
    )
    recall_ok = shadow["recall_at_10"] >= active["recall_at_10"]
    quality_gain = (
        shadow["mrr"] - active["mrr"] >= 0.05
        or shadow["top5_rate"] - active["top5_rate"] >= 0.01
    )
    return {
        "active": active,
        "shadow": shadow,
        "safety_regression_count": safety_regressions,
        "activation_eligible": bool(recall_ok and quality_gain and safety_regressions == 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--active-collection", required=True)
    parser.add_argument("--model-id", default="BAAI/bge-m3")
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--model-fingerprint", default="")
    args = parser.parse_args()
    golden_hash = hashlib.sha256(args.golden.read_bytes()).hexdigest()
    model_path = args.model_path or (
        Path(os.environ["BGE_M3_MODEL_PATH"])
        if os.getenv("BGE_M3_MODEL_PATH")
        else None
    )
    fingerprint = args.model_fingerprint or hashlib.sha256(
        f"{args.model_id}|{model_path or ''}".encode("utf-8")
    ).hexdigest()
    manifest = build_shadow_manifest(
        active_collection=args.active_collection,
        model_id=args.model_id,
        model_fingerprint=fingerprint,
        dataset_sha256=golden_hash,
        model_path=model_path,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: manifest[key] for key in ("status", "reason_code", "shadow_collection", "active_collection_after")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

