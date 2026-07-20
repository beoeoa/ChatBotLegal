#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify official form candidates and extract metadata.

Input:
- notebook_data/forms/official_forms_candidates_full.json (optional metadata)
- data/uploads/forms/official_candidates/**

Output:
- notebook_data/forms/official_forms_candidates_classified.json

Rules:
- Extract text from PDF/DOC/DOCX when possible
- Suggest domain/procedure only from evidence
- Never auto-approve (review_status always candidate_pending_review)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates"
INPUT_MANIFEST = ROOT / "notebook_data" / "forms" / "official_forms_candidates_full.json"
OUTPUT_PATH = ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"

FORM_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}

DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ho_tich": (
        "hộ tịch", "ho tich", "khai sinh", "khai tử", "khai tu", "kết hôn", "ket hon",
        "xác nhận tình trạng hôn nhân", "độc thân", "doc than", "nuôi con nuôi",
        "cải chính hộ tịch", "chứng thực", "chung thuc", "giám hộ", "giam ho",
        "tư pháp", "tu phap",
    ),
    "cu_tru": (
        "cư trú", "cu tru", "thường trú", "thuong tru", "tạm trú", "tam tru",
        "hộ khẩu", "ho khau", "căn cước", "can cuoc", "cccd", "an ninh trật tự",
        "thông tin cư trú", "đăng ký cư trú",
    ),
    "dat_dai_xay_dung": (
        "đất đai", "dat dai", "xây dựng", "xay dung", "giấy phép xây dựng",
        "sổ đỏ", "so do", "quyền sử dụng đất", "quyen su dung dat", "nhà ở",
        "nha o", "địa chính", "dia chinh", "quy hoạch", "quy hoach",
        "biến động đất đai", "sang tên", "sang ten",
    ),
    "trat_tu_do_thi": (
        "trật tự đô thị", "trat tu do thi", "vỉa hè", "via he", "đỗ xe", "do xe",
        "lòng đường", "long duong", "chiếm dụng", "chiem dung", "đô thị",
        "an toàn giao thông", "giao thông đường bộ",
    ),
    "khieu_nai_to_cao": (
        "khiếu nại", "khieu nai", "tố cáo", "to cao", "tiếp công dân",
        "tiep cong dan", "khiếu tố", "khieu to", "thanh tra",
    ),
    "xu_phat_hanh_chinh": (
        "xử phạt", "xu phat", "vi phạm hành chính", "vi pham hanh chinh",
        "biên bản vi phạm", "quyết định xử phạt", "mức phạt", "tịch thu",
    ),
}

PROCEDURE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dang_ky_khai_sinh": ("đăng ký khai sinh", "dang ky khai sinh", "tờ khai đăng ký khai sinh", "khai sinh"),
    "dang_ky_ket_hon": ("đăng ký kết hôn", "dang ky ket hon", "tờ khai đăng ký kết hôn", "kết hôn"),
    "xac_nhan_doc_than": (
        "xác nhận tình trạng hôn nhân", "xac nhan tinh trang hon nhan",
        "giấy xác nhận độc thân", "tinh trang hon nhan",
    ),
    "dang_ky_khai_tu": ("đăng ký khai tử", "dang ky khai tu", "khai tử", "khai tu"),
    "cap_giay_phep_xay_dung": (
        "cấp giấy phép xây dựng", "cap giay phep xay dung", "giấy phép xây dựng",
        "xin phép xây dựng",
    ),
    "sang_ten_so_do": (
        "sang tên sổ đỏ", "sang ten so do", "biến động đất đai", "chuyển nhượng quyền sử dụng đất",
        "đăng ký biến động",
    ),
    "dang_ky_thuong_tru": ("đăng ký thường trú", "dang ky thuong tru", "thường trú"),
    "dang_ky_tam_tru": ("đăng ký tạm trú", "dang ky tam tru", "tạm trú"),
    "khieu_nai": ("đơn khiếu nại", "don khieu nai", "khiếu nại lần đầu", "khiếu nại lần hai"),
    "xu_phat_vi_pham": ("biên bản vi phạm", "quyết định xử phạt", "xử phạt hành chính"),
}


@dataclass
class ExtractResult:
    text: str
    method: str
    error: str | None = None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    text = (value or "").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fold_ascii(value: str) -> str:
    # lightweight fold for matching (keep Vietnamese too; lower-case only)
    return normalize_text(value).lower()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pdf_text(path: Path, max_pages: int = 8) -> ExtractResult:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="pypdf_unavailable", error=str(exc))
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages[:max_pages]:
            parts.append(page.extract_text() or "")
        text = normalize_text("\n".join(parts))
        return ExtractResult(text=text, method="pypdf")
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="pypdf", error=str(exc))


def extract_docx_text(path: Path, max_paras: int = 120) -> ExtractResult:
    # Prefer python-docx; fallback to raw zip xml
    try:
        import docx  # type: ignore

        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs[:max_paras]]
        text = normalize_text("\n".join(parts))
        if text:
            return ExtractResult(text=text, method="python-docx")
    except Exception:
        pass
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        raw = re.sub(r"</w:(?:p|tr)>", "\n", raw)
        raw = re.sub(r"<[^>]+>", " ", raw)
        text = normalize_text(raw)
        return ExtractResult(text=text, method="docx_xml")
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="docx", error=str(exc))


def extract_doc_text(path: Path) -> ExtractResult:
    # Best-effort: many .doc files may actually be HTML/RTF wrappers or unreadable binary.
    try:
        data = path.read_bytes()
        # Try UTF-16LE / UTF-8 printable extraction
        for enc in ("utf-16le", "utf-8", "cp1258", "latin-1"):
            try:
                raw = data.decode(enc, errors="ignore")
            except Exception:
                continue
            # Keep only reasonably printable sequences
            cleaned = re.sub(r"[^\x09\x0A\x0D\x20-\x7E\u00C0-\u1EF9]+", " ", raw)
            cleaned = normalize_text(cleaned)
            if len(cleaned) >= 80:
                return ExtractResult(text=cleaned[:20000], method=f"doc_bytes_{enc}")
        return ExtractResult(text="", method="doc_bytes", error="no_readable_text")
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="doc_bytes", error=str(exc))


def extract_text(path: Path) -> ExtractResult:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix == ".docx":
        return extract_docx_text(path)
    if suffix == ".doc":
        return extract_doc_text(path)
    if suffix in {".xls", ".xlsx"}:
        # No heavy spreadsheet parse; use filename only
        return ExtractResult(text="", method="spreadsheet_skipped", error="no_spreadsheet_parser")
    return ExtractResult(text="", method="unsupported", error=f"unsupported_ext:{suffix}")


def score_keywords(haystack: str, keywords: Iterable[str]) -> tuple[int, list[str]]:
    hits: list[str] = []
    score = 0
    for kw in keywords:
        if kw in haystack:
            hits.append(kw)
            # longer phrases get slightly higher weight
            score += 2 if " " in kw else 1
    return score, hits


def detect_domain(haystack: str) -> tuple[str | None, float, list[str]]:
    best_domain = None
    best_score = 0
    best_hits: list[str] = []
    for domain, kws in DOMAIN_KEYWORDS.items():
        score, hits = score_keywords(haystack, kws)
        if score > best_score:
            best_domain, best_score, best_hits = domain, score, hits
    if best_score <= 0:
        return None, 0.0, []
    # confidence: soft scale
    conf = min(0.95, 0.35 + 0.1 * best_score)
    return best_domain, conf, best_hits


def detect_procedure(haystack: str) -> tuple[str | None, float, list[str]]:
    best_id = None
    best_score = 0
    best_hits: list[str] = []
    for proc_id, kws in PROCEDURE_KEYWORDS.items():
        score, hits = score_keywords(haystack, kws)
        if score > best_score:
            best_id, best_score, best_hits = proc_id, score, hits
    if best_score <= 0:
        return None, 0.0, []
    conf = min(0.95, 0.4 + 0.12 * best_score)
    return best_id, conf, best_hits


def detect_form_name(text: str, fallback_name: str) -> tuple[str, str]:
    """Return (detected_form_name, reason)."""
    lines = [normalize_text(x) for x in (text or "").splitlines() if normalize_text(x)]
    # Prefer lines with form markers
    markers = (
        "tờ khai", "to khai", "mẫu số", "mau so", "biểu mẫu", "bieu mau",
        "đơn ", "don ", "giấy ", "giay ",
    )
    for line in lines[:80]:
        low = line.lower()
        if any(m in low for m in markers) and 8 <= len(line) <= 180:
            return line, "matched_form_marker_line"
    # Fallback: first substantial line
    for line in lines[:30]:
        if 12 <= len(line) <= 180 and not line.lower().startswith("cộng hòa"):
            return line, "first_substantial_line"
    # Filename fallback
    name = Path(fallback_name).stem
    name = re.sub(r"^[0-9a-f]{8,}-", "", name, flags=re.I)
    name = name.replace(".pdf", "").replace(".doc", "").replace("_", " ").strip()
    return name or fallback_name, "filename_fallback"


def load_manifest_index() -> dict[str, dict[str, Any]]:
    if not INPUT_MANIFEST.exists():
        return {}
    try:
        data = json.loads(INPUT_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {}
    index: dict[str, dict[str, Any]] = {}
    for rec in data.get("records") or []:
        if not isinstance(rec, dict):
            continue
        for key in ("sha256", "local_path", "filename", "source_url"):
            val = rec.get(key)
            if val:
                index[str(val).replace("\\", "/").lower()] = rec
    return index


def iter_candidate_files(limit: int | None = None) -> list[Path]:
    if not CANDIDATE_DIR.exists():
        return []
    files = [
        p for p in sorted(CANDIDATE_DIR.rglob("*"))
        if p.is_file() and p.suffix.lower() in FORM_EXTS
    ]
    if limit is not None:
        files = files[: max(0, limit)]
    return files


def classify_file(path: Path, manifest_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    digest = sha256_file(path)
    meta = (
        manifest_index.get(digest.lower())
        or manifest_index.get(rel.lower())
        or manifest_index.get(path.name.lower())
        or {}
    )

    extracted = extract_text(path)
    text = extracted.text or ""
    hay = fold_ascii(f"{path.name}\n{meta.get('form_title') or ''}\n{text}")

    domain, domain_conf, domain_hits = detect_domain(hay)
    procedure_id, proc_conf, proc_hits = detect_procedure(hay)
    form_name, form_reason = detect_form_name(text, path.name)

    # overall confidence
    conf = 0.2
    if text:
        conf += 0.2
    conf = max(conf, domain_conf * 0.7 + proc_conf * 0.3)
    if not text:
        conf = min(conf, 0.35)

    reasons = []
    if extracted.error:
        reasons.append(f"extract_error={extracted.error}")
    reasons.append(f"extract_method={extracted.method}")
    reasons.append(f"form_name_reason={form_reason}")
    if domain_hits:
        reasons.append("domain_hits=" + ",".join(domain_hits[:8]))
    else:
        reasons.append("domain_hits=none")
    if proc_hits:
        reasons.append("procedure_hits=" + ",".join(proc_hits[:8]))
    else:
        reasons.append("procedure_hits=none")
    if not text:
        reasons.append("text_empty_filename_only")

    return {
        "id": meta.get("id") or digest[:24],
        "file_name": path.name,
        "file_path": rel,
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "source_url": meta.get("source_url"),
        "page_url": meta.get("page_url"),
        "source_id": meta.get("source_id"),
        "source_name": meta.get("source_name"),
        "detected_form_name": form_name,
        "suggested_procedure_id": procedure_id,
        "suggested_domain": domain,
        "confidence": round(float(conf), 4),
        "reason": "; ".join(reasons),
        "review_status": "candidate_pending_review",
        "is_approved": False,
        "extract_method": extracted.method,
        "text_chars": len(text),
        "text_sample": text[:500],
        "classified_at": utcnow(),
    }


def build_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_domain: dict[str, int] = {}
    by_procedure: dict[str, int] = {}
    with_text = 0
    for r in records:
        dom = r.get("suggested_domain") or "unknown"
        by_domain[dom] = by_domain.get(dom, 0) + 1
        proc = r.get("suggested_procedure_id") or "unknown"
        by_procedure[proc] = by_procedure.get(proc, 0) + 1
        if int(r.get("text_chars") or 0) > 0:
            with_text += 1

    ranked = sorted(records, key=lambda x: float(x.get("confidence") or 0), reverse=True)
    return {
        "generated_at": utcnow(),
        "input_manifest": str(INPUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "candidate_dir": str(CANDIDATE_DIR.relative_to(ROOT)).replace("\\", "/"),
        "summary": {
            "total_classified": len(records),
            "with_extracted_text": with_text,
            "without_text": len(records) - with_text,
            "by_domain": dict(sorted(by_domain.items(), key=lambda x: (-x[1], x[0]))),
            "by_procedure": dict(sorted(by_procedure.items(), key=lambda x: (-x[1], x[0]))),
            "auto_approved": 0,
        },
        "records": records,
        "top10": ranked[:10],
        "notes": [
            "AI/heuristic classification only. review_status remains candidate_pending_review.",
            "No auto-approval is performed.",
            "procedure_id/domain are suggestions and may be null when evidence is weak.",
        ],
    }


def print_top10(report: dict[str, Any]) -> None:
    print("=== OFFICIAL FORM CLASSIFICATION ===")
    summary = report.get("summary") or {}
    print(f"total_classified: {summary.get('total_classified')}")
    print(f"with_extracted_text: {summary.get('with_extracted_text')}")
    print(f"without_text: {summary.get('without_text')}")
    print(f"by_domain: {summary.get('by_domain')}")
    print("TOP 10:")
    lines: list[str] = []
    for i, rec in enumerate(report.get("top10") or [], start=1):
        line = (
            f"{i:02d}. conf={rec.get('confidence'):.3f} "
            f"domain={rec.get('suggested_domain')} "
            f"proc={rec.get('suggested_procedure_id')} "
            f"name={(rec.get('detected_form_name') or '')[:80]}"
        )
        print(line)
        print(f"    file={rec.get('file_name')}")
        reason = str(rec.get("reason") or "")
        lines.append(line)
        lines.append(f"    file={rec.get('file_name')}")
        lines.append(f"    reason={reason}")
        lines.append("")
    # Avoid Windows console UnicodeEncodeError for Vietnamese reasons.
    top10_path = OUTPUT_PATH.with_name("official_forms_candidates_classified_top10.txt")
    top10_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"top10_detail: {top10_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0, help="Max files to classify (0 = all)")
    p.add_argument("--min-files", type=int, default=20, help="Warn if fewer than this many files classified")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    limit = int(args.limit) if int(args.limit) > 0 else None
    files = iter_candidate_files(limit=limit)
    if not files:
        print("No candidate files found.")
        return 1

    manifest_index = load_manifest_index()
    records = [classify_file(path, manifest_index) for path in files]
    report = build_report(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print_top10(report)
    print(f"output: {OUTPUT_PATH}")
    if len(records) < int(args.min_files):
        print(f"WARNING: classified only {len(records)} < min-files {args.min_files}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())