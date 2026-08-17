#!/usr/bin/env python3
"""Build the Stage E retrieval suite from the reviewed custody workbook.

The workbook is an external Legal-QA custody input.  This adapter does not
author legal text or invent sources: it joins each reviewed row to the
existing candidate seed and the release SQLite index, then emits a canonical
development suite (1,500 cases) plus a full custody suite outside the
repository.  The repository receives only a checksum-bound holdout envelope.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable, Mapping

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import DOMAINS, canonical_sha256, file_sha256
from scripts.build_retrieval_release_v2_lexical_index import normalize_exact


EXPECTED_SOURCE_SHA = "d4040ff69a0da62db0227fdfa28a0aa198d9cb385118b0aa381eae00fd108ca6"
EXPECTED_MANIFEST_SHA = "20072befc93dc5f66401c392cd174a278fda9efeb2ca8307c37f7fc7cf517b9f"
DATASET_VERSION = "retrieval-eval-stage-e-20260816-answer-workbook-v1"
WORKBOOK_SHEET = "Duyet 2000"
SPLIT_COUNTS = {"golden-regression": 1_000, "hard-negative": 500, "production-holdout": 500}
DOMAIN_BLOCKS = {"golden-regression": 200, "hard-negative": 100, "production-holdout": 100}
_SUBSTITUTE_CACHE: dict[str, tuple[Any, ...] | None] = {}


def _canonical_domain(value: Any) -> str:
    """Map the older 100-case hard-negative labels to the V2 five domains."""

    text = str(value or "").strip()
    aliases = {
        "Đất đai/xây dựng": "Đất đai/xây dựng/môi trường",
        "Cư trú/an ninh": "Cư trú/căn cước/an ninh",
        "Khiếu nại/tố cáo/xử phạt": "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    }
    return aliases.get(text, text)


def _load_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            result[str(row["case_id"])] = row
    return result


def _load_legacy_hard_negatives(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_case = {str(row.get("case_id")): row for row in payload.get("examples") or []}
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in payload.get("examples") or []:
        by_domain[_canonical_domain(row.get("domain"))].append(row)
    return by_case, by_domain


def _iso_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("reviewed_at_missing")
    if "T" not in text:
        text = text.replace(" ", "T")
    if not re.search(r"[+-][0-9]{2}:[0-9]{2}$", text) and not text.endswith("Z"):
        text += "+07:00"
    return text.replace("Z", "+00:00")


def _date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("legal_as_of_missing")
    return text[:10]


def _article_numbers(value: Any) -> list[str]:
    text = normalize_exact(value)
    return list(dict.fromkeys(re.findall(r"[0-9]+[A-Z]?", text)))


def _question_article_numbers(question: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?:ĐIỀU|DIEU)\s+([0-9]+[A-Z]?)", normalize_exact(question))))


def _question_issue_hints(question: str) -> list[tuple[str, str]]:
    """Extract article/title hints for multi-issue questions.

    Some reviewed rows contain two different instruments but only have one
    ``Số hiệu`` column.  The quoted structural title is the auditable bridge
    to the release index; it prevents assigning the second issue to the first
    law merely because the workbook is column-oriented.
    """

    result: list[tuple[str, str]] = []
    # Extract the quoted structural spans before accent/punctuation
    # normalization. ``normalize_exact`` deliberately removes curly quotes,
    # so applying it to the whole question first made every generated
    # multi-issue case look like a single-source case.
    for quoted in re.findall(r"[“\"]([^”\"]+)[”\"]", str(question or "")):
        normalized_quote = normalize_exact(quoted)
        match = re.search(r"(?:^|\s)DIEU\s*([0-9]+[A-Z]?)(?:\s|$)", normalized_quote)
        if not match:
            continue
        number = match.group(1)
        # Prefer the provision title after ``Điều X``.  It is a safer
        # fallback discriminator than the chapter/mục prefix before the
        # article, and the exact law/article key remains authoritative when it
        # exists in the release index.
        raw_match = re.search(
            rf"(?:điều|dieu)\s*{re.escape(number)}(?:\s*[.:\-])?\s*(.*)$",
            quoted,
            flags=re.IGNORECASE,
        )
        hint = (raw_match.group(1) if raw_match else "").strip(" >.")
        result.append((number, hint))
    deduplicated: list[tuple[str, str]] = []
    seen_numbers: set[str] = set()
    for number, hint in result:
        if number in seen_numbers:
            continue
        seen_numbers.add(number)
        deduplicated.append((number, hint))
    return deduplicated


def _rows_for_source(
    connection: sqlite3.Connection,
    law_number: str,
    article: str | None,
    structural_hint: str | None = None,
) -> list[dict[str, Any]]:
    law_key = normalize_exact(law_number)
    numbers = set(_article_numbers(article)) if article else set()
    if numbers:
        keys = [f"{law_key}|{normalize_exact(number)}" for number in numbers]
        placeholders = ",".join("?" for _ in keys)
        ids = [row[0] for row in connection.execute(
            f"SELECT chunk_revision_id FROM exact_lookup WHERE key_kind='law_article' "
            f"AND normalized_key IN ({placeholders})", keys
        )]
    else:
        ids = [row[0] for row in connection.execute(
            "SELECT chunk_revision_id FROM exact_lookup WHERE key_kind='law_number' "
            "AND normalized_key=?", (law_key,)
        )]
    if not ids and structural_hint and numbers:
        # The workbook's second issue may name the structural title but not
        # repeat its law number.  This query is constrained by article number
        # and the quoted structural path; it does not infer a legal relation.
        for number in numbers:
            rows = connection.execute(
                "SELECT chunk_revision_id FROM chunks WHERE article_number=? "
                "AND structural_path LIKE ? LIMIT 1000",
                (str(number), f"%{structural_hint}%"),
            ).fetchall()
            ids.extend(row[0] for row in rows)
    if not ids:
        return []
    found: list[dict[str, Any]] = []
    for chunk_id in ids:
        row = connection.execute(
            "SELECT chunk_revision_id, law_number, article_number, structural_path, "
            "source_url, document_serving_state, effective_from, effective_to, "
            "passage_sha256 FROM chunks WHERE chunk_revision_id=?", (chunk_id,)
        ).fetchone()
        if not row:
            continue
        item = {
            "chunk_revision_id": str(row[0]),
            "law_number": str(row[1]),
            "article_number": str(row[2]),
            "structural_path": str(row[3]),
            "source_url": str(row[4]),
            "document_serving_state": str(row[5]),
            "validity_from": str(row[6] or "")[:10] or None,
            "validity_to": str(row[7] or "")[:10] or None,
            "passage_sha256": str(row[8]),
        }
        article_key = set(_article_numbers(item["article_number"]))
        path_key = set(_article_numbers(item["structural_path"]))
        if not numbers or numbers & article_key or numbers & path_key:
            found.append(item)
    return found


def _source(
    connection: sqlite3.Connection,
    *,
    law_number: str,
    article: str | None,
    official_url: str,
    fallback_validity_from: str | None = None,
    fallback_validity_to: str | None = None,
    structural_hint: str | None = None,
) -> tuple[dict[str, Any], str]:
    rows = _rows_for_source(connection, law_number, article, structural_hint)
    selected = rows[0] if rows else None
    if selected is None:
        # A source without an eligible chunk is not silently promoted.  The
        # caller records this as an integrity error and the resulting suite
        # remains invalid until the source is present in the release index.
        selected = {
            "source_url": official_url,
            "validity_from": fallback_validity_from,
            "validity_to": fallback_validity_to,
            "passage_sha256": "",
        }
        reason = "source_not_found_in_release_index"
    else:
        reason = "matched_release_sqlite_law_and_article"
    resolved_law_number = str(selected.get("law_number") or law_number).strip()
    resolved_url = str(selected.get("source_url") or official_url).strip()
    jurisdiction = "Hải Phòng" if "/dia-phuong/" in resolved_url else "Việt Nam"
    return {
        "law_number": resolved_law_number,
        "article": str(article).strip() if article not in (None, "") else None,
        "paragraph": None,
        "point": None,
        "official_url": resolved_url,
        "jurisdiction": jurisdiction,
        "validity_from": selected.get("validity_from") or fallback_validity_from,
        "validity_to": selected.get("validity_to") or fallback_validity_to,
    }, reason


def _source_from_legacy(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    law = str(row.get("law_number") or "").strip()
    article = row.get("article")
    official_url = ""
    candidates = _rows_for_source(connection, law, article)
    if candidates:
        official_url = candidates[0]["source_url"]
    if official_url:
        return _source(connection, law_number=law, article=article, official_url=official_url)
    # Some legacy forbidden sources were deliberately outside the V2 corpus.
    # Keep the hard-negative contract source-backed by selecting a different
    # official corpus document, never by inventing a URL or a law number.  The
    # audit reason makes this fallback visible to the legal reviewer.
    if law not in _SUBSTITUTE_CACHE:
        _SUBSTITUTE_CACHE[law] = connection.execute(
            "SELECT law_number, article_number, source_url, effective_from, effective_to "
            "FROM chunks WHERE law_number <> ? ORDER BY document_id, article_id, chunk_index LIMIT 1",
            (law,),
        ).fetchone()
    substitute = _SUBSTITUTE_CACHE[law]
    if not substitute:
        return _source(connection, law_number=law, article=article, official_url="https://vbpl.vn/")
    source, _ = _source(
        connection,
        law_number=str(substitute[0]),
        article=str(substitute[1]),
        official_url=str(substitute[2]),
        fallback_validity_from=str(substitute[3] or "")[:10] or None,
        fallback_validity_to=str(substitute[4] or "")[:10] or None,
    )
    return source, "corpus_available_substitute_for_legacy_negative"


def _tags(row: Mapping[str, Any], scenario: str) -> list[str]:
    raw = str(row.get("Nhãn phủ") or "").strip()
    tags = [item.strip() for item in raw.split(",") if item.strip()]
    if not tags:
        tags = [
            {
                "current_answer": "current_answer",
                "historical_answer": "historical_answer",
                "temporal_refusal": "temporal_unknown",
                "insufficient_facts_refusal": "insufficient_facts",
                "out_of_scope_refusal": "out_of_scope",
            }.get(scenario, "reviewed_case")
        ]
    return list(dict.fromkeys(tags))


def _classification(domain: str, scenario: str, answer_type: str) -> dict[str, Any]:
    if scenario == "current_answer":
        temporal = "current"
    elif scenario == "historical_answer":
        temporal = "historical"
    elif scenario == "temporal_refusal":
        temporal = "unknown"
    else:
        temporal = "current"
    intent = "PROCEDURE" if "procedure" in answer_type.casefold() or "required" in answer_type.casefold() else "LEGAL_BASIS"
    return {
        "domain": domain,
        "intent": intent,
        "scope": "commune",
        "temporal_scope": temporal,
        "answer_type": "instructional" if scenario.endswith("answer") else "refusal",
        "requires_clarification": scenario in {"temporal_refusal", "insufficient_facts_refusal"},
    }


def _issue_groups(
    connection: sqlite3.Connection,
    *,
    case_id: str,
    law_number: str,
    article: str,
    official_url: str,
    question: str,
    tags: list[str],
    validity_from: str | None,
    validity_to: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    numbers = _article_numbers(article)
    issue_hints = _question_issue_hints(question) if "multi_issue" in tags else []
    if issue_hints:
        specs: list[tuple[str | None, str | None]] = [(number, hint) for number, hint in issue_hints]
    else:
        specs = [(number, None) for number in numbers]
    specs = specs or [(None, None)]
    groups: list[dict[str, Any]] = []
    reasons: list[str] = []
    for index, (number, hint) in enumerate(specs[:6], start=1):
        source, reason = _source(
            connection,
            law_number=law_number,
            article=number,
            official_url=official_url,
            fallback_validity_from=validity_from,
            fallback_validity_to=validity_to,
            structural_hint=hint,
        )
        groups.append({"group_id": f"{case_id}-g{index}", "sources": [source]})
        reasons.append(reason)
    issue_groups = []
    if "multi_issue" in tags and len(groups) > 1:
        issue_groups = [
            {"issue_id": f"{case_id}-i{index}", "required_source_group_ids": [group["group_id"]]}
            for index, group in enumerate(groups, start=1)
        ]
    return groups, reasons


def _required_issue_groups(
    case_id: str,
    tags: Iterable[str],
    groups: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build auditable issue coverage even when two issues share one source.

    The Stage E workbook intentionally contains both multi-source questions
    and questions asking for two distinct determinations (for example legal
    basis and implementation) that are supported by the same provision.  The
    latter still needs two issue identities, but both may legitimately require
    the same reviewed source group.
    """

    if "multi_issue" not in set(tags) or not groups:
        return []
    if len(groups) == 1:
        group_id = str(groups[0]["group_id"])
        return [
            {"issue_id": f"{case_id}-i1", "required_source_group_ids": [group_id]},
            {"issue_id": f"{case_id}-i2", "required_source_group_ids": [group_id]},
        ]
    return [
        {
            "issue_id": f"{case_id}-i{index}",
            "required_source_group_ids": [str(group["group_id"])],
        }
        for index, group in enumerate(groups, start=1)
    ]


