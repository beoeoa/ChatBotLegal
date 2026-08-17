from __future__ import annotations

from api.routers.live_support import (
    _consume_ws_auth_ticket,
    _issue_ws_auth_ticket,
    _legacy_ws_query_token_allowed,
)


def test_websocket_auth_ticket_is_target_bound_one_time_and_short_lived() -> None:
    token = _issue_ws_auth_ticket(
        user_id="user_account:citizen",
        role="citizen",
        ticket_id="support-ticket-1",
        domain=None,
        now=100.0,
    )

    identity = _consume_ws_auth_ticket(
        token,
        ticket_id="support-ticket-1",
        domain=None,
        now=120.0,
    )

    assert identity == ("user_account:citizen", "citizen")
    assert _consume_ws_auth_ticket(
        token,
        ticket_id="support-ticket-1",
        domain=None,
        now=121.0,
    ) is None

    wrong_target = _issue_ws_auth_ticket(
        user_id="user_account:officer",
        role="officer",
        ticket_id=None,
        domain="ho_tich_chung_thuc",
        now=200.0,
    )
    assert _consume_ws_auth_ticket(
        wrong_target,
        ticket_id=None,
        domain="dat_dai_xay_dung",
        now=201.0,
    ) is None

    expired = _issue_ws_auth_ticket(
        user_id="user_account:officer",
        role="officer",
        ticket_id=None,
        domain="ho_tich_chung_thuc",
        now=300.0,
    )
    assert _consume_ws_auth_ticket(
        expired,
        ticket_id=None,
        domain="ho_tich_chung_thuc",
        now=361.0,
    ) is None


def test_production_forbids_legacy_session_token_in_websocket_query(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    assert _legacy_ws_query_token_allowed() is False

    monkeypatch.setenv("PRODUCTION_MODE", "false")
    assert _legacy_ws_query_token_allowed() is True
