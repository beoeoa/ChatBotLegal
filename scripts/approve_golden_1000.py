"""Freeze a user-approved Golden 1,000 release without mutating the legal corpus."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def v2_proof(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not str(value.get("quote") or "").strip():
        return None
    return {
        "quote": str(value["quote"]),
        "page_number": value.get("page_number"),
        "char_start": value.get("char_start"),
        "char_end": value.get("char_end"),
        "source_sha256": None,
    }


def v2_source(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "law_number": str(value.get("law_number") or ""),
        "article": str(value["article"]) if value.get("article") is not None else None,
        "clause": str(value["clause"]) if value.get("clause") is not None else None,
        "point": str(value["point"]) if value.get("point") is not None else None,
        "reason": str(value["reason"]) if value.get("reason") is not None else None,
        "proof": v2_proof(value.get("proof")),
    }
    return result


def v2_case(case: dict[str, Any]) -> dict[str, Any]:
    split = str(case.get("evaluation_split") or "")
    risk_tags = [
        str(tag)
        for tag in case.get("risk_tags") or []
        if tag != "requires_human_legal_approval"
    ]
    risk_tags.extend(["user_approved_all", f"evaluation_split:{split}"])
    if case.get("expected_answer_mode") == "explicit_fallback":
        risk_tags.append("fallback:explicit_insufficient_evidence")
    return {
        "case_id": case["case_id"],
        "schema_version": "2.0",
        "domain": case["domain"],
        "procedure_family": case.get("procedure_family"),
        "legal_as_of": case["legal_as_of"],
        "questions": {
            "citizen": case["questions"]["citizen"],
            "officer": case["questions"].get("officer"),
        },
        "expected_sources": [v2_source(item) for item in case.get("expected_sources") or []],
        "forbidden_sources": [v2_source(item) for item in case.get("forbidden_sources") or []],
        "required_claims": copy.deepcopy(case.get("required_claims") or []),
        "expected_answer_mode": (
            "source_view_only"
            if case.get("expected_answer_mode") == "explicit_fallback"
            else case.get("expected_answer_mode")
        ),
        "expected_refusal": bool(case.get("expected_refusal")),
        "risk_tags": sorted(set(risk_tags)),
        "review_status": "approved",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--workbook-verification", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--canonical-output", type=Path, required=True)
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    validation = json.loads(args.validation.read_text(encoding="utf-8"))
    workbook_verification = json.loads(args.workbook_verification.read_text(encoding="utf-8"))
    cases = dataset.get("cases") or []
    if len(cases) != 1000:
        raise SystemExit("approval refused: dataset does not contain exactly 1,000 cases")
    if not validation.get("all_passed") or not workbook_verification.get("all_passed"):
        raise SystemExit("approval refused: pre-approval machine gates did not pass")
    if any(case.get("review_status") != "pending_human_review" for case in cases):
        raise SystemExit("approval refused: unexpected pre-approval case status")

    approved_at = datetime.now(timezone.utc).isoformat()
    user_statement = "Tôi đã xem qua toàn bộ và đồng ý duyệt tất cả."
    input_hashes = {
        "dataset_sha256": sha256(args.dataset),
        "workbook_sha256": sha256(args.workbook),
        "validation_sha256": sha256(args.validation),
        "workbook_verification_sha256": sha256(args.workbook_verification),
    }

    rich = copy.deepcopy(dataset)
    rich["dataset_kind"] = "user_approved_legal_ground_truth"
    rich["status"] = "approved"
    rich["approved_at"] = approved_at
    rich["approval"] = {
        "decision": "approve_all",
        "actor": "workspace_user",
        "channel": "codex_task",
        "statement_sha256": hashlib.sha256(user_statement.encode("utf-8")).hexdigest(),
        "case_count": 1000,
        "machine_gates_passed_before_approval": True,
    }
    rich["usage_notice"] = (
        "User-approved Golden ground truth at legal_as_of=2026-08-11. "
        "Revalidate affected cases whenever a referenced document changes validity."
    )
    rich["summary"]["approved_case_count"] = 1000
    rich["summary"]["pending_review_count"] = 0
    for case in rich["cases"]:
        case["review_status"] = "approved"
        case["risk_tags"] = sorted(
            {
                *(tag for tag in case.get("risk_tags") or [] if tag != "requires_human_legal_approval"),
                "user_approved_all",
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rich_path = args.output_dir / "golden-1000-approved-full.json"
    atomic_json(rich_path, rich)

    canonical = {
        "schema_version": "2.0",
        "dataset_kind": "user_approved_legal_ground_truth",
        "review_notice": "All 1,000 cases were explicitly approved by the workspace user after review.",
        "legal_as_of": dataset["legal_as_of"],
        "approved_at": approved_at,
        "approval": rich["approval"],
        "case_metadata": [
            {
                "case_id": case["case_id"],
                "evaluation_split": case["evaluation_split"],
                "unresolved_reason": case.get("unresolved_reason"),
                "original_answer_mode": case.get("expected_answer_mode"),
            }
            for case in cases
        ],
        "cases": [v2_case(case) for case in cases],
    }
    canonical_path = args.output_dir / "golden-1000-approved-v2.json"
    atomic_json(canonical_path, canonical)
    atomic_json(args.canonical_output, canonical)

    receipt_core = {
        "schema_version": "golden-1000-approval-receipt-v1",
        "status": "approved",
        "approved_at": approved_at,
        "legal_as_of": dataset["legal_as_of"],
        "decision": "approve_all",
        "actor": "workspace_user",
        "channel": "codex_task",
        "case_count": 1000,
        "claim_count": dataset["summary"]["claim_count"],
        "source_count": dataset["summary"]["source_count"],
        "input_hashes": input_hashes,
        "output_hashes": {
            "approved_full_sha256": sha256(rich_path),
            "approved_v2_sha256": sha256(canonical_path),
            "canonical_store_sha256": sha256(args.canonical_output),
        },
        "production_corpus_mutated": False,
        "production_vectors_mutated": False,
    }
    canonical_receipt = json.dumps(receipt_core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    receipt_core["entry_hash"] = hashlib.sha256(canonical_receipt.encode("utf-8")).hexdigest()
    receipt_path = args.output_dir / "approval-receipt.json"
    atomic_json(receipt_path, receipt_core)

    manifest = {
        "schema_version": "golden-1000-approved-manifest-v1",
        "status": "approved",
        "legal_as_of": dataset["legal_as_of"],
        "approved_at": approved_at,
        "artifacts": {
            "approved_full": {"path": rich_path.name, "sha256": sha256(rich_path)},
            "approved_v2": {"path": canonical_path.name, "sha256": sha256(canonical_path)},
            "approval_receipt": {"path": receipt_path.name, "sha256": sha256(receipt_path)},
            "canonical_store": {"path": str(args.canonical_output), "sha256": sha256(args.canonical_output)},
        },
        "production_mutation": False,
    }
    atomic_json(args.output_dir / "approved-manifest.json", manifest)
    print(json.dumps({
        "status": "approved",
        "cases": len(cases),
        "approved_full": str(rich_path),
        "approved_v2": str(canonical_path),
        "canonical_store": str(args.canonical_output),
        "receipt_entry_hash": receipt_core["entry_hash"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
