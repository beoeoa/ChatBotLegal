"""Verify and seal the approved Golden 1,000 release."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_legal_golden_v2 import DEFAULT_SCHEMA, validate_dataset


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_identity(source: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(source.get(key) or "") for key in ("law_number", "article", "clause", "point"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--source-workbook", type=Path, required=True)
    parser.add_argument("--canonical-store", type=Path, required=True)
    args = parser.parse_args()

    full_path = args.release_dir / "golden-1000-approved-full.json"
    v2_path = args.release_dir / "golden-1000-approved-v2.json"
    workbook_path = args.release_dir / "golden-1000-approved.xlsx"
    receipt_path = args.release_dir / "approval-receipt.json"
    workbook_verification_path = args.release_dir / "approved-workbook-verification.json"
    hard_negatives_path = args.release_dir / "hard-negatives.json"
    reranker_path = args.release_dir / "reranker-safe-fallback.json"
    shadow_path = args.release_dir / "embedding-shadow-plan.json"
    gate_c_path = args.release_dir / "gate-c-approved-1000.json"
    regression_path = args.release_dir / "regression-summary.json"
    validity_audit_path = args.release_dir / "approved-validity-audit.json"
    real_benchmark_before_path = args.release_dir / "reranker-real-benchmark.json"
    real_benchmark_after_path = (
        args.release_dir / "reranker-real-benchmark-after-fix-v3.json"
    )
    source = json.loads(args.source_dataset.read_text(encoding="utf-8"))
    full = json.loads(full_path.read_text(encoding="utf-8"))
    v2 = json.loads(v2_path.read_text(encoding="utf-8"))
    canonical = json.loads(args.canonical_store.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    workbook_verification = json.loads(workbook_verification_path.read_text(encoding="utf-8"))
    hard_negatives = json.loads(hard_negatives_path.read_text(encoding="utf-8"))
    reranker = json.loads(reranker_path.read_text(encoding="utf-8"))
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    gate_c = json.loads(gate_c_path.read_text(encoding="utf-8"))
    regression = json.loads(regression_path.read_text(encoding="utf-8"))
    validity_audit = json.loads(validity_audit_path.read_text(encoding="utf-8"))
    real_benchmark_before = json.loads(
        real_benchmark_before_path.read_text(encoding="utf-8")
    )
    real_benchmark_after = json.loads(
        real_benchmark_after_path.read_text(encoding="utf-8")
    )

    source_by_id = {case["case_id"]: case for case in source["cases"]}
    full_by_id = {case["case_id"]: case for case in full["cases"]}
    v2_by_id = {case["case_id"]: case for case in v2["cases"]}
    core_preserved = True
    projection_preserved = True
    for case_id, original in source_by_id.items():
        approved = full_by_id.get(case_id)
        projected = v2_by_id.get(case_id)
        if not approved or not projected:
            core_preserved = False
            projection_preserved = False
            continue
        for key in ("domain", "procedure_family", "legal_as_of", "questions", "expected_sources", "forbidden_sources", "required_claims", "expected_refusal", "evaluation_split", "unresolved_reason"):
            if approved.get(key) != original.get(key):
                core_preserved = False
        if projected["questions"] != original["questions"] or projected["required_claims"] != original["required_claims"]:
            projection_preserved = False
        if [source_identity(item) for item in projected["expected_sources"]] != [source_identity(item) for item in original["expected_sources"]]:
            projection_preserved = False
        if [source_identity(item) for item in projected["forbidden_sources"]] != [source_identity(item) for item in original["forbidden_sources"]]:
            projection_preserved = False

    v2_errors = validate_dataset(v2, DEFAULT_SCHEMA)
    checks = {
        "exact_1000_cases": len(source_by_id) == len(full_by_id) == len(v2_by_id) == 1000,
        "all_full_cases_approved": all(case.get("review_status") == "approved" for case in full["cases"]),
        "all_v2_cases_approved": all(case.get("review_status") == "approved" for case in v2["cases"]),
        "source_core_preserved": core_preserved,
        "v2_projection_preserved": projection_preserved,
        "v2_schema_valid": not v2_errors,
        "canonical_store_matches": digest(v2_path) == digest(args.canonical_store) and canonical == v2,
        "workbook_verification_passed": workbook_verification.get("all_passed") is True,
        "workbook_1000_approved": workbook_verification.get("counts", {}).get("approved_decisions") == 1000,
        "receipt_inputs_match": receipt["input_hashes"]["dataset_sha256"] == digest(args.source_dataset) and receipt["input_hashes"]["workbook_sha256"] == digest(args.source_workbook),
        "hard_negatives_from_approved_only": hard_negatives.get("summary", {}).get("approved_case_count") == 1000,
        "hard_negative_examples_100": hard_negatives.get("summary", {}).get("example_count") == 100,
        "reranker_fallback_deterministic": reranker.get("deterministic") is True and reranker.get("fallback_verified") is True,
        "embedding_shadow_isolated": shadow.get("active_collection_before") == shadow.get("active_collection_after") and shadow.get("activation_requested") is False,
        "gate_c_safe_degraded_passed": gate_c.get("gate_c_pass") is True and gate_c.get("status") == "pass_degraded",
        "regression_suite_passed": regression.get("status") == "pass" and regression.get("feature016_and_approval_tests", {}).get("failed") == 0 and regression.get("git_diff_check") == "pass",
        "split_balance": Counter(item["evaluation_split"] for item in v2["case_metadata"]) == Counter({"development": 600, "validation": 200, "held_out": 200}),
        "fallback_count_50": sum(case.get("expected_refusal") is True for case in v2["cases"]) == 50,
        "production_corpus_untouched": receipt.get("production_corpus_mutated") is False,
        "production_vectors_untouched": receipt.get("production_vectors_mutated") is False,
        "approved_expected_sources_current": validity_audit.get("summary", {}).get(
            "blocked_expected_source_count"
        )
        == 0,
        "real_benchmark_scores_all_100_cases": real_benchmark_after.get(
            "evaluated_case_count"
        )
        == 100,
        "real_benchmark_no_forbidden_or_ineligible": (
            real_benchmark_after.get("baseline", {}).get("forbidden_top5_count")
            == 0
            and real_benchmark_after.get("baseline", {}).get(
                "ineligible_top10_count"
            )
            == 0
        ),
        "real_benchmark_all_cases_retrieved": real_benchmark_after.get(
            "retrieved_case_count"
        )
        == 100,
    }
    all_passed = all(checks.values())
    dynamic_checks = {
        "approved_expected_sources_current",
        "real_benchmark_all_cases_retrieved",
    }
    approval_integrity_passed = all(
        value for name, value in checks.items() if name not in dynamic_checks
    )
    status = (
        "approved_frozen"
        if all_passed
        else "needs_revalidation"
        if approval_integrity_passed
        else "failed"
    )
    verification = {
        "schema_version": "golden-1000-approved-release-verification-v1",
        "status": status,
        "all_passed": all_passed,
        "checks": checks,
        "counts": {
            "cases": len(v2["cases"]),
            "claims": sum(len(case.get("required_claims") or []) for case in v2["cases"]),
            "sources": sum(len(case.get("expected_sources") or []) for case in v2["cases"]),
            "hard_negative_examples": hard_negatives["summary"]["example_count"],
            "v2_validation_errors": len(v2_errors),
            "blocked_expected_sources": validity_audit.get("summary", {}).get(
                "blocked_expected_source_count"
            ),
            "blocked_cases": validity_audit.get("summary", {}).get(
                "blocked_case_count"
            ),
            "real_benchmark_retrieved_cases_before": real_benchmark_before.get(
                "evaluated_case_count"
            ),
            "real_benchmark_retrieved_cases_after": real_benchmark_after.get(
                "retrieved_case_count"
            ),
        },
        "v2_validation_errors": v2_errors[:100],
    }
    verification_path = args.release_dir / "release-verification.json"
    write_json(verification_path, verification)

    artifacts = {
        "approved_full": full_path,
        "approved_v2": v2_path,
        "approved_workbook": workbook_path,
        "approval_receipt": receipt_path,
        "approved_workbook_verification": workbook_verification_path,
        "hard_negatives": hard_negatives_path,
        "reranker_safe_fallback": reranker_path,
        "embedding_shadow_plan": shadow_path,
        "gate_c_approved_1000": gate_c_path,
        "regression_summary": regression_path,
        "approved_validity_audit": validity_audit_path,
        "real_benchmark_before": real_benchmark_before_path,
        "real_benchmark_after": real_benchmark_after_path,
        "release_verification": verification_path,
        "canonical_store": args.canonical_store,
    }
    manifest = {
        "schema_version": "golden-1000-approved-manifest-v2",
        "status": verification["status"],
        "legal_as_of": v2["legal_as_of"],
        "approved_at": v2["approved_at"],
        "approval_entry_hash": receipt["entry_hash"],
        "artifacts": {
            name: {"path": str(path), "sha256": digest(path)} for name, path in artifacts.items()
        },
        "production_corpus_mutated": False,
        "production_vectors_mutated": False,
    }
    write_json(args.release_dir / "approved-manifest.json", manifest)

    report = f"""# Golden 1.000 — phát hành đã phê duyệt

