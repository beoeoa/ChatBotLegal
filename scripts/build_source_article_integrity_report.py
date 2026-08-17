"""Audit expected law/article references against candidate PostgreSQL rows."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_evaluation import (
    expected_sources_for_case,
    normalize_article,
    normalize_domain,
    normalize_law_number,
)
from scripts.backup_legal_retrieval import _database_url


DEFAULT_CANDIDATE = ROOT / "reports" / "m1-freeze" / "candidate_manifest.json"
DEFAULT_GOLDEN = ROOT / "notebook_data" / "feature016-golden-1000-approved.json"
DEFAULT_HARD = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_OUTPUT = (
    ROOT / "reports" / "retrieval-quality-v2" / "source_article_integrity_report.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(item) for item in (values or [])]


def _article_alternatives(value: Any) -> set[str]:
    return {
        normalize_article(part)
        for part in re.split(r"[,;/|]+", str(value or ""))
        if normalize_article(part)
    }


def main() -> int:
    import argparse
    from sqlalchemy import bindparam, create_engine, text

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    candidate_path = args.candidate_manifest.resolve()
    candidate = _load(candidate_path)
    documents = [dict(item) for item in (candidate.get("documents") or [])]
    document_ids = sorted(int(item["document_id"]) for item in documents)
    manifest_laws: dict[str, set[int]] = {}
    for document in documents:
        manifest_laws.setdefault(
            normalize_law_number(document.get("law_number")), set()
        ).add(int(document["document_id"]))

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            inventory = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT d.id AS document_id, d.law_number, "
                        "a.id AS article_id, a.article_number, a.title "
                        "FROM legal_documents d "
                        "LEFT JOIN legal_articles a ON a.document_id=d.id "
                        "WHERE d.id IN :document_ids"
                    ).bindparams(bindparam("document_ids", expanding=True)),
                    {"document_ids": document_ids},
                ).mappings()
            ]
    finally:
        engine.dispose()

    articles_by_law: dict[str, set[str]] = {}
    article_rows_by_law: dict[str, list[dict[str, Any]]] = {}
    for row in inventory:
        law = normalize_law_number(row.get("law_number"))
        article = normalize_article(row.get("article_number"))
        articles_by_law.setdefault(law, set())
        if article:
            articles_by_law[law].add(article)
        article_rows_by_law.setdefault(law, []).append(row)

    datasets = {
        "golden-1000": (args.golden.resolve(), _load(args.golden.resolve())),
        "hard-negative-100": (
            args.hard_negative.resolve(),
            _load(args.hard_negative.resolve()),
        ),
    }
    rows: list[dict[str, Any]] = []
    for dataset_name, (_path, payload) in datasets.items():
        for case in _cases(payload):
            if bool(case.get("expected_refusal")):
                continue
            for source in expected_sources_for_case(case):
                law_number = str(source.get("law_number") or "").strip()
                law = normalize_law_number(law_number)
                expected_articles = _article_alternatives(source.get("article"))
                available_articles = articles_by_law.get(law, set())
                if law not in manifest_laws:
                    status = "law_absent"
                    action = "official_source_and_legal_review"
                elif expected_articles and not expected_articles.intersection(
                    available_articles
                ):
                    status = "article_absent"
                    action = "golden_mapping_or_nested_provision_review"
                else:
                    status = "available"
                    action = "none"
                rows.append(
                    {
                        "dataset": dataset_name,
                        "case_id": case.get("case_id"),
                        "domain": normalize_domain(case.get("domain")),
                        "legal_as_of": case.get("legal_as_of"),
                        "law_number": law_number,
                        "expected_article": source.get("article"),
                        "status": status,
                        "required_action": action,
                        "candidate_document_ids": sorted(manifest_laws.get(law, set())),
                        "available_article_numbers": sorted(available_articles),
                        "candidate_article_row_count": len(
                            article_rows_by_law.get(law, [])
                        ),
                        "candidate_article_rows": [
                            {
                                "article_id": item.get("article_id"),
                                "article_number": item.get("article_number"),
                                "title": item.get("title"),
                            }
                            for item in sorted(
                                article_rows_by_law.get(law, []),
                                key=lambda value: (
                                    normalize_article(value.get("article_number")),
                                    int(value.get("article_id") or 0),
                                ),
                            )[:25]
                        ]
                        if status == "article_absent"
                        else [],
                    }
                )

    gaps = [row for row in rows if row["status"] != "available"]
    counts = Counter(row["status"] for row in rows)
    by_dataset = {
        name: dict(Counter(row["status"] for row in rows if row["dataset"] == name))
        for name in datasets
    }
    report = {
        "schema_version": "legal-source-article-integrity-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not gaps else "fail",
        "candidate_collection": candidate.get("candidate_collection"),
        "candidate_manifest_file_sha256": _sha256(candidate_path),
        "candidate_manifest_sha256": candidate.get("manifest_sha256"),
        "datasets": {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, (path, _payload) in datasets.items()
        },
        "expected_reference_count": len(rows),
        "available_reference_count": counts.get("available", 0),
        "law_absent_reference_count": counts.get("law_absent", 0),
        "article_absent_reference_count": counts.get("article_absent", 0),
        "by_dataset": by_dataset,
        "gaps": gaps,
        "database_mutated": False,
        "candidate_collection_mutated": False,
        "active_pointer_changed": False,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "expected_reference_count": len(rows),
                "available_reference_count": counts.get("available", 0),
                "law_absent_reference_count": counts.get("law_absent", 0),
                "article_absent_reference_count": counts.get("article_absent", 0),
                "by_dataset": by_dataset,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
