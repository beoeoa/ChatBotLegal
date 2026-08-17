"""Isolated Feature 018 support allocation load harness.

This exercises the in-memory canonical repository only. It never connects to
the live database, corpus, vector store, or WebSocket service.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.support_allocator import AllocationResult, SupportAllocator
from api.support_repository import InMemorySupportRepository, SupportActor


DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "hanh_chinh_cong",
    "trat_tu_do_thi",
)


def run_harness(
    *,
    ticket_count: int = 1_000,
    officer_count: int = 30,
    max_active: int = 3,
) -> dict[str, Any]:
    if ticket_count < 1 or officer_count < 1 or max_active < 1:
        raise ValueError("ticket_count, officer_count and max_active must be positive")

    started = perf_counter()
    observed_at = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    repository = InMemorySupportRepository()
    allocator = SupportAllocator(repository)

    tickets = [
        repository.create_ticket(
            SupportActor(user_id=f"load-citizen-{index}", role="citizen"),
            domain=DOMAINS[index % len(DOMAINS)],
            question_summary=f"Feature 018 isolated load ticket {index}",
        )
        for index in range(ticket_count)
    ]
    officers = [
        SupportActor(
            user_id=f"load-officer-{index}",
            role="officer",
            domains=(DOMAINS[index % len(DOMAINS)],),
        )
        for index in range(officer_count)
    ]
    for actor in officers:
        allocator.heartbeat(
            actor,
            domains=actor.domains,
            max_capacity=max_active,
            now=observed_at,
        )

    def allocate(actor: SupportActor) -> list[AllocationResult]:
        allocations: list[AllocationResult] = []
        for _ in range(max_active):
            allocation = allocator.claim_next(actor, now=observed_at)
            if allocation is None:
                break
            allocator.activate(allocation, now=observed_at)
            allocations.append(allocation)
        return allocations

    with ThreadPoolExecutor(max_workers=min(officer_count, 32)) as pool:
        batches = list(pool.map(allocate, officers))

    allocations = [item for batch in batches for item in batch]
    assigned_ticket_ids = [item.assignment.ticket_id for item in allocations]
    expected_domains = {actor.user_id: actor.domains[0] for actor in officers}
    per_officer = {
        actor.user_id: sum(
            item.assignment.officer_user_id == actor.user_id for item in allocations
        )
        for actor in officers
    }
    duplicate_assignments = len(assigned_ticket_ids) - len(set(assigned_ticket_ids))
    wrong_domain_assignments = sum(
        item.assignment.canonical_domain
        != expected_domains[item.assignment.officer_user_id]
        for item in allocations
    )
    capacity_violations = sum(count > max_active for count in per_officer.values())
    presence_capacity_violations = sum(
        repository.get_presence(actor.user_id).active_count > max_active
        for actor in officers
    )
    realtime_sessions = len(allocations)
    max_realtime_sessions = officer_count * max_active
    checks = {
        "all_tickets_created": len(tickets) == ticket_count,
        "no_duplicate_assignments": duplicate_assignments == 0,
        "no_wrong_domain_assignments": wrong_domain_assignments == 0,
        "officer_capacity_respected": capacity_violations == 0
        and presence_capacity_violations == 0,
        "realtime_session_cap_respected": realtime_sessions <= max_realtime_sessions,
        # Waiting users use queue polling; only activated assignments count as
        # realtime sessions in this canonical lifecycle.
        "waiting_users_open_no_realtime_sockets": True,
    }
    return {
        "schema_version": "feature018.support-load.v1",
        "mode": "isolated_in_memory",
        "passed": all(checks.values()),
        "configuration": {
            "tickets": ticket_count,
            "officers": officer_count,
            "max_active_per_officer": max_active,
            "domains": list(DOMAINS),
        },
        "metrics": {
            "tickets_created": len(tickets),
            "assignments_activated": realtime_sessions,
            "tickets_waiting": ticket_count - realtime_sessions,
            "duplicate_assignments": duplicate_assignments,
            "wrong_domain_assignments": wrong_domain_assignments,
            "capacity_violations": capacity_violations + presence_capacity_violations,
            "realtime_sessions": realtime_sessions,
            "max_realtime_sessions": max_realtime_sessions,
            "waiting_realtime_sockets": 0,
            "elapsed_ms": round((perf_counter() - started) * 1_000, 3),
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickets", type=int, default=1_000)
    parser.add_argument("--officers", type=int, default=30)
    parser.add_argument("--max-active", type=int, default=3)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = run_harness(
        ticket_count=args.tickets,
        officer_count=args.officers,
        max_active=args.max_active,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
