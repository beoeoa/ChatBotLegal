"""Evaluate VNLegal-LAL retrieval for the role test questions without an LLM."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from evaluate_role_answers import QUESTIONS


SEARCH_URL = "http://127.0.0.1:8765/search"
OUTPUT_PATH = Path("notebook_data/retrieval-evaluation.json")


def main() -> None:
    evaluations = []
    with httpx.Client(timeout=240) as client:
        for index, item in enumerate(QUESTIONS, start=1):
            print(f"[{index}/10] {item['id']}: {item['topic']}", flush=True)
            response = client.post(
                SEARCH_URL,
                json={
                    "query": item["question"],
                    "limit": 10,
                    "candidate_count": 300,
                },
            )
            response.raise_for_status()
            payload = response.json()
            sources = []
            for result in payload.get("results", []):
                sources.append(
                    {
                        "chunk_id": result.get("chunk_id"),
                        "law_number": result.get("law_number"),
                        "document_title": result.get("document_title"),
                        "document_status": result.get("document_status"),
                        "scope": result.get("scope"),
                        "article_number": result.get("article_number"),
                        "article_title": result.get("article_title"),
                        "score": result.get("score"),
                        "content": result.get("content"),
                    }
                )
            evaluations.append(
                {
                    **item,
                    "timing_ms": payload.get("timing_ms"),
                    "source_count": len(sources),
                    "sources": sources,
                }
            )
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "results": evaluations,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(str(OUTPUT_PATH.resolve()), flush=True)


if __name__ == "__main__":
    main()
