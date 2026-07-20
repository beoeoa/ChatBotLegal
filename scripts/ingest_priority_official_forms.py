"""Download a small, curated supplement of high-value official forms.

The existing Hai Phong crawler primarily discovers procedure packages. This
script fills important gaps with deterministic, reviewed source definitions.
It only stores original files or verbatim PDF page slices; it never recreates
the wording of a legal form.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "uploads" / "forms" / "priority_official"
OUTPUT_PATH = ROOT / "notebook_data" / "forms" / "priority_official_forms.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

BCA_SOURCE_PAGE = (
    "https://vanban.bocongan.gov.vn/co-so-du-lieu-van-ban/"
    "van-ban-hop-nhat-thong-tu-quy-dinh-chi-tiet-mot-so-dieu-va-"
    "bien-phap-thi-hanh-luat-cu-tru-1762764131?tab=attributes"
)

STANDALONE_SOURCES = (
    {
        "slug": "ct01-thay-doi-thong-tin-cu-tru",
        "title": "Mẫu CT01 - Tờ khai thay đổi thông tin cư trú",
        "domain": "cu_tru_an_ninh",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/"
            "photo-library-20251110154050-8c5a715e-dafe-4af0-bf1c-"
            "fc1f5d98d3dd-1-mau-ct01-ban-hanh-kem-theo-thong-tu-53.doc"
        ),
        "source_page_url": BCA_SOURCE_PAGE,
        "publisher": "Bộ Công an",
        "legal_basis": "Văn bản hợp nhất 42/VBHN-BCA năm 2025",
    },
    {
        "slug": "ct02-xac-nhan-thong-tin-cu-tru",
        "title": "Mẫu CT02 - Tờ khai đề nghị xác nhận thông tin về cư trú",
        "domain": "cu_tru_an_ninh",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/"
            "photo-library-20251110154050-0f5e8493-75c7-4d63-a2b3-"
            "72cff259be46-2-mau-ct02-ban-hanh-kem-theo-thong-tu-so-53.doc"
        ),
        "source_page_url": BCA_SOURCE_PAGE,
        "publisher": "Bộ Công an",
        "legal_basis": "Văn bản hợp nhất 42/VBHN-BCA năm 2025",
    },
    {
        "slug": "ct12-thong-ke-cu-tru",
        "title": "Mẫu CT12 - Thống kê tình hình, kết quả đăng ký, quản lý cư trú",
        "domain": "cu_tru_an_ninh",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/"
            "photo-library-20251110154050-5299e6a2-76a3-428e-a2cf-"
            "9d492376535d-4-mau-ct12-thong-ke-tinh-hinh-ket-qua-ang-ky-"
            "quan-ly-cu-tru-tt53.doc"
        ),
        "source_page_url": BCA_SOURCE_PAGE,
        "publisher": "Bộ Công an",
        "legal_basis": "Văn bản hợp nhất 42/VBHN-BCA năm 2025",
    },
    {
        "slug": "ct15-giao-nhan-ho-so-cu-tru",
        "title": "Mẫu CT15 - Sổ theo dõi giao, nhận hồ sơ cư trú",
        "domain": "cu_tru_an_ninh",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/"
            "photo-library-20251110154051-d4895a3d-811a-4a4b-9274-"
            "8804176cbccd-5-mau-ct15-so-theo-doi-giao-nhan-ho-so-cu-"
            "tru-thong-tu-53.doc"
        ),
        "source_page_url": BCA_SOURCE_PAGE,
        "publisher": "Bộ Công an",
        "legal_basis": "Văn bản hợp nhất 42/VBHN-BCA năm 2025",
    },
    {
        "slug": "ct16-tra-cuu-ho-so-cu-tru",
        "title": "Mẫu CT16 - Sổ theo dõi tra cứu, khai thác tàng thư hồ sơ cư trú",
        "domain": "cu_tru_an_ninh",
        "url": (
            "https://bocongan.gov.vn/media/bca-media/"
            "photo-library-20251110154051-95adeaae-7778-431f-9094-"
            "9d0fd2ec7b2c-6-mau-ct16-so-theo-doi-tra-cuu-khai-thac-"
            "tang-thu-ho-so-cu-tru-thong-tu-53.doc"
        ),
        "source_page_url": BCA_SOURCE_PAGE,
        "publisher": "Bộ Công an",
        "legal_basis": "Văn bản hợp nhất 42/VBHN-BCA năm 2025",
    },
)

PDF_PACKAGE_SOURCES = (
    {
        "slug": "hai-phong-dang-ky-ket-hon",
        "package_title": "Thủ tục đăng ký kết hôn cấp xã",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuongcatbi/"
            "6492/tintuc/2024/9/2.-thu-tuc-dang-ky-ket-hon"
            "638615605432505309.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-dang-ky-ket-hon",
                "title": "Tờ khai đăng ký kết hôn",
                "domain": "ho_tich_chung_thuc",
                "pages": (8, 10),
            },
        ),
    },
    {
        "slug": "hai-phong-khai-sinh-nhan-cha-me-con",
        "package_title": "Thủ tục đăng ký khai sinh kết hợp nhận cha, mẹ, con",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuonglamha/"
            "6395/tintuc/2024/7/4.-thu-tuc-dang-ky-khai-sinh-ket-hop-"
            "nhan-cha-me-con.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-dang-ky-khai-sinh",
                "title": "Tờ khai đăng ký khai sinh",
                "domain": "ho_tich_chung_thuc",
                "pages": (8, 9),
            },
            {
                "slug": "to-khai-dang-ky-nhan-cha-me-con",
                "title": "Tờ khai đăng ký nhận cha, mẹ, con",
                "domain": "ho_tich_chung_thuc",
                "pages": (10, 11),
            },
        ),
    },
    {
        "slug": "hai-phong-xac-nhan-tinh-trang-hon-nhan",
        "package_title": "Thủ tục cấp Giấy xác nhận tình trạng hôn nhân",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuonglamha/"
            "6395/tintuc/2024/7/12.-thu-tuc-cap-giay-xac-nhan-tinh-"
            "trang-hon-nhan.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-xac-nhan-tinh-trang-hon-nhan",
                "title": "Tờ khai cấp Giấy xác nhận tình trạng hôn nhân",
                "domain": "ho_tich_chung_thuc",
                "pages": (10, 12),
            },
        ),
    },
    {
        "slug": "hai-phong-dang-ky-giam-ho",
        "package_title": "Thủ tục đăng ký giám hộ cấp xã",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuonglamha/"
            "6395/tintuc/2024/7/9.-thu-tuc-dang-ky-giam-ho.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-dang-ky-giam-ho",
                "title": "Tờ khai đăng ký giám hộ",
                "domain": "ho_tich_chung_thuc",
                "pages": (8, 10),
            },
        ),
    },
    {
        "slug": "hai-phong-cham-dut-giam-ho",
        "package_title": "Thủ tục đăng ký chấm dứt giám hộ cấp xã",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuonglamha/"
            "6395/tintuc/2024/7/10.-thu-tuc-dang-ky-cham-dut-giam-ho.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-dang-ky-cham-dut-giam-ho",
                "title": "Tờ khai đăng ký chấm dứt giám hộ",
                "domain": "ho_tich_chung_thuc",
                "pages": (8, 9),
            },
        ),
    },
    {
        "slug": "hai-phong-dang-ky-lai-khai-sinh",
        "package_title": "Thủ tục đăng ký lại khai sinh cấp xã",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuonglamha/"
            "6395/tintuc/2024/7/13.-thu-tuc-dang-ky-lai-khai-sinh.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-dang-ky-lai-khai-sinh",
                "title": "Tờ khai đăng ký lại khai sinh",
                "domain": "ho_tich_chung_thuc",
                "pages": (10, 11),
            },
        ),
    },
    {
        "slug": "hai-phong-thay-doi-cai-chinh-ho-tich",
        "package_title": (
            "Thủ tục thay đổi, cải chính, bổ sung thông tin hộ tịch cấp phường"
        ),
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/SiteFolders/phuongngocson/"
            "6409/tintuc/2024/7/qt.tp.27.-thu-tuc-thay-doi-cai-chinh-"
            "bo-sung-thong-tin-ho-tich.signed.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "to-khai-thay-doi-cai-chinh-ho-tich",
                "title": (
                    "Tờ khai đăng ký thay đổi, cải chính, bổ sung thông tin "
                    "hộ tịch, xác định lại dân tộc"
                ),
                "domain": "ho_tich_chung_thuc",
                "pages": (10, 12),
            },
        ),
    },
    {
        "slug": "hai-phong-khieu-nai-2025",
        "package_title": "Danh mục thủ tục hành chính lĩnh vực khiếu nại năm 2025",
        "url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/6808/tintuc/2025/11/"
            "1.2-danh-m_c-tthc-l_nh-v_c-khi_u-n_i638990785291072657.pdf"
        ),
        "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
        "forms": (
            {
                "slug": "mau-01-don-khieu-nai",
                "title": "Mẫu số 01 - Đơn khiếu nại",
                "domain": "khieu_nai_to_cao_xu_phat",
                "pages": (9, 9),
            },
            {
                "slug": "mau-02-giay-uy-quyen-khieu-nai",
                "title": "Mẫu số 02 - Giấy ủy quyền khiếu nại",
                "domain": "khieu_nai_to_cao_xu_phat",
                "pages": (10, 10),
            },
            {
                "slug": "mau-12-kn-giay-bien-nhan",
                "title": "Mẫu số 12-KN - Giấy biên nhận thông tin, tài liệu, bằng chứng",
                "domain": "khieu_nai_to_cao_xu_phat",
                "pages": (11, 11),
            },
        ),
    },
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def form_id(source_url: str, slug: str) -> str:
    return hashlib.sha256(f"{source_url}|{slug}".encode("utf-8")).hexdigest()[:24]


def validate_content(content: bytes, expected: str) -> None:
    if expected == "pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("Nguồn trả về không phải PDF hợp lệ.")
    if expected == "doc" and not content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("Nguồn trả về không phải DOC hợp lệ.")


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def slice_pdf(content: bytes, first_page: int, last_page: int) -> bytes:
    reader = PdfReader(BytesIO(content))
    if first_page < 1 or last_page > len(reader.pages) or first_page > last_page:
        raise ValueError(
            f"Khoảng trang {first_page}-{last_page} không hợp lệ "
            f"cho PDF {len(reader.pages)} trang."
        )
    writer = PdfWriter()
    for index in range(first_page - 1, last_page):
        writer.add_page(reader.pages[index])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def base_record(source: dict[str, Any], source_hash: str) -> dict[str, Any]:
    return {
        "publisher": source["publisher"],
        "locality": "Hải Phòng" if "Hải Phòng" in source["publisher"] else "Toàn quốc",
        "administrative_level": "commune_relevant",
        "review_status": "admin_curated_source",
        "legal_status": "needs_admin_effectivity_review",
        "catalog_status": "available_official_source",
        "is_canonical": True,
        "source_sha256": source_hash,
        "retrieved_at": utcnow(),
        "priority_tier": "frequently_used",
    }


def ingest() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=60,
    ) as client:
        for source in STANDALONE_SOURCES:
            try:
                response = client.get(source["url"])
                response.raise_for_status()
                validate_content(response.content, "doc")
                filename = f"{source['slug']}.doc"
                destination = OUTPUT_DIR / filename
                write_bytes(destination, response.content)
                digest = sha256(response.content)
                records.append(
                    {
                        **base_record(source, digest),
                        "id": form_id(source["url"], source["slug"]),
                        "form_title": source["title"],
                        "domain": source["domain"],
                        "source_package_title": source["legal_basis"],
                        "source_page_url": source["source_page_url"],
                        "source_download_url": source["url"],
                        "source_package_path": str(destination.relative_to(ROOT)),
                        "file_type": "doc",
                        "size_bytes": len(response.content),
                        "is_verbatim_page_slice": False,
                    }
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                errors.append({"source": source["url"], "error": str(exc)})

        for source in PDF_PACKAGE_SOURCES:
            try:
                response = client.get(source["url"])
                response.raise_for_status()
                validate_content(response.content, "pdf")
                package_hash = sha256(response.content)
                package_path = OUTPUT_DIR / f"{source['slug']}-source.pdf"
                write_bytes(package_path, response.content)
                for form in source["forms"]:
                    first_page, last_page = form["pages"]
                    sliced = slice_pdf(response.content, first_page, last_page)
                    destination = OUTPUT_DIR / f"{form['slug']}.pdf"
                    write_bytes(destination, sliced)
                    records.append(
                        {
                            **base_record(source, package_hash),
                            "id": form_id(source["url"], form["slug"]),
                            "form_title": form["title"],
                            "domain": form["domain"],
                            "source_package_title": source["package_title"],
                            "source_page_url": source["url"],
                            "source_download_url": source["url"],
                            "source_package_path": str(destination.relative_to(ROOT)),
                            "source_original_path": str(package_path.relative_to(ROOT)),
                            "source_page": first_page,
                            "source_page_end": last_page,
                            "file_sha256": sha256(sliced),
                            "file_type": "pdf",
                            "size_bytes": len(sliced),
                            "is_verbatim_page_slice": True,
                        }
                    )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                errors.append({"source": source["url"], "error": str(exc)})

    summary = {
        "total_available": len(records),
        "standalone_files": sum(
            1 for record in records if not record["is_verbatim_page_slice"]
        ),
        "verbatim_pdf_slices": sum(
            1 for record in records if record["is_verbatim_page_slice"]
        ),
        "errors": len(errors),
    }
    payload = {
        "generated_at": utcnow(),
        "summary": summary,
        "forms": records,
        "errors": errors,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUTPUT_PATH)
    return payload


def main() -> int:
    payload = ingest()
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Supplement catalog: {OUTPUT_PATH}")
    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
