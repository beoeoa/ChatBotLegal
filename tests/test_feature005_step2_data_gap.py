from __future__ import annotations

from scripts.feature005_step2_data_gap import (
    IMPORT_TIMEOUT_SECONDS,
    REVIEWED_EXISTING_SERVING_SOURCES,
    STEP2_SOURCE_DECISIONS,
    _apply_scope_override,
    _gap_contract,
    _import_payload,
    _repair_missing_domain_quality,
    importable_source_specs,
    validate_step2_source_decisions,
)
from scripts.benchmark_db5_shadow import _collection_chunk_ids
from scripts.legal_search_server import (
    _import_domain_slug,
    _is_official_legal_source_url,
    _same_legal_document_identity,
)


def test_incremental_import_requires_verified_official_https_url() -> None:
    assert _is_official_legal_source_url(
        "https://vanban.chinhphu.vn/?docid=202609&pageid=27160"
    )
    assert _is_official_legal_source_url(
        "https://datafiles.chinhphu.vn/cpp/files/vbpq/2021/02/68.signed.pdf"
    )
    assert _is_official_legal_source_url(
        "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=170084"
    )
    assert _is_official_legal_source_url(
        "https://congbao.haiphong.gov.vn/van-ban/example"
    )
    assert not _is_official_legal_source_url("http://vbpl.vn/document")
    assert not _is_official_legal_source_url("https://example.com/document")
    assert not _is_official_legal_source_url("")


def test_same_number_different_document_type_is_not_a_duplicate_identity() -> None:
    existing_resolution = {
        "law_number": "93/2015/QH13",
        "document_type": "Nghị quyết",
        "title": "Về việc thực hiện chính sách hưởng bảo hiểm xã hội một lần",
    }
    requested_law = {
        "law_number": "93/2015/QH13",
        "document_type": "Luật",
        "title": "Luật Tố tụng hành chính",
    }

    assert not _same_legal_document_identity(existing_resolution, requested_law)
    assert _same_legal_document_identity(
        requested_law,
        {**requested_law, "title": "LUẬT TỐ TỤNG HÀNH CHÍNH"},
    )


def test_incremental_import_persists_canonical_retrieval_domain() -> None:
    assert _import_domain_slug("cu_tru_an_ninh", 9, "An ninh trật tự") == (
        "cu_tru_an_ninh"
    )
    assert _import_domain_slug(None, 8, "Đất đai - Xây dựng - Đô thị") == (
        "dat_dai_xay_dung"
    )
    assert _import_domain_slug(None, 10, "Vi phạm hành chính") == (
        "khieu_nai_to_cao_xu_phat"
    )


def test_step2_decisions_cover_every_verified_gap_without_unsafe_substitution() -> None:
    validation = validate_step2_source_decisions(STEP2_SOURCE_DECISIONS)

    assert validation["gap_rows"] == 8
    assert validation["unresolved_rows"] == 0
    assert validation["unsafe_substitutions"] == 0
    assert validation["duplicate_import_numbers"] == []


def test_step2_replaces_outdated_expectations_and_imports_only_current_sources() -> None:
    by_case = {}
    for decision in STEP2_SOURCE_DECISIONS:
        by_case.setdefault(decision["case_id"], []).append(decision)

    assert any(
        row["decision"] == "correct_expected_source"
        and "26/2023/QH15" in row["replacement_law_numbers"]
        for row in by_case["ct_001"]
    )
    assert any(
        row["decision"] == "correct_expected_source"
        and "154/2024/NĐ-CP" in row["replacement_law_numbers"]
        for row in by_case["ct_002"]
    )
    assert any(
        row["decision"] == "correct_expected_source"
        and row["reason_code"] == "expired_source_replaced"
        for row in by_case["tt_001"]
    )
    assert any(
        row["decision"] == "fail_closed"
        and row["reason_code"] == "partial_effectivity_requires_provision_level_review"
        for row in by_case["golden_urban_003"]
    )
    assert any(
        row["decision"] == "fail_closed"
        and row["reason_code"]
        == "replacement_sources_partially_effective_require_review"
        for row in by_case["golden_urban_004"]
    )

    specs = importable_source_specs(STEP2_SOURCE_DECISIONS)
    law_numbers = {row["law_number"] for row in specs}
    assert law_numbers == {
        "68/2020/QH14",
        "154/2024/NĐ-CP",
        "168/2024/NĐ-CP",
        "58/2026/NĐ-CP",
    }
    assert all(row["effective_date"] <= "2026-07-23" for row in specs)
    assert all(row["expired_date"] is None for row in specs)
    assert all(_is_official_legal_source_url(row["source_url"]) for row in specs)
    assert all(_is_official_legal_source_url(row["content_url"]) for row in specs)

    existing_numbers = {
        row["law_number"] for row in REVIEWED_EXISTING_SERVING_SOURCES
    }
    assert existing_numbers == {"17/2024/TT-BCA", "36/2024/QH15"}
    assert all(
        _is_official_legal_source_url(row["source_url"])
        for row in REVIEWED_EXISTING_SERVING_SOURCES
    )
    assert {
        row["domain_slug"] for row in REVIEWED_EXISTING_SERVING_SOURCES
    } == {"cu_tru_an_ninh", "trat_tu_do_thi"}


