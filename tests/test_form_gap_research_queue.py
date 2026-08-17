from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_form_gap_research_queue import (
    _future_review_batches,
    build_research_queue,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _cache_path(cache_dir: Path, instrument: str) -> Path:
    digest = hashlib.sha256(instrument.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{digest}.json"


def test_research_queue_deduplicates_identities_and_preserves_only_official_evidence(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    cache_dir = tmp_path / "cache"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "manifest_sha256": "a" * 64,
            "source_snapshot_sha256": "b" * 64,
            "counts": {
                "target_identities": 5,
                "approved_runtime_identities": 2,
                "pending_identities": 3,
                "ready_for_human_attestation_identities": 1,
                "terminal_gap_identities": 2,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(
        run_dir / "gaps.json",
        {
            "records": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-partial",
                    "procedure_id": "1.000001",
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                },
                {
                    "occurrence_id": "occurrence-2",
                    "requirement_identity_id": "identity-partial",
                    "procedure_id": "1.000002",
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                },
                {
                    "occurrence_id": "occurrence-3",
                    "requirement_identity_id": "identity-missing",
                    "procedure_id": "1.000003",
                    "reason_code": "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND",
                },
            ]
        },
    )
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-partial",
                    "procedure_id": "1.000001",
                    "issuing_instrument": "09/2025/TT-BNV",
                    "resolved_form_code": "04",
                    "appendix_identifier": "I",
                    "canonical_identity_key": "canonical-partial",
                },
                {
                    "occurrence_id": "occurrence-2",
                    "requirement_identity_id": "identity-partial",
                    "procedure_id": "1.000002",
                    "issuing_instrument": "09/2025/TT-BNV",
                    "resolved_form_code": "04",
                    "appendix_identifier": "I",
                    "canonical_identity_key": "canonical-partial",
                },
                {
                    "occurrence_id": "occurrence-3",
                    "requirement_identity_id": "identity-missing",
                    "procedure_id": "1.000003",
                    "issuing_instrument": "99/2025/TT-TEST",
                    "resolved_form_code": "01",
                    "canonical_identity_key": "canonical-missing",
                },
            ]
        },
    )
    _write(
        _cache_path(cache_dir, "09/2025/TT-BNV"),
        {
            "status": "found",
            "document": {
                "id": "178434",
                "docNum": "09/2025/TT-BNV",
                "detailUrl": "https://vbpl.vn/van-ban/chi-tiet/example--178434",
                "effStatus": {"name": "Hết hiệu lực một phần"},
                "effFrom": "2025-07-01T00:00:00",
                "effTo": None,
                "documentRelatedList": [{"id": "relation-1"}],
            },
        },
    )
    _write(
        _cache_path(cache_dir, "99/2025/TT-TEST"),
        {
            "status": "found",
            "document": {
                "id": "untrusted",
                "detailUrl": "https://example.com/not-official",
            },
        },
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=cache_dir,
        legal_as_of="2026-07-29",
    )

    assert result["summary"]["identity_count"] == 2
    assert result["summary"]["reason_counts"] == {
        "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND": 1,
        "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW": 1,
    }
    partial = result["records"][0]
    assert partial["requirement_identity_id"] == "identity-partial"
    assert partial["procedure_ids"] == ["1.000001", "1.000002"]
    assert partial["official_source_page"].startswith("https://vbpl.vn/")
    assert partial["official_effectivity_status"] == "Hết hiệu lực một phần"
    assert partial["related_document_reference_count"] == 1
    assert partial["research_action"] == "VERIFY_EXACT_APPENDIX_EFFECTIVITY"
    assert partial["runtime_eligible"] is False
    missing = result["records"][1]
    assert missing["official_source_page"] is None
    assert missing["research_action"] == "RETRY_EXACT_OFFICIAL_DOCUMENT_LOOKUP"
    assert "local_path" not in json.dumps(result)
    assert "does_not_exist" not in json.dumps(result)


