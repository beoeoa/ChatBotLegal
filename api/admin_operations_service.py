"""Normalized, no-tech operational alerts for the Feature 018 Admin home."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit


SEVERITY_SCORE = {"critical": 300, "warning": 200, "info": 100}
MODULE_SCORE = {
    "support": 90,
    "lifecycle": 85,
    "import": 80,
    "vector": 75,
    "forms": 70,
    "faq": 65,
    "provider": 60,
    "security": 55,
    "service": 50,
}


def _module(code: str) -> str:
    if code.startswith("support."):
        return "support"
    if code.startswith("legal."):
        return "lifecycle"
    if code.startswith("health.embedding") or "vector" in code:
        return "vector"
    if code.startswith(("import.", "candidate.", "knowledge.ocr")):
        return "import"
    if code.startswith("forms."):
        return "forms"
    if code.startswith("faq."):
        return "faq"
    if code.startswith("models."):
        return "provider"
    if code.startswith("users."):
        return "security"
    return "service"


ACTION_POLICY: dict[str, tuple[str, str]] = {
    "support.overdue": ("Mở danh sách phiên quá SLA", "/admin/support?status=overdue"),
    "support.unassigned": ("Mở danh sách chưa phân công", "/admin/support?status=queued"),
    "import.failed": ("Mở tác vụ cần xử lý", "/legal-import?tab=proposals&status=needs_attention"),
    "candidate.pending": ("Rà soát đề xuất văn bản", "/legal-import?tab=proposals&status=pending"),
    "candidate.unverified_import": ("Rà soát bản nhập chưa xác nhận", "/legal-import?tab=proposals&status=imported"),
    "legal.unknown_validity": ("Rà soát hiệu lực chưa rõ", "/legal-management?validity_status=unknown"),
    "legal.missing_source": ("Bổ sung nguồn chính thức", "/legal-management?data_quality=missing_source"),
    "legal.missing_metadata": ("Bổ sung thông tin văn bản", "/legal-management?data_quality=missing_metadata"),
    "legal.zero_chunks": ("Mở danh sách chưa sẵn sàng tra cứu", "/legal-management?data_quality=zero_chunks"),
    "forms.pending": ("Xác minh nguồn biểu mẫu", "/legal-import?tab=forms&form_status=candidate_pending_review"),
    "forms.missing_files": ("Mở danh sách biểu mẫu thiếu nguồn", "/legal-import?tab=forms&form_status=missing_source"),
    "faq.pending": ("Xác nhận FAQ chờ xử lý", "/faq-management?public_state=pending"),
    "models.not_ready": ("Mở hướng dẫn cấu hình AI", "/settings/api-keys?step=provider"),
    "users.locked": ("Rà soát tài khoản bị khóa", "/users?status=locked"),
}

MODULE_ACTION: dict[str, tuple[str, str]] = {
    "support": ("Mở danh sách hỗ trợ", "/admin/support"),
    "lifecycle": ("Mở danh sách văn bản", "/legal-management"),
    "vector": ("Kiểm tra sẵn sàng tra cứu", "/legal-management?vector_state=needs_review"),
    "import": ("Mở hàng công việc nhập liệu", "/legal-import"),
    "forms": ("Mở quản trị biểu mẫu", "/legal-import?tab=forms"),
    "faq": ("Mở quản trị FAQ", "/faq-management"),
    "provider": ("Mở hướng dẫn cấu hình AI", "/settings/api-keys"),
    "security": ("Mở quản lý tài khoản", "/users"),
    "service": ("Mở tổng quan hệ thống", "/admin"),
}


def _action(code: str, module: str, href: str | None) -> tuple[str, str]:
    if code in ACTION_POLICY:
        label, policy_href = ACTION_POLICY[code]
        return label, href or policy_href
    label, default_href = MODULE_ACTION[module]
    return label, href or default_href


def project_operational_alerts(raw_items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project server alerts to one stable business-action contract."""

    projected = []
    for raw in raw_items:
        count = max(0, int(raw.get("count") or 0))
        if count == 0:
            continue
        code = str(raw.get("code") or "operations.unknown")
        severity = str(raw.get("severity") or "warning")
        if severity not in SEVERITY_SCORE:
            severity = "warning"
        module = _module(code)
        action_label, href = _action(code, module, raw.get("href"))
        split = urlsplit(href)
        priority = SEVERITY_SCORE[severity] + MODULE_SCORE[module]
        observed_at = str(raw.get("observed_at") or "")
        alert_id = hashlib.sha256(f"{code}|{observed_at}".encode("utf-8")).hexdigest()[:24]
        projected.append({
            "id": alert_id,
            "code": code,
            "module": module,
            "severity": severity,
            "priority": priority,
            "state_label": {
                "critical": "Cần xử lý ngay",
                "warning": "Cần xử lý hôm nay",
                "info": "Cần theo dõi",
            }[severity],
            "title": str(raw.get("title") or "Công việc cần xử lý"),
            "impact": str(raw.get("description") or "Có thể ảnh hưởng đến quy trình nghiệp vụ."),
            "count": count,
            "primary_action": {"label": action_label, "href": href},
            "worklist_filters": dict(parse_qsl(split.query, keep_blank_values=True)),
            "observed_at": observed_at,
            "technical_detail": {
                "collapsed_by_default": True,
                "code": code,
                "source_href": raw.get("href"),
            },
        })
    return sorted(projected, key=lambda item: (-item["priority"], item["code"]))

