#!/usr/bin/env python
"""Download and prepare the four remaining priority form groups.

The command never approves, indexes, embeds, or promotes a candidate.  It
extracts exact form pages from official packages and writes only pending
records to the existing review queue when ``--apply`` is supplied.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import unicodedata

import httpx
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.priority_gap_candidates import (
    build_priority_gap_candidates,
    merge_priority_gap_candidates,
)
from api.official_http import build_verified_ssl_context
from scripts.crawl_canonical_forms import (
    is_allowed_official_url,
    validate_download,
)


CANDIDATE_PATH = (
    ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
)
WORK_DIR = (
    ROOT
    / "data"
    / "uploads"
    / "forms"
    / "official_candidates"
    / "verified_20260727"
)
SOURCE_DIR = WORK_DIR / "sources" / "priority_gap"
FORM_DIR = WORK_DIR / "forms" / "priority_gap"
REPORT_PATH = (
    ROOT / "reports" / "feature005" / "priority-gap-candidate-preparation-20260727.json"
)

LAND_SOURCE_PAGE = "https://vanban.chinhphu.vn/?docid=210791&pageid=27160"
LAND_DOWNLOAD_URL = (
    "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/7/101-nd.signed.pdf"
)
LAND_HAI_PHONG_SOURCE_PAGE = (
    "https://phulien.haiphong.gov.vn/linh-vuc-dat-dai/"
    "quyet-dinh-so-3266-qd-ubnd-ve-viec-cong-bo-danh-muc-thu-tuc-hanh-"
    "chinh-duoc-sua-doi-bo-sung-linh-775273"
)
LAND_HAI_PHONG_DOWNLOAD_URL = (
    "https://cdn.haiphong.gov.vn/gov-hpg/6846/tintuc/2025/8/"
    "noi-dung-linh-vuc-dat-dai-cap-xa638917399917625152.docx"
)
CONSTRUCTION_SOURCE_PAGE = (
    "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/6/32-vbhn-bxd.pdf"
)
CONSTRUCTION_DOWNLOAD_URL = (
    "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/6/32-vbhn-bxd-kem.pdf"
)
CIVIL_SOURCE_PAGE = (
    "https://sotp.haiphong.gov.vn/van-ban-chi-dao-dieu-hanh-68017/"
    "cong-bo-thu-tuc-hanh-chinh-linh-vuc-ho-tich-769866"
)
CIVIL_DOWNLOAD_URL = (
    "https://cdn.haiphong.gov.vn/gov-hpg/6804/tintuc/2025/11/"
    "tthc-linh-vucho-tich638990521771580708.pdf"
)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return " ".join(
        "".join(
            character
            for character in decomposed
            if unicodedata.category(character) != "Mn"
        ).split()
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, filename: str) -> tuple[Path, dict[str, object]]:
    if not is_allowed_official_url(url):
        raise RuntimeError("SOURCE_HOST_NOT_ALLOWED")
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    target = SOURCE_DIR / filename
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(90.0, connect=15.0),
        headers={"User-Agent": "ChatBotLegal-PriorityGap/1.0 (+candidate-only)"},
        verify=build_verified_ssl_context(),
    ) as client:
        response = client.get(url)
        response.raise_for_status()
    redirect_chain = [*[str(item.url) for item in response.history], str(response.url)]
    if any(not is_allowed_official_url(item) for item in redirect_chain):
        raise RuntimeError("REDIRECT_HOST_NOT_ALLOWED")
    valid, reason, _format = validate_download(
        response.content,
        response.headers.get("content-type"),
        str(response.url),
    )
    if not valid:
        raise RuntimeError(reason)
    target.write_bytes(response.content)
    return target, {
        "download_url": str(response.url),
        "redirect_chain": redirect_chain,
        "content_type": response.headers.get("content-type"),
        "size_bytes": len(response.content),
        "sha256": _sha256(target),
    }


def _extract_pdf_section(
    source: Path,
    target: Path,
    *,
    required_phrases: tuple[str, ...],
    next_marker: str,
    max_pages: int,
) -> tuple[Path, list[int]]:
    reader = PdfReader(str(source))
    folded_pages = [_fold(page.extract_text() or "") for page in reader.pages]
    required = tuple(_fold(item) for item in required_phrases)
    start = next(
        (
            index
            for index, text in enumerate(folded_pages)
            if all(item in text for item in required)
        ),
        None,
    )
    if start is None:
        raise RuntimeError(f"EXPECTED_FORM_NOT_FOUND:{target.name}")
    marker = _fold(next_marker)
    end = min(len(reader.pages), start + max_pages)
    for index in range(start + 1, end):
        if marker in folded_pages[index]:
            end = index
            break
    pages = list(range(start, end))
    if not pages:
        raise RuntimeError(f"EMPTY_FORM_SECTION:{target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for page_index in pages:
        writer.add_page(reader.pages[page_index])
    with target.open("wb") as stream:
        writer.write(stream)
    return target, pages


def prepare_assets() -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    land_source, land_provenance = _download(
        LAND_DOWNLOAD_URL,
        "nghi-dinh-101-2024-nd-cp.pdf",
    )
    construction_source, construction_provenance = _download(
        CONSTRUCTION_DOWNLOAD_URL,
        "32-vbhn-bxd-2026-phu-luc.pdf",
    )
    land_form = (
        ROOT
        / "data"
        / "uploads"
        / "forms"
        / "official_candidates"
        / "66de716894a3a5b5-Noi-dung-linh-vuc-dat-dai-cap-xa.docx.docx"
    )
    if not land_form.is_file() or _sha256(land_form) != (
        "66de716894a3a5b5e5855dc14ff2878b6e31bc6797f9295d1997265b94c2c83a"
    ):
        raise RuntimeError("VERIFIED_HAI_PHONG_LAND_PACKAGE_MISSING_OR_DRIFTED")
    land_pages: list[int] = []
    construction_form, construction_pages = _extract_pdf_section(
        construction_source,
        FORM_DIR / "mau-01-don-de-nghi-cap-giay-phep-xay-dung-2026.pdf",
        required_phrases=(
            "Mẫu số 01",
            "Đơn đề nghị cấp giấy phép xây dựng",
            "Cộng hòa xã hội chủ nghĩa Việt Nam",
        ),
        next_marker="Mẫu số 02",
        max_pages=8,
    )
    foreign_birth = WORK_DIR / "forms" / "to-khai-dang-ky-khai-sinh-2025.pdf"
    foreign_marriage = WORK_DIR / "forms" / "to-khai-dang-ky-ket-hon-2025.pdf"
    for existing in (foreign_birth, foreign_marriage):
        if not existing.is_file():
            raise RuntimeError(f"VERIFIED_CIVIL_FORM_MISSING:{existing.name}")

    assets = {
        "land": {
            "path": land_form,
            "sha256": _sha256(land_form),
            "source_page_url": LAND_HAI_PHONG_SOURCE_PAGE,
            "source_download_url": LAND_HAI_PHONG_DOWNLOAD_URL,
            "source_package_sha256": _sha256(land_form),
            "source_pages_zero_based": land_pages,
        },
        "foreign_birth": {
            "path": foreign_birth,
            "sha256": _sha256(foreign_birth),
            "source_page_url": CIVIL_SOURCE_PAGE,
            "source_download_url": CIVIL_DOWNLOAD_URL,
            "source_package_sha256": (
                "c70d676eb7cf4f113360d6054bb1f37f42ace19d5c24ade0eacb2f316a6c572b"
            ),
            "source_pages_zero_based": [11, 12],
        },
        "foreign_marriage": {
            "path": foreign_marriage,
            "sha256": _sha256(foreign_marriage),
            "source_page_url": CIVIL_SOURCE_PAGE,
            "source_download_url": CIVIL_DOWNLOAD_URL,
            "source_package_sha256": (
                "c70d676eb7cf4f113360d6054bb1f37f42ace19d5c24ade0eacb2f316a6c572b"
            ),
            "source_pages_zero_based": [121, 122],
        },
        "construction": {
            "path": construction_form,
            "sha256": _sha256(construction_form),
            "source_page_url": CONSTRUCTION_SOURCE_PAGE,
            "source_download_url": str(construction_provenance["download_url"]),
            "source_package_sha256": construction_provenance["sha256"],
            "source_pages_zero_based": construction_pages,
        },
    }
    return assets, {
        "land_legal_basis": land_provenance,
        "land_hai_phong_form_package": {
            "download_url": LAND_HAI_PHONG_DOWNLOAD_URL,
            "sha256": _sha256(land_form),
            "size_bytes": land_form.stat().st_size,
        },
        "construction": construction_provenance,
    }


def run(*, apply: bool) -> dict[str, object]:
    assets, packages = prepare_assets()
    prepared_at = datetime.now(timezone.utc).isoformat()
    candidates = build_priority_gap_candidates(
        assets,
        project_root=ROOT,
        prepared_at=prepared_at,
    )
    original = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    updated = merge_priority_gap_candidates(original, candidates)
    report = {
        "schema_version": "priority-gap-candidates-v1",
        "generated_at": prepared_at,
        "status": "READY_FOR_HUMAN_REVIEW",
        "applied": apply,
        "candidate_count": len(candidates),
        "auto_approved": 0,
        "runtime_promoted": 0,
        "corpus_modified": False,
        "embedding_run": False,
        "active_collection_changed": False,
        "packages": packages,
        "candidates": [
            {
                key: candidate.get(key)
                for key in (
                    "id",
                    "priority_group",
                    "procedure_id",
                    "official_procedure_code",
                    "form_code",
                    "requested_legacy_form_code",
                    "legacy_form_status",
                    "file_path",
                    "sha256",
                    "source_page_url",
                    "source_download_url",
                    "review_status",
                    "legal_review_status",
                    "runtime_eligible",
                    "mapping_reason_code",
                )
                if candidate.get(key) is not None
            }
            for candidate in candidates
        ],
    }
    if apply:
        CANDIDATE_PATH.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = run(apply=args.apply)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
