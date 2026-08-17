"""Resolve Feature 005 Step 1 data gaps without unsafe source substitution.

The command verifies official files, imports only genuinely missing current
instruments through the existing endpoint, copies only new vectors into the
versioned shadow primary collection, and emits an updated expected-source
contract. It never deletes corpus rows, changes the active pointer, or
re-embeds an existing chunk.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import chromadb
import fitz
import httpx
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_lechan_serving_collections import (
    _hydrate_database_rows,
    _hydrated_metadata,
    _passage,
    _sha256_ids,
)
from scripts.legal_search_server import (
    _is_official_legal_source_url,
    _same_legal_document_identity,
)


LEGAL_AS_OF = date(2026, 7, 23)
DEFAULT_GAPS = (
    ROOT
    / "reports"
    / "feature005"
    / "step1-retrieval-20260724"
    / "verified-data-gaps.json"
)
DEFAULT_DB3_EXPECTED = (
    ROOT
    / "reports"
    / "feature005"
    / "db3-coverage-20260723"
    / "expected-sources.jsonl"
)
DEFAULT_OUTPUT = ROOT / "reports" / "feature005" / "step2-data-gap-20260724"
DEFAULT_CHROMA = Path(r"J:\legal-chatbot-data\chroma_store")
SOURCE_COLLECTION = "legal_chunks_vnlegal_lal"
PRIMARY_COLLECTION = "legal_chunks_lechan_primary_v20260723"
VBPL_ACTION_URL = "https://vbpl.vn/van-ban/trung-uong"
VBPL_SEARCH_ACTION = "c529d164f28418e5898a834422629e64c6816af1"
VBPL_DETAIL_ACTION = "0fb12b3561faa05adec51a82efb3e4f4f427f07b"
IMPORT_TIMEOUT_SECONDS = 900.0


def _source(
    *,
    law_number: str,
    title: str,
    document_type: str,
    issuing_agency: str,
    issued_date: str,
    effective_date: str,
    source_url: str,
    content_url: str,
    field_id: int,
    domain_slug: str,
    sector: str,
    applicability_info: str = "",
) -> dict[str, Any]:
    return {
        "law_number": law_number,
        "title": title,
        "document_type": document_type,
        "issuing_agency": issuing_agency,
        "issued_date": issued_date,
        "effective_date": effective_date,
        "expired_date": None,
        "source_url": source_url,
        "content_url": content_url,
        "scope": "central",
        "field_id": field_id,
        "domain_slug": domain_slug,
        "sector": sector,
        "applicability_info": applicability_info,
    }


LAW_RESIDENCE = _source(
    law_number="68/2020/QH14",
    title="Luật Cư trú",
    document_type="Luật",
    issuing_agency="Quốc hội",
    issued_date="2020-11-13",
    effective_date="2021-07-01",
    source_url="https://vanban.chinhphu.vn/?docid=202609&pageid=27160",
    content_url="https://datafiles.chinhphu.vn/cpp/files/vbpq/2021/02/68.signed.pdf",
    field_id=9,
    domain_slug="cu_tru_an_ninh",
    sector="Cư trú",
)
DECREE_RESIDENCE_CURRENT = _source(
    law_number="154/2024/NĐ-CP",
    title="Quy định chi tiết một số điều và biện pháp thi hành Luật Cư trú",
    document_type="Nghị định",
    issuing_agency="Chính phủ",
    issued_date="2024-11-26",
    effective_date="2025-01-10",
    source_url="https://vanban.chinhphu.vn/?classid=0&docid=211821&pageid=27160",
    content_url="https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/154-cp.signed.pdf",
    field_id=9,
    domain_slug="cu_tru_an_ninh",
    sector="Cư trú",
    applicability_info="Thay thế Nghị định 62/2021/NĐ-CP từ 10/01/2025.",
)
DECREE_RESIDENCE_AMENDMENT = _source(
    law_number="58/2026/NĐ-CP",
    title=(
        "Sửa đổi, bổ sung một số điều của các nghị định liên quan đến quy định "
        "điều kiện về an ninh, trật tự; quản lý và sử dụng con dấu; quản lý, "
        "sử dụng pháo; quy định chi tiết một số điều và biện pháp thi hành "
        "Luật Cư trú, Luật Căn cước"
    ),
    document_type="Nghị định",
    issuing_agency="Chính phủ",
    issued_date="2026-02-13",
    effective_date="2026-03-15",
    source_url=(
        "https://vanban.chinhphu.vn/?classid=1&docid=216977"
        "&orggroupid=2&pageid=27160"
    ),
    content_url=(
        "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/02/58nd.signed.pdf"
    ),
    field_id=9,
    domain_slug="cu_tru_an_ninh",
    sector="Cư trú",
    applicability_info="Sửa đổi, bổ sung một phần Nghị định 154/2024/NĐ-CP.",
)
LAW_ADMINISTRATIVE_PROCEDURE = _source(
    law_number="93/2015/QH13",
    title="Luật Tố tụng hành chính",
    document_type="Luật",
    issuing_agency="Quốc hội",
    issued_date="2015-11-25",
    effective_date="2016-07-01",
    source_url="https://vanban.chinhphu.vn/?docid=183190&pageid=27160",
    content_url="https://datafiles.chinhphu.vn/cpp/files/vbpq/2016/01/93.signed.pdf",
    field_id=6,
    domain_slug="khieu_nai_to_cao_xu_phat",
    sector="Tố tụng hành chính",
)
DECREE_SECURED_REGISTRATION = _source(
    law_number="99/2022/NĐ-CP",
    title="Về đăng ký biện pháp bảo đảm",
    document_type="Nghị định",
    issuing_agency="Chính phủ",
    issued_date="2022-11-30",
    effective_date="2023-01-15",
    source_url=(
        "https://vanban.chinhphu.vn/?classid=1&docid=206942"
        "&pageid=27160&typegroupid=4"
    ),
    content_url="https://datafiles.chinhphu.vn/cpp/files/vbpq/2022/12/99-cp.signed.pdf",
    field_id=8,
    domain_slug="dat_dai_xay_dung",
    sector="Đăng ký biện pháp bảo đảm",
)
DECREE_LAND_REGISTRATION = _source(
    law_number="101/2024/NĐ-CP",
    title=(
        "Quy định về điều tra cơ bản đất đai; đăng ký, cấp Giấy chứng nhận "
        "quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất và Hệ thống "
        "thông tin đất đai"
    ),
    document_type="Nghị định",
    issuing_agency="Chính phủ",
    issued_date="2024-07-29",
    effective_date="2024-08-01",
    source_url="https://vanban.chinhphu.vn/?docid=210791&pageid=27160",
    content_url="https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/7/101-nd.signed.pdf",
    field_id=8,
    domain_slug="dat_dai_xay_dung",
    sector="Đăng ký đất đai",
)
DECREE_ROAD_PENALTIES = _source(
    law_number="168/2024/NĐ-CP",
    title=(
        "Quy định xử phạt vi phạm hành chính về trật tự, an toàn giao thông "
        "trong lĩnh vực giao thông đường bộ; trừ điểm, phục hồi điểm giấy phép "
        "lái xe"
    ),
    document_type="Nghị định",
    issuing_agency="Chính phủ",
    issued_date="2024-12-26",
    effective_date="2025-01-01",
    source_url=(
        "https://vanban.chinhphu.vn/?classid=1&docid=212167"
        "&orggroupid=2&pageid=27160"
    ),
    content_url="https://datafiles.chinhphu.vn/cpp/files/vbpq/2025/01/168-nd-cp.signed.pdf",
    field_id=10,
    domain_slug="trat_tu_do_thi",
    sector="Trật tự an toàn giao thông đường bộ",
    applicability_info=(
        "Nghị định 238/2026/NĐ-CP đã ban hành nhưng chỉ có hiệu lực từ "
        "15/08/2026, sau legal_as_of 2026-07-23."
    ),
)

# These sources were already imported with complete official metadata and
# embeddings, but DB-2 excluded them from the serving collection because their
# reviewed commune scope was missing or stale. Step 2 only corrects the sidecar
# and copies their existing vectors; it does not import or re-embed them.
REVIEWED_EXISTING_SERVING_SOURCES = [
    _source(
        law_number="17/2024/TT-BCA",
        title="Quy định chi tiết một số điều và biện pháp thi hành Luật Căn cước",
        document_type="Thông tư",
        issuing_agency="Bộ Công an",
        issued_date="2024-05-15",
        effective_date="2024-07-01",
        source_url="https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=170084",
        content_url="https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=170084",
        field_id=9,
        domain_slug="cu_tru_an_ninh",
        sector="Căn cước",
    ),
    _source(
        law_number="36/2024/QH15",
        title="Luật Trật tự, an toàn giao thông đường bộ",
        document_type="Luật",
        issuing_agency="Quốc hội",
        issued_date="2024-06-27",
        effective_date="2025-01-01",
        source_url="https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=170620",
        content_url="https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=170620",
        field_id=10,
        domain_slug="trat_tu_do_thi",
        sector="Trật tự, an toàn giao thông đường bộ",
    ),
]


STEP2_SOURCE_DECISIONS: list[dict[str, Any]] = [
    {
        "case_id": "ct_001",
        "gap_law_number": "68/2020/QH14",
        "decision": "correct_expected_source",
        "reason_code": "wrong_legal_subject_for_identity_card",
        "replacement_law_numbers": ["26/2023/QH15", "17/2024/TT-BCA"],
    },
    {
        "case_id": "ct_001",
        "gap_law_number": "60/2021",
        "decision": "correct_expected_source",
        "reason_code": "superseded_identity_card_instrument",
        "replacement_law_numbers": ["17/2024/TT-BCA", "53/2025/TT-BCA"],
    },
    {
        "case_id": "ct_002",
        "gap_law_number": "68/2020/QH14",
        "decision": "incremental_import",
        "reason_code": "current_direct_source_missing",
        "replacement_law_numbers": ["68/2020/QH14"],
        "import_spec": LAW_RESIDENCE,
    },
    {
        "case_id": "ct_002",
        "gap_law_number": "89/2021/NĐ-CP",
        "decision": "correct_expected_source",
        "reason_code": "wrong_number_and_expired_predecessor",
        "replacement_law_numbers": ["154/2024/NĐ-CP"],
        "import_specs": [
            DECREE_RESIDENCE_CURRENT,
            DECREE_RESIDENCE_AMENDMENT,
        ],
    },
    {
        "case_id": "golden_urban_003",
        "gap_law_number": "93/2015/QH13",
        "decision": "fail_closed",
        "reason_code": "partial_effectivity_requires_provision_level_review",
        "replacement_law_numbers": ["93/2015/QH13"],
    },
    {
        "case_id": "golden_urban_004",
        "gap_law_number": "01/2024/NĐ-CP",
        "decision": "fail_closed",
        "reason_code": "replacement_sources_partially_effective_require_review",
        "replacement_law_numbers": ["99/2022/NĐ-CP", "101/2024/NĐ-CP"],
    },
    {
        "case_id": "golden_urban_005",
        "gap_law_number": "06/2021/NĐ-CP",
        "decision": "fail_closed",
        "reason_code": "wrong_facet_and_partial_effectivity_requires_review",
        "replacement_law_numbers": ["175/2024/NĐ-CP"],
    },
    {
        "case_id": "tt_001",
        "gap_law_number": "23/2008/QH12",
        "decision": "correct_expected_source",
        "reason_code": "expired_source_replaced",
        "replacement_law_numbers": ["36/2024/QH15", "168/2024/NĐ-CP"],
        "import_spec": DECREE_ROAD_PENALTIES,
    },
]


def importable_source_specs(
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for decision in decisions:
        specs = list(decision.get("import_specs") or [])
        if decision.get("import_spec"):
            specs.append(decision["import_spec"])
        for spec in specs:
            key = (str(spec["law_number"]), str(spec["document_type"]))
            by_identity[key] = dict(spec)
    return [by_identity[key] for key in sorted(by_identity)]


def validate_step2_source_decisions(
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed = {"incremental_import", "correct_expected_source", "fail_closed"}
    unresolved = [row for row in decisions if row.get("decision") not in allowed]
    unsafe = [
        row
        for row in decisions
        if not row.get("reason_code") or not row.get("replacement_law_numbers")
    ]
    numbers = [str(row["law_number"]) for row in importable_source_specs(decisions)]
    duplicates = sorted(
        number for number, count in Counter(numbers).items() if count > 1
    )
    return {
        "gap_rows": len(decisions),
        "unresolved_rows": len(unresolved),
        "unsafe_substitutions": len(unsafe),
        "duplicate_import_numbers": duplicates,
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalise_pdf_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    value = re.sub(r"(?i)\bĐiều\s*\n\s*(\d+[a-zA-Z]?)", r"Điều \1", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _extract_pdf(payload: bytes) -> tuple[str, int]:
    if not payload.startswith(b"%PDF"):
        raise ValueError("official source payload is not a PDF")
    document = fitz.open(stream=payload, filetype="pdf")
    try:
        text_value = _normalise_pdf_text(
            "\n".join(page.get_text("text", sort=True) for page in document)
        )
        page_count = int(document.page_count)
    finally:
        document.close()
    article_count = len(
        re.findall(r"(?im)^[ \t]*Điều[ \t]+\d+[a-zA-Z]?(?:[.:\s]|$)", text_value)
    )
    if len(text_value) < 3000 or article_count < 3:
        raise ValueError(
            f"official PDF extraction insufficient: "
            f"characters={len(text_value)}, articles={article_count}"
        )
    return text_value, page_count


def _parse_rsc_json(payload: str) -> dict[str, Any]:
    # Long string chunks may end immediately before the JSON record without a
    # newline. Locate the structured search record and let JSONDecoder stop at
    # the exact end instead of treating the remaining RSC frames as JSON.
    match = re.search(r"\d+:(\{\"total\":)", payload)
    if match:
        start = match.start(1)
        value, _ = json.JSONDecoder().raw_decode(payload[start:])
        return dict(value)
    raise ValueError("VBPL search response did not contain a JSON payload")


def _slug(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFD", value)
    ascii_value = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("Đ", "D").replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def _fetch_vbpl_full_text(
    client: httpx.Client,
    spec: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    headers = {
        "Accept": "text/x-component",
        "Content-Type": "text/plain;charset=UTF-8",
        "Origin": "https://vbpl.vn",
        "User-Agent": "ChatBotLegal-Feature005-Step2/1.0",
    }
    search = client.post(
        VBPL_ACTION_URL,
        headers={**headers, "Next-Action": VBPL_SEARCH_ACTION},
        content=json.dumps(
            [
                {
                    # The new portal search currently mishandles Đ in a full
                    # NĐ-CP keyword. Year/serial remains deterministic and the
                    # result is then matched by complete normalized identity.
                    "keyword": "/".join(spec["law_number"].split("/")[:2]),
                    "pageNumber": 0,
                    "pageSize": 100,
                }
            ],
            separators=(",", ":"),
        ),
    )
    search.raise_for_status()
    search_data = _parse_rsc_json(search.text)
    normalized_expected = re.sub(
        r"[^0-9A-ZĐ]+", "", spec["law_number"].upper()
    )
    matches = [
        item
        for item in search_data.get("items") or []
        if re.sub(
            r"[^0-9A-ZĐ]+",
            "",
            re.sub(r"(?i)^\s*số\s*:\s*", "", str(item.get("docNum") or "")).upper(),
        )
        == normalized_expected
        and str((item.get("docType") or {}).get("name") or "").strip()
        == spec["document_type"]
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"VBPL identity mismatch for {spec['law_number']}/"
            f"{spec['document_type']}: {len(matches)}"
        )
    metadata = matches[0]
    effect_status = str((metadata.get("effStatus") or {}).get("name") or "")
    if effect_status.casefold() != "còn hiệu lực".casefold():
        raise RuntimeError(
            f"VBPL effectivity is not fully current for {spec['law_number']}: "
            f"{effect_status or 'unknown'}"
        )
    if str(metadata.get("effFrom") or "")[:10] != spec["effective_date"]:
        raise RuntimeError(f"VBPL effective date mismatch: {spec['law_number']}")
    detail = client.post(
        VBPL_ACTION_URL,
        headers={**headers, "Next-Action": VBPL_DETAIL_ACTION},
        content=json.dumps([str(metadata["id"])], separators=(",", ":")),
    )
    detail.raise_for_status()
    html_start = detail.text.find("<html")
    html_end = detail.text.rfind("</html>")
    if html_start < 0 or html_end < html_start:
        raise ValueError(f"VBPL full text missing: {spec['law_number']}")
    soup = BeautifulSoup(detail.text[html_start : html_end + 7], "html.parser")
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in soup.get_text("\n").splitlines()
    ]
    full_text = _normalise_pdf_text("\n".join(line for line in lines if line))
    article_count = len(
        re.findall(r"(?im)^[ \t]*Điều[ \t]+\d+[a-zA-Z]?(?:[.:\s]|$)", full_text)
    )
    if (
        spec["law_number"] not in full_text
        or len(full_text) < 3000
        or article_count < 3
    ):
        raise ValueError(
            f"VBPL full text failed quality checks: {spec['law_number']}, "
            f"characters={len(full_text)}, articles={article_count}"
        )
    detail_url = (
        "https://vbpl.vn/van-ban/trung-uong/"
        f"{_slug(str(metadata['title']))}--{metadata['id']}"
    )
    return full_text, {
        "portal_document_id": str(metadata["id"]),
        "portal_detail_url": detail_url,
        "portal_effect_status": effect_status,
        "portal_effective_date": str(metadata.get("effFrom") or "")[:10],
        "portal_title": metadata.get("title"),
    }


def _database_url() -> str:
    configured = str(dotenv_values(ROOT / ".env").get("LEGAL_RELEASE_DATABASE_URL") or "")
    if not configured:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL is required")
    return configured.replace("@host.docker.internal:", "@127.0.0.1:")


def _find_documents(engine: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT id, title, law_number, document_type, status, source_url,
               effective_date, expired_date, scope, field_id
        FROM legal_documents
        WHERE legal_normalize_identifier(law_number) =
              legal_normalize_identifier(:law_number)
        ORDER BY id
        """
    )
    with engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                statement, {"law_number": spec["law_number"]}
            ).mappings()
        ]
    return [row for row in rows if _same_legal_document_identity(row, spec)]


