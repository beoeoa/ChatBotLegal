from scripts.configure_feature3_pilot import first_row, record_id, safe_fingerprint, without_id


def test_record_id_requires_a_surreal_identifier():
    assert record_id("model:chat") == "model:chat"
    try:
        record_id("missing")
    except ValueError:
        pass
    else:
        raise AssertionError("plain values must not be accepted as record IDs")


def test_payload_preserves_fields_but_not_record_id():
    payload = without_id({"id": "credential:main", "api_key": "encrypted-value", "provider": "openai"})
    assert payload == {"api_key": "encrypted-value", "provider": "openai"}


def test_result_helpers_handle_supported_shapes_without_secret_content():
    assert first_row({"id": "model:chat"}) == {"id": "model:chat"}
    assert first_row([[{"id": "model:chat"}]]) == {"id": "model:chat"}
    assert first_row([]) is None
    assert safe_fingerprint("model:chat", "credential:main") == safe_fingerprint(
        "model:chat", "credential:main"
    )
