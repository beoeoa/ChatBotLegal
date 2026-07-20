"""Build the sanitized 30-case packet for independent legal-expert review.

The packet deliberately excludes questions, answers, facts, credentials, and
raw citations. Machine-proposed source labels remain candidates only and never
become approved legal metadata without an expert decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.pilot_quality_gate import artifact_fingerprint, validate_manifest
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from pilot_quality_gate import artifact_fingerprint, validate_manifest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "pilot" / "ask_quality_manifest.json"
DEFAULT_EXPERT = ROOT / "notebook_data" / "legal-golden-expert-review.json"
DEFAULT_OUTPUT = (
    ROOT
    / "specs"
    / "004-gate-role-rollout"
    / "expert-review-packet.template.json"
)

LEGAL_REVIEW_CRITERIA = (
    "unsupported_or_fabricated_source",
    "wrong_document_effectivity",
    "wrong_authority_or_jurisdiction",
    "fabricated_article_date_deadline_fee_or_form",
    "important_source_or_conflict_omitted",
    "unsupported_certainty_when_evidence_is_insufficient",
)
_FORBIDDEN_KEYS = {
    "answer",
    "authorization",
    "citations",
    "citizen_question",
    "credential",
    "officer_question",
    "password",
    "prompt",
    "question",
    "required_facts",
    "secret",
    "token",
}


class PacketValidationError(ValueError):
    """Raised when the packet could leak content or imply expert approval."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PacketValidationError(f"cannot_read_json:{path}") from exc
    if not isinstance(value, dict):
        raise PacketValidationError(f"json_root_must_be_object:{path}")
    return value


def _candidate_from_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        label = next(
            (
                str(value.get(key) or "").strip()
                for key in ("title", "name", "law_number", "document_id", "id")
                if value.get(key)
            ),
            "",
        )
        if not label:
            return None
        article_refs = value.get("article_refs") or value.get("articles") or []
        if not isinstance(article_refs, list):
            article_refs = [article_refs]
        return {
            "candidate_label": label,
            "document_id": value.get("document_id") or value.get("doc_id"),
            "law_number": value.get("law_number"),
            "source_url": value.get("source_url") or value.get("official_url"),
            "authority": value.get("authority") or value.get("issuing_authority"),
            "effective_status": value.get("effective_status"),
            "jurisdiction": value.get("jurisdiction"),
            "article_refs": [str(item) for item in article_refs if str(item).strip()],
            "review_state": "candidate_pending_review",
            "accepted_by_expert": None,
        }
    label = str(value or "").strip()
    if not label:
        return None
    return {
        "candidate_label": label,
        "document_id": None,
        "law_number": None,
        "source_url": None,
        "authority": None,
        "effective_status": None,
        "jurisdiction": None,
        "article_refs": [],
        "review_state": "candidate_pending_review",
        "accepted_by_expert": None,
    }


def _source_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    proposal = record.get("machine_proposal")
    proposed = proposal.get("expected_citations") if isinstance(proposal, dict) else []
    values = [
        *(record.get("expected_documents") or []),
        *(record.get("expected_articles") or []),
        *(proposed or []),
    ]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        candidate = _candidate_from_value(value)
        if candidate is None:
            continue
        key = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def assert_packet_privacy(packet: Any, *, path: str = "packet") -> None:
    if isinstance(packet, Mapping):
        for key, value in packet.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_KEYS:
                raise PacketValidationError(f"forbidden_packet_field:{path}.{key}")
            assert_packet_privacy(value, path=f"{path}.{key}")
        records = packet.get("records")
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, Mapping):
                    raise PacketValidationError("packet_record_must_be_object")
                if record.get("expert_review_status") != "pending":
                    raise PacketValidationError("expert_review_must_remain_pending")
                for field in (
                    "expert_name",
                    "reviewed_at",
                    "expert_score",
                    "citation_applicable",
                    "form_applicable",
                    "critical_hallucination",
                ):
                    if record.get(field) is not None:
                        raise PacketValidationError(
                            f"expert_authority_field_must_be_empty:{field}"
                        )
    elif isinstance(packet, list):
        for index, value in enumerate(packet):
            assert_packet_privacy(value, path=f"{path}[{index}]")


def build_expert_review_packet(
    manifest: dict[str, Any], expert_payload: dict[str, Any]
) -> dict[str, Any]:
    manifest_errors = validate_manifest(manifest, expert_payload)
    if manifest_errors:
        raise PacketValidationError(";".join(manifest_errors))
    expert_by_id = {
        str(item.get("review_id")): item
        for item in expert_payload.get("records") or []
        if isinstance(item, dict) and item.get("review_id")
    }
    records: list[dict[str, Any]] = []
    for case in manifest.get("cases") or []:
        review_id = str(case["review_id"])
        source = expert_by_id[review_id]
        records.append(
            {
                "review_id": review_id,
                "case_id": source.get("case_id"),
                "domain": str(case["domain"]),
                "role": str(case["role"]),
                "scenario_class": str(case["scenario_class"]),
                "legal_as_of": source.get("legal_as_of"),
                "event_date": source.get("event_date"),
                "source_candidates": _source_candidates(source),
                "legal_review_criteria": list(LEGAL_REVIEW_CRITERIA),
                "citation_applicable": None,
                "form_applicable": None,
                "critical_hallucination": None,
                "expert_review_status": "pending",
                "expert_score": None,
                "expert_name": None,
                "reviewed_at": None,
                "review_notes": None,
            }
        )
    packet = {
        "schema_version": "1.0",
        "purpose": "Sanitized 30-case legal-expert review packet; no automated approval.",
        "source_fingerprints": {
            "manifest_sha256": artifact_fingerprint(manifest),
            "expert_source_sha256": artifact_fingerprint(expert_payload),
        },
        "record_count": len(records),
        "records": records,
    }
    assert_packet_privacy(packet)
    return packet


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise PacketValidationError(f"refusing_to_overwrite:{path}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expert", type=Path, default=DEFAULT_EXPERT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Create the packet at --output. Existing files are never overwritten.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        packet = build_expert_review_packet(
            _read_json(args.manifest), _read_json(args.expert)
        )
        if args.write:
            _write_new(args.output, packet)
    except PacketValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "record_count": packet["record_count"],
                "all_reviews_pending": True,
                "contains_protected_content": False,
                "written": bool(args.write),
                "output": str(args.output) if args.write else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
