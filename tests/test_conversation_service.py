import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import conversation_service as svc


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_create_list_get_send_soft_delete_and_context_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))

    owner_a = "legacy:citizen"
    owner_b = "legacy:officer"

    created = run(svc.create_conversation(owner_key=owner_a, role_context="citizen", title="Chat A", domain="ho_tich_chung_thuc"))
    assert created["id"]
    assert created["title"] == "Chat A"
    assert created["role_context"] == "citizen"
    assert created["message_count"] == 0

    listed = run(svc.list_conversations(owner_key=owner_a, role_context="citizen"))
    assert any(item["id"] == created["id"] for item in listed)

    # Owner B must not see owner A conversation.
    listed_b = run(svc.list_conversations(owner_key=owner_b, role_context="officer"))
    assert all(item["id"] != created["id"] for item in listed_b)
    missing = run(svc.get_conversation(created["id"], owner_key=owner_b, role_context="officer"))
    assert missing is None

    user_msg = run(svc.add_message(created["id"], owner_key=owner_a, role="user", content="Đăng ký khai sinh ở Lê Chân cần gì?", role_context="citizen"))
    assert user_msg["role"] == "user"
    assistant_msg = run(
        svc.add_message(
            created["id"],
            owner_key=owner_a,
            role="assistant",
            content="Kết luận ngắn: nộp tại UBND phường nơi cư trú.",
            role_context="citizen",
            status="complete",
            citations=[{"law_number": "60/2014/QH13", "article_number": "14"}],
            grounding_status="fully_grounded",
        )
    )
    assert assistant_msg["status"] == "complete"
    assert assistant_msg["citations"][0]["law_number"] == "60/2014/QH13"

    detail = run(svc.get_conversation(created["id"], owner_key=owner_a, role_context="citizen"))
    assert detail is not None
    assert detail["message_count"] == 2
    assert detail["title"] != "Cuộc trò chuyện mới"  # auto title from first user message when default

    # Follow-up context is token-limited and prioritizes prior conclusions/citations.
    context = run(svc.get_followup_context(created["id"], owner_key=owner_a, role_context="citizen", max_chars=2000, max_messages=8))
    assert len(context) >= 2
    joined = "\n".join(item["content"] for item in context)
    assert "khai sinh" in joined.lower() or "Kết luận" in joined
    assert "60/2014/QH13" in joined

    # Rename
    renamed = run(svc.rename_conversation(created["id"], title="Hộ tịch Lê Chân", owner_key=owner_a, role_context="citizen"))
    assert renamed and renamed["title"] == "Hộ tịch Lê Chân"

    # Soft delete hides from list/get for owner.
    ok = run(svc.soft_delete_conversation(created["id"], owner_key=owner_a, role_context="citizen"))
    assert ok is True
    listed_after = run(svc.list_conversations(owner_key=owner_a, role_context="citizen"))
    assert all(item["id"] != created["id"] for item in listed_after)
    gone = run(svc.get_conversation(created["id"], owner_key=owner_a, role_context="citizen"))
    assert gone is None


def test_build_token_limited_context_prefers_recent_conclusions():
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"Câu hỏi dài số {i} " + ("x" * 400)})
        messages.append({
            "role": "assistant",
            "content": f"Kết luận số {i}: " + ("y" * 800),
            "citations": [{"law_number": f"{i}/2015/NĐ-CP", "article_number": str(i)}],
            "grounding_status": "fully_grounded",
        })
    ctx = svc.build_token_limited_context(messages, max_chars=2500, max_messages=6)
    assert 1 <= len(ctx) <= 6
    # newest first preference means last conclusions remain
    text = "\n".join(item["content"] for item in ctx)
    assert "Kết luận số 19" in text or "19/2015/NĐ-CP" in text
    assert len(text) <= 2600


def test_add_message_preserves_answer_when_snapshot_schema_rejects_nested_field(monkeypatch):
    async def use_surreal():
        return True

    async def existing_conversation(*args, **kwargs):
        return {"id": "conversation:test", "title": "Test", "message_count": 1}

    calls = []

    async def create(table, payload):
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError(
                "Found field 'citations_snapshot[0].article_number', "
                "but no such field exists"
            )
        return [{"id": "conversation_message:test", **payload}]

    monkeypatch.setattr(svc, "_use_surreal", use_surreal)
    monkeypatch.setattr(svc, "get_conversation", existing_conversation)
    monkeypatch.setattr(svc, "repo_create", create)
    monkeypatch.setattr(svc, "repo_update", lambda *args, **kwargs: asyncio.sleep(0))

    message = run(
        svc.add_message(
            "test",
            owner_key="user:test",
            role="assistant",
            content="Kết luận được lưu đầy đủ.",
            real_user_id="user:test",
            status="complete",
            citations=[{"law_number": "60/2014/QH13", "article_number": "16"}],
        )
    )

    assert message["content"] == "Kết luận được lưu đầy đủ."
    assert message["status"] == "complete"
    assert message["citations"][0]["article_number"] == "16"
    assert len(calls) == 2
    assert "citations_snapshot" in calls[0]
    assert "citations_snapshot" not in calls[1]


def test_duplicate_completed_assistant_snapshot_is_not_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))

    owner = "user:citizen-1"
    conversation = run(
        svc.create_conversation(
            owner_key=owner,
            role_context="citizen",
            title="Chat trùng lượt",
        )
    )
    first = run(
        svc.add_message(
            conversation["id"],
            owner_key=owner,
            role="assistant",
            content="Kết luận duy nhất.",
            status="complete",
        )
    )
    second = run(
        svc.add_message(
            conversation["id"],
            owner_key=owner,
            role="assistant",
            content="Kết luận duy nhất.",
            status="complete",
        )
    )
    detail = run(svc.get_conversation(conversation["id"], owner_key=owner, role_context="citizen"))
    assert first["id"] == second["id"]
    assert detail["message_count"] == 1


def test_duplicate_assistant_error_is_not_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))

    owner = "user:citizen-error"
    conversation = run(
        svc.create_conversation(
            owner_key=owner,
            role_context="citizen",
            title="Chat lỗi",
        )
    )
    first = run(
        svc.add_message(
            conversation["id"],
            owner_key=owner,
            role="assistant",
            content="Yêu cầu quá thời gian 60 giây.",
            status="error",
        )
    )
    second = run(
        svc.add_message(
            conversation["id"],
            owner_key=owner,
            role="assistant",
            content="Yêu cầu quá thời gian 60 giây.",
            status="error",
        )
    )
    detail = run(
        svc.get_conversation(
            conversation["id"],
            owner_key=owner,
            role_context="citizen",
        )
    )
    assert first["id"] == second["id"]
    assert detail["message_count"] == 1