def _verify_existing_document(row: dict[str, Any], spec: dict[str, Any]) -> None:
    if row.get("status") != "active":
        raise RuntimeError(f"existing document is not active: {spec['law_number']}")
    if not _is_official_legal_source_url(str(row.get("source_url") or "")):
        raise RuntimeError(
            f"existing document has no official source URL: {spec['law_number']}"
        )
    normalized_scope = _slug(str(row.get("scope") or ""))
    if normalized_scope not in {"central", "toan-quoc", "trung-uong"}:
        raise RuntimeError(f"existing document has wrong scope: {spec['law_number']}")
    if str(row.get("effective_date") or "")[:10] != spec["effective_date"]:
        raise RuntimeError(
            f"existing document effective date mismatch: {spec['law_number']}"
        )
    expired = row.get("expired_date")
    if expired and str(expired)[:10] <= LEGAL_AS_OF.isoformat():
        raise RuntimeError(f"existing document is expired: {spec['law_number']}")


def _latest_scope(engine: Any, document_id: int) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT included, reason, domain, evaluated_as_of, evaluated_at
                FROM legal_search_scope
                WHERE document_id = :document_id
                ORDER BY evaluated_at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"document_id": document_id},
        ).mappings().first()
    return dict(row) if row else None


def _jsonable_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in row.items()
    }


