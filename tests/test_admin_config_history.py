from surrealdb import RecordID

import pytest

from api import admin_config_history


@pytest.mark.asyncio
async def test_revision_retention_deletes_with_record_id(monkeypatch):
    async def fake_create(table, payload):
        return {"id": "admin_config_revision:new", **payload}

    calls = []

    async def fake_query(query, variables):
        calls.append((query, variables))
        if query.startswith("SELECT"):
            return [{"id": "admin_config_revision:old"}]
        return []

    async def fake_audit(**kwargs):
        return None

    monkeypatch.setattr(admin_config_history, "repo_create", fake_create)
    monkeypatch.setattr(admin_config_history, "repo_query", fake_query)
    monkeypatch.setattr(admin_config_history, "write_audit_log", fake_audit)

    await admin_config_history.record_config_revision(
        config_type="model_defaults",
        before={"default_chat_model": "model:old"},
        after={"default_chat_model": "model:new"},
        actor_user_id="user_account:admin",
        reason="Kiểm thử đổi model",
    )

    delete_query, delete_variables = calls[-1]
    assert delete_query == "DELETE $id;"
    assert isinstance(delete_variables["id"], RecordID)
    assert str(delete_variables["id"]) == "admin_config_revision:old"
