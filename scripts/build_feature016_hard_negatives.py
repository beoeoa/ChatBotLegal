"""Build deterministic, reviewer-authorized hard negatives from Golden v2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: str(source.get(key) or "").strip()
        for key in ("law_number", "article", "clause", "point")
        if str(source.get(key) or "").strip()
    }


def build_hard_negatives(golden: Mapping[str, Any]) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    approved_count = 0
    positive_count = 0
    negative_count = 0
    for case in golden.get("cases") or []:
        if not isinstance(case, Mapping) or case.get("review_status") != "approved":
            continue
        approved_count += 1
        positives = [
            normalized
            for item in (case.get("expected_sources") or [])
            if isinstance(item, Mapping) and (normalized := _source(item))
        ]
        negatives = [
            normalized
            for item in (case.get("forbidden_sources") or [])
            if isinstance(item, Mapping) and (normalized := _source(item))
        ]
        if not positives or not negatives:
            continue
        questions = case.get("questions") or {}
        question = str(
            questions.get("citizen")
            or questions.get("officer")
            or questions.get("admin")
            or ""
        ).strip()
        examples.append(
            {
                "case_id": str(case.get("case_id") or ""),
                "domain": str(case.get("domain") or ""),
                "legal_as_of": str(case.get("legal_as_of") or ""),
                "question": question,
                "positive_sources": positives,
                "hard_negatives": negatives,
                "negative_origin": "explicit_forbidden_source",
            }
        )
        positive_count += len(positives)
        negative_count += len(negatives)
    examples.sort(key=lambda item: item["case_id"])
    return {
        "schema_version": "feature016-hard-negatives-v1",
        "policy": "approved_explicit_sources_only",
        "summary": {
            "approved_case_count": approved_count,
            "example_count": len(examples),
            "positive_source_count": positive_count,
            "hard_negative_source_count": negative_count,
        },
        "examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.golden.read_bytes()
    golden = json.loads(raw.decode("utf-8"))
    result = build_hard_negatives(golden)
    result["source"] = {
        "path": str(args.golden),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

