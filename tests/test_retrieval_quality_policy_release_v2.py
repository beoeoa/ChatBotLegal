from __future__ import annotations

import json
from pathlib import Path

from scripts.build_retrieval_quality_policy_release_report_v2 import build


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _inventory_documents() -> list[dict]:
    return [
        {"document_id": 1, "serving_state": "current_retrievable"},
        {"document_id": 2, "serving_state": "historical_only"},
        *(
            {"document_id": value, "serving_state": "quarantined"}
            for value in range(3, 12_237)
        ),
    ]


def test_release_quality_report_separates_quality_from_legal_approval(tmp_path: Path):
    inventory = tmp_path / "inventory.json"
    build_report = tmp_path / "build.json"
    verification = tmp_path / "verification.json"
    output = tmp_path / "quality.json"
    documents = _inventory_documents()
    _write(inventory, {
        "source_snapshot_document_count": 12_236,
        "source_snapshot_sha256": "a" * 64,
        "documents": documents,
        "inventory_counts": {"state_sum": 12_236},
    })
    _write(build_report, {
        "release_id": "r1",
        "counts": {
            "chunk_count": 2,
            "vector_count": 2,
            "included_document_count": 2,
            "current_retrievable_document_count": 1,
            "historical_only_document_count": 1,
        },
        "stats": {},
        "database_mutated": False,
        "active_pointer_changed": False,
    })
    _write(verification, {"structural_gate_passed": True, "chunk_count": 2, "invalid_chunk_count": 0})
    report = build(
        inventory_path=inventory,
        build_report_path=build_report,
        verification_path=verification,
        output=output,
    )
    assert report["quality_gate_passed"] is True
    assert report["legal_release_approved"] is False
    assert report["release_gate_passed"] is False


def test_release_quality_report_fails_on_oversized_chunks(tmp_path: Path):
    inventory = tmp_path / "inventory.json"
    build_report = tmp_path / "build.json"
    verification = tmp_path / "verification.json"
    output = tmp_path / "quality.json"
    _write(inventory, {
        "source_snapshot_document_count": 12_236,
        "documents": _inventory_documents(),
    })
    _write(build_report, {
        "release_id": "r1",
        "counts": {"chunk_count": 2, "vector_count": 2, "included_document_count": 2,
                   "current_retrievable_document_count": 1, "historical_only_document_count": 1},
        "stats": {"oversized_or_invalid_chunk": 1},
        "database_mutated": False,
        "active_pointer_changed": False,
    })
    _write(verification, {"structural_gate_passed": False, "chunk_count": 2, "invalid_chunk_count": 1})
    report = build(inventory_path=inventory, build_report_path=build_report,
                   verification_path=verification, output=output)
    assert report["quality_gate_passed"] is False
