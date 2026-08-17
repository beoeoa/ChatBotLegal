import hashlib
import json

import pytest

from scripts.run_retrieval_release_v2_benchmark import (
    _aggregate_summaries,
    _development_cases,
    _experiment_quality,
    _m5_specs,
    _m6_parent_config,
    _metrics,
    _verify_reranker_manifest,
)


def test_metrics_contract_excludes_refusals_and_keeps_segments_and_misses():
    summary = _metrics([
        {
            "case_id": "answer-1",
            "domain": "Hộ tịch/chứng thực",
            "intent": "PROCEDURE",
            "temporal_scope": "current",
            "issue_count": 1,
            "answer_required": True,
            "expected_refusal": False,
            "hit_at_10": True,
            "top5_hit": True,
            "candidate_hit_at_50": True,
            "reciprocal_rank_at_10": 1.0,
            "issue_recall_at_10": 1.0,
            "all_required_sources_coverage": 1.0,
            "correct_refusal": False,
            "latency_ms": 10.0,
            "reranker_latency_ms": 0.0,
            "outside_manifest_count": 0,
            "invalid_temporal_count": 0,
        },
        {
            "case_id": "refusal-1",
            "domain": "Hộ tịch/chứng thực",
            "intent": "OUT_OF_SCOPE",
            "temporal_scope": "unknown",
            "issue_count": 0,
            "answer_required": False,
            "expected_refusal": True,
            "hit_at_10": False,
            "top5_hit": False,
            "candidate_hit_at_50": False,
            "reciprocal_rank_at_10": 0.0,
            "issue_recall_at_10": 0.0,
            "all_required_sources_coverage": 1.0,
            "correct_refusal": True,
            "latency_ms": 5.0,
            "reranker_latency_ms": 0.0,
            "outside_manifest_count": 0,
            "invalid_temporal_count": 0,
        },
    ])
    assert summary["metric_contract_version"] == "legal-retrieval-metrics-v2"
    assert summary["answer_required_count"] == 1
    assert summary["recall_at_10"] == 1.0
    assert summary["mrr_at_10"] == 1.0
    assert summary["top5_rate"] == 1.0
    assert summary["correct_refusal_rate"] == 1.0
    assert summary["per_intent"]["PROCEDURE"]["answer_required_count"] == 1


def test_metrics_expose_exact_law_article_gate_separately():
    row = {
        "case_id": "exact-1",
        "domain": "Hộ tịch/chứng thực",
        "intent": "SPECIFIC_DOCUMENT",
        "temporal_scope": "current",
        "issue_count": 1,
        "exact_law_article_case": True,
        "answer_required": True,
        "expected_refusal": False,
        "hit_at_10": True,
        "top5_hit": True,
        "candidate_hit_at_50": True,
        "reciprocal_rank_at_10": 1.0,
        "issue_recall_at_10": 1.0,
        "all_required_sources_coverage": 1.0,
        "stage_hits": {"exact": True},
        "correct_refusal": False,
        "latency_ms": 10.0,
        "reranker_latency_ms": 0.0,
        "outside_manifest_count": 0,
        "invalid_temporal_count": 0,
    }
    summary = _metrics([row])
    assert summary["exact_law_article_case_count"] == 1
    assert summary["exact_law_article_lookup_recall_at_50"] == 1.0
    assert summary["exact_law_article_final_recall_at_10"] == 1.0


def test_repeated_metric_aggregation_uses_median_and_worst_safety_counts():
    base = _metrics([
        {
            "case_id": "a",
            "domain": "Hộ tịch/chứng thực",
            "intent": "PROCEDURE",
            "temporal_scope": "current",
            "issue_count": 1,
            "answer_required": True,
            "expected_refusal": False,
            "hit_at_10": True,
            "top5_hit": True,
            "candidate_hit_at_50": True,
            "reciprocal_rank_at_10": 1.0,
            "issue_recall_at_10": 1.0,
            "all_required_sources_coverage": 1.0,
            "correct_refusal": False,
            "latency_ms": 10.0,
            "reranker_latency_ms": 2.0,
            "outside_manifest_count": 0,
            "invalid_temporal_count": 0,
        }
    ])
    worse = dict(base)
    worse["recall_at_10"] = 0.0
    worse["errors"] = 1
    aggregate = _aggregate_summaries([base, worse])
    assert aggregate["recall_at_10"] == 0.5
    assert aggregate["errors"] == 1