def test_import_payload_does_not_send_download_only_metadata() -> None:
    spec = importable_source_specs(STEP2_SOURCE_DECISIONS)[0]
    payload = _import_payload(spec, "Điều 1. Phạm vi điều chỉnh\n" * 20)

    assert "content_url" not in payload
    assert payload["confirmed_official_source"] is True
    assert payload["source_url"].startswith("https://")
    assert payload["structure"] == "auto"


class _FakeCollection:
    def __init__(self, identifiers: list[str]) -> None:
        self.identifiers = identifiers

    def count(self) -> int:
        return len(self.identifiers)

    def get(self, *, limit: int, offset: int, include: list[str]) -> dict:
        return {"ids": self.identifiers[offset : offset + limit]}


def test_shadow_allowlist_includes_incremental_collection_membership() -> None:
    collection = _FakeCollection(["chunk-10", "chunk-20", "chunk-999999"])

    assert _collection_chunk_ids(collection) == {10, 20, 999999}


class _FakeTransaction:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: dict = {}

    def __enter__(self) -> "_FakeTransaction":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters: dict) -> None:
        self.statement = str(statement)
        self.parameters = parameters


class _FakeEngine:
    def __init__(self) -> None:
        self.transaction = _FakeTransaction()

    def begin(self) -> _FakeTransaction:
        return self.transaction


def test_scope_override_updates_single_row_sidecar_instead_of_inserting() -> None:
    engine = _FakeEngine()

    _apply_scope_override(
        engine,
        document_id=120184,
        domain_slug="trat_tu_do_thi",
    )

    assert "UPDATE legal_search_scope" in engine.transaction.statement
    assert "INSERT INTO legal_search_scope" not in engine.transaction.statement
    assert engine.transaction.parameters["document_id"] == 120184
    assert engine.transaction.parameters["domain"] == "trat_tu_do_thi"


class _FakeResult:
    def __init__(self, *, scalar: int = 0, rowcount: int = 0) -> None:
        self.scalar = scalar
        self.rowcount = rowcount

    def scalar_one(self) -> int:
        return self.scalar


class _QualityTransaction(_FakeTransaction):
    def __init__(self) -> None:
        super().__init__()
        self.statements: list[str] = []

    def execute(self, statement, parameters: dict) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        self.parameters = parameters
        if "SELECT count(*)" in sql:
            return _FakeResult(scalar=0)
        return _FakeResult(rowcount=31)


class _QualityEngine(_FakeEngine):
    def __init__(self) -> None:
        self.transaction = _QualityTransaction()


def test_reviewed_domain_repairs_only_missing_domain_quality_reason() -> None:
    engine = _QualityEngine()

    repaired = _repair_missing_domain_quality(
        engine,
        document_id=116875,
    )

    assert repaired == 31
    update = next(
        sql for sql in engine.transaction.statements
        if "UPDATE legal_chunk_quality" in sql
    )
    normalized_update = " ".join(update.split())
    assert (
        "quality.quality_reasons = ARRAY['missing_domain']::text[]"
        in normalized_update
    )
    assert "quality_reasons = ARRAY[]::text[]" in normalized_update
    assert engine.transaction.parameters["document_id"] == 116875


def test_incremental_import_timeout_covers_large_official_instrument() -> None:
    assert IMPORT_TIMEOUT_SECONDS >= 600


def test_underspecified_multi_domain_question_is_fail_closed_not_fabricated() -> None:
    contract = _gap_contract(
        case_id="pilot_an_sinh_y_te_giao_duc_024",
        law_number=None,
        reason_code="underspecified_social_health_or_education_procedure",
        resolution="fail_closed_request_concrete_procedure_and_foreign_factor",
    )

    assert contract["outcome"] == "VERIFIED_DATA_GAP"
    assert contract["expected_law_number"] is None
    assert contract["corpus_match_count"] == 0
    assert contract["resolution"].startswith("fail_closed_request_")
