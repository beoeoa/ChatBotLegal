from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_trivy_release_scan import evaluate_release, evaluate_scan


def _write_report(
    path: Path,
    *,
    image_id: str = "sha256:" + ("a" * 64),
    vulnerabilities: list[dict[str, str]] | None = None,
    package_class: str = "os-pkgs",
) -> Path:
    path.write_text(
        json.dumps(
            {
                "SchemaVersion": 2,
                "CreatedAt": "2026-07-29T00:00:00Z",
                "ArtifactName": "chatbotlegal-test:release",
                "ArtifactType": "container_image",
                "Metadata": {"ImageID": image_id},
                "Results": [
                    {
                        "Target": "test",
                        "Class": package_class,
                        "Type": "debian" if package_class == "os-pkgs" else "python-pkg",
                        "Vulnerabilities": vulnerabilities or [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _vulnerability(
    vulnerability_id: str,
    *,
    fixed_version: str = "",
    severity: str = "HIGH",
) -> dict[str, str]:
    return {
        "VulnerabilityID": vulnerability_id,
        "PkgName": "example",
        "InstalledVersion": "1.0",
        "FixedVersion": fixed_version,
        "Severity": severity,
        "Status": "affected",
    }


def test_release_scan_blocks_remediable_high_critical(tmp_path: Path) -> None:
    scan = _write_report(
        tmp_path / "scan.json",
        vulnerabilities=[_vulnerability("CVE-TEST-1", fixed_version="1.1")],
    )

    result = evaluate_release([("app", scan)])

    assert result["status"] == "BLOCKED_RELEASE"
    assert result["reason_codes"] == ["REMEDIABLE_HIGH_CRITICAL"]
    assert result["totals"]["remediable_count"] == 1


def test_release_scan_blocks_unclassified_language_finding(tmp_path: Path) -> None:
    scan = _write_report(
        tmp_path / "scan.json",
        vulnerabilities=[_vulnerability("CVE-TEST-2")],
        package_class="lang-pkgs",
    )

    result = evaluate_release([("retrieval", scan)])

    assert result["status"] == "BLOCKED_RELEASE"
    assert result["reason_codes"] == ["UNCLASSIFIED_LANGUAGE_HIGH_CRITICAL"]
    assert result["totals"]["unclassified_language_count"] == 1


def test_release_scan_requires_decision_for_unfixed_os_findings(
    tmp_path: Path,
) -> None:
    scan = _write_report(
        tmp_path / "scan.json",
        vulnerabilities=[_vulnerability("CVE-TEST-3", severity="CRITICAL")],
    )

    result = evaluate_release([("app", scan)])

    assert result["status"] == "DECISION_REQUIRED"
    assert result["reason_codes"] == [
        "UNFIXED_OS_HIGH_CRITICAL_REQUIRES_RISK_DECISION"
    ]
    assert result["policy"]["unfixed_findings_hidden"] is False


def test_release_scan_passes_only_without_high_critical_findings(
    tmp_path: Path,
) -> None:
    scan = _write_report(tmp_path / "scan.json")

    result = evaluate_release([("app", scan)])

    assert result["status"] == "PASS"
    assert result["reason_codes"] == []


def test_release_scan_rejects_missing_image_digest(tmp_path: Path) -> None:
    scan = _write_report(tmp_path / "scan.json", image_id="")

    with pytest.raises(ValueError, match="image_digest_required"):
        evaluate_scan(scan, "app")
