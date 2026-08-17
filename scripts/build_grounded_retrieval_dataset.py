"""Build a deterministic, source-grounded retrieval evaluation dataset.

The generator is read-only with respect to the legal corpus.  It creates
questions from active article metadata that already exists in PostgreSQL and
emits a matching expected-source contract.  It never invents a law number,
article, date, form or effectivity and refuses to emit rows without an
official URL, active status and non-empty article content.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

import chromadb
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.feature005_db1_snapshot import DEFAULT_CHROMA_PATH
from api.legal_exact_retrieval import normalize_exact_identifier, plan_exact_lookup
from scripts.reconcile_legal_source_requirements import select_corpus_database_url

ACTIVE_COLLECTION = "legal_chunks_lechan_primary_v20260723"
ALLOWED_DOMAINS = {
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "trat_tu_do_thi",
    "noi_vu_hanh_chinh",
    "cu_tru_an_ninh",
    "an_sinh_y_te_giao_duc",
}


def _clean(value: Any, limit: int = 180) -> str:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    return text_value[:limit]


def _normalized_words(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").casefold())
    return " ".join(
        "".join(char for char in normalized if unicodedata.category(char) != "Mn").split()
    )


def _expired_legal_basis_document_ids(legal_as_of: str) -> set[int]:
    """Mirror the serving hard gate for relationships without mutating data."""

    statement = text(
        """
        SELECT r.source_document_id, r.target_document_id, r.relationship_type,
               source.status AS source_status,
               source.expired_date AS source_expired_date,
               target.status AS target_status,
               target.expired_date AS target_expired_date
        FROM legal_document_relationships r
        LEFT JOIN legal_documents source ON source.id = r.source_document_id
        LEFT JOIN legal_documents target ON target.id = r.target_document_id
        """
    )
    rejected: set[int] = set()
    with create_engine(select_corpus_database_url()).connect() as connection:
        for row in connection.execute(statement).mappings():
            if _normalized_words(row["relationship_type"]) != "van ban can cu":
                continue
            source_expired = (
                str(row["source_status"] or "").casefold() == "expired"
                or (
                    row["source_expired_date"] is not None
                    and str(row["source_expired_date"])[:10] <= legal_as_of
                )
            )
            target_expired = (
                str(row["target_status"] or "").casefold() == "expired"
                or (
                    row["target_expired_date"] is not None
                    and str(row["target_expired_date"])[:10] <= legal_as_of
                )
            )
            if target_expired and row["source_document_id"] is not None:
                rejected.add(int(row["source_document_id"]))
            if source_expired and row["target_document_id"] is not None:
                rejected.add(int(row["target_document_id"]))
    return rejected


def _authoritative_document_domains() -> dict[int, str]:
    """Use reviewed SQL scope instead of potentially stale vector metadata."""

    statement = text(
        """
        SELECT d.id AS document_id,
               COALESCE(g.group_slug, search_scope.domain) AS domain_slug
        FROM legal_documents d
        LEFT JOIN legal_commune_field_groups g
          ON g.field_id = d.field_id AND g.included = TRUE
        LEFT JOIN legal_search_scope search_scope
          ON search_scope.document_id = d.id
         AND search_scope.included = TRUE
        WHERE d.status = 'active'
        """
    )
    domains: dict[int, str] = {}
    with create_engine(select_corpus_database_url()).connect() as connection:
        for row in connection.execute(statement).mappings():
            domain = str(row["domain_slug"] or "")
            if domain in ALLOWED_DOMAINS:
                domains[int(row["document_id"])] = domain
    return domains


def _eligible_article_identities(legal_as_of: str) -> set[tuple[int, str]]:
    """Match the serving SQL quality/effectivity gate before creating gold."""

    statement = text(
        """
        SELECT DISTINCT d.id AS document_id, a.article_number
        FROM legal_article_chunks c
        JOIN legal_articles a ON a.id = c.article_id
        JOIN legal_documents d ON d.id = a.document_id
        JOIN legal_chunk_quality quality
          ON quality.chunk_id = c.id
         AND quality.eligible = TRUE
        WHERE d.status = 'active'
          AND a.status = 'active'
          AND (d.effective_date IS NULL OR d.effective_date <= :as_of)
          AND (d.expired_date IS NULL OR d.expired_date > :as_of)
          AND (a.effective_from IS NULL OR a.effective_from <= :as_of)
          AND (a.effective_to IS NULL OR a.effective_to > :as_of)
          AND btrim(coalesce(a.article_number, '')) <> ''
        """
    )
    with create_engine(select_corpus_database_url()).connect() as connection:
        return {
            (int(row["document_id"]), str(row["article_number"]))
            for row in connection.execute(statement, {"as_of": legal_as_of}).mappings()
        }


def _select_authoritative_domain(
    metadata: dict[str, Any],
    authoritative_domains: dict[int, str],
) -> str:
    """Fail closed rather than trusting a stale Chroma domain label."""

    try:
        document_id = int(metadata.get("document_id") or 0)
    except (TypeError, ValueError):
        return ""
    return authoritative_domains.get(document_id, "")


def _rows(chroma_path: Path, collection_name: str, legal_as_of: str) -> list[dict[str, Any]]:
    """Read only identities that are present in the active serving collection."""

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    payload = collection.get(include=["metadatas"])
    expired_basis_documents = _expired_legal_basis_document_ids(legal_as_of)
    authoritative_domains = _authoritative_document_domains()
    eligible_articles = _eligible_article_identities(legal_as_of)
    rows: list[dict[str, Any]] = []
    for metadata in payload.get("metadatas") or []:
        row = dict(metadata or {})
        document_id = int(row.get("document_id") or 0)
        domain = _select_authoritative_domain(row, authoritative_domains)
        effective = str(row.get("effective_date") or "")
        expired = str(row.get("expired_date") or "")
        if domain not in ALLOWED_DOMAINS:
            continue
        if str(row.get("document_status") or "").casefold() != "active":
            continue
        if str(row.get("article_status") or "active").casefold() == "expired":
            continue
        if effective and effective > legal_as_of:
            continue
        if expired and expired <= legal_as_of:
            continue
        if not str(row.get("source_url") or "").startswith(("https://", "http://")):
            continue
        if not document_id or not row.get("law_number") or not row.get("article_number"):
            continue
        if document_id in expired_basis_documents:
            continue
        if (document_id, str(row["article_number"])) not in eligible_articles:
            continue
        exact_plan = plan_exact_lookup(
            f"Theo {row['law_number']}, Điều {row['article_number']}"
        )
        if (
            not exact_plan.law_number
            or exact_plan.law_number
            != normalize_exact_identifier(row["law_number"])
        ):
            continue
        row["domain"] = domain
        rows.append(row)
    return rows


def build_dataset(rows: list[dict[str, Any]], legal_as_of: str, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # One row per document/article is the canonical identity.  No synthetic
    # duplication is used to reach the threshold.
    unique: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["document_id"]), str(row["article_number"]))
        unique.setdefault(key, row)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in unique.values():
        grouped.setdefault(str(row["domain"]), []).append(row)
    for rows_in_domain in grouped.values():
        rows_in_domain.sort(
            key=lambda row: (int(row["document_id"]), str(row["article_number"]))
        )
    # Round-robin keeps all supported domains represented instead of allowing
    # one large field to consume the first 1,000 sorted identities.
    ordered: list[dict[str, Any]] = []
    offsets = {domain: 0 for domain in grouped}
    domains = sorted(grouped)
    while len(ordered) < min(limit, len(unique)):
        progressed = False
        for domain in domains:
            index = offsets[domain]
            if index >= len(grouped[domain]):
                continue
            ordered.append(grouped[domain][index])
            offsets[domain] += 1
            progressed = True
            if len(ordered) >= limit:
                break
        if not progressed:
            break
    cases: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    for index, row in enumerate(ordered[:limit], start=1):
        law = _clean(row["law_number"], 80)
        article = _clean(row["article_number"], 40)
        title = _clean(row["document_title"], 140)
        case_id = f"corpus_article_{index:04d}_{int(row['document_id'])}_{hashlib.sha1(article.encode()).hexdigest()[:8]}"
        question = f"Theo {law}, Điều {article} của văn bản “{title}” quy định nội dung gì?"
        expected_contracts = [
            {
                "outcome": "AVAILABLE_CORRECTLY_TIERED",
                "document_id": int(row["document_id"]),
                "law_number": law,
                "provision": None,
            },
            {
                "outcome": "AVAILABLE_CORRECTLY_TIERED",
                "document_id": int(row["document_id"]),
                "law_number": law,
                "provision": article,
            },
        ]
        cases.append(
            {
                "case_id": case_id,
                "question": question,
                "legal_as_of": legal_as_of,
                "domain": row["domain"],
                "intent": "authority",
                "expected_sources": expected_contracts,
            }
        )
        expected.extend({"case_id": case_id, **contract} for contract in expected_contracts)
    return cases, expected


def rehydrate_authoritative_domains(
    cases: list[dict[str, Any]],
    authoritative_domains: dict[int, str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Correct a prior active-collection snapshot using reviewed SQL scope."""

    selected: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_case in cases:
        case = dict(raw_case)
        case_id = str(case.get("case_id") or "")
        contracts = [
            dict(item)
            for item in case.get("expected_sources") or []
            if isinstance(item, dict)
        ]
        document_id = next(
            (
                int(item["document_id"])
                for item in contracts
                if item.get("document_id") is not None
            ),
            0,
        )
        domain = authoritative_domains.get(document_id, "")
        if not case_id or case_id in seen_ids or domain not in ALLOWED_DOMAINS:
            continue
        seen_ids.add(case_id)
        case["domain"] = domain
        selected.append(case)
        expected.extend({"case_id": case_id, **contract} for contract in contracts)
        if len(selected) >= limit:
            break
    return selected, expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--collection", default=ACTIVE_COLLECTION)
    parser.add_argument(
        "--source-dataset",
        type=Path,
        help="Rehydrate a prior active-collection snapshot without reopening Chroma.",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--expected-sources", type=Path, required=True)
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.source_dataset:
        payload = json.loads(args.source_dataset.read_text(encoding="utf-8-sig"))
        source_cases = payload.get("questions", payload) if isinstance(payload, dict) else payload
        if not isinstance(source_cases, list):
            raise SystemExit("source dataset must be a list or questions object")
        cases, expected = rehydrate_authoritative_domains(
            [dict(item) for item in source_cases if isinstance(item, dict)],
            _authoritative_document_domains(),
            args.limit,
        )
    else:
        cases, expected = build_dataset(
            _rows(args.chroma_path, args.collection, args.legal_as_of),
            args.legal_as_of,
            args.limit,
        )
    if len(cases) < args.limit:
        raise SystemExit(f"insufficient distinct active source-grounded articles: {len(cases)} < {args.limit}")
    args.dataset.parent.mkdir(parents=True, exist_ok=True)
    args.expected_sources.parent.mkdir(parents=True, exist_ok=True)
    args.dataset.write_text(json.dumps({"questions": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.expected_sources.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in expected) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"case_count": len(cases), "dataset": str(args.dataset), "expected_sources": str(args.expected_sources)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