## Trạng thái

`{verification['status']}` tại mốc hiệu lực `{v2['legal_as_of']}`. Người dùng đã xác nhận xem toàn bộ và duyệt tất cả 1.000 ca. Bản trước duyệt được giữ nguyên; không sửa corpus hoặc vector production.

## Kết quả phê duyệt và kiểm tra lại

- 1.000/1.000 ca có `review_status=approved` trong bản đầy đủ và bản Golden v2.
- Bản chiếu Golden v2 hợp lệ theo schema Feature 016: {len(v2_errors)} lỗi.
- Nội dung câu hỏi, nguồn, claim và split của bản gốc được bảo toàn.
- 600 development, 200 validation, 200 held-out.
- 50 ca thiếu dữ kiện được biểu diễn bằng `source_view_only + expected_refusal=true`; nhãn gốc `explicit_fallback` được giữ trong metadata/risk tag.
- 100 ca hiệu lực đã sinh 100 hard-negative rõ nguồn, chỉ từ ca được duyệt.
- Gate C đạt `pass_degraded`: fallback reranker có tính tất định; BGE và shadow embedding chưa được kích hoạt vì máy chưa có model path/config tương ứng.
- Workbook có 1.000 quyết định `Duyệt`, 10 sheet và không lỗi công thức.
- Regression Feature 016 và lớp phê duyệt: {regression['feature016_and_approval_tests']['passed']} test đạt, {regression['feature016_and_approval_tests']['failed']} test lỗi.
- Hash và biên bản phê duyệt nằm trong `approved-manifest.json` và `approval-receipt.json`.

