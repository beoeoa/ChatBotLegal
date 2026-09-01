"""Phase B eval harness for the private router dataset (rule + fail-open).

Default: no Ollama. Set ROUTER_DATASET_RUN_LLM=1 to hit qwen2.5:0.5b.

Hard fails only:
  (a) any gold legal_query predicted as chat_meta / out_of_scope
  (b) dataset size < 1000
  (c) missing train/dev/holdout splits
  (d) LLM schema contains ACL / department / effectivity fields

Overall accuracy and fallback rate are printed, not gated at 99.5%.
Holdout is eval-only; this module does not tune prompts from holdout.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import pytest
from loguru import logger

logger.remove()

from api.pipeline_contracts import (
    LLM_ROUTER_FORBIDDEN_KEYS,
    AuthorizationScopeV1,
    PipelineDecisionV1,
    build_authorization_scope_v1,
    decide_pipeline,
    llm_schema_contains_acl_fields,
    strip_llm_router_forbidden_fields,
)
from api.routers import search as search_router
from api.unified_router import decide_router

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "router_dataset_v1"
SPLITS = ("train", "dev", "holdout")
EVAL_SPLITS = ("dev", "holdout")
CONVERSATION_ROUTES = {
    "legal_query",
    "document_followup",
    "chat_meta",
    "out_of_scope",
}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _load_split(split: str) -> list[dict]:
    path = FIXTURE_DIR / f"{split}.jsonl"
    rows: list[dict] = []
    raw = path.read_text(encoding="utf-8")
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["_split_file"] = split
        rows.append(row)
    return rows


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def test_dataset_files_exist_and_meet_size_gate() -> None:
    missing = [name for name in SPLITS if not (FIXTURE_DIR / f"{name}.jsonl").is_file()]
    assert missing == [], f"missing splits: {missing}"
    counts = {name: len(_load_split(name)) for name in SPLITS}
    total = sum(counts.values())
    print("dataset counts:", counts, "total", total)
    assert total >= 1000, f"dataset size {total} < 1000"
    assert all(count > 0 for count in counts.values()), counts


def test_dataset_schema_and_no_question_leakage() -> None:
    seen_questions: dict[str, str] = {}
    for split in SPLITS:
        for row in _load_split(split):
            assert row["split"] == split
            assert row["role"] in {"citizen", "officer"}
            assert isinstance(row["question"], str) and row["question"].strip()
            assert row["expected_conversation_route"] in CONVERSATION_ROUTES
            assert isinstance(row.get("tags"), list)
            assert isinstance(row.get("needs_llm"), bool)
            question = row["question"]
            previous = seen_questions.get(question)
            if previous and previous != split:
                raise AssertionError(
                    f"question leaked across splits {previous} -> {split}: {question!r}"
                )
            seen_questions[question] = split


def test_llm_schema_must_not_contain_acl_fields() -> None:
    hits: list[str] = []
    for schema in (
        search_router.CONVERSATION_ROUTER_SCHEMA,
        getattr(search_router, "CONVERSATION_ROUTER_SHORT_SCHEMA", None),
    ):
        hits.extend(llm_schema_contains_acl_fields(schema))
    properties = set((search_router.CONVERSATION_ROUTER_SCHEMA or {}).get("properties") or {})
    short = getattr(search_router, "CONVERSATION_ROUTER_SHORT_SCHEMA", {}) or {}
    properties |= set(short.get("properties") or {})
    overlap = {key for key in properties if key.casefold() in {item.casefold() for item in LLM_ROUTER_FORBIDDEN_KEYS}}
    assert hits == [], f"LLM schema ACL fields: {hits}"
    assert overlap == set(), f"LLM schema properties include ACL fields: {overlap}"


def test_strip_llm_router_forbidden_fields() -> None:
    cleaned = strip_llm_router_forbidden_fields(
        {
            "conversation_route": "legal_query",
            "confidence": 0.9,
            "department": "hộ tịch",
            "acl": {"allowed_domains": ["dat_dai_xay_dung"]},
            "effectivity": "current",
            "allowed_domains": ["ho_tich_chung_thuc"],
        }
    )
    assert cleaned["conversation_route"] == "legal_query"
    assert "department" not in cleaned
    assert "acl" not in cleaned
    assert "effectivity" not in cleaned
    assert "allowed_domains" not in cleaned


def test_pipeline_decision_has_no_acl_fields() -> None:
    names = {item.name for item in PipelineDecisionV1.__dataclass_fields__.values()}
    forbidden = {key.casefold() for key in LLM_ROUTER_FORBIDDEN_KEYS}
    overlap = {name for name in names if name.casefold() in forbidden}
    assert overlap == set(), overlap
    decision = decide_pipeline("Kể chuyện cười đi", role="citizen")
    assert decision.conversation_route == "out_of_scope"
    assert decision.legal_issues == ()
    payload_keys = {key.casefold() for key in decision.to_payload()}
    assert not (payload_keys & forbidden)


def test_authorization_scope_is_frozen_and_backend_only() -> None:
    domains = ["ho_tich_chung_thuc"]
    scope = build_authorization_scope_v1(role="officer", allowed_domains=domains)
    domains.append("dat_dai_xay_dung")
    assert scope.allowed_domains == ("ho_tich_chung_thuc",)
    with pytest.raises(Exception):
        scope.role = "citizen"  # type: ignore[misc]
    snapshot_scope = build_authorization_scope_v1(
        role="citizen",
        snapshot=AuthorizationScopeV1(
            role="officer",
            primary_unit_id="unit-civil",
            grants_checksum="abc",
            allowed_domains=("an_sinh_y_te_giao_duc",),
        ),
    )
    assert snapshot_scope.role == "officer"
    assert snapshot_scope.primary_unit_id == "unit-civil"
    assert snapshot_scope.allowed_domains == ("an_sinh_y_te_giao_duc",)
    assert snapshot_scope.grants_checksum == "abc"


def test_legal_query_plan_does_not_overwrite_account_domain() -> None:
    decision = decide_pipeline(
        "Tôi mua nhà đất đã có Giấy chứng nhận tại Hải Phòng, cần làm các bước sang tên nào?",
        role="officer",
        allowed_domains=["ho_tich_chung_thuc"],
    )
    assert decision.conversation_route == "legal_query"
    assert decision.canonical_domain != "ho_tich_chung_thuc"
    assert decision.canonical_domain in {"dat_dai_xay_dung", "unknown"}


def test_router_dataset_eval_rule_path(capsys: pytest.CaptureFixture[str]) -> None:
    rows: list[dict] = []
    for split in EVAL_SPLITS:
        rows.extend(_load_split(split))
    assert rows, "dev+holdout empty"

    # Warm imports / catalogs outside the timed window.
    decide_pipeline("xin chào", role="citizen")

    latencies: list[float] = []
    predicted: list[str] = []
    gold_routes: list[str] = []
    fallback = 0
    legal_to_meta_oos: list[str] = []
    confusion: Counter[tuple[str, str]] = Counter()

    for row in rows:
        started = time.perf_counter()
        decision = decide_pipeline(
            row["question"],
            role=row.get("role") or "citizen",
            history=row.get("history") or [],
            active_document=row.get("active_document"),
        )
        latencies.append(time.perf_counter() - started)
        pred = decision.conversation_route
        gold = row["expected_conversation_route"]
        predicted.append(pred)
        gold_routes.append(gold)
        confusion[(gold, pred)] += 1
        if decision.source == "fail_open":
            fallback += 1
        if gold == "legal_query" and pred in {"chat_meta", "out_of_scope"}:
            legal_to_meta_oos.append(row["id"])

    n = len(rows)
    correct = sum(1 for gold, pred in zip(gold_routes, predicted) if gold == pred)
    accuracy = correct / n if n else 0.0
    fallback_rate = fallback / n if n else 0.0
    p95 = _p95(latencies)
    print(
        "router_dataset_v1 rule-only "
        f"n={n} accuracy={accuracy:.4f} fallback_rate={fallback_rate:.4f} "
        f"p95_s={p95:.4f} legal_query_to_meta_oos={len(legal_to_meta_oos)}"
    )
    print("confusion gold->pred:", dict(confusion))
    if _env_flag("ROUTER_DATASET_RUN_LLM"):
        print(
            "ROUTER_DATASET_RUN_LLM=1 requested; this harness still gates the "
            "rule path. Warm-Ollama GO metrics (99.5% / fallback<=1% / "
            "router P95<=1.5s) are not claimed here."
        )

    captured = capsys.readouterr()
    print(captured.out, end="")
    assert legal_to_meta_oos == [], (
        "gold legal_query predicted as chat_meta/out_of_scope: "
        + ", ".join(legal_to_meta_oos[:20])
    )
    assert p95 < 0.05, f"rule-only p95 {p95:.4f}s exceeds 0.05s"


def test_decide_router_matches_pipeline_on_gold_smalltalk() -> None:
    question = "Kể chuyện cười đi"
    router = decide_router(question, role="citizen")
    pipeline = decide_pipeline(question, role="citizen")
    assert router.conversation_route == pipeline.conversation_route == "out_of_scope"
    assert pipeline.source == "rule"