def test_research_queue_flags_same_code_across_appendices_before_resolution(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "form_identities": [
            {
                "identity_id": "identity-collision",
                "form_code": "05",
                "issuing_instrument": "148/2025/NĐ-CP",
                "procedure_ids": ["1.013844", "3.000449"],
                "canonical_titles": [
                    "Đơn đăng ký chỉ định theo Mẫu số 05 Phụ lục V "
                    "Nghị định 148/2025/NĐ-CP",
                    "Văn bản công bố theo Mẫu số 05 Phụ lục IV "
                    "Nghị định 148/2025/NĐ-CP",
                ],
            }
        ]
    }
    _write(manifest_path, manifest)
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "manifest_sha256": manifest_sha256,
            "counts": {
                "terminal_gap_identities": 1,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(
        run_dir / "gaps.json",
        {
            "records": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-collision",
                    "procedure_id": "1.013844",
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                },
                {
                    "occurrence_id": "occurrence-2",
                    "requirement_identity_id": "identity-collision",
                    "procedure_id": "3.000449",
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                },
            ]
        },
    )
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-collision",
                    "procedure_id": "1.013844",
                    "issuing_instrument": "148/2025/NĐ-CP",
                    "resolved_form_code": "05",
                },
                {
                    "occurrence_id": "occurrence-2",
                    "requirement_identity_id": "identity-collision",
                    "procedure_id": "3.000449",
                    "issuing_instrument": "148/2025/NĐ-CP",
                    "resolved_form_code": "05",
                },
            ]
        },
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        legal_as_of="2026-07-29",
        requirement_manifest_path=manifest_path,
    )

    record = result["records"][0]
    assert record["reason_code"] == "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW"
    assert record["research_action"] == "SPLIT_COLLIDING_FORM_IDENTITY"
    assert record["identity_collision_appendix_identifiers"] == ["IV", "V"]
    assert record["research_status"] == "PENDING_IDENTITY_MODEL_CORRECTION"
    assert record["approved"] is False
    assert record["runtime_eligible"] is False


def test_research_queue_fails_closed_when_terminal_identity_count_drifts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 2,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(
        run_dir / "gaps.json",
        {
            "records": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-1",
                    "procedure_id": "1.000001",
                    "reason_code": "FORM_CODE_UNRESOLVED",
                }
            ]
        },
    )
    _write(run_dir / "occurrence-registry.json", {"occurrences": []})

    with pytest.raises(ValueError, match="FORM_GAP_RESEARCH_IDENTITY_COUNT_DRIFT"):
        build_research_queue(
            run_dir=run_dir,
            cache_dir=tmp_path / "cache",
            legal_as_of="2026-07-29",
        )


