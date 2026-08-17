#!/usr/bin/env python3
"""Build a candidate-only form inventory for Central, Hai Phong and Le Chan.

The national public-service API is the procedure source of truth.  Existing
official assets are merged only when they already carry an exact procedure
mapping.  The command never approves records, mutates the runtime catalog,
touches the vector collection, or calls a language model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.three_tier_form_inventory import (
    build_privacy_safe_summary,
    canonicalize_form_candidates,
    classify_component_kind,
    classify_domain,
    classify_source_tier,
    extract_form_code,
    fold,
    infer_executing_level,
)
from scripts.crawl_canonical_forms import (
    candidate_from_download,
    is_allowed_official_url,
)
from scripts.discover_official_procedure_sources import (
    DETAIL_ENDPOINT,
    PORTAL_ORIGIN,
    SEARCH_ENDPOINT,
    collect_profile_components,
)

FORMS_DIR = ROOT / "notebook_data" / "forms"
CACHE_DIR = ROOT / "data" / "source_cache" / "three_tier_forms"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "three_tier_form_candidates"
REPORT_DIR = ROOT / "reports" / "feature005"
USER_AGENT = "ChatBotLegal-ThreeTierFormInventory/1.0"
CURRENT_STATES = {"ACTIVE", "UPDATED"}


def _read(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_public_catalog(client: httpx.Client) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    last_id = ""
    for _page in range(100):
        response = client.post(
            SEARCH_ENDPOINT,
            json={
                "limit": 100,
                "lastId": last_id,
                "q": "",
                "categoryId": "",
                "departmentCode": "",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "OK":
            raise RuntimeError("OFFICIAL_PORTAL_CATALOG_FAILED")
        data = payload.get("data") or {}
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        if not items:
            break
        records.extend(items)
        next_id = str(data.get("lastId") or "")
        if not next_id or next_id == last_id:
            break
        last_id = next_id
    unique = {
        str(item.get("id")): item
        for item in records
        if str(item.get("id") or "").strip()
    }
    return sorted(unique.values(), key=lambda item: str(item.get("id")))


def select_scoped_procedures(
    catalog: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    exclusions: dict[str, int] = {}

    def reject(reason: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1

    for item in catalog:
        if str(item.get("state") or "").upper() not in CURRENT_STATES:
            reject("PROCEDURE_NOT_CURRENT")
            continue
        domain = classify_domain(item.get("categories") or [])
        if domain is None:
            reject("OUTSIDE_FIVE_DOMAINS")
            continue
        executing_level = infer_executing_level(item.get("departments") or [])
        if executing_level not in {"province", "commune"}:
            reject("OUTSIDE_PROVINCE_OR_COMMUNE_SCOPE")
            continue
        source_tier = classify_source_tier(
            formality_type=item.get("type"),
            publisher=item.get("departmentPromulgate"),
            executing_level=executing_level,
        )
        if source_tier is None:
            reject("OTHER_PROVINCE_OR_UNVERIFIED_TIER")
            continue
        selected.append(
            {
                **item,
                "domain": domain,
                "executing_level": executing_level,
                "source_tier": source_tier,
            }
        )
    return selected, dict(sorted(exclusions.items()))


def _fetch_detail(formality_id: str, timeout: float) -> dict[str, Any]:
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = client.post(DETAIL_ENDPOINT, json={"id": formality_id})
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "OK" or not isinstance(payload.get("data"), dict):
            raise RuntimeError("OFFICIAL_PORTAL_DETAIL_FAILED")
        return payload["data"]


def fetch_details(
    procedures: list[dict[str, Any]],
    *,
    workers: int,
    timeout: float,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    details: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_fetch_detail, str(item["id"]), timeout): item
            for item in procedures
        }
        for future in as_completed(futures):
            item = futures[future]
            formality_id = str(item["id"])
            try:
                details[formality_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "formality_id": formality_id,
                        "reason_code": "BLOCKED_EXTERNAL_DETAIL_FETCH",
                        "error_type": type(exc).__name__,
                    }
                )
    return details, sorted(errors, key=lambda item: item["formality_id"])


def _attachment_url(component: dict[str, Any]) -> str | None:
    for attachment in component.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        for key in ("downloadUrl", "downloadURL", "fileUrl", "fileURL", "url", "path"):
            value = str(attachment.get(key) or "").strip()
            if value:
                url = urljoin(PORTAL_ORIGIN, value)
                if is_allowed_official_url(url):
                    return url
    return None


def _explicit_effective_from(detail: dict[str, Any]) -> str | None:
    candidates: set[str] = set()
    records = [detail, *(detail.get("legalBasisesDetails") or [])]
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in (
            "effectiveFrom",
            "effectiveDate",
            "effective_from",
            "effective_date",
        ):
            value = str(record.get(key) or "").strip()
            if not value:
                continue
            try:
                candidates.add(date.fromisoformat(value[:10]).isoformat())
            except ValueError:
                continue
    return next(iter(candidates)) if len(candidates) == 1 else None


def candidates_from_details(
    procedures: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    procedure_gaps: list[dict[str, Any]] = []
    removed_counts = {
        "supporting_document": 0,
        "official_result": 0,
    }
    for procedure in procedures:
        formality_id = str(procedure["id"])
        detail = details.get(formality_id)
        if detail is None:
            procedure_gaps.append(
                {
                    "procedure_id": procedure.get("code") or formality_id,
                    "reason_code": "BLOCKED_EXTERNAL_DETAIL_FETCH",
                }
            )
            continue
        legal_basis = [
            str(item.get("code") or "").strip()
            for item in detail.get("legalBasisesDetails") or []
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        ]
        source_page = f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/{formality_id}"
        form_count = 0
        for component in collect_profile_components(detail):
            kind = classify_component_kind(component)
            if kind != "applicant_form":
                removed_counts[kind] = removed_counts.get(kind, 0) + 1
                continue
            form_count += 1
            component_identity = str(
                component.get("profileComponentId")
                or component.get("id")
                or component.get("code")
                or component.get("name")
                or form_count
            )
            candidate_id = hashlib.sha256(
                f"dvc:{formality_id}:{component_identity}".encode("utf-8")
            ).hexdigest()[:24]
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "procedure_id": procedure.get("code") or formality_id,
                    "procedure_code": procedure.get("code"),
                    "procedure_name": procedure.get("name"),
                    "official_formality_id": formality_id,
                    "domain": procedure["domain"],
                    "executing_level": procedure["executing_level"],
                    "source_tier": procedure["source_tier"],
                    "form_name": component.get("name"),
                    "form_code": extract_form_code(component.get("name")),
                    "component_code": component.get("code"),
                    "component_kind": "applicant_form",
                    "official_source_page": source_page,
                    "official_download_url": _attachment_url(component),
                    "local_path": None,
                    "sha256": None,
                    "issuing_instruments": sorted(set(legal_basis)),
                    "effective_from": _explicit_effective_from(detail),
                    "effective_to": None,
                    "portal_state": procedure.get("state"),
                    "review_status": "candidate_pending_review",
                    "approved": False,
                    "is_seed": False,
                    "is_demo": False,
                    "is_quarantined": False,
                    "provenance": [
                        {
                            "publisher": detail.get("departmentPromulgateName")
                            or procedure.get("departmentPromulgate"),
                            "portal": PORTAL_ORIGIN,
                            "formality_id": formality_id,
                            "retrieved_at": _utcnow(),
                        }
                    ],
                }
            )
        if form_count == 0:
            procedure_gaps.append(
                {
                    "procedure_id": procedure.get("code") or formality_id,
                    "reason_code": "NO_APPLICANT_FORM_LISTED",
                    "source_tier": procedure["source_tier"],
                    "domain": procedure["domain"],
                }
            )
        removed_counts["official_result"] += len(detail.get("resultsDetails") or [])
    return candidates, procedure_gaps, removed_counts


def download_candidate_files(
    candidates: list[dict[str, Any]],
    *,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for candidate in candidates:
            url = str(candidate.get("official_download_url") or "")
            if not url:
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
                downloaded = candidate_from_download(
                    requirement_id=str(candidate["candidate_id"]),
                    procedure_id=str(candidate["procedure_id"]),
                    canonical_name=str(candidate.get("form_name") or ""),
                    source_page=str(candidate["official_source_page"]),
                    download_url=url,
                    content=response.content,
                    content_type=response.headers.get("content-type"),
                    download_dir=DOWNLOAD_DIR,
                )
                if downloaded.get("status") != "AVAILABLE_OFFICIAL_FILE":
                    errors.append(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "reason_code": downloaded.get("reason_code"),
                        }
                    )
                    continue
                candidate["local_path"] = downloaded.get("local_path")
                candidate["sha256"] = downloaded.get("sha256")
                candidate["file_format"] = downloaded.get("file_format")
                candidate["size_bytes"] = downloaded.get("size_bytes")
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "reason_code": "BLOCKED_EXTERNAL_DOWNLOAD",
                        "error_type": type(exc).__name__,
                    }
                )
    return candidates, errors


def existing_reviewed_candidates() -> list[dict[str, Any]]:
    forms = _read(FORMS_DIR / "canonical_forms_catalog_v1.json", {}).get("forms") or []
    procedures = {
        str(item.get("procedure_id")): item
        for item in _read(FORMS_DIR / "canonical_procedures_v1.json", {}).get("procedures") or []
    }
    records: list[dict[str, Any]] = []
    for form in forms:
        if not form.get("approved"):
            continue
        internal_id = str((form.get("procedure_ids") or [""])[0])
        procedure = procedures.get(internal_id, {})
        evidence = procedure.get("candidate_source_evidence") or {}
        form_evidence = [
            item
            for item in form.get("candidate_source_evidence") or []
            if isinstance(item, dict)
        ]
        official_code = evidence.get("official_procedure_code") or next(
            (
                item.get("official_procedure_code")
                for item in form_evidence
                if item.get("official_procedure_code")
            ),
            None,
        )
        source_page = str(form.get("official_source_page") or "")
        # A provincial mirror does not make a centrally prescribed template
        # a local override.  The reviewed catalog currently cites only central
        # issuing instruments; keep the source page as provenance.
        tier = "central"
        records.append(
            {
                "candidate_id": f"reviewed-{form.get('form_id')}",
                "procedure_id": official_code or internal_id,
                "procedure_code": official_code,
                "procedure_name": procedure.get("name"),
                "domain": form.get("domain"),
                "executing_level": form.get("administrative_level"),
                "source_tier": tier,
                "form_name": form.get("canonical_name"),
                "form_code": form.get("form_code"),
                "component_kind": "applicant_form",
                "official_source_page": form.get("official_source_page"),
                "official_download_url": form.get("official_download_url"),
                "local_path": form.get("local_path"),
                "sha256": form.get("sha256"),
                "issuing_instruments": sorted(
                    {
                        (
                            str(item.get("document_number") or item.get("code") or "").strip()
                            if isinstance(item, dict)
                            else str(item or "").strip()
                        )
                        for item in form.get("legal_basis") or []
                        if (
                            str(item.get("document_number") or item.get("code") or "").strip()
                            if isinstance(item, dict)
                            else str(item or "").strip()
                        )
                    }
                ),
                "effective_from": form.get("effective_from"),
                "effective_to": form.get("effective_to"),
                "portal_state": "ACTIVE",
                "review_status": form.get("legal_review_status") or form.get("review_status"),
                "prior_approval_status": True,
                "approved": False,
                "is_seed": False,
                "is_demo": False,
                "is_quarantined": False,
                "provenance": [form.get("provenance") or {"kind": "canonical_catalog"}],
            }
        )
    return records


def run(
    *,
    legal_as_of: str,
    workers: int = 8,
    timeout: float = 30.0,
    download: bool = True,
    max_procedures: int | None = None,
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        catalog = fetch_public_catalog(client)
    scoped, catalog_exclusions = select_scoped_procedures(catalog)
    if max_procedures is not None:
        scoped = scoped[: max(0, max_procedures)]
    details, detail_errors = fetch_details(scoped, workers=workers, timeout=timeout)
    candidates, procedure_gaps, removed_counts = candidates_from_details(scoped, details)
    download_errors: list[dict[str, Any]] = []
    if download:
        candidates, download_errors = download_candidate_files(
            candidates,
            timeout=timeout,
        )
    candidates.extend(existing_reviewed_candidates())
    inventory = canonicalize_form_candidates(candidates, legal_as_of=legal_as_of)
    inventory.update(
        {
            "generated_at": _utcnow(),
            "run_id": run_id,
            "official_catalog_total": len(catalog),
            "scoped_procedure_count": len(scoped),
            "scoped_procedures_by_tier": dict(
                sorted(
                    {
                        tier: sum(1 for item in scoped if item["source_tier"] == tier)
                        for tier in ("central", "hai_phong_override", "le_chan_local")
                    }.items()
                )
            ),
            "scoped_procedures_by_level": dict(
                sorted(
                    {
                        level: sum(
                            1 for item in scoped if item["executing_level"] == level
                        )
                        for level in ("province", "commune")
                    }.items()
                )
            ),
            "le_chan_applicable_procedure_count": sum(
                1 for item in scoped if item["executing_level"] == "commune"
            ),
            "catalog_exclusion_counts": catalog_exclusions,
            "removed_non_form_counts": removed_counts,
            "procedure_gaps": procedure_gaps,
            "detail_errors": detail_errors,
            "download_errors": download_errors,
            "runtime_mutated": False,
            "auto_approved_count": 0,
        }
    )
    summary = build_privacy_safe_summary(inventory, run_id=run_id)
    summary.update(
        {
            "official_catalog_total": len(catalog),
            "scoped_procedure_count": len(scoped),
            "scoped_procedures_by_tier": inventory["scoped_procedures_by_tier"],
            "scoped_procedures_by_level": inventory["scoped_procedures_by_level"],
            "le_chan_applicable_procedure_count": inventory[
                "le_chan_applicable_procedure_count"
            ],
            "procedure_gap_count": len(procedure_gaps),
            "detail_error_count": len(detail_errors),
            "download_error_count": len(download_errors),
            "removed_non_form_counts": removed_counts,
            "runtime_mutated": False,
            "feature_flag_changed": False,
        }
    )
    _write(
        CACHE_DIR / f"dvc-public-catalog-{legal_as_of}.json",
        {
            "retrieved_at": _utcnow(),
            "source": SEARCH_ENDPOINT,
            "records": catalog,
        },
    )
    _write(FORMS_DIR / "three_tier_form_inventory_v1.json", inventory)
    _write(
        FORMS_DIR / "three_tier_procedure_catalog_v1.json",
        {
            "schema_version": 1,
            "generated_at": inventory["generated_at"],
            "run_id": run_id,
            "legal_as_of": legal_as_of,
            "source": SEARCH_ENDPOINT,
            "procedures": [
                {
                    "procedure_id": item.get("id"),
                    "procedure_code": item.get("code"),
                    "procedure_name": item.get("name"),
                    "domain": item.get("domain"),
                    "executing_level": item.get("executing_level"),
                    "source_tier": item.get("source_tier"),
                    "portal_state": item.get("state"),
                    "formality_type": item.get("type"),
                    "publisher": item.get("departmentPromulgate"),
                    "official_source_page": (
                        f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/{item.get('id')}"
                    ),
                }
                for item in scoped
            ],
        },
    )
    _write(
        FORMS_DIR / "three_tier_form_review_shortlist_v1.json",
        {
            "schema_version": 1,
            "generated_at": inventory["generated_at"],
            "run_id": run_id,
            "legal_as_of": legal_as_of,
            "candidate_only": True,
            "auto_approved_count": 0,
            "forms": inventory["review_shortlist"],
        },
    )
    _write(REPORT_DIR / f"three-tier-form-inventory-{run_id}.json", summary)
    _write(REPORT_DIR / "three-tier-form-inventory-latest.json", summary)
    return {"inventory": inventory, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--max-procedures", type=int)
    args = parser.parse_args()
    result = run(
        legal_as_of=args.legal_as_of,
        workers=max(1, min(args.workers, 12)),
        timeout=max(5.0, args.timeout),
        download=not args.no_download,
        max_procedures=args.max_procedures,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
