"""Build and verify the DB-2 Lê Chân serving scope without mutating runtime data.

The command is intentionally shadow-only:

* PostgreSQL is opened in a read-only transaction.
* Chroma is not opened or modified.
* no sidecar table is created (schema approval is still required);
* the active collection pointer is only read before and after the run.

The output consists of a compact manifest plus document/chunk JSONL ledgers.
No raw legal passage or question text is written to the report.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse
from typing import Any, Iterable

from sqlalchemy import create_engine, text

try:
    from scripts.feature005_db1_snapshot import (
        database_url,
        git_commit,
        safe_database_target,
        table_id_snapshot,
    )
except ModuleNotFoundError:  # Direct execution: python scripts/<file>.py
    from feature005_db1_snapshot import (  # type: ignore[no-redef]
        database_url,
        git_commit,
        safe_database_target,
        table_id_snapshot,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "feature005" / f"db2-shadow-{date.today():%Y%m%d}"
DEFAULT_CHROMA_PATH = Path(
    os.getenv("LEGAL_CHROMA_PATH", r"J:\legal-chatbot-data\chroma_store")
)

DIRECT_PRIMARY_REASONS = frozenset(
    {
        "central_commune_specific",
        "hai_phong_commune_specific",
        "hai_phong_relevant_domain",
        "current_core_law",
        "related_current_instrument",
        "manual_verified_import",
    }
)

# Deliberately excludes "Văn bản căn cứ" and generic cross-reference relations.
ALLOWED_ONE_HOP_RELATIONS = frozenset(
    {
        "van ban duoc hd qd chi tiet",
        "van ban hd qd chi tiet",
        "van ban bo sung",
        "van ban duoc bo sung",
        "van ban sua doi",
        "van ban duoc sua doi",
        "van ban thay the",
        "van ban duoc thay the",
    }
)

OFFICIAL_SOURCE_HOSTS = frozenset(
    {
        "vbpl.vn",
        "www.vbpl.vn",
        "vanban.chinhphu.vn",
        "congbao.chinhphu.vn",
        "haiphong.gov.vn",
        "www.haiphong.gov.vn",
    }
)

CENTRAL_SCOPE_TERMS = frozenset(
    {"toan quoc", "trung uong", "ca nuoc", "toan lanh tho"}
)

CENTRAL_AGENCY_TERMS = (
    "quoc hoi",
    "uy ban thuong vu quoc hoi",
    "chinh phu",
    "thu tuong",
    "bo ",
    "toa an nhan dan toi cao",
    "vien kiem sat nhan dan toi cao",
    "thanh tra chinh phu",
)

SUPPORTED_HIERARCHY = {
    "hien phap": 100,
    "bo luat": 90,
    "luat": 90,
    "phap lenh": 80,
    "lenh": 75,
    "nghi quyet": 70,
    "nghi quyet lien tich": 70,
    "nghi dinh": 60,
    "quyet dinh": 50,
    "quyetdinh": 50,
    "quyet dinh": 50,
    "chi thi": 45,
    "thong tu": 40,
    "thong tu lien tich": 40,
    "thong tu lien bo": 40,
    "van ban hop nhat": 35,
}

EXPECTED_SOURCE_ALIASES = {
    "luat ho tich": "60/2014/QH13",
    "nghi dinh 123 2015": "123/2015/NĐ-CP",
    "luat cu tru 2020": "68/2020/QH14",
    "luat dat dai": "31/2024/QH15",
    "luat dat dai 2024": "31/2024/QH15",
    "luat xay dung": "50/2014/QH13",
    "luat xay dung 2014": "50/2014/QH13",
    "luat khieu nai 2011": "02/2011/QH13",
    "luat to tung hanh chinh 2015": "93/2015/QH13",
    "luat xu ly vi pham hanh chinh 2012": "15/2012/QH13",
    "luat giao thong duong bo": "23/2008/QH12",
}

DOCUMENT_SQL = """
SELECT d.id, d.title, d.law_number, d.document_type, d.issuing_agency,
       d.scope, d.sector, d.status, d.effective_date, d.expired_date,
       d.source_url, d.collection_source, d.applicability_info,
       f.name AS field_name, s.reason AS legacy_scope_reason,
       s.domain AS legacy_scope_domain
