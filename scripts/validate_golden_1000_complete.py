"""Deep, independent validation for the 1,000-case Golden review packet."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-complete"
AS_OF = date(2026, 8, 11)
OFFICIAL_PREFIXES = ("https://vbpl.vn/", "https://vanban.chinhphu.vn/")
DOMAINS = [
    "Hộ tịch/chứng thực", "Đất đai/xây dựng", "Cư trú/an ninh",
    "Khiếu nại/tố cáo/xử phạt", "An sinh/y tế/giáo dục",
]
EXPECTED_CATEGORY_PER_DOMAIN = {
    "procedure": 120, "exact_article": 30, "multi_issue": 20,
    "validity": 20, "insufficient_evidence": 10,
}


def db_url() -> str:
    line = next(
        row for row in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if row.startswith("LEGAL_RELEASE_DATABASE_URL=")
    )
    return line.split("=", 1)[1].replace("postgresql+psycopg2://", "postgresql://").replace("host.docker.internal", "127.0.0.1")


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", re.sub(r"\s+", " ", str(value or "")).strip()).casefold()
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


def category(case: dict) -> str:
    tags = [tag.removeprefix("scenario_category_") for tag in case["risk_tags"] if tag.startswith("scenario_category_")]
    return tags[0] if len(tags) == 1 else "invalid"


def current(status: object, effective: object, expired: object) -> bool:
    n = norm(status)
    if n in {"expired", "inactive", "repealed", "het hieu luc", "het hieu luc toan bo"}:
        return False
    if effective and effective > AS_OF:
        return False
    if expired and expired <= AS_OF:
        return False
    return True


def union_nonspace_coverage(content: str, spans: list[tuple[int, int]]) -> float:
    covered = [False] * len(content)
    for start, end in spans:
        for index in range(max(0, start), min(len(content), end)):
            covered[index] = True
    relevant = [index for index, char in enumerate(content) if not char.isspace()]
    return sum(covered[index] for index in relevant) / len(relevant) if relevant else 1.0


def main() -> None:
    dataset_path = OUT / "golden-1000-complete.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = payload["cases"]
    evidence_payload = json.loads((OUT / "claim-evidence-map.json").read_text(encoding="utf-8"))
    evidence = evidence_payload["claims"]
    evidence_by_case_claim = {(row["case_id"], row["claim_id"]): row for row in evidence}
    guard_payload = json.loads((OUT / "validity-guards.json").read_text(encoding="utf-8"))
    guards_by_law = {row["law_number"]: row for row in guard_payload["guards"]}
    current_payload = json.loads((OUT / "official-current-sources.json").read_text(encoding="utf-8"))
    current_by_law = {row["law_number"]: row for row in current_payload["sources"]}

    document_ids = sorted({row["db_document_id"] for row in evidence})
    article_ids = sorted({row["db_article_id"] for row in evidence})
    forbidden_laws = sorted({row["law_number"] for case in cases for row in case["forbidden_sources"]})
    conn = psycopg2.connect(db_url(), connect_timeout=10)
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, d.law_number, d.title, d.status, d.effective_date,
               d.expired_date, d.source_url
        FROM legal_documents d WHERE d.id = ANY(%s)
    """, (document_ids,))
    documents = {row[0]: row for row in cur.fetchall()}
    cur.execute("""
        SELECT id, document_id, article_number, content, status,
               effective_from, effective_to
        FROM legal_articles WHERE id = ANY(%s)
    """, (article_ids,))
    articles = {row[0]: row for row in cur.fetchall()}
    cur.execute("""
        SELECT DISTINCT d.law_number
        FROM legal_documents d WHERE d.law_number = ANY(%s)
    """, (forbidden_laws,))
    existing_forbidden = {row[0] for row in cur.fetchall()}
    cur.execute("""
        SELECT DISTINCT target.law_number
        FROM legal_document_relationships r
        JOIN legal_documents target ON target.id=r.target_document_id
        WHERE target.law_number = ANY(%s)
          AND LOWER(r.relationship_type) LIKE '%%hết hiệu lực%%'
    """, (forbidden_laws,))
    forbidden_with_expiry_relation = {row[0] for row in cur.fetchall()}
    conn.close()

    case_keys = {
        "case_id", "schema_version", "domain", "procedure_family", "legal_as_of",
        "questions", "expected_sources", "forbidden_sources", "required_claims",
        "expected_answer_mode", "expected_refusal", "risk_tags", "evaluation_split",
        "review_status", "unresolved_reason",
    }
    source_keys = {"law_number", "document_title", "article", "clause", "point", "reason", "proof"}
    proof_keys = {"official_url", "quote", "page_number", "char_start", "char_end", "bounding_box", "checked_at"}
    claim_keys = {"claim_id", "facet", "text", "order", "critical"}
    forbidden_keys = {"law_number", "reason"}

    errors: dict[str, list] = defaultdict(list)
    signatures = []
    question_norms = []
    category_by_domain = {domain: Counter() for domain in DOMAINS}
    exact_coverages = []
    source_article_by_case: dict[str, set[int]] = defaultdict(set)

    for case in cases:
        cid = case.get("case_id", "missing")
        cat = category(case)
        category_by_domain.get(case.get("domain"), Counter())[cat] += 1
        if set(case) != case_keys:
            errors["schema_case"].append(cid)
        if set(case.get("questions", {})) != {"citizen", "officer"}:
            errors["schema_question"].append(cid)
        for source in case.get("expected_sources", []):
            if set(source) != source_keys or set(source.get("proof", {})) != proof_keys:
                errors["schema_source"].append(cid)
            if not str(source.get("proof", {}).get("official_url", "")).startswith(OFFICIAL_PREFIXES):
                errors["unofficial_url"].append((cid, source.get("law_number")))
        for claim in case.get("required_claims", []):
            if set(claim) != claim_keys:
                errors["schema_claim"].append((cid, claim.get("claim_id")))
        if any(set(row) != forbidden_keys for row in case.get("forbidden_sources", [])):
            errors["schema_forbidden"].append(cid)

        forbidden = [row["law_number"] for row in case.get("forbidden_sources", [])]
        expected = [row["law_number"] for row in case.get("expected_sources", [])]
        if len(forbidden) != len(set(forbidden)):
            errors["duplicate_forbidden"].append(cid)
        if set(forbidden) & set(expected):
            errors["expected_forbidden_overlap"].append(cid)

        claims = case.get("required_claims", [])
        signature = "|".join(expected + [str(row.get("article")) for row in case.get("expected_sources", [])] + [norm(row.get("text")) for row in claims])
        if case.get("expected_refusal"):
            signature = f"refusal|{case.get('domain')}|{case.get('unresolved_reason')}"
        signatures.append(signature)
        question_norms.append(norm(case.get("questions", {}).get("citizen")))

        if case.get("legal_as_of") != AS_OF.isoformat():
            errors["wrong_legal_as_of"].append(cid)
        if cat == "insufficient_evidence":
            if not case.get("expected_refusal") or case.get("expected_answer_mode") != "explicit_fallback":
                errors["bad_refusal_label"].append(cid)
            if case.get("expected_sources") or claims or not case.get("unresolved_reason"):
                errors["bad_refusal_payload"].append(cid)
        elif case.get("expected_refusal") or case.get("expected_answer_mode") != "grounded_answer":
            errors["bad_grounded_label"].append(cid)

        spans_by_article: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for claim in claims:
            key = (cid, claim["claim_id"])
            row = evidence_by_case_claim.get(key)
            if not row:
                errors["claim_without_evidence"].append(key)
                continue
            article = articles.get(row["db_article_id"])
            document = documents.get(row["db_document_id"])
            if not article or not document or article[1] != document[0]:
                errors["broken_db_reference"].append(key)
                continue
            content = article[3] or ""
            start, end = row["char_start"], row["char_end"]
            if not (0 <= start < end <= len(content)) or content[start:end] != row["quote"] or row["quote"] != claim["text"]:
                errors["claim_span_mismatch"].append(key)
            if not current(document[3], document[4], document[5]) or not current(article[4], article[5], article[6]):
                errors["expired_expected_source"].append(key)
            if not str(document[6] or "").startswith(OFFICIAL_PREFIXES):
                errors["db_source_not_official"].append(key)
            source_article_by_case[cid].add(row["db_article_id"])
            spans_by_article[row["db_article_id"]].append((start, end))

        for source in case.get("expected_sources", []):
            matching = [
                (article_id, article) for article_id, article in articles.items()
                if documents.get(article[1], (None, None))[1] == source["law_number"]
                and str(article[2]) == str(source["article"])
            ]
            if not any(article[3] == source["proof"]["quote"] for _, article in matching):
                errors["source_quote_not_db_exact"].append((cid, source["law_number"], source["article"]))

        if cat == "exact_article":
            if "đầy đủ điều" not in norm(case["questions"]["citizen"]) or len(case["expected_sources"]) != 1:
                errors["bad_exact_article_shape"].append(cid)
            if len(source_article_by_case[cid]) != 1:
                errors["exact_article_multiple_articles"].append(cid)
            else:
                article_id = next(iter(source_article_by_case[cid]))
                coverage = union_nonspace_coverage(articles[article_id][3] or "", spans_by_article[article_id])
                exact_coverages.append((cid, coverage))
                if coverage < 0.995:
                    errors["exact_article_incomplete"].append((cid, coverage))
                ordered = [(row["char_start"], row["char_end"]) for claim in claims if (row := evidence_by_case_claim.get((cid, claim["claim_id"])))]
                if ordered != sorted(ordered) or [claim["order"] for claim in claims] != list(range(1, len(claims) + 1)):
                    errors["exact_article_wrong_order"].append(cid)
        if cat == "multi_issue":
            if len(case["expected_sources"]) < 2 or len(claims) < 2 or len(source_article_by_case[cid]) < 2:
                errors["bad_multi_issue_shape"].append(cid)
        if cat == "validity":
            if not case["forbidden_sources"] or "hiệu lực" not in case["questions"]["citizen"].lower():
                errors["bad_validity_shape"].append(cid)
            for law in forbidden:
                if law not in existing_forbidden:
                    errors["forbidden_not_in_db"].append((cid, law))
                guard = guards_by_law.get(law)
                registry_valid = bool(
                    guard
                    and str(guard.get("official_url", "")).startswith(OFFICIAL_PREFIXES)
                    and guard.get("expired_date")
                    and date.fromisoformat(guard["expired_date"]) <= AS_OF
                    and guard.get("checked_at") == AS_OF.isoformat()
                )
                if law not in forbidden_with_expiry_relation and not registry_valid:
                    errors["forbidden_without_expiry_relation"].append((cid, law))

    # Exact target distributions.
    used_expected_laws = {source["law_number"] for case in cases for source in case["expected_sources"]}
    official_current_complete = True
    for law in sorted(used_expected_laws):
        registry = current_by_law.get(law)
        if not (
            registry
            and registry.get("status") == "Còn hiệu lực"
            and (
                registry.get("expired_date") is None
                or date.fromisoformat(registry["expired_date"]) > AS_OF
            )
            and registry.get("checked_at") == AS_OF.isoformat()
            and str(registry.get("official_url", "")).startswith(OFFICIAL_PREFIXES)
        ):
            official_current_complete = False
            errors["official_current_registry"].append(law)
    checks = {
        "exact_case_count": len(cases) == 1000,
        "case_ids_unique": len({case["case_id"] for case in cases}) == 1000,
        "questions_unique": len(set(question_norms)) == 1000,
        "legal_signatures_unique": len(set(signatures)) == 1000,
        "domain_balance": Counter(case["domain"] for case in cases) == Counter({domain: 200 for domain in DOMAINS}),
        "split_balance": Counter(case["evaluation_split"] for case in cases) == Counter({"development": 600, "validation": 200, "held_out": 200}),
        "category_balance_per_domain": all(category_by_domain[domain] == Counter(EXPECTED_CATEGORY_PER_DOMAIN) for domain in DOMAINS),
        "all_claims_have_one_evidence": len(evidence_by_case_claim) == sum(len(case["required_claims"]) for case in cases),
        "official_current_registry_complete": official_current_complete,
    }

    signature_counter = Counter(signatures)
    question_counter = Counter(question_norms)
    duplicate_signatures = [signature for signature, count in signature_counter.items() if count > 1]
    duplicate_questions = [question for question, count in question_counter.items() if count > 1]

    # Exhaustive 499,500-pair token check; flag only extremely close cases.
    token_sets = [set(question.split()) for question in question_norms]
    claim_token_sets = [
        set(norm(" ".join(claim["text"] for claim in case["required_claims"])).split())
        for case in cases
    ]
    anchors = [
        (
            category(case),
            tuple((source["law_number"], str(source["article"])) for source in case["expected_sources"]),
        )
        for case in cases
    ]
    near_duplicates = []
    for left in range(len(token_sets)):
        for right in range(left + 1, len(token_sets)):
            if anchors[left] != anchors[right]:
                continue
            union = token_sets[left] | token_sets[right]
            similarity = len(token_sets[left] & token_sets[right]) / len(union) if union else 1.0
            claim_union = claim_token_sets[left] | claim_token_sets[right]
            claim_similarity = (
                len(claim_token_sets[left] & claim_token_sets[right]) / len(claim_union)
                if claim_union else 1.0
            )
            if similarity >= 0.90 and claim_similarity >= 0.85:
                near_duplicates.append((
                    cases[left]["case_id"], cases[right]["case_id"],
                    round(similarity, 4), round(claim_similarity, 4),
                ))
                if len(near_duplicates) >= 200:
                    break
        if len(near_duplicates) >= 200:
            break
    checks["all_pair_near_duplicates_zero"] = not near_duplicates
    checks["no_validation_errors"] = not errors
    all_passed = all(checks.values())

    report = {
        "schema_version": "golden-1000-independent-validation-v1",
        "as_of": AS_OF.isoformat(),
        "read_only_db": True,
        "all_passed": all_passed,
        "checks": checks,
        "counts": {
            "cases": len(cases), "claims": sum(len(case["required_claims"]) for case in cases),
            "claim_evidence_rows": len(evidence), "sources": sum(len(case["expected_sources"]) for case in cases),
            "duplicate_signatures": len(duplicate_signatures), "duplicate_questions": len(duplicate_questions),
            "near_duplicate_pairs": len(near_duplicates), "error_types": len(errors),
            "error_rows": sum(len(rows) for rows in errors.values()),
        },
        "distribution": {
            "domains": dict(Counter(case["domain"] for case in cases)),
            "splits": dict(Counter(case["evaluation_split"] for case in cases)),
            "categories_by_domain": {domain: dict(counter) for domain, counter in category_by_domain.items()},
        },
        "exact_article_coverage": {
            "minimum": min((coverage for _, coverage in exact_coverages), default=None),
            "average": sum(coverage for _, coverage in exact_coverages) / len(exact_coverages) if exact_coverages else None,
        },
        "errors": {name: rows[:100] for name, rows in errors.items()},
        "samples": {
            "duplicate_signatures": duplicate_signatures[:10],
            "duplicate_questions": duplicate_questions[:10],
            "near_duplicates": near_duplicates[:30],
        },
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
    }
    (OUT / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_passed": all_passed, "checks": checks, "counts": report["counts"], "exact_article_coverage": report["exact_article_coverage"], "error_types": list(errors)}, ensure_ascii=False))
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
