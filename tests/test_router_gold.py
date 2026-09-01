"""Gold set for the conversation + legal router (no retrieval, no answer LLM).

Run:
  .venv\\Scripts\\python.exe -m pytest tests/test_router_gold.py -q
  .venv\\Scripts\\python.exe -m pytest tests/test_router_gold.py -q -k rule
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from api import conversational_orchestrator as orchestrator
from api.legal_answer_router import route_legal_answer

GOLD_PATH = Path(__file__).resolve().parent / "fixtures" / "router_gold_cases.jsonl"

CONVERSATION_ROUTES = {
    "chat_meta",
    "out_of_scope",
    "document_followup",
    "legal_query",
}
LEGAL_ROUTES = {
    "exact_article",
    "procedure_form",
    "general_legal",
    "historical",
    "clarification",
    None,
}
# Cases where procedure vs general is allowed to alias.
LEGAL_ROUTE_ALIASES = {
    "c-gen-01": {"general_legal", "procedure_form"},
    "c-gen-03": {"general_legal", "procedure_form", "clarification"},
    "c-proc-02": {"procedure_form", "general_legal"},
    "o-acl-01": {"general_legal", "procedure_form"},
}


def _load_gold() -> list[dict]:
    rows: list[dict] = []
    raw = GOLD_PATH.read_text(encoding="utf-8")
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        row["_line"] = line_no
        rows.append(row)
    return rows


GOLD = _load_gold()


def test_gold_file_is_utf8_and_covers_required_routes() -> None:
    assert GOLD_PATH.is_file(), f"missing {GOLD_PATH}"
    ids = [row["id"] for row in GOLD]
    assert len(ids) == len(set(ids)), "duplicate gold ids"
    routes = {row["expected_conversation_route"] for row in GOLD}
    assert routes == CONVERSATION_ROUTES
    roles = {row["role"] for row in GOLD}
    assert roles == {"citizen", "officer"}
    assert any(row["history"] for row in GOLD)
    assert any(row["active_document"] for row in GOLD)
    assert any(row["needs_llm"] for row in GOLD)
    assert any(not row["needs_llm"] for row in GOLD)


@pytest.mark.parametrize("row", GOLD, ids=lambda row: row["id"])
def test_gold_schema(row: dict) -> None:
    assert row["role"] in {"citizen", "officer"}
    assert isinstance(row["question"], str) and row["question"].strip()
    assert row["expected_conversation_route"] in CONVERSATION_ROUTES
    assert row.get("expected_legal_route") in LEGAL_ROUTES
    assert row["gate"] in {"rule", "llm"}
    if row["expected_conversation_route"] in {"chat_meta", "out_of_scope"}:
        assert row.get("expected_legal_route") in {None, "clarification"}
    if row["gate"] == "llm":
        assert row["needs_llm"] is True


def _decide(row: dict):
    return orchestrator.decide_conversation_turn(
        row["question"],
        history_messages=row.get("history") or [],
        active_document=row.get("active_document"),
    )


@pytest.mark.parametrize(
    "row",
    [row for row in GOLD if row["gate"] == "rule"],
    ids=lambda row: row["id"],
)
def test_rule_gate_conversation_route(row: dict) -> None:
    decision = _decide(row)
    assert decision.route == row["expected_conversation_route"], (
        f"{row['id']}: expected conversation_route={row['expected_conversation_route']!r} "
        f"got {decision.route!r} for {row['question']!r}"
    )
    if row["expected_conversation_route"] == "document_followup":
        if row.get("active_document"):
            assert decision.active_document_available
        else:
            assert not decision.active_document_available


@pytest.mark.parametrize(
    "row",
    [
        row
        for row in GOLD
        if row["gate"] == "rule"
        and row["expected_conversation_route"] == "legal_query"
        and row.get("expected_legal_route")
        and row["expected_legal_route"] != "clarification"
    ],
    ids=lambda row: row["id"],
)
def test_rule_gate_legal_route(row: dict) -> None:
    # Gold measures the lookup plan, not officer ACL. ACL is a later backend
    # gate: do not pass account_domain here or M4 will overwrite the question domain.
    routed = route_legal_answer(
        row["question"],
        remediation=True,
        role=row["role"],
    )
    allowed = LEGAL_ROUTE_ALIASES.get(row["id"], {row["expected_legal_route"]})
    assert routed.answer_route in allowed, (
        f"{row['id']}: expected legal_route in {sorted(allowed)} got {routed.answer_route!r}"
    )
    expected_domain = row.get("expected_domain")
    if expected_domain and routed.decision is not None:
        got = routed.decision.canonical_domain
        assert got in {expected_domain, "unknown"} or expected_domain in str(got), (
            f"{row['id']}: expected domain {expected_domain!r} got {got!r}"
        )


def test_confusion_summary_rule_gate() -> None:
    """Print a tiny confusion count; fail if any rule-gate conversation mismatch."""
    pairs: list[tuple[str, str]] = []
    for row in GOLD:
        if row["gate"] != "rule":
            continue
        pred = _decide(row).route
        pairs.append((row["expected_conversation_route"], pred))
    mismatches = [p for p in pairs if p[0] != p[1]]
    by_gold = Counter(g for g, _ in pairs)
    print("rule-gate gold counts:", dict(by_gold))
    print("mismatches:", mismatches)
    assert mismatches == []


@pytest.mark.parametrize(
    "row",
    [row for row in GOLD if row["needs_llm"]],
    ids=lambda row: row["id"],
)
def test_llm_cases_are_labeled_not_executed(row: dict) -> None:
    """0.5B is not called here. Label-only until a local router model is wired."""
    assert row["gate"] == "llm"
    assert row["expected_conversation_route"] in CONVERSATION_ROUTES