def test_reranker_manifest_verifies_pinned_custom_code(tmp_path):
    model_dir = tmp_path / "model"
    code_dir = tmp_path / "custom-code"
    model_dir.mkdir()
    code_dir.mkdir()
    model_file = model_dir / "model.safetensors"
    config_file = model_dir / "config.json"
    for path, value in (
        (model_file, b"model-bytes"),
        (config_file, b"{}"),
        (code_dir / "configuration.py", b"class NewConfig: pass\n"),
        (code_dir / "modeling.py", b"class NewForSequenceClassification: pass\n"),
    ):
        path.write_bytes(value)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "legal-retrieval-v2-reranker-manifest-v1",
                "model_id": "test/gte",
                "revision": "pinned-model-revision",
                "runtime_download_allowed": False,
                "files": {
                    "model.safetensors": {
                        "size_bytes": model_file.stat().st_size,
                        "sha256": digest(model_file),
                    },
                    "config.json": digest(config_file),
                },
                "custom_code": {
                    "repository": "test/custom-code",
                    "revision": "pinned-code-revision",
                    "local_path": str(code_dir),
                    "files": {
                        "configuration.py": digest(code_dir / "configuration.py"),
                        "modeling.py": digest(code_dir / "modeling.py"),
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    evidence = _verify_reranker_manifest(model_dir, manifest_path)
    assert evidence["model_id"] == "test/gte"
    assert evidence["custom_code"]["revision"] == "pinned-code-revision"
    assert evidence["custom_code_path"] == str(code_dir.resolve())
    assert evidence["files"]["model.safetensors"]["sha256"] == digest(model_file)


def test_reranker_manifest_rejects_custom_code_without_loader_files(tmp_path):
    model_dir = tmp_path / "model"
    code_dir = tmp_path / "custom-code"
    model_dir.mkdir()
    code_dir.mkdir()
    model_file = model_dir / "model.safetensors"
    model_file.write_bytes(b"model")
    (code_dir / "configuration.py").write_bytes(b"config")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_id": "test/gte",
                "revision": "rev",
                "runtime_download_allowed": False,
                "files": {"model.safetensors": hashlib.sha256(b"model").hexdigest()},
                "custom_code": {
                    "revision": "code-revision",
                    "local_path": str(code_dir),
                    "files": {
                        "configuration.py": hashlib.sha256(b"config").hexdigest()
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="loader_files_missing"):
        _verify_reranker_manifest(model_dir, manifest_path)


def test_experiment_selection_puts_safety_before_quality():
    safe = {
        "splits": {
            "golden-regression": {
                "answer_required_count": 10,
                "candidate_recall_at_50": 0.99,
                "recall_at_10": 0.90,
                "mrr_at_10": 0.80,
                "top5_rate": 0.70,
                "all_required_sources_coverage": 0.90,
                "errors": 0,
                "outside_manifest_count": 0,
                "invalid_temporal_count": 0,
                "latency_ms": {"p95": 1000},
            }
        }
    }
    unsafe = {
        "splits": {
            "golden-regression": {
                **safe["splits"]["golden-regression"],
                "recall_at_10": 0.99,
                "mrr_at_10": 0.99,
                "outside_manifest_count": 1,
            }
        }
    }
    assert _experiment_quality(safe, mode="m5") > _experiment_quality(unsafe, mode="m5")


def test_m5_m6_selection_excludes_production_holdout():
    cases = [
        {"case_id": "g-1", "split": "golden-regression"},
        {"case_id": "h-1", "split": "hard-negative"},
        {"case_id": "p-1", "split": "production-holdout"},
    ]
    assert [case["case_id"] for case in _development_cases(cases)] == ["g-1", "h-1"]


def test_m5_matrix_has_exact_lookup_and_issue_split_controls():
    specs = _m5_specs()
    changed = {str(item["changed"]) for item in specs}
    assert "exact_lookup_enabled" in changed
    assert "issue_split_enabled" in changed
    control = next(item for item in specs if item["id"] == "m5-control")
    assert control["exact_lookup_enabled"] is True
    assert control["issue_split_enabled"] is False


def test_m5_experiments_change_only_the_declared_retrieval_variable():
    specs = _m5_specs()
    control = next(item for item in specs if item["id"] == "m5-control")
    weighted_anchor = next(item for item in specs if item["id"] == "m5-fusion-weighted-060-040")
    invariant_keys = {
        "lexical_top_k",
        "vector_top_k",
        "fusion_strategy",
        "vector_weight",
        "lexical_weight",
        "exact_lookup_enabled",
        "issue_split_enabled",
    }
    for spec in specs:
        changed = {
            key
            for key in invariant_keys
            if spec.get(key) != control.get(key)
        }
        if spec["changed"] == "none":
            assert changed == set()
        elif spec["changed"] == "fusion_weights":
            # Weighted fusion has an explicit family anchor.  Weight members
            # are compared to that anchor, not to legacy_stack control.
            family_changed = {
                key
                for key in invariant_keys
                if spec.get(key) != weighted_anchor.get(key)
            }
            assert family_changed <= {"vector_weight", "lexical_weight"}
        elif spec["changed"] == "fusion_strategy":
            assert changed <= {"fusion_strategy"}
        else:
            # K=20 and the 0.6/0.4 weighted control are intentionally
            # retained in the matrix; their declared variable may have the
            # same value as control, but no other variable may move.
            assert changed <= {spec["changed"]}


def test_m6_parent_config_is_copied_from_selected_m5_experiment():
    parent = _m6_parent_config({
        "selected_experiment": "m5-issue-splitting",
        "experiments": [{
            "experiment_id": "m5-issue-splitting",
            "config": {
                "vector_top_k": 50,
                "lexical_top_k": 30,
                "fusion_strategy": "rrf",
                "vector_weight": 0.5,
                "lexical_weight": 0.5,
                "exact_lookup_enabled": True,
                "issue_split_enabled": True,
            },
        }],
    })
    assert parent["vector_top_k"] == 50
    assert parent["lexical_top_k"] == 30
    assert parent["fusion_strategy"] == "rrf"
    assert parent["issue_split_enabled"] is True
