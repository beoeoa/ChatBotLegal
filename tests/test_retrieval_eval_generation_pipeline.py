from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from api.retrieval_eval_generation import (
    DOMAINS,
    SPLIT_SIZES,
    build_candidate,
    build_generation_plan,
    build_generation_provenance,
    ensure_external_holdout_path,
    require_local_ollama_url,
    validate_candidate_pool,
    validate_generated_question,
    validate_generation_plan,
)


def test_generation_plan_has_exact_split_domain_and_scenario_quotas() -> None:
    plan = build_generation_plan()

    assert len(plan) == 2_000
    assert Counter(item["split"] for item in plan) == Counter(SPLIT_SIZES)

    for split, split_size in SPLIT_SIZES.items():
        per_domain = split_size // len(DOMAINS)
        split_rows = [item for item in plan if item["split"] == split]
        assert Counter(item["domain"] for item in split_rows) == {
            domain: per_domain for domain in DOMAINS
        }

        for domain in DOMAINS:
            domain_rows = [item for item in split_rows if item["domain"] == domain]
            multiplier = per_domain // 100
            assert Counter(item["scenario"] for item in domain_rows) == {
                "current_answer": 65 * multiplier,
                "historical_answer": 15 * multiplier,
                "temporal_refusal": 5 * multiplier,
                "insufficient_facts_refusal": 5 * multiplier,
                "out_of_scope_refusal": 10 * multiplier,
            }

            tags = Counter(tag for item in domain_rows for tag in item["coverage_tags"])
            assert tags["exact_law_article"] >= 20 * multiplier
            assert tags["procedure"] >= 20 * multiplier
            assert tags["multi_issue"] >= 15 * multiplier
            assert tags["validity_trap"] >= 20 * multiplier

    validate_generation_plan(plan)


def test_plan_is_pending_final_review_and_holdout_is_sealed() -> None:
    plan = build_generation_plan()

    assert len({item["case_id"] for item in plan}) == 2_000
    assert {item["review_status"] for item in plan} == {"pending_final_review"}
    assert all(item["visibility"] == "sealed" for item in plan if item["split"] == "production-holdout")
    assert all(item["visibility"] == "development" for item in plan if item["split"] != "production-holdout")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434/",
        "http://[::1]:11434",
    ],
)
def test_only_local_ollama_endpoints_are_accepted(url: str) -> None:
    assert require_local_ollama_url(url).startswith("http://")


@pytest.mark.parametrize(
    "url",
    [
        "https://openrouter.ai/api/v1",
        "https://api.deepseek.com",
        "http://192.168.1.20:11434",
        "ftp://localhost:11434",
    ],
)
def test_remote_or_non_http_model_endpoints_fail_closed(url: str) -> None:
    with pytest.raises(ValueError, match="local Ollama"):
        require_local_ollama_url(url)


def test_candidate_copies_approved_source_provenance_but_never_auto_approves() -> None:
    slot = build_generation_plan()[0]
    source = {
        "document_id": "doc-001",
        "law_number": "01/2024/NĐ-CP",
        "article": "Điều 3",
        "official_source_url": "https://vbpl.vn/example",
        "source_snapshot_sha256": "a" * 64,
        "passage_sha256": "b" * 64,
        "metadata_attested": True,
    }
    provenance = build_generation_provenance(
        ragas_version="0.4.3",
        deepeval_version="3.9.9",
        ollama_version="0.32.3",
        generation_model="qwen2.5:3b",
        generation_model_digest="c" * 64,
        embedding_model="nomic-embed-text:latest",
        embedding_model_digest="d" * 64,
        ollama_url="http://127.0.0.1:11434",
    )

    candidate = build_candidate(
        slot=slot,
        question="Điều 3 quy định điều kiện nào?",
        source=source,
        framework="ragas",
        provenance=provenance,
    )

    assert candidate["official_source_url"] == source["official_source_url"]
    assert candidate["source_snapshot_sha256"] == source["source_snapshot_sha256"]
    assert candidate["review_status"] == "pending_final_review"
    assert candidate["reviewer_approval"] is None
    assert candidate["expected_answer"] is None
    assert candidate["generation"]["network_policy"] == "local_only"


def test_candidate_rejects_unattested_or_incomplete_source() -> None:
    slot = build_generation_plan()[0]
    source = {
        "document_id": "doc-001",
        "law_number": "01/2024/NĐ-CP",
        "article": "Điều 3",
        "source_snapshot_sha256": "a" * 64,
        "passage_sha256": "b" * 64,
        "metadata_attested": False,
    }
    provenance = {
        "network_policy": "local_only",
        "ollama_url": "http://127.0.0.1:11434",
    }

    with pytest.raises(ValueError, match="attested official source"):
        build_candidate(
            slot=slot,
            question="Điều kiện nào được áp dụng?",
            source=source,
            framework="deepeval",
            provenance=provenance,
        )


def test_holdout_artifact_must_be_outside_repository(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="outside the repository"):
        ensure_external_holdout_path(repo_root / "reports" / "holdout.json", repo_root)

    external = tmp_path / "custody" / "holdout.json"
    assert ensure_external_holdout_path(external, repo_root) == external.resolve()


def test_duplicate_questions_across_splits_fail_closed() -> None:
    plan = build_generation_plan()
    common = {
        "official_source_url": "https://vbpl.vn/example",
        "source_snapshot_sha256": "a" * 64,
        "passage_sha256": "b" * 64,
        "review_status": "pending_final_review",
    }
    candidates = [
        {**plan[0], **common, "question": "Hồ sơ cần những giấy tờ nào?"},
        {**plan[-1], **common, "question": "  hồ sơ CẦN những giấy tờ nào ? "},
    ]

    with pytest.raises(ValueError, match="duplicate question"):
        validate_candidate_pool(candidates, require_complete=False)


def test_generated_question_rejects_invented_legal_instrument() -> None:
    slot = {
        "case_id": "E-GR-0001",
        "scenario": "historical_answer",
        "coverage_tags": [],
    }
    source = {"law_number": "60/2014/QH13", "article": "3"}

    errors = validate_generated_question(
        "Theo Điều 3 của Hiến pháp 2013, quy định áp dụng vào năm 2020 thế nào?",
        slot=slot,
        source=source,
    )

    assert "invented_legal_instrument_title" in errors


def test_exact_and_historical_slots_require_their_grounding_anchors() -> None:
    slot = {
        "case_id": "E-GR-0002",
        "scenario": "historical_answer",
        "coverage_tags": ["exact_law_article", "validity_trap"],
    }
    source = {"law_number": "60/2014/QH13", "article": "3"}

    errors = validate_generated_question(
        "Quy định này được áp dụng thế nào?",
        slot=slot,
        source=source,
    )

    assert "exact_law_number_missing" in errors
    assert "exact_article_missing" in errors
    assert "historical_anchor_missing" in errors


def test_out_of_scope_question_cannot_smuggle_a_legal_citation() -> None:
    slot = {
        "case_id": "E-PH-0099",
        "scenario": "out_of_scope_refusal",
        "coverage_tags": [],
    }
    source = {"law_number": "60/2014/QH13", "article": "3"}

    errors = validate_generated_question(
        "Theo 60/2014/QH13, nên mua điện thoại nào để chơi game?",
        slot=slot,
        source=source,
    )

    assert "out_of_scope_contains_legal_citation" in errors
