from __future__ import annotations

import pytest
from types import SimpleNamespace

from api.legal_answer_trust_log import build_public_trust_projection
from api.legal_provider_privacy import ProviderEgressBlocked, prepare_provider_egress


SENSITIVE = (
    "Tôi là Nguyễn Văn A, CCCD 012345678901, điện thoại 0912345678, "
    "email nguyenvana@example.com, địa chỉ 12 Lạch Tray. Tôi cần làm khai sinh."
)


def test_cloud_egress_redacts_pii_and_audit_contains_hashes_only():
    decision = prepare_provider_egress(
        SENSITIVE,
        provider="openrouter",
        model="nvidia/nemotron-3-nano-30b-a3b:free",
    )

    assert decision.mode == "cloud"
    assert decision.redaction_applied is True
    assert not any(
        secret in decision.text
        for secret in (
            "Nguyễn Văn A",
            "012345678901",
            "0912345678",
            "nguyenvana@example.com",
            "12 Lạch Tray",
        )
    )
    assert set(decision.redaction_categories) >= {
        "person_name",
        "citizen_id",
        "phone",
        "email",
        "address",
    }
    assert "text" not in decision.audit_record
    assert "question" not in decision.audit_record
    assert "012345678901" not in str(decision.audit_record)


def test_local_provider_does_not_rewrite_or_egress_text():
    decision = prepare_provider_egress(
        SENSITIVE, provider="ollama", model="qwen2.5:3b"
    )
    assert decision.mode == "local"
    assert decision.text == SENSITIVE
    assert decision.redaction_applied is False


def test_cloud_egress_fails_closed_when_sensitive_free_text_cannot_be_redacted():
    with pytest.raises(ProviderEgressBlocked, match="pii_redaction_incomplete"):
        prepare_provider_egress(
            "Hồ sơ bệnh án cá nhân: nội dung tự do không có cấu trúc.",
            provider="openai",
            model="gpt-5-mini",
        )


def test_public_provider_labels_never_expose_url_credentials_or_query_secrets():
    decision = prepare_provider_egress(
        "Điều kiện đăng ký khai sinh là gì?",
        provider="https://user:secret@api.example.test/v1",
        model="models/legal?api_key=secret-token",
    )
    projection = build_public_trust_projection(
        trace={},
        citations=[],
        answer_mode="normal",
        provider_label=decision.provider_label,
        provider_mode=decision.mode,
        model_label=decision.model_label,
        redaction_applied=decision.redaction_applied,
    )
    public = projection["generation_provenance"]

    assert public["mode"] == "cloud"
    assert "secret" not in str(public).casefold()
    assert "api_key" not in str(public).casefold()
    assert "http" not in str(public).casefold()


def test_search_router_redacts_when_any_participating_model_is_cloud():
    from api.routers.search import _prepare_ask_provider_egress

    decision = _prepare_ask_provider_egress(
        SENSITIVE,
        SimpleNamespace(provider="ollama", name="local-strategy"),
        SimpleNamespace(provider="openrouter", name="cloud-answer"),
        SimpleNamespace(provider="ollama", name="local-final"),
    )

    assert decision.mode == "cloud"
    assert decision.provider_label == "openrouter"
    assert "012345678901" not in decision.text


def test_provider_disclosure_flag_keeps_mode_but_hides_specific_labels(monkeypatch):
    monkeypatch.setenv("LEGAL_PROVIDER_DISCLOSURE_ENABLED", "false")
    projection = build_public_trust_projection(
        trace={},
        citations=[],
        answer_mode="normal",
        provider_label="openrouter",
        provider_mode="cloud",
        model_label="vendor/private-model",
    )["generation_provenance"]

    assert projection == {
        "mode": "cloud",
        "provider_label": "configured-provider",
        "model_label": None,
        "redaction_applied": False,
    }
