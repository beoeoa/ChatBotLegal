#!/usr/bin/env python3
"""Remediate candidate-only legal sources without mutating the 7,245 baseline.

The script deliberately separates three outcomes:

* reviewed active scope overrides are recorded for the candidate manifest only;
* expired instruments stay excluded and are never reactivated;
* one officially verified missing source is staged in PostgreSQL, but is not
  visible to runtime retrieval until a candidate collection passes Golden.

It is idempotent and fail-closed.  No Chroma collection is opened for writing.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_legal_retrieval import _database_url
from scripts.legal_search_server import (
    LegalImportRequest,
    _build_import_units,
    _split_article,
)
from scripts.resolve_three_tier_form_sources import (
    _validated_content,
    fetch_official_document,
)


LEGAL_AS_OF = date(2026, 8, 14)
BACKUP_MANIFEST = ROOT / "backups" / "candidate-remediation-preapply-20260814" / "manifest.json"
OFFICIAL_CACHE = ROOT / "reports" / "corpus-thinning" / "official-cache"
DOWNLOAD_CACHE = ROOT / "reports" / "corpus-thinning" / "official-download-cache"
DEFAULT_OUTPUT = ROOT / "reports" / "corpus-thinning" / "candidate-remediation-v2.json"

SCOPE_OVERRIDES: tuple[dict[str, Any], ...] = (
    {"document_id": 30257, "law_number": "166/2013/NĐ-CP", "domain": "khieu_nai_to_cao_xu_phat"},
    {"document_id": 105910, "law_number": "118/2021/NĐ-CP", "domain": "khieu_nai_to_cao_xu_phat"},
    {"document_id": 114986, "law_number": "70/2024/NĐ-CP", "domain": "cu_tru_an_ninh"},
    {"document_id": 119374, "law_number": "46/2024/QH15", "domain": "ho_tich_chung_thuc"},
    {"document_id": 121952, "law_number": "74/2025/QH15", "domain": "an_sinh_y_te_giao_duc"},
    {"document_id": 121955, "law_number": "73/2025/QH15", "domain": "an_sinh_y_te_giao_duc"},
    {"document_id": 125577, "law_number": "238/2025/NĐ-CP", "domain": "an_sinh_y_te_giao_duc"},
    {"document_id": 126927, "law_number": "374/2025/NĐ-CP", "domain": "an_sinh_y_te_giao_duc"},
)

EXPIRED_RETAINED: tuple[dict[str, Any], ...] = (
    {"document_id": 103124, "law_number": "55/2021/TT-BCA", "expired_date": "2026-07-01"},
    {"document_id": 111170, "law_number": "66/2023/TT-BCA", "expired_date": "2026-07-01"},
)

# Only 62/2020/QH14 remains required by the approved, current Golden dataset.
# The other 17 were false blockers introduced by mixing the retired Golden-100
# source list with the approved Golden-1000 residence remap.
RETIRED_FALSE_MISSING: tuple[str, ...] = (
    "102/2016/QH13", "123/2024/NĐ-CP", "15/2012/QH13", "15/2021/NĐ-CP",
    "158/2025/QĐ-UBND", "28/2020/TT-BGDĐT", "31/2019/NĐ-CP",
    "32/2020/TT-BGDĐT", "39/2009/QH12", "45/2020/NĐ-CP", "51/2010/QH12",
    "51/2024/QH15", "595/QĐ-BHXH", "62/2021/NĐ-CP", "82/2020/NĐ-CP",
    "88/2024/NĐ-CP", "91/2015/QH13",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_backup() -> dict[str, Any]:
    payload = json.loads(BACKUP_MANIFEST.read_text(encoding="utf-8"))
    dump = BACKUP_MANIFEST.parent / str(payload["postgres"]["file"])
    if not dump.is_file() or _sha256(dump) != payload["postgres"]["sha256"]:
        raise RuntimeError("preapply_postgres_backup_verification_failed")
    return {
        "manifest": str(BACKUP_MANIFEST.resolve()),
        "postgres_sha256": payload["postgres"]["sha256"],
        "postgres_bytes": int(payload["postgres"]["bytes"]),
    }


def _official_62_payload() -> tuple[LegalImportRequest, dict[str, Any]]:
    law_number = "62/2020/QH14"
    bundle = fetch_official_document(
        law_number,
        timeout=45,
        cache_dir=OFFICIAL_CACHE,
        refresh=False,
        legal_as_of=LEGAL_AS_OF.isoformat(),
    )
    document = dict(bundle.get("document") or {})
    if bundle.get("status") != "found" or str(document.get("id")) != "144268":
        raise RuntimeError("official_62_identity_not_found")
    if document.get("docNum") != law_number:
        raise RuntimeError("official_62_number_mismatch")
    if str((document.get("effStatus") or {}).get("name") or "").casefold() not in {
        "còn hiệu lực", "hết hiệu lực một phần"
    }:
        raise RuntimeError("official_62_not_current")
    files = [
        item for item in (bundle.get("files") or [])
        if str(item.get("fileName") or "").casefold().endswith(".docx")
        and "62.2020" in str(item.get("fileName") or "")
    ]
    if len(files) != 1:
        raise RuntimeError("official_62_pdf_attachment_ambiguous")
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        content, download_url, file_format = _validated_content(
            client,
            document_id="144268",
            file_record=files[0],
            cache_dir=DOWNLOAD_CACHE,
        )
    if file_format != "docx":
        raise RuntimeError("official_62_attachment_not_docx")
    try:
        from docx import Document

        document_file = Document(BytesIO(content))
        text_parts = [paragraph.text.strip() for paragraph in document_file.paragraphs]
        for table in document_file.tables:
            for row in table.rows:
                text_parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        legal_text = "\n".join(part for part in text_parts if part).strip()
    except Exception as exc:  # pragma: no cover - parser boundary
        raise RuntimeError("official_62_docx_extraction_failed") from exc
    normalized = " ".join(legal_text.upper().split())
    if (
        len(legal_text) < 10_000
        or "62/2020/QH14" not in normalized
        or "DỰNG" not in normalized
    ):
        raise RuntimeError("official_62_content_validation_failed")
    request = LegalImportRequest(
        title="Luật sửa đổi, bổ sung một số điều của Luật Xây dựng",
        law_number=law_number,
        document_type="Luật",
        issuing_agency="Quốc hội",
        scope="Trung ương",
        sector="Xây dựng",
        field_id=8,
        issued_date=date(2020, 6, 17),
        effective_date=date(2021, 1, 1),
        expired_date=None,
        source_url="https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=144268",
        applicability_info=(
            "Nguồn VBPL ItemID 144268; còn hiệu lực tại 2026-08-14. "
            "Staging chỉ dành cho collection ứng viên."
        ),
        content=legal_text,
        confirmed_official_source=True,
        domain_slug="dat_dai_xay_dung",
        structure="auto",
    )
    return request, {
        "official_item_id": "144268",
        "official_detail_url": document.get("detailUrl"),
        "official_source_url": request.source_url,
        "download_url": download_url,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "text_sha256": hashlib.sha256(legal_text.encode("utf-8")).hexdigest(),
        "characters": len(legal_text),
        "pages": None,
        "official_effectivity": {
            "from": document.get("effFrom"),
            "to": document.get("effTo"),
            "status": (document.get("effStatus") or {}).get("name"),
        },
    }


def _verified_document(connection: Any, decision: Mapping[str, Any]) -> dict[str, Any]:
    row = connection.execute(
        text(
            "SELECT id, law_number, title, status, effective_date, expired_date, source_url "
            "FROM legal_documents WHERE id = :document_id"
        ),
        {"document_id": int(decision["document_id"])},
    ).mappings().one()
    value = dict(row)
    if value["law_number"] != decision["law_number"]:
        raise RuntimeError(f"scope_identity_mismatch:{decision['document_id']}")
    return value


def _stage_document(connection: Any, request: LegalImportRequest) -> dict[str, Any]:
    existing = connection.execute(
        text(
            "SELECT id, status, source_url FROM legal_documents "
            "WHERE law_number = :law_number AND document_type = :document_type "
            "ORDER BY id"
        ),
        {"law_number": request.law_number, "document_type": request.document_type},
    ).mappings().all()
    if existing:
        if len(existing) != 1 or existing[0]["source_url"] != request.source_url:
            raise RuntimeError("official_62_existing_identity_conflict")
        document_id = int(existing[0]["id"])
        counts = connection.execute(
            text(
                "SELECT COUNT(DISTINCT a.id) articles, COUNT(c.id) chunks "
                "FROM legal_articles a LEFT JOIN legal_article_chunks c ON c.article_id=a.id "
                "WHERE a.document_id=:document_id"
            ),
            {"document_id": document_id},
        ).mappings().one()
        if int(counts["chunks"] or 0) < 1:
            raise RuntimeError("official_62_existing_staging_has_no_chunks")
        return {"document_id": document_id, **dict(counts), "reused": True}

    structure, articles, prepared_chunks = _build_import_units(request)
    if not articles:
        raise RuntimeError("official_62_no_import_units")
    document_id = int(
        connection.execute(
            text(
                """
                INSERT INTO legal_documents (
                    title, law_number, issued_date, effective_date, expired_date,
                    status, source_url, field_id, document_type, issuing_agency,
                    scope, sector, collection_source, applicability_info
                ) VALUES (
                    :title, :law_number, :issued_date, :effective_date, :expired_date,
                    'staging', :source_url, :field_id, :document_type, :issuing_agency,
                    :scope, :sector, 'candidate_corpus_official_staging', :applicability_info
                ) RETURNING id
                """
            ),
            request.model_dump(exclude={"content", "confirmed_official_source", "domain_slug", "structure"}),
        ).scalar_one()
    )
    connection.execute(
        text(
            """
            INSERT INTO legal_search_scope (
                document_id, included, reason, domain, evaluated_as_of, evaluated_at
            ) VALUES (
                :document_id, FALSE, 'candidate_corpus_remediation',
                'dat_dai_xay_dung', :legal_as_of, :evaluated_at
            )
            """
        ),
        {"document_id": document_id, "legal_as_of": LEGAL_AS_OF, "evaluated_at": datetime.now(timezone.utc)},
    )
    chunk_count = 0
    for article in articles:
        article_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO legal_articles (
                        document_id, article_number, title, content,
                        effective_from, effective_to, status
                    ) VALUES (
                        :document_id, :article_number, :title, :content,
                        :effective_from, :effective_to, 'staging'
                    ) RETURNING id
                    """
                ),
                {
                    "document_id": document_id,
                    "article_number": article["article_number"],
                    "title": article["title"],
                    "content": article["content"],
                    "effective_from": request.effective_date,
                    "effective_to": request.expired_date,
                },
            ).scalar_one()
        )
        chunks = prepared_chunks if structure == "unstructured" else _split_article(article)
        for chunk in chunks:
            connection.execute(
                text(
                    """
                    INSERT INTO legal_article_chunks (article_id, chunk_index, heading, content)
                    VALUES (:article_id, :chunk_index, :heading, :content)
                    """
                ),
                {"article_id": article_id, **{key: chunk.get(key) for key in ("chunk_index", "heading", "content")}},
            )
            chunk_count += 1
    if chunk_count < 1:
        raise RuntimeError("official_62_no_chunks")
    return {
        "document_id": document_id,
        "articles": len(articles),
        "chunks": chunk_count,
        "structure": structure,
        "reused": False,
    }


