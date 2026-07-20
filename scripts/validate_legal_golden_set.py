#!/usr/bin/env python3
"""Validator for Legal QA Golden Set."""

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import httpx

DEFAULT_API_URL = "http://127.0.0.1:5055"


@dataclass
class EvaluationResult:
    question_id: str
    domain: str
    role: str
    question: str
    answer: str = ""
    citations: list = field(default_factory=list)
    grounding_score: float = 0.0
    role_score: float = 0.0
    overall_score: float = 0.0
    errors: list = field(default_factory=list)


def load_golden_set(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def extract_citations(answer: str) -> list[str]:
    pattern = r"\[legal:([^\]]+)\]"
    return re.findall(pattern, answer)


async def login(api_url: str) -> str:
    """Get an admin auth token for API calls."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_url}/api/auth/login",
            json={"password": "gfi", "role": "admin"},
        )
        if resp.status_code >= 400:
            resp = await client.post(
                f"{api_url}/api/auth/login",
                json={"identifier": "admin", "password": "gfi", "role": "admin"},
            )
        resp.raise_for_status()
        return resp.json().get("token") or "gfi"


async def get_chat_model_id(api_url: str, token: str) -> str:
    """Get a usable chat model ID from the API. Prefers deepseek-chat."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}", "X-User-Role": "admin"}
        r = await client.get(f"{api_url}/api/models", headers=headers)
        r.raise_for_status()
        models = r.json()
        for m in models:
            if m.get("type") == "language" and "deepseek-chat" in (m.get("name") or "").lower():
                return m["id"]
        for m in models:
            if m.get("type") == "language" and m.get("provider") == "deepseek":
                return m["id"]
        for m in models:
            if m.get("type") == "language" and m.get("provider") == "google":
                return m["id"]
        for m in models:
            if m.get("type") == "language":
                return m["id"]
        raise RuntimeError("No language model found")


async def ask_question(
    question: str, role: str, api_url: str, token: str, model_id: str
) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        headers = {"Authorization": f"Bearer {token}", "X-User-Role": "admin"}
        body = {
            "question": question,
            "role": role,
            "offline_mode": False,
            "show_rag_trace": True,
            "strategy_model": model_id,
            "answer_model": model_id,
            "final_answer_model": model_id,
        }
        response = await client.post(
            f"{api_url}/api/search/ask/simple", json=body, headers=headers
        )
        response.raise_for_status()
        return response.json()


async def evaluate_single(
    test_case: dict, role: str, api_url: str, token: str, model_id: str
) -> EvaluationResult:
    question_key = f"question_{role}"
    question = test_case.get(question_key, test_case.get("question"))
    if not question:
        return EvaluationResult(
            question_id=test_case["id"],
            domain=test_case["domain"],
            role=role,
            question="",
            errors=["No question"],
        )
    try:
        response = await ask_question(question, role, api_url, token, model_id)
        answer = response.get("answer", "")
        # Prefer structured citations from response (UI no longer embeds [legal:id])
        structured = response.get("citations") or []
        citations = []
        for c in structured:
            if isinstance(c, dict):
                label = c.get("label") or c.get("law_number") or c.get("doc_id") or ""
                if label:
                    citations.append(str(label))
            elif c:
                citations.append(str(c))
        if not citations:
            citations = extract_citations(answer)

        # Also score natural law-number mentions in answer text
        natural_hits = re.findall(r"\b\d{1,4}/\d{4}/[A-Za-zÀ-ỹĐđ0-9\-]+\b", answer or "")
        for hit in natural_hits:
            if hit not in citations:
                citations.append(hit)

        expected = test_case.get("expected_citations") or []
        expected_hits = 0
        for exp in expected:
            exp_norm = str(exp).lower()
            blob = (" ".join(citations) + " " + (answer or "")).lower()
            if exp_norm in blob:
                expected_hits += 1
        expected_ratio = (expected_hits / len(expected)) if expected else (1.0 if citations else 0.0)

        gs = response.get("grounding_status", "")
        if gs in {"grounded", "partially_grounded"} and (citations or expected_hits > 0):
            grounding_score = 0.85 if expected_ratio >= 0.34 else 0.7
        elif gs == "insufficient_evidence":
            grounding_score = 0.7  # Safe fallback - correct behavior per spec
        elif citations:
            grounding_score = 0.65
        else:
            grounding_score = 0.3

        # Role template cues
        role_score = 0.5
        if role == "citizen":
            cues = ["Kết luận", "Giấy tờ", "Hướng dẫn"]
        else:
            cues = ["Kết luận", "Căn cứ", "Quy trình", "Hồ sơ", "xác minh"]
        hit_cues = sum(1 for c in cues if c.lower() in (answer or "").lower())
        role_score = min(1.0, 0.4 + 0.15 * hit_cues)

        overall = grounding_score * 0.5 + role_score * 0.3 + 0.2
        return EvaluationResult(
            question_id=test_case["id"],
            domain=test_case["domain"],
            role=role,
            question=question,
            answer=answer[:200],
            citations=citations[:5],
            grounding_score=grounding_score,
            role_score=role_score,
            overall_score=overall,
        )
    except Exception as e:
        return EvaluationResult(
            question_id=test_case["id"],
            domain=test_case["domain"],
            role=role,
            question=question,
            errors=[str(e)],
        )


async def run_evaluation(
    golden_set_path: Path, api_url: str, max_q: int = None
) -> dict:
    golden_set = load_golden_set(golden_set_path)
    questions = (
        golden_set.get("questions", [])[:max_q]
        if max_q
        else golden_set.get("questions", [])
    )
    token = await login(api_url)
    model_id = await get_chat_model_id(api_url, token)
    print(f"Using model: {model_id}")
    results = []
    for test_case in questions:
        for role in ["citizen", "officer"]:
            result = await evaluate_single(
                test_case, role, api_url, token, model_id
            )
            results.append(result)
            status = "PASS" if result.overall_score >= 0.6 else "FAIL"
            print(
                f"  [{status}] {result.question_id} ({role}): "
                f"score={result.overall_score:.2f}"
            )
    if results:
        avg_score = sum(r.overall_score for r in results) / len(results)
        passed = sum(1 for r in results if r.overall_score >= 0.6)
    else:
        avg_score = 0
        passed = 0
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "average_score": avg_score,
        "results": [asdict(r) for r in results],
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=Path("notebook_data/legal-golden-set.json"),
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = await run_evaluation(args.golden_set, args.api_url, args.max_questions)
    pct = report["passed"] / report["total"] * 100 if report["total"] else 0
    print(f"\nEvaluation Complete:")
    print(f"  Total: {report['total']}")
    print(f"  Passed: {report['passed']} ({pct:.1f}%)")
    print(f"  Failed: {report['failed']}")
    print(f"  Average Score: {report['average_score']:.2f}")
    if args.output:
        with open(args.output, "w", encoding="utf-8-sig") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Report saved to {args.output}")
    if report["average_score"] < 0.6:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