def test_research_queue_rejects_unaccounted_pending_identities(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 0,
                "unaccounted_pending_identities": 1,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(run_dir / "gaps.json", {"records": []})
    _write(run_dir / "occurrence-registry.json", {"occurrences": []})

    with pytest.raises(ValueError, match="FORM_COMPLETION_IDENTITIES_UNACCOUNTED"):
        build_research_queue(
            run_dir=run_dir,
            cache_dir=tmp_path / "cache",
            legal_as_of="2026-07-29",
        )


def test_research_queue_records_probe_resolution_without_promoting_runtime(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    probe_dir = tmp_path / "probe"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 1,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(
        run_dir / "gaps.json",
        {
            "records": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-1",
                    "procedure_id": "1.000001",
                    "reason_code": "INVALID_UNICODE_METADATA",
                }
            ]
        },
    )
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {
                    "occurrence_id": "occurrence-1",
                    "requirement_identity_id": "identity-1",
                    "procedure_id": "1.000001",
                }
            ]
        },
    )
    _write(
        probe_dir / "code-groups.json",
        {
            "groups": [
                {
                    "group_id": "group-1",
                    "appendix_identifier": "II",
                    "procedure_metadata": {},
                }
            ]
        },
    )
    _write(
        probe_dir / "code-resolution.json",
        {
            "pending_records": [
                {
                    "id": "candidate-1",
                    "three_tier_group_id": "group-1",
                    "proposed_canonical_form_id": "form-1",
                    "procedure_id": "1.000001",
                    "form_code": "TP-CC-06",
                    "effectivity_reason_code": "FORM_EFFECTIVITY_VERIFIED",
                    "effectivity_source_url": "https://vbpl.vn/van-ban/effectivity",
                    "provenance": {"document_number": "05/2025/TT-BTP"},
                    "source_page_url": "https://vbpl.vn/van-ban/example",
                    "source_download_url": "https://moj.gov.vn/form.pdf",
                    "sha256": "d" * 64,
                    "effective_from": "2025-07-01",
                    "effective_to": None,
                    "approved": False,
                    "runtime_eligible": False,
                    "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
                    "local_path": "must/not/leak.pdf",
                }
            ]
        },
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        legal_as_of="2026-07-29",
        probe_dirs=[probe_dir],
    )

    record = result["records"][0]
    assert record["research_status"] == (
        "TECHNICALLY_RESOLVED_PENDING_FUTURE_HUMAN_BATCH"
    )
    assert record["research_action"] == "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION"
    assert record["technical_evidence"]["sha256"] == "d" * 64
    assert record["technical_evidence"]["form_code"] == "TP-CC-06"
    assert record["technical_evidence"]["issuing_instrument"] == (
        "05/2025/TT-BTP"
    )
    assert record["technical_evidence"]["effectivity_reason_code"] == (
        "FORM_EFFECTIVITY_VERIFIED"
    )
    assert record["technical_evidence"]["effectivity_source_url"] == (
        "https://vbpl.vn/van-ban/effectivity"
    )
    assert record["technical_evidence"]["appendix_identifier"] == "II"
    assert record["technical_evidence"]["source_download_url"].startswith(
        "https://moj.gov.vn/"
    )
    assert record["runtime_eligible"] is False
    assert result["summary"]["technically_resolved_identity_count"] == 1
    assert result["future_review_batches"] == [
        {
            "batch_id": "future-review-01",
            "status": "PENDING_HUMAN_ATTESTATION",
            "identity_count": 1,
            "requirement_identity_ids": ["identity-1"],
            "human_attestation_required": True,
            "approved": False,
            "runtime_eligible": False,
        }
    ]
    assert "must/not/leak.pdf" not in json.dumps(result)


