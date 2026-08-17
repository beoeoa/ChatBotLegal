"""Run the deterministic 52 x 20 x 3 procedure/form resolution matrix."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import (
    FormCatalog,
    _binding_is_approved,
    _form_is_runtime_approved,
    fold_text,
    normalize_procedure_id,
)


REPORT_DIR = ROOT / "reports" / "feature005" / "forms-completion-20260724"
REPORT_PATH = REPORT_DIR / "f6-form-resolution-matrix.json"
ROLES = ("citizen", "officer", "admin")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int((len(ordered) - 1) * percentile)),
    )
    return ordered[index]


def _variants(
    procedure: dict[str, Any],
    next_procedure: dict[str, Any],
    forms: list[dict[str, Any]],
    all_forms: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    runtime_requirement_pairs: set[tuple[str, str]] | None = None,
) -> list[tuple[str, set[str]]]:
    procedure_id = str(procedure["procedure_id"])
    name = str(procedure["name"])
    aliases = [
        str(value)
        for value in procedure.get("aliases") or []
        if str(value).strip()
    ]
    alias = max(aliases, key=len, default=name)
    folded_name = fold_text(name)
    approved_binding_pairs = {
        (
            normalize_procedure_id(item.get("procedure_id")),
            str(item.get("form_id") or ""),
        )
        for item in bindings
        if _binding_is_approved(item)
    }
    code_form = next(
        (
            form
            for form in forms
            if form.get("form_code")
            and _form_is_runtime_approved(form)
            and (
                procedure_id,
                str(form.get("form_id") or ""),
            )
            in approved_binding_pairs
            and (
                runtime_requirement_pairs is None
                or (
                    procedure_id,
                    str(form.get("form_id") or ""),
                )
                in runtime_requirement_pairs
            )
        ),
        None,
    )
    if code_form:
        code = str(code_form["form_code"])
        code_expected = {
            str(value)
            for candidate in all_forms
            if _form_is_runtime_approved(candidate)
            if fold_text(candidate.get("form_code")) == fold_text(code)
            for value in candidate.get("procedure_ids") or []
            if (
                normalize_procedure_id(value),
                str(candidate.get("form_id") or ""),
            )
            in approved_binding_pairs
            if (
                runtime_requirement_pairs is None
                or (
                    normalize_procedure_id(value),
                    str(candidate.get("form_id") or ""),
                )
                in runtime_requirement_pairs
            )
        }
        code_query = f"Cho tôi mẫu {code}"
    else:
        code_expected = {procedure_id}
        code_query = f"Cho tôi biểu mẫu của thủ tục {name}"
    multi_expected = {procedure_id, str(next_procedure["procedure_id"])}

    return [
        (name, {procedure_id}),
        (folded_name, {procedure_id}),
        (f"Tôi cần biểu mẫu {name}", {procedure_id}),
        (f"Cho tôi tải mẫu của thủ tục {name}", {procedure_id}),
        (
            f"mã thủ tục {procedure_id}"
            if procedure_id[:1].isdigit()
            else procedure_id.replace("_", " "),
            {procedure_id},
        ),
        (fold_text(alias), {procedure_id}),
        (alias, {procedure_id}),
        (f"Tra cứu thủ tục {name}", {procedure_id}),
        (name.upper(), {procedure_id}),
        (f"({name})?", {procedure_id}),
        (f"Hồ sơ thực hiện {name} gồm gì", {procedure_id}),
        (f"{name} tại phường thuộc quận Lê Chân", {procedure_id}),
        (f"Cần mẫu cũ hay mẫu hiện hành cho {name}", {procedure_id}),
        (f"Xin hướng dẫn nơi nộp thủ tục {name}", {procedure_id}),
        (f"Người dân hỏi thủ tục {name}", {procedure_id}),
        (f"Cán bộ xử lý thủ tục {name}", {procedure_id}),
        (f"Admin kiểm tra provenance thủ tục {name}", {procedure_id}),
        (code_query, code_expected),
        (
            f"{name}; đồng thời {next_procedure['name']}",
            multi_expected,
        ),
        (f"Không dấu: thủ tục {folded_name}; cần tờ khai", {procedure_id}),
    ]


def evaluate_matrix(
    catalog: FormCatalog,
    *,
    write: bool = True,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    # The matrix is the deep, 52-procedure canonical-regression suite.  The
    # runtime bridge deliberately adds the wider 418-procedure DVC surface;
    # exercising it here would multiply this suite to 25,080 cases and blur
    # its signal.  The separately checksum-bound feature006 lookup dataset
    # covers that 418-procedure release surface.
    procedures = sorted(
        (
            procedure
            for procedure in catalog.procedures
            if not (
                isinstance(procedure.get("provenance"), dict)
                and procedure["provenance"].get("kind")
                == "official_three_tier_runtime_bridge"
            )
        ),
        key=lambda item: str(item.get("procedure_id") or ""),
    )
    # Resolve the canonical regression prompts against the same canonical
    # procedure surface.  The broader bridge remains covered by the distinct
    # 418-procedure release dataset; mixing its numeric DVC aliases here would
    # report an equivalent bridge identifier as a false canonical mismatch.
    matrix_catalog = FormCatalog(
        procedures=procedures,
        forms=catalog.forms,
        bindings=catalog.bindings,
        runtime_requirement_pairs=catalog._runtime_requirement_pairs,
        project_root=catalog.project_root,
    )
    forms_by_procedure: dict[str, list[dict[str, Any]]] = {}
    for procedure in procedures:
        pid = str(procedure["procedure_id"])
        forms_by_procedure[pid] = [
            form
            for form in catalog.forms
            if pid in {str(value) for value in form.get("procedure_ids") or []}
        ]

    timings: list[float] = []
    wrong_procedure = 0
    positive_procedure_cases = 0
    top1_hits = 0
    wrong_form = 0
    role_leakage = 0
    pending_exposure = 0
    expired_exposure = 0
    forms_unavailable = 0
    variant_count = 0
    role_counts: Counter[str] = Counter()
    failure_groups: Counter[str] = Counter()

    for index, procedure in enumerate(procedures):
        next_procedure = procedures[(index + 1) % len(procedures)]
        variants = _variants(
            procedure,
            next_procedure,
            forms_by_procedure[str(procedure["procedure_id"])],
            catalog.forms,
            catalog.bindings,
            matrix_catalog._runtime_requirement_pairs,
        )
        variant_count += len(variants)
        for variant_index, (question, expected_ids) in enumerate(variants):
            for role in ROLES:
                role_counts[role] += 1
                started = time.perf_counter()
                procedure_result = matrix_catalog.resolve_procedures(question)
                form_result = matrix_catalog.resolve_forms(
                    question,
                    role=role,
                    as_of=date(2026, 7, 24),
                )
                timings.append((time.perf_counter() - started) * 1000)

                matches = procedure_result["matches"]
                top_id = str(matches[0]["procedure_id"]) if matches else ""
                if expected_ids:
                    positive_procedure_cases += 1
                    if top_id in expected_ids:
                        top1_hits += 1
                    else:
                        wrong_procedure += 1
                        failure_groups[
                            f"{procedure['procedure_id']}:{variant_index}:{top_id or 'none'}"
                        ] += 1
                else:
                    if matches:
                        wrong_procedure += 1
                        failure_groups[
                            f"{procedure['procedure_id']}:{variant_index}:{top_id}"
                        ] += 1

                for form in form_result["recommended_forms"]:
                    form_procedures = {
                        str(value)
                        for value in (
                            catalog._forms_by_id.get(
                                str(form.get("form_id") or ""),
                                {},
                            ).get("procedure_ids")
                            or []
                        )
                    }
                    if not form_procedures & expected_ids:
                        wrong_form += 1
                    if role == "citizen" and form.get("audience") == "officer":
                        role_leakage += 1
                    source = catalog._forms_by_id.get(
                        str(form.get("form_id") or ""),
                        {},
                    )
                    if source.get("review_status") != "approved":
                        pending_exposure += 1
                    effective_to = source.get("effective_to")
                    expired_by_date = False
                    if effective_to:
                        try:
                            expired_by_date = (
                                date.fromisoformat(str(effective_to)[:10])
                                < date(2026, 7, 24)
                            )
                        except ValueError:
                            expired_by_date = True
                    if expired_by_date or source.get("supersedes_form_id"):
                        expired_exposure += 1
                if form_result["forms_unavailable"]:
                    forms_unavailable += 1

    case_count = sum(role_counts.values())
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": "2026-07-24",
        "procedure_count": len(procedures),
        "variants_per_procedure": (
            variant_count // max(1, len(procedures))
        ),
        "role_counts": dict(role_counts),
        "case_count": case_count,
        "procedure_top1_rate": (
            top1_hits / positive_procedure_cases
            if positive_procedure_cases
            else 0.0
        ),
        "wrong_procedure_count": wrong_procedure,
        "wrong_form_count": wrong_form,
        "role_leakage_count": role_leakage,
        "pending_reference_seed_exposure_count": pending_exposure,
        "expired_or_superseded_exposure_count": expired_exposure,
        "forms_unavailable_case_count": forms_unavailable,
        "lookup_p50_ms": round(statistics.median(timings), 3)
        if timings
        else 0.0,
        "lookup_p95_ms": round(_percentile(timings, 0.95), 3),
        "lookup_max_ms": round(max(timings), 3) if timings else 0.0,
        "model_call_count": 0,
        "failure_groups": dict(sorted(failure_groups.items())),
        "privacy": {
            "contains_question_text": False,
            "contains_answer_text": False,
            "contains_credentials": False,
        },
    }
    report["technical_pass"] = bool(
        case_count >= 3120
        and report["procedure_top1_rate"] >= 0.99
        and wrong_procedure == 0
        and wrong_form == 0
        and role_leakage == 0
        and pending_exposure == 0
        and expired_exposure == 0
        and report["lookup_p95_ms"] <= 200
    )
    if write:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the deterministic procedure/form matrix"
    )
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = evaluate_matrix(
        FormCatalog.load_default(),
        write=True,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["technical_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