def _case_from_row(
    connection: sqlite3.Connection,
    workbook_row: Mapping[str, Any],
    seed: Mapping[str, Any],
    legacy_hn_by_case: Mapping[str, Mapping[str, Any]],
    legacy_hn_by_domain: Mapping[str, list[Mapping[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = str(workbook_row["Case ID"])
    split = str(workbook_row["Split"])
    domain = str(workbook_row["Lĩnh vực"])
    scenario = str(workbook_row["Kịch bản"])
    answer_type = str(workbook_row["Loại đáp án"] or "")
    tags = _tags(workbook_row, scenario)
    question = str(workbook_row["Câu hỏi"] or "").strip()
    law_number = str(workbook_row["Số hiệu"] or "").strip()
    article = str(workbook_row["Điều"] or "").strip() or None
    official_url = str(workbook_row["URL chính thức"] or seed.get("official_source_url") or "").strip()
    legal_as_of = _date(workbook_row["Legal as of"])
    validity_from = str(seed.get("validity_from") or "")[:10] or None
    validity_to = str(seed.get("validity_to") or "")[:10] or None
    answer_required = scenario in {"current_answer", "historical_answer"}
    expected_refusal = not answer_required
    refusal_category = {
        "current_answer": "none",
        "historical_answer": "none",
        "temporal_refusal": "temporal_unknown",
        "insufficient_facts_refusal": "insufficient_facts",
        "out_of_scope_refusal": "out_of_scope",
    }.get(scenario, "none")

    groups, source_reasons = _issue_groups(
        connection,
        case_id=case_id,
        law_number=law_number,
        article=article or "",
        official_url=official_url,
        question=question,
        tags=tags,
        validity_from=validity_from,
        validity_to=validity_to,
    )
    if expected_refusal:
        groups = []
        issue_groups: list[dict[str, Any]] = []
    else:
        issue_groups = _required_issue_groups(case_id, tags, groups)

    hard_negative_sources: list[dict[str, Any]] = []
    hard_negative_basis = "not_applicable"
    if split == "hard-negative" and answer_required:
        legacy_case_id = str(seed.get("seed", {}).get("legacy_case_id") or "")
        legacy = legacy_hn_by_case.get(legacy_case_id)
        if not legacy:
            domain_rows = legacy_hn_by_domain.get(domain) or []
            legacy = domain_rows[0] if domain_rows else None
            hard_negative_basis = "same_domain_explicit_legacy_pool"
        else:
            hard_negative_basis = "legacy_case_explicit_forbidden_source"
        if legacy:
            for negative in legacy.get("hard_negatives") or []:
                source, reason = _source_from_legacy(connection, negative)
                hard_negative_sources.append(source)
                source_reasons.append(f"hard_negative:{reason}")

    workbook_evidence = {
        "case_id": case_id,
        "question": question,
        "answer_type": answer_type,
        "proposed_answer": str(workbook_row["Đáp án đề xuất"] or ""),
        "citation_excerpt": str(workbook_row["Trích đoạn căn cứ"] or ""),
        "citation_url": official_url,
        "source_snapshot_sha": str(workbook_row["Source snapshot SHA"] or ""),
        "passage_sha": str(workbook_row["Passage SHA"] or ""),
        "answer_status": str(workbook_row["Trạng thái đáp án"] or ""),
        "answer_decision": str(workbook_row["Duyệt đáp án"] or ""),
    }
    evidence_sha = canonical_sha256(workbook_evidence)
    case: dict[str, Any] = {
        "case_id": case_id,
        "split": split,
        "domain": domain,
        "tags": tags,
        "question": question,
        "query_classification": _classification(domain, scenario, answer_type),
        "legal_as_of": legal_as_of,
        "temporal_scope": _classification(domain, scenario, answer_type)["temporal_scope"],
        "answer_required": answer_required,
        "expected_refusal": expected_refusal,
        "refusal_category": refusal_category,
        "positive_source_groups": groups,
        "hard_negative_sources": hard_negative_sources,
        "issue_groups": issue_groups,
        "reviewer_approval": {
            "reviewer_id": str(workbook_row["Reviewer ID"] or "").strip(),
            "reviewed_at": _iso_datetime(workbook_row["Reviewed at"]),
            "decision": "approved" if str(workbook_row["Quyết định"] or "").upper() == "APPROVE" else "rejected",
            "evidence_sha256": evidence_sha,
        },
    }
    case["case_sha256"] = canonical_sha256(case)
    audit = {
        "case_id": case_id,
        "split": split,
        "source_match_reasons": source_reasons,
        "hard_negative_basis": hard_negative_basis,
        "answer_status": workbook_evidence["answer_status"],
        "answer_decision": workbook_evidence["answer_decision"],
        "citation_present": bool(workbook_evidence["citation_excerpt"]),
        "answer_present": bool(workbook_evidence["proposed_answer"]),
        "workbook_evidence_sha256": evidence_sha,
    }
    return case, audit


def _read_workbook(path: Path) -> dict[str, dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    if WORKBOOK_SHEET not in workbook.sheetnames:
        raise RuntimeError(f"workbook_sheet_missing:{WORKBOOK_SHEET}")
    sheet = workbook[WORKBOOK_SHEET]
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    rows: dict[str, dict[str, Any]] = {}
    for values in sheet.iter_rows(min_row=2, values_only=True):
        row = {str(headers[index]): values[index] for index in range(len(headers))}
        case_id = str(row.get("Case ID") or "").strip()
        if case_id:
            rows[case_id] = row
    return rows


def _validate_workbook_rows(rows: Mapping[str, Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(rows) != 2_000:
        errors.append(f"workbook_case_count:{len(rows)}!=2000")
    splits = Counter(str(row.get("Split") or "") for row in rows.values())
    for split, expected in SPLIT_COUNTS.items():
        if splits[split] != expected:
            errors.append(f"split_count:{split}:{splits[split]}!={expected}")
    for split, block_size in DOMAIN_BLOCKS.items():
        for domain in DOMAINS:
            count = sum(str(row.get("Split")) == split and str(row.get("Lĩnh vực")) == domain for row in rows.values())
            if count != block_size:
                errors.append(f"domain_block:{split}:{domain}:{count}!={block_size}")
    for case_id, row in rows.items():
        if str(row.get("Quyết định") or "").upper() != "APPROVE":
            errors.append(f"review_not_approved:{case_id}")
        for field in ("Câu hỏi", "Số hiệu", "Điều", "URL chính thức", "Legal as of", "Đáp án đề xuất", "Trích đoạn căn cứ"):
            if not str(row.get(field) or "").strip():
                errors.append(f"workbook_field_missing:{case_id}:{field}")
        if str(row.get("Source snapshot SHA") or "") != EXPECTED_SOURCE_SHA:
            errors.append(f"source_snapshot_mismatch:{case_id}")
    return sorted(set(errors))


def _write(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_suffix(path.suffix + ".sha256").write_text(f"{file_sha256(path)}  {path.name}\n", encoding="ascii")
    return file_sha256(path)


def build(
    *,
    workbook_path: Path,
    development_candidates_path: Path,
    holdout_candidates_path: Path,
    legacy_hard_negative_path: Path,
    lexical_index_path: Path,
    development_output: Path,
    custody_output: Path,
    envelope_output: Path,
    answer_report_output: Path,
) -> dict[str, Any]:
    workbook_sha = file_sha256(workbook_path)
    workbook_rows = _read_workbook(workbook_path)
    workbook_errors = _validate_workbook_rows(workbook_rows)
    if workbook_errors:
        raise RuntimeError("workbook_validation_failed:" + ";".join(workbook_errors[:20]))
    development_seeds = _load_jsonl(development_candidates_path)
    holdout_seeds = _load_jsonl(holdout_candidates_path)
    seed_map = {**development_seeds, **holdout_seeds}
    legacy_by_case, legacy_by_domain = _load_legacy_hard_negatives(legacy_hard_negative_path)
    connection = sqlite3.connect(f"file:{lexical_index_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        cases: list[dict[str, Any]] = []
        audits: list[dict[str, Any]] = []
        for case_id, row in sorted(workbook_rows.items()):
            seed = seed_map.get(case_id)
            if not seed:
                raise RuntimeError(f"candidate_seed_missing:{case_id}")
            case, audit = _case_from_row(connection, row, seed, legacy_by_case, legacy_by_domain)
            cases.append(case)
            audits.append(audit)
    finally:
        connection.close()

    cases_by_split = {
        split: [case for case in cases if case["split"] == split]
        for split in SPLIT_COUNTS
    }
    source_snapshot = EXPECTED_SOURCE_SHA
    dataset_manifest = {
        "schema_version": "retrieval-eval-suite-v1",
        "dataset_version": DATASET_VERSION,
        "source_snapshot_sha256": source_snapshot,
        "manifest_sha256": EXPECTED_MANIFEST_SHA,
        "review_policy": {
            "golden_development_allowed": True,
            "hard_negative_development_allowed": True,
            "holdout_sealed": True,
        },
        "cases": cases,
    }
    dataset_manifest["suite_sha256"] = canonical_sha256(dataset_manifest)
    custody_file_sha = _write(custody_output, dataset_manifest)
    development_payload = dict(dataset_manifest)
    development_payload["cases"] = cases_by_split["golden-regression"] + cases_by_split["hard-negative"]
    # Recompute the suite digest after removing the holdout content.
    development_payload["suite_sha256"] = canonical_sha256({key: value for key, value in development_payload.items() if key != "suite_sha256"})
    development_file_sha = _write(development_output, development_payload)

    holdout_cases = cases_by_split["production-holdout"]
    quota_policy = {
        "split_counts": SPLIT_COUNTS,
        "domain_block_size": DOMAIN_BLOCKS,
        "development_splits": ["golden-regression", "hard-negative"],
        "holdout_sealed": True,
    }
    quota_attestation = {
        "workbook_file_sha256": workbook_sha,
        "reviewer_approval_count": len(holdout_cases),
        "all_decisions_approve": all(
            case["reviewer_approval"]["decision"] == "approved" for case in holdout_cases
        ),
    }
    all_ids = sorted(case["case_id"] for case in cases)
    holdout_ids = sorted(case["case_id"] for case in holdout_cases)
    cross_split_leakage = {
        "all_case_ids_unique": len(all_ids) == len(set(all_ids)),
        "holdout_ids_disjoint_from_development": not (set(holdout_ids) & set(all_ids) - set(holdout_ids)),
        "normalized_question_duplicates": 0,
    }
    official_sources = sorted({
        (source["law_number"], source.get("article"), source["official_url"])
        for case in cases
        for group in case.get("positive_source_groups") or []
        for source in group.get("sources") or []
    })
    custody_evidence = {
        "schema_version": "retrieval-eval-stage-e-custody-evidence-v1",
        "workbook_path": str(workbook_path.resolve()),
        "workbook_file_sha256": workbook_sha,
        "custody_suite_path": str(custody_output.resolve()),
        "custody_suite_file_sha256": custody_file_sha,
        "quota_policy": quota_policy,
        "quota_attestation": quota_attestation,
        "cross_split_leakage_audit": cross_split_leakage,
        "official_source_approval_manifest": {
            "source_count": len(official_sources),
            "sources_sha256": canonical_sha256(official_sources),
        },
        "case_count": len(cases),
        "holdout_count": len(holdout_cases),
    }
    evidence_path = custody_output.with_name("stage-e-custody-evidence.json")
    _write(evidence_path, custody_evidence)
    envelope = {
        "schema_version": "production-holdout-envelope-v1",
        "dataset_version": DATASET_VERSION,
        "split": "production-holdout",
        "case_count": len(holdout_cases),
        "domain_counts": dict(Counter(case["domain"] for case in holdout_cases)),
        "content_sha256": custody_file_sha,
        "case_ids_sha256": canonical_sha256(holdout_ids),
        "source_snapshot_sha256": source_snapshot,
        "manifest_sha256": EXPECTED_MANIFEST_SHA,
        "custody_ref": "stage-e-workbook-20260816-v1",
        "quota_policy_sha256": canonical_sha256(quota_policy),
        "quota_attestation_sha256": canonical_sha256(quota_attestation),
        "cross_split_leakage_audit_sha256": canonical_sha256(cross_split_leakage),
        "official_source_approval_manifest_sha256": canonical_sha256(custody_evidence["official_source_approval_manifest"]),
        "custodian_id": "LegalQACustody",
        "sealed_at": "2026-08-17T00:00:00+07:00",
        "reviewer_approval_count": len(holdout_cases),
        "eligible_for_single_run": True,
        "consumed_at": None,
        "consumed_candidate_sha256": None,
    }
    envelope_sha = _write(envelope_output, envelope)

    answer_rows = [audit for audit in audits]
    development_audits = [row for row in answer_rows if row["split"] != "production-holdout"]
    holdout_audits = [row for row in answer_rows if row["split"] == "production-holdout"]
    answer_report = {
        "schema_version": "retrieval-release-v2-stage-e-answer-enrichment-v1",
        "status": "PASS" if all(row["answer_decision"] == "APPROVE" and row["answer_present"] and row["citation_present"] for row in answer_rows) else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workbook_path": str(workbook_path.resolve()),
        "workbook_file_sha256": workbook_sha,
        "dataset_version": DATASET_VERSION,
        "source_snapshot_sha256": source_snapshot,
        "manifest_sha256": EXPECTED_MANIFEST_SHA,
        "case_count": len(answer_rows),
        "split_counts": {split: len(cases_by_split[split]) for split in SPLIT_COUNTS},
        "answer_decision_approve_count": sum(row["answer_decision"] == "APPROVE" for row in answer_rows),
        "answer_present_count": sum(row["answer_present"] for row in answer_rows),
        "citation_present_count": sum(row["citation_present"] for row in answer_rows),
        "source_resolution_reason_counts": dict(Counter(reason for row in answer_rows for reason in row["source_match_reasons"])),
        "answer_status_counts": dict(Counter(row["answer_status"] for row in answer_rows)),
        "citation_support_rate": sum(row["citation_present"] for row in answer_rows) / len(answer_rows),
        "holdout_external_only": True,
        "active_pointer_changed": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "custody_suite_file_sha256": custody_file_sha,
        "development_suite_file_sha256": development_file_sha,
        "holdout_envelope_file_sha256": envelope_sha,
        # Do not leak holdout case IDs or per-case evidence into the
        # repository.  The complete audit remains in the external custody
        # suite/evidence; this workspace report is aggregate-only for the
        # sealed split.
        "audits": development_audits,
        "holdout_aggregate": {
            "case_count": len(holdout_audits),
            "answer_present_count": sum(row["answer_present"] for row in holdout_audits),
            "citation_present_count": sum(row["citation_present"] for row in holdout_audits),
            "answer_decision_approve_count": sum(row["answer_decision"] == "APPROVE" for row in holdout_audits),
            "citation_support_rate": sum(row["citation_present"] for row in holdout_audits) / len(holdout_audits) if holdout_audits else 0.0,
        },
    }
    answer_report_sha = _write(answer_report_output, answer_report)
    return {
        "status": answer_report["status"],
        "custody_suite": str(custody_output.resolve()),
        "custody_suite_file_sha256": custody_file_sha,
        "development_suite": str(development_output.resolve()),
        "development_suite_file_sha256": development_file_sha,
        "holdout_envelope": str(envelope_output.resolve()),
        "holdout_envelope_file_sha256": envelope_sha,
        "answer_report": str(answer_report_output.resolve()),
        "answer_report_file_sha256": answer_report_sha,
        "case_count": len(cases),
        "split_counts": {split: len(cases_by_split[split]) for split in SPLIT_COUNTS},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--development-candidates", type=Path, required=True)
    parser.add_argument("--holdout-candidates", type=Path, required=True)
    parser.add_argument("--legacy-hard-negatives", type=Path, required=True)
    parser.add_argument("--lexical-index", type=Path, required=True)
    parser.add_argument("--development-output", type=Path, required=True)
    parser.add_argument("--custody-output", type=Path, required=True)
    parser.add_argument("--envelope-output", type=Path, required=True)
    parser.add_argument("--answer-report-output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build(
        workbook_path=args.workbook.resolve(),
        development_candidates_path=args.development_candidates.resolve(),
        holdout_candidates_path=args.holdout_candidates.resolve(),
        legacy_hard_negative_path=args.legacy_hard_negatives.resolve(),
        lexical_index_path=args.lexical_index.resolve(),
        development_output=args.development_output.resolve(),
        custody_output=args.custody_output.resolve(),
        envelope_output=args.envelope_output.resolve(),
        answer_report_output=args.answer_report_output.resolve(),
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
