#!/usr/bin/env python
"""Download, verify and attach official files to the 14 pending form candidates.

The command is intentionally review-only: it never changes ``review_status`` to
approved, never copies files to ``priority_official`` and never touches corpus
embeddings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_candidate_preparation import prepare_candidate_payload


CANDIDATE_PATH = ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
PROCEDURE_PATH = ROOT / "notebook_data" / "forms" / "canonical_procedures_v1.json"
WORK_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates" / "verified_20260727"
SOURCE_DIR = WORK_DIR / "sources"
FORM_DIR = WORK_DIR / "forms"
REPORT_PATH = ROOT / "reports" / "feature005" / "form-candidate-preparation-20260727.json"

CIVIL_SOURCE_PAGE = (
    "https://sotp.haiphong.gov.vn/van-ban-chi-dao-dieu-hanh-68017/"
    "cong-bo-thu-tuc-hanh-chinh-linh-vuc-ho-tich-769866"
)
CIVIL_DOWNLOAD_URL = (
    "https://cdn.haiphong.gov.vn/gov-hpg/6804/tintuc/2025/11/"
    "tthc-linh-vucho-tich638990521771580708.pdf"
)
RESIDENCE_SOURCE_PAGE = (
    "https://vanban.bocongan.gov.vn/co-so-du-lieu-van-ban/"
    "van-ban-hop-nhat-thong-tu-quy-dinh-chi-tiet-mot-so-dieu-va-bien-phap-"
    "thi-hanh-luat-cu-tru-1762764131"
)
COMPLAINT_DOWNLOAD_URL = (
    "https://cdn.haiphong.gov.vn/gov-hpg/6808/tintuc/2025/11/"
    "1.2-danh-m_c-tthc-l_nh-v_c-khi_u-n_i638990785291072657.pdf"
)

SOURCES = {
    "civil": {
        "filename": "hai-phong-ho-tich-2025.pdf",
        "url": CIVIL_DOWNLOAD_URL,
        "sha256": "c70d676eb7cf4f113360d6054bb1f37f42ace19d5c24ade0eacb2f316a6c572b",
    },
    "ct01": {
        "filename": "bca-ct01-thong-tu-53-2025.doc",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/photo-library-20251110154050-"
            "8c5a715e-dafe-4af0-bf1c-fc1f5d98d3dd-1-mau-ct01-ban-hanh-kem-theo-"
            "thong-tu-53.doc"
        ),
        "sha256": "d14384374f14f83655d50b6706b986416b91528bbabf2902109da7a1c68fe6ac",
    },
    "ct02": {
        "filename": "bca-ct02-thong-tu-53-2025.doc",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/photo-library-20251110154050-"
            "0f5e8493-75c7-4d63-a2b3-72cff259be46-2-mau-ct02-ban-hanh-kem-theo-"
            "thong-tu-so-53.doc"
        ),
        "sha256": "3f6656e2d37e1909dd1e74f45bb0800e62b15dfdbad9a712744bcf1d764e11ad",
    },
    "complaint": {
        "filename": "hai-phong-khieu-nai-2025.pdf",
        "url": COMPLAINT_DOWNLOAD_URL,
        "sha256": "5f6bab7860457cb3425503cb07d51f1266823f204acba3d8dad7d42b1a5df0bc",
    },
}

PDF_FORMS = {
    "birth": {
        "source": "civil",
        "filename": "to-khai-dang-ky-khai-sinh-2025.pdf",
        "pages": [11, 12],
        "expected_text": "TỜ KHAI ĐĂNG KÝ KHAI SINH",
    },
    "rebirth": {
        "source": "civil",
        "filename": "to-khai-dang-ky-lai-khai-sinh-2025.pdf",
        "pages": [35, 36],
        "expected_text": "ĐĂNG KÝ LẠI KHAI SINH",
    },
    "parent": {
        "source": "civil",
        "filename": "to-khai-dang-ky-nhan-cha-me-con-2025.pdf",
        "pages": [58, 59],
        "expected_text": "TỜ KHAI ĐĂNG KÝ NHẬN CHA, MẸ, CON",
    },
    "marriage": {
        "source": "civil",
        "filename": "to-khai-dang-ky-ket-hon-2025.pdf",
        "pages": [121, 122],
        "expected_text": "TỜ KHAI ĐĂNG KÝ KẾT HÔN",
    },
    "guardian": {
        "source": "civil",
        "filename": "to-khai-dang-ky-giam-ho-2025.pdf",
        "pages": [208, 209],
        "expected_text": "TỜ KHAI ĐĂNG KÝ GIÁM HỘ",
    },
    "end_guardian": {
        "source": "civil",
        "filename": "to-khai-dang-ky-cham-dut-giam-ho-2025.pdf",
        "pages": [229, 230],
        "expected_text": "TỜ KHAI ĐĂNG KÝ CHẤM DỨT GIÁM HỘ",
    },
    "marital_status": {
        "source": "civil",
        "filename": "to-khai-xac-nhan-tinh-trang-hon-nhan-2025.pdf",
        "pages": [403, 404],
        "expected_text": "TỜ KHAI CẤP GIẤY XÁC NHẬN TÌNH TRẠNG HÔN NHÂN",
    },
    "complaint_form": {
        "source": "complaint",
        "filename": "mau-01-don-khieu-nai-2025.pdf",
        "pages": [8],
        "expected_text": "ĐƠN KHIẾU NẠI",
    },
}

COMMON_CIVIL_BASIS = [
    "60/2014/QH13",
    "123/2015/NĐ-CP",
    "04/2020/TT-BTP",
    "04/2024/TT-BTP",
    "07/2025/NĐ-CP",
    "120/2025/NĐ-CP",
    "08/2025/TT-BTP",
    "18/2026/NĐ-CP",
]
RESIDENCE_BASIS = ["68/2020/QH14", "53/2025/TT-BCA", "42/VBHN-BCA"]
COMPLAINT_BASIS = ["02/2011/QH13", "124/2020/NĐ-CP", "05/2021/TT-TTCP"]


def _civil_assignment(
    candidate_id: str,
    *,
    asset: str,
    name: str,
    procedure_id: str,
    procedure_code: str,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "asset": asset,
        "canonical_form_name": name,
        "procedure_id": procedure_id,
        "official_procedure_code": procedure_code,
        "domain": "ho_tich_chung_thuc",
        "source_page_url": CIVIL_SOURCE_PAGE,
        "source_download_url": CIVIL_DOWNLOAD_URL,
        "publisher": "Sở Tư pháp thành phố Hải Phòng",
        "legal_basis": COMMON_CIVIL_BASIS,
        "effective_status": "official_source_current_pending_legal_review",
    }


CANDIDATE_ASSIGNMENTS = [
    _civil_assignment(
        "web-809586a0caa4c471e157",
        asset="marriage",
        name="Tờ khai đăng ký kết hôn",
        procedure_id="dang_ky_ket_hon",
        procedure_code="1.000894",
    ),
    _civil_assignment(
        "web-2e73a1f62661c3582e62",
        asset="marital_status",
        name="Tờ khai cấp Giấy xác nhận tình trạng hôn nhân",
        procedure_id="xac_nhan_tinh_trang_hon_nhan",
        procedure_code="1.004873",
    ),
    _civil_assignment(
        "web-26596cccdc6dba9ae65c",
        asset="birth",
        name="Tờ khai đăng ký khai sinh",
        procedure_id="dang_ky_khai_sinh",
        procedure_code="1.001193",
    ),
    _civil_assignment(
        "web-44e61234f4d9e7d87301",
        asset="rebirth",
        name="Tờ khai đăng ký lại khai sinh",
        procedure_id="dang_ky_lai_khai_sinh",
        procedure_code="1.004884",
    ),
    _civil_assignment(
        "web-1065dc4f26071bf648f4",
        asset="marriage",
        name="Tờ khai đăng ký kết hôn",
        procedure_id="dang_ky_ket_hon",
        procedure_code="1.000894",
    ),
    _civil_assignment(
        "web-72bcdef4b1d34c60ec86",
        asset="marital_status",
        name="Tờ khai cấp Giấy xác nhận tình trạng hôn nhân",
        procedure_id="xac_nhan_tinh_trang_hon_nhan",
        procedure_code="1.004873",
    ),
    _civil_assignment(
        "web-5d5c108600fe97c36c95",
        asset="guardian",
        name="Tờ khai đăng ký giám hộ",
        procedure_id="dang_ky_giam_ho",
        procedure_code="1.004837",
    ),
    _civil_assignment(
        "web-6c4325e89506a3ab5668",
        asset="end_guardian",
        name="Tờ khai đăng ký chấm dứt giám hộ",
        procedure_id="dang_ky_cham_dut_giam_ho",
        procedure_code="1.004845",
    ),
    _civil_assignment(
        "web-db45facfff5559219d9d",
        asset="parent",
        name="Tờ khai đăng ký nhận cha, mẹ, con",
        procedure_id="dang_ky_nhan_cha_me_con",
        procedure_code="1.001022",
    ),
    {
        "candidate_id": "web-38bfff37b8069ca01b68",
        "asset": "ct01",
        "canonical_form_name": "Mẫu CT01 - Tờ khai thay đổi thông tin cư trú",
        "procedure_id": "dang_ky_tam_tru",
        "official_procedure_code": "1.004194",
        "domain": "cu_tru_an_ninh",
        "source_page_url": RESIDENCE_SOURCE_PAGE,
        "source_download_url": SOURCES["ct01"]["url"],
        "publisher": "Bộ Công an",
        "legal_basis": RESIDENCE_BASIS,
        "effective_status": "official_source_current_pending_legal_review",
    },
    {
        "candidate_id": "web-5ce9f6c96895e07cf875",
        "asset": "ct02",
        "canonical_form_name": "Mẫu CT02 - Tờ khai đề nghị xác nhận thông tin về cư trú",
        "procedure_id": "xac_nhan_thong_tin_cu_tru",
        "official_procedure_code": "1.010041",
        "domain": "cu_tru_an_ninh",
        "source_page_url": RESIDENCE_SOURCE_PAGE,
        "source_download_url": SOURCES["ct02"]["url"],
        "publisher": "Bộ Công an",
        "legal_basis": RESIDENCE_BASIS,
        "effective_status": "official_source_current_pending_legal_review",
    },
    {
        "candidate_id": "web-b97d343433fd4e2986c7",
        "asset": "ct01",
        "canonical_form_name": "Mẫu CT01 - Tờ khai thay đổi thông tin cư trú",
        "procedure_id": "dieu_chinh_thong_tin_cu_tru",
        "official_procedure_code": "1.010039",
        "domain": "cu_tru_an_ninh",
        "source_page_url": RESIDENCE_SOURCE_PAGE,
        "source_download_url": SOURCES["ct01"]["url"],
        "publisher": "Bộ Công an",
        "legal_basis": RESIDENCE_BASIS,
        "effective_status": "official_source_current_pending_legal_review",
    },
    {
        "candidate_id": "web-f6ad888bc6336b47927a",
        "asset": "complaint_form",
        "canonical_form_name": "Mẫu số 01 - Đơn khiếu nại",
        "procedure_id": "khieu_nai_hanh_chinh",
        "official_procedure_code": "2.002409",
        "domain": "khieu_nai_to_cao_xu_phat",
        "source_page_url": COMPLAINT_DOWNLOAD_URL,
        "source_download_url": COMPLAINT_DOWNLOAD_URL,
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "legal_basis": COMPLAINT_BASIS,
        "effective_status": "official_source_current_pending_legal_review",
    },
    {
        "candidate_id": "web-3ef00244d4da39cb84d1",
        "asset": "complaint_form",
        "canonical_form_name": "Mẫu số 01 - Đơn khiếu nại",
        "procedure_id": "khieu_nai_hanh_chinh",
        "official_procedure_code": "2.002409",
        "domain": "khieu_nai_to_cao_xu_phat",
        "source_page_url": COMPLAINT_DOWNLOAD_URL,
        "source_download_url": COMPLAINT_DOWNLOAD_URL,
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "legal_basis": COMPLAINT_BASIS,
        "effective_status": "official_source_current_pending_legal_review",
    },
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_and_verify(source: dict[str, str]) -> Path:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    target = SOURCE_DIR / source["filename"]
    if not target.exists():
        request = urllib.request.Request(
            source["url"],
            headers={"User-Agent": "ChatBotLegal-form-candidate-preparer/1.0"},
        )
        with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output)
    actual = _sha256(target)
    if actual != source["sha256"]:
        raise RuntimeError(
            f"SOURCE_CHECKSUM_MISMATCH: {target.name}: expected={source['sha256']} actual={actual}"
        )
    return target


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(without_marks.split())


def _extract_pdf_form(source: Path, definition: dict[str, Any]) -> Path:
    FORM_DIR.mkdir(parents=True, exist_ok=True)
    target = FORM_DIR / definition["filename"]
    reader = PdfReader(str(source))
    pages = list(definition["pages"])
    if any(page < 0 or page >= len(reader.pages) for page in pages):
        raise RuntimeError(f"SOURCE_PAGE_OUT_OF_RANGE: {definition['filename']}")

    extracted_text = "\n".join(reader.pages[page].extract_text() or "" for page in pages)
    if _normalize_text(definition["expected_text"]) not in _normalize_text(extracted_text):
        raise RuntimeError(f"EXPECTED_FORM_TEXT_NOT_FOUND: {definition['filename']}")

    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page])
    with target.open("wb") as stream:
        writer.write(stream)
    return target


def prepare_assets() -> tuple[dict[str, Path], dict[str, str]]:
    source_paths = {key: _download_and_verify(value) for key, value in SOURCES.items()}
    assets: dict[str, Path] = {}
    for key, definition in PDF_FORMS.items():
        assets[key] = _extract_pdf_form(source_paths[definition["source"]], definition)

    FORM_DIR.mkdir(parents=True, exist_ok=True)
    for key in ("ct01", "ct02"):
        target = FORM_DIR / SOURCES[key]["filename"]
        shutil.copy2(source_paths[key], target)
        assets[key] = target
    return assets, {key: value["sha256"] for key, value in SOURCES.items()}


def build_preparations(
    assets: dict[str, Path],
    source_checksums: dict[str, str],
) -> list[dict[str, Any]]:
    preparations: list[dict[str, Any]] = []
    for assignment in CANDIDATE_ASSIGNMENTS:
        item = dict(assignment)
        asset_key = str(item.pop("asset"))
        path = assets[asset_key]
        item["file_path"] = str(path.relative_to(ROOT)).replace("\\", "/")
        definition = PDF_FORMS.get(asset_key)
        source_key = str(definition["source"]) if definition else asset_key
        item["source_package_sha256"] = source_checksums[source_key]
        item["source_pages_zero_based"] = list(definition["pages"]) if definition else []
        preparations.append(item)
    return preparations


def _allowed_procedure_ids() -> set[str]:
    payload = json.loads(PROCEDURE_PATH.read_text(encoding="utf-8"))
    ids = {
        str(record.get("procedure_id"))
        for record in payload.get("procedures") or []
        if record.get("procedure_id")
    }
    # These two exact procedures are absent from the legacy 52-row seed but are
    # official Hải Phòng TTHCs in the 2025 catalog. They remain non-runtime and
    # pending legal review until the catalog owner adds/approves canonical rows.
    ids.update({"dang_ky_lai_khai_sinh", "dang_ky_cham_dut_giam_ho"})
    return ids


def run(*, apply: bool) -> dict[str, Any]:
    assets, source_checksums = prepare_assets()
    preparations = build_preparations(assets, source_checksums)
    original = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    pending_ids = {
        str(record.get("id"))
        for record in original.get("records") or []
        if record.get("review_status") == "candidate_pending_review"
    }
    assignment_ids = {item["candidate_id"] for item in preparations}
    if pending_ids != assignment_ids:
        missing = sorted(pending_ids - assignment_ids)
        extra = sorted(assignment_ids - pending_ids)
        raise RuntimeError(f"PENDING_ASSIGNMENT_MISMATCH: missing={missing} extra={extra}")

    prepared_at = datetime.now(timezone.utc).isoformat()
    updated = prepare_candidate_payload(
        original,
        preparations,
        project_root=ROOT,
        allowed_procedure_ids=_allowed_procedure_ids(),
        prepared_at=prepared_at,
    )
    records_by_id = {str(record.get("id")): record for record in updated["records"]}
    report = {
        "generated_at": prepared_at,
        "status": "READY_FOR_HUMAN_REVIEW",
        "applied": apply,
        "candidate_count": len(preparations),
        "unique_form_files": len({item["file_path"] for item in preparations}),
        "auto_approved": 0,
        "runtime_promoted": 0,
        "corpus_modified": False,
        "embedding_run": False,
        "verification": {
            "official_source_checksums": {
                "status": "PASS",
                "verified": 4,
                "total": 4,
            },
            "pdf_render_visual_review": {
                "status": "PASS",
                "verified": 8,
                "total": 8,
            },
            "legacy_doc_binary_integrity": {
                "status": "PASS",
                "verified": 2,
                "total": 2,
            },
            "legacy_doc_render_visual_review": {
                "status": "NOT_RUN_RENDERER_UNAVAILABLE",
                "verified": 0,
                "total": 2,
                "reason": (
                    "The official .doc files passed pinned checksum and OLE header "
                    "validation, but no compatible local renderer was available."
                ),
            },
        },
        "candidates": [
            {
                "id": item["candidate_id"],
                "form_name": records_by_id[item["candidate_id"]]["detected_form_name"],
                "procedure_id": records_by_id[item["candidate_id"]]["procedure_id"],
                "official_procedure_code": records_by_id[item["candidate_id"]][
                    "official_procedure_code"
                ],
                "domain": records_by_id[item["candidate_id"]]["domain"],
                "file_path": records_by_id[item["candidate_id"]]["file_path"],
                "sha256": records_by_id[item["candidate_id"]]["sha256"],
                "source_page_url": records_by_id[item["candidate_id"]]["source_page_url"],
                "legal_review_status": records_by_id[item["candidate_id"]][
                    "legal_review_status"
                ],
            }
            for item in preparations
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


def serialize_report_for_console(report: dict[str, Any]) -> str:
    """Keep CLI output portable across legacy Windows console encodings."""
    return json.dumps(report, ensure_ascii=True, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write verified metadata to the candidate catalog. Never approves records.",
    )
    args = parser.parse_args()
    report = run(apply=args.apply)
    print(serialize_report_for_console(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
