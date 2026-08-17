#!/usr/bin/env python
"""Reconcile all canonical forms with official candidates, fail closed.

The command never creates a legal approval.  It writes deterministic mappings,
effectivity provenance, verified data gaps and a human attestation shortlist.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_candidate_reconciliation import reconcile_form_candidates
from api.form_review_sync import build_form_review_preview


FORMS_DIR = ROOT / "notebook_data" / "forms"
CANDIDATE_PATH = FORMS_DIR / "official_forms_candidates_classified.json"
CANONICAL_PATH = FORMS_DIR / "canonical_forms_catalog_v1.json"
BINDINGS_PATH = FORMS_DIR / "procedure_form_bindings_v1.json"
PROCEDURE_SOURCES_PATH = FORMS_DIR / "canonical_procedure_sources_v1.json"
FORM_SOURCE_FINDINGS_PATH = (
    FORMS_DIR / "canonical_form_source_findings_v1.json"
)
SOURCE_GAP_STORE_PATH = (
    ROOT / "data" / "source_gap_jobs" / "source_gap_jobs_v1.json"
)
EFFECTIVITY_PATH = FORMS_DIR / "official_form_effectivity_evidence_v1.json"
SHORTLIST_PATH = FORMS_DIR / "legal_attestation_shortlist_v1.json"
TARGET_MANIFEST_PATH = (
    FORMS_DIR / "canonical_form_source_resolution_targets_v1.json"
)
REPORT_PATH = (
    ROOT / "reports" / "feature005" / "form-reconciliation-20260727.json"
)
_RESOLUTION_EVIDENCE_STATUSES = {
    "BLOCKED_EXTERNAL",
    "EXCLUDED_NO_OFFICIAL_FORM",
    "SOURCE_DOWNLOAD_RETRY_REQUIRED",
    "SOURCE_MAPPING_UNRESOLVED",
    "VERIFIED_DATA_GAP",
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _serialize(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _write_atomic_pair(
    left_path: Path,
    left_payload: Mapping[str, Any],
    right_path: Path,
    right_payload: Mapping[str, Any],
) -> None:
    originals = {
        left_path: left_path.read_bytes(),
        right_path: right_path.read_bytes(),
    }
    temp_paths = {
        left_path: left_path.with_suffix(left_path.suffix + ".reconcile.tmp"),
        right_path: right_path.with_suffix(right_path.suffix + ".reconcile.tmp"),
    }
    try:
        for path, payload in (
            (left_path, left_payload),
            (right_path, right_payload),
        ):
            temp = temp_paths[path]
            temp.write_bytes(_serialize(payload))
            os.replace(temp, path)
    except Exception:
        for path, content in originals.items():
            path.write_bytes(content)
        raise
    finally:
        for temp in temp_paths.values():
            temp.unlink(missing_ok=True)


def _reason_counts(preview: Mapping[str, Any]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                reason
                for item in preview.get("excluded_items") or []
                for reason in item.get("reason_codes") or []
            ).items()
        )
    )


def _preview(
    *,
    candidates: Mapping[str, Any],
    forms: Mapping[str, Any],
    bindings: Mapping[str, Any],
    legal_as_of: str,
) -> dict[str, Any]:
    return build_form_review_preview(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        project_root=ROOT,
        legal_as_of=legal_as_of,
    )


def _preview_without_prior_resolution(
    *,
    candidates: Mapping[str, Any],
    forms: Mapping[str, Any],
    bindings: Mapping[str, Any],
    legal_as_of: str,
    target_form_ids: set[str] | None = None,
) -> dict[str, Any]:
    unresolved_view = deepcopy(dict(forms))
    for form in unresolved_view.get("forms") or []:
        if not isinstance(form, dict):
            continue
        if (
            target_form_ids is not None
            and str(form.get("form_id") or "") not in target_form_ids
        ):
            continue
        form["candidate_source_evidence"] = [
            item
            for item in form.get("candidate_source_evidence") or []
            if not (
                isinstance(item, Mapping)
                and str(item.get("status") or "")
                in _RESOLUTION_EVIDENCE_STATUSES
            )
        ]
    return _preview(
        candidates=candidates,
        forms=unresolved_view,
        bindings=bindings,
        legal_as_of=legal_as_of,
    )


def _with_source_gap_state(
    findings_payload: Mapping[str, Any],
    source_gap_payload: Mapping[str, Any],
) -> dict[str, Any]:
    enriched = deepcopy(dict(findings_payload))
    jobs_by_form = {
        str(item.get("case_id") or ""): item
        for item in source_gap_payload.get("jobs") or []
        if isinstance(item, Mapping)
        and str(item.get("gap_type") or "") == "MISSING_FORM_SOURCE"
    }
    for finding in enriched.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        job = jobs_by_form.get(str(finding.get("form_id") or ""))
        if not job:
            continue
        finding.update(
            {
                "source_gap_job_id": job.get("job_id"),
                "source_gap_status": job.get("status"),
                "source_gap_reason_code": job.get("reason_code"),
                "source_gap_checked_dates": list(
                    job.get("checked_dates") or []
                ),
            }
        )
    return enriched


def run(
    *,
    apply: bool,
    legal_as_of: str = "2026-07-27",
    target_baseline_catalog: Path | None = None,
) -> dict[str, Any]:
    candidates = _load(CANDIDATE_PATH)
    forms = _load(CANONICAL_PATH)
    bindings = _load(BINDINGS_PATH)
    procedure_sources = _load(PROCEDURE_SOURCES_PATH)
    form_source_findings = _load(FORM_SOURCE_FINDINGS_PATH)
    if SOURCE_GAP_STORE_PATH.is_file():
        form_source_findings = _with_source_gap_state(
            form_source_findings,
            _load(SOURCE_GAP_STORE_PATH),
        )
    effectivity = _load(EFFECTIVITY_PATH)

    frozen_target_ids: set[str] | None = None
    if TARGET_MANIFEST_PATH.is_file() and target_baseline_catalog is None:
        target_manifest = _load(TARGET_MANIFEST_PATH)
        frozen_target_ids = {
            str(value).strip()
            for value in target_manifest.get("form_ids") or []
            if str(value or "").strip()
        }
    baseline_forms = (
        _load(target_baseline_catalog)
        if target_baseline_catalog is not None
        else forms
    )
    baseline = _preview_without_prior_resolution(
        candidates=candidates,
        forms=baseline_forms,
        bindings=bindings,
        legal_as_of=legal_as_of,
        target_form_ids=frozen_target_ids,
    )
    mapping_stage = reconcile_form_candidates(
        canonical_forms_payload=forms,
        candidate_payload=candidates,
        procedure_sources_payload=procedure_sources,
        effectivity_evidence_payload={"rules": []},
        form_source_findings_payload=form_source_findings,
        project_root=ROOT,
        legal_as_of=legal_as_of,
    )
    mapping_preview = _preview(
        candidates=mapping_stage["candidate_payload"],
        forms=mapping_stage["canonical_forms_payload"],
        bindings=bindings,
        legal_as_of=legal_as_of,
    )
    final_stage = reconcile_form_candidates(
        canonical_forms_payload=mapping_stage["canonical_forms_payload"],
        candidate_payload=mapping_stage["candidate_payload"],
        procedure_sources_payload=procedure_sources,
        effectivity_evidence_payload=effectivity,
        form_source_findings_payload=form_source_findings,
        project_root=ROOT,
        legal_as_of=legal_as_of,
    )
    final_preview = _preview(
        candidates=final_stage["candidate_payload"],
        forms=final_stage["canonical_forms_payload"],
        bindings=bindings,
        legal_as_of=legal_as_of,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    outcomes = final_stage["report"]["outcomes"]
    if frozen_target_ids is not None:
        baseline_unmapped_ids = frozen_target_ids
    else:
        baseline_unmapped_ids = {
            str(item.get("canonical_form_id") or "")
            for item in baseline.get("excluded_items") or []
            if "CANDIDATE_NOT_FOUND" in (item.get("reason_codes") or [])
        }
    baseline_unmapped = len(baseline_unmapped_ids)
    target_outcomes = [
        item
        for item in outcomes
        if str(item.get("form_id") or "") in baseline_unmapped_ids
    ]
    target_status_counts = Counter(
        str(item.get("status") or "UNKNOWN") for item in target_outcomes
    )
    target_reason_counts = Counter(
        str(item.get("reason_code") or "UNKNOWN") for item in target_outcomes
    )
    final_forms_by_id = {
        str(item.get("form_id") or ""): item
        for item in final_stage["canonical_forms_payload"].get("forms") or []
        if isinstance(item, Mapping)
    }
    target_resolution_items: list[dict[str, Any]] = []
    for outcome in target_outcomes:
        form_id = str(outcome.get("form_id") or "")
        form = final_forms_by_id.get(form_id, {})
        resolution = next(
            (
                item
                for item in reversed(
                    form.get("candidate_source_evidence") or []
                )
                if isinstance(item, Mapping)
                and str(item.get("status") or "")
                == str(outcome.get("status") or "")
            ),
            {},
        )
        target_resolution_items.append(
            {
                "form_id": form_id,
                "procedure_id": outcome.get("procedure_id"),
                "canonical_name": form.get("canonical_name"),
                "status": outcome.get("status"),
                "reason_code": outcome.get("reason_code"),
                "official_sources_checked": list(
                    resolution.get("official_sources_checked") or []
                ),
                "source_gap_job_id": resolution.get("source_gap_job_id"),
                "source_gap_status": resolution.get("source_gap_status"),
                "source_gap_checked_dates": list(
                    resolution.get("source_gap_checked_dates") or []
                ),
                "auto_approved": False,
            }
        )
    outcome_counts = Counter(
        str(item.get("reason_code") or "UNKNOWN") for item in outcomes
    )
    shortlist = {
        "schema_version": "legal-attestation-shortlist-v1",
        "generated_at": generated_at,
        "legal_as_of": legal_as_of,
        "requires_human_confirmation": True,
        "auto_approved": False,
        "eligible_count": len(final_preview["eligible_items"]),
        "items": final_preview["eligible_items"],
        "verified_data_gaps": [
            {
                "form_id": item.get("form_id"),
                "procedure_id": item.get("procedure_id"),
                "reason_code": item.get("reason_code"),
            }
            for item in outcomes
            if item.get("status") == "VERIFIED_DATA_GAP"
        ],
        "excluded_no_official_forms": [
            {
                "form_id": item.get("form_id"),
                "procedure_id": item.get("procedure_id"),
                "reason_code": item.get("reason_code"),
            }
            for item in outcomes
            if item.get("status") == "EXCLUDED_NO_OFFICIAL_FORM"
        ],
        "unresolved_source_mappings": [
            {
                "form_id": item.get("form_id"),
                "procedure_id": item.get("procedure_id"),
                "reason_code": item.get("reason_code"),
            }
            for item in outcomes
            if item.get("status") == "SOURCE_MAPPING_UNRESOLVED"
        ],
        "blocked_external": [
            {
                "form_id": item.get("form_id"),
                "procedure_id": item.get("procedure_id"),
                "reason_code": item.get("reason_code"),
            }
            for item in outcomes
            if item.get("status") == "BLOCKED_EXTERNAL"
        ],
        "retryable_source_downloads": [
            {
                "form_id": item.get("form_id"),
                "procedure_id": item.get("procedure_id"),
                "reason_code": item.get("reason_code"),
            }
            for item in outcomes
            if item.get("status") == "SOURCE_DOWNLOAD_RETRY_REQUIRED"
        ],
    }
    report = {
        "schema_version": "canonical-form-reconciliation-report-v1",
        "generated_at": generated_at,
        "legal_as_of": legal_as_of,
        "status": (
            "READY_FOR_HUMAN_ATTESTATION"
            if final_preview["summary"]["eligible_forms"] > 0
            else "BLOCKED_LEGAL_REVIEW_DATA"
        ),
        "applied": apply,
        "baseline_unmapped_forms": baseline_unmapped,
        "attempted_unmapped_forms": baseline_unmapped,
        "target_resolution": {
            "target_forms": baseline_unmapped,
            "classified_forms": len(target_outcomes),
            "generic_candidate_not_found_after": int(
                final_preview.get("reason_counts", {}).get(
                    "CANDIDATE_NOT_FOUND",
                    0,
                )
            ),
            "status_counts": dict(sorted(target_status_counts.items())),
            "reason_counts": dict(sorted(target_reason_counts.items())),
            "excluded_no_official_forms": int(
                target_status_counts.get("EXCLUDED_NO_OFFICIAL_FORM", 0)
            ),
            "items": target_resolution_items,
        },
        "summary": final_stage["report"]["summary"],
        "outcome_reason_counts": dict(sorted(outcome_counts.items())),
        "preview_stages": {
            "baseline": {
                "summary": baseline["summary"],
                "reason_counts": _reason_counts(baseline),
            },
            "after_exact_mapping": {
                "summary": mapping_preview["summary"],
                "reason_counts": _reason_counts(mapping_preview),
            },
            "after_effectivity_provenance": {
                "summary": final_preview["summary"],
                "reason_counts": _reason_counts(final_preview),
            },
        },
        "human_attestation_required": True,
        "auto_approved": 0,
        "runtime_promoted": 0,
        "corpus_modified": False,
        "embedding_run": False,
        "active_collection_changed": False,
        "feature_flag_required_value": False,
    }

    if apply:
        _write_atomic_pair(
            CANDIDATE_PATH,
            final_stage["candidate_payload"],
            CANONICAL_PATH,
            final_stage["canonical_forms_payload"],
        )
        SHORTLIST_PATH.write_bytes(_serialize(shortlist))
        if not TARGET_MANIFEST_PATH.is_file() or target_baseline_catalog is not None:
            target_manifest = {
                "schema_version": "canonical-form-source-resolution-targets-v1",
                "legal_as_of": legal_as_of,
                "target_reason_code": "CANDIDATE_NOT_FOUND",
                "form_count": len(baseline_unmapped_ids),
                "form_ids": sorted(baseline_unmapped_ids),
                "immutable_scope": True,
            }
            target_temp = TARGET_MANIFEST_PATH.with_suffix(
                TARGET_MANIFEST_PATH.suffix + ".tmp"
            )
            target_temp.write_bytes(_serialize(target_manifest))
            os.replace(target_temp, TARGET_MANIFEST_PATH)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_bytes(_serialize(report))
    return {
        "report": report,
        "shortlist": shortlist,
        "candidate_payload": final_stage["candidate_payload"],
        "canonical_forms_payload": final_stage["canonical_forms_payload"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write candidate mappings and provenance; never approves forms.",
    )
    parser.add_argument("--legal-as-of", default="2026-07-27")
    parser.add_argument(
        "--target-baseline-catalog",
        type=Path,
        help=(
            "One-time pre-reconciliation canonical catalog used to freeze "
            "the target form IDs."
        ),
    )
    args = parser.parse_args()
    target_baseline = None
    if args.target_baseline_catalog is not None:
        target_baseline = args.target_baseline_catalog.resolve()
        try:
            target_baseline.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise SystemExit("target baseline must stay inside the project") from exc
    result = run(
        apply=args.apply,
        legal_as_of=args.legal_as_of,
        target_baseline_catalog=target_baseline,
    )
    print(json.dumps(result["report"], ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
