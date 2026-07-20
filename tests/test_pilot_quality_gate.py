import itertools

from scripts.pilot_quality_gate import (
    CONCURRENCY_LEVELS,
    DOMAINS,
    ENDPOINTS,
    ROLES,
    SCENARIO_CLASSES,
    evaluate_quality_gate,
)


def _manifest() -> dict:
    cases = []
    for domain, role, scenario_class in itertools.product(
        DOMAINS, ROLES, SCENARIO_CLASSES
    ):
        cases.append(
            {
                "review_id": f"{domain}:{role}:{scenario_class}",
                "domain": domain,
                "role": role,
                "scenario_class": scenario_class,
            }
        )
    return {
        "version": "1.0",
        "domains": list(DOMAINS),
        "roles": list(ROLES),
        "scenario_classes": list(SCENARIO_CLASSES),
        "cases": cases,
    }


def _experts(manifest: dict, *, score: float = 9.0) -> dict:
    return {
        "records": [
            {
                "review_id": item["review_id"],
                "domain": item["domain"],
                "role": item["role"],
                "question": "Synthetic legal pilot question",
                "expert_review_status": "approved",
                "expert_name": "Expert A",
                "reviewed_at": "2026-07-16T00:00:00+00:00",
                "expert_score": score,
                "expected_documents": ["60/2014/QH13"],
                "expected_articles": ["Điều 16"],
                "official_form_ids": ["form-1"],
            }
            for item in manifest["cases"]
        ]
    }


def _run(manifest: dict) -> dict:
    results = []
    for item, endpoint, concurrency in itertools.product(
        manifest["cases"], ENDPOINTS, CONCURRENCY_LEVELS
    ):
        results.append(
            {
                "review_id": item["review_id"],
                "domain": item["domain"],
                "role": item["role"],
                "endpoint": endpoint,
                "concurrency": concurrency,
                "status": "completed",
                "citation_applicable": True,
                "citation_ok": True,
                "form_applicable": True,
                "forms_ok": True,
                "critical_hallucination": False,
                "repair_used": False,
                "repair_observed": True,
            }
        )
    return {"mode": "execute", "results": results}


def test_gate_passes_only_complete_approved_matrix_at_thresholds():
    manifest = _manifest()
    report = evaluate_quality_gate(manifest, _experts(manifest), _run(manifest))

    assert report["pass"] is True
    assert report["schema_version"] == "1.0"
    assert set(report["artifact_fingerprints"]) == {
        "manifest_sha256",
        "expert_sha256",
        "run_sha256",
    }
    assert all(
        len(value) == 64 for value in report["artifact_fingerprints"].values()
    )
    assert report["coverage"]["expected_attempts"] == 240
    assert len(report["group_scores"]) == 10
    assert min(report["group_scores"].values()) == 9.0
    assert report["metrics"] == {
        "critical_hallucinations": 0,
        "citation_success_rate": 1.0,
        "form_success_rate": 1.0,
        "error_rate": 0.0,
        "repair_rate": 0.0,
    }


def test_gate_fails_closed_for_pending_expert_record():
    manifest = _manifest()
    experts = _experts(manifest)
    experts["records"][0]["expert_review_status"] = "pending"

    report = evaluate_quality_gate(manifest, experts, _run(manifest))

    assert report["pass"] is False
    assert report["checks"]["all_expert_records_approved"] is False
    assert experts["records"][0]["expert_review_status"] == "pending"


def test_gate_rejects_tampered_manifest_declarations():
    manifest = _manifest()
    manifest["roles"] = ["citizen"]

    report = evaluate_quality_gate(manifest, _experts(manifest), _run(manifest))

    assert report["checks"]["manifest_valid"] is False
    assert "manifest_roles_mismatch" in report["manifest"]["errors"]
    assert report["pass"] is False


def test_gate_enforces_group_score_and_zero_critical_hallucination():
    manifest = _manifest()
    experts = _experts(manifest)
    first_group = (manifest["cases"][0]["domain"], manifest["cases"][0]["role"])
    for item in experts["records"]:
        if (item["domain"], item["role"]) == first_group:
            item["expert_score"] = 8.99
    run = _run(manifest)
    run["results"][0]["critical_hallucination"] = True

    report = evaluate_quality_gate(manifest, experts, run)

    assert report["pass"] is False
    assert report["checks"]["all_group_scores_at_least_9"] is False
    assert report["checks"]["zero_critical_hallucinations"] is False


def test_gate_enforces_99_percent_citation_and_form_rates():
    manifest = _manifest()
    experts = _experts(manifest)

    passing = _run(manifest)
    for result in passing["results"][:2]:
        result["citation_ok"] = False
        result["forms_ok"] = False
    passing_report = evaluate_quality_gate(manifest, experts, passing)
    assert passing_report["metrics"]["citation_success_rate"] > 0.99
    assert passing_report["pass"] is True

    failing = _run(manifest)
    for result in failing["results"][:3]:
        result["citation_ok"] = False
        result["forms_ok"] = False
    failing_report = evaluate_quality_gate(manifest, experts, failing)
    assert failing_report["metrics"]["citation_success_rate"] < 0.99
    assert failing_report["checks"]["citation_success_at_least_99_percent"] is False
    assert failing_report["checks"]["form_success_at_least_99_percent"] is False


def test_gate_requires_error_below_1_percent_and_repair_below_10_percent():
    manifest = _manifest()
    experts = _experts(manifest)
    run = _run(manifest)
    for result in run["results"][:3]:
        result["status"] = "failed"
        result["citation_ok"] = False
        result["forms_ok"] = False
    for result in run["results"][3:27]:
        result["repair_used"] = True

    report = evaluate_quality_gate(manifest, experts, run)

    assert report["metrics"]["error_rate"] > 0.01
    assert report["metrics"]["repair_rate"] == 0.1
    assert report["checks"]["error_rate_below_1_percent"] is False
    assert report["checks"]["repair_rate_below_10_percent"] is False
    assert report["pass"] is False


def test_gate_fails_closed_when_per_attempt_repair_signal_is_missing():
    manifest = _manifest()
    run = _run(manifest)
    run["results"][0]["repair_observed"] = False

    report = evaluate_quality_gate(manifest, _experts(manifest), run)

    assert report["checks"]["repair_telemetry_complete"] is False
    assert report["pass"] is False