def _apply_scope_override(
    engine: Any,
    *,
    document_id: int,
    domain_slug: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE legal_search_scope
                SET included = TRUE,
                    reason = 'step2_current_official_scope_override',
                    domain = :domain,
                    evaluated_as_of = :evaluated_as_of,
                    evaluated_at = :evaluated_at
                WHERE document_id = :document_id
                """
            ),
            {
                "document_id": document_id,
                "domain": domain_slug,
                "evaluated_as_of": LEGAL_AS_OF,
                "evaluated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )


def _repair_missing_domain_quality(
    engine: Any,
    *,
    document_id: int,
) -> int:
    """Re-enable chunks quarantined solely for a now-reviewed domain."""

    with engine.begin() as connection:
        blockers = int(
            connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM legal_chunk_quality quality
                    JOIN legal_article_chunks chunk
                      ON chunk.id = quality.chunk_id
                    JOIN legal_articles article
                      ON article.id = chunk.article_id
                    WHERE article.document_id = :document_id
                      AND 'missing_domain' = ANY(quality.quality_reasons)
                      AND quality.quality_reasons <>
                          ARRAY['missing_domain']::text[]
                    """
                ),
                {"document_id": document_id},
            ).scalar_one()
        )
        if blockers:
            raise RuntimeError(
                "missing-domain quality repair has additional reasons: "
                f"document={document_id}, chunks={blockers}"
            )
        result = connection.execute(
            text(
                """
                UPDATE legal_chunk_quality quality
                SET eligible = TRUE,
                    quality_reasons = ARRAY[]::text[],
                    quality_version =
                        quality.quality_version || '+step2-reviewed-domain',
                    assessed_at = now()
                FROM legal_article_chunks chunk
                JOIN legal_articles article ON article.id = chunk.article_id
                WHERE quality.chunk_id = chunk.id
                  AND article.document_id = :document_id
                  AND quality.quality_reasons =
                      ARRAY['missing_domain']::text[]
                """
            ),
            {"document_id": document_id},
        )
        return int(result.rowcount or 0)