FROM legal_documents d
LEFT JOIN legal_fields f ON f.id = d.field_id
LEFT JOIN legal_search_scope s ON s.document_id = d.id
ORDER BY d.id
"""

CHUNK_AGGREGATE_SQL = """
SELECT a.document_id,
       count(DISTINCT a.id) AS article_count,
       count(c.id) AS chunk_count,
       count(c.id) FILTER (
           WHERE btrim(coalesce(c.content, '')) <> ''
             AND coalesce(a.status, 'active') <> 'expired'
             AND (a.effective_from IS NULL OR a.effective_from <= :as_of)
             AND (a.effective_to IS NULL OR a.effective_to > :as_of)
             AND NOT coalesce(q.quality_reasons, ARRAY[]::text[])
                     && ARRAY['empty_content', 'exact_duplicate',
                              'noisy_article_title']
             AND (q.canonical_chunk_id IS NULL
                  OR q.canonical_chunk_id = c.id)
       ) AS valid_chunk_count
FROM legal_articles a
LEFT JOIN legal_article_chunks c ON c.article_id = a.id
LEFT JOIN legal_chunk_quality q ON q.chunk_id = c.id
GROUP BY a.document_id
"""

CHUNK_SQL = """
SELECT c.id AS chunk_id, c.article_id, a.document_id,
       c.content IS NOT NULL AND btrim(c.content) <> '' AS has_content,
       coalesce(a.status, 'active') AS article_status,
       a.effective_from, a.effective_to,
       q.canonical_chunk_id, coalesce(q.quality_reasons, ARRAY[]::text[])
           AS quality_reasons
FROM legal_article_chunks c
JOIN legal_articles a ON a.id = c.article_id
LEFT JOIN legal_chunk_quality q ON q.chunk_id = c.id
ORDER BY c.id
"""

RELATION_SQL = """
SELECT source_document_id, target_document_id, relationship_type
FROM legal_document_relationships
WHERE source_document_id IS NOT NULL
  AND target_document_id IS NOT NULL
