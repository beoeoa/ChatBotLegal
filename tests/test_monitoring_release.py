from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_monitoring_targets_required_private_services_and_has_alerts():
    config = yaml.safe_load((ROOT / "deploy/monitoring/prometheus.yml").read_text(encoding="utf-8"))
    targets = config["scrape_configs"][0]["static_configs"][0]["targets"]
    assert targets == [
        "http://api:5055/health",
        "http://api:5055/ready",
        "http://api_replica:5055/health",
        "http://api_replica:5055/ready",
        "http://frontend:8502/",
        "http://frontend_replica:8502/",
        "http://legal_retrieval:8765/health",
        "http://legal_retrieval_replica:8765/health",
    ]
    alerts = yaml.safe_load((ROOT / "deploy/monitoring/feature018-alerts.yml").read_text(encoding="utf-8"))
    names = {rule["alert"] for rule in alerts["groups"][0]["rules"]}
    assert {"Feature018ServiceUnavailable", "Feature018ProbeSlow", "Feature018MonitoringTargetMissing"} <= names


def test_monitoring_assets_are_content_free_and_not_publicly_enabled():
    content = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "deploy/monitoring").glob("*")
        if path.is_file()
    ).casefold()
    for forbidden in ("question_text", "answer_text", "authorization_header", "cookie_value", "attachment_content"):
        assert forbidden not in content
    compose = yaml.safe_load((ROOT / "docker-compose.release.yml").read_text(encoding="utf-8"))
    assert "prometheus" not in compose["services"]
    assert "grafana" not in compose["services"]
    dashboard = json.loads((ROOT / "deploy/monitoring/feature018-dashboard.json").read_text(encoding="utf-8"))
    assert "content-free" in dashboard["tags"]
