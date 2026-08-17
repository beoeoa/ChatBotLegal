from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

from api.legal_validity_registry import apply_validity_overlay, project_validity_for_row


def snapshot(mode="protect"):
    verified = datetime(2026, 8, 8, tzinfo=timezone.utc).isoformat()
    return {
        "schema_version": "legal-validity-serving-v1",
        "generated_at": verified,
        "last_success_at": verified,
        "mode": mode,
        "coverage": {"eligible": 5, "observed": 4, "fresh": 4},
        "document_ids": {"1": "31/2024/QH15", "2": "10/2020/NĐ-CP"},
        "documents": {
            "31/2024/QH15": {
                "document_id": "1",
                "normalized_status": "active",
                "serving_action": "allow",
                "source_url": "https://vbpl.vn/active",
                "verified_at": verified,
                "effective_from": "2024-08-01",
                "effective_to": None,
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
            },
            "10/2020/NĐ-CP": {
                "document_id": "2",
                "normalized_status": "expired",
                "serving_action": "historical_only",
                "source_url": "https://vbpl.vn/expired",
                "verified_at": verified,
                "effective_from": "2020-02-01",
                "effective_to": "2026-08-01",
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
            },
            "12/2020/NĐ-CP": {
                "document_id": "3",
                "normalized_status": "expired_partial",
                "serving_action": "block_provisions",
                "source_url": "https://vbpl.vn/partial",
                "verified_at": verified,
                "effective_from": "2020-03-02",
                "effective_to": None,
                "affected_provisions": [{"article": "5", "clause": "2", "point": None}],
                "identity_status": "exact",
                "evidence_status": "sufficient",
            },
            "13/2020/NĐ-CP": {
                "document_id": "4",
                "normalized_status": "expired_partial",
                "serving_action": "block_document",
                "source_url": "https://vbpl.vn/partial-unresolved",
                "verified_at": verified,
                "effective_from": "2020-03-03",
                "effective_to": None,
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "partial_scope_missing",
            },
        },
    }


def result(law_number, *, article="1", clause=None, document_id=None):
    return {
        "chunk_id": f"{law_number}-{article}-{clause or ''}",
        "document_id": document_id,
        "law_number": law_number,
        "article_number": article,
        "clause_number": clause,
        "document_status": "active",
        "content": "Nội dung kiểm thử",
    }


def test_protect_filters_full_and_exact_partial_without_mutating_input():
    payload = {
        "results": [
            result("31/2024/QH15", document_id="1"),
            result("10/2020/NĐ-CP", document_id="2"),
            result("12/2020/NĐ-CP", article="5", clause="2"),
            result("12/2020/NĐ-CP", article="6"),
            result("13/2020/NĐ-CP"),
            result("99/2020/NĐ-CP"),
        ]
    }
    original = deepcopy(payload)

    filtered = apply_validity_overlay(
        payload,
        snapshot=snapshot(),
        as_of=date(2026, 8, 8),
        mode="protect",
    )

    assert payload == original
    assert [item["law_number"] for item in filtered["results"]] == [
        "31/2024/QH15",
        "12/2020/NĐ-CP",
        "99/2020/NĐ-CP",
    ]
    assert filtered["results"][0]["validity_sync"]["status"] == "active"
    assert filtered["results"][-1]["validity_sync"]["warning_code"] == "validity_not_observed"
    trace = filtered["validity_sync"]
    assert trace["filtered_count"] == 3
    assert trace["filtered_reasons"] == {
        "expired": 1,
        "expired_partial": 1,
        "partial_scope_unresolved": 1,
    }


def test_observe_keeps_shadow_items_and_strict_blocks_unobserved():
    payload = {"results": [result("10/2020/NĐ-CP"), result("99/2020/NĐ-CP")]}

    observe = apply_validity_overlay(
        payload,
        snapshot=snapshot("observe"),
        as_of=date(2026, 8, 8),
    )
    strict = apply_validity_overlay(
        payload,
        snapshot=snapshot("strict"),
        as_of=date(2026, 8, 8),
    )

    assert len(observe["results"]) == 2
    assert observe["results"][0]["validity_sync"]["would_block"] is True
    assert strict["results"] == []


def test_historical_as_of_allows_document_inside_known_interval():
    payload = {"results": [result("10/2020/NĐ-CP")]}

    historical = apply_validity_overlay(
        payload,
        snapshot=snapshot(),
        as_of=date(2025, 1, 1),
    )

    assert len(historical["results"]) == 1
    assert historical["results"][0]["validity_sync"]["warning_code"] == "historical_validity"


def test_batch_issues_receive_same_overlay_and_aggregate_trace():
    payload = {
        "issues": [
            {"issue_id": "a", "results": [result("31/2024/QH15")]},
            {"issue_id": "b", "results": [result("10/2020/NĐ-CP")]},
        ]
    }

    overlaid = apply_validity_overlay(
        payload,
        snapshot=snapshot(),
        as_of=date(2026, 8, 8),
    )

    assert len(overlaid["issues"][0]["results"]) == 1
    assert overlaid["issues"][1]["results"] == []
    assert overlaid["validity_sync"]["filtered_count"] == 1


def test_missing_snapshot_is_warning_in_protect_and_fail_closed_in_strict():
    payload = {"results": [result("31/2024/QH15")]}

    protect = apply_validity_overlay(payload, snapshot=None, mode="protect")
    strict = apply_validity_overlay(payload, snapshot=None, mode="strict")

    assert len(protect["results"]) == 1
    assert protect["results"][0]["validity_sync"]["warning_code"] == "validity_snapshot_unavailable"
    assert strict["results"] == []


def test_management_projection_keeps_expired_history_but_marks_current_answer_ineligible():
    row = result("10/2020/N\u0110-CP", document_id="2")

    projected = project_validity_for_row(
        row,
        snapshot=snapshot(),
        as_of=date(2026, 8, 8),
        mode="protect",
    )

    assert projected["status"] == "expired"
    assert projected["serving_action"] == "historical_only"
    assert projected["current_answer_eligible"] is False
    assert projected["historical_lookup_allowed"] is True
    assert projected["display_label"] == "H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
    assert apply_validity_overlay(
        {"results": [row]},
        snapshot=snapshot(),
        as_of=date(2026, 8, 8),
        mode="protect",
    )["results"] == []


def test_management_projection_never_mislabels_unobserved_storage_row_as_verified_active():
    projected = project_validity_for_row(
        result("99/2020/N\u0110-CP"),
        snapshot=snapshot(),
        as_of=date(2026, 8, 8),
        mode="protect",
    )

    assert projected["status"] == "unknown"
    assert projected["warning_code"] == "validity_not_observed"
    assert projected["display_label"] == "Ch\u01b0a x\u00e1c minh hi\u1ec7u l\u1ef1c"
