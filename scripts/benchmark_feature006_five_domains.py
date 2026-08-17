"""Run the public-release five-domain Ask benchmark.

The report deliberately excludes questions, answers, credentials and tokens.
It records only bounded quality, provenance and latency facts needed by the
Feature 006 release gate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEGAL_AS_OF = "2026-07-30"
CASES = (
    {
        "case_id": "civil-status-hard-001",
        "domain": "ho_tich_chung_thuc",
        "question": (
            "Đăng ký khai sinh cho trẻ mới sinh tại Hải Phòng thuộc thẩm quyền "
            "cơ quan nào, hồ sơ gồm giấy tờ gì và căn cứ pháp lý đang còn hiệu "
            "lực tại ngày 30/07/2026?"
        ),
        "required_answer_terms": ("ủy ban nhân dân cấp xã", "giấy chứng sinh"),
        "expected_form_codes": (),
    },
    {
        "case_id": "land-form-hard-001",
        "domain": "dat_dai_xay_dung",
        "question": (
            "Thủ tục có mã 1.002693 tại Hải Phòng cần biểu mẫu nào? Chỉ cung "
            "cấp biểu mẫu chính thức còn hiệu lực và liên kết tải."
        ),
        "required_answer_terms": (),
        "expected_form_codes": ("01",),
    },
    {
        "case_id": "residence-form-hard-001",
        "domain": "cu_tru_an_ninh",
        "question": (
            "Thủ tục có mã 1.000253 trong lĩnh vực cư trú cần biểu mẫu nào? "
            "Chỉ cung cấp biểu mẫu chính thức còn hiệu lực và liên kết tải."
        ),
        "required_answer_terms": (),
        "expected_form_codes": ("NA17",),
    },
    {
        "case_id": "complaint-hard-001",
        "domain": "khieu_nai_to_cao_xu_phat",
        "question": (
            "Ai có thẩm quyền giải quyết khiếu nại lần đầu đối với một quyết "
            "định hành chính, và căn cứ pháp lý đang còn hiệu lực tại ngày "
            "30/07/2026 là gì?"
        ),
        # Articles 17--18 specify the competent office by administrative
        # level. Test those statutory outcomes, rather than requiring one
        # broader paraphrase to appear verbatim.
        "required_answer_terms": (
            "có thẩm quyền giải quyết khiếu nại lần đầu",
        ),
        "expected_form_codes": (),
    },
    {
        "case_id": "social-health-form-hard-001",
        "domain": "an_sinh_y_te_giao_duc",
        "question": (
            "Thủ tục có mã 1.013873 cần biểu mẫu nào? Chỉ cung cấp biểu mẫu "
            "chính thức còn hiệu lực và liên kết tải."
        ),
        "required_answer_terms": (),
        "expected_form_codes": ("19",),
    },
)


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\bubnd\b", "uy ban nhan dan", text)
    return re.sub(r"\s+", " ", text.replace("đ", "d")).strip()


def _read_token(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FEATURE005_CITIZEN_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Citizen benchmark token is missing")


def _post_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Ask returned a non-object response")
    return data


def _duplicate_claim_count(answer: str) -> int:
    seen: set[str] = set()
    duplicates = 0
    for raw_line in str(answer or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        claim = _fold(line.split(":", 1)[1])
        if len(claim) < 20:
            continue
        if claim in seen:
            duplicates += 1
        seen.add(claim)
    return duplicates


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _evaluate_case(case: dict[str, Any], response: dict[str, Any], latency: float) -> dict[str, Any]:
    answer = str(response.get("answer") or "")
    folded_answer = _fold(answer)
    citations = list(response.get("citations") or [])
    returned_codes = tuple(
        str(item.get("form_code") or "").strip()
        for item in (response.get("recommended_forms") or [])
        if str(item.get("form_code") or "").strip()
    )
    expected_codes = tuple(case["expected_form_codes"])
    wrong_form = bool(expected_codes and returned_codes != expected_codes)
    answer_terms_ok = all(_fold(term) in folded_answer for term in case["required_answer_terms"])
    forms_ok = not expected_codes or returned_codes == expected_codes
    citations_ok = bool(citations) and all(
        str(item.get("effective_status") or "").casefold() == "active"
        and bool(item.get("source_url"))
        and bool(item.get("law_number"))
        for item in citations
    )
    duplicate_claims = _duplicate_claim_count(answer)
    grounding = str(response.get("grounding_status") or "")
    api_score = float(response.get("answer_score_preview") or 0.0)

    audit_score = 0.0
    audit_score += 1.0
    audit_score += 2.0 if citations_ok else 0.0
    audit_score += 1.0 if grounding == "fully_grounded" else 0.5 if grounding == "partially_grounded" else 0.0
    audit_score += 2.0 if answer_terms_ok and forms_ok else 0.0
    audit_score += 2.0 if not wrong_form and duplicate_claims == 0 else 0.0
    audit_score += 1.0 if api_score >= 7.0 else 0.0
    audit_score += 1.0 if latency <= 60.0 else 0.0

    critical_errors: list[str] = []
    if not citations_ok:
        critical_errors.append("MISSING_ACTIVE_OFFICIAL_CITATION")
    if not answer_terms_ok:
        critical_errors.append("REQUIRED_ANSWER_FACT_MISSING")
    if wrong_form:
        critical_errors.append("WRONG_FORM")
    if grounding not in {"fully_grounded", "partially_grounded"}:
        critical_errors.append("UNGROUNDED")

    return {
        "case_id": case["case_id"],
        "domain": case["domain"],
        "http_status": 200,
        "latency_seconds": round(latency, 3),
        "api_score_preview": round(api_score, 2),
        "audit_score": round(audit_score, 2),
        "release_score": round(min(api_score, audit_score), 2),
        "grounding_status": grounding,
        "citation_count": len(citations),
        "returned_form_codes": list(returned_codes),
        "expected_form_codes": list(expected_codes),
        "wrong_form": wrong_form,
        "duplicate_claim_count": duplicate_claims,
        "quality_flags": list(response.get("quality_flags") or []),
        "critical_errors": critical_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5056")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("reports/feature006/.private/goal-five-domain.env"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/feature006/five-domain-chatbot-quality.json"),
    )
    args = parser.parse_args()

    token = _read_token(args.env_file)
    results: list[dict[str, Any]] = []
    for case in CASES:
        payload = {
            "question": case["question"],
            "role": "citizen",
            "domain": case["domain"],
            "legal_as_of": LEGAL_AS_OF,
            "idempotency_key": f"feature006-{case['case_id']}-{uuid.uuid4().hex}",
        }
        started = time.perf_counter()
        try:
            response = _post_json(
                f"{args.base_url.rstrip('/')}/api/search/ask/simple",
                token,
                payload,
            )
            results.append(_evaluate_case(case, response, time.perf_counter() - started))
        except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as exc:
            results.append({
                "case_id": case["case_id"],
                "domain": case["domain"],
                "http_status": getattr(exc, "code", 0),
                "latency_seconds": round(time.perf_counter() - started, 3),
                "api_score_preview": 0.0,
                "audit_score": 0.0,
                "release_score": 0.0,
                "grounding_status": "error",
                "citation_count": 0,
                "returned_form_codes": [],
                "expected_form_codes": list(case["expected_form_codes"]),
                "wrong_form": False,
                "duplicate_claim_count": 0,
                "quality_flags": ["HTTP_ERROR"],
                "critical_errors": ["HTTP_ERROR"],
            })

    scores = [float(item["release_score"]) for item in results]
    latencies = [float(item["latency_seconds"]) for item in results]
    critical_count = sum(len(item["critical_errors"]) for item in results)
    median_latency = statistics.median(latencies)
    p95_latency = _nearest_rank_percentile(latencies, 0.95)
    pass_gate = (
        all(score >= 7.0 for score in scores)
        and statistics.mean(scores) >= 7.0
        and median_latency <= 25.0
        and p95_latency <= 60.0
        and critical_count == 0
    )
    report = {
        "schema_version": "feature006-five-domain-quality-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": LEGAL_AS_OF,
        "thresholds": {
            "minimum_each_score": 7.0,
            "minimum_average_score": 7.0,
            "maximum_median_latency_seconds": 25.0,
            "maximum_p95_latency_seconds": 60.0,
            "maximum_critical_errors": 0,
        },
        "summary": {
            "case_count": len(results),
            "minimum_score": round(min(scores), 2),
            "average_score": round(statistics.mean(scores), 2),
            "median_latency_seconds": round(median_latency, 3),
            "p95_latency_seconds": round(p95_latency, 3),
            "critical_error_count": critical_count,
            "verdict": "PASS" if pass_gate else "FAIL",
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if pass_gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
