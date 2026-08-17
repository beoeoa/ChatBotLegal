"""Independent, read-only verification for the Golden 1000 candidate set."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-luna"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LEGAL_RELEASE_DATABASE_URL="):
            return line.split("=", 1)[1].replace("postgresql+psycopg2://", "postgresql://").replace("host.docker.internal", "127.0.0.1")
    raise RuntimeError("LEGAL_RELEASE_DATABASE_URL missing")


def article_no(value: object) -> str:
    m = re.search(r"\d+", str(value or ""))
    return m.group(0) if m else ""


def main() -> None:
    payload = json.loads((OUT / "golden-1000-luna.json").read_text(encoding="utf-8"))
    evidence = json.loads((OUT / "source-evidence.json").read_text(encoding="utf-8"))
    duplicate = json.loads((OUT / "duplicate-report.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUT / "golden-1000-luna-manifest.json").read_text(encoding="utf-8"))
    cases = payload["cases"]
    evidence_by_key = {(x["case_id"], x["source_index"]): x for x in evidence["sources"]}

    contract_case = {"case_id", "schema_version", "domain", "procedure_family", "legal_as_of", "questions", "expected_sources", "forbidden_sources", "required_claims", "expected_answer_mode", "expected_refusal", "risk_tags", "evaluation_split", "review_status", "unresolved_reason"}
    contract_source = {"law_number", "document_title", "article", "clause", "point", "reason", "proof"}
    contract_proof = {"official_url", "quote", "page_number", "char_start", "char_end", "bounding_box", "checked_at"}
    forbidden_keys = {"law_number", "reason"}
    schema_errors = []
    for c in cases:
        if set(c) != contract_case:
            schema_errors.append((c["case_id"], "case_keys"))
        for s in c.get("expected_sources", []):
            proof_obj = s.get("proof") if isinstance(s.get("proof"), dict) else {}
            if set(s) != contract_source or set(proof_obj) != contract_proof:
                schema_errors.append((c["case_id"], "source_keys"))
        for s in c.get("forbidden_sources", []):
            if set(s) != forbidden_keys:
                schema_errors.append((c["case_id"], "forbidden_keys"))

    con = psycopg2.connect(db_url(), connect_timeout=10)
    cur = con.cursor()
    cur.execute("select document_id, article_number, content from legal_articles")
    article_content = {(doc, article_no(num)): content or "" for doc, num, content in cur.fetchall()}
    con.close()

    proof_sources = 0
    quote_mismatches = []
    bad_urls = []
    bad_ranges = []
    for c in cases:
        for idx, source in enumerate(c.get("expected_sources", []), 1):
            proof = source.get("proof") or {}
            url = proof.get("official_url") or ""
            if url and not (url.startswith("https://vbpl.vn/") or url.startswith("https://vanban.chinhphu.vn/")):
                bad_urls.append((c["case_id"], source.get("law_number"), url))
            quote = proof.get("quote") or ""
            if not quote:
                continue
            proof_sources += 1
            audit = evidence_by_key.get((c["case_id"], idx), {})
            doc_id = audit.get("db_document_id")
            expected_parts = [article_content.get((doc_id, article_no(n)), "") for n in re.findall(r"\d+", str(source.get("article") or ""))]
            expected = "\n\n".join(expected_parts)
            if expected != quote:
                quote_mismatches.append((c["case_id"], source.get("law_number"), source.get("article")))
            if proof.get("char_start") != 0 or proof.get("char_end") != len(quote):
                bad_ranges.append((c["case_id"], source.get("law_number")))

    category_counts = Counter(
        tag.removeprefix("scenario_category_")
        for c in cases
        for tag in c.get("risk_tags", [])
        if tag.startswith("scenario_category_")
    )
    token_sets = [set(re.findall(r"\w+", c["questions"]["citizen"].lower(), flags=re.UNICODE)) for c in cases]
    all_pair_token_near_duplicates = []
    for i, left in enumerate(token_sets):
        for j in range(i + 1, len(token_sets)):
            right = token_sets[j]
            union = left | right
            if union and len(left & right) / len(union) >= 0.85:
                all_pair_token_near_duplicates.append((cases[i]["case_id"], cases[j]["case_id"]))
    result = {
        "schema_version": "golden-1000-luna-independent-verification-v1",
        "read_only_db": True,
        "checks": {
            "exact_case_count": len(cases) == 1000,
            "domain_balance": dict(Counter(c["domain"] for c in cases)) == {"Hộ tịch/chứng thực": 200, "Đất đai/xây dựng": 200, "Cư trú/an ninh": 200, "Khiếu nại/tố cáo/xử phạt": 200, "An sinh/y tế/giáo dục": 200},
            "split_balance": dict(Counter(c["evaluation_split"] for c in cases)) == {"development": 600, "validation": 200, "held_out": 200},
            "scenario_category_balance": dict(category_counts) == {"procedure": 600, "exact_article": 150, "multi_issue": 100, "validity": 100, "insufficient_evidence": 50},
            "question_unique": len({c["questions"]["citizen"] for c in cases}) == 1000,
            "schema_exact": not schema_errors,
            "official_urls_only": not bad_urls,
            "db_quotes_exact": not quote_mismatches,
            "char_ranges_exact": not bad_ranges,
            "near_duplicates_zero": duplicate.get("near_duplicate_count") == 0,
            "all_pair_token_near_duplicates_zero": not all_pair_token_near_duplicates,
            "production_mutation": manifest.get("production_mutation") is False,
        },
        "counts": {
            "cases": len(cases),
            "proof_sources_checked": proof_sources,
            "quote_mismatches": len(quote_mismatches),
            "bad_urls": len(bad_urls),
            "bad_ranges": len(bad_ranges),
            "schema_errors": len(schema_errors),
            "legal_review_cases": payload.get("summary", {}).get("legal_review_count"),
            "all_pair_token_near_duplicates": len(all_pair_token_near_duplicates),
        },
        "samples": {"quote_mismatches": quote_mismatches[:5], "bad_urls": bad_urls[:5], "bad_ranges": bad_ranges[:5], "schema_errors": schema_errors[:5]},
    }
    (OUT / "final-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
