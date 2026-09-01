"""Holdout eval for Phase B: rule-first, qwen2.5:0.5b on fail-open remainder."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from collections import Counter
import sys
from pathlib import Path

ROOT_BOOT = Path(__file__).resolve().parents[1]
if str(ROOT_BOOT) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOT))

from api.unified_router import decide_router, should_invoke_router_llm

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "tests" / "fixtures" / "router_dataset_v1" / "holdout.jsonl"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("CHAT_LLM_ROUTER_OLLAMA_TAG", "qwen2.5:0.5b")
TIMEOUT = float(os.getenv("CHAT_LLM_ROUTER_V2_TIMEOUT_SECONDS", "3"))
MIN_CONF = 0.6

SHORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["conversation_route", "confidence"],
    "properties": {
        "conversation_route": {
            "type": "string",
            "enum": ["legal_query", "document_followup", "chat_meta", "out_of_scope"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

ROUTES = {"legal_query", "document_followup", "chat_meta", "out_of_scope"}


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _load() -> list[dict]:
    rows = []
    for line in HOLDOUT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _post_json(path: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def warm() -> None:
    _post_json(
        "/api/generate",
        {
            "model": MODEL,
            "prompt": (
                'Classify. Output JSON only: '
                '{"conversation_route":"legal_query","confidence":0.9}'
            ),
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "format": SHORT_SCHEMA,
            "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 64},
        },
        timeout=60,
    )


def call_llm(question: str, history: list, active_document) -> dict | None:
    hist = []
    for item in (history or [])[-4:]:
        if isinstance(item, dict):
            hist.append(f"{item.get('role','')}: {item.get('content','')}")
    doc = ""
    if isinstance(active_document, dict):
        doc = str(active_document.get("id") or active_document.get("title") or "")
    prompt = (
        "Classify the user turn. JSON only with conversation_route and confidence.\n"
        "Routes: legal_query | document_followup | chat_meta | out_of_scope\n"
        "chat_meta = greeting, thanks, bot help. out_of_scope = not Vietnamese law.\n"
        "document_followup = short follow-up about the active document.\n"
        "legal_query = legal/procedure question, including typos.\n"
        f"Question: {question}\n"
        f"History: {' | '.join(hist) if hist else '(none)'}\n"
        f"Active document: {doc or '(none)'}\n"
    )
    try:
        raw = _post_json(
            "/api/generate",
            {
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "format": SHORT_SCHEMA,
                "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 64},
            },
            timeout=max(TIMEOUT, 8.0),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    text = str(raw.get("response") or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    route = str(parsed.get("conversation_route") or parsed.get("route") or "")
    try:
        conf = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if route not in ROUTES:
        return None
    return {"conversation_route": route, "confidence": conf}


def run(mode: str) -> dict:
    rows = _load()
    latencies: list[float] = []
    confusion: Counter[tuple[str, str]] = Counter()
    correct = fallback = llm_n = llm_ok = legal_bad = 0
    details_bad: list[str] = []
    for row in rows:
        question = row["question"]
        started = time.perf_counter()
        rule = decide_router(
            question,
            role=row.get("role") or "citizen",
            history=row.get("history") or [],
            active_document=row.get("active_document"),
        )
        pred = rule.conversation_route
        source = rule.source
        invoke = False
        if rule.source == "fail_open":
            if mode == "failopen":
                invoke = True
            elif mode == "serving":
                invoke = should_invoke_router_llm(question, rule)
        if invoke:
            llm_n += 1
            llm = call_llm(question, row.get("history") or [], row.get("active_document"))
            if llm and llm["confidence"] >= MIN_CONF:
                pred = llm["conversation_route"]
                source = "llm"
                llm_ok += 1
            else:
                pred = "legal_query"
                source = "fail_open"
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)
        gold = row["expected_conversation_route"]
        confusion[(gold, pred)] += 1
        if pred == gold:
            correct += 1
        if source == "fail_open":
            fallback += 1
        if gold == "legal_query" and pred in {"chat_meta", "out_of_scope"}:
            legal_bad += 1
            details_bad.append(row.get("id", "?"))
    n = len(rows)
    return {
        "mode": mode,
        "n": n,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "correct": correct,
        "fallback_rate": round(fallback / n, 4) if n else 0.0,
        "fallback": fallback,
        "p95_s": round(_p95(latencies), 4),
        "mean_s": round(sum(latencies) / n, 4) if n else 0.0,
        "llm_invoked": llm_n,
        "llm_accepted": llm_ok,
        "legal_query_to_meta_oos": legal_bad,
        "legal_bad_ids": details_bad[:20],
        "confusion": {f"{g}->{p}": c for (g, p), c in confusion.most_common()},
        "gates": {
            "accuracy_99_5": (correct / n) >= 0.995 if n else False,
            "fallback_le_1pct": (fallback / n) <= 0.01 if n else False,
            "p95_le_1_5s": _p95(latencies) <= 1.5,
            "legal_to_meta_oos_zero": legal_bad == 0,
        },
    }


def main() -> None:
    print("warming", MODEL, "at", OLLAMA_URL, flush=True)
    warm()
    print("warm ok", flush=True)
    serving = run("serving")
    print("SERVING", json.dumps(serving, ensure_ascii=False), flush=True)
    failopen = run("failopen")
    print("FAILOPEN", json.dumps(failopen, ensure_ascii=False), flush=True)
    out = ROOT / "tests" / "fixtures" / "router_dataset_v1" / "holdout_eval_20260901.json"
    out.write_text(
        json.dumps({"serving": serving, "failopen": failopen}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("wrote", out)


if __name__ == "__main__":
    main()
