from __future__ import annotations

from datetime import date

from api.legal_structural_chunking import parse_structural_parents, split_parent_children_v2
from api.retrieval_release_contracts import (
    DOMAIN_BLOCK_SIZE,
    INVENTORY_DOCUMENT_COUNT,
    classify_inventory_document,
    inventory_counts,
    require_staging_collection_target,
    validate_inventory_partition,
)
from scripts.validate_retrieval_eval_suite_v1 import _scaled_target, validate_suite


class _WordTokenizer:
    def encode(self, value: str, add_special_tokens: bool = False) -> list[int]:
        return list(range(len(value.split())))

    def decode(self, values: list[int], skip_special_tokens: bool = False) -> str:
        return "x " * len(values)


def test_eval_quota_contract_matches_the_100_case_domain_block():
    # 65 current + 15 historical answers + 5 temporal + 5 insufficient-facts
    # + 10 out-of-scope refusals.
    components = (65, 15, 5, 5, 10)
    for split, block_size in DOMAIN_BLOCK_SIZE.items():
        assert sum(_scaled_target(value, split) for value in components) == block_size


def test_inventory_classification_is_explicit_and_partitioned():
    rows = [
        classify_inventory_document(
            {
                "document_id": 1,
                "law_number": "1/QH",
                "status": "active",
                "effective_date": "2020-01-01",
                "expired_date": None,
                "source_url": "https://example.gov.vn/1",
                "article_count": 1,
                "chunk_count": 1,
                "scope_included": True,
            },
            legal_as_of=date(2026, 8, 16),
        ),
        classify_inventory_document(
            {
                "document_id": 2,
                "law_number": "2/QH",
                "status": "expired",
                "effective_date": "2010-01-01",
                "expired_date": "2025-01-01",
                "source_url": "https://example.gov.vn/2",
                "article_count": 1,
                "chunk_count": 1,
                "scope_included": True,
            },
            legal_as_of=date(2026, 8, 16),
        ),
        classify_inventory_document(
            {
                "document_id": 3,
                "law_number": "",
                "status": "staging",
                "source_url": None,
                "article_count": 0,
                "chunk_count": 0,
                "scope_included": False,
            },
            legal_as_of=date(2026, 8, 16),
        ),
    ]
    assert [row["serving_state"] for row in rows] == [
        "current_retrievable",
        "historical_only",
        "quarantined",
    ]
    assert inventory_counts(rows)["state_sum"] == 3
    assert validate_inventory_partition(rows) == [
        f"inventory_document_count:3!={INVENTORY_DOCUMENT_COUNT}"
    ]


def test_future_effective_is_separate_from_quarantine() -> None:
    row = classify_inventory_document(
        {
            "document_id": 4,
            "law_number": "4/QH16",
            "status": "active",
            "effective_date": "2027-01-01",
            "expired_date": None,
            "source_url": "https://example.gov.vn/4",
            "article_count": 1,
            "chunk_count": 1,
            "scope_included": True,
        },
        legal_as_of=date(2026, 8, 16),
    )

    assert row["serving_state"] == "future_effective"
    assert row["classification_basis"] == "effective_date_after_legal_as_of"


def test_inventory_partition_normalizes_ids_and_rejects_invalid_rows():
    rows = [
        {"document_id": "7", "serving_state": "quarantined"},
        {"document_id": 7, "serving_state": "quarantined"},
        {"document_id": 0, "serving_state": "current_retrievable"},
        {"document_id": None, "serving_state": "provisional"},
    ]
    errors = validate_inventory_partition(rows)
    assert "duplicate_document_id" in errors
    assert "invalid_document_id" in errors
    assert any(item.startswith("unknown_serving_state:") for item in errors)


def test_v2_splitter_requires_tokenizer_and_never_silently_truncates():
    parent = {
        "article_number": "1",
        "title": "Điều 1. Nội dung",
        "content": " ".join(["nội dung"] * 1_050),
        "parent_kind": "article",
    }
    children = split_parent_children_v2(
        parent,
        tokenizer=_WordTokenizer(),
        max_tokens=512,
        overlap=64,
        release_id="release-v2-test",
    )
    assert len(children) >= 2
    assert all(0 < row["token_count"] <= 512 for row in children)
    assert all(row["quality_assessed"] and row["eligible"] for row in children)
    assert all(row["serving_state"] == "retrievable" for row in children)


