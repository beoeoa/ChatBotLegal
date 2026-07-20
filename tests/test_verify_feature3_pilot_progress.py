from scripts.verify_feature3_pilot_progress import sse_event_names


def test_sse_event_names_never_reads_data_payloads():
    lines = [
        "event: accepted",
        'data: {"token":"must-not-be-read"}',
        "event: status",
        "event: final",
    ]
    assert sse_event_names(lines) == ["accepted", "status", "final"]
