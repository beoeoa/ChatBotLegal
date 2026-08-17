"""Synchronize two superseded residence circulars without deleting history.

This is a deliberately bounded Phase-9 operator command.  It verifies the
official VBPL observations again, locks exactly two known PostgreSQL rows, and
only changes ``expired_date`` plus current-serving scope.  Articles, chunks and
vectors are never rewritten or deleted.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_validity_source import VBPLValiditySource
from api.legal_validity_registry import SNAPSHOT_PATH
from scripts.backup_legal_retrieval import _database_url


APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"
EXPIRED_ON = date(2026, 7, 1)
TARGETS: dict[str, dict[str, Any]] = {
    "55/2021/TT-BCA": {
        "document_id": 103124,
        "issued_date": "2021-05-15",
        "source_url": "https://vbpl.vn/van-ban/chi-tiet/thong-tu-so-55-2021-tt-bca-quy-dinh-chi-tiet-mot-so-dieu-va-bien-phap-thi-hanh-luat-cu-tru--148326",
    },
    "66/2023/TT-BCA": {
        "document_id": 111170,
        "issued_date": "2023-11-17",
        "source_url": "https://vbpl.vn/van-ban/chi-tiet/thong-tu-so-66-2023-tt-bca-sua-doi-bo-sung-mot-so-dieu-cua-thong-tu-so-55-2021-tt-bca-ngay-15-thang-5-nam-2021-cua-bo-truong-bo-cong-an-quy-dinh-chi-tiet-mot-so-dieu-va-bien-phap-t--163388",
    },
}


def validate_observation(law_number: str, observation: Mapping[str, Any]) -> None:
    expected = TARGETS[law_number]
    checks = {
        "law_number": observation.get("law_number") == law_number,
        "identity": observation.get("identity_status") == "exact",
        "evidence": observation.get("evidence_status") == "sufficient",
        "status": observation.get("normalized_status") == "expired",
        "effective_to": observation.get("effective_to") == EXPIRED_ON.isoformat(),
        "source_url": observation.get("source_url") == expected["source_url"],
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(f"OFFICIAL_EXPIRY_EVIDENCE_REJECTED:{law_number}:{','.join(failed)}")


def recent_snapshot_observation(law_number: str) -> dict[str, Any]:
    """Use only a recent, exact official-source observation during VBPL outage."""

    payload = json.loads(Path(SNAPSHOT_PATH).read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
    if age.days > 7 or age.total_seconds() < 0:
        raise RuntimeError("OFFICIAL_VALIDITY_SNAPSHOT_STALE")
    observation = dict((payload.get("documents") or {}).get(law_number) or {})
    if not observation:
        raise RuntimeError(f"OFFICIAL_VALIDITY_SNAPSHOT_MISSING:{law_number}")
    observation.setdefault("law_number", law_number)
    validate_observation(law_number, observation)
    observation["verification_transport"] = "recent_official_snapshot_fallback"
    observation["snapshot_generated_at"] = payload["generated_at"]
    return observation


async def fetch_observations() -> dict[str, dict[str, Any]]:
    source = VBPLValiditySource(timeout_seconds=30)
    result: dict[str, dict[str, Any]] = {}
    for law_number, expected in TARGETS.items():
        fetched = None
        for attempt in range(1, 4):
            fetched = await source.fetch(
                instrument=law_number,
                document_id=str(expected["document_id"]),
                as_of=date(2026, 8, 11),
                expected_issuing_agency="Bộ Công an",
                expected_issued_date=expected["issued_date"],
            )
            if fetched.observation is not None:
                break
            if attempt < 3:
                await asyncio.sleep(attempt * 2)
        assert fetched is not None
        if fetched.observation is None:
            if fetched.reason_code != "OFFICIAL_SOURCE_SERVER_ERROR":
                raise RuntimeError(
                    f"OFFICIAL_EXPIRY_FETCH_FAILED:{law_number}:{fetched.reason_code}"
                )
            observation = recent_snapshot_observation(law_number)
        else:
            observation = fetched.observation.to_dict()
            observation["verification_transport"] = "live_official_source"
        validate_observation(law_number, observation)
        result[law_number] = observation
    return result


def _rows(connection: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT d.id, d.law_number, d.status, d.expired_date,
                       d.source_url, s.included, s.reason AS scope_reason,
                       s.domain, s.evaluated_as_of, s.evaluated_at,
                       count(DISTINCT a.id) AS article_count,
                       count(ch.id) AS chunk_count
                FROM legal_documents d
                JOIN legal_search_scope s ON s.document_id = d.id
                LEFT JOIN legal_articles a ON a.document_id = d.id
                LEFT JOIN legal_article_chunks ch ON ch.article_id = a.id
                WHERE d.id = ANY(:ids)
                GROUP BY d.id, s.included, s.reason, s.domain,
                         s.evaluated_as_of, s.evaluated_at
                ORDER BY d.id
                """
            ),
            {"ids": [value["document_id"] for value in TARGETS.values()]},
        ).mappings()
    ]


