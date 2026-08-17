"""Fetch, verify and preview the official 116/2026/TT-BCA import package."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.crawlers.legal_document_pipeline import fetch_normalized_legal_document
from api.legal_validity_source import VBPLValiditySource


LAW_NUMBER = "116/2026/TT-BCA"
OFFICIAL_URL = (
    "https://vbpl.vn/van-ban/chi-tiet/"
    "thong-tu-so-116-2026-tt-bca-quy-dinh-chi-tiet-mot-so-dieu-va-bien-phap-"
    "thi-hanh-luat-cu-tru--a8024a50-83f3-11f1-9f42-03eb144ddbd7"
)


def build_import_payload(
    normalized: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    content = str(normalized.get("clean_markdown") or "").strip()
    if str(normalized.get("law_number") or "").strip() != LAW_NUMBER:
        raise ValueError("OFFICIAL_116_IDENTITY_MISMATCH")
    if str(observation.get("law_number") or "").strip() != LAW_NUMBER:
        raise ValueError("OFFICIAL_116_VALIDITY_IDENTITY_MISMATCH")
    if observation.get("identity_status") != "exact":
        raise ValueError("OFFICIAL_116_IDENTITY_NOT_EXACT")
    if observation.get("normalized_status") != "active":
        raise ValueError("OFFICIAL_116_NOT_CURRENT")
    if observation.get("evidence_status") != "sufficient":
        raise ValueError("OFFICIAL_116_VALIDITY_EVIDENCE_INSUFFICIENT")
    if len(content) < 1000 or "Điều 1" not in content:
        raise ValueError("OFFICIAL_116_CONTENT_INCOMPLETE")
    source_url = str(observation.get("source_url") or "").strip()
    if source_url != OFFICIAL_URL:
        raise ValueError("OFFICIAL_116_SOURCE_URL_MISMATCH")
    return {
        "title": str(normalized.get("title") or "").strip(),
        "law_number": LAW_NUMBER,
        "document_type": str(normalized.get("document_type") or "Thông tư").strip(),
        "issuing_agency": str(observation.get("issuing_agency") or "").strip(),
        "scope": "Trung ương",
        "sector": "Đăng ký, quản lý cư trú",
        "field_id": 9,
        "issued_date": observation.get("issued_date"),
        "effective_date": observation.get("effective_from"),
        "expired_date": observation.get("effective_to"),
        "source_url": source_url,
        "applicability_info": (
            "Nguồn VBPL xác nhận còn hiệu lực; áp dụng từ "
            f"{observation.get('effective_from')}."
        ),
        "content": content,
        "confirmed_official_source": True,
        "domain_slug": "cu_tru_an_ninh",
        "structure": "auto",
    }


async def prepare(output_dir: Path, retrieval_url: str) -> dict[str, Any]:
    normalized = await fetch_normalized_legal_document(
        OFFICIAL_URL, scope="central", timeout_seconds=60
    )
    validity = await VBPLValiditySource(timeout_seconds=30).fetch(
        instrument=LAW_NUMBER,
        document_id=None,
        as_of=date(2026, 8, 11),
        expected_issuing_agency="Bộ Công an",
        expected_issued_date="2026-06-29",
    )
    if validity.observation is None:
        raise RuntimeError(f"OFFICIAL_116_VALIDITY_FETCH_FAILED:{validity.reason_code}")
    observation = validity.observation.to_dict()
    payload = build_import_payload(normalized, observation)
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{retrieval_url.rstrip('/')}/import/preview", json=payload
        )
        response.raise_for_status()
        preview = response.json()
    if preview.get("valid") is not True:
        raise RuntimeError(f"OFFICIAL_116_IMPORT_PREVIEW_REJECTED:{preview!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    content_path = output_dir / "116-2026-TT-BCA.txt"
    payload_path = output_dir / "116-2026-TT-BCA-import-payload.json"
    manifest_path = output_dir / "116-2026-TT-BCA-source-manifest.json"
    content_path.write_text(payload["content"] + "\n", encoding="utf-8")
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    content_hash = hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "golden-294-official-source-v1",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "law_number": LAW_NUMBER,
        "source_url": OFFICIAL_URL,
        "source_status": validity.source_status,
        "validity_reason_code": validity.reason_code,
        "observation": observation,
        "normalized": {
            key: normalized.get(key)
            for key in (
                "status",
                "final_url",
                "title",
                "law_number",
                "document_type",
                "issuing_agency",
                "issued_date",
                "effective_date",
                "characters",
                "content_hash",
                "primary_domain",
                "matched_domains",
                "extraction",
            )
        },
        "content_sha256": content_hash,
        "content_characters": len(payload["content"]),
        "import_preview": preview,
        "artifacts": {
            "content": str(content_path),
            "payload": str(payload_path),
        },
        "mutation_performed": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--retrieval-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    manifest = asyncio.run(prepare(args.output_dir.resolve(), args.retrieval_url))
    print(
        json.dumps(
            {
                "law_number": manifest["law_number"],
                "source_url": manifest["source_url"],
                "characters": manifest["content_characters"],
                "content_sha256": manifest["content_sha256"],
                "preview": {
                    key: manifest["import_preview"].get(key)
                    for key in ("valid", "structure", "article_count", "chunk_count")
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