def test_research_queue_preserves_strict_dvc_provenance_without_private_fields(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    probe_dir = tmp_path / "probe"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-dvc",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 1,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(
        run_dir / "gaps.json",
        {
            "records": [
                {
                    "occurrence_id": "occurrence-dvc",
                    "requirement_identity_id": "identity-dvc",
                    "procedure_id": "1.013868",
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                }
            ]
        },
    )
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {
                    "occurrence_id": "occurrence-dvc",
                    "requirement_identity_id": "identity-dvc",
                    "procedure_id": "1.013868",
                    "issuing_instrument": "91/2016/NĐ-CP",
                    "resolved_form_code": "05",
                    "appendix_identifier": "I",
                }
            ]
        },
    )
    _write(
        probe_dir / "code-groups.json",
        {
            "groups": [
                {
                    "group_id": "group-dvc",
                    "appendix_identifier": "I",
                    "procedure_metadata": {},
                }
            ]
        },
    )
    _write(
        probe_dir / "code-resolution.json",
        {
            "pending_records": [
                {
                    "id": "candidate-dvc",
                    "three_tier_group_id": "group-dvc",
                    "procedure_id": "1.013868",
                    "form_code": "05",
                    "appendix_identifier": "I",
                    "issuing_instrument": "91/2016/NĐ-CP",
                    "effectivity_reason_code": "FORM_EFFECTIVITY_VERIFIED",
                    "effectivity_source_url": "https://dichvucong.gov.vn/quyet-dinh-cong-bo/019eb146-cf41-72c8-9f55-1d3df27dd74a",
                    "provenance": {"document_number": "91/2016/NĐ-CP"},
                    "provenance_kind": "official_dvc_attachment",
                    "source_page_url": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/019d2bff-2d36-7533-81f7-2f3380a96f1b",
                    "source_download_url": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/019d2bff-2d36-7533-81f7-2f3380a96f1b",
                    "source_attachment_id": "019e2aed-9d64-73ee-a43e-245d6a2a06e6",
                    "source_retrieval_url": "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment",
                    "source_retrieval_method": "POST",
                    "source_package_sha256": "e" * 64,
                    "source_package_size_bytes": 24924,
                    "publication_decision_number": "1684/QĐ-BYT",
                    "size_bytes": 19886,
                    "extraction": {
                        "kind": "structural_docx_form_boundary",
                        "complete": True,
                        "element_range": [0, 35],
                        "form_code": "05",
                        "appendix_identifier": "I",
                        "local_path": "must/not/leak.docx",
                    },
                    "sha256": "d" * 64,
                    "approved": False,
                    "runtime_eligible": False,
                    "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
                    "local_path": "must/not/leak.docx",
                    "file_path": "must/not/leak.docx",
                    "source_file_name": "must-not-leak.docx",
                    "source_request_headers": {"Authorization": "must-not-leak"},
                }
            ]
        },
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        legal_as_of="2026-07-29",
        probe_dirs=[probe_dir],
    )

    evidence = result["records"][0]["technical_evidence"]
    assert evidence["provenance_kind"] == "official_dvc_attachment"
    assert evidence["source_attachment_id"] == "019e2aed-9d64-73ee-a43e-245d6a2a06e6"
    assert evidence["source_package_sha256"] == "e" * 64
    assert evidence["source_package_size_bytes"] == 24924
    assert evidence["source_retrieval_url"] == (
        "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment"
    )
    assert evidence["source_retrieval_method"] == "POST"
    assert evidence["publication_decision_number"] == "1684/QĐ-BYT"
    assert evidence["artifact_size_bytes"] == 19886
    assert evidence["extraction"] == {
        "kind": "structural_docx_form_boundary",
        "complete": True,
        "element_range": [0, 35],
        "form_code": "05",
        "appendix_identifier": "I",
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "must/not/leak.docx" not in serialized
    assert "must-not-leak.docx" not in serialized
    assert "Authorization" not in serialized


def test_future_review_batches_are_candidate_only_and_capped_at_25() -> None:
    records = [
        {
            "requirement_identity_id": f"identity-{index:02d}",
            "research_status": (
                "TECHNICALLY_RESOLVED_PENDING_FUTURE_HUMAN_BATCH"
            ),
        }
        for index in range(26)
    ]

    batches = _future_review_batches(records)

    assert [item["identity_count"] for item in batches] == [25, 1]
    assert all(item["identity_count"] <= 25 for item in batches)
    assert all(item["status"] == "PENDING_HUMAN_ATTESTATION" for item in batches)
    assert all(item["human_attestation_required"] is True for item in batches)
    assert all(item["approved"] is False for item in batches)
    assert all(item["runtime_eligible"] is False for item in batches)


def test_research_queue_aggregates_identical_probe_artifact_across_bindings(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    probe_dir = tmp_path / "probe"
    gap_rows = [
        {
            "occurrence_id": f"occurrence-{index}",
            "requirement_identity_id": "identity-1",
            "procedure_id": f"1.00000{index}",
            "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
        }
        for index in (1, 2)
    ]
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 1,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    _write(run_dir / "gaps.json", {"records": gap_rows})
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {**row, "resolved_form_code": "CT01"} for row in gap_rows
            ]
        },
    )
    _write(
        probe_dir / "code-groups.json",
        {
            "groups": [
                {
                    "group_id": "group-1",
                    "procedure_metadata": {
                        "1.000001": {"requirement_identity_id": "identity-1"},
                        "1.000002": {"requirement_identity_id": "identity-1"},
                    },
                }
            ]
        },
    )
    pending_records = []
    for index in (1, 2):
        pending_records.append(
            {
                "id": f"candidate-{index}",
                "three_tier_group_id": "group-1",
                "proposed_canonical_form_id": "form-1",
                "procedure_id": f"1.00000{index}",
                "form_code": "CT02",
                "effectivity_reason_code": "FORM_EFFECTIVITY_VERIFIED",
                "effectivity_source_url": "https://vbpl.vn/van-ban/effectivity",
                "provenance": {"document_number": "53/2025/TT-BCA"},
                "source_page_url": "https://vbpl.vn/van-ban/example",
                "source_download_url": "https://moj.gov.vn/form.doc",
                "sha256": "e" * 64,
                "effective_from": "2025-07-01",
                "effective_to": None,
                "approved": False,
                "runtime_eligible": False,
                "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
            }
        )
    _write(
        probe_dir / "code-resolution.json",
        {"pending_records": pending_records},
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        legal_as_of="2026-07-29",
        probe_dirs=[probe_dir],
    )

    evidence = result["records"][0]["technical_evidence"]
    assert evidence["procedure_ids"] == ["1.000001", "1.000002"]
    assert evidence["candidate_ids"] == ["candidate-1", "candidate-2"]
    assert evidence["canonical_form_ids"] == ["form-1"]
    assert evidence["sha256"] == "e" * 64
    assert result["records"][0]["form_codes"] == ["CT02"]
    assert result["records"][0]["baseline_form_codes"] == ["CT01"]
    assert result["records"][0]["identity_metadata_correction"] == (
        "STRUCTURED_PROBE_FORM_CODE_PRECEDENCE"
    )
    assert result["summary"]["technically_resolved_identity_count"] == 1


