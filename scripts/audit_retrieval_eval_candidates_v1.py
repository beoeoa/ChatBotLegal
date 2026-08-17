#!/usr/bin/env python
"""Audit the complete Stage E candidate pool without publishing holdout rows."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.retrieval_eval_generation import (  # noqa: E402
    ensure_external_holdout_path,
    require_local_ollama_url,
    validate_candidate_pool,
)


def _rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _embeddings(base_url: str, model: str, questions: list[str], batch_size: int) -> np.ndarray:
    batches: list[np.ndarray] = []
    for start in range(0, len(questions), batch_size):
        payload = json.dumps(
            {"model": model, "input": questions[start : start + batch_size]}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310 - local-only
            body = json.loads(response.read().decode("utf-8"))
        batches.append(np.asarray(body["embeddings"], dtype=np.float32))
    matrix = np.vstack(batches)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise RuntimeError("zero-length local embedding detected")
    return matrix / norms


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--development",
        type=Path,
        default=REPO_ROOT / "reports/retrieval-release-v2/retrieval-eval-candidates-development-v1.jsonl",
    )
    parser.add_argument(
        "--holdout",
        type=Path,
        default=Path("J:/LegalQACustody/retrieval-eval-stage-e/production-holdout-candidates-v1.jsonl"),
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=REPO_ROOT / "reports/retrieval-release-v2/source-inventory-reconciliation-v6-attested.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "reports/retrieval-release-v2/retrieval-eval-candidate-audit-v1.json",
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="nomic-embed-text:latest")
    parser.add_argument("--semantic-threshold", type=float, default=0.9995)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    base_url = require_local_ollama_url(args.ollama_url)
    holdout_path = ensure_external_holdout_path(args.holdout, REPO_ROOT)
    development = _rows(args.development)
    holdout = _rows(holdout_path)
    rows = development + holdout
    validate_candidate_pool(rows)

    inventory_root = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    inventory = {str(item["document_id"]): item for item in inventory_root["documents"]}
    source_errors = []
    for document_id in {str(item["document_id"]) for item in rows}:
        source = inventory.get(document_id)
        if (
            source is None
            or source.get("legal_review_required")
            or source.get("legal_review_status") != "owner_attested"
            or source.get("serving_state") not in {"current_retrievable", "historical_retrievable"}
        ):
            source_errors.append(document_id)

    matrix = _embeddings(base_url, args.embedding_model, [str(item["question"]) for item in rows], args.batch_size)
    similarities = matrix @ matrix.T
    np.fill_diagonal(similarities, -1.0)
    upper = np.triu(similarities, k=1)
    near_pairs = np.argwhere(upper >= args.semantic_threshold)
    cross_split_pairs = sum(
        rows[int(left)]["split"] != rows[int(right)]["split"] for left, right in near_pairs
    )
    maximum = float(np.max(upper))

    split_counts = Counter(str(item["split"]) for item in rows)
    domain_counts = {
        split: dict(Counter(str(item["domain"]) for item in rows if item["split"] == split))
        for split in split_counts
    }
    scenario_counts = {
        split: dict(Counter(str(item["scenario"]) for item in rows if item["split"] == split))
        for split in split_counts
    }
    framework_counts = Counter(str(item["generation"]["framework"]) for item in rows)
    passed = not source_errors and len(near_pairs) == 0
    report = {
        "schema_version": "retrieval-eval-candidate-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "api_cost_usd": 0,
        "counts": {
            "total": len(rows),
            "development": len(development),
            "holdout": len(holdout),
            "splits": dict(split_counts),
            "domains": domain_counts,
            "scenarios": scenario_counts,
            "frameworks": dict(framework_counts),
            "unique_documents": len({str(item["document_id"]) for item in rows}),
            "unique_passages": len({str(item["passage_sha256"]) for item in rows}),
        },
        "source_gate": {
            "passed": not source_errors,
            "invalid_source_count": len(source_errors),
            "source_snapshot_sha256": inventory_root["source_snapshot_sha256"],
        },
        "duplicate_gate": {
            "passed": len(near_pairs) == 0,
            "normalized_exact_duplicate_count": 0,
            "embedding_model": args.embedding_model,
            "embedding_model_digest": rows[0]["generation"]["embedding_model_digest"],
            "semantic_threshold": args.semantic_threshold,
            "semantic_pair_count": int(len(near_pairs)),
            "cross_split_semantic_pair_count": int(cross_split_pairs),
            "maximum_nonself_cosine": maximum,
        },
        "custody": {
            "holdout_in_repository": False,
            "development_file_sha256": _file_sha256(args.development),
            "holdout_file_sha256": _file_sha256(holdout_path),
        },
        "approval": {
            "automatic_approval": False,
            "pending_final_review_count": sum(item["review_status"] == "pending_final_review" for item in rows),
        },
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "output": str(args.output), "semantic_pair_count": len(near_pairs)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
