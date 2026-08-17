#!/usr/bin/env python3
"""Collect the 40 approved browser-UAT questions from persisted Ask history."""

from __future__ import annotations

import json
import os
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx


CITIZEN = [
    ("dossier_form", "Nhà tôi vừa có em bé, giờ đi đăng ký khai sinh lần đầu thì cần mang những giấy tờ gì, có mẫu nào phải điền sẵn không?"),
    ("exact_article", "Cho tôi hỏi Điều 18a của Luật 88/2025/QH15 quy định cụ thể những gì?"),
    ("exact_form", "Khách nước ngoài ở nhà tôi qua đêm thì dùng mẫu NA17 nào, cho tôi xin đúng biểu mẫu chính thức để tải."),
    ("form_legal_basis", "Tôi muốn xin điều chỉnh quyết định giao đất thì phải dùng mẫu đơn nào và căn cứ văn bản nào vậy?"),
    ("hard_procedure", "Mẹ tôi 82 tuổi, không có lương hưu. Bà có thể xin trợ cấp hằng tháng không, hồ sơ nộp ở đâu?"),
    ("deadline", "Tôi gửi khiếu nại rồi mà mãi chưa được giải quyết; luật quy định thời hạn bao lâu, trường hợp phức tạp có được kéo dài không?"),
    ("procedure_dossier", "Em mới thuê trọ ở Hải Phòng, đăng ký tạm trú online cần giấy tờ gì và chủ nhà phải làm gì giúp em?"),
    ("procedure_form", "Tôi cần giấy xác nhận tình trạng hôn nhân để mua nhà, làm thủ tục thế nào và có biểu mẫu điện tử không?"),
    ("multi_issue", "Bé nhà tôi chưa có khai sinh và cũng chưa nhập thông tin cư trú; tôi có thể làm hai việc cùng lúc không, cần chuẩn bị gì?"),
    ("ambiguous_form", "Mẫu 01 tải ở đâu vậy bạn? Tôi đang cần gấp mà không biết chọn mẫu nào."),
    ("historical", "Nếu việc vi phạm xảy ra từ năm 2024 thì khi tra thời hiệu xử phạt phải áp dụng quy định ở thời điểm nào?"),
    ("procedure_dossier", "Ông tôi vừa mất tại nhà, gia đình đăng ký khai tử ở đâu và cần giấy báo tử hay giấy tờ gì thay thế?"),
    ("easy_procedure", "Tôi có bản photo bằng tốt nghiệp, muốn chứng thực bản sao thì ra phường được không và cần mang bản gốc chứ?"),
    ("dossier_form", "Gia đình tôi sử dụng đất lâu năm nhưng chưa có sổ, xin cấp giấy chứng nhận lần đầu cần hồ sơ và mẫu đơn nào?"),
    ("exact_form_set", "Muốn giải thể một trường tiểu học tư thục thì cần tờ trình, đề án hay những mẫu nào?"),
    ("procedure_dossier", "Con tôi 12 tuổi chưa làm căn cước, cha mẹ phải đưa cháu đi hay có thể làm trực tuyến, hồ sơ gồm gì?"),
    ("general_legal", "Tố cáo nặc danh nhưng có ảnh và tài liệu rõ ràng thì cơ quan có xem xét hay bỏ luôn?"),
    ("multi_facet", "Tôi bị xử phạt hành chính và muốn khiếu nại: gửi ai, hạn bao lâu, trong lúc chờ thì có phải nộp phạt không?"),
    ("exact_article", "Điều 37a của Luật 88/2025/QH15 có những khoản, điểm nào? Xin đừng chỉ trích một đoạn ngắn."),
    ("procedure_dossier", "Tôi làm mất thẻ căn cước rồi, giờ xin cấp lại ở đâu, cần giấy tờ và mất khoảng bao lâu?"),
]

