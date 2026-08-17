"""DB-3 coverage comparison for the Lê Chân shadow serving scope.

This command is read-only with respect to PostgreSQL, Chroma, imported rows and
the active collection. It produces privacy-safe source/procedure/form ledgers
and a legal-review packet. A technical comparison can complete while the
overall status remains ``awaiting_legal_review``.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse
from typing import Any

from sqlalchemy import create_engine, text

try:
    from scripts.build_lechan_shadow_scope import (
        EXPECTED_SOURCE_ALIASES,
        active_pointer,
        database_url,
        extract_legal_number,
        git_commit,
        law_number_key,
        normalize,
        safe_database_target,
        sha256_file,
        source_host,
        table_id_snapshot,
    )
except ModuleNotFoundError:  # Direct execution
    from build_lechan_shadow_scope import (  # type: ignore[no-redef]
        EXPECTED_SOURCE_ALIASES,
        active_pointer,
        database_url,
        extract_legal_number,
        git_commit,
        law_number_key,
        normalize,
        safe_database_target,
        sha256_file,
        source_host,
        table_id_snapshot,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB2_MANIFEST = (
    ROOT / "reports" / "feature005" / f"db2-shadow-{date.today():%Y%m%d}"
    / "manifest.json"
)
DEFAULT_OUTPUT = (
    ROOT / "reports" / "feature005" / f"db3-coverage-{date.today():%Y%m%d}"
)
DEFAULT_CHROMA_PATH = Path(r"J:\legal-chatbot-data\chroma_store")

TRUSTED_FORM_HOSTS = {
    "haiphong.gov.vn",
    "vanban.bocongan.gov.vn",
}

DOMAIN_COMPATIBILITY = {
    "ho_tich_chung_thuc": {"ho_tich_chung_thuc", "ho_tich"},
    "cu_tru_an_ninh": {"cu_tru_an_ninh", "cu_tru"},
    "dat_dai_xay_dung": {
        "dat_dai_xay_dung",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
        "trat_tu_do_thi",
    },
    "khieu_nai_to_cao_xu_phat": {
        "khieu_nai_to_cao_xu_phat",
        "khieu_nai_to_cao",
        "noi_vu_hanh_chinh",
    },
    "an_sinh_y_te_giao_duc": {
        "an_sinh_y_te_giao_duc",
        "an_sinh_y_te",
        "giao_duc_van_hoa",
    },
}

FORM_STOPWORDS = {
    "to",
    "khai",
    "don",
    "giay",
    "mau",
    "so",
    "theo",
    "ve",
    "viec",
    "de",
    "nghi",
    "thu",
    "tuc",
}


def provision_number(value: object) -> str | None:
    match = re.fullmatch(r"\s*[ĐDđd]iều\s+([0-9]+[a-zA-Z]?)\s*", str(value or ""))
    return match.group(1).casefold() if match else None


def provision_numbers_in_text(value: object) -> list[str]:
    return [
        item.casefold()
        for item in re.findall(r"[ĐDđd]iều\s+([0-9]+[a-zA-Z]?)", str(value or ""))
    ]


def expected_law_number(value: object) -> str | None:
    normalized = normalize(value)
    for alias, number in EXPECTED_SOURCE_ALIASES.items():
        if alias in normalized:
            return law_number_key(number)
    return extract_legal_number(value)


def corpus_law_number_matches(
    expected_number: str | None,
    documents: dict[int, dict[str, Any]],
) -> int:
    if not expected_number:
        return 0
    if re.fullmatch(r"\d{1,4}/\d{4}", expected_number):
        return sum(
            law_number_key(item.get("law_number")).startswith(expected_number + "/")
            for item in documents.values()
        )
    return sum(
        law_number_key(item.get("law_number")) == expected_number
        for item in documents.values()
    )


def _local_file(record: dict[str, Any], root: Path) -> Path | None:
    raw = (
        record.get("priority_path")
        or record.get("local_path")
        or record.get("source_package_path")
    )
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_absolute() else root / path


def _trusted_form_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in TRUSTED_FORM_HOSTS)


def form_hard_gate(
    record: dict[str, Any],
    *,
    root: Path = ROOT,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if record.get("review_status") != "approved":
        reasons.append("catalog_not_approved")
    host = source_host(record.get("source_page_url"))
    if not _trusted_form_host(host):
        reasons.append("unapproved_form_source_host")
    local_file = _local_file(record, root)
    if local_file is None or not local_file.is_file() or local_file.stat().st_size <= 0:
        reasons.append("official_form_file_missing")
    if record.get("legal_status") != "admin_reviewed":
        reasons.append("effectivity_not_approved")
    domain = normalize(record.get("domain"))
    if not domain or domain == "unknown":
        reasons.append("form_domain_missing")
    if record.get("title_quality") == "needs_title_review":
        reasons.append("form_title_needs_review")
    if record.get("effectivity_flags"):
        reasons.append("form_effectivity_flagged")
    return not reasons, sorted(set(reasons))


def _form_codes(value: object) -> set[str]:
    normalized = normalize(value).upper()
    return set(
        re.findall(
            r"\b(?:CT[0-9]{2}|[0-9]{1,2}\s*DK|MAU\s*SO\s*[0-9]{1,3})\b",
            normalized,
        )
    )


def _domain_compatible(expected: object, candidate: object) -> bool:
    expected_domain = normalize(expected).replace(" ", "_")
    candidate_domain = normalize(candidate).replace(" ", "_")
    allowed = DOMAIN_COMPATIBILITY.get(expected_domain, {expected_domain})
    return candidate_domain in allowed


def strict_form_match(
    expected: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not _domain_compatible(expected.get("domain"), candidate.get("domain")):
        reasons.append("form_domain_mismatch")
    procedure_match = (
        normalize(expected.get("procedure_id"))
        and normalize(expected.get("procedure_id"))
        == normalize(candidate.get("procedure_id"))
    )
    expected_title = normalize(expected.get("form_title"))
    candidate_title = normalize(candidate.get("form_title"))
    exact_title = bool(expected_title and expected_title == candidate_title)
    codes_match = bool(
        _form_codes(expected.get("form_title"))
        & _form_codes(candidate.get("form_title"))
    )
    expected_tokens = set(expected_title.split()) - FORM_STOPWORDS
    candidate_tokens = set(candidate_title.split()) - FORM_STOPWORDS
    overlap = (
        len(expected_tokens & candidate_tokens) / len(expected_tokens)
        if expected_tokens
        else 0.0
    )
    title_match = exact_title or codes_match or overlap >= 0.75
    if not procedure_match:
        reasons.append("procedure_id_mismatch")
    if not title_match:
        reasons.append("form_identity_mismatch")
    return not reasons, sorted(set(reasons))


def review_approval_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(item.get("expert_review_status") or "missing") for item in records)
    complete = all(
        item.get("expert_review_status") == "approved"
        and bool(item.get("expert_name"))
        and bool(item.get("reviewed_at"))
        for item in records
    )
    return {
        "approved": bool(records) and complete,
        "records": len(records),
        "status_counts": dict(status_counts),
        "named_reviewers": sum(bool(item.get("expert_name")) for item in records),
        "reviewed_at_count": sum(bool(item.get("reviewed_at")) for item in records),
        "expected_source_contracts": sum(
            bool(
                item.get("expected_documents")
                or item.get("mandatory_documents")
                or item.get("machine_proposal", {}).get("expected_citations")
            )
            for item in records
        ),
        "forbidden_source_contracts": sum(
            bool(item.get("forbidden_documents")) for item in records
        ),
    }


def _load_jsonl(path: Path, key: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            result[int(row[key])] = row
    return result


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            encoded = (
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            handle.write(encoded.decode("utf-8"))
            digest.update(encoded)
    return {
        "file": path.name,
        "count": len(rows),
        "sha256": digest.hexdigest(),
    }


def _input_file(path: Path) -> dict[str, Any]:
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256_file(path)}


def _document_snapshot(connection) -> dict[str, Any]:
    return {
        "documents": table_id_snapshot(connection, "legal_documents"),
        "articles": table_id_snapshot(connection, "legal_articles"),
        "chunks": table_id_snapshot(connection, "legal_article_chunks"),
    }


def _document_checks(
    document_id: int,
    shadow_documents: dict[int, dict[str, Any]],
    database_documents: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    shadow = shadow_documents.get(document_id)
    document = database_documents.get(document_id)
    hard_gate = dict(shadow.get("hard_gate") or {}) if shadow else {}
    return {
        "document_present": document is not None,
        "effectivity": bool(hard_gate.get("effectivity")),
        "scope": bool(hard_gate.get("scope")),
        "hierarchy": bool(hard_gate.get("hierarchy")),
        "article_chunk": bool(hard_gate.get("article_chunk")),
        "tier": shadow.get("tier") if shadow else None,
        "tier_correct": bool(shadow and shadow.get("tier") == "primary"),
    }


def _technical_source_outcome(checks: dict[str, Any]) -> tuple[str, str]:
    if not checks["document_present"]:
        return "VERIFIED_DATA_GAP", "expected_document_not_in_corpus"
    failed = [
        key
        for key in ("effectivity", "scope", "hierarchy", "article_chunk")
        if not checks[key]
    ]
    if failed:
        return "RULE_OR_METADATA_FAILURE", "expected_document_failed_" + failed[0]
    if not checks["tier_correct"]:
        return "RULE_OR_TIER_FAILURE", "expected_document_not_primary"
    return "AVAILABLE_CORRECTLY_TIERED", "expected_document_primary"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db2-manifest", type=Path, default=DEFAULT_DB2_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    db2_path = args.db2_manifest.resolve()
    db2 = json.loads(db2_path.read_text(encoding="utf-8"))
    shadow_documents = _load_jsonl(
        db2_path.parent / db2["documents"]["ledger"], "document_id"
    )

    golden_path = ROOT / "notebook_data" / "legal-golden-set.json"
    roles_path = ROOT / "tests" / "fixtures" / "feature005_role_matrix.json"
    priority_path = ROOT / "notebook_data" / "forms" / "priority_200_forms.json"
    forms_path = ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json"
    review_path = ROOT / "notebook_data" / "legal-golden-expert-review.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    roles = json.loads(roles_path.read_text(encoding="utf-8"))
    priority = json.loads(priority_path.read_text(encoding="utf-8"))
    forms = json.loads(forms_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))

    url = database_url()
    pointer_before = active_pointer(args.chroma_path)
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        source_before = _document_snapshot(connection)
        database_documents = {
            int(row["id"]): dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT id, law_number, title, document_type, status,
                           effective_date, expired_date, scope, sector,
                           source_url
                    FROM legal_documents
                    ORDER BY id
                    """
                )
            ).mappings()
        }
        articles_by_document: dict[int, set[str]] = defaultdict(set)
        article_counts: Counter[int] = Counter()
        for row in connection.execute(
            text(
                """
                SELECT document_id, article_number
                FROM legal_articles
                ORDER BY document_id, id
                """
            )
        ).mappings():
            document_id = int(row["document_id"])
            article_counts[document_id] += 1
            article_number = normalize(row["article_number"]).replace("dieu ", "")
            if article_number:
                articles_by_document[document_id].add(article_number)
        transaction.rollback()

    db2_expected = {
        (item["set"], item["case_id"], item["reference_sha256"]): item
        for item in db2["expected_source_audit"]["records"]
    }
    expected_rows: list[dict[str, Any]] = []
    source_data_gaps: list[dict[str, Any]] = []
    available_source_failures: list[dict[str, Any]] = []

    for case in golden.get("questions") or []:
        current_document_id: int | None = None
        for reference in case.get("expected_citations") or []:
            reference_hash = hashlib.sha256(str(reference).encode("utf-8")).hexdigest()
            article_number = provision_number(reference)
            if article_number:
                if current_document_id is None:
                    row = {
                        "set": "golden_167",
                        "case_id": case.get("id"),
                        "reference_sha256": reference_hash,
                        "kind": "provision",
                        "document_id": None,
                        "provision": article_number,
                        "outcome": "EXPECTED_CONTRACT_GAP",
                        "reason_code": "provision_without_document_locator",
                    }
                else:
                    present = article_number in articles_by_document.get(
                        current_document_id, set()
                    )
                    document_checks = _document_checks(
                        current_document_id,
                        shadow_documents,
                        database_documents,
                    )
                    document_outcome, document_reason = _technical_source_outcome(
                        document_checks
                    )
                    if not present:
                        outcome = "VERIFIED_DATA_GAP"
                        reason_code = "expected_provision_not_in_corpus_document"
                    elif document_outcome != "AVAILABLE_CORRECTLY_TIERED":
                        outcome = "RULE_OR_TIER_FAILURE"
                        reason_code = document_reason
                    else:
                        outcome = "AVAILABLE_CORRECTLY_TIERED"
                        reason_code = "expected_provision_present"
                    row = {
                        "set": "golden_167",
                        "case_id": case.get("id"),
                        "reference_sha256": reference_hash,
                        "kind": "provision",
                        "document_id": current_document_id,
                        "provision": article_number,
                        "provision_present": present,
                        "article_count_checked": article_counts[current_document_id],
                        "document_checks": document_checks,
                        "outcome": outcome,
                        "reason_code": reason_code,
                    }
                    if not present:
                        source_data_gaps.append(row)
                    elif outcome != "AVAILABLE_CORRECTLY_TIERED":
                        available_source_failures.append(row)
                expected_rows.append(row)
                continue

            resolution = db2_expected.get(
                ("golden_167", case.get("id"), reference_hash)
            )
            if not resolution or resolution.get("resolution") == "non_document_locator":
                expected_rows.append(
                    {
                        "set": "golden_167",
                        "case_id": case.get("id"),
                        "reference_sha256": reference_hash,
                        "kind": "non_document_locator",
                        "outcome": "NOT_A_DOCUMENT_EXPECTATION",
                        "reason_code": "generic_or_non_document_locator",
                    }
                )
                continue
            document_id = resolution.get("document_id")
            if document_id is None:
                expected_number = expected_law_number(reference)
                corpus_matches = corpus_law_number_matches(
                    expected_number, database_documents
                )
                row = {
                    "set": "golden_167",
                    "case_id": case.get("id"),
                    "reference_sha256": reference_hash,
                    "kind": "document",
                    "document_id": None,
                    "resolution": resolution.get("resolution"),
                    "expected_law_number": expected_number,
                    "outcome": "VERIFIED_DATA_GAP",
                    "reason_code": "expected_document_not_in_corpus",
                    "corpus_match_count": corpus_matches,
                }
                source_data_gaps.append(row)
                expected_rows.append(row)
                current_document_id = None
                continue
            current_document_id = int(document_id)
            checks = _document_checks(
                current_document_id, shadow_documents, database_documents
            )
            outcome, reason_code = _technical_source_outcome(checks)
            row = {
                "set": "golden_167",
                "case_id": case.get("id"),
                "reference_sha256": reference_hash,
                "kind": "document",
                "document_id": current_document_id,
                "law_number": database_documents[current_document_id].get("law_number"),
                "checks": checks,
                "outcome": outcome,
                "reason_code": reason_code,
            }
            if outcome == "VERIFIED_DATA_GAP":
                source_data_gaps.append(row)
            elif outcome != "AVAILABLE_CORRECTLY_TIERED":
                available_source_failures.append(row)
            expected_rows.append(row)

    role_contracts = {
        item["case_id"]: item for item in db2["expected_source_audit"]["role_cases"]
    }
    for case in roles.get("cases") or []:
        contract = role_contracts.get(case.get("id")) or {}
        document_ids = [int(value) for value in contract.get("resolved_document_ids") or []]
        if not document_ids:
            expected_rows.append(
                {
                    "set": "role_9",
                    "case_id": case.get("id"),
                    "kind": "document_contract",
                    "outcome": "EXPECTED_CONTRACT_GAP",
                    "reason_code": "role_case_expected_sources_not_approved",
                }
            )
            continue
        for document_id in document_ids:
            checks = _document_checks(document_id, shadow_documents, database_documents)
            outcome, reason_code = _technical_source_outcome(checks)
            row = {
                "set": "role_9",
                "case_id": case.get("id"),
                "kind": "document",
                "document_id": document_id,
                "law_number": database_documents[document_id].get("law_number"),
                "checks": checks,
                "outcome": outcome,
                "reason_code": reason_code,
            }
            if outcome != "AVAILABLE_CORRECTLY_TIERED":
                available_source_failures.append(row)
            expected_rows.append(row)
            for article_number in provision_numbers_in_text(case.get("question")):
                present = article_number in articles_by_document.get(document_id, set())
                document_checks = _document_checks(
                    document_id, shadow_documents, database_documents
                )
                document_outcome, document_reason = _technical_source_outcome(
                    document_checks
                )
                if not present:
                    provision_outcome = "VERIFIED_DATA_GAP"
                    provision_reason = "expected_provision_not_in_corpus_document"
                elif document_outcome != "AVAILABLE_CORRECTLY_TIERED":
                    provision_outcome = "RULE_OR_TIER_FAILURE"
                    provision_reason = document_reason
                else:
                    provision_outcome = "AVAILABLE_CORRECTLY_TIERED"
                    provision_reason = "expected_provision_present"
                provision_row = {
                    "set": "role_9",
                    "case_id": case.get("id"),
                    "kind": "provision",
                    "document_id": document_id,
                    "provision": article_number,
                    "provision_present": present,
                    "article_count_checked": article_counts[document_id],
                    "document_checks": document_checks,
                    "outcome": provision_outcome,
                    "reason_code": provision_reason,
                }
                if not present:
                    source_data_gaps.append(provision_row)
                elif provision_outcome != "AVAILABLE_CORRECTLY_TIERED":
                    available_source_failures.append(provision_row)
                expected_rows.append(provision_row)

    approved_forms = [
        item for item in forms.get("forms") or []
        if item.get("review_status") == "approved"
    ]
    form_audit_rows: list[dict[str, Any]] = []
    form_by_id: dict[str, dict[str, Any]] = {}
    for item in approved_forms:
        passed, reasons = form_hard_gate(item)
        row = {
            "form_id": item.get("id"),
            "procedure_id_hash": (
                hashlib.sha256(str(item.get("procedure_id")).encode("utf-8")).hexdigest()
                if item.get("procedure_id")
                else None
            ),
            "hard_gate_pass": passed,
            "reason_codes": reasons or ["approved_form_hard_gate_pass"],
        }
        form_audit_rows.append(row)
        form_by_id[str(item.get("id"))] = item

    procedure_rows: list[dict[str, Any]] = []
    procedure_gap_counts: Counter[str] = Counter()
    for expected in priority.get("forms") or []:
        candidates: list[tuple[dict[str, Any], list[str], list[str]]] = []
        for candidate in approved_forms:
            same_procedure = (
                normalize(expected.get("procedure_id"))
                and normalize(expected.get("procedure_id"))
                == normalize(candidate.get("procedure_id"))
            )
            same_title = (
                normalize(expected.get("form_title"))
                and normalize(expected.get("form_title"))
                == normalize(candidate.get("form_title"))
            )
            code_overlap = bool(
                _form_codes(expected.get("form_title"))
                & _form_codes(candidate.get("form_title"))
            )
            if not (same_procedure or same_title or code_overlap):
                continue
            strict_pass, match_reasons = strict_form_match(expected, candidate)
            hard_pass, hard_reasons = form_hard_gate(candidate)
            candidates.append(
                (
                    candidate,
                    [] if strict_pass else match_reasons,
                    [] if hard_pass else hard_reasons,
                )
            )
        eligible = [
            candidate
            for candidate, match_reasons, hard_reasons in candidates
            if not match_reasons and not hard_reasons
        ]
        if eligible:
            outcome = "AVAILABLE_VERIFIED"
            reason = "approved_form_exact_identity_and_hard_gate"
        elif not candidates:
            outcome = "VERIFIED_DATA_GAP"
            reason = "approved_form_candidate_not_found"
        else:
            candidate_reasons = [
                reason
                for _, match_reasons, hard_reasons in candidates
                for reason in match_reasons + hard_reasons
            ]
            outcome = "VERIFIED_DATA_GAP"
            reason = Counter(candidate_reasons).most_common(1)[0][0]
        if outcome == "VERIFIED_DATA_GAP":
            procedure_gap_counts[reason] += 1
        procedure_rows.append(
            {
                "priority_form_id": expected.get("id"),
                "procedure_id": expected.get("procedure_id"),
                "domain": expected.get("domain"),
                "outcome": outcome,
                "reason_code": reason,
                "candidate_count": len(candidates),
                "eligible_form_ids": sorted(
                    str(item.get("id")) for item in eligible
                ),
            }
        )

    review_state = review_approval_state(review.get("records") or [])
    forbidden_rows = [
        {
            "review_id": item.get("review_id"),
            "forbidden_document_count": len(item.get("forbidden_documents") or []),
            "forbidden_authority_count": len(item.get("forbidden_authority") or []),
            "review_status": item.get("expert_review_status"),
        }
        for item in review.get("records") or []
    ]

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        source_after = _document_snapshot(connection)
        transaction.rollback()
    engine.dispose()
    pointer_after = active_pointer(args.chroma_path)

    source_ledger = _write_jsonl(output_dir / "expected-sources.jsonl", expected_rows)
    procedure_ledger = _write_jsonl(
        output_dir / "procedure-coverage.jsonl", procedure_rows
    )
    form_ledger = _write_jsonl(
        output_dir / "approved-form-audit.jsonl", form_audit_rows
    )
    technical_checks = {
        "db2_manifest_verified": db2.get("status") == "verified",
        "all_available_expected_sources_primary": not available_source_failures,
        "all_expected_rows_classified": all(
            bool(item.get("outcome")) and bool(item.get("reason_code"))
            for item in expected_rows
        ),
        "all_priority_forms_classified": (
            len(procedure_rows) == 202
            and all(item.get("outcome") for item in procedure_rows)
        ),
        "all_approved_catalog_records_audited": (
            len(form_audit_rows) == len(approved_forms)
        ),
        "source_database_unchanged": source_before == source_after,
        "active_collection_pointer_unchanged": pointer_before == pointer_after,
        "frequency_based_exclusion_used": any(
            any(
                token in normalize(reason)
                for token in ("low frequency", "rarely asked", "it duoc hoi")
            )
            for item in shadow_documents.values()
            for reason in item.get("reason_codes") or []
        ),
        "model_metadata_inference_used": False,
        "corpus_rows_modified": False,
    }
    technical_pass = (
        technical_checks["db2_manifest_verified"]
        and technical_checks["all_available_expected_sources_primary"]
        and technical_checks["all_expected_rows_classified"]
        and technical_checks["all_priority_forms_classified"]
        and technical_checks["all_approved_catalog_records_audited"]
        and technical_checks["source_database_unchanged"]
        and technical_checks["active_collection_pointer_unchanged"]
        and not technical_checks["frequency_based_exclusion_used"]
        and not technical_checks["model_metadata_inference_used"]
        and not technical_checks["corpus_rows_modified"]
    )
    if not technical_pass:
        status = "verification_failed"
    elif not review_state["approved"]:
        status = "awaiting_legal_review"
    else:
        status = "verified"

    review_packet = {
        "schema_version": "feature005-db3-legal-review-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": db2["legal_as_of"],
        "status": "pending",
        "instructions": {
            "approve_expected_sources": True,
            "approve_forbidden_sources": True,
            "do_not_infer_missing_sources": True,
            "reviewer_name": None,
            "reviewed_at": None,
            "decision": None,
        },
        "counts": {
            "expected_source_rows": len(expected_rows),
            "verified_data_gaps": len(source_data_gaps),
            "role_contract_gaps": sum(
                item["reason_code"] == "role_case_expected_sources_not_approved"
                for item in expected_rows
            ),
            "priority_form_rows": len(procedure_rows),
            "priority_form_verified_gaps": sum(
                item["outcome"] == "VERIFIED_DATA_GAP"
                for item in procedure_rows
            ),
            "forbidden_review_rows": len(forbidden_rows),
        },
        "expected_source_ledger": source_ledger,
        "procedure_ledger": procedure_ledger,
        "forbidden_source_review": {
            "existing_contracts": review_state["forbidden_source_contracts"],
            "rows": forbidden_rows,
        },
    }
    review_packet_path = output_dir / "legal-review-packet.json"
    review_packet_path.write_text(
        json.dumps(review_packet, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "schema_version": "feature005-db3-coverage-v1",
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": db2["legal_as_of"],
        "git_commit": git_commit(),
        "runtime_target": safe_database_target(url),
        "mode": "shadow_read_only",
        "inputs": [
            _input_file(path)
            for path in (
                db2_path,
                golden_path,
                roles_path,
                priority_path,
                forms_path,
                review_path,
            )
        ],
        "source_coverage": {
            "rows": len(expected_rows),
            "available_correctly_tiered": sum(
                item["outcome"] == "AVAILABLE_CORRECTLY_TIERED"
                for item in expected_rows
            ),
            "verified_data_gaps": len(source_data_gaps),
            "contract_gaps": sum(
                item["outcome"] == "EXPECTED_CONTRACT_GAP"
                for item in expected_rows
            ),
            "available_source_rule_or_tier_failures": len(
                available_source_failures
            ),
            "outcome_counts": dict(
                Counter(item["outcome"] for item in expected_rows)
            ),
            "reason_counts": dict(
                Counter(item["reason_code"] for item in expected_rows)
            ),
            "ledger": source_ledger,
        },
        "priority_procedure_coverage": {
            "total": len(procedure_rows),
            "available_verified": sum(
                item["outcome"] == "AVAILABLE_VERIFIED"
                for item in procedure_rows
            ),
            "verified_data_gaps": sum(
                item["outcome"] == "VERIFIED_DATA_GAP"
                for item in procedure_rows
            ),
            "gap_reason_counts": dict(procedure_gap_counts),
            "ledger": procedure_ledger,
        },
        "approved_form_catalog": {
            "approved_records": len(approved_forms),
            "hard_gate_pass": sum(
                item["hard_gate_pass"] for item in form_audit_rows
            ),
            "hard_gate_fail": sum(
                not item["hard_gate_pass"] for item in form_audit_rows
            ),
            "reason_counts": dict(
                Counter(
                    reason
                    for item in form_audit_rows
                    for reason in item["reason_codes"]
                )
            ),
            "ledger": form_ledger,
        },
        "verified_data_gap_evidence": {
            "source_or_provision_gaps": source_data_gaps,
            "priority_form_gap_count": sum(procedure_gap_counts.values()),
        },
        "legal_review": {
            **review_state,
            "packet": review_packet_path.name,
            "required_before_pass": True,
        },
        "source_snapshot": {"before": source_before, "after": source_after},
        "active_collection_pointer": {
            "before": pointer_before,
            "after": pointer_after,
        },
        "technical_validation": technical_checks,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": status,
                "manifest": str(manifest_path),
                "technical_pass": technical_pass,
                "available_source_failures": len(available_source_failures),
                "verified_data_gaps": len(source_data_gaps),
                "priority_form_gaps": sum(procedure_gap_counts.values()),
                "legal_review": review_state,
            },
            ensure_ascii=False,
        )
    )
    return 2 if status == "verification_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