def remediate(*, apply: bool, output: Path) -> dict[str, Any]:
    backup = _verify_backup()
    request, official = _official_62_payload()
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            scope_rows = []
            for decision in SCOPE_OVERRIDES:
                current = _verified_document(connection, decision)
                if current["status"] != "active" or (
                    current["expired_date"] is not None
                    and current["expired_date"] <= LEGAL_AS_OF
                ):
                    raise RuntimeError(f"scope_override_not_current:{decision['document_id']}")
                scope_rows.append(
                    {
                        **decision,
                        "title": current["title"],
                        "action": "candidate_only_include",
                        "reason": "reviewed_commune_relevance_2026-08-14",
                    }
                )
            expired_rows = []
            for decision in EXPIRED_RETAINED:
                current = _verified_document(connection, decision)
                if str(current["expired_date"]) != decision["expired_date"]:
                    raise RuntimeError(f"expired_evidence_mismatch:{decision['document_id']}")
                expired_rows.append(
                    {
                        **decision,
                        "title": current["title"],
                        "action": "retain_historical_exclude_candidate",
                        "replacement_law_number": "116/2026/TT-BCA",
                    }
                )

            if apply:
                repaired = connection.execute(
                    text(
                        """
                        UPDATE legal_documents
                        SET effective_date = DATE '2008-06-20'
                        WHERE id = 116057
                          AND law_number = '02/2008/TTLT-BTNMT-BNV'
                          AND effective_date IS NULL
                          AND source_url LIKE '%ItemID=168034%'
                        """
                    )
                ).rowcount
                if repaired not in {0, 1}:
                    raise RuntimeError("metadata_repair_cardinality_invalid")
                staged = _stage_document(connection, request)
            else:
                repaired = 0
                staged = {"document_id": None, "articles": None, "chunks": None, "reused": False}

            missing_effective = [
                int(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT d.id
                        FROM legal_documents d
                        JOIN legal_search_scope s ON s.document_id=d.id AND s.included=TRUE
                        WHERE d.status='active' AND d.effective_date IS NULL
                        ORDER BY d.id
                        """
                    )
                )
            ]
            if apply and 116057 in missing_effective:
                raise RuntimeError("metadata_repair_not_persisted")
            if not apply:
                connection.rollback()
    finally:
        engine.dispose()

    supplement = {
        "document_id": staged["document_id"],
        "law_number": request.law_number,
        "title": request.title,
        "domain": "dat_dai_xay_dung",
        "action": "candidate_staging_include",
        "reason": "official_candidate_staging_2026-08-14",
        "article_count": staged["articles"],
        "chunk_count": staged["chunks"],
        "official_item_id": official["official_item_id"],
    }
    payload = {
        "schema_version": "legal-corpus-candidate-remediation-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": LEGAL_AS_OF.isoformat(),
        "mode": "apply" if apply else "plan",
        "approval_status": "approved_for_candidate_build" if apply else "plan_only",
        "baseline_mutated": False,
        "active_pointer_changed": False,
        "chroma_mutated": False,
        "backup": backup,
        "active_scope_overrides": scope_rows,
        "expired_retained": expired_rows,
        "staging_supplements": [supplement] if apply else [],
        "official_source_evidence": official,
        "missing_source_reconciliation": {
            "previous_reported_missing": 18,
            "staged_current_required": ["62/2020/QH14"] if apply else [],
            "retired_false_blockers": list(RETIRED_FALSE_MISSING),
            "reason_code": "retired_golden100_mixed_with_approved_golden1000",
        },
        "metadata_remediation": {
            "restored_count": 1 if apply else 0,
            "restored": (
                [{
                    "document_id": 116057,
                    "law_number": "02/2008/TTLT-BTNMT-BNV",
                    "effective_date": "2008-06-20",
                    "official_item_id": "168034",
                }]
                if apply
                else []
            ),
            "quarantined_count": len(missing_effective),
            "quarantined_document_ids": missing_effective,
            "quarantine_reason": "official_effective_date_unverified_excluded_from_candidate",
        },
    }
    if apply:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = remediate(apply=args.apply, output=args.output.resolve())
    print(
        json.dumps(
            {
                "mode": result["mode"],
                "scope_overrides": len(result["active_scope_overrides"]),
                "expired_retained": len(result["expired_retained"]),
                "staging_supplements": len(result["staging_supplements"]),
                "metadata_restored": result["metadata_remediation"]["restored_count"],
                "metadata_quarantined": result["metadata_remediation"]["quarantined_count"],
                "output": str(args.output.resolve()) if args.apply else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
