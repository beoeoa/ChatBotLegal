from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "scripts" / "golden_review_import_contract.mjs"


def _run_contract(tmp_path: Path, body: str) -> dict:
    runner = tmp_path / "review-import-check.mjs"
    runner.write_text(
        f"""
import {{ parseSourceLabel, normalizeReviewedRow }} from {json.dumps(CONTRACT.as_uri())};
{body}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(runner)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)


def test_named_law_and_article_are_normalized(tmp_path: Path):
    result = _run_contract(
        tmp_path,
        """
const result = parseSourceLabel('Luật Hộ tịch 2014 (Điều 18)', {forbidden: false});
process.stdout.write(JSON.stringify(result));
""",
    )
    assert result["source"]["law_number"] == "60/2014/QH13"
    assert result["source"]["article"] == "18"
    assert result["errors"] == []


def test_replaced_expected_source_is_fail_closed(tmp_path: Path):
    result = _run_contract(
        tmp_path,
        """
const result = parseSourceLabel('Nghị định 146/2018/NĐ-CP (Điều 14)', {
  forbidden: false,
  legalAsOf: '2026-08-10',
});
process.stdout.write(JSON.stringify(result));
""",
    )
    assert result["source"]["law_number"] == "146/2018/NĐ-CP"
    assert "expected_source_replaced" in result["errors"]
    assert result["replacement"] == "188/2025/NĐ-CP"


def test_replaced_source_is_allowed_in_forbidden_list(tmp_path: Path):
    result = _run_contract(
        tmp_path,
        """
const result = parseSourceLabel('Nghị định 146/2018/NĐ-CP (Hết hiệu lực)', {
  forbidden: true,
  legalAsOf: '2026-08-10',
});
process.stdout.write(JSON.stringify(result));
""",
    )
    assert result["errors"] == []
    assert result["source"]["law_number"] == "146/2018/NĐ-CP"


def test_approved_case_with_claims_cannot_expect_blanket_refusal(tmp_path: Path):
    result = _run_contract(
        tmp_path,
        """
const row = {
  caseId: 'web-001',
  domain: 'Hộ tịch/chứng thực',
  question: 'Đăng ký kết hôn cần gì?',
  legalAsOf: '2026-08-10',
  procedureFamily: 'Đăng ký kết hôn',
  expectedSources: ['Luật Hộ tịch 2014 (Điều 18)'],
  forbiddenSources: ['Nghị định 158/2005/NĐ-CP (Hết hiệu lực)'],
  requiredClaims: ['1. Hai bên phải cùng có mặt.'],
  answerMode: 'source_view_only',
  expectedRefusal: true,
  reviewStatus: 'approved',
};
process.stdout.write(JSON.stringify(normalizeReviewedRow(row)));
""",
    )
    assert "approved_claim_case_cannot_expect_refusal" in result["errors"]
    assert "approved_claim_case_requires_answer_mode" in result["errors"]
    assert result["case"]["review_status"] == "proposed"
    assert result["case"]["required_claims"][0]["order"] == 1


def test_unknown_source_identity_cannot_be_promoted(tmp_path: Path):
    result = _run_contract(
        tmp_path,
        """
const result = parseSourceLabel('Quyết định mới nhất của địa phương', {
  forbidden: false,
  legalAsOf: '2026-08-10',
});
process.stdout.write(JSON.stringify(result));
""",
    )
    assert result["source"] is None
    assert "source_identity_unresolved" in result["errors"]
