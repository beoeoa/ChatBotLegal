"""Run the Feature 017 workflow against an explicitly isolated PostgreSQL DB.

The schema must already be created by ``manage_feature017_form_schema.py up``.
This script refuses protected/live-looking database names and never activates a
public runtime flag.  It is intended for repeatable migration rehearsal only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_models import (
    ActorContext,
    FormReviewSubmission,
    FormWorkflowStatus,
)
from api.form_governance_repository import PostgresFormGovernanceRepository
from api.form_governance_service import FormGovernanceService
from api.form_procedure_scope import fixed_procedure_scope_lookup
from scripts.manage_feature017_form_schema import assert_isolated_database


def _metadata(checksum: str) -> dict[str, object]:
    return {
        "procedure": {
            "procedure_id": "feature017-rehearsal-procedure",
            "name": "Thủ tục diễn tập quản trị biểu mẫu",
            "domain": "cu_tru_an_ninh",
            "authority": "UBND cấp xã",
            "official_source_url": "https://dichvucong.gov.vn/feature017-rehearsal",
            "legal_as_of": "2026-08-11",
            "coverage_status": "released",
        },
        "asset": {
            "form_id": "feature017-rehearsal-form",
            "canonical_name": "Biểu mẫu diễn tập",
            "asset_kind": "file",
            "source_url": "https://vbpl.vn/feature017-rehearsal.pdf",
            "source_checksum": checksum,
            "audiences": ["citizen"],
            "coverage_status": "released",
        },
        "bindings": [
            {
                "procedure_id": "feature017-rehearsal-procedure",
                "form_id": "feature017-rehearsal-form",
                "requirement": "required",
                "audience": "citizen",
                "coverage_status": "released",
            }
        ],
        "aliases": ["làm thủ tục diễn tập biểu mẫu"],
    }


def rehearse() -> dict[str, object]:
    database_url = assert_isolated_database(confirmed=True)
    repository = PostgresFormGovernanceRepository(database_url)
    service = FormGovernanceService(
        repository,
        procedure_scope_lookup=fixed_procedure_scope_lookup({
            "feature017-rehearsal-procedure": "cu_tru_an_ninh",
        }),
    )
    officer = ActorContext(
        user_id="feature017-rehearsal-officer",
        role="officer",
        domains=["cu_tru_an_ninh"],
    )
    admin = ActorContext(user_id="feature017-rehearsal-admin", role="admin")
    checksum = "a" * 64

    case = service.submit(
        officer,
        FormReviewSubmission(
            procedure_id="feature017-rehearsal-procedure",
            domain="cu_tru_an_ninh",
            title="Biểu mẫu diễn tập",
            source_url="https://vbpl.vn/feature017-rehearsal.pdf",
            source_checksum=checksum,
        ),
    )
    case = service.transition(admin, case.case_id, FormWorkflowStatus.SOURCE_APPROVED)
    case = service.enrich(admin, case.case_id, _metadata(checksum))
    case = service.ready_for_attestation(admin, case.case_id)
    preview = service.attestation_preview(admin, case.case_id)
    case = service.attest(admin, case.case_id, preview["fingerprint"])

    gap = service.verify_gap(
        admin,
        {
            "target_type": "procedure",
            "target_id": "feature017-rehearsal-no-form",
            "reason_code": "NO_OFFICIAL_FORM_LISTED",
            "evidence_source_url": "https://dichvucong.gov.vn/feature017-no-form",
            "evidence_sha256": "b" * 64,
            "legal_as_of": "2026-08-11",
            "procedure_ids": ["feature017-rehearsal-no-form"],
            "domain": "cu_tru_an_ninh",
            "procedure_name": "Thủ tục diễn tập không có biểu mẫu chính thức",
            "procedure_source_url": "https://dichvucong.gov.vn/feature017-no-form",
            "aliases": ["thủ tục diễn tập không có mẫu"],
        },
    )
    release = service.build_release(
        admin,
        [case.case_id],
        legal_as_of=date(2026, 8, 11),
    )
    release = service.validate_release(admin, release["release_id"])
    pointer = service.activate_release(admin, release["release_id"])

    table_names = set(inspect(repository.engine).get_table_names())
    expected_tables = {
        "legal_procedure",
        "legal_form_asset",
        "procedure_form_binding",
        "procedure_question_alias",
    }
    if not expected_tables.issubset(table_names):
        raise RuntimeError("feature017_rehearsal_projection_tables_missing")

    with repository.engine.connect() as connection:
        counts = {
            table: int(connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
            for table in (
                "legal_procedure",
                "legal_form_asset",
                "procedure_form_binding",
                "procedure_question_alias",
                "form_review_case",
                "form_release",
                "form_source_gap",
                "form_active_release",
                "form_workflow_event",
                "form_notification_outbox",
            )
        }

    audit_immutable = False
    try:
        with repository.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE form_workflow_event "
                    "SET detail_hash = :hash WHERE object_id = :object_id"
                ),
                {"hash": "f" * 64, "object_id": case.case_id},
            )
    except Exception:
        audit_immutable = True

    result = {
        "status": "passed",
        "database": "isolated_rehearsal",
        "live_apply": False,
        "case_status": service.get_case(admin, case.case_id).status.value,
        "gap_status": gap["status"],
        "release_status": release["status"],
        "active_release_matches": pointer["release_id"] == release["release_id"],
        "audit_immutable": audit_immutable,
        "counts": counts,
    }
    if result["case_status"] != "released" or not all(
        (
            result["gap_status"] == "verified_gap",
            result["release_status"] == "validated",
            result["active_release_matches"],
            result["audit_immutable"],
            counts["legal_procedure"] == 2,
            counts["legal_form_asset"] == 1,
            counts["procedure_form_binding"] == 1,
            counts["procedure_question_alias"] == 2,
            counts["form_active_release"] == 1,
        )
    ):
        raise RuntimeError(f"feature017_rehearsal_failed:{result}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_isolated:
        raise RuntimeError("isolated_database_confirmation_required")
    result = rehearse()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
