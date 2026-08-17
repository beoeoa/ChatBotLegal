from scripts.load_test_support import run_harness


def test_support_load_harness_enforces_assignment_invariants():
    report = run_harness(ticket_count=75, officer_count=10, max_active=3)

    assert report["passed"] is True
    assert report["metrics"]["assignments_activated"] == 30
    assert report["metrics"]["duplicate_assignments"] == 0
    assert report["metrics"]["wrong_domain_assignments"] == 0
    assert report["metrics"]["waiting_realtime_sockets"] == 0
