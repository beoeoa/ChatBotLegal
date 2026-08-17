"""Validate feature-016 golden cases without promoting review status."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


DEFAULT_SCHEMA = (
    Path(__file__).parents[1]
    / "specs"
    / "016-legal-answer-trust-hardening"
    / "contracts"
    / "golden-case-v2.schema.json"
)


def _norm(value: Any) -> str:
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))
    ).strip().casefold()


def _source_key(source: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(
        _norm(source.get(field))
        for field in ("law_number", "article", "clause", "point")
    )


def validate_dataset(
    payload: dict[str, Any], schema_path: str | Path = DEFAULT_SCHEMA
) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != "2.0":
        errors.append("dataset.schema_version must be 2.0")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return errors + ["dataset.cases must be an array"]

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    seen_ids: set[str] = set()
    seen_questions: dict[str, str] = {}
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for violation in sorted(validator.iter_errors(case), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in violation.path)
            errors.append(f"{prefix}.{location}: {violation.message}")

        case_id = str(case.get("case_id") or "")
        if case_id in seen_ids:
            errors.append(f"{prefix}: duplicate case_id {case_id}")
        seen_ids.add(case_id)

        questions = case.get("questions") or {}
        for role, question in questions.items():
            if not question:
                continue
            question_hash = hashlib.sha256(_norm(question).encode("utf-8")).hexdigest()
            if question_hash in seen_questions:
                errors.append(
                    f"{prefix}.questions.{role}: duplicate question with "
                    f"{seen_questions[question_hash]}"
                )
            else:
                seen_questions[question_hash] = f"{case_id}:{role}"

        expected = case.get("expected_sources") or []
        forbidden = case.get("forbidden_sources") or []
        if case.get("review_status") == "approved" and not expected and not case.get(
            "expected_refusal"
        ):
            errors.append(
                f"{prefix}: approved case needs an expected source or explicit refusal"
            )
        for label, sources in (("expected_sources", expected), ("forbidden_sources", forbidden)):
            keys = [_source_key(source) for source in sources if isinstance(source, dict)]
            if len(keys) != len(set(keys)):
                errors.append(f"{prefix}.{label}: duplicate legal source")
            for source_index, source in enumerate(sources):
                proof = source.get("proof") if isinstance(source, dict) else None
                if not isinstance(proof, dict):
                    continue
                start = proof.get("char_start")
                end = proof.get("char_end")
                if (start is None) != (end is None):
                    errors.append(
                        f"{prefix}.{label}[{source_index}].proof: both char_start and char_end are required"
                    )
                elif start is not None and end is not None and end <= start:
                    errors.append(
                        f"{prefix}.{label}[{source_index}].proof.char_end must be greater than char_start"
                    )

        claims = case.get("required_claims") or []
        claim_ids = [str(claim.get("claim_id")) for claim in claims if isinstance(claim, dict)]
        if len(claim_ids) != len(set(claim_ids)):
            errors.append(f"{prefix}.required_claims: duplicate claim_id")
        orders = [claim.get("order") for claim in claims if isinstance(claim, dict)]
        if orders and sorted(orders) != list(range(1, len(orders) + 1)):
            errors.append(
                f"{prefix}.required_claims: order must be unique and contiguous from 1"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, type=Path)
    args = parser.parse_args()
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    errors = validate_dataset(payload, args.schema)
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {"valid": True, "cases": len(payload.get("cases") or [])},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
