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


def test_message_history_uses_opaque_30_message_cursor_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))

    owner = "user:pagination"
    conversation = run(
        svc.create_conversation(owner_key=owner, role_context="citizen", title="65 messages")
    )
    for index in range(65):
        run(
            svc.add_message(
                conversation["id"],
                owner_key=owner,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message-{index:02d}",
                role_context="citizen",
            )
        )

    first = run(
        svc.get_conversation_message_page(
            conversation["id"], owner_key=owner, role_context="citizen"
        )
    )
    assert first is not None
    assert len(first["messages"]) == 30
    assert first["messages"][0]["content"] == "message-35"
    assert first["messages"][-1]["content"] == "message-64"
    assert first["has_more"] is True
    assert first["next_cursor"] and "pagination" not in first["next_cursor"]

    second = run(
        svc.get_conversation_message_page(
            conversation["id"],
            owner_key=owner,
            role_context="citizen",
            before=first["next_cursor"],
        )
    )
    assert second is not None
    assert [message["content"] for message in second["messages"]] == [
        f"message-{index:02d}" for index in range(5, 35)
    ]
    assert second["has_more"] is True

    third = run(
        svc.get_conversation_message_page(
            conversation["id"],
            owner_key=owner,
            role_context="citizen",
            before=second["next_cursor"],
        )
    )
    assert third is not None
    assert [message["content"] for message in third["messages"]] == [
        f"message-{index:02d}" for index in range(5)
    ]
    assert third["has_more"] is False
    assert third["next_cursor"] is None


def test_message_cursor_is_scoped_to_its_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))
    owner = "user:pagination-scope"
    first = run(svc.create_conversation(owner_key=owner, role_context="citizen"))
    second = run(svc.create_conversation(owner_key=owner, role_context="citizen"))
    cursor = svc._encode_message_cursor(first["id"], 30)

    try:
        run(
            svc.get_conversation_message_page(
                second["id"], owner_key=owner, role_context="citizen", before=cursor
            )
        )
    except ValueError as exc:
        assert "Invalid conversation message cursor" in str(exc)
    else:
        raise AssertionError("A cursor from another conversation must be rejected")


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


def test_duplicate_answer_enriches_existing_message_with_v1_presentation(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_path / "conversations"))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))
    owner = "user:presentation-history"
    conversation = run(
        svc.create_conversation(owner_key=owner, role_context="citizen")
    )
    first = run(
        svc.add_message(
            conversation["id"],
            owner_key=owner,
            role="assistant",
            content="Kết luận đã kiểm chứng.",
            status="complete",
        )
    )
    enriched = run(
        svc.add_message(
            conversation["id"],
            owner_key=owner,
            role="assistant",
            content="Kết luận đã kiểm chứng.",
            status="complete",
            attachments=[{
                "kind": "legal_answer_presentation",
                "value": {
                    "presentation_version": "legal-answer-v1",
                    "answer_route": "general_legal",
                    "verification_label": "Đã kiểm chứng từ 1 nguồn",
                    "sections": {"short_answer": "Kết luận đã kiểm chứng."},
                },
            }],
        )
    )

    assert enriched["id"] == first["id"]
    assert enriched["presentation_version"] == "legal-answer-v1"
    detail = run(
        svc.get_conversation(
            conversation["id"], owner_key=owner, role_context="citizen"
        )
    )
    assert detail["message_count"] == 1
    assert detail["messages"][0]["sections"]["short_answer"] == "Kết luận đã kiểm chứng."


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


def test_recovery_does_not_append_historical_answer_to_completed_turn(monkeypatch):
    """A repeated question must not revive an older legacy answer."""

    async def use_surreal():
        return True

    queries = []

    async def query(sql, params):
        queries.append((sql, params))
        if "conversation_message" in sql:
            return [
                {
                    "id": "conversation_message:user",
                    "sender_role": "user",
                    "content": "Câu hỏi lặp lại",
                    "created_at": "2026-07-25T01:00:00Z",
                },
                {
                    "id": "conversation_message:assistant",
                    "sender_role": "assistant",
                    "content": "Câu trả lời structured đã kiểm chứng",
                    "status": "complete",
                    "created_at": "2026-07-25T01:00:01Z",
                },
            ]
        return [
            {
                "answer": "Câu trả lời legacy cũ không được phục hồi",
                "created": "2026-07-24T01:00:00Z",
            }
        ]

    async def unexpected_add(*args, **kwargs):
        raise AssertionError("Completed turn must not receive another assistant answer")

    monkeypatch.setattr(svc, "_use_surreal", use_surreal)
    monkeypatch.setattr(svc, "repo_query", query)
    monkeypatch.setattr(svc, "add_message", unexpected_add)

    restored = run(
        svc.recover_missing_assistant_message(
            "completed",
            owner_key="user:citizen",
            real_user_id="citizen",
            role_context="citizen",
        )
    )

    assert restored is False
    assert len(queries) == 1


def test_recovery_restores_only_turn_without_following_assistant(monkeypatch):
    async def use_surreal():
        return True

    messages = [
        {
            "id": "conversation_message:user-1",
            "sender_role": "user",
            "content": "Câu hỏi đã trả lời",
            "created_at": "2026-07-25T01:00:00Z",
        },
        {
            "id": "conversation_message:assistant-1",
            "sender_role": "assistant",
            "content": "Câu trả lời hiện tại",
            "status": "complete",
            "created_at": "2026-07-25T01:00:01Z",
        },
        {
            "id": "conversation_message:user-2",
            "sender_role": "user",
            "content": "Câu hỏi bị thiếu đáp án",
            "created_at": "2026-07-25T01:01:00Z",
        },
    ]
    history_questions = []

    async def query(sql, params):
        if "conversation_message" in sql:
            return messages
        history_questions.append(params["question"])
        return [{"answer": "Đáp án được khôi phục"}]

    restored_payloads = []

    async def add(*args, **kwargs):
        restored_payloads.append(kwargs)
        return {"id": "conversation_message:restored", **kwargs}

    monkeypatch.setattr(svc, "_use_surreal", use_surreal)
    monkeypatch.setattr(svc, "repo_query", query)
    monkeypatch.setattr(svc, "add_message", add)

    restored = run(
        svc.recover_missing_assistant_message(
            "missing",
            owner_key="user:citizen",
            real_user_id="citizen",
            role_context="citizen",
        )
    )

    assert restored is True
    assert history_questions == ["Câu hỏi bị thiếu đáp án"]
    assert [item["content"] for item in restored_payloads] == ["Đáp án được khôi phục"]