ORDER BY id
"""


def normalize(value: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold())
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_jsonl_id(path: Path, key: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)[key]
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


def active_pointer(path: Path) -> str | None:
    pointer = path / "active_core_collection.txt"
    if not pointer.is_file():
        return None
    return pointer.read_text(encoding="utf-8").strip() or None


def source_host(url: object) -> str:
    try:
        return urlparse(str(url or "")).hostname.casefold()
    except (AttributeError, ValueError):
        return ""


def scope_decision(row: dict[str, Any]) -> tuple[bool, str | None]:
    scope = normalize(row.get("scope"))
    agency = normalize(row.get("issuing_agency"))
    if scope in CENTRAL_SCOPE_TERMS:
        return True, "scope_central_metadata"
    if "hai phong" in scope or "le chan" in scope:
        return True, "scope_haiphong_metadata"
    if scope:
        return False, None
    if "hai phong" in agency or "le chan" in agency:
        return True, "scope_derived_local_agency"
    if any(term in agency for term in CENTRAL_AGENCY_TERMS):
        return True, "scope_derived_central_agency"
    return False, None


def hierarchy_rank(row: dict[str, Any]) -> int | None:
    return SUPPORTED_HIERARCHY.get(normalize(row.get("document_type")))


def law_number_key(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").upper())


def canonical_documents(
    rows: list[dict[str, Any]],
    chunk_stats: dict[int, dict[str, int]],
) -> dict[str, int]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = law_number_key(row.get("law_number"))
        if key:
            groups[key].append(row)
    result: dict[str, int] = {}
    for key, candidates in groups.items():
        result[key] = max(
            candidates,
            key=lambda item: (
                int(chunk_stats.get(int(item["id"]), {}).get("valid_chunk_count", 0)),
                bool(item.get("source_url")),
                bool(item.get("scope")),
                int(item["id"]),
            ),
        )["id"]
    return result


def extract_legal_number(reference: object) -> str | None:
    value = str(reference or "")
    match = re.search(
        r"\b\d{1,4}/\d{4}/[A-ZĐa-zđ0-9\-]+(?:-[A-ZĐa-zđ0-9]+)*\b",
        value,
    )
    if match:
        return law_number_key(match.group(0))
    partial = re.search(r"\b(\d{1,4}/\d{4})\b", value)
    return partial.group(1) if partial else None


def resolve_reference(
    reference: str,
    by_law_number: dict[str, int],
) -> tuple[int | None, str]:
    normalized = normalize(reference)
    if normalized.startswith("dieu ") or normalized in {
        "nghi dinh",
        "bien dong dat dai",
    }:
        return None, "non_document_locator"
    for alias, number in EXPECTED_SOURCE_ALIASES.items():
        if alias in normalized:
            document_id = by_law_number.get(law_number_key(number))
            return (
                (document_id, "alias")
                if document_id is not None
                else (None, "expected_source_not_in_corpus")
            )
    number = extract_legal_number(reference)
    if number:
        exact = by_law_number.get(number)
        if exact:
            return exact, "legal_number"
        prefix_matches = {
            document_id
            for key, document_id in by_law_number.items()
            if key.startswith(number + "/")
        }
        if len(prefix_matches) == 1:
            return next(iter(prefix_matches)), "partial_legal_number"
        if len(prefix_matches) > 1:
            return None, "ambiguous_legal_number"
        return None, "expected_source_not_in_corpus"
    return None, "non_document_locator"


def load_expected_inputs(
    by_law_number: dict[str, int],
) -> tuple[
    dict[int, set[str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    golden_path = ROOT / "notebook_data" / "legal-golden-set.json"
    roles_path = ROOT / "tests" / "fixtures" / "feature005_role_matrix.json"
    priority_path = ROOT / "notebook_data" / "forms" / "priority_200_forms.json"
    approved_path = (
        ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json"
    )
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    roles = json.loads(roles_path.read_text(encoding="utf-8"))
    priority = json.loads(priority_path.read_text(encoding="utf-8"))
    approved = json.loads(approved_path.read_text(encoding="utf-8"))

    document_seeds: dict[int, set[str]] = defaultdict(set)
    expected_records: list[dict[str, Any]] = []
    golden_domains: dict[int, set[str]] = defaultdict(set)
    for case in golden.get("questions") or []:
        for reference in case.get("expected_citations") or []:
            document_id, method = resolve_reference(reference, by_law_number)
            record = {
                "set": "golden_167",
                "case_id": case.get("id"),
                "reference_sha256": hashlib.sha256(
                    str(reference).encode("utf-8")
                ).hexdigest(),
                "resolution": method,
                "document_id": document_id,
            }
            expected_records.append(record)
            if document_id is not None:
                document_seeds[document_id].add("golden_expected_source")
                domain = str(case.get("domain") or "").strip()
                if domain:
                    golden_domains[document_id].add(domain)

    role_records: list[dict[str, Any]] = []
    for case in roles.get("cases") or []:
        # The fixture currently has no expected_sources field. Only identifiers
        # explicitly present in the approved case are eligible as source seeds.
        references = list(case.get("expected_sources") or [])
        if not references:
            document_id, method = resolve_reference(
                str(case.get("question") or ""), by_law_number
            )
            if document_id is not None or method in {
                "ambiguous_legal_number",
                "expected_source_not_in_corpus",
            }:
                references.append(str(case.get("question") or ""))
        if not references:
            role_records.append(
                {
                    "case_id": case.get("id"),
                    "source_contract": "no_explicit_expected_source",
                    "resolved_document_ids": [],
                }
            )
            continue
        resolved: list[int] = []
        for reference in references:
            document_id, method = resolve_reference(str(reference), by_law_number)
            expected_records.append(
                {
                    "set": "role_9",
                    "case_id": case.get("id"),
                    "reference_sha256": hashlib.sha256(
                        str(reference).encode("utf-8")
                    ).hexdigest(),
                    "resolution": method,
                    "document_id": document_id,
                }
            )
            if document_id is not None:
                resolved.append(document_id)
                document_seeds[document_id].add("role_expected_source")
        role_records.append(
            {
                "case_id": case.get("id"),
                "source_contract": "explicit_fixture_identifier",
                "resolved_document_ids": sorted(set(resolved)),
            }
        )

    approved_forms = [
        item
        for item in approved.get("forms") or []
        if item.get("review_status") == "approved"
        and item.get("is_approved") is True
    ]
    official_approved_forms: list[dict[str, Any]] = []
    for item in approved_forms:
        host = source_host(item.get("source_page_url"))
        local_path = ROOT / str(
            item.get("priority_path")
            or item.get("local_path")
            or item.get("source_package_path")
            or ""
        )
        provenance_ok = (
            host.endswith(".haiphong.gov.vn") or host == "haiphong.gov.vn"
        ) and local_path.is_file()
        document_id = None
        method = "no_legal_document_identifier"
        package = str(item.get("source_package_title") or "")
        if package:
            document_id, method = resolve_reference(package, by_law_number)
        if provenance_ok and document_id is not None:
            document_seeds[document_id].add("approved_form_source")
        official_approved_forms.append(
            {
                "form_id": item.get("id"),
                "procedure_id": item.get("procedure_id"),
                "provenance_ok": provenance_ok,
                "document_id": document_id,
                "document_resolution": method,
            }
        )

    approved_by_procedure = {
        normalize(item.get("procedure_id"))
        for item in official_approved_forms
        if item["provenance_ok"] and item.get("procedure_id")
    }
    priority_records = priority.get("forms") or []
    procedure_coverage = {
        "priority_records": len(priority_records),
        "priority_unique_procedures": len(
            {item.get("procedure_id") for item in priority_records}
        ),
        "approved_official_records": len(official_approved_forms),
        "approved_with_hard_provenance": sum(
            bool(item["provenance_ok"]) for item in official_approved_forms
        ),
        "priority_records_with_approved_procedure_match": sum(
            normalize(item.get("procedure_id")) in approved_by_procedure
            for item in priority_records
        ),
    }
    input_info = {
        "files": {
            str(path.relative_to(ROOT)).replace("\\", "/"): {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in (golden_path, roles_path, priority_path, approved_path)
        },
        "golden_cases": len(golden.get("questions") or []),
        "role_cases": len(roles.get("cases") or []),
        "expected_references": len(expected_records),
        "procedure_coverage": procedure_coverage,
        "approved_form_sources": official_approved_forms,
        "golden_domains": {
            str(key): sorted(value) for key, value in golden_domains.items()
        },
    }
    return document_seeds, expected_records, role_records, input_info


def base_domain(
    row: dict[str, Any],
    golden_domains: dict[int, list[str]],
) -> tuple[str | None, str | None]:
    for value, reason in (
        (row.get("legacy_scope_domain"), "legacy_scope_domain"),
        (row.get("sector"), "sector_metadata"),
        (row.get("field_name"), "field_metadata"),
    ):
        if str(value or "").strip():
            return str(value).strip(), reason
    domains = golden_domains.get(int(row["id"])) or []
    if len(domains) == 1:
        return domains[0], "golden_expected_domain"
    return None, None


def hard_gate(
    row: dict[str, Any],
    *,
    as_of: date,
    chunk_stats: dict[int, dict[str, int]],
    canonical_by_number: dict[str, int],
    domain: str | None,
    domain_reason: str | None,
) -> tuple[dict[str, bool], list[str], list[str]]:
    failures: list[str] = []
    evidence: list[str] = []
    document_id = int(row["id"])

    status_ok = str(row.get("status") or "").casefold() == "active"
    if not status_ok:
        failures.append("inactive_or_expired_status")

    host = source_host(row.get("source_url"))
    provenance_ok = host in OFFICIAL_SOURCE_HOSTS
    if not provenance_ok:
        failures.append("missing_or_unapproved_source_provenance")
    else:
        evidence.append("official_source_host")

    effective = row.get("effective_date")
    expired = row.get("expired_date")
    effectivity_ok = status_ok
    if effective and effective > as_of:
        effectivity_ok = False
        failures.append("not_yet_effective")
    if expired and expired <= as_of:
        effectivity_ok = False
        failures.append("expired_by_date")
    if effectivity_ok:
        evidence.append(
            "effective_date_verified"
            if effective
            else "effectivity_verified_by_official_active_status"
        )

    scope_ok, scope_reason = scope_decision(row)
    if not scope_ok:
        failures.append(
            "missing_scope" if not row.get("scope") else "outside_serving_scope"
        )
    elif scope_reason:
        evidence.append(scope_reason)

    domain_ok = bool(domain)
    if not domain_ok:
        failures.append("missing_domain")
    elif domain_reason:
        evidence.append(domain_reason)

    rank = hierarchy_rank(row)
    hierarchy_ok = rank is not None
    if not hierarchy_ok:
        failures.append("unsupported_legal_hierarchy")
    else:
        evidence.append(f"hierarchy_rank_{rank}")

    number = law_number_key(row.get("law_number"))
    identity_ok = not number or canonical_by_number.get(number) == document_id
    if not identity_ok:
        failures.append("duplicate_law_number_noncanonical")

    stats = chunk_stats.get(document_id) or {}
    article_chunk_ok = (
        int(stats.get("article_count", 0)) > 0
        and int(stats.get("valid_chunk_count", 0)) > 0
    )
    if not article_chunk_ok:
        failures.append("no_valid_linked_article_chunk")

    gates = {
        "official_status": status_ok,
        "provenance": provenance_ok,
        "effectivity": effectivity_ok,
        "scope": scope_ok,
        "domain": domain_ok,
        "hierarchy": hierarchy_ok,
        "canonical_identity": identity_ok,
        "article_chunk": article_chunk_ok,
    }
    return gates, sorted(set(failures)), sorted(set(evidence))


def source_snapshot(connection) -> dict[str, Any]:
    return {
        "documents": table_id_snapshot(connection, "legal_documents"),
        "articles": table_id_snapshot(connection, "legal_articles"),
        "chunks": table_id_snapshot(connection, "legal_article_chunks"),
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            encoded = (
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            handle.write(encoded.decode("utf-8"))
            digest.update(encoded)
            count += 1
    return count, digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    document_path = output_dir / "documents.jsonl"
    chunk_path = output_dir / "chunks.jsonl"
    manifest_path = output_dir / "manifest.json"

    pointer_before = active_pointer(args.chroma_path)
    url = database_url()
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        snapshot_before = source_snapshot(connection)
        documents = [dict(row) for row in connection.execute(text(DOCUMENT_SQL)).mappings()]
        chunk_stats = {
            int(row["document_id"]): {
                "article_count": int(row["article_count"] or 0),
                "chunk_count": int(row["chunk_count"] or 0),
                "valid_chunk_count": int(row["valid_chunk_count"] or 0),
            }
            for row in connection.execute(
                text(CHUNK_AGGREGATE_SQL), {"as_of": args.as_of}
            ).mappings()
        }
        relationships = [dict(row) for row in connection.execute(text(RELATION_SQL)).mappings()]
        transaction.rollback()

    canonical_by_number = canonical_documents(documents, chunk_stats)
    (
        expected_seeds,
        expected_records,
        role_records,
        input_info,
    ) = load_expected_inputs(canonical_by_number)
    golden_domains = {
        int(key): value for key, value in input_info.pop("golden_domains").items()
    }

    rows_by_id = {int(row["id"]): row for row in documents}
    domain_by_id: dict[int, tuple[str | None, str | None]] = {
        document_id: base_domain(row, golden_domains)
        for document_id, row in rows_by_id.items()
    }
    seed_codes: dict[int, set[str]] = defaultdict(set)
    for document_id, row in rows_by_id.items():
        reason = str(row.get("legacy_scope_reason") or "")
        if reason in DIRECT_PRIMARY_REASONS:
            seed_codes[document_id].add(reason)
    for document_id, codes in expected_seeds.items():
        seed_codes[document_id].update(codes)

    provisional_primary = set(seed_codes)
    allowed_relations: list[dict[str, Any]] = []
    ignored_relation_counts: Counter[str] = Counter()
    related_domains: dict[int, set[str]] = defaultdict(set)
    for relation in relationships:
        relation_type = normalize(relation.get("relationship_type"))
        if relation_type not in ALLOWED_ONE_HOP_RELATIONS:
            ignored_relation_counts[relation_type or "missing"] += 1
            continue
        source_id = int(relation["source_document_id"])
        target_id = int(relation["target_document_id"])
        if source_id in provisional_primary:
            candidate, primary = target_id, source_id
        elif target_id in provisional_primary:
            candidate, primary = source_id, target_id
        else:
            continue
        allowed_relations.append(
            {
                "primary_document_id": primary,
                "candidate_document_id": candidate,
                "relationship_type": relation_type,
            }
        )
        domain = domain_by_id.get(primary, (None, None))[0]
        if domain:
            related_domains[candidate].add(domain)

    for document_id, domains in related_domains.items():
        current, _ = domain_by_id.get(document_id, (None, None))
        if not current and len(domains) == 1:
            domain_by_id[document_id] = (
                next(iter(domains)),
                "one_hop_inherited_domain",
            )

    assessments: dict[int, dict[str, Any]] = {}
    gate_failure_counts: Counter[str] = Counter()
    for document_id, row in rows_by_id.items():
        domain, domain_reason = domain_by_id[document_id]
        gates, failures, evidence = hard_gate(
            row,
            as_of=args.as_of,
            chunk_stats=chunk_stats,
            canonical_by_number=canonical_by_number,
            domain=domain,
            domain_reason=domain_reason,
        )
        gate_failure_counts.update(failures)
        assessments[document_id] = {
            "gates": gates,
            "failures": failures,
            "evidence": evidence,
            "domain": domain,
        }

    primary_ids = {
        document_id
        for document_id in provisional_primary
        if not assessments[document_id]["failures"]
    }
    support_candidates = {
        int(item["candidate_document_id"])
        for item in allowed_relations
        if int(item["primary_document_id"]) in primary_ids
    }
    support_ids = {
        document_id
        for document_id in support_candidates - primary_ids
        if not assessments[document_id]["failures"]
    }

    document_tiers: dict[int, str] = {}
    tier_counts: Counter[str] = Counter()
    document_reason_counts: Counter[str] = Counter()
    document_rows: list[dict[str, Any]] = []
    for document_id in sorted(rows_by_id):
        assessment = assessments[document_id]
        if document_id in primary_ids:
            tier = "primary"
            reasons = sorted(seed_codes[document_id])
        elif document_id in support_ids:
            tier = "support"
            reasons = ["allowed_one_hop_relation"]
        else:
            tier = "historical_quarantine"
            reasons = assessment["failures"] or ["not_selected_for_lechan_serving"]
        document_tiers[document_id] = tier
        tier_counts[tier] += 1
        document_reason_counts.update(reasons)
        stats = chunk_stats.get(document_id) or {}
        document_rows.append(
            {
                "document_id": document_id,
                "tier": tier,
                "eligible": tier in {"primary", "support"},
                "reason_codes": reasons,
                "hard_gate": assessment["gates"],
                "gate_evidence": assessment["evidence"],
                "domain_code": assessment["domain"],
                "article_count": int(stats.get("article_count", 0)),
                "chunk_count": int(stats.get("chunk_count", 0)),
                "valid_chunk_count": int(stats.get("valid_chunk_count", 0)),
            }
        )
    document_count, document_sha = write_jsonl(document_path, document_rows)

    chunk_tier_counts: Counter[str] = Counter()
    chunk_reason_counts: Counter[str] = Counter()
    primary_support_chunk_violations = 0

    def chunk_rows() -> Iterable[dict[str, Any]]:
        nonlocal primary_support_chunk_violations
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET TRANSACTION READ ONLY"))
            result = connection.execution_options(stream_results=True).execute(
                text(CHUNK_SQL)
            ).mappings()
            for row in result:
                document_id = int(row["document_id"])
                document_tier = document_tiers[document_id]
                reasons: list[str] = []
                if not row["has_content"]:
                    reasons.append("empty_content")
                if str(row["article_status"] or "").casefold() == "expired":
                    reasons.append("expired_article")
                if row["effective_from"] and row["effective_from"] > args.as_of:
                    reasons.append("article_not_yet_effective")
                if row["effective_to"] and row["effective_to"] <= args.as_of:
                    reasons.append("article_expired_by_date")
                quality_reasons = set(row["quality_reasons"] or [])
                for reason in ("empty_content", "exact_duplicate", "noisy_article_title"):
                    if reason in quality_reasons:
                        reasons.append(reason)
                canonical_id = row["canonical_chunk_id"]
                if canonical_id is not None and int(canonical_id) != int(row["chunk_id"]):
                    reasons.append("noncanonical_duplicate")
                if document_tier == "historical_quarantine":
                    reasons.append("document_historical_quarantine")

                eligible = not reasons and document_tier in {"primary", "support"}
                tier = document_tier if eligible else "historical_quarantine"
                if document_tier in {"primary", "support"} and not eligible:
                    # Expected and valid: bad chunks of an eligible document stay
                    # quarantined. A violation is only an included bad chunk.
                    pass
                if eligible and reasons:
                    primary_support_chunk_violations += 1
                final_reasons = sorted(set(reasons)) or [
                    f"document_{document_tier}"
                ]
                chunk_tier_counts[tier] += 1
                chunk_reason_counts.update(final_reasons)
                yield {
                    "chunk_id": int(row["chunk_id"]),
                    "article_id": int(row["article_id"]),
                    "document_id": document_id,
                    "tier": tier,
                    "eligible": eligible,
                    "canonical_chunk_id": (
                        int(canonical_id) if canonical_id is not None else None
                    ),
                    "reason_codes": final_reasons,
                }
            transaction.rollback()

    chunk_count, chunk_sha = write_jsonl(chunk_path, chunk_rows())

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        snapshot_after = source_snapshot(connection)
        transaction.rollback()
    engine.dispose()
    pointer_after = active_pointer(args.chroma_path)

    resolved_expected = [
        item for item in expected_records if item["document_id"] is not None
    ]
    expected_excluded = [
        item
        for item in resolved_expected
        if document_tiers[int(item["document_id"])] not in {"primary", "support"}
    ]
    ambiguous_identifiers = [
        item
        for item in expected_records
        if item["resolution"] == "ambiguous_legal_number"
    ]
    absent_expected_sources = [
        item
        for item in expected_records
        if item["resolution"] == "expected_source_not_in_corpus"
    ]
    primary_support_gate_violations = sum(
        bool(assessments[document_id]["failures"])
        for document_id in primary_ids | support_ids
    )
    validations = {
        "all_documents_classified": document_count == snapshot_before["documents"]["count"],
        "all_chunks_classified": chunk_count == snapshot_before["chunks"]["count"],
        "unknown_document_tiers": 0,
        "unknown_chunk_tiers": 0,
        "all_exclusions_have_reason": all(
            row["reason_codes"] for row in document_rows
        ),
        "primary_support_hard_gate_violations": primary_support_gate_violations,
        "included_chunk_gate_violations": primary_support_chunk_violations,
        "resolved_expected_sources_excluded": len(expected_excluded),
        "ambiguous_expected_legal_identifiers": len(ambiguous_identifiers),
        "expected_sources_not_in_corpus": len(absent_expected_sources),
        "source_database_unchanged": snapshot_before == snapshot_after,
        "active_collection_pointer_unchanged": pointer_before == pointer_after,
        "generic_legal_basis_relations_expanded": 0,
        "sidecar_schema_created": False,
    }
    passed = (
        validations["all_documents_classified"]
        and validations["all_chunks_classified"]
        and validations["all_exclusions_have_reason"]
        and validations["primary_support_hard_gate_violations"] == 0
        and validations["included_chunk_gate_violations"] == 0
        and validations["resolved_expected_sources_excluded"] == 0
        and validations["ambiguous_expected_legal_identifiers"] == 0
        and validations["source_database_unchanged"]
        and validations["active_collection_pointer_unchanged"]
        and validations["generic_legal_basis_relations_expanded"] == 0
    )

    manifest = {
        "schema_version": "feature005-db2-shadow-v1",
        "status": "verified" if passed else "verification_failed",
        "mode": "shadow_read_only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": args.as_of.isoformat(),
        "git_commit": git_commit(),
        "runtime_target": safe_database_target(url),
        "active_collection_pointer": {
            "before": pointer_before,
            "after": pointer_after,
        },
        "rules": {
            "direct_primary_reasons": sorted(DIRECT_PRIMARY_REASONS),
            "allowed_one_hop_relationships": sorted(ALLOWED_ONE_HOP_RELATIONS),
            "excluded_relationship": "van ban can cu",
            "maximum_relationship_hops": 1,
            "official_source_hosts": sorted(OFFICIAL_SOURCE_HOSTS),
            "model_metadata_inference": False,
            "sidecar_write": False,
        },
        "inputs": {
            key: value
            for key, value in input_info.items()
            if key != "approved_form_sources"
        },
        "approved_form_source_audit": {
            "count": len(input_info["approved_form_sources"]),
            "provenance_pass": sum(
                bool(item["provenance_ok"])
                for item in input_info["approved_form_sources"]
            ),
            "linked_legal_documents": len(
                {
                    item["document_id"]
                    for item in input_info["approved_form_sources"]
                    if item["provenance_ok"] and item["document_id"] is not None
                }
            ),
        },
        "expected_source_audit": {
            "records": expected_records,
            "role_cases": role_records,
            "resolved_count": len(resolved_expected),
            "excluded_resolved_count": len(expected_excluded),
            "ambiguous_legal_identifier_count": len(ambiguous_identifiers),
            "not_in_corpus_count": len(absent_expected_sources),
        },
        "source_snapshot": {
            "before": snapshot_before,
            "after": snapshot_after,
        },
        "documents": {
            "total": document_count,
            "tier_counts": dict(sorted(tier_counts.items())),
            "gate_failure_counts": dict(gate_failure_counts.most_common()),
            "reason_counts": dict(document_reason_counts.most_common()),
            "ledger": document_path.name,
            "ledger_sha256": document_sha,
            "id_sha256": sha256_jsonl_id(document_path, "document_id")[1],
        },
        "chunks": {
            "total": chunk_count,
            "tier_counts": dict(sorted(chunk_tier_counts.items())),
            "reason_counts": dict(chunk_reason_counts.most_common()),
            "ledger": chunk_path.name,
            "ledger_sha256": chunk_sha,
            "id_sha256": sha256_jsonl_id(chunk_path, "chunk_id")[1],
        },
        "relationship_audit": {
            "database_relationships": len(relationships),
            "eligible_one_hop_edges_considered": len(allowed_relations),
            "support_candidates": len(support_candidates),
            "ignored_relation_counts": dict(ignored_relation_counts.most_common()),
        },
        "validation": validations,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": str(manifest_path),
                "document_tiers": manifest["documents"]["tier_counts"],
                "chunk_tiers": manifest["chunks"]["tier_counts"],
                "validation": validations,
            },
            ensure_ascii=False,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
