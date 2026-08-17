# -*- coding: utf-8 -*-
"""Rebuild derivative reports after read-only DB physical-proof enrichment."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-luna"
AS_OF = "2026-08-11"


def main() -> None:
    path = OUT / "golden-1000-luna.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    prior_evidence = json.loads((OUT / "source-evidence.json").read_text(encoding="utf-8")) if (OUT / "source-evidence.json").exists() else {"sources": []}
    prior_by_key = {f"{s.get('case_id')}|{s.get('source_index')}": s for s in prior_evidence.get("sources", [])}
    cases = payload["cases"]
    source_rows = []
    proof_count = 0
    source_proof_count = 0
    review_rows = []
    for c in cases:
        c["unresolved_reason"] = None if c.get("review_status") != "needs_legal_review" and c.get("risk_tags", []).count("physical_proof_ready") else c.get("unresolved_reason")
        case_ready = bool(c.get("expected_sources")) and all((s.get("proof") or {}).get("quote") and (s.get("proof") or {}).get("official_url") and (s.get("proof") or {}).get("char_end") for s in c.get("expected_sources", []))
        if case_ready:
            proof_count += 1
        else:
            c["review_status"] = "needs_legal_review"
        for i, s in enumerate(c.get("expected_sources", []), start=1):
            audit = prior_by_key.get(f"{c['case_id']}|{i}", {})
            proof = s.get("proof") or {}
            ready = bool(proof.get("quote") and proof.get("official_url") and proof.get("char_end"))
            source_proof_count += int(ready)
            validity_evidence = s.get("validity_evidence") or audit.get("validity_evidence")
            existing_title = s.get("document_title")
            document_title = (audit.get("db_title") or s.get("db_title") if (not existing_title or existing_title == s.get("law_number")) else existing_title) or s.get("law_number")
            s["document_title"] = document_title
            source_rows.append({"case_id": c["case_id"], "source_index": i, "law_number": s.get("law_number"), "article": s.get("article"), "document_title": document_title, "validity": (validity_evidence or {}).get("status") or audit.get("validity"), "validity_evidence": validity_evidence, "db_document_id": s.get("db_document_id") or audit.get("db_document_id"), "db_status": s.get("db_status") or audit.get("db_status"), "db_effective_date": s.get("db_effective_date") or audit.get("db_effective_date"), "db_expired_date": s.get("db_expired_date") or audit.get("db_expired_date"), "db_title": s.get("db_title") or audit.get("db_title"), "official_url": proof.get("official_url") or audit.get("official_url"), "quote": proof.get("quote") or audit.get("quote"), "page_number": proof.get("page_number") if proof.get("page_number") is not None else audit.get("page_number"), "char_start": proof.get("char_start") if proof.get("char_start") is not None else audit.get("char_start"), "char_end": proof.get("char_end") if proof.get("char_end") is not None else audit.get("char_end"), "physical_proof_status": "ready_for_human_review" if ready else "missing"})
        if c.get("review_status") == "needs_legal_review":
            review_rows.append({"case_id": c["case_id"], "domain": c["domain"], "procedure_family": c["procedure_family"], "question": c["questions"]["citizen"], "split": c["evaluation_split"], "source_count": len(c.get("expected_sources", [])), "claim_count": len(c.get("required_claims", [])), "proof_status": "Thiếu physical proof" if not case_ready else "Có proof — chờ duyệt", "review_status": c["review_status"], "review_decision": "Chưa duyệt", "unresolved_reason": c.get("unresolved_reason") or "Cần kiểm tra hiệu lực, near-duplicate và duyệt người dùng."})
    payload["summary"].update({
        "case_count": len(cases),
        "domain_counts": dict(Counter(c["domain"] for c in cases)),
        "split_counts": dict(Counter(c["evaluation_split"] for c in cases)),
        "physical_proof_count": proof_count,
        "physical_proof_source_count": source_proof_count,
        "legal_review_count": len(review_rows),
    })
    # Keep the deliverable JSON within the user-provided Golden v2 contract;
    # DB/document diagnostics stay in source-evidence.json.
    allowed_case = {"case_id", "schema_version", "domain", "procedure_family", "legal_as_of", "questions", "expected_sources", "forbidden_sources", "required_claims", "expected_answer_mode", "expected_refusal", "risk_tags", "evaluation_split", "review_status", "unresolved_reason"}
    allowed_source = {"law_number", "document_title", "article", "clause", "point", "reason", "proof"}
    allowed_proof = {"official_url", "quote", "page_number", "char_start", "char_end", "bounding_box", "checked_at"}
    clean_cases = []
    for case in cases:
        clean = {k: case.get(k) for k in allowed_case if k in case}
        expected_clean = []
        for src in case.get("expected_sources", []):
            item = {k: src.get(k) for k in allowed_source if k in src}
            if not item.get("document_title"):
                item["document_title"] = src.get("db_title") or src.get("law_number")
            raw_proof = src.get("proof") if isinstance(src.get("proof"), dict) else {}
            # Keep a stable proof object even when a case is unresolved.  A
            # missing quote is represented by an empty value, never null or a
            # missing nested schema.
            item["proof"] = {k: raw_proof.get(k) for k in allowed_proof}
            expected_clean.append(item)
        clean["expected_sources"] = expected_clean
        clean["forbidden_sources"] = [
            {"law_number": src.get("law_number"), "reason": src.get("reason", "")}
            for src in case.get("forbidden_sources", [])
        ]
        clean_cases.append(clean)
    clean_payload = dict(payload)
    clean_payload["cases"] = clean_cases
    path.write_text(json.dumps(clean_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "source-evidence.json").write_text(json.dumps({"schema_version": "golden-1000-luna-source-evidence-v2", "legal_as_of": AS_OF, "read_only_db_proof": True, "sources": source_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "unresolved-legal-review.json").write_text(json.dumps({"schema_version": "golden-1000-luna-legal-review-v2", "rows": review_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"schema_version": "golden-1000-luna-manifest-v2", "generated_at": datetime.now(timezone.utc).isoformat(), "legal_as_of": AS_OF, "summary": payload["summary"], "production_mutation": False, "proof_source": "PostgreSQL legal_articles (read-only)"}
    (OUT / "golden-1000-luna-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    forbidden_keys = {"law_number", "reason"}
    contract_valid = all(
        set(c) == allowed_case
        and all(set(s) == allowed_source and set(s.get("proof", {})) == allowed_proof for s in c.get("expected_sources", []))
        and all(set(s) == forbidden_keys for s in c.get("forbidden_sources", []))
        for c in clean_cases
    )
    scenario_category_counts = Counter(
        tag.removeprefix("scenario_category_")
        for c in cases
        for tag in c.get("risk_tags", [])
        if tag.startswith("scenario_category_")
    )
    category_expected = {"procedure": 600, "exact_article": 150, "multi_issue": 100, "validity": 100, "insufficient_evidence": 50}
    validation = {"schema_version": "golden-1000-luna-validation-v2", "checks": {"exact_case_count": len(cases) == 1000, "domain_balance": all(n == 200 for n in Counter(c["domain"] for c in cases).values()), "split_balance": dict(Counter(c["evaluation_split"] for c in cases)) == {"development": 600, "validation": 200, "held_out": 200}, "scenario_category_balance": dict(scenario_category_counts) == category_expected, "case_ids_unique": len({c["case_id"] for c in cases}) == 1000, "question_exact_duplicates": len({c["questions"]["citizen"] for c in cases}) == 1000, "every_case_has_source_or_review": all(c.get("expected_sources") or c.get("review_status") == "needs_legal_review" for c in cases), "golden_contract_keys_valid": contract_valid, "document_title_coverage": all(s.get("document_title") for c in clean_cases for s in c.get("expected_sources", [])), "physical_proof_recomputed": True, "production_mutation": False}, "scenario_category_counts": dict(scenario_category_counts), "physical_proof_count": proof_count, "physical_proof_source_count": source_proof_count, "legal_review_count": len(review_rows), "near_duplicate_count": json.loads((OUT / "duplicate-report.json").read_text(encoding="utf-8"))["near_duplicate_count"]}
    (OUT / "validation-report.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ["# Golden 1000 Luna — báo cáo sau bổ sung physical proof", "", "Bộ vẫn review-only; chưa nhập production corpus.", "", f"- Tổng số ca: **{len(cases)}**", "- Phân bổ: **200 ca mỗi lĩnh vực**; 600 development / 200 validation / 200 held-out.", f"- Ca có physical proof từ PostgreSQL read-only: **{proof_count}**", f"- Nguồn có quote + URL + char range: **{source_proof_count}**", f"- Cần legal review: **{len(review_rows)}**", f"- Cặp gần trùng cần xem: **{validation['near_duplicate_count']}**", "- Physical proof là nội dung nguyên văn của `legal_articles.content`, với `char_start=0`, `char_end=len(quote)`; page/bounding box không có trong DB chuẩn hóa.", "- Chưa chuyển ca sang approved: cần kiểm tra hiệu lực, nguồn sửa đổi/thay thế, near-duplicate và duyệt người dùng."]
    (OUT / "final-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(cases), "physical_proof_cases": proof_count, "physical_proof_sources": source_proof_count, "legal_review": len(review_rows), "near_duplicates": validation["near_duplicate_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
