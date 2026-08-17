"""Build review-ready Golden V3 strictly from an approved release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import fold_text


DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
)
CATEGORIES = (("procedure_dossier", 400), ("exact_form_set", 300), ("conditional_eform_gap", 100), ("ambiguity_hard_negative", 100), ("validity_role_version", 100))


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_release(manifest: dict) -> None:
    coverage = manifest.get("coverage") or {}
    if manifest.get("schema_version") != "form-release-v1": raise ValueError("FEATURE017_RELEASE_INVALID")
    if not coverage.get("complete") or any(coverage.get(done) != coverage.get(total) for done, total in (("procedure_decided", "procedure_total"), ("identity_decided", "identity_total"), ("binding_decided", "binding_total"))):
        raise ValueError("FEATURE017_COVERAGE_INCOMPLETE")
    by_domain = Counter(str(x.get("domain")) for x in manifest.get("procedures") or [] if x.get("coverage_status") in {"released", "verified_gap", "owner_deferred"})
    missing = [domain for domain in DOMAINS if not by_domain[domain]]
    if missing: raise ValueError("FEATURE017_DOMAIN_COVERAGE_INCOMPLETE:" + ",".join(missing))
    gaps = list(manifest.get("gaps") or [])
    exclusions = list(manifest.get("exclusions") or [])
    gap_counts = Counter(str(item.get("target_type") or "") for item in gaps)
    exclusion_counts = Counter(
        str(item.get("target_type") or "") for item in exclusions
    )
    expected = {
        "procedure_decided": len(manifest.get("procedures") or []),
        "identity_decided": len(manifest.get("assets") or []) + gap_counts["identity"] + exclusion_counts["identity"],
        "binding_decided": len(manifest.get("bindings") or []) + gap_counts["binding"] + exclusion_counts["binding"],
    }
    if any(int(coverage.get(key) or 0) != count for key, count in expected.items()):
        raise ValueError("FEATURE017_COVERAGE_MANIFEST_MISMATCH")


def build_cases(manifest: dict) -> list[dict]:
    _validate_release(manifest); release_hash = canonical_hash(manifest)
    procedures = list(manifest["procedures"]); assets = {x["form_id"]: x for x in manifest.get("assets") or []}; bindings = list(manifest.get("bindings") or [])
    by_domain = defaultdict(list)
    for procedure in procedures: by_domain[procedure["domain"]].append(procedure)
    normalized_names = Counter(
        fold_text(item.get("name") or item.get("canonical_name") or "")
        for item in procedures
    )
    category_slots = [name for name, count in CATEGORIES for _ in range(count)]
    # Distribute the fixed global category composition round-robin across five domains.
    slots_by_domain = {d: category_slots[i::5] for i, d in enumerate(DOMAINS)}
    cases = []
    occurrences = Counter()
    for domain in DOMAINS:
        pool = sorted(by_domain[domain], key=lambda x: x["procedure_id"])
        answerable_index = 0
        for index, category in enumerate(slots_by_domain[domain]):
            expected_clarification = category == "ambiguity_hard_negative" and index % 2 == 0
            if expected_clarification:
                procedure = pool[index % len(pool)]
            else:
                procedure = pool[answerable_index % len(pool)]
                answerable_index += 1
            pid = procedure["procedure_id"]
            related = [
                x for x in bindings
                if x.get("procedure_id") == pid
                and x.get("coverage_status") == "released"
                and x.get("audience") in {"citizen", "both"}
            ]
            form_ids = sorted({x.get("form_id") for x in related if x.get("form_id")})
            if expected_clarification: expected_ids = []
            else: expected_ids = form_ids
            variant = occurrences[(domain, pid)] % 4
            occurrences[(domain, pid)] += 1
            label = procedure.get("name") or procedure.get("canonical_name") or pid
            code = procedure.get("procedure_code") or pid
            if normalized_names[fold_text(label)] > 1:
                questions = (
                    f"Tôi cần làm thủ tục mã {code}: {label}, hồ sơ và biểu mẫu gồm những gì?",
                    f"Cho tôi xin đúng biểu mẫu của thủ tục mã {code}: {label}.",
                    f"Thủ tục mã {code} dùng mẫu nào?",
                    f"Tôi muốn thực hiện thủ tục mã {code}: {label} tại cấp xã thì chuẩn bị ra sao?",
                )
            else:
                questions = (f"Tôi cần làm thủ tục {label}, hồ sơ và biểu mẫu gồm những gì?", f"Cho tôi xin đúng biểu mẫu của {label}.", f"Thủ tục mã {code} dùng mẫu nào?", f"Tôi muốn thực hiện {label} tại cấp xã thì chuẩn bị ra sao?")
            source_asset = assets.get(expected_ids[0]) if expected_ids else None
            cases.append({"case_id": f"f017_{domain}_{index+1:03d}", "schema_version": "3.0", "domain": domain, "category": category, "question": ("Tôi cần Mẫu 01 nhưng chưa rõ thủ tục nào." if expected_clarification else questions[variant]), "procedure_id": (None if expected_clarification else pid), "expected_form_ids": expected_ids, "forbidden_form_ids": sorted(set(assets) - set(expected_ids))[:20], "required_or_conditional": ("none" if not expected_ids else ("mixed" if len({x.get('requirement') for x in related}) > 1 else related[0].get("requirement", "required"))), "condition": next((x.get("condition") for x in related if x.get("requirement") == "conditional"), None), "audience": "citizen", "legal_as_of": manifest["legal_as_of"], "source_url": source_asset.get("source_url") if source_asset else procedure.get("official_source_url"), "source_checksum": source_asset.get("source_checksum") if source_asset else procedure.get("official_source_checksum"), "expected_clarification": expected_clarification, "expected_state": ("clarification_required" if expected_clarification else procedure.get("coverage_status")), "expected_answer_mode": ("source_view_only" if expected_clarification or procedure.get("coverage_status") != "released" else "grounded_answer"), "release_id": manifest["release_id"], "release_manifest_sha256": release_hash, "generation_type": "template", "review_status": "proposed"})
    procedure_counts = Counter(
        item["procedure_id"] for item in cases if item["procedure_id"] is not None
    )
    expected_procedures = {
        str(item["procedure_id"]) for item in procedures
        if item.get("coverage_status") in {"released", "verified_gap", "owner_deferred"}
    }
    if (
        len(cases) != 1000
        or Counter(x["category"] for x in cases) != Counter(dict(CATEGORIES))
        or any(sum(x["domain"] == d for x in cases) != 200 for d in DOMAINS)
        or any(procedure_counts[procedure_id] < 3 for procedure_id in expected_procedures)
    ):
        raise AssertionError("FEATURE017_GOLDEN_DISTRIBUTION_INVALID")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--release-manifest", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--json-only", action="store_true", help="Create deterministic JSON artifacts; the review workbook is authored by the bundled spreadsheet runtime."); args = parser.parse_args()
    manifest = json.loads(args.release_manifest.read_text(encoding="utf-8")); cases = build_cases(manifest); args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "golden-v3-1000.json").write_text(json.dumps({"schema_version": "feature017-golden-v3-dataset-v1", "review_status": "proposed", "approved_checksum": None, "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "manifest.json").write_text(json.dumps({"case_count": 1000, "release_id": manifest["release_id"], "release_manifest_sha256": canonical_hash(manifest), "review_status": "proposed", "approved_checksum": None, "automation_promoted": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print({"cases": 1000, "review_status": "proposed", "approved_checksum": None}); return 0


if __name__ == "__main__": raise SystemExit(main())