def test_v2_splitter_counts_full_embedding_passage_with_prefix():
    parent = {
        "article_number": "1",
        "title": "Điều 1. Nội dung",
        "content": " ".join(["nội dung"] * 500),
        "parent_kind": "article",
    }
    children = split_parent_children_v2(
        parent,
        tokenizer=_WordTokenizer(),
        max_tokens=512,
        overlap=64,
        release_id="release-v2-test",
        passage_prefix="Luật mẫu\n01/2026/QH15",
    )
    assert children
    assert all(row["token_count"] <= 512 for row in children)
    assert all(row["passage_text"].startswith("Luật mẫu\n01/2026/QH15\n") for row in children)
    assert all(row["passage_sha256"] == row["passage_text_sha256"] for row in children)


def test_v2_splitter_simplifies_malformed_oversized_heading_without_truncating_content():
    parent = {
        "article_number": "7",
        "title": "Điều 7. " + "tiêu đề lỗi " * 800,
        "content": "nội dung pháp lý ngắn",
        "parent_kind": "article",
    }
    children = split_parent_children_v2(
        parent,
        tokenizer=_WordTokenizer(),
        max_tokens=512,
        overlap=64,
        release_id="release-v2-test",
        passage_prefix="Văn bản mẫu\n07/2026/NĐ-CP",
    )
    assert len(children) == 1
    assert children[0]["content"] == "nội dung pháp lý ngắn"
    assert "structural_heading_simplified_for_token_budget" in children[0]["quality_reasons"]
    assert children[0]["token_count"] <= 512


def test_structural_parser_normalizes_unicode_and_preserves_appendix_boundary():
    decomposed = "Điê\u0300u 1. Nội dung\r\n1. Khoản đầu\r\nPhụ lục I: Bảng hồ sơ\r\nDòng bảng"
    parents = parse_structural_parents(decomposed)
    assert [row["parent_kind"] for row in parents] == ["article", "appendix"]
    assert parents[0]["article_number"] == "1"
    assert parents[1]["article_number"] == "PL-I"
    assert "\r" not in parents[0]["content"]


def test_structural_parser_does_not_promote_prose_or_table_like_lines_to_headings():
    content = (
        "Phụ lục kèm theo hồ sơ là một câu văn.\n"
        "BẢNG THÀNH PHẦN HỒ SƠ\n"
        "Cột 1 | Cột 2\n"
        "Nội dung OCR chưa chắc chắn"
    )
    parents = parse_structural_parents(content)
    assert parents == []


def test_ocr_like_uncertain_heading_remains_unstructured_text():
    # OCR commonly loses the Vietnamese diacritic in "Điều". The parser must
    # not guess a legal boundary from that uncertain line.
    content = "Dieu 3. Noi dung OCR\n1. Van ban can legal review"
    assert parse_structural_parents(content) == []


def test_eval_suite_validator_fails_closed_for_legacy_or_incomplete_payload():
    report = validate_suite(
        {
            "schema_version": "retrieval-eval-suite-v1",
            "dataset_version": "draft",
            "source_snapshot_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "review_policy": {
                "golden_development_allowed": True,
                "hard_negative_development_allowed": True,
                "holdout_sealed": False,
            },
            "cases": [],
            "suite_sha256": "c" * 64,
        }
    )
    assert report["valid"] is False
    assert any("total_case_count" in item for item in report["errors"])
    assert "holdout_not_sealed" in report["errors"]


def test_serving_pointer_contract_is_distinct_from_collection_manifest():
    from jsonschema import Draft202012Validator
    from pathlib import Path
    import json

    schema_path = Path("specs/018-production-release-readiness/contracts/retrieval-serving-manifest-v3.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["kind"]["const"] == "release_pointer"
    assert "kind" in schema["required"]
    assert Draft202012Validator(schema).schema["properties"]["kind"]["const"] == "release_pointer"


def test_serving_pointer_builder_uses_release_pointer_schema():
    import inspect
    import json
    from pathlib import Path
    from scripts import build_retrieval_serving_manifest_v3 as builder

    schema = json.loads(
        Path("specs/018-production-release-readiness/contracts/retrieval-serving-manifest-v3.schema.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["kind"]["const"] == "release_pointer"
    assert "retrieval-serving-manifest-v3.schema.json" in inspect.getsource(
        builder._validate_schema
    )


def test_staging_writer_rejects_active_pointer_alias(tmp_path):
    (tmp_path / "active_core_collection.txt").write_text("active-v1\n", encoding="utf-8")
    assert require_staging_collection_target("shadow-v2", tmp_path) == "active-v1"
    try:
        require_staging_collection_target("active-v1", tmp_path)
    except RuntimeError as exc:
        assert str(exc) == "staging_target_must_differ_from_active_pointer"
    else:  # pragma: no cover - assertion branch
        raise AssertionError("active pointer alias was not rejected")
