"""Checksum-bound procedure/topic to legal-basis catalog governance.

Candidate generation is read-only and never makes a catalog runtime eligible.
Only a complete attestation from a reviewer independent of the builder can
materialize approved law/article bindings.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence
import unicodedata

from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact


CANDIDATE_SCHEMA_VERSION = "legal-procedure-topic-basis-candidate-v1"
ATTESTATION_SCHEMA_VERSION = "legal-basis-catalog-attestation-v1"
APPROVED_SCHEMA_VERSION = "legal-procedure-topic-basis-approved-v1"

_DOMAIN_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tu_phap_ho_tich", ("ho tich", "chung thuc", "nuoi con nuoi", "cong chung")),
    ("dat_dai_xay_dung", ("dat dai", "xay dung", "nha o", "quy hoach", "moi truong", "vat lieu xay dung", "ha tang ky thuat")),
    ("cu_tru_an_ninh", ("cu tru", "can cuoc", "an ninh", "xuat nhap canh", "dinh danh", "trat tu")),
    ("khieu_nai_to_cao", ("khieu nai", "to cao", "tiep cong dan", "xu ly vi pham", "xu phat")),
    ("an_sinh_y_te_giao_duc", ("giao duc", "y te", "kham benh", "duoc", "bao tro", "nguoi co cong", "tre em", "giam ngheo", "an toan thuc pham", "bao hiem", "phong benh")),
)

_LAW_NUMBER_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?/\d{4}/[A-ZÀ-ỸĐ0-9]+(?:-[A-ZÀ-ỸĐ0-9]+)*)",
    flags=re.IGNORECASE,
)


def canonical_sha256(value: Mapping[str, Any], hash_field: str = "attestation_sha256") -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop(hash_field, None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    """Return a streaming SHA-256 without loading large immutable indexes."""
    return _file_sha256(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fold(value: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(text.replace("đ", "d").split())


def _domain(detail: Mapping[str, Any]) -> str | None:
    categories = " ".join(
        str(item.get("name") or item.get("categoryName") or "")
        for item in detail.get("categoriesDetails") or []
        if isinstance(item, Mapping)
    )
    searchable = _fold(f"{categories} {detail.get('name') or ''}")
    for domain, markers in _DOMAIN_MARKERS:
        if any(marker in searchable for marker in markers):
            return domain
    return None


def _aliases(detail: Mapping[str, Any]) -> list[str]:
    values: list[str] = [str(detail.get("name") or "").strip()]
    keywords = detail.get("keywords") or []
    if isinstance(keywords, str):
        values.extend(part.strip() for part in keywords.replace(";", ",").split(","))
    elif isinstance(keywords, Sequence):
        values.extend(str(item).strip() for item in keywords if not isinstance(item, Mapping))
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _fold(value)
        if value and key not in seen:
            output.append(value)
            seen.add(key)
    return output


def _canonical_law_code(value: object) -> str:
    text = str(value or "").strip()
    match = _LAW_NUMBER_RE.search(text)
    canonical = match.group("number") if match else text
    canonical = re.sub(r"/NQ-QH(?P<term>\d+)$", r"/QH\g<term>", canonical, flags=re.IGNORECASE)
    canonical = re.sub(r"/NĐ-C$", "/NĐ-CP", canonical, flags=re.IGNORECASE)
    return canonical


def _resolved_basis(
    connection: sqlite3.Connection,
    *,
    procedure_id: str,
    code: str,
    title: str,
) -> dict[str, Any]:
    canonical_code = _canonical_law_code(code)
    normalized = normalize_exact(canonical_code)
    rows = connection.execute(
        """
        SELECT c.document_id,c.document_serving_state,c.source_url,
               c.effective_from,c.effective_to,c.article_number
          FROM exact_lookup e JOIN chunks c
            ON c.chunk_revision_id=e.chunk_revision_id
         WHERE e.key_kind='law_number' AND e.normalized_key=?
         ORDER BY CASE c.document_serving_state
                    WHEN 'current_retrievable' THEN 0
                    WHEN 'historical_only' THEN 1 ELSE 2 END,
                  c.effective_from DESC,c.document_id,c.chunk_index
        """,
        (normalized,),
    ).fetchall()
    primary = next(
        (
            row for row in rows
            if str(row["source_url"] or "").startswith(("https://vbpl.vn/", "https://vanban.chinhphu.vn/"))
        ),
        rows[0] if rows else None,
    )
    articles = sorted(
        {str(row["article_number"]).strip() for row in rows if str(row["article_number"] or "").strip()},
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    )
    basis_id = "basis-" + hashlib.sha256(
        f"{procedure_id}|{normalized}".encode("utf-8")
    ).hexdigest()[:20]
    basis = {
        "basis_id": basis_id,
        "law_number": canonical_code,
        "title": title or code,
        "official_url": (str(primary["source_url"] or "") or None) if primary else None,
        "document_id": int(primary["document_id"]) if primary else None,
        "serving_state": (str(primary["document_serving_state"] or "") or None) if primary else None,
        "effective_from": (str(primary["effective_from"] or "") or None) if primary else None,
        "effective_to": (str(primary["effective_to"] or "") or None) if primary else None,
        "article_numbers": [],
        "available_article_numbers": articles,
        "article_scope": "requires_legal_qa",
        "resolution_status": "resolved_official" if primary and primary["source_url"] else "unresolved_official_metadata",
    }
    basis["basis_sha256"] = canonical_sha256(basis, "basis_sha256")
    return basis


def build_candidate_catalog(
    snapshot_path: Path,
    sqlite_path: Path,
    *,
    builder_id: str,
) -> dict[str, Any]:
    if not builder_id.strip():
        raise ValueError("builder_id_required")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    if snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("details"), dict):
        raise ValueError("governance_snapshot_invalid")
    source_url = str(snapshot.get("source") or "")
    if "dichvucong.gov.vn" not in source_url:
        raise ValueError("official_governance_source_required")
    connection = sqlite3.connect(f"file:{sqlite_path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    entries: list[dict[str, Any]] = []
    try:
        for detail in snapshot["details"].values():
            if not isinstance(detail, Mapping) or not bool(detail.get("isWard")):
                continue
            if str(detail.get("state") or "").upper() not in {"ACTIVE", "UPDATED"}:
                continue
            domain = _domain(detail)
            procedure_id = str(detail.get("id") or "").strip()
            procedure_code = str(detail.get("code") or "").strip()
            procedure_name = str(detail.get("name") or "").strip()
            aliases = _aliases(detail)
            if not domain or not procedure_id or not procedure_code or not procedure_name or not aliases:
                continue
            bases: list[dict[str, Any]] = []
            seen_laws: set[str] = set()
            for raw in detail.get("legalBasisesDetails") or []:
                if not isinstance(raw, Mapping):
                    continue
                code = _canonical_law_code(raw.get("code"))
                normalized = normalize_exact(code)
                if not normalized or normalized in seen_laws:
                    continue
                seen_laws.add(normalized)
                bases.append(
                    _resolved_basis(
                        connection,
                        procedure_id=procedure_id,
                        code=code,
                        title=str(raw.get("name") or code).strip(),
                    )
                )
            entry = {
                "entry_id": "procedure-" + hashlib.sha256(procedure_id.encode("utf-8")).hexdigest()[:20],
                "procedure_id": procedure_id,
                "procedure_code": procedure_code,
                "procedure_name": procedure_name,
                "domain": domain,
                "aliases": aliases,
                "legal_bases": bases,
                "review_status": "pending_legal_qa",
            }
            entry["entry_sha256"] = canonical_sha256(entry, "entry_sha256")
            entries.append(entry)
    finally:
        connection.close()
    entries.sort(key=lambda item: (item["domain"], item["procedure_code"], item["entry_id"]))
    snapshot_sha = _file_sha256(snapshot_path)
    catalog = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "catalog_id": f"procedure-topic-basis-{snapshot_sha[:16]}",
        "builder_id": builder_id,
        "source": {
            "kind": "dvc_governance_snapshot",
            "official_url": source_url,
            "snapshot_sha256": snapshot_sha,
            "legal_as_of": str(snapshot.get("legal_as_of") or "")[:10],
            "sqlite_sha256": _file_sha256(sqlite_path),
        },
        "review_policy": {
            "independent_reviewer_required": True,
            "builder_may_review": False,
            "all_entries_require_decision": True,
        },
        "entries": entries,
        "independent_human_legal_qa_complete": False,
        "runtime_eligible": False,
    }
    catalog["catalog_sha256"] = canonical_sha256(catalog, "catalog_sha256")
    return catalog


def _verify_catalog(catalog: Mapping[str, Any]) -> None:
    if catalog.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("candidate_catalog_schema_mismatch")
    if str(catalog.get("catalog_sha256") or "") != canonical_sha256(catalog, "catalog_sha256"):
        raise ValueError("candidate_catalog_checksum_mismatch")
    if catalog.get("runtime_eligible") or catalog.get("independent_human_legal_qa_complete"):
        raise ValueError("candidate_catalog_must_be_unapproved")


def materialize_approved_catalog(
    catalog: Mapping[str, Any],
    attestation: Mapping[str, Any],
    *,
    snapshot_path: Path | None = None,
    sqlite_path: Path | None = None,
) -> dict[str, Any]:
    _verify_catalog(catalog)
    if attestation.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        raise ValueError("attestation_schema_mismatch")
    if str(attestation.get("attestation_sha256") or "") != canonical_sha256(attestation):
        raise ValueError("attestation_checksum_mismatch")
    if attestation.get("catalog_sha256") != catalog.get("catalog_sha256"):
        raise ValueError("catalog_checksum_mismatch")
    reviewer = str(attestation.get("reviewer_id") or "").strip()
    if (
        not reviewer
        or reviewer == str(catalog.get("builder_id") or "")
        or attestation.get("reviewer_role") != "independent_legal_qa"
        or attestation.get("independent_of_builder") is not True
        or not str(attestation.get("reviewer_organization") or "").strip()
    ):
        raise ValueError("reviewer_not_independent")
    source = catalog.get("source") or {}
    if snapshot_path and _file_sha256(snapshot_path) != source.get("snapshot_sha256"):
        raise ValueError("governance_snapshot_drift")
    if sqlite_path and _file_sha256(sqlite_path) != source.get("sqlite_sha256"):
        raise ValueError("sqlite_source_drift")

    entries = {str(entry["entry_id"]): entry for entry in catalog.get("entries") or []}
    decisions = list(attestation.get("decisions") or [])
    decision_ids = [str(item.get("entry_id") or "") for item in decisions]
    if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != set(entries):
        raise ValueError("decision_coverage_mismatch")
    if any(item.get("decision") == "NEEDS_CHANGES" for item in decisions):
        raise ValueError("attestation_contains_needs_changes")

    approved_entries: list[dict[str, Any]] = []
    for decision in decisions:
        entry = entries[str(decision["entry_id"])]
        if decision.get("decision") == "REJECT":
            continue
        known = {str(item["basis_id"]): item for item in entry.get("legal_bases") or []}
        approved_ids = list(decision.get("approved_basis_ids") or [])
        if not approved_ids or len(approved_ids) != len(set(approved_ids)) or not set(approved_ids) <= set(known):
            raise ValueError("approved_basis_mismatch")
        bindings = {
            str(item.get("basis_id") or ""): list(item.get("article_numbers") or [])
            for item in decision.get("approved_article_bindings") or []
        }
        if set(bindings) != set(approved_ids):
            raise ValueError("article_binding_coverage_mismatch")
        output_bases: list[dict[str, Any]] = []
        for basis_id in approved_ids:
            basis = copy.deepcopy(known[basis_id])
            if basis.get("resolution_status") != "resolved_official":
                raise ValueError("unresolved_basis_cannot_be_approved")
            articles = [str(value).strip() for value in bindings[basis_id] if str(value).strip()]
            if not articles or not set(articles) <= set(basis.get("available_article_numbers") or []):
                raise ValueError("approved_article_not_in_immutable_index")
            basis["article_numbers"] = articles
            basis["article_scope"] = "independent_legal_qa_approved"
            basis["basis_sha256"] = canonical_sha256(basis, "basis_sha256")
            output_bases.append(basis)
        output_entry = copy.deepcopy(entry)
        output_entry["legal_bases"] = output_bases
        output_entry["review_status"] = "approved"
        output_entry["entry_sha256"] = canonical_sha256(output_entry, "entry_sha256")
        approved_entries.append(output_entry)

    if not approved_entries:
        raise ValueError("no_approved_catalog_entries")
    approved = {
        "schema_version": APPROVED_SCHEMA_VERSION,
        "catalog_id": catalog["catalog_id"],
        "candidate_catalog_sha256": catalog["catalog_sha256"],
        "source": copy.deepcopy(source),
        "reviewer": {
            "reviewer_id": reviewer,
            "reviewer_role": attestation["reviewer_role"],
            "reviewer_organization": attestation["reviewer_organization"],
            "reviewed_at": attestation["reviewed_at"],
            "attestation_sha256": attestation["attestation_sha256"],
        },
        "entries": approved_entries,
        "independent_human_legal_qa_complete": True,
        "runtime_eligible": True,
    }
    approved["catalog_sha256"] = canonical_sha256(approved, "catalog_sha256")
    return approved


__all__ = [
    "APPROVED_SCHEMA_VERSION",
    "ATTESTATION_SCHEMA_VERSION",
    "CANDIDATE_SCHEMA_VERSION",
    "build_candidate_catalog",
    "canonical_sha256",
    "file_sha256",
    "materialize_approved_catalog",
]