def test_probe_fallback_refuses_ambiguous_procedure_to_identity_mapping(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    probe_dir = tmp_path / "probe"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 2,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    gap_rows = [
        {
            "occurrence_id": f"occurrence-{index}",
            "requirement_identity_id": f"identity-{index}",
            "procedure_id": "1.000001",
            "reason_code": "INVALID_UNICODE_METADATA",
        }
        for index in (1, 2)
    ]
    _write(run_dir / "gaps.json", {"records": gap_rows})
    _write(run_dir / "occurrence-registry.json", {"occurrences": gap_rows})
    _write(
        probe_dir / "code-groups.json",
        {"groups": [{"group_id": "group-1", "procedure_metadata": {}}]},
    )
    _write(
        probe_dir / "code-resolution.json",
        {
            "pending_records": [
                {
                    "id": "candidate-1",
                    "three_tier_group_id": "group-1",
                    "procedure_id": "1.000001",
                    "source_page_url": "https://vbpl.vn/van-ban/example",
                    "source_download_url": "https://moj.gov.vn/form.pdf",
                    "sha256": "d" * 64,
                    "approved": False,
                    "runtime_eligible": False,
                    "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="FORM_GAP_RESEARCH_PROBE_IDENTITY_MISSING"):
        build_research_queue(
            run_dir=run_dir,
            cache_dir=tmp_path / "cache",
            legal_as_of="2026-07-29",
            probe_dirs=[probe_dir],
        )


def test_probe_fallback_uses_exact_procedure_instrument_and_form_code(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    probe_dir = tmp_path / "probe"
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "counts": {
                "terminal_gap_identities": 2,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    gap_rows = [
        {
            "occurrence_id": f"occurrence-{code}",
            "requirement_identity_id": f"identity-{code}",
            "procedure_id": "1.000001",
            "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
        }
        for code in ("03", "04")
    ]
    _write(run_dir / "gaps.json", {"records": gap_rows})
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {
                    **row,
                    "issuing_instrument": "09/2025/TT-BNV",
                    "resolved_form_code": code,
                }
                for row, code in zip(gap_rows, ("03", "04"), strict=True)
            ]
        },
    )
    _write(
        probe_dir / "code-groups.json",
        {"groups": [{"group_id": "group-04", "procedure_metadata": {}}]},
    )
    _write(
        probe_dir / "code-resolution.json",
        {
            "pending_records": [
                {
                    "id": "candidate-04",
                    "three_tier_group_id": "group-04",
                    "procedure_id": "1.000001",
                    "form_code": "Mẫu số 04",
                    "provenance": {"document_number": "09 / 2025 / tt-bnv"},
                    "source_page_url": "https://vbpl.vn/van-ban/example",
                    "source_download_url": "https://moj.gov.vn/form-04.pdf",
                    "sha256": "d" * 64,
                    "effectivity_reason_code": "FORM_EFFECTIVITY_VERIFIED",
                    "effectivity_source_url": "https://vbpl.vn/van-ban/effectivity",
                    "approved": False,
                    "runtime_eligible": False,
                    "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
                }
            ]
        },
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        legal_as_of="2026-07-29",
        probe_dirs=[probe_dir],
    )

    records = {
        record["requirement_identity_id"]: record for record in result["records"]
    }
    assert records["identity-03"].get("technical_evidence") is None
    assert records["identity-04"]["technical_evidence"]["candidate_id"] == (
        "candidate-04"
    )
    assert result["summary"]["technically_resolved_identity_count"] == 1


def test_probe_fallback_prefers_checksum_bound_manifest_code_over_legacy_registry(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    probe_dir = tmp_path / "probe"
    manifest_path = tmp_path / "form-requirement-manifest.json"
    _write(
        manifest_path,
        {
            "form_identities": [
                {
                    "identity_id": "identity-1a",
                    "form_code": "1A",
                    "issuing_instrument": "20/2021/NĐ-CP",
                    "procedure_ids": ["1.001776"],
                },
                {
                    "identity_id": "identity-1b",
                    "form_code": "1B",
                    "issuing_instrument": "20/2021/NĐ-CP",
                    "procedure_ids": ["1.001776"],
                },
            ]
        },
    )
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    _write(
        run_dir / "report.json",
        {
            "run_id": "run-1",
            "legal_as_of": "2026-07-29",
            "manifest_sha256": manifest_sha256,
            "counts": {
                "terminal_gap_identities": 2,
                "unaccounted_pending_identities": 0,
            },
            "feature_flag_enabled": False,
        },
    )
    gap_rows = [
        {
            "occurrence_id": f"occurrence-{suffix}",
            "requirement_identity_id": f"identity-{suffix}",
            "procedure_id": "1.001776",
            "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
        }
        for suffix in ("1a", "1b")
    ]
    _write(run_dir / "gaps.json", {"records": gap_rows})
    _write(
        run_dir / "occurrence-registry.json",
        {
            "occurrences": [
                {
                    **row,
                    "issuing_instrument": "20/2021/NĐ-CP",
                    "resolved_form_code": (
                        "ZZ"
                        if row["requirement_identity_id"] == "identity-1a"
                        else "1A"
                    ),
                }
                for row in gap_rows
            ]
        },
    )
    _write(
        probe_dir / "code-groups.json",
        {"groups": [{"group_id": "group-1b", "procedure_metadata": {}}]},
    )
    _write(
        probe_dir / "code-resolution.json",
        {
            "pending_records": [
                {
                    "id": "candidate-1b",
                    "three_tier_group_id": "group-1b",
                    "procedure_id": "1.001776",
                    "form_code": "1B",
                    "provenance": {"document_number": "20/2021/NĐ-CP"},
                    "source_page_url": "https://vbpl.vn/van-ban/example",
                    "source_download_url": "https://moj.gov.vn/form-1b.pdf",
                    "sha256": "e" * 64,
                    "effectivity_reason_code": "FORM_EFFECTIVITY_VERIFIED",
                    "effectivity_source_url": "https://vbpl.vn/van-ban/effectivity",
                    "approved": False,
                    "runtime_eligible": False,
                    "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
                }
            ]
        },
    )

    result = build_research_queue(
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        legal_as_of="2026-07-29",
        probe_dirs=[probe_dir],
        requirement_manifest_path=manifest_path,
    )

    records = {
        record["requirement_identity_id"]: record for record in result["records"]
    }
    assert records["identity-1a"].get("technical_evidence") is None
    assert records["identity-1a"]["baseline_form_codes"] == ["ZZ"]
    assert records["identity-1a"]["form_codes"] == ["1A"]
    assert records["identity-1a"]["identity_metadata_correction"] == (
        "CHECKSUM_BOUND_MANIFEST_FORM_CODE_PRECEDENCE"
    )
    assert records["identity-1b"]["technical_evidence"]["form_code"] == "1B"
    assert records["identity-1b"]["baseline_form_codes"] == ["1A"]
    assert records["identity-1b"]["form_codes"] == ["1B"]
    assert result["summary"]["technically_resolved_identity_count"] == 1