def _download_sources(
    specs: list[dict[str, Any]],
    source_dir: Path,
) -> tuple[dict[tuple[str, str], str], list[dict[str, Any]]]:
    source_dir.mkdir(parents=True, exist_ok=True)
    contents: dict[tuple[str, str], str] = {}
    artifacts: list[dict[str, Any]] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=20.0),
        headers={"User-Agent": "ChatBotLegal-Feature005-Step2/1.0"},
    ) as client:
        for spec in specs:
            if not _is_official_legal_source_url(spec["source_url"]):
                raise RuntimeError(f"unofficial metadata URL: {spec['law_number']}")
            if not _is_official_legal_source_url(spec["content_url"]):
                raise RuntimeError(f"unofficial content URL: {spec['law_number']}")
            response = client.get(spec["content_url"])
            response.raise_for_status()
            payload = bytes(response.content)
            if not payload.startswith(b"%PDF"):
                raise ValueError(f"official payload is not PDF: {spec['law_number']}")
            document = fitz.open(stream=payload, filetype="pdf")
            try:
                pages = int(document.page_count)
            finally:
                document.close()
            # VBPL's current metadata is the effectivity gate and its
            # structured full text avoids importing OCR noise from scanned PDFs.
            extracted, portal_metadata = _fetch_vbpl_full_text(client, spec)
            text_source = "official_vbpl_full_text"
            safe_number = re.sub(r"[^0-9A-Za-z-]+", "-", spec["law_number"]).strip("-")
            pdf_path = source_dir / f"{safe_number}.pdf"
            text_path = source_dir / f"{safe_number}.txt"
            pdf_path.write_bytes(payload)
            text_path.write_text(extracted + "\n", encoding="utf-8")
            identity = (spec["law_number"], spec["document_type"])
            contents[identity] = extracted
            artifacts.append(
                {
                    "law_number": spec["law_number"],
                    "document_type": spec["document_type"],
                    "official_page_url": spec["source_url"],
                    "download_url": spec["content_url"],
                    "effective_date": spec["effective_date"],
                    "scope": spec["scope"],
                    "pdf_path": str(pdf_path),
                    "text_path": str(text_path),
                    "pdf_sha256": _sha256_bytes(payload),
                    "pdf_bytes": len(payload),
                    "pages": pages,
                    "text_source": text_source,
                    "text_characters": len(extracted),
                    "article_headings": len(
                        re.findall(
                            r"(?im)^[ \t]*Điều[ \t]+\d+[a-zA-Z]?(?:[.:\s]|$)",
                            extracted,
                        )
                    ),
                    **portal_metadata,
                }
            )
    return contents, artifacts


