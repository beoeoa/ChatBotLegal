import pytest

from scripts.repair_golden294_teacher_article4 import (
    ARTICLE_ID,
    ARTICLE_NUMBER,
    CHUNK_ID,
    DOCUMENT_ID,
    LAW_NUMBER,
    NEW_TEXT,
    OLD_TEXT,
    corrected_text,
    passage_text,
    validate_identity,
)


def _row() -> dict:
    return {
        "document_id": DOCUMENT_ID,
        "article_id": ARTICLE_ID,
        "chunk_id": CHUNK_ID,
        "law_number": LAW_NUMBER,
        "article_number": ARTICLE_NUMBER,
        "document_status": "active",
        "article_status": "active",
        "document_title": "Luật Nhà giáo",
        "document_type": "Luật",
        "issuing_agency": "Quốc hội",
        "scope": "Toàn quốc",
        "sector": "Giáo dục và đào tạo",
        "field_name": "Giáo dục và đào tạo",
        "article_title": "Điều 4. Giải thích từ ngữ",
        "chunk_heading": "Điều 4",
    }


def test_exact_official_replacement_is_idempotent():
    source = f"Giáo viên giảng dạy {OLD_TEXT}."
    corrected = corrected_text(source)

    assert corrected.count(NEW_TEXT) == 1
    assert OLD_TEXT not in corrected
    assert corrected_text(corrected) == corrected


def test_replacement_rejects_ambiguous_or_missing_source():
    with pytest.raises(ValueError, match="NOT_EXACT"):
        corrected_text(f"{OLD_TEXT}; {OLD_TEXT}")
    with pytest.raises(ValueError, match="NOT_EXACT"):
        corrected_text("nội dung khác")


def test_identity_gate_is_exact_and_fail_closed():
    row = _row()
    validate_identity(row)
    row["chunk_id"] = 1
    with pytest.raises(RuntimeError, match="chunk_id"):
        validate_identity(row)


def test_embedding_passage_contains_grounding_metadata_and_new_text():
    value = passage_text(_row(), f"1. {NEW_TEXT}.")

    assert LAW_NUMBER in value
    assert "Luật Nhà giáo" in value
    assert NEW_TEXT in value
