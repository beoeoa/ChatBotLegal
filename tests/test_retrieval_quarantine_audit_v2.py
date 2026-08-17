from __future__ import annotations

from datetime import date
import json

from scripts.audit_retrieval_quarantine_v2 import (
    allowed_official_url,
    build_report,
    propose_quarantine_disposition,
    summarize_records,
)


def test_quarantine_proposal_never_approves_expired_out_of_scope_document() -> None:
    row = {
        "document_id": 1,
        "status_observed": "expired",
        "scope_included_observed": False,
        "effective_date": "2020-01-01",
        "expired_date": "2021-01-01",
    }

    proposal = propose_quarantine_disposition(row, legal_as_of=date(2026, 8, 16))

    assert proposal["provisional_candidate_state"] == "historical_only"
    assert proposal["review_reason"] == "expired_and_outside_scope"
    assert proposal["approved_for_serving"] is False
    assert proposal["legal_review_status"] == "required"


def test_quarantine_proposal_keeps_future_and_staging_out_of_serving() -> None:
    future = propose_quarantine_disposition(
        {
            "status_observed": "active",
            "scope_included_observed": False,
            "effective_date": "2027-01-01",
            "expired_date": None,
        },
        legal_as_of=date(2026, 8, 16),
    )
    staging = propose_quarantine_disposition(
        {
            "status_observed": "staging",
            "scope_included_observed": False,
            "effective_date": "2021-01-01",
            "expired_date": None,
        },
        legal_as_of=date(2026, 8, 16),
    )

    assert future["provisional_candidate_state"] == "quarantined"
    assert future["review_reason"] == "future_effective"
    assert staging["provisional_candidate_state"] == "quarantined"
    assert staging["review_reason"] == "staging_record"


def test_allowed_official_url_rejects_non_https_and_lookalike_hosts() -> None:
    assert allowed_official_url("https://vbpl.vn/Pages/vbpq-toanvan.aspx") is True
    assert allowed_official_url("https://sub.vbpl.vn/document") is True
    assert allowed_official_url("http://vbpl.vn/document") is False
    assert allowed_official_url("https://vbpl.vn.attacker.example/document") is False


def test_summary_counts_transport_without_conflating_legal_approval() -> None:
    summary = summarize_records(
        [
            {
                "provisional_candidate_state": "historical_only",
                "review_reason": "expired_and_outside_scope",
                "legal_review_status": "required",
                "transport_observation": {"fetched": True, "status_code": 200},
            },
            {
                "provisional_candidate_state": "quarantined",
                "review_reason": "future_effective",
                "legal_review_status": "required",
                "transport_observation": {"fetched": False, "status_code": 404},
            },
        ]
    )

    assert summary["record_count"] == 2
    assert summary["fetched_count"] == 1
    assert summary["http_status_counts"] == {"200": 1, "404": 1}
    assert summary["approved_for_serving_count"] == 0


def test_resume_does_not_treat_no_fetch_checkpoint_as_completed(tmp_path, monkeypatch) -> None:
    documents = []
    for index in range(1, 12_237):
        documents.append(
            {
                "document_id": index,
                "law_number": f"L/{index}",
                "status_observed": "expired" if index <= 4_994 else "active",
                "serving_state": "quarantined" if index <= 4_994 else "current_retrievable",
                "classification_basis": "outside_search_scope" if index <= 4_994 else "scope_and_effectivity_observed",
                "scope_included_observed": False if index <= 4_994 else True,
                "effective_date": "2020-01-01",
                "expired_date": "2021-01-01" if index <= 4_994 else None,
                "article_count": 1,
                "chunk_count": 1,
                "source_url": "https://vbpl.vn/example",
            }
        )
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(
        json.dumps(
            {"legal_as_of": "2026-08-16", "source_snapshot_sha256": "a" * 64, "active_pointer": "baseline", "documents": documents}
        ),
        encoding="utf-8",
    )
    output = tmp_path / "audit.json"
    build_report(input_path=inventory_path, output_path=output, fetch=False)
    monkeypatch.setattr(
        "scripts.audit_retrieval_quarantine_v2._fetch_transport",
        lambda url, *, timeout: {"url": url, "fetched": False, "reason": "mocked"},
    )
    report = build_report(input_path=inventory_path, output_path=output, fetch=True, workers=1, timeout=0.001, resume=True)

    assert report["fetch_enabled"] is True
    assert report["summary"]["fetched_count"] == 0
    assert all(
        (row["transport_observation"].get("reason") != "fetch_disabled")
        for row in report["records"]
    )