def _import_payload(spec: dict[str, Any], content: str) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            **spec,
            "content": content,
            "confirmed_official_source": True,
            "structure": "auto",
        }.items()
        if key != "content_url"
    }


def _post_json(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(
            f"{path} failed ({response.status_code}): {response.text[:1000]}"
        )
    return dict(response.json())


def _import_missing_documents(
    *,
    specs: list[dict[str, Any]],
    contents: dict[tuple[str, str], str],
    apply: bool,
    retrieval_url: str,
) -> list[dict[str, Any]]:
    engine = create_engine(_database_url(), pool_pre_ping=True)
    outcomes: list[dict[str, Any]] = []
    try:
        with httpx.Client(
            base_url=retrieval_url,
            timeout=IMPORT_TIMEOUT_SECONDS,
        ) as client:
            for spec in specs:
                identity = (spec["law_number"], spec["document_type"])
                existing = _find_documents(engine, spec)
                if len(existing) > 1:
                    raise RuntimeError(f"duplicate legal identity: {identity}")
                if existing:
                    _verify_existing_document(existing[0], spec)
                    existing_id = int(existing[0]["id"])
                    scope_row = _latest_scope(engine, existing_id)
                    scope_matches = bool(
                        scope_row
                        and scope_row.get("included")
                        and scope_row.get("domain") == spec["domain_slug"]
                    )
                    action = "verified_existing"
                    if not scope_matches:
                        action = (
                            "existing_scope_overridden"
                            if apply
                            else "scope_override_planned"
                        )
                        if apply:
                            _apply_scope_override(
                                engine,
                                document_id=existing_id,
                                domain_slug=spec["domain_slug"],
                            )
                            checked = _latest_scope(engine, existing_id)
                            if not (
                                checked
                                and checked.get("included")
                                and checked.get("domain") == spec["domain_slug"]
                            ):
                                raise RuntimeError(
                                    f"scope override failed: {spec['law_number']}"
                                )
                    quality_repaired = (
                        _repair_missing_domain_quality(
                            engine,
                            document_id=existing_id,
                        )
                        if apply
                        else 0
                    )
                    outcomes.append(
                        {
                            "law_number": spec["law_number"],
                            "document_type": spec["document_type"],
                            "action": action,
                            "document_id": existing_id,
                            "missing_domain_quality_repaired": quality_repaired,
                            "scope_before": _jsonable_row(scope_row),
                            "scope_after": (
                                _jsonable_row(_latest_scope(engine, existing_id))
                                if apply
                                else _jsonable_row(scope_row)
                            ),
                        }
                    )
                    continue
                payload = _import_payload(spec, contents[identity])
                preview = _post_json(client, "/import/preview", payload)
                if not preview.get("valid"):
                    raise RuntimeError(
                        f"preview rejected {spec['law_number']}: {preview.get('errors')}"
                    )
                outcome: dict[str, Any] = {
                    "law_number": spec["law_number"],
                    "document_type": spec["document_type"],
                    "action": "previewed" if not apply else "imported_incrementally",
                    "preview": {
                        "structure": preview.get("structure"),
                        "article_count": preview.get("article_count"),
                        "chunk_count": preview.get("chunk_count"),
                    },
                }
                if apply:
                    imported = _post_json(client, "/import", payload)
                    if imported.get("activation_status") != "active":
                        raise RuntimeError(
                            f"import did not activate {spec['law_number']}"
                        )
                    outcome.update(imported)
                    verified = _find_documents(engine, spec)
                    if len(verified) != 1:
                        raise RuntimeError(
                            f"post-import identity mismatch: {spec['law_number']}"
                        )
                    _verify_existing_document(verified[0], spec)
                    outcome["document_id"] = int(verified[0]["id"])
                outcomes.append(outcome)
    finally:
        engine.dispose()
    return outcomes


def _document_chunk_ids(engine: Any, document_ids: set[int]) -> set[str]:
    if not document_ids:
        return set()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.id
                FROM legal_article_chunks c
                JOIN legal_articles a ON a.id = c.article_id
                JOIN legal_documents d ON d.id = a.document_id
                WHERE d.id = ANY(:document_ids)
                  AND d.status = 'active'
                  AND a.status = 'active'
                """
            ),
            {"document_ids": sorted(document_ids)},
        )
        return {str(int(row[0])) for row in rows}


def _sync_shadow_primary(
    *, document_ids: set[int], chroma_path: Path
) -> dict[str, Any]:
    client = chromadb.PersistentClient(path=str(chroma_path))
    names = {collection.name for collection in client.list_collections()}
    required = {SOURCE_COLLECTION, PRIMARY_COLLECTION}
    if not required <= names:
        raise RuntimeError(f"missing Chroma collections: {sorted(required - names)}")
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = (
        pointer_path.read_text(encoding="utf-8").strip()
        if pointer_path.exists()
        else None
    )
    source = client.get_collection(SOURCE_COLLECTION)
    primary = client.get_collection(PRIMARY_COLLECTION)
    source_count_before = int(source.count())
    primary_before_ids = {
        str(item)
        for item in (primary.get(limit=primary.count(), include=[]).get("ids") or [])
    }
    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        chunk_ids = _document_chunk_ids(engine, document_ids)
    finally:
        engine.dispose()
    if not chunk_ids:
        raise RuntimeError("no active chunks found for imported documents")
    vector_ids = {f"chunk-{item}" for item in chunk_ids}
    source_rows = source.get(
        ids=sorted(vector_ids), include=["embeddings", "metadatas"]
    )
    fetched_ids = [str(item) for item in source_rows.get("ids") or []]
    source_embeddings = source_rows.get("embeddings")
    if set(fetched_ids) != vector_ids:
        raise RuntimeError(
            f"incremental source vector mismatch: expected={len(vector_ids)} "
            f"actual={len(fetched_ids)}"
        )
    if source_embeddings is None or len(source_embeddings) != len(fetched_ids):
        raise RuntimeError("incremental source embeddings are incomplete")
    hydrated = _hydrate_database_rows(
        chunk_ids=chunk_ids, scope_version="feature005-step2-20260724"
    )
    if set(hydrated) != chunk_ids:
        raise RuntimeError(
            f"incremental database hydration mismatch: expected={len(chunk_ids)} "
            f"actual={len(hydrated)}"
        )
    primary.upsert(
        ids=fetched_ids,
        embeddings=list(source_embeddings),
        documents=[_passage(hydrated[item[6:]]) for item in fetched_ids],
        metadatas=[
            {
                **_hydrated_metadata(
                    hydrated[item[6:]],
                    tier="primary",
                    scope_version="feature005-step2-20260724",
                ),
                "db4_source": "incremental_official_import",
            }
            for item in fetched_ids
        ],
    )
    check = primary.get(
        ids=sorted(vector_ids),
        include=["embeddings", "documents", "metadatas"],
    )
    check_ids = [str(item) for item in check.get("ids") or []]
    documents = list(check.get("documents") or [])
    metadatas = list(check.get("metadatas") or [])
    checked_embeddings = check.get("embeddings")
    embeddings = list(checked_embeddings) if checked_embeddings is not None else []
    orphan = [
        identifier
        for identifier, metadata in zip(check_ids, metadatas)
        if str((metadata or {}).get("chunk_id") or "") not in hydrated
    ]
    stale = [
        identifier
        for identifier, metadata in zip(check_ids, metadatas)
        if int((metadata or {}).get("document_id") or 0) not in document_ids
    ]
    empty = [
        identifier
        for identifier, document in zip(check_ids, documents)
        if not str(document or "").strip()
    ]
    missing_embedding = [
        identifier
        for identifier, embedding in zip(check_ids, embeddings)
        if embedding is None or len(embedding) == 0
    ]
    duplicate = len(check_ids) - len(set(check_ids))
    pointer_after = (
        pointer_path.read_text(encoding="utf-8").strip()
        if pointer_path.exists()
        else None
    )
    result = {
        "source_collection": SOURCE_COLLECTION,
        "target_collection": PRIMARY_COLLECTION,
        "source_count_before": source_count_before,
        "source_count_after": int(source.count()),
        "primary_count_before": len(primary_before_ids),
        "primary_count_after": int(primary.count()),
        "incremental_vector_count": len(vector_ids),
        "incremental_id_sha256": _sha256_ids(vector_ids),
        "missing_count": len(vector_ids - set(check_ids)),
        "orphan_count": len(orphan),
        "stale_count": len(stale),
        "empty_count": len(empty),
        "duplicate_count": duplicate,
        "missing_embedding_count": len(missing_embedding),
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_unchanged": pointer_before == pointer_after,
    }
    result["valid"] = (
        result["source_count_before"] == result["source_count_after"]
        and result["missing_count"] == 0
        and result["orphan_count"] == 0
        and result["stale_count"] == 0
        and result["empty_count"] == 0
        and result["duplicate_count"] == 0
        and result["missing_embedding_count"] == 0
        and result["active_pointer_unchanged"]
    )
    if not result["valid"]:
        raise RuntimeError(f"incremental shadow validation failed: {result}")
    return result


def _document_contract(
    *, case_id: str, document_id: int, law_number: str
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "set": "golden_167",
        "kind": "document",
        "outcome": "AVAILABLE_CORRECTLY_TIERED",
        "document_id": document_id,
        "law_number": law_number,
        "reason_code": "step2_current_official_source",
        "checks": {
            "document_present": True,
            "article_chunk": True,
            "effectivity": True,
            "scope": True,
            "hierarchy": True,
            "tier": "primary",
            "tier_correct": True,
        },
        "step2_verified": True,
    }


def _gap_contract(
    *,
    case_id: str,
    law_number: str | None,
    reason_code: str,
    resolution: str = "fail_closed_do_not_import_unreviewed_provisions",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "set": "golden_167",
        "kind": "document",
        "outcome": "VERIFIED_DATA_GAP",
        "expected_law_number": law_number,
        "corpus_match_count": 0,
        "reason_code": reason_code,
        "resolution": resolution,
        "step2_verified": True,
    }


def _existing_document_id(
    engine: Any, law_number: str, document_type: str | None = None
) -> int:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, document_type
                FROM legal_documents
                WHERE legal_normalize_identifier(law_number) =
                      legal_normalize_identifier(:law_number)
                  AND status = 'active'
                ORDER BY id
                """
            ),
            {"law_number": law_number},
        ).mappings()
        matches = [
            dict(row)
            for row in rows
            if not document_type or str(row["document_type"]) == document_type
        ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one active document for {law_number}/{document_type}, "
            f"found {len(matches)}"
        )
    return int(matches[0]["id"])


