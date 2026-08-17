"""Build versioned procedure/form catalog artifacts without mutating source data."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import (
    classify_requirement,
    fold_text,
    is_synthetic_procedure_id,
    normalize_procedure_id,
)

DEFAULT_RAW = ROOT / "notebook_data" / "forms" / "priority_200_forms.json"
DEFAULT_OUTPUT = ROOT / "notebook_data" / "forms"
DEFAULT_REPORT = ROOT / "reports" / "feature005" / "forms-completion-20260724"
LEGAL_AS_OF = "2026-07-24"

SOURCE_CATALOGS = (
    "forms_manifest.json",
    "haiphong_official_form_index.json",
    "haiphong_official_forms_catalog.json",
    "official_forms_candidates_classified.json",
    "priority_official_forms.json",
)

KNOWN_ALIASES: dict[str, list[str]] = {
    "dang_ky_khai_sinh": ["khai sinh", "làm giấy khai sinh"],
    "dang_ky_khai_sinh_nuoc_ngoai": [
        "khai sinh có yếu tố nước ngoài",
        "trẻ sinh ở nước ngoài",
    ],
    "dang_ky_ket_hon": ["kết hôn", "làm giấy kết hôn"],
    "xac_nhan_tinh_trang_hon_nhan": [
        "xác nhận độc thân",
        "giấy độc thân",
        "tình trạng hôn nhân",
        "xac_nhan_doc_than",
    ],
    "dang_ky_khai_tu": ["khai tử", "làm giấy khai tử"],
    "dang_ky_thuong_tru": ["nhập hộ khẩu", "đăng ký hộ khẩu thường trú"],
    "dang_ky_tam_tru": ["tạm trú", "đăng ký tạm trú"],
    "sang_ten_so_do": [
        "sang tên sổ đỏ",
        "đăng ký biến động đất đai",
        "chuyển nhượng quyền sử dụng đất",
    ],
    "cap_giay_phep_xay_dung": [
        "xin phép xây dựng",
        "giấy phép xây dựng nhà ở",
    ],
    "khieu_nai_hanh_chinh": [
        "khiếu nại",
        "đơn khiếu nại",
        "khieu_nai",
    ],
    "tro_cap_xa_hoi": [
        "trợ cấp xã hội",
        "bảo trợ xã hội",
        "tro_cap_bao_tro_xa_hoi",
    ],
}

KNOWN_CANONICAL_NAMES = {
    "sang_ten_so_do": "Sang tên sổ đỏ (đăng ký biến động đất đai)",
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("forms", "records", "items", "candidates"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    forms = payload.get("forms")
    if isinstance(forms, dict):
        return [
            row
            for values in forms.values()
            if isinstance(values, list)
            for row in values
            if isinstance(row, dict)
        ]
    return []


def _canonical_form_name(title: str) -> str:
    value = re.sub(
        r"\s+theo\s+mẫu\s+Thông\s+tư\s+.+$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip()


def _form_code(title: str) -> str | None:
    patterns = (
        r"\b(?:Mẫu\s+số\s+)?(\d{1,3}[a-zA-Z]?/[A-ZĐa-zđ]{1,8})\b",
        r"\b(CT\d{2})\b",
        r"\bmẫu\s+(\d+[a-z]?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, title, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def _stable_form_id(procedure_id: str, name: str, code: str | None) -> str:
    identity = f"{procedure_id}|{fold_text(code or name)}"
    return f"form-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _form_type(title: str, classification: str) -> str:
    folded = fold_text(title)
    if classification == "ONLINE_EFORM":
        return "online_eform"
    if folded.startswith("don"):
        return "application"
    if folded.startswith("to khai"):
        return "declaration"
    if folded.startswith("bien ban"):
        return "record"
    if folded.startswith(("quyet dinh", "mau quyet dinh")):
        return "decision"
    if folded.startswith("phieu"):
        return "receipt"
    return "application"


def _authority_level(procedure_id: str) -> str:
    if procedure_id == "cap_phieu_ly_lich_tu_phap":
        return "city"
    return "commune"


def _build_baseline(forms_dir: Path, raw_count: int) -> dict[str, Any]:
    catalog_counts: dict[str, Any] = {}
    checksums: set[str] = set()
    for name in SOURCE_CATALOGS:
        path = forms_dir / name
        if not path.is_file():
            catalog_counts[name] = {"rows": 0, "approved": 0}
            continue
        rows = _extract_rows(_load(path))
        approved = sum(
            str(row.get("review_status") or "").casefold() == "approved"
            or row.get("is_approved") is True
            for row in rows
        )
        official_with_file = sum(
            str(row.get("official_level") or "").casefold() == "official"
            and row.get("has_official_file") is True
            for row in rows
        )
        for row in rows:
            digest = str(row.get("source_sha256") or row.get("sha256") or "")
            if digest:
                checksums.add(digest.casefold())
        catalog_counts[name] = {
            "rows": len(rows),
            "approved": approved,
            "official_with_file": official_with_file,
        }
    db1_path = (
        ROOT
        / "reports"
        / "feature005"
        / "backup-db1-20260723"
        / "db1-manifest.json"
    )
    db4_path = (
        ROOT
        / "reports"
        / "feature005"
        / "db4-serving-20260724-r2"
        / "manifest.json"
    )
    db1 = _load(db1_path) if db1_path.is_file() else {}
    db4 = _load(db4_path) if db4_path.is_file() else {}
    postgres = (
        db1.get("source_snapshot", {}).get("postgres", {})
        if isinstance(db1, dict)
        else {}
    )
    document_stats = postgres.get("documents", {})
    core_stats = postgres.get("scope", {})
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": LEGAL_AS_OF,
        "feature_flag": {
            "name": "LEGAL_SECTION_GROUNDING_ENABLED",
            "required_value": False,
        },
        "legal_corpus": {
            "document_count": document_stats.get("total"),
            "article_count": postgres.get("articles", {}).get("count"),
            "chunk_count": postgres.get("chunks", {}).get("count"),
            "legacy_core_document_count": core_stats.get("included_documents"),
            "document_id_sha256": postgres.get("document_ids", {}).get(
                "sha256_ids"
            ),
            "article_id_sha256": postgres.get("articles", {}).get(
                "sha256_ids"
            ),
            "chunk_id_sha256": postgres.get("chunks", {}).get("sha256_ids"),
            "active_pointer_changed": db4.get("active_pointer_unchanged")
            is False,
            "counts_source": str(db1_path.relative_to(ROOT))
            if db1
            else None,
        },
        "serving_collections": {
            "primary": db4.get("primary_collection"),
            "primary_count": db4.get("collections", {})
            .get("primary", {})
            .get("actual_vectors"),
            "primary_id_sha256": db4.get("collections", {})
            .get("primary", {})
            .get("id_sha256"),
            "support": db4.get("support_collection"),
            "support_count": db4.get("collections", {})
            .get("support", {})
            .get("actual_vectors"),
            "support_id_sha256": db4.get("collections", {})
            .get("support", {})
            .get("id_sha256"),
            "manifest_source": str(db4_path.relative_to(ROOT))
            if db4
            else None,
        },
        "raw_requirement_count": raw_count,
        "source_catalogs": catalog_counts,
        "unique_asset_checksums": len(checksums),
        "contains_credentials": False,
        "contains_user_content": False,
    }


def build_catalogs(
    *,
    raw_requirements_path: Path = DEFAULT_RAW,
    output_dir: Path = DEFAULT_OUTPUT,
    report_dir: Path = DEFAULT_REPORT,
    write: bool = True,
) -> dict[str, Any]:
    raw = _load(Path(raw_requirements_path))
    rows = raw.get("forms")
    if not isinstance(rows, list):
        raise ValueError("priority catalog must contain a forms array")

    requirements: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_names: dict[str, str] = {}
    domains: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("procedure_id") or "").strip()
        title = str(row.get("form_title") or "").strip()
        classification = classify_requirement(title, source_id)
        canonical_id = (
            None
            if classification == "SYNTHETIC_PLACEHOLDER"
            else normalize_procedure_id(source_id)
        )
        requirement = {
            "requirement_id": str(row.get("id") or ""),
            "source_procedure_id": source_id,
            "procedure_id": canonical_id,
            "form_title": title,
            "procedure_name": str(row.get("source_package_title") or "").strip(),
            "domain": str(row.get("domain") or "").strip(),
            "classification": classification,
            "runtime_eligible": classification
            in {
                "REAL_PROCEDURE_FORM_REQUIREMENT",
                "ONLINE_EFORM",
                "OFFICER_INTERNAL_FORM",
            },
            "reason_code": classification,
                "source_status": "gap",
                "review_status": "pending",
                "approved": False,
                "provenance": {
                    "kind": "candidate_discovery",
                    "source_file": "notebook_data/forms/priority_200_forms.json",
                },
                "legal_review_status": "not_reviewed",
            }
        requirements.append(requirement)
        if canonical_id:
            grouped[canonical_id].append(requirement)
            raw_names.setdefault(canonical_id, str(requirement["procedure_name"]))
            domains.setdefault(canonical_id, str(requirement["domain"]))

    procedures: list[dict[str, Any]] = []
    for procedure_id in sorted(grouped):
        aliases = {
            procedure_id.replace("_", " "),
            raw_names.get(procedure_id, ""),
            *KNOWN_ALIASES.get(procedure_id, []),
        }
        source_ids = {
            item["source_procedure_id"]
            for item in grouped[procedure_id]
            if item["source_procedure_id"] != procedure_id
        }
        aliases.update(source_ids)
        procedures.append(
            {
                "procedure_id": procedure_id,
                "official_procedure_code": None,
                "name": KNOWN_CANONICAL_NAMES.get(procedure_id)
                or raw_names.get(procedure_id)
                or procedure_id.replace("_", " "),
                "aliases": sorted(alias for alias in aliases if alias),
                "domain": domains.get(procedure_id) or "unknown",
                "authority_level": _authority_level(procedure_id),
                "receiving_authority": None,
                "decision_authority": None,
                "jurisdiction": "Hai Phong",
                "legal_as_of": LEGAL_AS_OF,
                "official_procedure_url": None,
                "source_status": "gap",
                "review_status": "pending",
                "approved": False,
                "provenance": {
                    "kind": "candidate_discovery",
                    "source_file": "notebook_data/forms/priority_200_forms.json",
                },
                "legal_review_status": "not_reviewed",
                "runtime_eligible": False,
            }
        )

    forms_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    for requirement in requirements:
        if not requirement["runtime_eligible"] or not requirement["procedure_id"]:
            continue
        procedure_key: str = str(requirement["procedure_id"])
        canonical_name = _canonical_form_name(str(requirement["form_title"]))
        code = _form_code(canonical_name)
        key = (procedure_key, fold_text(code or canonical_name))
        form = forms_by_key.get(key)
        if form is None:
            form_id = _stable_form_id(procedure_key, canonical_name, code)
            classification = str(requirement["classification"])
            form = {
                "form_id": form_id,
                "procedure_ids": [procedure_key],
                "form_code": code,
                "canonical_name": canonical_name,
                "aliases": sorted(
                    {
                        requirement["form_title"],
                        canonical_name,
                        *(KNOWN_ALIASES.get(procedure_key, [])),
                    }
                ),
                "audience": (
                    "officer"
                    if classification == "OFFICER_INTERNAL_FORM"
                    else "citizen"
                ),
                "form_type": _form_type(canonical_name, classification),
                "usage": (
                    "officer_internal"
                    if classification == "OFFICER_INTERNAL_FORM"
                    else "applicant_form"
                ),
                "required_or_conditional": "required",
                "condition": None,
                "domain": requirement["domain"],
                "administrative_level": _authority_level(procedure_key),
                "jurisdiction": "Hai Phong",
                "official_source_page": None,
                "official_download_url": None,
                "local_path": None,
                "file_format": (
                    "online" if classification == "ONLINE_EFORM" else None
                ),
                "sha256": None,
                "legal_basis": [],
                "effective_from": None,
                "effective_to": None,
                "supersedes_form_id": None,
                "source_classification": "unverified_missing_source",
                "review_status": "candidate_pending_review",
                "approved": False,
                "provenance": {
                    "kind": "candidate_discovery",
                    "source_file": "notebook_data/forms/priority_200_forms.json",
                },
                "legal_review_status": "not_reviewed",
                "url_status": "unverified",
                "distribution_variants": (
                    ["online"] if classification == "ONLINE_EFORM" else []
                ),
                "source_requirement_ids": [requirement["requirement_id"]],
            }
            forms_by_key[key] = form
        elif requirement["requirement_id"] not in form["source_requirement_ids"]:
            form["source_requirement_ids"].append(requirement["requirement_id"])
        bindings.append(
            {
                "procedure_id": procedure_key,
                "form_id": form["form_id"],
                "requirement_id": requirement["requirement_id"],
                "required_or_conditional": form["required_or_conditional"],
                "condition": form["condition"],
                "binding_status": "candidate_pending_review",
                "review_status": "pending",
                "approved": False,
            }
        )

    # Deduplicate repeated source rows that resolve to the same relation.
    unique_bindings: dict[tuple[str, str], dict[str, Any]] = {}
    for binding in bindings:
        unique_bindings[(binding["procedure_id"], binding["form_id"])] = binding
    bindings = sorted(
        unique_bindings.values(),
        key=lambda item: (item["procedure_id"], item["form_id"]),
    )
    forms = sorted(forms_by_key.values(), key=lambda item: item["form_id"])

    procedures_payload = {
        "schema_version": 1,
        "legal_as_of": LEGAL_AS_OF,
        "procedures": procedures,
    }
    requirements_payload = {
        "schema_version": 1,
        "legal_as_of": LEGAL_AS_OF,
        "requirements": requirements,
    }
    forms_payload = {
        "schema_version": 1,
        "legal_as_of": LEGAL_AS_OF,
        "forms": forms,
    }
    bindings_payload = {
        "schema_version": 1,
        "legal_as_of": LEGAL_AS_OF,
        "bindings": bindings,
    }
    baseline = _build_baseline(
        Path(raw_requirements_path).parent,
        len(requirements),
    )
    classification_counts = Counter(
        item["classification"] for item in requirements
    )
    f0_report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": LEGAL_AS_OF,
        "raw_requirement_count": len(requirements),
        "classified_requirement_count": len(requirements),
        "classification_counts": dict(sorted(classification_counts.items())),
        "canonical_procedure_count": len(procedures),
        "canonical_form_candidate_count": len(forms),
        "synthetic_requirement_count": classification_counts[
            "SYNTHETIC_PLACEHOLDER"
        ],
        "runtime_synthetic_count": sum(
            is_synthetic_procedure_id(item["procedure_id"]) for item in procedures
        ),
        "unknown_classification_count": classification_counts["UNKNOWN"],
        "duplicate_meaning_aliases": {
            alias: canonical
            for alias, canonical in (
                ("xac_nhan_doc_than", "xac_nhan_tinh_trang_hon_nhan"),
                ("khieu_nai", "khieu_nai_hanh_chinh"),
                ("tro_cap_bao_tro_xa_hoi", "tro_cap_xa_hoi"),
            )
        },
        "pass": (
            len(requirements) == 202
            and len(procedures) == 52
            and classification_counts["UNKNOWN"] == 0
            and classification_counts["SYNTHETIC_PLACEHOLDER"] == 90
        ),
    }

    path_payloads = (
        (Path(output_dir) / "canonical_procedures_v1.json", procedures_payload),
        (
            Path(output_dir) / "canonical_form_requirements_v1.json",
            requirements_payload,
        ),
        (
            Path(output_dir) / "canonical_forms_catalog_v1.json",
            forms_payload,
        ),
        (
            Path(output_dir) / "procedure_form_bindings_v1.json",
            bindings_payload,
        ),
        (Path(report_dir) / "baseline.json", baseline),
        (Path(report_dir) / "f0-scope-report.json", f0_report),
    )
    written_paths: list[str] = []
    if write:
        for path, payload in path_payloads:
            _write(path, payload)
            written_paths.append(str(path))

    return {
        "procedures": procedures_payload,
        "requirements": requirements_payload,
        "forms": forms_payload,
        "bindings": bindings_payload,
        "baseline": baseline,
        "f0_report": f0_report,
        "written_paths": written_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build canonical procedure/form catalog artifacts"
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    artifacts = build_catalogs(
        raw_requirements_path=args.raw,
        output_dir=args.output_dir,
        report_dir=args.report_dir,
        write=not args.check,
    )
    print(json.dumps(artifacts["f0_report"], ensure_ascii=False, indent=2))
    return 0 if artifacts["f0_report"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
