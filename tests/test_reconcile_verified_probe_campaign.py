import hashlib
import json
from pathlib import Path

from pypdf import PdfWriter

from scripts.reconcile_verified_probe_campaign import reconcile_campaign


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "runs" / "source-run"
    source.mkdir(parents=True)
    manifest_sha = "a" * 64
    snapshot_sha = "b" * 64
    _write(
        source / "report.json",
        {
            "run_id": "source-run",
            "legal_as_of": "2026-07-30",
            "status": "READY_FOR_HUMAN_ATTESTATION",
            "manifest_sha256": manifest_sha,
            "source_snapshot_sha256": snapshot_sha,
            "completion_counts": {
                "target_identities": 2,
                "approved_runtime_identities": 1,
                "pending_identities": 1,
                "ready_for_human_attestation_identities": 0,
                "terminal_gap_identities": 1,
                "unaccounted_pending_identities": 0,
            },
            "counts": {},
        },
    )
    _write(
        source / "review-batches.json",
        {
            "manifest_sha256": manifest_sha,
            "source_snapshot_sha256": snapshot_sha,
            "batches": [],
        },
    )
    _write(source / "review-shortlist.json", {"records": []})
    _write(
        source / "gaps.json",
        {
            "records": [
                {
                    "requirement_identity_id": "identity-1",
                    "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
                }
            ]
        },
    )
    _write(
        source / "source-attempts.json",
        {
            "records": [],
            "identity_records": [
                {
                    "requirement_identity_id": "identity-1",
                    "status": "terminal_gap",
                    "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
                }
            ],
            "identity_count": 1,
        },
    )
    _write(
        source / "effectivity-graph.json",
        {
            "nodes": [
                {
                    "requirement_identity_id": "identity-1",
                    "effectivity_status": "not_eligible",
                }
            ],
            "edges": [],
        },
    )
    _write(source / "other.json", {"preserved": True})

    artifact = tmp_path / "artifacts" / "form.pdf"
    artifact.parent.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with artifact.open("wb") as handle:
        writer.write(handle)
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    queue = tmp_path / "queue.json"
    _write(
        queue,
        {
            "records": [
                {
                    "requirement_identity_id": "identity-1",
                    "research_action": "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION",
                    "technical_evidence": {
                        "sha256": sha,
                        "effectivity_reason_code": "CURRENT_AS_OF_DATE",
                        "source_page_url": "https://vbpl.vn/test",
                    },
                }
            ]
        },
    )
    verification = tmp_path / "verification.json"
    _write(
        verification,
        {
            "technical_pass": True,
            "failed_count": 0,
            "queue_sha256": hashlib.sha256(queue.read_bytes()).hexdigest(),
            "checks": [
                {
                    "requirement_identity_id": "identity-1",
                    "status": "PASS",
                }
            ],
        },
    )
    probe = tmp_path / "probe"
    group_id = "group-1"
    _write(
        probe / "code-groups.json",
        {
            "groups": [
                {
                    "group_id": group_id,
                    "form_code": "M01",
                    "issuing_instrument": "01/2026/TT-TEST",
                    "source_tier": "central",
                    "procedure_ids": ["procedure-1"],
                    "procedure_metadata": {},
                }
            ]
        },
    )
    _write(
        probe / "code-resolution.json",
        {
            "resolved_groups": [
                {
                    "group_id": group_id,
                    "artifact": {
                        "source_pages_zero_based": [0],
                        "source_package_sha256": sha,
                    },
                }
            ],
            "pending_records": [
                {
                    "id": "candidate-1",
                    "three_tier_group_id": group_id,
                    "proposed_canonical_form_id": "form-1",
                    "canonical_form_name": "Synthetic form",
                    "form_code": "M01",
                    "procedure_id": "procedure-1",
                    "domain": "fixture",
                    "administrative_level": "commune",
                    "jurisdiction": "Hai Phong",
                    "local_path": str(
                        artifact.relative_to(tmp_path)
                    ).replace("\\", "/"),
                    "sha256": sha,
                    "source_sha256": sha,
                    "source_page_url": "https://vbpl.vn/test",
                    "source_download_url": "https://vbpl.vn/test.pdf",
                    "publisher": "Official fixture publisher",
                    "legal_basis": ["01/2026/TT-TEST"],
                    "effective_from": "2026-01-01",
                    "effective_to": None,
                    "effective_status": "current",
                    "effectivity_reason_code": "CURRENT_AS_OF_DATE",
                    "provenance": {
                        "publisher": "Official fixture publisher",
                        "retrieved_at": "2026-07-30T00:00:00Z",
                    },
                    "review_status": "candidate_pending_review",
                    "preparation_status": "ready_for_human_review",
                    "technical_validation": {"status": "passed"},
                    "approved": False,
                    "runtime_eligible": False,
                    "source_pages_zero_based": [0],
                }
            ],
        },
    )
    candidate_store = tmp_path / "candidate-store.json"
    _write(candidate_store, {"summary": {}, "records": []})
    return {
        "source": source,
        "destination": tmp_path / "runs" / "destination-run",
        "queue": queue,
        "verification": verification,
        "probe": probe,
        "candidate_store": candidate_store,
        "backup": tmp_path / "backup",
        "status": tmp_path / "status.json",
        "latest": tmp_path / "latest.json",
        "report": tmp_path / "integration.json",
    }


def test_reconciles_verified_candidate_into_new_non_serving_campaign(tmp_path):
    paths = _fixture(tmp_path)

    report = reconcile_campaign(
        source_run_dir=paths["source"],
        destination_run_dir=paths["destination"],
        queue_path=paths["queue"],
        verification_path=paths["verification"],
        probe_dirs=[paths["probe"]],
        candidate_store_path=paths["candidate_store"],
        candidate_backup_dir=paths["backup"],
        campaign_status_path=paths["status"],
        latest_path=paths["latest"],
        report_output_path=paths["report"],
    )

    assert report["ready_for_human_attestation_identities"] == 1
    assert report["terminal_gap_identities"] == 0
    assert report["automated_approval"] is False
    batches = json.loads(
        (paths["destination"] / "review-batches.json").read_text()
    )
    assert batches["batches"][0]["identity_count"] == 1
    store = json.loads(paths["candidate_store"].read_text())
    assert store["records"][0]["approved"] is False
    assert store["records"][0]["runtime_eligible"] is False
    assert (paths["backup"] / "backup-manifest.json").is_file()


def test_reconciliation_rejects_stale_verification_before_mutation(tmp_path):
    paths = _fixture(tmp_path)
    verification = json.loads(paths["verification"].read_text())
    verification["queue_sha256"] = "0" * 64
    _write(paths["verification"], verification)

    try:
        reconcile_campaign(
            source_run_dir=paths["source"],
            destination_run_dir=paths["destination"],
            queue_path=paths["queue"],
            verification_path=paths["verification"],
            probe_dirs=[paths["probe"]],
            candidate_store_path=paths["candidate_store"],
            candidate_backup_dir=paths["backup"],
            campaign_status_path=paths["status"],
            latest_path=paths["latest"],
            report_output_path=paths["report"],
        )
    except ValueError as exc:
        assert str(exc) == "PROBE_VERIFICATION_NOT_CURRENT_OR_PASSING"
    else:
        raise AssertionError("stale verification must fail")

    assert not paths["destination"].exists()
    assert json.loads(paths["candidate_store"].read_text())["records"] == []
