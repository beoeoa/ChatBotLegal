#!/usr/bin/env python3
"""Build a reproducible form-requirement manifest for the five release domains.

The manifest answers two different questions without conflating them:

* how many current procedures mention an applicant form; and
* how many distinct form identities the chatbot needs to serve.

The command is read-only with respect to runtime catalogs and approvals.  It
stores an official-source snapshot plus review artifacts, but never promotes a
form into production.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_source_resolution import (  # noqa: E402
    extract_strict_form_code,
    extract_strict_form_codes,
    resolve_single_issuing_instrument,
)
from api.three_tier_form_inventory import (  # noqa: E402
    classify_component_kind,
    fold,
)
from scripts.build_three_tier_form_inventory import (  # noqa: E402
    fetch_details,
    fetch_public_catalog,
    select_scoped_procedures,
)
from scripts.discover_official_procedure_sources import (  # noqa: E402
    PORTAL_ORIGIN,
    SEARCH_ENDPOINT,
    collect_profile_components,
)

DEFAULT_OUTPUT_DIR = ROOT / "reports" / "feature006"
DEFAULT_CACHE_DIR = ROOT / "data" / "source_cache" / "form_requirements"
CANONICAL_CATALOG = ROOT / "notebook_data" / "forms" / "canonical_forms_catalog_v1.json"
USER_AGENT = "ChatBotLegal-FormRequirementManifest/1.0"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _title_signature(value: Any) -> str:
    """Normalize a title for counting while retaining the original evidence."""

    text = fold(value)
    text = re.sub(r"^(?:\d+\s+|[a-z]\s+)+", "", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.split(
        r"\b(?:ban hanh kem theo|duoc ban hanh kem theo|kem theo thong tu|"
        r"kem theo nghi dinh|theo thong tu so|theo nghi dinh so|"
        r"neu nguoi co yeu cau|neu nop ho so|truong hop nguoi|"
        r"doi voi truong hop)\b",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(
        r"\b(?:theo\s+)?mau(?:\s+so)?\s+"
        r"(?:[a-z]{1,10}(?:\s+[a-z]{1,10})*\s+)?\d{1,4}[a-z]?\b",
        " ",
        text,
    )
    text = re.sub(r"\btheo mau(?: quy dinh)?\b.*$", " ", text)
    text = re.sub(r"\b(?:ban giay|ban dien tu|truc tuyen)\b", " ", text)
    return " ".join(text.split()) or fold(value)


def _canonical_code(value: Any) -> str | None:
    code = extract_strict_form_code(value)
    if code:
        return code
    raw = re.sub(r"\s+", "", str(value or "")).upper()
    return raw or None


def _catalog_approval_index() -> dict[tuple[str, str], list[str]]:
    payload = _read(CANONICAL_CATALOG, {})
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for form in payload.get("forms") or []:
        if not form.get("approved") or not form.get("runtime_eligible"):
            continue
        code = _canonical_code(form.get("form_code"))
        if not code:
            continue
        for basis in form.get("legal_basis") or []:
            instrument = (
                basis.get("document_number") or basis.get("code")
                if isinstance(basis, dict)
                else basis
            )
            normalized = re.sub(r"\s+", "", str(instrument or "")).upper()
            if normalized:
                index[(code, normalized)].append(str(form.get("form_id") or ""))
    return index


def _identity_status(*, code: str | None, instrument: str | None, eform: bool) -> str:
    if code and instrument:
        return "CONFIRMED_CODE_AND_INSTRUMENT"
    if eform and not code:
        return "OFFICIAL_EFORM_IDENTITY"
    if code:
        return "CODE_PENDING_INSTRUMENT"
    if instrument:
        return "NAMED_FORM_PENDING_CODE"
    return "NAME_PENDING_IDENTITY"


def _identity_key(
    *,
    domain: str,
    code: str | None,
    instrument: str | None,
    title_signature: str,
    eform: bool,
) -> str:
    if code and instrument:
        raw = f"exact|{code}|{instrument}"
    elif eform:
        raw = f"eform|{domain}|{title_signature}"
    elif code:
        raw = f"pending-code|{domain}|{code}|{title_signature}"
    else:
        raw = f"pending-name|{domain}|{instrument or ''}|{title_signature}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def build_manifest(
    *,
    catalog: list[dict[str, Any]],
    scoped: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
    detail_errors: list[dict[str, str]],
    legal_as_of: str,
    retrieved_at: str,
) -> dict[str, Any]:
    approval_index = _catalog_approval_index()
    grouped: dict[str, dict[str, Any]] = {}
    procedure_rows: list[dict[str, Any]] = []
    removed = Counter()
    raw_form_component_count = 0
    expanded_form_reference_count = 0
    multi_form_component_count = 0

    for procedure in scoped:
        formality_id = str(procedure.get("id") or "")
        procedure_id = str(procedure.get("code") or formality_id)
        detail = details.get(formality_id)
        source_page = f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/{formality_id}"
        procedure_identity_ids: set[str] = set()
        procedure_eform_flags: list[bool] = []

        if detail is not None:
            legal_basis = [
                str(item.get("code") or "").strip()
                for item in detail.get("legalBasisesDetails") or []
                if isinstance(item, dict) and str(item.get("code") or "").strip()
            ]
            for component in collect_profile_components(detail):
                kind = classify_component_kind(component)
                if kind != "applicant_form":
                    removed[kind] += 1
                    continue
                raw_form_component_count += 1
                name = str(component.get("name") or "").strip()
                folded_name = fold(name)
                eform = (
                    "mau ho tich dien tu tuong tac" in folded_name
                    or "mau dien tu tuong tac" in folded_name
                    or "bieu mau dien tu tuong tac" in folded_name
                )
                codes = extract_strict_form_codes(name)
                if len(codes) > 1:
                    multi_form_component_count += 1
                references: Iterable[str | None] = codes or [None]
                instrument = resolve_single_issuing_instrument(
                    {"form_name": name, "issuing_instruments": legal_basis}
                )
                signature = _title_signature(name)

                for code in references:
                    expanded_form_reference_count += 1
                    identity_id = _identity_key(
                        domain=str(procedure.get("domain") or "unknown"),
                        code=code,
                        instrument=instrument,
                        title_signature=signature,
                        eform=eform,
                    )
                    status = _identity_status(
                        code=code,
                        instrument=instrument,
                        eform=eform,
                    )
                    approved_ids = approval_index.get((code, instrument), []) if code and instrument else []
                    record = grouped.setdefault(
                        identity_id,
                        {
                            "identity_id": identity_id,
                            "identity_status": status,
                            "release_status": (
                                "APPROVED_RUNTIME"
                                if approved_ids
                                else "PENDING_LEGAL_REVIEW"
                            ),
                            "form_code": code,
                            "issuing_instrument": instrument,
                            "title_signature": signature,
                            "canonical_titles": set(),
                            "domains": set(),
                            "procedure_ids": set(),
                            "procedure_names": set(),
                            "official_source_pages": set(),
                            "distribution_variants": set(),
                            "approved_catalog_form_ids": set(approved_ids),
                        },
                    )
                    record["canonical_titles"].add(name)
                    record["domains"].add(str(procedure.get("domain") or "unknown"))
                    record["procedure_ids"].add(procedure_id)
                    record["procedure_names"].add(str(procedure.get("name") or ""))
                    record["official_source_pages"].add(source_page)
                    record["distribution_variants"].add("interactive_eform" if eform else "paper_or_file")
                    procedure_identity_ids.add(identity_id)
                    procedure_eform_flags.append(eform)

            removed["official_result"] += len(detail.get("resultsDetails") or [])

        if detail is None:
            coverage_status = "DETAIL_FETCH_BLOCKED"
        elif not procedure_identity_ids:
            coverage_status = "NO_OFFICIAL_FORM_LISTED"
        elif all(procedure_eform_flags):
            coverage_status = "OFFICIAL_EFORM_ONLY"
        elif any(grouped[item]["identity_status"].startswith(("CODE_PENDING", "NAMED_", "NAME_")) for item in procedure_identity_ids):
            coverage_status = "OFFICIAL_FORM_REQUIRED_PENDING_IDENTITY"
        else:
            coverage_status = "OFFICIAL_FORM_REQUIRED_CONFIRMED"

        procedure_rows.append(
            {
                "procedure_id": procedure_id,
                "official_formality_id": formality_id,
                "procedure_name": procedure.get("name"),
                "domain": procedure.get("domain"),
                "executing_level": procedure.get("executing_level"),
                "source_tier": procedure.get("source_tier"),
                "coverage_status": coverage_status,
                "required_form_identity_ids": sorted(procedure_identity_ids),
                "official_source_page": source_page,
            }
        )

    identities: list[dict[str, Any]] = []
    for record in grouped.values():
        identities.append(
            {
                **record,
                "canonical_titles": sorted(record["canonical_titles"]),
                "domains": sorted(record["domains"]),
                "procedure_ids": sorted(record["procedure_ids"]),
                "procedure_names": sorted(record["procedure_names"]),
                "official_source_pages": sorted(record["official_source_pages"]),
                "distribution_variants": sorted(record["distribution_variants"]),
                "approved_catalog_form_ids": sorted(record["approved_catalog_form_ids"]),
            }
        )
    identities.sort(
        key=lambda item: (
            item["domains"],
            item.get("form_code") or "",
            item.get("issuing_instrument") or "",
            item["title_signature"],
        )
    )
    procedure_rows.sort(key=lambda item: (item["domain"], item["procedure_id"]))

    identity_status_counts = Counter(item["identity_status"] for item in identities)
    release_status_counts = Counter(item["release_status"] for item in identities)
    procedure_status_counts = Counter(item["coverage_status"] for item in procedure_rows)
    identity_release = {
        item["identity_id"]: item["release_status"] for item in identities
    }
    runtime_procedure_readiness = Counter()
    for procedure in procedure_rows:
        identity_ids = procedure["required_form_identity_ids"]
        approved_count = sum(
            identity_release.get(identity_id) == "APPROVED_RUNTIME"
            for identity_id in identity_ids
        )
        if not identity_ids:
            runtime_procedure_readiness["NO_OFFICIAL_FORM_LISTED"] += 1
        elif approved_count == len(identity_ids):
            runtime_procedure_readiness["FULLY_READY"] += 1
        elif approved_count:
            runtime_procedure_readiness["PARTIALLY_READY"] += 1
        else:
            runtime_procedure_readiness["NOT_READY"] += 1
    domain_identity_counts = Counter(
        domain for item in identities for domain in item["domains"]
    )
    domain_procedure_counts = Counter(item["domain"] for item in procedure_rows)
    summary = {
        "official_catalog_total": len(catalog),
        "scoped_procedure_count": len(scoped),
        "detail_success_count": len(details),
        "detail_error_count": len(detail_errors),
        "raw_form_component_count": raw_form_component_count,
        "expanded_form_reference_count": expanded_form_reference_count,
        "multi_form_component_count": multi_form_component_count,
        "required_form_identity_planning_count": len(identities),
        "deliverable_type_counts": {
            "interactive_eform": sum(
                "interactive_eform" in item["distribution_variants"]
                for item in identities
            ),
            "paper_or_file": sum(
                "paper_or_file" in item["distribution_variants"]
                for item in identities
            ),
        },
        "identity_status_counts": dict(sorted(identity_status_counts.items())),
        "release_status_counts": dict(sorted(release_status_counts.items())),
        "procedure_coverage_status_counts": dict(sorted(procedure_status_counts.items())),
        "runtime_procedure_readiness_counts": dict(
            sorted(runtime_procedure_readiness.items())
        ),
        "form_identities_by_domain": dict(sorted(domain_identity_counts.items())),
        "procedures_by_domain": dict(sorted(domain_procedure_counts.items())),
        "removed_non_form_counts": dict(sorted(removed.items())),
        "runtime_catalog_mutated": False,
        "automated_approval_count": 0,
        "data_sufficiency_verdict": (
            "PASS"
            if not detail_errors
            and release_status_counts.get("PENDING_LEGAL_REVIEW", 0) == 0
            else "BLOCKED_DATA"
        ),
    }
    return {
        "schema_version": 1,
        "generated_at": _utcnow(),
        "retrieved_at": retrieved_at,
        "legal_as_of": legal_as_of,
        "source": SEARCH_ENDPOINT,
        "candidate_only": True,
        "summary": summary,
        "form_identities": identities,
        "procedures": procedure_rows,
        "detail_errors": detail_errors,
    }


def _write_csvs(output_dir: Path, manifest: dict[str, Any], legal_as_of: str) -> None:
    fields = [
        "identity_id",
        "identity_status",
        "release_status",
        "form_code",
        "issuing_instrument",
        "title",
        "domains",
        "procedure_count",
        "procedure_ids",
        "distribution_variants",
        "official_source_pages",
    ]
    rows = [
        {
            "identity_id": item["identity_id"],
            "identity_status": item["identity_status"],
            "release_status": item["release_status"],
            "form_code": item.get("form_code") or "",
            "issuing_instrument": item.get("issuing_instrument") or "",
            "title": " | ".join(item["canonical_titles"]),
            "domains": " | ".join(item["domains"]),
            "procedure_count": len(item["procedure_ids"]),
            "procedure_ids": " | ".join(item["procedure_ids"]),
            "distribution_variants": " | ".join(item["distribution_variants"]),
            "official_source_pages": " | ".join(item["official_source_pages"]),
        }
        for item in manifest["form_identities"]
    ]
    for identity_path, selected_rows in (
        (output_dir / f"form-requirement-list-{legal_as_of}.csv", rows),
        (
            output_dir / f"pending-form-identities-{legal_as_of}.csv",
            [row for row in rows if row["release_status"] != "APPROVED_RUNTIME"],
        ),
    ):
        with identity_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(selected_rows)

    procedure_path = output_dir / f"procedure-form-coverage-{legal_as_of}.csv"
    with procedure_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "procedure_id",
            "official_formality_id",
            "procedure_name",
            "domain",
            "executing_level",
            "source_tier",
            "coverage_status",
            "required_form_count",
            "required_form_identity_ids",
            "official_source_page",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in manifest["procedures"]:
            writer.writerow(
                {
                    **{field: item.get(field, "") for field in fields},
                    "required_form_count": len(item["required_form_identity_ids"]),
                    "required_form_identity_ids": " | ".join(item["required_form_identity_ids"]),
                }
            )


def _write_summary(output_dir: Path, manifest: dict[str, Any], legal_as_of: str) -> None:
    summary = manifest["summary"]
    lines = [
        "# Báo cáo nhu cầu biểu mẫu cho chatbot",
        "",
        f"- Ngày chốt dữ liệu: `{legal_as_of}`",
        f"- Thủ tục trong phạm vi: **{summary['scoped_procedure_count']}**",
        f"- Định danh biểu mẫu phục vụ lập kế hoạch: **{summary['required_form_identity_planning_count']}**",
        f"- Mẫu giấy/tệp: **{summary['deliverable_type_counts']['paper_or_file']}**",
        f"- Biểu mẫu điện tử tương tác: **{summary['deliverable_type_counts']['interactive_eform']}**",
        f"- Thành phần biểu mẫu thô: **{summary['raw_form_component_count']}**",
        f"- Tham chiếu sau khi tách dòng chứa nhiều mẫu: **{summary['expanded_form_reference_count']}**",
        "",
        "## Trạng thái định danh",
        "",
    ]
    for key, value in summary["identity_status_counts"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Độ phủ thủ tục", ""])
    for key, value in summary["procedure_coverage_status_counts"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Mức sẵn sàng của kho đang chạy", ""])
    for key, value in summary["runtime_procedure_readiness_counts"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## Điều kiện phát hành",
            "",
            "Số lượng lập kế hoạch không đồng nghĩa với số mẫu đã được phép phát hành. "
            "Chỉ các dòng `APPROVED_RUNTIME` đã qua cổng duyệt hiện tại; các dòng còn "
            "lại phải xác minh văn bản ban hành, hiệu lực và tệp chính thức.",
            "",
        ]
    )
    (output_dir / f"form-requirement-summary-{legal_as_of}.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def run(*, legal_as_of: str, output_dir: Path, cache_dir: Path, refresh: bool) -> dict[str, Any]:
    cache_path = cache_dir / f"dvc-form-requirements-{legal_as_of}.json"
    cached = _read(cache_path, {}) if not refresh else {}
    if cached.get("catalog") and cached.get("details"):
        catalog = cached["catalog"]
        details = cached["details"]
        retrieved_at = str(cached.get("retrieved_at") or "")
        scoped, _ = select_scoped_procedures(catalog)
        detail_errors = cached.get("detail_errors") or []
    else:
        with httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            catalog = fetch_public_catalog(client)
        scoped, _ = select_scoped_procedures(catalog)
        details, detail_errors = fetch_details(scoped, workers=8, timeout=30)
        retrieved_at = _utcnow()
        _write_json(
            cache_path,
            {
                "schema_version": 1,
                "retrieved_at": retrieved_at,
                "legal_as_of": legal_as_of,
                "source": SEARCH_ENDPOINT,
                "catalog": catalog,
                "details": details,
                "detail_errors": detail_errors,
            },
        )

    manifest = build_manifest(
        catalog=catalog,
        scoped=scoped,
        details=details,
        detail_errors=detail_errors,
        legal_as_of=legal_as_of,
        retrieved_at=retrieved_at,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / f"form-requirement-manifest-{legal_as_of}.json", manifest)
    _write_csvs(output_dir, manifest, legal_as_of)
    _write_summary(output_dir, manifest, legal_as_of)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    manifest = run(
        legal_as_of=args.legal_as_of,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
        refresh=args.refresh,
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
