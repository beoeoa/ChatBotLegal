"""Run read-only semantic search against exported VNLegal-LAL vector shards."""

from __future__ import annotations

import argparse
import heapq
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

QUERY_PREFIX = (
    "Instruct: Given a Vietnamese legal question, retrieve relevant legal "
    "passages that answer the question\nQuery: "
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Vietnamese legal question")
    parser.add_argument(
        "--vectors",
        type=Path,
        default=Path(
            r"J:\legal-chatbot-data\vnlegal_lal_vectors\results"
            r"\vnlegal_lal_chroma_vectors"
        ),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(
            r"J:\legal-chatbot-data\sentence_transformers"
            r"\models--darklethelong--vnlegal-lal\snapshots"
            r"\de759324ef931a2475ae8db97137b6a6cbb98aa0"
        ),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="Exclude documents effective after this date (YYYY-MM-DD)",
    )
    parser.add_argument("--scope", help="Optional case-insensitive scope filter")
    return parser.parse_args()


def encode_query(query: str, model_path: Path) -> np.ndarray:
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModel.from_pretrained(
        model_path,
        local_files_only=True,
        dtype=torch.float32,
    )
    model.eval()

    inputs = tokenizer(
        [QUERY_PREFIX + query],
        padding=True,
        truncation=True,
        max_length=2048,
        return_tensors="pt",
    )
    with torch.inference_mode():
        output = model(**inputs)
        last_token = inputs["attention_mask"].sum(dim=1) - 1
        embedding = output.last_hidden_state[
            torch.arange(len(inputs["input_ids"])), last_token
        ]
        embedding = F.normalize(embedding, p=2, dim=1)

    return embedding[0].cpu().numpy().astype(np.float32)


def find_candidates(
    vector_root: Path,
    query_vector: np.ndarray,
    candidate_count: int,
) -> list[tuple[float, str, int]]:
    heap: list[tuple[float, str, int]] = []

    for embedding_path in sorted(vector_root.glob("embeddings_*.npy")):
        shard = embedding_path.stem.rsplit("_", 1)[-1]
        embeddings = np.load(embedding_path, mmap_mode="r")
        scores = embeddings @ query_vector
        local_count = min(candidate_count, len(scores))
        local_rows = np.argpartition(scores, -local_count)[-local_count:]

        for row in local_rows:
            item = (float(scores[row]), shard, int(row))
            if len(heap) < candidate_count:
                heapq.heappush(heap, item)
            elif item[0] > heap[0][0]:
                heapq.heapreplace(heap, item)

    return sorted(heap, reverse=True)


def load_results(
    vector_root: Path,
    candidates: list[tuple[float, str, int]],
    as_of: date,
    scope: str | None,
    top_k: int,
) -> list[dict]:
    by_shard: dict[str, list[tuple[float, int]]] = {}
    for score, shard, row in candidates:
        by_shard.setdefault(shard, []).append((score, row))

    results: list[dict] = []
    for shard, shard_candidates in by_shard.items():
        metadata = pd.read_parquet(vector_root / f"metadata_{shard}.parquet")
        for score, row in shard_candidates:
            record = metadata.iloc[row]
            effective_date = pd.to_datetime(
                record.get("effective_date"), errors="coerce"
            )
            if pd.notna(effective_date) and effective_date.date() > as_of:
                continue

            record_scope = str(record.get("scope") or "")
            if scope and scope.casefold() not in record_scope.casefold():
                continue

            results.append(
                {
                    "score": round(score, 4),
                    "chunk_id": int(record["chunk_id"]),
                    "document_title": record.get("document_title"),
                    "law_number": record.get("law_number"),
                    "article_title": record.get("article_title"),
                    "scope": record.get("scope"),
                    "effective_date": record.get("effective_date"),
                    "source_url": record.get("source_url"),
                }
            )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top_k]


def main() -> None:
    args = parse_args()
    query_vector = encode_query(args.query, args.model)
    candidates = find_candidates(
        args.vectors,
        query_vector,
        candidate_count=max(args.top_k * 10, 100),
    )
    results = load_results(
        args.vectors,
        candidates,
        as_of=args.as_of,
        scope=args.scope,
        top_k=args.top_k,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
