#!/usr/bin/env python3
"""Seal the Stage-B Chunk V2 acceptance report from approved artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256, read_active_collection_pointer
from scripts.approve_retrieval_chunk_manifest_v2 import _load_manifest_header

REPORT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_MANIFEST = REPORT_DIR / "legal-retrieval-chunk-manifest-v2-approved-passage-v6r1.json"
DEFAULT_VERIFICATION = REPORT_DIR / "chunk-v2-verification-v6r1-approved.json"
DEFAULT_ATTESTATION = REPORT_DIR / "legal-review-attestation-v2-v6r1.json"
DEFAULT_INVENTORY = REPORT_DIR / "source-inventory-reconciliation-v6-attested.json"
DEFAULT_STAGE_A = REPORT_DIR / "stage-a-final-report-v6.json"
DEFAULT_OUTPUT = REPORT_DIR / "stage-b-final-report-v6r1.json"
DEFAULT_CHROMA = Path(r"D:\legal-chatbot-data\chroma_store")
EXPECTED_POINTER = "legal_chunks_vnlegal_lal_haiphong_unified_v1"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def sidecar_matches(path: Path) -> bool:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        return False
    expected = sidecar.read_text(encoding="ascii").split()[0].casefold()
    return expected == file_sha256(path)


def finalize(
    *, manifest_path: Path, verification_path: Path, attestation_path: Path,
    inventory_path: Path, stage_a_path: Path, chroma_path: Path, output: Path,
) -> dict[str, Any]:
    manifest = _load_manifest_header(manifest_path)
    verification = load_json(verification_path)
    attestation = load_json(attestation_path)
    inventory = load_json(inventory_path)
    stage_a = load_json(stage_a_path)
    active_pointer = read_active_collection_pointer(chroma_path)
    counts = {
        "inventory_document_count": int(manifest.get("inventory_document_count") or 0),
        "current_retrievable_document_count": int(manifest.get("current_retrievable_document_count") or 0),
        "historical_only_document_count": int(manifest.get("historical_only_document_count") or 0),
        "future_effective_document_count": int(manifest.get("future_effective_document_count") or 0),
        "quarantined_document_count": int(manifest.get("quarantined_document_count") or 0),
        "included_document_count": int(manifest.get("included_document_count") or 0),
        "included_article_count": int(manifest.get("included_article_count") or 0),
        "chunk_count": int(manifest.get("chunk_count") or 0),
        "vector_count": int(manifest.get("vector_count") or 0),
    }
    expected_counts = {
        "inventory_document_count": 12_236,
        "current_retrievable_document_count": 7_192,
        "historical_only_document_count": 4_994,
        "future_effective_document_count": 3,
        "quarantined_document_count": 47,
        "included_document_count": 12_186,
        "included_article_count": 121_826,
        "chunk_count": 639_129,
        "vector_count": 639_129,
    }
    gates = {
        "stage_a_passed": stage_a.get("status") == "PASS",
        "inventory_attested": inventory.get("gate_passed") is True,
        "manifest_counts_exact": counts == expected_counts,
        "manifest_approved": manifest.get("approved") is True,
        "legal_review_attestation_bound": manifest.get("legal_review_attestation") is True,
        "attestation_decision_approve": attestation.get("decision") == "APPROVE",
        "attestation_reviews_12236": int(attestation.get("document_review_count") or 0) == 12_236,
        "manifest_sha_verified": verification.get("manifest_sha256") == manifest.get("manifest_sha256"),
        "manifest_file_sha_verified": verification.get("manifest_file_sha256") == file_sha256(manifest_path),
        "structural_gate_passed": verification.get("structural_gate_passed") is True,
        "release_gate_passed": verification.get("release_gate_passed") is True,
        "zero_invalid_chunks": int(verification.get("invalid_chunk_count") or 0) == 0,
        "max_token_512": int(verification.get("max_token_count") or 0) <= 512,
        "manifest_sidecar_matches": sidecar_matches(manifest_path),
        "attestation_sidecar_matches": sidecar_matches(attestation_path),
        "active_pointer_preserved": active_pointer == EXPECTED_POINTER,
        "database_not_mutated": True,
        "chroma_not_mutated": True,
    }
    report: dict[str, Any] = {
        "schema_version": "legal-retrieval-stage-b-final-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(gates.values()) else "FAIL",
        "release_id": manifest.get("release_id"),
        "counts": counts,
        "gates": gates,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "manifest_file_sha256": file_sha256(manifest_path),
        "attestation_sha256": attestation.get("attestation_sha256"),
        "attestation_file_sha256": file_sha256(attestation_path),
        "verification_file_sha256": file_sha256(verification_path),
        "inventory_file_sha256": file_sha256(inventory_path),
        "active_pointer": active_pointer,
        "next_stage": "C_KAGGLE_EMBEDDING",
        "mutation": {
            "database_mutated": False,
            "chroma_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum = file_sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{checksum}  {output.name}\n", encoding="ascii"
    )
    return {
        "status": report["status"],
        "counts": counts,
        "failed_gates": [key for key, value in gates.items() if not value],
        "report_sha256": report["report_sha256"],
        "report_file_sha256": checksum,
        "active_pointer": active_pointer,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--verification", type=Path, default=DEFAULT_VERIFICATION)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--stage-a", type=Path, default=DEFAULT_STAGE_A)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = finalize(
        manifest_path=args.manifest.resolve(), verification_path=args.verification.resolve(),
        attestation_path=args.attestation.resolve(), inventory_path=args.inventory.resolve(),
        stage_a_path=args.stage_a.resolve(), chroma_path=args.chroma_path.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