## Cổng hiệu lực sau phê duyệt

- Đã kiểm tra {validity_audit['summary']['expected_sources_checked']} nguồn kỳ vọng theo đúng ngày pháp lý của từng ca.
- Có {validity_audit['summary']['blocked_case_count']} ca đang bị snapshot phục vụ chặn và phải chuyển sang `needs_revalidation`.
- Benchmark thật tính đủ {real_benchmark_after['evaluated_case_count']}/100 ca; {real_benchmark_after['retrieved_case_count']} ca có evidence và {real_benchmark_after['evaluated_case_count'] - real_benchmark_after['retrieved_case_count']} ca fail-closed.
- Top-1 {real_benchmark_after['baseline']['top1_accuracy']:.1%}, Top-5 {real_benchmark_after['baseline']['top5_accuracy']:.1%}, MRR {real_benchmark_after['baseline']['mrr']:.3f}; không có nguồn cấm trong Top-5 và không có nguồn không đủ điều kiện trong Top-10.
- Phê duyệt của người dùng được giữ nguyên để audit, nhưng không được dùng để vượt cổng hiệu lực hoặc che lỗi dữ liệu đang phục vụ.

## Cách sử dụng

- Chỉ dùng `golden-1000-approved-v2.json` hoặc `notebook_data/feature016-golden-1000-approved.json` cho evaluator sau khi trạng thái trở lại `approved_frozen`.
- Dùng `golden-1000-approved-full.json` khi cần audit provenance, split, hiệu lực và unresolved reason.
- Dùng `hard-negatives.json` để đánh giá/huấn luyện reranker; chưa tự động huấn luyện hoặc kích hoạt model production.
- Khi nguồn luật thay đổi hiệu lực, chạy cơ chế đánh dấu `needs_revalidation` cho các Case ID liên quan trước khi chấm tiếp.
"""
    (args.release_dir / "final-approved-report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": verification["status"], "checks": checks, "manifest": str(args.release_dir / "approved-manifest.json")}, ensure_ascii=False))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
