import json
from pathlib import Path

import pytest

from scripts.assemble_retrieval_eval_suite_v1 import assemble


def _split(path: Path, split: str, count: int, *, sealed: bool = False):
    payload = {
        "schema_version": "retrieval-eval-suite-v1",
        "dataset_version": "dataset-v1",
        "source_snapshot_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "review_policy": {
            "golden_development_allowed": True,
            "hard_negative_development_allowed": True,
            "holdout_sealed": sealed,
        },
        "cases": [],
    }
    for index in range(count):
        payload["cases"].append({
            "case_id": f"{split}-{index}",
            "split": split,
            "domain": "Hộ tịch/chứng thực",
            "tags": ["exact_law_article", "procedure", "multi_issue", "validity_trap"],
            "question": "q",
            "legal_as_of": "2026-08-16",
            "temporal_scope": "current",
            "answer_required": True,
            "expected_refusal": False,
            "refusal_category": "none",
            "positive_source_groups": [{"group_id": f"g-{index}", "sources": [{
                "law_number": "1/2020/QH14",
                "article": "1",
                "official_url": "https://vbpl.vn/example",
                "jurisdiction": "Vietnam",
                "validity_from": "2020-01-01",
                "validity_to": None,
            }]}],
            "hard_negative_sources": [{
                "law_number": "2/2020/QH14",
                "article": None,
                "official_url": "https://vbpl.vn/example2",
                "jurisdiction": "Vietnam",
                "validity_from": "2020-01-01",
                "validity_to": None,
            }] if split == "hard-negative" else [],
            "issue_groups": [{"issue_id": f"i-{index}", "required_source_group_ids": [f"g-{index}"]}],
            "reviewer_approval": {
                "reviewer_id": "qa",
                "reviewed_at": "2026-08-16T00:00:00Z",
                "decision": "approved",
                "evidence_sha256": "c" * 64,
            },
        })
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def test_assembly_refuses_missing_sealed_holdout(tmp_path: Path):
    golden = tmp_path / "golden.json"
    hard = tmp_path / "hard.json"
    holdout = tmp_path / "holdout.json"
    _split(golden, "golden-regression", 1)
    _split(hard, "hard-negative", 1)
    _split(holdout, "production-holdout", 1, sealed=False)
    with pytest.raises(ValueError, match="case_count|holdout_not_sealed"):
        assemble(golden=golden, hard_negative=hard, holdout=holdout, output=tmp_path / "out.json")


def test_cli_blocker_report_is_written_without_partial_suite(tmp_path: Path):
    from scripts.assemble_retrieval_eval_suite_v1 import main

    output = tmp_path / "suite.json"
    rc = main([
        "--golden", str(tmp_path / "golden.json"),
        "--hard-negative", str(tmp_path / "hard.json"),
        "--holdout", str(tmp_path / "holdout.json"),
        "--output", str(output),
    ])
    assert rc == 2
    assert not output.exists()
    report = output.with_suffix(output.suffix + ".assembly-blocked.json")
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["partial_suite_written"] is False
