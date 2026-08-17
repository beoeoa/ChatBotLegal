"""Evaluate Feature 016 Gate D from deterministic proof and rehearsal evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.legal_audit_chain import (
    append_audit_entry,
    create_checkpoint,
    verify_audit_chain,
    verify_checkpoint,
)
from api.legal_citation_provenance import citation_can_support_claim, verify_citation
from api.legal_provider_privacy import ProviderEgressBlocked, prepare_provider_egress


def _proof_checks() -> dict[str, bool]:
    page = "Điều 1. Nội dung.\nKhoản 1. Ủy ban nhân dân cấp xã thực hiện thủ tục."
    quote = "Ủy ban nhân dân cấp xã thực hiện thủ tục."
    asset = b"feature016-isolated-source-asset"
    start = page.index(quote)
    proof = {
        "source_asset_sha256": hashlib.sha256(asset).hexdigest(),
        "page_number": 2,
        "char_start": start,
        "char_end": start + len(quote),
        "bounding_box": [10, 20, 300, 40],
        "text_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "verification_status": "verified",
    }
    common = {
        "source_text": page,
        "support_quote": quote,
        "source_pages": {2: page, 3: "Trang khác."},
        "source_asset_bytes": asset,
        "internal_url": "/legal-documents/fixture",
    }
    valid = verify_citation(provenance=proof, **common)
    wrong_quote = verify_citation(
        provenance=proof, **{**common, "support_quote": "Trích dẫn bị bịa."}
    )
    wrong_page = verify_citation(
        provenance={**proof, "page_number": 3}, **common
    )
    wrong_asset = verify_citation(
        provenance={**proof, "source_asset_sha256": "0" * 64}, **common
    )
    metadata = verify_citation(
        source_text=None,
        support_quote=None,
        provenance=None,
        internal_url="/legal-documents/fixture",
    )
    return {
        "physical_span_verified": valid.verification_level == "physical_span",
        "wrong_quote_rejected": wrong_quote.status == "rejected",
        "wrong_page_rejected": wrong_page.status == "rejected",
        "wrong_asset_rejected": wrong_asset.status == "rejected",
        "metadata_only_not_claim_supporting": not citation_can_support_claim(metadata),
    }


def _audit_checks() -> dict[str, bool]:
    entries: list[dict[str, Any]] = []
    for sequence in range(1, 4):
        entries.append(
            append_audit_entry(
                entries,
                event_time=f"2026-08-10T10:0{sequence}:00+00:00",
                event_type="legal.candidate.review",
                actor_id="admin-fixture",
                actor_role="admin",
                object_type="legal_document",
                object_id="fixture",
                detail={"sequence": sequence},
            )
        )
    edited = deepcopy(entries)
    edited[0]["detail_hash"] = "f" * 64
    inserted = deepcopy(entries)
    inserted.insert(1, deepcopy(entries[0]))
    private_key = Ed25519PrivateKey.generate()
    checkpoint = create_checkpoint(
        entries,
        chain_id="legal-critical",
        checkpoint_id="gate-d",
        created_at="2026-08-10T10:10:00+00:00",
        private_key=private_key,
    )
    return {
        "valid_chain_verified": verify_audit_chain(entries).valid,
        "edit_detected": not verify_audit_chain(edited).valid,
        "delete_detected": not verify_audit_chain([entries[0], entries[2]]).valid,
        "insert_detected": not verify_audit_chain(inserted).valid,
        "reorder_detected": not verify_audit_chain(
            [entries[1], entries[0], entries[2]]
        ).valid,
        "tail_deletion_detected": not verify_checkpoint(
            checkpoint, entries=entries[:-1], public_key=private_key.public_key()
        ).valid,
        "wrong_key_detected": not verify_checkpoint(
            checkpoint,
            entries=entries,
            public_key=Ed25519PrivateKey.generate().public_key(),
        ).valid,
    }


def _provider_checks() -> dict[str, bool]:
    original = (
        "Tôi là Nguyễn Văn A, CCCD 012345678901, điện thoại 0912345678, "
        "email citizen@example.test, địa chỉ 12 Lạch Tray."
    )
    decision = prepare_provider_egress(
        original,
        provider="https://user:secret@api.example.test/v1",
        model="models/legal?api_key=secret-token",
    )
    blocked = False
    try:
        prepare_provider_egress(
            "Hồ sơ bệnh án cá nhân: nội dung tự do.",
            provider="openai",
            model="configured-model",
        )
    except ProviderEgressBlocked:
        blocked = True
    public_labels = f"{decision.provider_label} {decision.model_label}".casefold()
    audit = str(decision.audit_record).casefold()
    return {
        "cloud_pii_redacted": decision.redaction_applied
        and not any(
            value in decision.text
            for value in (
                "Nguyễn Văn A",
                "012345678901",
                "0912345678",
                "citizen@example.test",
                "12 Lạch Tray",
            )
        ),
        "unredactable_cloud_blocked": blocked,
        "audit_has_hashes_not_raw_prompt": "input_sha256" in audit
        and "012345678901" not in audit
        and "citizen@example.test" not in audit,
        "public_labels_safe": not any(
            value in public_labels for value in ("secret", "api_key", "http", "user:")
        ),
    }


def evaluate_gate_d(migration: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        **_proof_checks(),
        **_audit_checks(),
        **_provider_checks(),
        "isolated_database_used": str(migration.get("database") or "").startswith(
            "feature016_trust_rehearsal_"
        ),
        "migration_forward_passed": migration.get("up_status") == "applied"
        and all(int(value or 0) == 0 for value in (migration.get("forward_counts") or {}).values()),
        "migration_rollback_passed": migration.get("down_status") == "applied"
        and all(bool(value) for value in (migration.get("removed") or {}).values()),
        "live_database_untouched": migration.get("live_database_touched") is False,
        "real_corpus_untouched": migration.get("real_corpus_mutated") is False,
        "browser_uat_not_run": migration.get("browser_uat_run") is False,
    }
    failed = sorted(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": "feature016-gate-d-v1",
        "status": "pass" if not failed else "fail",
        "gate_d_pass": not failed,
        "reason_codes": failed,
        "checks": checks,
        "migration_rehearsal": dict(migration),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate_gate_d(
        json.loads(args.migration.read_text(encoding="utf-8"))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "gate_d_pass", "reason_codes")
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["gate_d_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
