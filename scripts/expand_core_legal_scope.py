"""Safely add reviewed five-domain records to the fast legal search scope.

The source VNLegal-LAL collection already contains their embeddings. This
script changes only the reviewed SQL scope; rebuild_filtered_chroma.py copies
the existing vectors afterwards. Run without --apply for a dry run.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import bindparam, text

from legal_search_server import retriever


CORE_DOMAINS = (
    "dat_dai_moi_truong",
    "xay_dung_do_thi",
    "an_sinh_y_te",
    "giao_duc_van_hoa",
    "tu_phap_ho_tich",
    "cu_tru_an_ninh",
)


def _candidates(connection):
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT s.document_id, s.domain, s.reason, d.law_number, d.title,
                       d.document_type, d.issuing_agency, d.scope, d.source_url,
                       d.effective_date, d.expired_date
                FROM legal_search_scope s
                JOIN legal_documents d ON d.id = s.document_id
                WHERE s.included = FALSE
                  AND s.domain IN :domains
                  AND s.reason = 'out_of_commune_scope'
                  AND d.status = 'active'
                  AND (d.effective_date IS NULL OR d.effective_date <= CURRENT_DATE)
                  AND (d.expired_date IS NULL OR d.expired_date > CURRENT_DATE)
                ORDER BY s.domain, d.id
                """
            ).bindparams(bindparam("domains", expanding=True)),
            {"domains": list(CORE_DOMAINS)},
        ).mappings()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Update reviewed scope after backup")
    parser.add_argument(
        "--backup-dir",
        default=r"J:\legal-chatbot-data\backups\legal_scope",
        help="Directory for the immutable pre-change manifest",
    )
    args = parser.parse_args()
    with retriever._engine.connect() as connection:
        rows = _candidates(connection)

    by_domain: dict[str, int] = {}
    for row in rows:
        domain = str(row["domain"])
        by_domain[domain] = by_domain.get(domain, 0) + 1
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": date.today().isoformat(),
        "apply": args.apply,
        "candidate_count": len(rows),
        "by_domain": by_domain,
        "candidates": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not args.apply:
        return

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"core_scope_before_expand_{date.today().isoformat()}.json"
    backup_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    now = datetime.now()
    with retriever._engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE legal_search_scope
                SET included = TRUE,
                    reason = 'core_domain_expansion_2026_07_14',
                    evaluated_as_of = CURRENT_DATE,
                    evaluated_at = :now
                WHERE document_id IN :document_ids
                """
            ).bindparams(bindparam("document_ids", expanding=True)),
            {"document_ids": [row["document_id"] for row in rows], "now": now},
        )
    print(json.dumps({"updated": len(rows), "backup": str(backup_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
