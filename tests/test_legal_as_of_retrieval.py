from unittest.mock import patch

import pytest

from api.models import AskRequest
from api.routers.search import _call_legal_retrieval


class _Client:
    payloads: list[dict] = []

    async def search(self, payload):
        self.payloads.append(dict(payload))
        return {"results": [], "trace": {}}


@pytest.mark.asyncio
async def test_legal_as_of_is_sent_to_every_retrieval_tier():
    _Client.payloads.clear()
    request = AskRequest(
        question="Quy định áp dụng tại thời điểm xảy ra sự việc?",
        role="citizen",
        strategy_model="model-1",
        answer_model="model-1",
        final_answer_model="model-1",
        event_date="2020-02-03",
    )
    with patch("api.routers.search.get_legal_search_client", return_value=_Client()):
        await _call_legal_retrieval(request)

    assert _Client.payloads
    assert all(payload["as_of"] == "2020-02-03" for payload in _Client.payloads)


@pytest.mark.asyncio
async def test_unselected_domain_uses_unambiguous_current_question_domain():
    _Client.payloads.clear()
    request = AskRequest(
        question="Tôi cần chuẩn bị giấy tờ gì để đăng ký khai sinh cho con?",
        role="citizen",
        strategy_model="model-1",
        answer_model="model-1",
        final_answer_model="model-1",
    )
    with patch("api.routers.search.get_legal_search_client", return_value=_Client()):
        await _call_legal_retrieval(request)

    assert _Client.payloads
    assert all(
        payload["domain"] == "ho_tich_chung_thuc"
        for payload in _Client.payloads
    )


@pytest.mark.asyncio
async def test_explicit_domain_is_not_overridden_by_auto_detection():
    _Client.payloads.clear()
    request = AskRequest(
        question="Tôi cần đăng ký khai sinh",
        role="citizen",
        domain="hanh_chinh_cong",
        strategy_model="model-1",
        answer_model="model-1",
        final_answer_model="model-1",
    )
    with patch("api.routers.search.get_legal_search_client", return_value=_Client()):
        await _call_legal_retrieval(request)

    assert all(payload["domain"] == "hanh_chinh_cong" for payload in _Client.payloads)
