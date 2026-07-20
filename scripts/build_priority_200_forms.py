# -*- coding: utf-8 -*-
"""Compatibility API for the Step-10 procedure-centric priority catalog.

The catalog describes forms that the crawler should locate.  It is deliberately
separate from the manifest of downloaded, administrator-reviewed official
files; callers must not interpret ``selected_count`` as an ingestion count.
"""
from __future__ import annotations

import json
from collections import Counter

from scripts.build_priority_200_procedure_catalog import OUT, expand, CORE


OUTPUT_PATH = OUT
CANONICAL_DOMAINS = {
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
}


def _canonical_domain(domain: str) -> str | None:
    if domain in CANONICAL_DOMAINS:
        return domain
    # Ward-level urban-order forms belong to the land/construction workstream.
    if domain == "trat_tu_do_thi":
        return "dat_dai_xay_dung"
    return None


def build_priority_catalog() -> dict:
    procedures = expand(list(CORE))
    forms: list[dict] = []
    for procedure in procedures:
        domain = _canonical_domain(procedure["domain"])
        if domain is None:
            continue
        for position, title in enumerate(procedure["expected_form_names"], start=1):
            forms.append(
                {
                    "id": f"{procedure['procedure_id']}:{position}",
                    "form_title": title,
                    "source_package_title": procedure["procedure_name"],
                    "procedure_id": procedure["procedure_id"],
                    "domain": domain,
                    "catalog_status": "discovery_target",
                    "official_file_verified": False,
                    "preferred_sources": procedure["preferred_sources"],
                }
            )

    domain_counts = Counter(row["domain"] for row in forms)
    payload = {
        "schema_version": 3,
        "description": (
            "Danh mục mục tiêu để crawler tìm biểu mẫu; không phải số tệp "
            "chính thức đã tải hoặc đã được admin duyệt."
        ),
        "summary": {
            "selected_count": len(forms),
            "target_count": 200,
            "target_met": len(forms) >= 200,
            "domain_counts": dict(sorted(domain_counts.items())),
            "verified_official_file_count": 0,
        },
        "forms": forms,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    result = build_priority_catalog()
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["summary"]["target_met"] else 1)
