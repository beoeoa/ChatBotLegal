from open_notebook.database.repository import prepare_create_payload, prepare_update_payload


def test_regular_create_payload_has_repository_timestamps():
    payload = prepare_create_payload("user_audit_log", {"id": "ignored", "action": "test"})

    assert "id" not in payload
    assert payload["action"] == "test"
    assert payload["created"]
    assert payload["updated"]


def test_crawler_run_uses_its_schema_lifecycle_fields_only():
    payload = prepare_create_payload(
        "legal_crawl_run",
        {"started_at": "2026-07-12T00:00:00Z", "status": "running"},
    )

    assert payload["started_at"] == "2026-07-12T00:00:00Z"
    assert "created" not in payload
    assert "updated" not in payload


def test_crawler_run_update_uses_its_schema_lifecycle_fields_only():
    payload = prepare_update_payload("legal_crawl_run", {"status": "completed"})

    assert payload["status"] == "completed"
    assert "updated" not in payload


def test_regular_update_payload_gets_repository_timestamp():
    payload = prepare_update_payload("user_audit_log", {"action": "test"})

    assert payload["action"] == "test"
    assert payload["updated"]
