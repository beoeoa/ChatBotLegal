import pytest

from scripts.apply_golden294_live_import_116 import next_action


@pytest.mark.parametrize(
    ("status", "import_status", "expected"),
    [
        ("pending", "", "review"),
        ("approved", "validation_failed", "enqueue"),
        ("import_failed", "failed", "enqueue"),
        ("import_queued", "queued", "poll"),
        ("approved", "running", "poll"),
        ("imported", "completed", "verify"),
        ("rejected", "", "fail"),
    ],
)
def test_next_action_is_fail_closed(status, import_status, expected):
    assert next_action(status, import_status) == expected
