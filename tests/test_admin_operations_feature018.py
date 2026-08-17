from api.admin_operations_service import project_operational_alerts


def test_alert_projection_prioritizes_business_impact_and_exposes_one_filtered_action():
    observed_at = "2026-08-13T10:00:00+07:00"
    alerts = project_operational_alerts([
        {
            "code": "faq.pending",
            "severity": "info",
            "title": "FAQ chờ xác nhận",
            "description": "Chưa công khai.",
            "count": 7,
            "href": "/faq-management?public_state=pending",
            "observed_at": observed_at,
        },
        {
            "code": "support.overdue",
            "severity": "critical",
            "title": "Phiên hỗ trợ quá SLA",
            "description": "Người dân đang chờ quá thời hạn phục vụ.",
            "count": 2,
            "href": None,
            "observed_at": observed_at,
        },
        {
            "code": "import.failed",
            "severity": "critical",
            "title": "Tác vụ nạp dữ liệu bị lỗi",
            "description": "Văn bản mới chưa vào hàng rà soát.",
            "count": 1,
            "href": "/legal-import?tab=proposals&status=needs_attention",
            "observed_at": observed_at,
        },
    ])

    assert [item["code"] for item in alerts] == [
        "support.overdue", "import.failed", "faq.pending"
    ]
    assert alerts[0]["module"] == "support"
    assert alerts[0]["state_label"] == "Cần xử lý ngay"
    assert alerts[0]["impact"] == "Người dân đang chờ quá thời hạn phục vụ."
    assert alerts[0]["primary_action"] == {
        "label": "Mở danh sách phiên quá SLA",
        "href": "/admin/support?status=overdue",
    }
    assert alerts[1]["worklist_filters"] == {
        "tab": "proposals", "status": "needs_attention"
    }
    assert alerts[1]["technical_detail"]["collapsed_by_default"] is True


def test_alert_projection_has_no_technical_action_copy_and_covers_release_modules():
    observed_at = "2026-08-13T10:00:00+07:00"
    raw = [
        ("legal.unknown_validity", "lifecycle"),
        ("health.embedding.degraded", "vector"),
        ("candidate.pending", "import"),
        ("forms.pending", "forms"),
        ("faq.pending", "faq"),
        ("models.not_ready", "provider"),
    ]
    alerts = project_operational_alerts([{
        "code": code,
        "severity": "warning",
        "title": f"Cảnh báo {code}",
        "description": "Ảnh hưởng nghiệp vụ cần được rà soát.",
        "count": 1,
        "href": None,
        "observed_at": observed_at,
    } for code, _ in raw])

    by_code = {item["code"]: item for item in alerts}
    assert {by_code[code]["module"] for code, _ in raw} == {
        "lifecycle", "vector", "import", "forms", "faq", "provider"
    }
    for code, _ in raw:
        item = by_code[code]
        assert item["primary_action"]["label"]
        assert not any(term in item["primary_action"]["label"].casefold() for term in (
            "chunk", "vector id", "fingerprint", "json", "sql"
        ))
        assert item["technical_detail"]["code"] == code
