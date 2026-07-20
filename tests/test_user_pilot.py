from datetime import datetime, timedelta, timezone

from api.user_service import (
    DEPARTMENT_TO_DOMAINS,
    _profile_payload,
    _sanitize_profile_record,
    _session_touch_due,
    get_domains_for_department,
)


def test_department_to_domains_mapping():
    """Department mapping uses the five pilot domain keys."""
    assert DEPARTMENT_TO_DOMAINS["Tư pháp - Hộ tịch"] == ["ho_tich_chung_thuc"]
    assert DEPARTMENT_TO_DOMAINS["Địa chính - Xây dựng"] == ["dat_dai_xay_dung"]
    assert DEPARTMENT_TO_DOMAINS["Văn hóa - Xã hội"] == ["an_sinh_y_te_giao_duc"]
    assert DEPARTMENT_TO_DOMAINS["Hành chính công"] == ["hanh_chinh_cong"]
    assert DEPARTMENT_TO_DOMAINS["Trật tự đô thị"] == ["trat_tu_do_thi"]
    assert DEPARTMENT_TO_DOMAINS["Khiếu nại - Tố cáo - Xử phạt"] == ["khieu_nai_to_cao_xu_phat"]


def test_get_domains_for_department():
    """Department labels map to domain filters used by retrieval and live support."""
    assert get_domains_for_department("Tư pháp - Hộ tịch") == ["ho_tich_chung_thuc"]
    assert get_domains_for_department("Địa chính - Xây dựng") == ["dat_dai_xay_dung"]
    assert get_domains_for_department("Văn hóa - Xã hội") == ["an_sinh_y_te_giao_duc"]
    assert get_domains_for_department("Hành chính công") == ["hanh_chinh_cong"]
    assert get_domains_for_department("Trật tự đô thị") == ["trat_tu_do_thi"]
    assert get_domains_for_department("Random Department") == []
    assert get_domains_for_department(None) == []


def test_profile_payload_auto_mapping():
    """_profile_payload maps department to allowed_domains when not explicitly set."""
    payload = {
        "full_name": "Test Officer",
        "department": "Tư pháp - Hộ tịch",
    }
    result = _profile_payload(payload)
    assert result["ward"] == "Phường Lê Chân, Hải Phòng"
    assert result["department"] == "Tư pháp - Hộ tịch"
    assert result["allowed_domains"] == ["ho_tich_chung_thuc"]

    payload_explicit = {
        "full_name": "Special Officer",
        "department": "Tư pháp - Hộ tịch",
        "allowed_domains": ["ho_tich_chung_thuc"],
    }
    result_explicit = _profile_payload(payload_explicit)
    assert result_explicit["allowed_domains"] == ["ho_tich_chung_thuc"]

    payload_custom_ward = {
        "full_name": "Ward Admin",
        "ward": "Phường Cát Dài, Hải Phòng",
        "department": "Địa chính - Xây dựng",
    }
    result_custom_ward = _profile_payload(payload_custom_ward)
    assert result_custom_ward["ward"] == "Phường Cát Dài, Hải Phòng"
    assert result_custom_ward["allowed_domains"] == ["dat_dai_xay_dung"]


def test_sanitize_profile_record():
    """_sanitize_profile_record defaults ward and preserves pilot metadata."""
    raw_record = {
        "full_name": "Officer A",
        "department": "Văn hóa - Xã hội",
    }
    sanitized = _sanitize_profile_record(raw_record)
    assert sanitized["ward"] == "Phường Lê Chân, Hải Phòng"
    assert sanitized["allowed_domains"] == []
    assert sanitized["department"] == "Văn hóa - Xã hội"


def test_session_touch_is_debounced(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_SESSION_TOUCH_SECONDS", "600")
    now = datetime(2026, 7, 15, 2, 30, tzinfo=timezone.utc)
    assert _session_touch_due(now - timedelta(minutes=11), now=now) is True
    assert _session_touch_due(now - timedelta(minutes=2), now=now) is False
    assert _session_touch_due("invalid", now=now) is True