OFFICER = {
    "officer_hotich": [
        ("dossier_form", "Người dân hỏi đăng ký khai sinh lần đầu thì cán bộ cần hướng dẫn đúng thành phần hồ sơ và biểu mẫu nào?"),
        ("authority_validity", "Đề nghị tra đầy đủ quy định hiện hành về cấp giấy xác nhận tình trạng hôn nhân, gồm thẩm quyền và giá trị sử dụng."),
        ("exact_form", "Thủ tục đăng ký khai tử tại cấp xã đang dùng tờ khai nào? Cho tôi đúng nguồn biểu mẫu đã phát hành."),
        ("multi_issue", "Một công dân vừa đề nghị đăng ký khai sinh cho trẻ vừa hỏi cập nhật cư trú; hãy tách hai việc và nêu phần nào thuộc hộ tịch."),
    ],
    "officer_daidai": [
        ("dossier_form", "Hồ sơ đăng ký đất đai, cấp giấy chứng nhận lần đầu cho hộ gia đình gồm những giấy tờ chính nào và dùng mẫu số mấy?"),
        ("exact_form", "Khi điều chỉnh quyết định giao đất hoặc cho thuê đất thì hệ thống phải trả đúng Mẫu 04 nào, căn cứ ở đâu?"),
        ("validity_form", "Nhờ kiểm tra hiệu lực hiện hành của Quyết định 52/2026/QĐ-UBND và các biểu mẫu đất đai liên quan."),
        ("ambiguous_form", "Người dân chỉ nói “cho xin Mẫu 04 về đất” thì cần hỏi lại thông tin gì để tránh gửi nhầm biểu mẫu?"),
    ],
    "officer_ansinh": [
        ("hard_procedure", "Người 80 tuổi không có lương hưu hỏi trợ cấp xã hội hằng tháng: điều kiện, hồ sơ và nơi tiếp nhận hiện hành là gì?"),
        ("exact_form_set", "Thủ tục giải thể trường tiểu học theo đề nghị của tổ chức cần chính xác những biểu mẫu nào?"),
        ("dossier_basis", "Hộ nghèo xin hỗ trợ chi phí học tập cho con thì cán bộ nên yêu cầu giấy tờ gì và căn cứ nào?"),
        ("multi_issue", "Một gia đình hỏi đồng thời miễn học phí, bảo hiểm y tế và trợ cấp xã hội; hãy tách ba nội dung, không trộn điều kiện."),
    ],
    "officer_cutru": [
        ("exact_form", "Thủ tục khai báo tạm trú cho người nước ngoài bằng phiếu khai báo tạm trú dùng đúng mẫu NA17 nào?"),
        ("procedure_dossier", "Người thuê nhà đăng ký tạm trú trực tuyến cần những tài liệu gì, trách nhiệm của chủ chỗ ở ra sao?"),
        ("exact_article", "Đề nghị lấy đầy đủ Điều 18a của Luật 88/2025/QH15 theo đúng thứ tự khoản, điểm và nguồn hiện hành."),
        ("multi_issue", "Công dân vừa hỏi đăng ký thường trú vào nhà thuê vừa hỏi cấp lại căn cước bị mất; tách rõ hai thủ tục giúp tôi."),
    ],
    "officer_khieunai": [
        ("deadline", "Thời hạn giải quyết khiếu nại lần đầu là bao lâu trong vụ việc bình thường và phức tạp?"),
        ("general_legal", "Đơn tố cáo không ghi tên nhưng kèm chứng cứ cụ thể thì tiếp nhận, xử lý thế nào theo quy định hiện hành?"),
        ("legal_effect", "Người bị xử phạt khiếu nại quyết định thì có phải chấp hành quyết định trong thời gian chờ giải quyết không?"),
        ("multi_issue", "Một người vừa khiếu nại quyết định hành chính vừa tố cáo cán bộ; hãy phân biệt thẩm quyền, thời hạn và hồ sơ của từng việc."),
    ],
}

PASS_CASES = {("citizen01", 10), ("officer_daidai", 4)}
PARTIAL_CASES = {
    ("citizen01", 1),
    ("citizen01", 2),
    ("citizen01", 8),
    ("citizen01", 14),
    ("citizen01", 19),
    ("officer_hotich", 1),
    ("officer_cutru", 3),
}
WRONG_CASES = {("citizen01", 3)}


