"""Apply the approved official current-validity correction for two laws.

VBPL's search projection reported ``expired_partial`` without provision scope,
while the official instrument attribute/full-text pages identify both source
laws as currently effective.  This command records a newer, exact manual
official-source observation after the one amended Teacher-Law provision has
been reconciled in PostgreSQL and Chroma.  It does not edit or delete corpus
history and it keeps the earlier observations/events for audit.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_validity_models import (
    EvidenceStatus,
    IdentityStatus,
    LegalValidityObservation,
    NormalizedValidityStatus,
)
from api.legal_validity_registry import LegalValidityRegistry, SNAPSHOT_PATH
from api.user_service import write_audit_log
from open_notebook.database.repository import repo_query
from scripts.backup_legal_retrieval import _database_url
from scripts.repair_golden294_teacher_article4 import NEW_TEXT


APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"
LEGAL_AS_OF = "2026-08-11"
OFFICIAL_EVIDENCE: dict[str, dict[str, Any]] = {
    "31/2024/QH15": {
        "document_id": "127345",
        "issuing_agency": "Quốc hội",
        "issued_date": "2024-01-18",
        "effective_from": "2025-01-01",
        "source_url": (
            "https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=177815"
        ),
        "official_status": "Còn hiệu lực",
        "review_scope": "Golden cases cite Articles 1-30",
        "reviewed_amendment_sources": [
            "https://vbpl.vn/TW/pages/vbpq-toanvan.aspx?ItemID=170509",
            "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=186269",
            "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=186271",
        ],
    },
    "73/2025/QH15": {
        "document_id": "121955",
        "issuing_agency": "Quốc hội",
        "issued_date": "2025-06-16",
        "effective_from": "2026-01-01",
        "source_url": (
            "https://vbpl.vn/caobang/Pages/vbpq-toanvan.aspx?ItemID=179262"
        ),
        "official_status": "Còn hiệu lực",
        "review_scope": "Article 4 clause 1 reconciled before activation",
        "reviewed_amendment_sources": [
            "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=187742"
        ],
    },
}


def validate_official_evidence(evidence: Mapping[str, Mapping[str, Any]]) -> None:
    if set(evidence) != {"31/2024/QH15", "73/2025/QH15"}:
        raise ValueError("GOLDEN294_VALIDITY_EVIDENCE_TARGET_SET_MISMATCH")
    for law_number, item in evidence.items():
        if item.get("official_status") != "Còn hiệu lực":
            raise ValueError(f"GOLDEN294_VALIDITY_NOT_CURRENT:{law_number}")
        source_url = str(item.get("source_url") or "")
        if not source_url.startswith("https://vbpl.vn/"):
            raise ValueError(f"GOLDEN294_VALIDITY_SOURCE_NOT_OFFICIAL:{law_number}")
        amendment_sources = list(item.get("reviewed_amendment_sources") or [])
        if not amendment_sources or any(
            not str(url).startswith("https://vbpl.vn/")
            for url in amendment_sources
        ):
            raise ValueError(
                f"GOLDEN294_VALIDITY_AMENDMENT_EVIDENCE_MISSING:{law_number}"
            )


def validate_postgres_current_sources() -> dict[str, Any]:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT d.id, d.law_number, d.status, d.expired_date,
                       s.included, s.reason AS scope_reason, s.domain,
                       count(DISTINCT a.id) AS article_count,
                       count(c.id) AS chunk_count
                FROM legal_documents d
                JOIN legal_search_scope s ON s.document_id = d.id
                JOIN legal_articles a ON a.document_id = d.id
                JOIN legal_article_chunks c ON c.article_id = a.id
                WHERE d.id = ANY(:ids)
                GROUP BY d.id, s.included, s.reason, s.domain
                ORDER BY d.id
                """
            ),
            {"ids": [127345, 121955]},
        ).mappings().all()
        teacher_article = connection.execute(
            text(
                """
                SELECT a.content AS article_content, c.content AS chunk_content
                FROM legal_articles a
                JOIN legal_article_chunks c ON c.article_id = a.id
                WHERE a.id = 877617 AND c.id = 701396
                """
            )
        ).mappings().one()
    engine.dispose()
    by_law = {str(row["law_number"]): dict(row) for row in rows}
    if set(by_law) != set(OFFICIAL_EVIDENCE):
        raise RuntimeError("GOLDEN294_VALIDITY_POSTGRES_TARGET_SET_MISMATCH")
    for law_number, row in by_law.items():
        expected_id = int(OFFICIAL_EVIDENCE[law_number]["document_id"])
        if int(row["id"]) != expected_id or row["status"] != "active":
            raise RuntimeError(f"GOLDEN294_VALIDITY_POSTGRES_IDENTITY:{law_number}")
        if row["expired_date"] is not None:
            raise RuntimeError(f"GOLDEN294_VALIDITY_POSTGRES_NOT_CURRENT:{law_number}")
        # Legal validity and commune-core eligibility are independent gates.
        # The Teacher Law is intentionally retained in the source-wide tier
        # for Golden/evidence retrieval even though it is not a commune-core
        # document.  Never broaden its serving scope as a side effect of a
        # validity correction.
        if law_number == "31/2024/QH15" and not bool(row["included"]):
            raise RuntimeError(f"GOLDEN294_VALIDITY_CORE_SCOPE_MISSING:{law_number}")
        if law_number == "73/2025/QH15" and (
            bool(row["included"])
            or str(row.get("scope_reason") or "") != "out_of_commune_scope"
        ):
            raise RuntimeError(f"GOLDEN294_VALIDITY_SOURCE_SCOPE_CHANGED:{law_number}")
        if int(row["article_count"] or 0) <= 0 or int(row["chunk_count"] or 0) <= 0:
            raise RuntimeError(f"GOLDEN294_VALIDITY_POSTGRES_CONTENT_MISSING:{law_number}")
    if NEW_TEXT not in str(teacher_article["article_content"]):
        raise RuntimeError("GOLDEN294_TEACHER_ARTICLE4_NOT_RECONCILED")
    if NEW_TEXT not in str(teacher_article["chunk_content"]):
        raise RuntimeError("GOLDEN294_TEACHER_CHUNK_NOT_RECONCILED")
    return {
        "documents": by_law,
        "teacher_article4_reconciled": True,
    }


