"""Write the final Golden 1000 review report in clean UTF-8.

This is a report-only step; it reads the generated JSON artifacts and does not
change the legal corpus or any production collection.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-luna"


def main() -> None:
    payload = json.loads((OUT / "golden-1000-luna.json").read_text(encoding="utf-8"))
    validation = json.loads((OUT / "validation-report.json").read_text(encoding="utf-8"))
    duplicate = json.loads((OUT / "duplicate-report.json").read_text(encoding="utf-8"))
    verification = json.loads((OUT / "final-verification.json").read_text(encoding="utf-8")) if (OUT / "final-verification.json").exists() else {}
    summary = payload["summary"]
    categories = validation.get("scenario_category_counts", summary.get("scenario_category_counts", {}))
    report = [
        "# Golden 1000 Luna — báo cáo kiểm tra",
        "",
        "Bộ dữ liệu hiện ở chế độ review-only; chưa nhập vào corpus production.",
        "",
        f"- Tổng số ca: **{len(payload['cases'])}**",
        "- Phân bổ: **200 ca mỗi lĩnh vực**; 600 development / 200 validation / 200 held-out.",
        "- Cơ cấu mỗi lĩnh vực: **120 thủ tục (60%) / 30 Điều-khoản-điểm (15%) / 20 đa vấn đề (10%) / 20 hiệu lực (10%) / 10 thiếu điều kiện-dự phòng (5%)**.",
        f"- Cơ cấu toàn bộ: `{json.dumps(categories, ensure_ascii=False)}`",
        f"- Ca có physical proof từ PostgreSQL read-only: **{summary.get('physical_proof_count', 0)}**",
        f"- Nguồn có quote + URL + char range: **{summary.get('physical_proof_source_count', 0)}**",
        f"- Cần legal review: **{summary.get('legal_review_count', 0)}**",
        f"- Nguồn chưa khớp DB: **{summary.get('db_source_missing_count', 0)}**",
        f"- Cặp gần trùng: **{duplicate.get('near_duplicate_count', 0)}**",
        f"- Xác minh độc lập DB: `{json.dumps(verification.get('checks', {}), ensure_ascii=False)}`",
        "- Physical proof là nội dung nguyên văn từ `legal_articles.content`; page/bounding box không có trong DB chuẩn hóa.",
        "- Chưa có ca nào chuyển sang approved; cần người duyệt kiểm tra URL chính thức, hiệu lực, Điều/khoản/điểm, quote và claims trước khi duyệt.",
        f"- Validator: `{json.dumps(validation.get('checks', {}), ensure_ascii=False)}`",
    ]
    (OUT / "final-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUT / "final-report.md"), "utf8": True, "cases": len(payload["cases"]), "near_duplicates": duplicate.get("near_duplicate_count", 0)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