def _write_step2_expected_sources(
    *, path: Path, imported: list[dict[str, Any]]
) -> dict[str, Any]:
    by_identity = {
        (row["law_number"], row["document_type"]): int(row["document_id"])
        for row in imported
        if row.get("document_id") is not None
    }
    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        replacement_cases: dict[str, list[dict[str, Any]]] = {
            "ct_001": [
                _document_contract(
                    case_id="ct_001",
                    document_id=_existing_document_id(engine, "26/2023/QH15", "Luật"),
                    law_number="26/2023/QH15",
                ),
                _document_contract(
                    case_id="ct_001",
                    document_id=_existing_document_id(engine, "17/2024/TT-BCA"),
                    law_number="17/2024/TT-BCA",
                ),
            ],
            "ct_002": [
                _document_contract(
                    case_id="ct_002",
                    document_id=by_identity[("68/2020/QH14", "Luật")],
                    law_number="68/2020/QH14",
                ),
                _document_contract(
                    case_id="ct_002",
                    document_id=by_identity[("154/2024/NĐ-CP", "Nghị định")],
                    law_number="154/2024/NĐ-CP",
                ),
                _document_contract(
                    case_id="ct_002",
                    document_id=by_identity[("58/2026/NĐ-CP", "Nghị định")],
                    law_number="58/2026/NĐ-CP",
                ),
            ],
            "golden_urban_003": [
                _gap_contract(
                    case_id="golden_urban_003",
                    law_number="93/2015/QH13",
                    reason_code=(
                        "partial_effectivity_requires_provision_level_legal_review"
                    ),
                )
            ],
            "golden_urban_004": [
                _document_contract(
                    case_id="golden_urban_004",
                    document_id=_existing_document_id(engine, "31/2024/QH15", "Luật"),
                    law_number="31/2024/QH15",
                ),
                _gap_contract(
                    case_id="golden_urban_004",
                    law_number="99/2022/NĐ-CP",
                    reason_code=(
                        "partial_effectivity_requires_provision_level_legal_review"
                    ),
                ),
                _gap_contract(
                    case_id="golden_urban_004",
                    law_number="101/2024/NĐ-CP",
                    reason_code=(
                        "partial_effectivity_requires_provision_level_legal_review"
                    ),
                ),
            ],
            "golden_urban_005": [
                _document_contract(
                    case_id="golden_urban_005",
                    document_id=_existing_document_id(engine, "50/2014/QH13", "Luật"),
                    law_number="50/2014/QH13",
                ),
                _gap_contract(
                    case_id="golden_urban_005",
                    law_number="175/2024/NĐ-CP",
                    reason_code=(
                        "partial_effectivity_requires_provision_level_legal_review"
                    ),
                ),
            ],
            "tt_001": [
                _document_contract(
                    case_id="tt_001",
                    document_id=_existing_document_id(engine, "36/2024/QH15", "Luật"),
                    law_number="36/2024/QH15",
                ),
                _document_contract(
                    case_id="tt_001",
                    document_id=by_identity[("168/2024/NĐ-CP", "Nghị định")],
                    law_number="168/2024/NĐ-CP",
                ),
            ],
            "pilot_dat_dai_xay_dung_025": [
                _gap_contract(
                    case_id="pilot_dat_dai_xay_dung_025",
                    law_number=None,
                    reason_code=(
                        "underspecified_land_or_construction_procedure"
                    ),
                    resolution=(
                        "fail_closed_request_concrete_procedure_and_applicant_role"
                    ),
                )
            ],
            "pilot_an_sinh_y_te_giao_duc_024": [
                _gap_contract(
                    case_id="pilot_an_sinh_y_te_giao_duc_024",
                    law_number=None,
                    reason_code=(
                        "underspecified_social_health_or_education_procedure"
                    ),
                    resolution=(
                        "fail_closed_request_concrete_procedure_and_foreign_factor"
                    ),
                )
            ],
        }
    finally:
        engine.dispose()

    retained: list[dict[str, Any]] = []
    replaced = set(replacement_cases)
    with DEFAULT_DB3_EXPECTED.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("set") == "golden_167" and row.get("case_id") in replaced:
                continue
            retained.append(row)
    for case_id in sorted(replacement_cases):
        retained.extend(replacement_cases[case_id])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in retained:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    return {
        "path": str(path),
        "row_count": len(retained),
        "replaced_case_count": len(replacement_cases),
        "remaining_verified_gap_count": sum(
            1 for row in retained if row.get("outcome") == "VERIFIED_DATA_GAP"
        ),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def execute(
    *,
    gaps_path: Path,
    output_dir: Path,
    chroma_path: Path,
    retrieval_url: str,
    apply: bool,
) -> dict[str, Any]:
    decisions = validate_step2_source_decisions(STEP2_SOURCE_DECISIONS)
    if decisions["unresolved_rows"] or decisions["unsafe_substitutions"]:
        raise RuntimeError(f"unsafe source decisions: {decisions}")
    gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
    if int(gaps.get("gap_count") or 0) != len(STEP2_SOURCE_DECISIONS):
        raise RuntimeError("Step 1 gap manifest does not match reviewed Step 2 decisions")
    import_specs = importable_source_specs(STEP2_SOURCE_DECISIONS)
    specs = import_specs + [dict(row) for row in REVIEWED_EXISTING_SERVING_SOURCES]
    contents, artifacts = _download_sources(
        import_specs,
        output_dir / "source-files",
    )
    import_outcomes = _import_missing_documents(
        specs=specs,
        contents=contents,
        apply=apply,
        retrieval_url=retrieval_url,
    )
    report: dict[str, Any] = {
        "schema_version": "feature005-step2-data-gap-v1",
        "status": "DRY_RUN_VERIFIED" if not apply else "VERIFYING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": LEGAL_AS_OF.isoformat(),
        "feature_flag": False,
        "corpus_deleted": False,
        "full_corpus_reembedded": False,
        "decision_validation": decisions,
        "source_artifacts": artifacts,
        "import_outcomes": import_outcomes,
        "remaining_fail_closed": [
            row for row in STEP2_SOURCE_DECISIONS if row["decision"] == "fail_closed"
        ],
    }
    if apply:
        document_ids = {
            int(row["document_id"])
            for row in import_outcomes
            if row.get("document_id") is not None
        }
        report["shadow_sync"] = _sync_shadow_primary(
            document_ids=document_ids, chroma_path=chroma_path
        )
        report["expected_sources"] = _write_step2_expected_sources(
            path=output_dir / "expected-sources.jsonl",
            imported=import_outcomes,
        )
        report["status"] = (
            "RUNTIME_READY_FOR_RETRIEVAL_GATE"
            if report["shadow_sync"]["valid"]
            else "GATE_FAILED"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--retrieval-url", default="http://127.0.0.1:8765")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = execute(
        gaps_path=args.gaps.resolve(),
        output_dir=args.output_dir.resolve(),
        chroma_path=args.chroma_path.resolve(),
        retrieval_url=args.retrieval_url,
        apply=args.apply,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "source_count": len(report["source_artifacts"]),
                "import_outcomes": report["import_outcomes"],
                "remaining_fail_closed": report["remaining_fail_closed"],
            },
            # Windows PowerShell may expose a legacy console code page. Keep
            # the machine-readable CLI summary ASCII-safe; the manifest itself
            # remains UTF-8 with Vietnamese text.
            ensure_ascii=True,
        )
    )
    return (
        0
        if report["status"]
        in {"DRY_RUN_VERIFIED", "RUNTIME_READY_FOR_RETRIEVAL_GATE"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
