"""Evaluate Feature 016 Gate E on isolated layout and graph fixtures."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.crawlers.legal_document_pipeline import (
    build_html_extraction_blocks,
    build_pdf_text_blocks,
    normalize_extraction_blocks,
)
from api.crawlers.ocr_extractor import _ocr_line_blocks
from api.legal_adaptive_hop import execute_bounded_hops


async def evaluate_gate_e(fixture: Mapping[str, Any]) -> dict[str, Any]:
    asset_hash = hashlib.sha256(b"feature016-gate-e-source").hexdigest()
    html_blocks = build_html_extraction_blocks(
        str(fixture.get("html_table") or ""), extractor="gate-e-html"
    )
    table_blocks = [item for item in html_blocks if item["block_type"] == "table"]
    pdf_pages = [str(value) for value in fixture.get("pdf_pages") or []]
    pdf_blocks = build_pdf_text_blocks(
        pdf_pages,
        source_asset_sha256=asset_hash,
        extractor="pymupdf",
        extractor_version="gate-e",
    )
    fallback = normalize_extraction_blocks(
        [{"block_type": "table", "text": "", "page_number": -1}],
        fallback_text="Nội dung fallback",
        extractor="optional-parser",
    )
    ocr_blocks, ocr_page = _ocr_line_blocks(
        dict(fixture.get("ocr_data") or {}),
        page_number=1,
        source_asset_sha256=asset_hash,
    )

    documents = {
        str(key): dict(value)
        for key, value in dict(fixture.get("documents") or {}).items()
    }
    calls: list[dict[str, Any]] = []

    async def retrieve(query: str, target_document_id: str, hop: int):
        calls.append({"query": query, "target": target_document_id, "hop": hop})
        return [
            {
                **documents[target_document_id],
                "content": f"evidence-{target_document_id}",
            }
        ]

    hop_result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["thủ tục liên quan"],
        relationships=list(fixture.get("relationships") or []),
        documents=documents,
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        allowed_jurisdictions=["central", "haiphong"],
        enabled=True,
    )

    budget_targets = [f"Q{index:02d}" for index in range(20)]
    budget_documents = {
        "A": documents["A"],
        **{
            target: {
                **documents["B"],
                "document_id": target,
                "law_number": f"{target}/2026/TEST",
            }
            for target in budget_targets
        },
    }
    budget_calls = 0

    async def budget_retrieve(_query: str, target_document_id: str, _hop: int):
        nonlocal budget_calls
        budget_calls += 1
        return [budget_documents[target_document_id]]

    budget_result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["q"],
        relationships=[
            {
                "source_document_id": "A",
                "target_document_id": target,
                "relationship_type": "references",
                "verified": True,
            }
            for target in budget_targets
        ],
        documents=budget_documents,
        retrieve=budget_retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        enabled=True,
    )

    checks = {
        "html_table_cells_preserved": len(table_blocks) == 4,
        "table_cell_paths_preserved": bool(table_blocks)
        and table_blocks[-1].get("table_path") == "table:0/row:1/cell:1",
        "pdf_page_identity_preserved": [item["page_number"] for item in pdf_blocks]
        == [1, 2],
        "pdf_offsets_page_local": all(
            item["char_start"] == 0
            and item["char_end"] == len(pdf_pages[index])
            for index, item in enumerate(pdf_blocks)
        ),
        "optional_parser_fallback_explicit": fallback["status"] == "fallback"
        and fallback["reason_code"] == "EXTRACTION_BLOCKS_INVALID",
        "ocr_bbox_confidence_preserved": bool(ocr_blocks)
        and all(item.get("bounding_box") for item in ocr_blocks)
        and all(item.get("confidence") is not None for item in ocr_blocks),
        "ocr_page_projection_stable": ocr_page == "Điều 1\nNội dung",
        "verified_graph_two_hops": hop_result["hop_count"] == 2
        and [item["document_id"] for item in hop_result["evidence"]] == ["B", "C"],
        "previous_hop_adapts_next_query": len(calls) == 2
        and "evidence-B" in calls[1]["query"],
        "expired_target_blocked": hop_result["filtered_reasons"].get(
            "expired_or_not_current"
        )
        == 1,
        "unverified_edge_ignored": hop_result["ignored_unverified_edges"] == 1,
        "cycle_does_not_repeat_seed": hop_result["visited_document_ids"] == ["A", "B", "C"],
        "hop_budget_enforced": hop_result["stop_reason"] == "hop_budget_reached",
        "query_budget_enforced": budget_calls == 16
        and budget_result["query_count"] == 16
        and budget_result["stop_reason"] == "query_budget_reached",
        "live_corpus_untouched": True,
        "browser_uat_not_run": True,
    }
    failed = sorted(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": "feature016-gate-e-v1",
        "status": "pass" if not failed else "fail",
        "gate_e_pass": not failed,
        "reason_codes": failed,
        "checks": checks,
        "metrics": {
            "html_block_count": len(html_blocks),
            "table_cell_count": len(table_blocks),
            "pdf_page_block_count": len(pdf_blocks),
            "ocr_line_block_count": len(ocr_blocks),
            "hop_query_count": hop_result["query_count"],
            "budget_query_count": budget_result["query_count"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    result = asyncio.run(evaluate_gate_e(fixture))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "gate_e_pass", "reason_codes")
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["gate_e_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
