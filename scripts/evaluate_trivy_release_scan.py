"""Evaluate Trivy image reports without hiding unfixed vulnerabilities.

The release policy distinguishes three outcomes:

* ``BLOCKED_RELEASE`` when a HIGH/CRITICAL finding has an available fix, or
  when an application-language finding remains unclassified.
* ``DECISION_REQUIRED`` when the only remaining findings are unfixed
  operating-system packages. A release owner must explicitly assess those
  base-image risks; this script never auto-accepts them.
* ``PASS`` only when no HIGH/CRITICAL finding remains in the supplied reports.

Trivy should apply reviewed OpenVEX statements while producing the input
report. Findings excluded by a VEX statement therefore do not appear here.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


VALID_SEVERITIES = {"HIGH", "CRITICAL"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finding_key(vulnerability: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _text(vulnerability.get("VulnerabilityID")),
        _text(vulnerability.get("PkgName")),
        _text(vulnerability.get("InstalledVersion")),
    )


def _summarize_findings(
    findings: Iterable[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    unique: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
    raw_count = 0
    for package_class, vulnerability in findings:
        raw_count += 1
        unique.setdefault(_finding_key(vulnerability), (package_class, vulnerability))

    severity_counts: Counter[str] = Counter()
    remediable: list[dict[str, str]] = []
    language_unclassified: list[dict[str, str]] = []
    os_unfixed: list[dict[str, str]] = []

    for package_class, vulnerability in unique.values():
        severity = _text(vulnerability.get("Severity")).upper()
        if severity not in VALID_SEVERITIES:
            continue
        severity_counts[severity] += 1
        item = {
            "vulnerability_id": _text(vulnerability.get("VulnerabilityID")),
            "package": _text(vulnerability.get("PkgName")),
            "installed_version": _text(vulnerability.get("InstalledVersion")),
            "fixed_version": _text(vulnerability.get("FixedVersion")),
            "severity": severity,
            "status": _text(vulnerability.get("Status")),
        }
        if item["fixed_version"]:
            remediable.append(item)
        elif package_class == "os-pkgs":
            os_unfixed.append(item)
        else:
            language_unclassified.append(item)

    sort_key = lambda item: (
        item["severity"] != "CRITICAL",
        item["vulnerability_id"],
        item["package"],
    )
    remediable.sort(key=sort_key)
    language_unclassified.sort(key=sort_key)
    os_unfixed.sort(key=sort_key)
    return {
        "raw_finding_count": raw_count,
        "unique_high_critical_count": sum(severity_counts.values()),
        "by_severity": dict(sorted(severity_counts.items())),
        "remediable_count": len(remediable),
        "unclassified_language_count": len(language_unclassified),
        "unfixed_os_count": len(os_unfixed),
        "remediable": remediable,
        "unclassified_language": language_unclassified,
        "unfixed_os": os_unfixed,
    }


def evaluate_scan(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("SchemaVersion") != 2:
        raise ValueError(f"{label}: unsupported_trivy_schema")
    if payload.get("ArtifactType") != "container_image":
        raise ValueError(f"{label}: container_image_report_required")

    findings: list[tuple[str, dict[str, Any]]] = []
    for result in payload.get("Results") or []:
        package_class = _text(result.get("Class")) or "unknown"
        for vulnerability in result.get("Vulnerabilities") or []:
            severity = _text(vulnerability.get("Severity")).upper()
            if severity in VALID_SEVERITIES:
                findings.append((package_class, vulnerability))

    summary = _summarize_findings(findings)
    image_id = _text((payload.get("Metadata") or {}).get("ImageID"))
    if not image_id.startswith("sha256:"):
        raise ValueError(f"{label}: image_digest_required")
    return {
        "label": label,
        "report_path": path.as_posix(),
        "artifact_name": _text(payload.get("ArtifactName")),
        "image_digest": image_id,
        "scan_created_at": _text(payload.get("CreatedAt")),
        **summary,
    }


def evaluate_release(scans: list[tuple[str, Path]]) -> dict[str, Any]:
    if not scans:
        raise ValueError("at_least_one_scan_required")
    reports = [evaluate_scan(path, label) for label, path in scans]
    remediable = sum(report["remediable_count"] for report in reports)
    language = sum(report["unclassified_language_count"] for report in reports)
    os_unfixed = sum(report["unfixed_os_count"] for report in reports)

    reason_codes: list[str] = []
    if remediable:
        reason_codes.append("REMEDIABLE_HIGH_CRITICAL")
    if language:
        reason_codes.append("UNCLASSIFIED_LANGUAGE_HIGH_CRITICAL")
    if remediable or language:
        status = "BLOCKED_RELEASE"
    elif os_unfixed:
        status = "DECISION_REQUIRED"
        reason_codes.append("UNFIXED_OS_HIGH_CRITICAL_REQUIRES_RISK_DECISION")
    else:
        status = "PASS"

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason_codes": reason_codes,
        "policy": {
            "severity_scope": ["HIGH", "CRITICAL"],
            "remediable_findings": "block",
            "unclassified_language_findings": "block",
            "unfixed_os_findings": "release_owner_decision_required",
            "unfixed_findings_hidden": False,
        },
        "totals": {
            "remediable_count": remediable,
            "unclassified_language_count": language,
            "unfixed_os_count": os_unfixed,
        },
        "reports": reports,
    }


def _parse_scan(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("scan must use LABEL=PATH")
    path = Path(raw_path)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"scan report not found: {path}")
    return label.strip(), path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan",
        action="append",
        required=True,
        type=_parse_scan,
        help="Trivy JSON report as LABEL=PATH; repeat for every release image.",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = evaluate_release(args.scan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(
        f"status={result['status']} "
        f"remediable={result['totals']['remediable_count']} "
        f"language={result['totals']['unclassified_language_count']} "
        f"unfixed_os={result['totals']['unfixed_os_count']}"
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
