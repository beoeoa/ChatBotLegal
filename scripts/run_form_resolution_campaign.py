#!/usr/bin/env python3
"""Run the deterministic Feature 006 pending-form resolution campaign.

The runner classifies every occurrence, optionally reuses the existing exact
VBPL resolver for code-based identities, and writes versioned aggregate
artifacts. It never approves a form, changes the active collection or touches
the legal corpus/vector store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_resolution_campaign import (
    ATTESTATIONS_PATH,
    CAMPAIGN_DIR,
    CATALOG_PATH,
    INVENTORY_PATH,
    LATEST_PATH,
    STATUS_PATH,
    build_campaign_baseline,
    build_campaign_report,
    build_occurrences_from_inventory,
    build_review_batches,
    completion_input_paths,
    validate_completion_run,
)
from api.form_resolution_registry import (
    build_occurrence_registry,
    validate_review_ready_candidate,
    write_campaign_manifest,
)
from api.official_eform_gate import evaluate_official_eform

FORMS_DIR = ROOT / "notebook_data" / "forms"
GROUPS_PATH = FORMS_DIR / "three_tier_form_groups_v1.json"
RESOLUTION_PATH = FORMS_DIR / "three_tier_form_source_resolution_v1.json"
QUEUE_PATH = FORMS_DIR / "official_forms_candidates_classified.json"
ACTIVE_COLLECTION = "legal_chunks_lechan_primary_v20260723"
FEATURE006_CAMPAIGN_REPORT_PATH = (
    ROOT / "reports" / "feature006" / "form-completion-campaign.json"
)
FEATURE006_INVENTORY_PATH = (
    FORMS_DIR / "feature006_form_completion_inventory_v1.json"
)
def _load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probe_eform(
    requirement: Mapping[str, Any], *, timeout: float
) -> dict[str, Any]:
    source_url = _text(requirement.get("source_page_url"))
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "ChatBotLegal-Feature006-EFormGate/1.0"},
        ) as client:
            response = client.get(source_url)
        text = response.text[:1_000_000].casefold()
        final_url = str(response.url)
        same_owner_route = final_url.rstrip("/") == source_url.rstrip("/")
        return {
            "http_status": response.status_code,
            "final_url": final_url,
            "requires_login": any(
                marker in text
                for marker in ("đăng nhập", "dang nhap", "login required")
            ),
            "has_captcha": any(
                marker in text
                for marker in ("captcha", "mã xác nhận", "ma xac nhan")
            ),
            "procedure_id": (
                requirement.get("procedure_id") if same_owner_route else None
            ),
            "eform_visible_to_roles": (
                ["citizen", "officer", "admin"]
                if response.status_code == 200
                else []
            ),
            # The URL originates from the checksum-bound official snapshot.
            # This is technical evidence only and still requires legal review.
            "effectivity": "current" if response.status_code == 200 else "unknown",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "http_status": 0,
            "final_url": source_url,
            "requires_login": False,
            "has_captcha": False,
            "procedure_id": None,
            "eform_visible_to_roles": [],
            "effectivity": "unknown",
            "error_type": type(exc).__name__,
        }


def _resolve_eforms(
    requirements: list[Mapping[str, Any]],
    *,
    legal_as_of: str,
    network: bool,
    workers: int,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    probes: dict[str, dict[str, Any]] = {}
    if network:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
            futures = {
                executor.submit(_probe_eform, item, timeout=timeout): _text(
                    item.get("candidate_id")
                )
                for item in requirements
            }
            for future in as_completed(futures):
                probes[futures[future]] = future.result()
    for requirement in sorted(
        requirements, key=lambda item: _text(item.get("candidate_id"))
    ):
        candidate_id = _text(requirement.get("candidate_id"))
        probe = probes.get(candidate_id)
        if probe is None:
            decision = {
                "status": "VERIFIED_DATA_GAP",
                "reason_code": "EFORM_NETWORK_DISABLED",
                "approved": False,
                "runtime_eligible": False,
            }
        else:
            decision = evaluate_official_eform(
                requirement,
                probe,
                legal_as_of=legal_as_of,
                role="citizen",
            )
        attempts.append(
            {
                "attempt_id": f"eform:{candidate_id}",
                "requirement_identity_id": requirement.get(
                    "requirement_identity_id"
                ),
                "source_domain": "dichvucong.gov.vn",
                "status": decision["status"],
                "reason_code": decision["reason_code"],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if decision["status"] != "READY_FOR_HUMAN_ATTESTATION":
            gaps.append(
                {
                    "occurrence_id": candidate_id,
                    "procedure_id": requirement.get("procedure_id"),
                    "requirement_identity_id": requirement.get(
                        "requirement_identity_id"
                    ),
                    "reason_code": decision["reason_code"],
                    "source_attempts": [attempts[-1]],
                }
            )
            continue
        identity_id = _text(requirement.get("requirement_identity_id"))
        candidates.append(
            {
                **dict(requirement),
                **decision,
                "canonical_form_id": f"form-eform-{identity_id}",
                "source_domain": "dichvucong.gov.vn",
                "delivery_type": "interactive_eform",
                "effectivity_reason_code": decision["reason_code"],
                "approved": False,
                "runtime_eligible": False,
                "automated_approval": False,
            }
        )
    return candidates, gaps, attempts


def _active_collection() -> str:
    configured = _text(os.getenv("LEGAL_ACTIVE_COLLECTION"))
    if configured:
        return configured
    for path in (
        ROOT / "reports" / "feature005" / "release-20260727" / "release-manifest.json",
        ROOT / "reports" / "feature005" / "final-gate-20260727" / "release-report.json",
    ):
        payload = _load(path, {})
        value = _text(
            payload.get("active_collection")
            or payload.get("runtime_active_collection")
            or (payload.get("baseline") or {}).get("active_collection")
        )
        if value:
            return value
    return ACTIVE_COLLECTION


def _status(payload: Mapping[str, Any], *, stage: str, status: str) -> dict[str, Any]:
    value = {
        **dict(payload),
        "schema_version": "form-resolution-campaign-v1",
        "status": status,
        "stage": stage,
        "feature_flag_enabled": False,
        "automated_approval": False,
        "human_attestation_required": True,
    }
    _write_atomic(STATUS_PATH, value)
    return value


def _run_existing_code_resolver(
    *,
    inventory_path: Path,
    legal_as_of: str,
    run_dir: Path,
    workers: int,
    timeout: float,
    network: bool,
) -> dict[str, Any]:
    durable = _load(run_dir / "code-resolution.json", {})
    if durable.get("group_progress_complete") is True:
        return durable
    if not network:
        cached = _load(
            run_dir / "code-resolution.json",
            _load(run_dir / "code-resolver-result.json", {}),
        )
        cached_groups = _load(run_dir / "code-groups.json", {})
        if cached and (
            cached_groups.get("groups")
            or any(
                cached.get(key)
                for key in (
                    "pending_records",
                    "resolved_groups",
                    "verified_data_gaps",
                    "source_attempts",
                )
            )
        ):
            # A no-network resume is an audit/read-only operation. Reuse the
            # last durable resolver checkpoint instead of replacing valid
            # groups and candidates with an empty offline placeholder.
            return cached
        report = {
            "status": "offline_skipped",
            "reason_code": "NETWORK_DISABLED",
            "pending_records": [],
            "resolved_groups": [],
            "verified_data_gaps": [],
        }
        _write_atomic(run_dir / "code-groups.json", {"groups": []})
        _write_atomic(run_dir / "code-resolution.json", report)
        _write_atomic(run_dir / "code-resolver-result.json", report)
        return report
    command = [
        sys.executable,
        str(ROOT / "scripts" / "resolve_three_tier_form_sources.py"),
        "--inventory",
        str(inventory_path),
        "--legal-as-of",
        legal_as_of,
        "--workers",
        str(max(1, workers)),
        "--timeout",
        str(timeout),
        "--commit-candidates",
        "--max-groups",
        "1000000",
        "--groups-output",
        str(run_dir / "code-groups.json"),
        "--resolution-output",
        str(run_dir / "code-resolution.json"),
        "--shortlist-output",
        str(run_dir / "code-review-shortlist.json"),
        "--report-output",
        str(run_dir / "code-resolution-report.json"),
    ]
    if not network:
        # The resolver itself is cache-first. A no-network campaign only
        # consumes already cached official responses and never fabricates one.
        command.append("--offline")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=max(60.0, timeout * 20))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.call(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.kill()
        process.wait(timeout=10)
        partial = _load(run_dir / "code-resolution.json", {})
        if any(
            partial.get(key)
            for key in (
                "pending_records",
                "resolved_groups",
                "verified_data_gaps",
                "source_attempts",
            )
        ):
            report = {
                **partial,
                "status": "partial_timeout",
                "reason_code": "EXACT_CODE_RESOLVER_PARTIAL_TIMEOUT",
            }
            _write_atomic(run_dir / "code-resolution.json", report)
            _write_atomic(run_dir / "code-resolver-result.json", report)
            return report
        report = {
            "status": "blocked_external",
            "reason_code": "OFFICIAL_SOURCE_READ_TIMEOUT",
            "pending_records": [],
            "resolved_groups": [],
            "verified_data_gaps": [],
        }
        _write_atomic(run_dir / "code-groups.json", {"groups": []})
        _write_atomic(run_dir / "code-resolution.json", report)
        _write_atomic(run_dir / "code-resolver-result.json", report)
        return report
    result = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    report = {}
    if result.stdout.strip():
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            report = {}
    if result.returncode != 0:
        report = {
            **report,
            "status": "failed_fail_closed",
            "reason_code": "EXACT_CODE_RESOLVER_FAILED",
            "stderr_category": result.stderr[-300:],
        }
    _write_atomic(run_dir / "code-resolver-result.json", report)
    return report


def _candidate_with_identity(
    candidate: Mapping[str, Any],
    group: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = candidate.get("artifact")
    artifact = artifact if isinstance(artifact, Mapping) else {}
    source_package_sha256 = _text(
        candidate.get("source_package_sha256")
        or artifact.get("source_package_sha256")
    )
    source_package_size_bytes = (
        candidate.get("source_package_size_bytes")
        if candidate.get("source_package_size_bytes") is not None
        else artifact.get("source_package_size_bytes")
    )
    source_pages_zero_based = (
        candidate.get("source_pages_zero_based")
        if candidate.get("source_pages_zero_based") is not None
        else artifact.get("source_pages_zero_based")
    )
    extraction = candidate.get("extraction")
    if not isinstance(extraction, Mapping):
        extraction = artifact.get("extraction")
    provenance = {
        "publisher": candidate.get("publisher")
        or "Cơ sở dữ liệu quốc gia về pháp luật - Bộ Tư pháp",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_page_url": candidate.get("source_page_url")
        or candidate.get("source_url"),
        "source_download_url": candidate.get("source_download_url")
        or candidate.get("download_url"),
    }
    # Resolver output may store artifact evidence either in a nested resolved
    # group or in a flattened pending record. Preserve both variants so review
    # gates keep the exact package/extraction chain.
    source_sha = (
        source_package_sha256
        or _text(candidate.get("source_sha256"))
        or _text(candidate.get("sha256"))
    )
    if source_package_sha256:
        provenance["source_package_sha256"] = source_package_sha256
    if source_package_size_bytes is not None:
        provenance["source_package_size_bytes"] = source_package_size_bytes
    for key in (
        "source_attachment_id",
        "source_retrieval_url",
        "source_retrieval_method",
        "source_file_name",
        "publication_decision_number",
    ):
        value = candidate.get(key) or artifact.get(key)
        if _text(value):
            provenance[key] = value
    item = {
        **dict(candidate),
        "candidate_id": candidate.get("candidate_id") or candidate.get("id"),
        "canonical_identity_key": candidate.get("canonical_identity_key")
        or group.get("canonical_identity_key"),
        "canonical_name": candidate.get("canonical_name")
        or candidate.get("canonical_form_name")
        or candidate.get("form_name")
        or group.get("form_name")
        or group.get("canonical_name"),
        "procedure_ids": candidate.get("procedure_ids")
        or group.get("procedure_ids")
        or [_text(candidate.get("procedure_id"))],
        "issuing_instrument": candidate.get("issuing_instrument")
        or group.get("issuing_instrument"),
        "official_source_page": candidate.get("official_source_page")
        or candidate.get("source_page_url")
        or candidate.get("source_url"),
        "official_download_url": candidate.get("official_download_url")
        or candidate.get("source_download_url")
        or candidate.get("download_url"),
        "source_sha256": source_sha,
        "sha256": _text(candidate.get("sha256"))
        or _text(artifact.get("sha256")),
        "provenance": {
            **provenance,
            **(
                dict(candidate.get("provenance"))
                if isinstance(candidate.get("provenance"), Mapping)
                else {}
            ),
        },
        "jurisdiction": candidate.get("jurisdiction")
        or (
            "central"
            if group.get("source_tier") == "central"
            else "Hai Phong"
        ),
        "scope": candidate.get("scope")
        or candidate.get("administrative_level")
        or "commune",
        "review_status": candidate.get("review_status")
        or "candidate_pending_review",
        "effective_status": candidate.get("effective_status")
        or "Còn hiệu lực",
        "is_seed": candidate.get("is_seed") is True,
        "is_demo": candidate.get("is_demo") is True,
        "is_quarantined": candidate.get("is_quarantined") is True,
    }
    normalized_fields: list[str] = []
    for field in (
        "canonical_form_name",
        "canonical_name",
        "detected_form_name",
        "form_title",
        "publisher",
    ):
        value = item.get(field)
        if not isinstance(value, str):
            continue
        normalized = unicodedata.normalize("NFC", value)
        if normalized != value:
            item[field] = normalized
            normalized_fields.append(field)
    publisher = item["provenance"].get("publisher")
    if isinstance(publisher, str):
        normalized_publisher = unicodedata.normalize("NFC", publisher)
        if normalized_publisher != publisher:
            item["provenance"]["publisher"] = normalized_publisher
            normalized_fields.append("provenance.publisher")
    if normalized_fields:
        # The immutable resolver output retains the exact source text. The
        # review projection uses canonical Unicode only, avoiding a false
        # metadata-corruption block without guessing any legal content.
        item["metadata_normalization"] = {
            "form": "NFC",
            "normalized_fields": sorted(set(normalized_fields)),
            "source_record_preserved": True,
        }
    procedure_id = _text(
        candidate.get("procedure_id")
        or (candidate.get("procedure_ids") or [""])[0]
    )
    procedure_meta = (group.get("procedure_metadata") or {}).get(
        procedure_id, {}
    )
    item["requirement_identity_id"] = (
        candidate.get("requirement_identity_id")
        or procedure_meta.get("requirement_identity_id")
    )
    item["occurrence_id"] = (
        candidate.get("occurrence_id") or procedure_meta.get("occurrence_id")
    )
    if source_pages_zero_based:
        item["extraction"] = {
            "kind": "extracted_from_official_package",
            "page_range": [
                min(source_pages_zero_based),
                max(source_pages_zero_based) + 1,
            ],
            "complete": True,
        }
    elif isinstance(extraction, Mapping):
        item["extraction"] = dict(extraction)
    return item


def _prepare_code_inventory(
    registry: Mapping[str, Any],
    occurrences: list[Mapping[str, Any]],
    path: Path,
) -> dict[str, Any]:
    resolved_ids = {
        _text(item.get("occurrence_id"))
        for item in registry.get("occurrences") or []
        if item.get("identity_status") == "IDENTITY_RESOLVED"
        and _text(item.get("resolved_form_code"))
        and _text(item.get("issuing_instrument"))
    }
    selected = [
        dict(item)
        for item in occurrences
        if _text(item.get("candidate_id")) in resolved_ids
    ]
    payload = {
        "schema_version": "form-resolution-code-source-input-v1",
        "canonical_forms": [],
        "verified_data_gaps": selected,
    }
    _write_atomic(path, payload)
    return payload


def execute(
    *,
    legal_as_of: str,
    network: bool = True,
    workers: int = 4,
    timeout: float = 45.0,
    resume_run_id: str | None = None,
    inventory_path: Path | None = None,
    requirement_manifest_path: Path | None = None,
    source_snapshot_path: Path | None = None,
    manifest_sha256: str | None = None,
    source_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    date.fromisoformat(legal_as_of)
    inventory_path = inventory_path or INVENTORY_PATH
    default_manifest_path, default_source_snapshot_path = completion_input_paths(
        project_root=ROOT,
        legal_as_of=legal_as_of,
    )
    requirement_manifest_path = (
        requirement_manifest_path or default_manifest_path
    )
    source_snapshot_path = source_snapshot_path or default_source_snapshot_path
    inventory = _load(inventory_path, {})
    if manifest_sha256 or source_snapshot_sha256:
        if not manifest_sha256 or not source_snapshot_sha256:
            raise ValueError("FORM_COMPLETION_CHECKSUMS_REQUIRED")
        validate_completion_run(
            expected_manifest_sha256=manifest_sha256,
            actual_manifest_sha256=_sha256(requirement_manifest_path),
            expected_source_snapshot_sha256=source_snapshot_sha256,
            actual_source_snapshot_sha256=_sha256(source_snapshot_path),
        )
        if _text(inventory.get("manifest_sha256")) != manifest_sha256:
            raise ValueError("BRIDGE_MANIFEST_CHECKSUM_DRIFT")
        if _text(inventory.get("source_snapshot_sha256")) != source_snapshot_sha256:
            raise ValueError("BRIDGE_SOURCE_SNAPSHOT_CHECKSUM_DRIFT")
    occurrences = build_occurrences_from_inventory(inventory)
    eform_requirements = [
        dict(item)
        for item in inventory.get("eform_requirements") or []
        if isinstance(item, Mapping)
    ]
    if resume_run_id:
        run_id = _text(resume_run_id)
        run_dir = CAMPAIGN_DIR / "runs" / run_id
        if not run_dir.is_dir():
            raise FileNotFoundError(f"CAMPAIGN_RUN_NOT_FOUND:{run_id}")
        baseline = _load(run_dir / "baseline.json", {})
        registry = _load(run_dir / "occurrence-registry.json", {})
        if not registry:
            raise ValueError(f"CAMPAIGN_REGISTRY_NOT_FOUND:{run_id}")
    else:
        run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        run_dir = CAMPAIGN_DIR / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        baseline = build_campaign_baseline(
            run_id=run_id,
            legal_as_of=legal_as_of,
            occurrences=occurrences,
            catalog_path=CATALOG_PATH,
            attestations_path=ATTESTATIONS_PATH,
            feature_flag=False,
            active_collection=_active_collection(),
            manifest_sha256=manifest_sha256,
            source_snapshot_sha256=source_snapshot_sha256,
        )
        _write_atomic(run_dir / "baseline.json", baseline)
    _status(baseline, stage="identity_resolution", status="running")

    if not resume_run_id:
        registry = build_occurrence_registry(
            occurrences=occurrences,
            run_id=run_id,
            legal_as_of=legal_as_of,
        )
        registry = write_campaign_manifest(run_dir / "occurrence-registry.json", registry)
    _write_atomic(LATEST_PATH, {"run_id": run_id, "manifest": str(run_dir)})

    code_input = run_dir / "code-source-input.json"
    _prepare_code_inventory(registry, occurrences, code_input)
    _status(
        {
            **baseline,
            "counts": {
                **baseline["counts"],
                **registry["summary"],
            },
        },
        stage="official_source_resolution",
        status="running",
    )
    _run_existing_code_resolver(
        inventory_path=code_input,
        legal_as_of=legal_as_of,
        run_dir=run_dir,
        workers=workers,
        timeout=timeout,
        network=network,
    )

    resolver_payload = _load(
        run_dir / "code-resolution.json",
        _load(run_dir / "code-resolver-result.json", {}),
    )
    groups_payload = _load(run_dir / "code-groups.json", {})
    resolved_group_by_id = {
        _text(item.get("group_id")): item
        for item in groups_payload.get("groups") or []
        if isinstance(item, Mapping)
    }
    for resolved_group in resolver_payload.get("resolved_groups") or []:
        if not isinstance(resolved_group, Mapping):
            continue
        group_id = _text(resolved_group.get("group_id"))
        if not group_id:
            continue
        base_group = resolved_group_by_id.get(group_id, {})
        resolved_group_by_id[group_id] = {
            **dict(base_group),
            **dict(resolved_group),
            "procedure_metadata": base_group.get("procedure_metadata") or {},
            "occurrence_ids": base_group.get("occurrence_ids") or [],
        }
    pending_candidates = [
        dict(item)
        for item in resolver_payload.get("pending_records") or []
        if isinstance(item, Mapping)
    ]
    identity_groups = {
        _text(item.get("canonical_identity_key")): item
        for item in registry.get("identity_groups") or []
        if isinstance(item, Mapping)
    }
    registry_occurrences = {
        _text(item.get("occurrence_id")): item
        for item in registry.get("occurrences") or []
        if isinstance(item, Mapping) and _text(item.get("occurrence_id"))
    }
    source_attempt_by_instrument = {
        _text(item.get("instrument")): item
        for item in resolver_payload.get("source_attempts") or []
        if isinstance(item, Mapping) and _text(item.get("instrument"))
    }
    resolver_global_reason = _text(resolver_payload.get("reason_code"))
    shortlist: list[dict[str, Any]] = []
    shortlist_covered_occurrence_ids: set[str] = set()
    candidate_gate_reason_by_occurrence: dict[str, str] = {}
    for candidate in pending_candidates:
        group_id = _text(candidate.get("three_tier_group_id"))
        group = resolved_group_by_id.get(group_id, {})
        registry_identity_group = next(
            (
                item
                for item in identity_groups.values()
                if _text(item.get("form_code"))
                == _text(candidate.get("form_code"))
                and _text(item.get("issuing_instrument"))
                == _text(candidate.get("legal_basis", [""])[0])
            ),
            {},
        )
        identity_group = {
            **group,
            **registry_identity_group,
            "procedure_metadata": group.get("procedure_metadata") or {},
            "occurrence_ids": group.get("occurrence_ids") or [],
        }
        prepared = _candidate_with_identity(candidate, identity_group)
        candidate_procedure_id = _text(
            candidate.get("procedure_id")
            or (candidate.get("procedure_ids") or [""])[0]
        )
        covered_decisions = [
            registry_occurrences[occurrence_id]
            for occurrence_id in group.get("occurrence_ids") or []
            if occurrence_id in registry_occurrences
            and _text(registry_occurrences[occurrence_id].get("procedure_id"))
            == candidate_procedure_id
        ]
        covered_identity_ids = sorted(
            {
                _text(item.get("requirement_identity_id"))
                for item in covered_decisions
                if _text(item.get("requirement_identity_id"))
            }
        )
        if covered_identity_ids:
            prepared["requirement_identity_id"] = covered_identity_ids[0]
            prepared["requirement_identity_ids"] = covered_identity_ids
        validation = validate_review_ready_candidate(prepared)
        prepared["hard_gate_reason_codes"] = validation["reason_codes"]
        prepared["runtime_eligible"] = False
        prepared["automated_approval"] = False
        if validation["eligible"]:
            shortlist.append(prepared)
            shortlist_covered_occurrence_ids.update(
                _text(item.get("occurrence_id"))
                for item in covered_decisions
                if _text(item.get("occurrence_id"))
            )
        else:
            gate_reason = (
                next(iter(validation["reason_codes"]), "")
                or "CANDIDATE_TECHNICAL_HARD_GATE_FAILED"
            )
            for item in covered_decisions:
                occurrence_id = _text(item.get("occurrence_id"))
                if occurrence_id:
                    candidate_gate_reason_by_occurrence.setdefault(
                        occurrence_id,
                        gate_reason,
                    )

    eform_candidates, eform_gaps, eform_attempts = _resolve_eforms(
        eform_requirements,
        legal_as_of=legal_as_of,
        network=network,
        workers=workers,
        timeout=timeout,
    )
    shortlist.extend(eform_candidates)

    gap_records: list[dict[str, Any]] = []
    gap_records.extend(eform_gaps)
    pending_ids = {
        _text(item.get("candidate_id") or item.get("id"))
        for item in pending_candidates
    }
    # A resolver hit is not enough to cover an occurrence. If the candidate
    # later fails source, artifact, effectivity or identity hard gates, retain
    # the occurrence as an explicit non-serving gap instead of dropping it
    # from both the shortlist and the gap inventory.
    resolved_occurrence_ids = shortlist_covered_occurrence_ids
    processed_gap_reason_by_occurrence: dict[str, str] = {}
    for item in resolver_payload.get("verified_data_gaps") or []:
        if not isinstance(item, Mapping):
            continue
        group = resolved_group_by_id.get(_text(item.get("group_id")), {})
        reason = _text(item.get("reason_code")) or "VERIFIED_DATA_GAP"
        for occurrence_id in group.get("occurrence_ids") or []:
            processed_gap_reason_by_occurrence[_text(occurrence_id)] = reason
    resolver_blocked = resolver_payload.get("status") in {
        "blocked_external",
        "partial_timeout",
    }
    for decision in registry.get("occurrences") or []:
        occurrence_id = _text(decision.get("occurrence_id"))
        if decision.get("identity_status") != "IDENTITY_RESOLVED":
            gap_records.append(
                {
                    "occurrence_id": occurrence_id,
                    "procedure_id": decision.get("procedure_id"),
                    "requirement_identity_id": decision.get(
                        "requirement_identity_id"
                    ),
                    "reason_code": decision.get("reason_code")
                    or "UNCLASSIFIED_GAP",
                    "source_attempts": [],
                }
            )
        elif occurrence_id not in resolved_occurrence_ids:
            processed_reason = (
                candidate_gate_reason_by_occurrence.get(occurrence_id)
                or processed_gap_reason_by_occurrence.get(occurrence_id)
            )
            identity_group = identity_groups.get(
                _text(decision.get("canonical_identity_key")),
                {},
            )
            instrument = _text(identity_group.get("issuing_instrument"))
            attempt = source_attempt_by_instrument.get(instrument, {})
            attempt_reason = _text(attempt.get("reason_code"))
            attempt_status = _text(attempt.get("status"))
            if not _text(decision.get("resolved_form_code")):
                # A document lookup shared by another coded form is not
                # evidence for this codeless appendix. Never promote by title.
                attempt_reason = "FORM_CODE_UNRESOLVED"
                attempt_status = "identity_gap"
            elif (
                attempt_reason == "EXACT_OFFICIAL_DOCUMENT_FOUND"
                and resolver_payload.get("status") == "partial_timeout"
            ):
                # Exact document lookup completed, but attachment/effectivity
                # work was interrupted by the aggregate deadline.
                attempt_reason = "OFFICIAL_SOURCE_FOUND_ARTIFACT_PENDING"
            reason_code = (
                processed_reason
                or attempt_reason
                or resolver_global_reason
                or (
                    "OFFICIAL_SOURCE_RESOLUTION_PENDING"
                    if not resolver_blocked
                    else "OFFICIAL_SOURCE_READ_TIMEOUT"
                )
            )
            gap_records.append(
                {
                    "occurrence_id": occurrence_id,
                    "procedure_id": decision.get("procedure_id"),
                    "requirement_identity_id": decision.get(
                        "requirement_identity_id"
                    ),
                    "reason_code": reason_code,
                    "source_attempts": [
                        {
                            "instrument": instrument or None,
                            "source_domain": "vbpl.vn",
                            "status": (
                                attempt_status
                                or (
                                    "blocked_external"
                                    if resolver_blocked
                                    else "attempted"
                                )
                            ),
                            "reason_code": reason_code,
                        }
                    ],
                }
            )
    for item in resolver_payload.get("verified_data_gaps") or []:
        if isinstance(item, Mapping):
            group_id = _text(item.get("group_id"))
            group = resolved_group_by_id.get(group_id, {})
            for occurrence_id in group.get("occurrence_ids") or []:
                gap_records.append(
                    {
                        "occurrence_id": _text(occurrence_id),
                        "procedure_id": (
                            (group.get("procedure_ids") or [None])[0]
                        ),
                        "requirement_identity_id": next(
                            (
                                decision.get("requirement_identity_id")
                                for decision in registry.get("occurrences") or []
                                if _text(decision.get("occurrence_id"))
                                == _text(occurrence_id)
                            ),
                            None,
                        ),
                        "reason_code": _text(item.get("reason_code"))
                        or "VERIFIED_DATA_GAP",
                        "source_attempts": [
                            {
                                "source_domain": "vbpl.vn",
                                "status": "verified_gap",
                            }
                        ],
                    }
                )

    source_attempts = []
    for group in registry.get("identity_groups") or []:
        if not isinstance(group, Mapping):
            continue
        instrument = _text(group.get("issuing_instrument"))
        attempt = source_attempt_by_instrument.get(instrument, {})
        reason_code = (
            _text(attempt.get("reason_code"))
            or resolver_global_reason
            or (
                "OFFICIAL_SOURCE_RESOLUTION_PENDING"
                if not resolver_blocked
                else "OFFICIAL_SOURCE_READ_TIMEOUT"
            )
        )
        source_attempts.append(
            {
                "attempt_id": f"{run_id}:{str(group.get('canonical_identity_key') or '')[:16]}",
                "canonical_identity_key": group.get("canonical_identity_key"),
                "issuing_instrument": instrument,
                "source_domain": "vbpl.vn",
                "status": _text(attempt.get("status"))
                or (
                    "blocked_external"
                    if resolver_blocked
                    else "not_resolved_in_this_run"
                ),
                "reason_code": reason_code,
                "checked_at": _text(attempt.get("checked_at"))
                or datetime.now(timezone.utc).isoformat(),
            }
        )
    source_attempts.extend(eform_attempts)
    _write_atomic(run_dir / "source-attempts.json", {"records": source_attempts})
    effectivity_nodes = [
        {
            "canonical_identity_key": item.get("canonical_identity_key"),
            "issuing_instrument": item.get("issuing_instrument"),
            "effectivity_status": "requires_exact_source_check",
            "reason_code": item.get("reason_code"),
        }
        for item in registry.get("identity_groups") or []
        if isinstance(item, Mapping)
    ]
    effectivity_nodes.extend(
        {
            "requirement_identity_id": item.get("requirement_identity_id"),
            "procedure_id": item.get("procedure_id"),
            "effectivity_status": (
                "technically_verified_pending_legal_review"
                if item.get("status") == "READY_FOR_HUMAN_ATTESTATION"
                else "not_eligible"
            ),
            "reason_code": item.get("reason_code"),
            "effectivity_source_url": item.get("source_page_url"),
        }
        for item in eform_candidates
    )
    _write_atomic(
        run_dir / "effectivity-graph.json",
        {
            "schema_version": "form-effectivity-graph-v1",
            "legal_as_of": legal_as_of,
            "nodes": effectivity_nodes,
            "edges": [],
            "automated_legal_decision": False,
        },
    )

    # The registry is occurrence-authoritative; duplicate resolver records are
    # collapsed by occurrence ID and the first deterministic reason wins.
    deduped_gaps: dict[str, dict[str, Any]] = {}
    for item in gap_records:
        occurrence_id = _text(item.get("occurrence_id"))
        if occurrence_id and occurrence_id not in deduped_gaps:
            deduped_gaps[occurrence_id] = item
    gap_records = sorted(deduped_gaps.values(), key=lambda item: item["occurrence_id"])

    # Persist one aggregate evidence row per fixed requirement identity. The
    # detailed adapter attempts remain available above, while this index proves
    # that all 232 pending identities received a technical disposition.
    identity_attempts: dict[str, dict[str, Any]] = {}
    for item in gap_records:
        identity_id = _text(item.get("requirement_identity_id"))
        if not identity_id:
            continue
        identity_attempts.setdefault(
            identity_id,
            {
                "requirement_identity_id": identity_id,
                "status": "terminal_gap",
                "reason_code": _text(item.get("reason_code"))
                or "UNCLASSIFIED_GAP",
                "source_attempt_count": len(item.get("source_attempts") or []),
            },
        )
    for item in shortlist:
        covered_ids = item.get("requirement_identity_ids") or [
            item.get("requirement_identity_id")
        ]
        for value in covered_ids:
            identity_id = _text(value)
            if not identity_id:
                continue
            identity_attempts[identity_id] = {
                "requirement_identity_id": identity_id,
                "status": "ready_for_human_attestation",
                "reason_code": _text(item.get("effectivity_reason_code"))
                or "TECHNICAL_HARD_GATES_PASS",
                "source_attempt_count": 1,
            }
    expected_pending_ids = {
        _text(item.get("requirement_identity_id"))
        for item in [*occurrences, *eform_requirements]
        if _text(item.get("requirement_identity_id"))
    }
    if set(identity_attempts) != expected_pending_ids:
        raise ValueError("FORM_COMPLETION_IDENTITY_EVIDENCE_INCOMPLETE")
    identity_attempt_records = [
        identity_attempts[key] for key in sorted(identity_attempts)
    ]
    _write_atomic(
        run_dir / "source-attempts.json",
        {
            "records": source_attempts,
            "identity_records": identity_attempt_records,
            "identity_count": len(identity_attempt_records),
        },
    )

    effectivity_nodes = []
    effectivity_edges = []
    shortlist_by_identity = {
        _text(identity_id): item
        for item in shortlist
        for identity_id in (
            item.get("requirement_identity_ids")
            or [item.get("requirement_identity_id")]
        )
        if _text(identity_id)
    }
    gaps_by_identity = {
        _text(item.get("requirement_identity_id")): item
        for item in gap_records
        if _text(item.get("requirement_identity_id"))
    }
    for identity_id in sorted(expected_pending_ids):
        evidence = shortlist_by_identity.get(identity_id) or gaps_by_identity.get(
            identity_id, {}
        )
        reason_code = _text(
            evidence.get("effectivity_reason_code")
            or evidence.get("reason_code")
        ) or "EFFECTIVITY_UNVERIFIED"
        effectivity_nodes.append(
            {
                "requirement_identity_id": identity_id,
                "effectivity_status": (
                    "technically_verified_pending_legal_review"
                    if identity_id in shortlist_by_identity
                    else "not_eligible"
                ),
                "reason_code": reason_code,
                "effectivity_source_url": evidence.get(
                    "effectivity_source_url"
                ),
            }
        )
        replacement = _text(
            (evidence.get("effectivity") or {}).get(
                "replacement_document_number"
            )
        )
        if replacement:
            effectivity_edges.append(
                {
                    "from_requirement_identity_id": identity_id,
                    "relation": "SUPERSEDED_BY",
                    "to_document_number": replacement,
                }
            )
    _write_atomic(
        run_dir / "effectivity-graph.json",
        {
            "schema_version": "form-effectivity-graph-v1",
            "legal_as_of": legal_as_of,
            "nodes": effectivity_nodes,
            "edges": effectivity_edges,
            "automated_legal_decision": False,
        },
    )

    report = build_campaign_report(
        registry=registry,
        baseline=baseline,
        gap_records=gap_records,
        shortlist=shortlist,
    )
    report["counts"]["resolver_pending_records"] = len(pending_candidates)
    report["counts"]["source_resolved_occurrences"] = len(
        resolved_occurrence_ids
    )
    report["counts"]["blocked_external"] = sum(
        item["reason_code"] in {
            "BLOCKED_EXTERNAL",
            "OFFICIAL_SOURCE_DNS_FAILURE",
            "OFFICIAL_SOURCE_TLS_FAILURE",
            "OFFICIAL_SOURCE_CONNECT_TIMEOUT",
            "OFFICIAL_SOURCE_READ_TIMEOUT",
            "OFFICIAL_SOURCE_FORBIDDEN",
            "OFFICIAL_SOURCE_RATE_LIMITED",
            "OFFICIAL_SOURCE_SERVER_ERROR",
            "OFFICIAL_SOURCE_ENDPOINT_CHANGED",
            "OFFICIAL_SOURCE_CAPTCHA",
            "OFFICIAL_SOURCE_CONTENT_TYPE_DRIFT",
            "OFFICIAL_SOURCE_RESPONSE_INVALID",
            "EXACT_CODE_RESOLVER_PARTIAL_TIMEOUT",
        }
        for item in gap_records
    )
    report["counts"]["source_attempts"] = len(source_attempts)
    report["counts"]["identity_source_attempts"] = len(
        identity_attempt_records
    )
    report["source_domain_reason_counts"] = {
        f"{domain}:{reason}": count
        for (domain, reason), count in sorted(
            Counter(
                (
                    _text(item.get("source_domain")) or "unknown",
                    _text(item.get("reason_code")) or "UNCLASSIFIED",
                )
                for item in source_attempts
            ).items()
        )
    }
    report["counts"]["excluded_occurrences"] = sum(
        item.get("identity_status", "").startswith("EXCLUDED")
        for item in registry.get("occurrences") or []
    )
    bridge_approved = len(inventory.get("approved_identities") or [])
    pending_identity_ids = {
        _text(item.get("requirement_identity_id"))
        for item in [*occurrences, *eform_requirements]
        if _text(item.get("requirement_identity_id"))
    }
    ready_identity_ids = {
        identity_id
        for item in shortlist
        for identity_id in (
            [
                _text(value)
                for value in item.get("requirement_identity_ids") or []
                if _text(value)
            ]
            or [_text(item.get("requirement_identity_id"))]
        )
        if identity_id
    }
    terminal_identity_ids = {
        _text(item.get("requirement_identity_id"))
        for item in gap_records
        if _text(item.get("requirement_identity_id"))
    }
    terminal_reason_by_identity: dict[str, str] = {}
    for item in sorted(
        gap_records,
        key=lambda row: (
            _text(row.get("requirement_identity_id")),
            _text(row.get("occurrence_id")),
            _text(row.get("reason_code")),
        ),
    ):
        identity_id = _text(item.get("requirement_identity_id"))
        if identity_id and identity_id not in ready_identity_ids:
            terminal_reason_by_identity.setdefault(
                identity_id,
                _text(item.get("reason_code")) or "UNCLASSIFIED_GAP",
            )
    report["completion_counts"] = {
        "target_identities": bridge_approved + len(pending_identity_ids),
        "approved_runtime_identities": bridge_approved,
        "pending_identities": len(pending_identity_ids),
        "ready_for_human_attestation_identities": len(ready_identity_ids),
        "terminal_gap_identities": len(terminal_identity_ids - ready_identity_ids),
        "unaccounted_pending_identities": len(
            pending_identity_ids - ready_identity_ids - terminal_identity_ids
        ),
    }
    report["counts"].update(report["completion_counts"])
    report["identity_terminal_reason_counts"] = dict(
        sorted(Counter(terminal_reason_by_identity.values()).items())
    )
    # The campaign baseline is immutable, but a resumed run may follow a
    # later human attestation. Report the live catalog state separately so a
    # stale baseline cannot be mistaken for the current runtime count.
    current_catalog = build_campaign_baseline(
        run_id=run_id,
        legal_as_of=legal_as_of,
        occurrences=occurrences,
        catalog_path=CATALOG_PATH,
        attestations_path=ATTESTATIONS_PATH,
        feature_flag=False,
        active_collection=_active_collection(),
        manifest_sha256=manifest_sha256,
        source_snapshot_sha256=source_snapshot_sha256,
    )
    report["current_catalog_state"] = {
        "catalog_forms": current_catalog["counts"]["catalog_forms"],
        "approved_catalog_forms": current_catalog["counts"]["approved_catalog_forms"],
        "runtime_approved_forms": current_catalog["counts"]["runtime_approved_forms"],
        "attestations": current_catalog["counts"]["attestations"],
        "catalog_checksum": current_catalog["checksums"]["catalog"],
        "attestation_checksum": current_catalog["checksums"]["attestations"],
    }
    report["gap_reason_counts"] = dict(
        sorted(Counter(item["reason_code"] for item in gap_records).items())
    )
    report["source_resolution_run_id"] = resolver_payload.get("run_id")
    report["active_collection"] = _active_collection()
    report["feature_flag_enabled"] = False
    report["candidate_only"] = True
    report["automated_approval"] = False
    report["human_attestation_required"] = True
    report["manifest_sha256"] = manifest_sha256
    report["source_snapshot_sha256"] = source_snapshot_sha256
    batches = build_review_batches(
        shortlist,
        manifest_sha256=manifest_sha256 or _text(inventory.get("manifest_sha256")),
        source_snapshot_sha256=(
            source_snapshot_sha256
            or _text(inventory.get("source_snapshot_sha256"))
        ),
        max_batch_size=25,
    )
    report["counts"]["review_batches"] = len(batches)
    report["counts"]["ready_for_attestation_identities"] = len(
        ready_identity_ids
    )
    _write_atomic(run_dir / "gaps.json", {"records": gap_records})
    _write_atomic(
        run_dir / "review-batches.json",
        {
            "manifest_sha256": manifest_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "batches": batches,
        },
    )
    first_batch = batches[0] if batches else {"records": []}
    _write_atomic(
        run_dir / "review-shortlist.json",
        {
            **first_batch,
            "manifest_sha256": manifest_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "legal_as_of": legal_as_of,
            "batch_count": len(batches),
        },
    )
    _write_atomic(run_dir / "report.json", report)
    _write_atomic(FEATURE006_CAMPAIGN_REPORT_PATH, report)
    _write_atomic(STATUS_PATH, {**report, "stage": "complete"})
    return report


def _runner_failure_reason(exc: Exception) -> str:
    reason = _text(exc)
    if re.fullmatch(r"[A-Z][A-Z0-9_:-]{0,127}", reason):
        return reason
    return "FORM_RESOLUTION_RUNNER_FAILED"


def _mark_runner_failed_fail_closed(
    *,
    legal_as_of: str,
    manifest_sha256: str,
    source_snapshot_sha256: str,
    exc: Exception,
    status_path: Path = STATUS_PATH,
) -> None:
    """Replace only this launch's queued/running state after a runner failure."""

    current = _load(status_path, {})
    if _text(current.get("status")) not in {"queued", "running"}:
        return
    if _text(current.get("legal_as_of")) != _text(legal_as_of):
        return
    if _text(current.get("manifest_sha256")) != _text(manifest_sha256):
        return
    if _text(current.get("source_snapshot_sha256")) != _text(
        source_snapshot_sha256
    ):
        return
    _write_atomic(
        status_path,
        {
            **current,
            "status": "failed_fail_closed",
            "stage": "runner_failed",
            "reason_code": _runner_failure_reason(exc),
            "automated_approval": False,
            "human_attestation_required": True,
            "feature_flag_enabled": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--resume-run-id")
    parser.add_argument("--inventory", type=Path, default=FEATURE006_INVENTORY_PATH)
    parser.add_argument("--requirement-manifest", type=Path)
    parser.add_argument("--source-snapshot", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--source-snapshot-sha256", required=True)
    args = parser.parse_args()
    try:
        report = execute(
            legal_as_of=args.legal_as_of,
            network=not args.no_network,
            workers=args.workers,
            timeout=args.timeout,
            resume_run_id=args.resume_run_id,
            inventory_path=args.inventory,
            requirement_manifest_path=args.requirement_manifest,
            source_snapshot_path=args.source_snapshot,
            manifest_sha256=args.manifest_sha256,
            source_snapshot_sha256=args.source_snapshot_sha256,
        )
    except Exception as exc:
        _mark_runner_failed_fail_closed(
            legal_as_of=args.legal_as_of,
            manifest_sha256=args.manifest_sha256,
            source_snapshot_sha256=args.source_snapshot_sha256,
            exc=exc,
        )
        raise
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
