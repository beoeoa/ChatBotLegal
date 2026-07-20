from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.prepare_pilot_expert_review_packet import (
    PacketValidationError,
    assert_packet_privacy,
    build_expert_review_packet,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "pilot" / "ask_quality_manifest.json"
EXPERT_PATH = ROOT / "notebook_data" / "legal-golden-expert-review.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).casefold()
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def test_packet_contains_exact_manifest_records_and_no_protected_content():
    manifest = _load(MANIFEST_PATH)
    packet = build_expert_review_packet(manifest, _load(EXPERT_PATH))

    records = packet["records"]
    assert len(records) == 30
    assert {item["review_id"] for item in records} == {
        item["review_id"] for item in manifest["cases"]
    }
    assert {
        (item["domain"], item["role"], item["scenario_class"])
        for item in records
    } == {
        (item["domain"], item["role"], item["scenario_class"])
        for item in manifest["cases"]
    }

    forbidden = {
        "answer",
        "citations",
        "citizen_question",
        "officer_question",
        "prompt",
        "question",
        "required_facts",
        "token",
    }
    assert forbidden.isdisjoint(set(_walk_keys(packet)))
    assert_packet_privacy(packet)


def test_packet_leaves_all_expert_authority_fields_pending():
    packet = build_expert_review_packet(_load(MANIFEST_PATH), _load(EXPERT_PATH))

    for record in packet["records"]:
        assert record["expert_review_status"] == "pending"
        assert record["expert_name"] is None
        assert record["reviewed_at"] is None
        assert record["expert_score"] is None
        assert record["citation_applicable"] is None
        assert record["form_applicable"] is None
        assert record["critical_hallucination"] is None
        assert record["review_notes"] is None
        for candidate in record["source_candidates"]:
            assert candidate["review_state"] == "candidate_pending_review"
            assert candidate["accepted_by_expert"] is None


def test_packet_source_candidates_are_labels_not_promoted_legal_facts():
    packet = build_expert_review_packet(_load(MANIFEST_PATH), _load(EXPERT_PATH))
    record = next(item for item in packet["records"] if item["source_candidates"])

    candidate = record["source_candidates"][0]
    assert candidate["candidate_label"]
    assert candidate["review_state"] == "candidate_pending_review"
    assert candidate["document_id"] is None
    assert candidate["source_url"] is None
    assert candidate["authority"] is None
    assert candidate["effective_status"] is None
    assert candidate["jurisdiction"] is None


def test_packet_rejects_manifest_or_expert_mismatch():
    manifest = _load(MANIFEST_PATH)
    experts = _load(EXPERT_PATH)
    experts["records"] = [
        item
        for item in experts["records"]
        if item.get("review_id") != manifest["cases"][0]["review_id"]
    ]

    with pytest.raises(PacketValidationError, match="unknown_review_id"):
        build_expert_review_packet(manifest, experts)


def test_packet_privacy_rejects_auto_approval_or_question_content():
    with pytest.raises(PacketValidationError):
        assert_packet_privacy(
            {
                "records": [
                    {
                        "review_id": "case:officer",
                        "expert_review_status": "approved",
                        "expert_name": "automated",
                        "reviewed_at": "2026-07-17T00:00:00Z",
                        "expert_score": 10,
                    }
                ]
            }
        )

    with pytest.raises(PacketValidationError):
        assert_packet_privacy({"records": [], "question": "protected"})