def observation_for(
    law_number: str, *, observed_at: datetime
) -> LegalValidityObservation:
    item = OFFICIAL_EVIDENCE[law_number]
    return LegalValidityObservation(
        document_id=str(item["document_id"]),
        law_number=law_number,
        issuing_agency=str(item["issuing_agency"]),
        issued_date=str(item["issued_date"]),
        source_url=str(item["source_url"]),
        source_kind="vbpl_manual_official_review",
        raw_status=str(item["official_status"]),
        normalized_status=NormalizedValidityStatus.ACTIVE,
        effective_from=str(item["effective_from"]),
        effective_to=None,
        affecting_document_number=None,
        affected_provisions=(),
        identity_status=IdentityStatus.EXACT,
        evidence_status=EvidenceStatus.SUFFICIENT,
        observed_at=observed_at,
        source_updated_at=observed_at,
    )


async def apply_current_validity(output_path: Path) -> dict[str, Any]:
    validate_official_evidence(OFFICIAL_EVIDENCE)
    postgres = validate_postgres_current_sources()
    snapshot_before = json.loads(Path(SNAPSHOT_PATH).read_text(encoding="utf-8"))
    registry = LegalValidityRegistry()
    admin_rows = await repo_query(
        "SELECT id FROM user_account WHERE username = 'admin' AND is_active = true LIMIT 1;"
    )
    if len(admin_rows) != 1:
        raise RuntimeError("GOLDEN294_ACTIVE_ADMIN_IDENTITY_MISSING")
    actor_user_id = str(admin_rows[0]["id"])
    observed_at = datetime.now(timezone.utc)
    recorded: dict[str, Any] = {}
    decisions: dict[str, Any] = {}
    for law_number in OFFICIAL_EVIDENCE:
        result = await registry.record_observation(
            observation_for(law_number, observed_at=observed_at),
            scope="current_answer",
            document_title=(
                "Luật Đất đai 2024"
                if law_number == "31/2024/QH15"
                else "Luật Nhà giáo"
            ),
        )
        recorded[law_number] = {
            "created": bool(result.get("created")),
            "observation_id": str((result.get("observation") or {}).get("id") or ""),
            "event_id": str((result.get("event") or {}).get("id") or "") or None,
        }
        event_id = recorded[law_number]["event_id"]
        if event_id:
            decisions[law_number] = await registry.record_decision(
                event_id=event_id,
                action="confirm_mapping",
                reason=(
                    "Người dùng đã duyệt Phase 9; đã đối chiếu trang "
                    "thuộc tính/toàn văn VBPL và nguồn sửa đổi chính thức."
                ),
                actor_user_id=actor_user_id,
            )
    snapshot_after = await registry.refresh_snapshot_projection()
    if not snapshot_after:
        raise RuntimeError("GOLDEN294_VALIDITY_SNAPSHOT_REFRESH_FAILED")
    for law_number in OFFICIAL_EVIDENCE:
        entry = (snapshot_after.get("documents") or {}).get(law_number) or {}
        if entry.get("normalized_status") != "active":
            raise RuntimeError(f"GOLDEN294_VALIDITY_SNAPSHOT_NOT_ACTIVE:{law_number}")
        if entry.get("serving_action") != "allow":
            raise RuntimeError(f"GOLDEN294_VALIDITY_SNAPSHOT_NOT_ALLOWED:{law_number}")
        if entry.get("identity_status") != "exact" or entry.get("evidence_status") != "sufficient":
            raise RuntimeError(f"GOLDEN294_VALIDITY_SNAPSHOT_EVIDENCE:{law_number}")
    await write_audit_log(
        action="legal.validity.golden294_current_correction",
        entity_type="legal_validity_snapshot",
        entity_id="31-2024-QH15+73-2025-QH15",
        actor_user_id=actor_user_id,
        actor_role="admin",
        details={
            "legal_as_of": LEGAL_AS_OF,
            "laws": sorted(OFFICIAL_EVIDENCE),
            "official_sources": {
                law: item["source_url"] for law, item in OFFICIAL_EVIDENCE.items()
            },
            "teacher_article4_reconciled": True,
            "hard_delete": False,
        },
    )
    report = {
        "schema_version": "golden-294-current-validity-v1",
        "applied_at": observed_at.isoformat(),
        "approval_phrase_verified": True,
        "legal_as_of": LEGAL_AS_OF,
        "official_evidence": OFFICIAL_EVIDENCE,
        "postgres": postgres,
        "surreal": {"recorded": recorded, "decisions": decisions},
        "snapshot": {
            "before": {
                law: (snapshot_before.get("documents") or {}).get(law)
                for law in OFFICIAL_EVIDENCE
            },
            "after": {
                law: (snapshot_after.get("documents") or {}).get(law)
                for law in OFFICIAL_EVIDENCE
            },
        },
        "preservation": {
            "postgres_corpus_mutated_by_this_command": False,
            "vectors_mutated_by_this_command": False,
            "prior_observations_deleted": False,
            "hard_delete": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.approval != APPROVAL_PHRASE:
        raise ValueError("GOLDEN294_LIVE_APPROVAL_PHRASE_REQUIRED")
    report = asyncio.run(apply_current_validity(args.output.resolve()))
    print(
        json.dumps(
            {"laws": sorted(OFFICIAL_EVIDENCE), "status": "active_confirmed"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