def validate_database_rows(rows: list[Mapping[str, Any]]) -> None:
    by_law = {str(row.get("law_number")): row for row in rows}
    if set(by_law) != set(TARGETS):
        raise RuntimeError("RESIDENCE_EXPIRY_TARGET_SET_MISMATCH")
    for law_number, expected in TARGETS.items():
        row = by_law[law_number]
        if int(row.get("id") or 0) != expected["document_id"]:
            raise RuntimeError(f"RESIDENCE_EXPIRY_DOCUMENT_ID_MISMATCH:{law_number}")
        if str(row.get("status") or "") != "active":
            raise RuntimeError(f"RESIDENCE_EXPIRY_STORED_STATUS_MISMATCH:{law_number}")
        current_expiry = row.get("expired_date")
        if current_expiry not in (None, EXPIRED_ON, EXPIRED_ON.isoformat()):
            raise RuntimeError(f"RESIDENCE_EXPIRY_DATE_CONFLICT:{law_number}")
        if int(row.get("article_count") or 0) <= 0 or int(row.get("chunk_count") or 0) <= 0:
            raise RuntimeError(f"RESIDENCE_EXPIRY_HISTORY_MISSING:{law_number}")


def apply_sync(observations: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    engine = create_engine(_database_url())
    applied_at = datetime.now(timezone.utc)
    with engine.begin() as connection:
        # Lock both metadata and scope rows before validating the bounded target
        # set.  The aggregate snapshot below cannot itself use FOR UPDATE.
        connection.execute(
            text("SELECT id FROM legal_documents WHERE id = ANY(:ids) FOR UPDATE"),
            {"ids": [value["document_id"] for value in TARGETS.values()]},
        ).all()
        connection.execute(
            text(
                "SELECT document_id FROM legal_search_scope "
                "WHERE document_id = ANY(:ids) FOR UPDATE"
            ),
            {"ids": [value["document_id"] for value in TARGETS.values()]},
        ).all()
        before = _rows(connection)
        validate_database_rows(before)
        connection.execute(
            text(
                """
                UPDATE legal_documents
                SET expired_date = :expired_date
                WHERE id = ANY(:ids)
                """
            ),
            {
                "expired_date": EXPIRED_ON,
                "ids": [value["document_id"] for value in TARGETS.values()],
            },
        )
        connection.execute(
            text(
                """
                UPDATE legal_search_scope
                SET included = FALSE,
                    reason = 'expired_official_2026-07-01',
                    evaluated_as_of = :as_of,
                    evaluated_at = :evaluated_at
                WHERE document_id = ANY(:ids)
                """
            ),
            {
                "as_of": date(2026, 8, 11),
                "evaluated_at": applied_at.replace(tzinfo=None),
                "ids": [value["document_id"] for value in TARGETS.values()],
            },
        )
        after = _rows(connection)
        validate_database_rows(after)
        for row in after:
            if row["expired_date"] != EXPIRED_ON or row["included"] is not False:
                raise RuntimeError(f"RESIDENCE_EXPIRY_POSTCONDITION_FAILED:{row['law_number']}")
            if row["scope_reason"] != "expired_official_2026-07-01":
                raise RuntimeError(f"RESIDENCE_EXPIRY_SCOPE_REASON_FAILED:{row['law_number']}")
    engine.dispose()
    return {
        "schema_version": "golden-294-residence-expiry-v1",
        "applied_at": applied_at.isoformat(),
        "targets": list(TARGETS),
        "official_observations": dict(observations),
        "before": before,
        "after": after,
        "preservation": {
            "stored_status_unchanged": True,
            "articles_unchanged": True,
            "chunks_unchanged": True,
            "vectors_unchanged": True,
            "hard_delete": False,
        },
        "rollback": {
            "requires_explicit_approval": True,
            "source": "pre-change-baseline.json",
            "document_ids": [value["document_id"] for value in TARGETS.values()],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.approval != APPROVAL_PHRASE:
        raise ValueError("GOLDEN294_LIVE_APPROVAL_PHRASE_REQUIRED")
    observations = asyncio.run(fetch_observations())
    report = apply_sync(observations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"targets": report["targets"], "status": "applied"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
