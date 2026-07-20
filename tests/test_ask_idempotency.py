import asyncio

import pytest

from api import ask_idempotency


@pytest.fixture(autouse=True)
def clear_idempotency_state():
    ask_idempotency._completed.clear()
    ask_idempotency._inflight.clear()
    yield
    ask_idempotency._completed.clear()
    ask_idempotency._inflight.clear()


@pytest.mark.asyncio
async def test_duplicate_request_waits_for_owner_and_reuses_result():
    mode, _ = await ask_idempotency.begin("citizen:key-12345")
    assert mode == "owner"
    mode, waiter = await ask_idempotency.begin("citizen:key-12345")
    assert mode == "wait"

    result = {"answer": "Một câu trả lời duy nhất"}
    await ask_idempotency.complete("citizen:key-12345", result)
    assert await asyncio.wait_for(waiter, timeout=1) == result

    mode, cached = await ask_idempotency.begin("citizen:key-12345")
    assert mode == "cached"
    assert cached == result


def test_idempotency_key_is_scoped_by_user_and_role():
    citizen = ask_idempotency.scoped_key(user_id="u1", role="citizen", key="same-key")
    officer = ask_idempotency.scoped_key(user_id="u1", role="officer", key="same-key")
    another_user = ask_idempotency.scoped_key(user_id="u2", role="citizen", key="same-key")
    assert len({citizen, officer, another_user}) == 3