def _manual_verdict(username: str, ordinal: int) -> tuple[str, str]:
    key = (username, ordinal)
    if key in PASS_CASES:
        return "pass", "Safe and useful for the requested decision."
    if key in PARTIAL_CASES:
        return "partial", "Some correct source/form content, but one or more requested facets are missing."
    if key in WRONG_CASES:
        return "wrong", "Returned official forms for a different procedure; unsafe despite grounded status."
    return "fail", "No usable answer to the main legal/procedure question."


def main() -> None:
    password = os.getenv("FEATURE017_UAT_PASSWORD", "")
    if not password:
        raise RuntimeError("FEATURE017_UAT_PASSWORD is required")
    with httpx.Client(base_url="http://127.0.0.1:5055", timeout=30) as client:
        login = client.post(
            "/api/auth/login",
            json={"identifier": "admin", "password": password, "role": "admin"},
        )
        login.raise_for_status()
        token = login.json()["token"]
        response = client.get(
            "/api/search/ask-history?limit=500",
            headers={"Authorization": f"Bearer {token}", "X-User-Role": "admin"},
        )
        response.raise_for_status()
        history = response.json()["results"]

    latest_by_question = {}
    for row in history:
        latest_by_question.setdefault(str(row.get("question") or ""), row)

    planned = [("citizen", "citizen01", *item) for item in CITIZEN]
    for username, questions in OFFICER.items():
        planned.extend(("officer", username, *item) for item in questions)

    per_user = Counter()
    rows = []
    for role, username, category, question in planned:
        per_user[username] += 1
        record = latest_by_question.get(question)
        item = {
            "role": role,
            "username": username,
            "ordinal": per_user[username],
            "category": category,
            "question": question,
            "browser_result": "timeout_180s"
            if username == "citizen01" and per_user[username] == 9
            else "response_rendered",
            "history_found": bool(record),
        }
        if record:
            trace = record.get("rag_trace") or {}
            metric = (trace.get("section_orchestration") or {}).get("metric") or {}
            answer = str(record.get("answer") or "")
            item.update(
                {
                    "created": record.get("created"),
                    "duration_ms": record.get("duration_ms"),
                    "grounding_status": record.get("grounding_status"),
                    "answer_route": trace.get("answer_route"),
                    "route_reason": trace.get("route_reason"),
                    "error_category": metric.get("error_category"),
                    "question_classification": trace.get("question_classification"),
                    "form_provenance": trace.get("form_provenance"),
                    "section_orchestration": trace.get("section_orchestration"),
                    "source_count": len(record.get("sources") or []),
                    "answer": answer,
                    "answer_chars": len(answer),
                    "is_insufficient": record.get("grounding_status")
                    == "insufficient_evidence",
                    "is_condensed_or_source_view": metric.get("error_category")
                    not in {None, "none"},
                }
            )
        verdict, reason = _manual_verdict(username, per_user[username])
        item["manual_verdict"] = verdict
        item["manual_reason"] = reason
        rows.append(item)

    durations = [
        float(row["duration_ms"])
        for row in rows
        if isinstance(row.get("duration_ms"), (int, float))
    ]
    summary = {
        "planned": 40,
        "browser_response_rendered": sum(
            row["browser_result"] == "response_rendered" for row in rows
        ),
        "browser_timeout": sum(row["browser_result"] != "response_rendered" for row in rows),
        "history_found": sum(row["history_found"] for row in rows),
        "fully_or_partially_grounded": sum(
            row.get("grounding_status") in {"fully_grounded", "partially_grounded"}
            for row in rows
        ),
        "insufficient_evidence": sum(row.get("is_insufficient", False) for row in rows),
        "history_source_rows": sum((row.get("source_count") or 0) > 0 for row in rows),
        "history_source_note": "Catalog/citation links rendered by the web UI are not stored in the ask-history sources array.",
        "manual_verdicts": dict(Counter(row["manual_verdict"] for row in rows)),
        "p50_ms": statistics.median(durations) if durations else None,
        "p95_ms": statistics.quantiles(durations, n=100)[94] if len(durations) > 1 else None,
        "max_ms": max(durations) if durations else None,
    }
    payload = {
        "schema_version": "web-uat-40-natural-questions-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "credentials_recorded": False,
        "summary": summary,
        "cases": rows,
    }
    output = Path("reports/feature017/web-uat-40-natural-questions.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
