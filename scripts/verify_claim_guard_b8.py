# -*- coding: utf-8 -*-
"""Bước 8 verification: post-answer claim guard for sensitive legal claims."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.routers import search as s  # noqa: E402


def _types(removed):
    return [x.get("claim_type") for x in removed]


def main() -> int:
    cases = []

    # A: unsupported deadline 03-05 days
    out, rem = s._guard_sensitive_claims(
        "Thời hạn giải quyết là 03–05 ngày làm việc. Bạn nên chuẩn bị hồ sơ đầy đủ.",
        {"results": [{"content": "Thẩm quyền UBND cấp huyện. Không nêu thời hạn.", "title": "NĐ"}]},
    )
    ok = (
        "03" not in out
        and "05" not in out
        and any(t in _types(rem) for t in ("thoi_han", "ngay_lam_viec"))
        and "chưa có căn cứ" in out
    )
    cases.append({"id": "A_deadline", "ok": ok, "out": out, "removed": rem})

    # B: unsupported fee amount
    out, rem = s._guard_sensitive_claims(
        "Lệ phí 50.000 đồng.",
        {"results": [{"content": "Thủ tục đăng ký khai sinh theo Nghị định 123.", "title": "NĐ 123"}]},
    )
    ok = "50.000" not in out and any(t in _types(rem) for t in ("so_tien", "le_phi")) and "chưa có căn cứ" in out
    cases.append({"id": "B_fee", "ok": ok, "out": out, "removed": rem})

    # C: supported authority kept
    out, rem = s._guard_sensitive_claims(
        "Thẩm quyền thuộc UBND cấp huyện theo Điều 29 Nghị định 123/2015/NĐ-CP.",
        {
            "results": [
                {
                    "content": "Điều 29 Nghị định 123/2015/NĐ-CP quy định thẩm quyền UBND cấp huyện.",
                    "law_number": "123/2015/NĐ-CP",
                    "article_number": "29",
                }
            ]
        },
    )
    ok = "UBND cấp huyện" in out and "Điều 29" in out and rem == []
    cases.append({"id": "C_authority_supported", "ok": ok, "out": out, "removed": rem})

    # D: free claim unsupported -> full sentence safe rewrite
    out, rem = s._guard_sensitive_claims(
        "Thủ tục này miễn phí hoàn toàn.",
        {"results": [{"content": "Đăng ký khai sinh tại UBND.", "title": "Luật hộ tịch"}]},
    )
    ok = (
        "miễn phí" not in out.lower()
        and "mien_phi" in _types(rem)
        and out.strip().startswith("Kho dữ liệu hiện tại chưa có căn cứ")
        and "hoàn toàn" not in out
    )
    cases.append({"id": "D_free_sentence", "ok": ok, "out": out, "removed": rem})

    # E: mixed authority keep + fee remove
    out, rem = s._guard_sensitive_claims(
        "Thẩm quyền UBND cấp huyện theo Điều 29 NĐ 123. Lệ phí 50.000 đồng.",
        {
            "results": [
                {
                    "content": "Điều 29 Nghị định 123/2015/NĐ-CP quy định thẩm quyền UBND cấp huyện.",
                    "law_number": "123/2015/NĐ-CP",
                    "article_number": "29",
                }
            ]
        },
    )
    ok = "UBND cấp huyện" in out and "Điều 29" in out and "50.000" not in out and any(
        t in _types(rem) for t in ("so_tien", "le_phi")
    )
    cases.append({"id": "E_auth_keep_fee_remove", "ok": ok, "out": out, "removed": rem})

    # Trace merge fields
    sample_rem = [{"claim_type": "thoi_han", "reason": "unsupported_by_retrieval", "original": "x", "replacement": "y"}]
    trace = s._merge_claim_guard_into_trace({"model": "test"}, sample_rem)
    ok_trace = (
        isinstance(trace.get("removed_unsupported_claims"), list)
        and trace["removed_unsupported_claims"][0].get("claim_type") == "thoi_han"
        and trace.get("claim_guard", {}).get("removed_count") == 1
        and "thoi_han" in (trace.get("claim_guard", {}).get("claim_types") or [])
    )
    cases.append({"id": "F_trace_merge", "ok": ok_trace, "out": trace, "removed": sample_rem})

    # Wiring presence
    src = Path(ROOT / "api" / "routers" / "search.py").read_text(encoding="utf-8")
    ok_wire = src.count("_guard_sensitive_claims(") >= 3 and "removed_unsupported_claims" in src
    cases.append({"id": "G_wired_in_ask_paths", "ok": ok_wire, "out": "present", "removed": []})

    report = {
        "step": 8,
        "title": "claim_guard_sensitive_claims",
        "passed": sum(1 for c in cases if c["ok"]),
        "total": len(cases),
        "all_ok": all(c["ok"] for c in cases),
        "cases": [
            {
                "id": c["id"],
                "ok": c["ok"],
                "out": c["out"] if not isinstance(c["out"], dict) else c["out"],
                "removed_types": _types(c["removed"]) if isinstance(c["removed"], list) else [],
            }
            for c in cases
        ],
    }
    out_path = ROOT / "notebook_data" / "forms" / "b8_claim_guard_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_ok": report["all_ok"], "passed": report["passed"], "total": report["total"], "report": str(out_path)}, ensure_ascii=False))
    for c in report["cases"]:
        print(f"{'PASS' if c['ok'] else 'FAIL'} {c['id']}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
