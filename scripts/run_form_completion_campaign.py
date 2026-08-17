"""Run the fail-closed official-form completion campaign.

The runner discovers official procedure sources, enqueues and processes
candidate-only downloads, synchronizes verified files into the human review
queue, refreshes canonical reconciliation, and writes aggregate-only status.
It never creates a legal attestation or changes runtime serving eligibility.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_completion_campaign import (
    apply_candidate_technical_validation,
    build_form_completion_jobs,
    build_privacy_safe_campaign_report,
    merge_source_findings_preserving_stronger_evidence,
)
from api.form_review_sync import build_form_review_preview
from api.source_gap_jobs import (
    DEFAULT_CANDIDATE_DIR,
    DEFAULT_FORM_REVIEW_DIR,
    DEFAULT_FORM_REVIEW_QUEUE_PATH,
    DEFAULT_STORE_PATH,
    enqueue_source_gap_job,
    load_source_gap_jobs,
    run_due_source_gap_jobs,
    sync_downloaded_form_candidate_to_review_queue,
)
from scripts.discover_official_procedure_sources import discover
from scripts.reconcile_canonical_form_candidates import run as reconcile

FORMS_DIR = ROOT / "notebook_data" / "forms"
STATE_DIR = ROOT / "data" / "form_completion_campaign"
STATUS_PATH = STATE_DIR / "status_v1.json"
LOCK_PATH = STATE_DIR / "campaign.lock"
REPORT_DIR = ROOT / "reports" / "feature005"
CANONICAL_PATH = FORMS_DIR / "canonical_forms_catalog_v1.json"
FINDINGS_PATH = FORMS_DIR / "canonical_form_source_findings_v1.json"
PROCEDURE_SOURCES_PATH = FORMS_DIR / "canonical_procedure_sources_v1.json"
BINDINGS_PATH = FORMS_DIR / "procedure_form_bindings_v1.json"
EFFECTIVITY_PATH = FORMS_DIR / "official_form_effectivity_evidence_v1.json"
SHORTLIST_PATH = FORMS_DIR / "legal_attestation_shortlist_v1.json"


def _load(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"INVALID_JSON_OBJECT:{path.name}")
    return payload


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_status(
    *,
    run_id: str,
    legal_as_of: str,
    status: str,
    stage: str,
    counts: Mapping[str, int] | None = None,
    reason_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    payload = build_privacy_safe_campaign_report(
        run_id=run_id,
        legal_as_of=legal_as_of,
        status=status,
        counts=counts or {},
        reason_counts=reason_counts or {},
    )
    payload["stage"] = stage
    _write_atomic(STATUS_PATH, payload)
    return payload


def _backup(run_id: str) -> Path:
    target = REPORT_DIR / f"form-completion-backup-{run_id}"
    target.mkdir(parents=True, exist_ok=False)
    for path in (
        CANONICAL_PATH,
        FINDINGS_PATH,
        PROCEDURE_SOURCES_PATH,
        BINDINGS_PATH,
        EFFECTIVITY_PATH,
        SHORTLIST_PATH,
        DEFAULT_FORM_REVIEW_QUEUE_PATH,
        DEFAULT_STORE_PATH,
    ):
        if path.is_file():
            shutil.copy2(path, target / path.name)
    return target


def _enrich_synced_candidates(
    *,
    jobs: list[Mapping[str, Any]],
    forms_payload: Mapping[str, Any],
    findings_payload: Mapping[str, Any],
    effectivity_payload: Mapping[str, Any],
) -> dict[str, int]:
    queue = _load(DEFAULT_FORM_REVIEW_QUEUE_PATH, {"records": []})
    records = [
        dict(item)
        for item in queue.get("records") or []
        if isinstance(item, Mapping)
    ]
    jobs_by_id = {
        str(item.get("job_id") or ""): item
        for item in jobs
        if isinstance(item, Mapping)
    }
    forms = {
        str(item.get("form_id") or ""): item
        for item in forms_payload.get("forms") or []
        if isinstance(item, Mapping)
    }
    findings = {
        str(item.get("form_id") or ""): item
        for item in findings_payload.get("findings") or []
        if isinstance(item, Mapping)
    }
    rules = [
        item
        for item in effectivity_payload.get("rules") or []
        if isinstance(item, Mapping)
    ]
    rules.extend(
        item
        for item in effectivity_payload.get("form_effectivity") or []
        if isinstance(item, Mapping)
    )
    passed = 0
    failed = 0
    for index, record in enumerate(records):
        job = jobs_by_id.get(str(record.get("source_gap_job_id") or ""))
        if job is None:
            continue
        form_id = str(job.get("canonical_form_id") or job.get("case_id") or "")
        form = forms.get(form_id)
        if form is None:
            continue
        enriched = apply_candidate_technical_validation(
            record,
            form=form,
            finding=findings.get(form_id, {}),
            effectivity_rules=rules,
        )
        records[index] = enriched
        if (enriched.get("technical_validation") or {}).get("status") == "passed":
            passed += 1
        else:
            failed += 1
    queue["records"] = records
    queue["summary"] = {
        **dict(queue.get("summary") or {}),
        "technical_validation_passed": passed,
        "technical_validation_incomplete": failed,
        "auto_approved": 0,
    }
    _write_atomic(DEFAULT_FORM_REVIEW_QUEUE_PATH, queue)
    return {"technical_passed": passed, "technical_incomplete": failed}


def execute(*, legal_as_of: str, network: bool = True) -> dict[str, Any]:
    date.fromisoformat(legal_as_of)
    run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("FORM_COMPLETION_CAMPAIGN_ALREADY_RUNNING") from exc
    os.close(lock_fd)
    try:
        _write_status(
            run_id=run_id,
            legal_as_of=legal_as_of,
            status="running",
            stage="backup",
        )
        _backup(run_id)

        _write_status(
            run_id=run_id,
            legal_as_of=legal_as_of,
            status="running",
            stage="official_source_discovery",
        )
        previous_findings = _load(FINDINGS_PATH, {"findings": []})
        discover(network=network)
        refreshed_findings = _load(FINDINGS_PATH, {"findings": []})
        _write_atomic(
            FINDINGS_PATH,
            merge_source_findings_preserving_stronger_evidence(
                previous_findings,
                refreshed_findings,
            ),
        )

        forms_payload = _load(CANONICAL_PATH, {"forms": []})
        findings_payload = _load(FINDINGS_PATH, {"findings": []})
        procedure_sources_payload = _load(
            PROCEDURE_SOURCES_PATH, {"procedures": []}
        )
        plan = build_form_completion_jobs(
            canonical_forms_payload=forms_payload,
            form_source_findings_payload=findings_payload,
            procedure_sources_payload=procedure_sources_payload,
            legal_as_of=legal_as_of,
        )
        for job in plan["jobs"]:
            enqueue_source_gap_job(job, store_path=DEFAULT_STORE_PATH)

        _write_status(
            run_id=run_id,
            legal_as_of=legal_as_of,
            status="running",
            stage="candidate_download",
            counts={"queued_jobs": len(plan["jobs"])},
            reason_counts=plan["unresolved_reason_counts"],
        )
        run_due_source_gap_jobs(
            store_path=DEFAULT_STORE_PATH,
            candidate_dir=DEFAULT_CANDIDATE_DIR,
            checked_on=date.fromisoformat(legal_as_of),
        )
        stored_jobs = load_source_gap_jobs(DEFAULT_STORE_PATH)
        sync_counts: Counter[str] = Counter()
        for job in stored_jobs:
            if (
                job.get("gap_type") != "MISSING_FORM_SOURCE"
                or job.get("status") != "downloaded_candidate"
                or not isinstance(job.get("official_metadata"), Mapping)
            ):
                continue
            result = sync_downloaded_form_candidate_to_review_queue(
                job,
                project_root=ROOT,
                queue_path=DEFAULT_FORM_REVIEW_QUEUE_PATH,
                review_dir=DEFAULT_FORM_REVIEW_DIR,
            )
            sync_counts[str(result.get("action") or "unknown")] += 1

        technical = _enrich_synced_candidates(
            jobs=stored_jobs,
            forms_payload=forms_payload,
            findings_payload=findings_payload,
            effectivity_payload=_load(EFFECTIVITY_PATH, {"rules": []}),
        )

        _write_status(
            run_id=run_id,
            legal_as_of=legal_as_of,
            status="running",
            stage="canonical_reconciliation",
            counts={
                "queued_jobs": len(plan["jobs"]),
                "candidate_sync_created": sync_counts["created"],
                "candidate_sync_updated": sync_counts["updated"],
                **technical,
            },
            reason_counts=plan["unresolved_reason_counts"],
        )
        reconcile(apply=True, legal_as_of=legal_as_of)

        refreshed_forms = _load(CANONICAL_PATH, {"forms": []})
        preview = build_form_review_preview(
            candidate_payload=_load(
                DEFAULT_FORM_REVIEW_QUEUE_PATH, {"records": []}
            ),
            canonical_forms_payload=refreshed_forms,
            bindings_payload=_load(BINDINGS_PATH, {"bindings": []}),
            project_root=ROOT,
            legal_as_of=legal_as_of,
        )
        job_status_counts = Counter(
            str(item.get("status") or "unknown") for item in stored_jobs
        )
        counts = {
            "active_catalog_forms": int(
                preview["summary"].get("active_catalog_forms") or 0
            ),
            "already_approved": int(
                preview["summary"].get("already_approved_forms") or 0
            ),
            "ready_for_attestation": int(
                preview["summary"].get("eligible_forms") or 0
            ),
            "remaining_unresolved": int(
                preview["summary"].get("excluded_forms") or 0
            ),
            "excluded_no_official_form": int(
                preview["summary"].get("excluded_no_official_forms") or 0
            ),
            "downloaded_candidates": job_status_counts["downloaded_candidate"],
            "verified_data_gap": job_status_counts["verified_gap"],
            "blocked_external": job_status_counts["blocked_external"],
            **technical,
        }
        status = (
            "waiting_for_human_attestation"
            if counts["ready_for_attestation"] > 0
            else "completed_fail_closed"
        )
        report = _write_status(
            run_id=run_id,
            legal_as_of=legal_as_of,
            status=status,
            stage="complete",
            counts=counts,
            reason_counts={
                **plan["unresolved_reason_counts"],
                **preview.get("reason_counts", {}),
            },
        )
        _write_atomic(
            REPORT_DIR / f"form-completion-campaign-{run_id}.json",
            report,
        )
        return report
    except Exception:
        _write_status(
            run_id=run_id,
            legal_as_of=legal_as_of,
            status="failed_fail_closed",
            stage="failed",
        )
        raise
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legal-as-of",
        default=date.today().isoformat(),
    )
    parser.add_argument("--no-network", action="store_true")
    args = parser.parse_args()
    report = execute(
        legal_as_of=args.legal_as_of,
        network=not args.no_network,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
