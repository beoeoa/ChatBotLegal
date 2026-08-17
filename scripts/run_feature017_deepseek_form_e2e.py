#!/usr/bin/env python3
"""Run the approved five-domain live form-answer smoke test.

The script records only public answer fields and never serializes credentials.
Ground truth is loaded from the approved Golden V3 artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


CASE_IDS = (
    "f017_ho_tich_chung_thuc_090",
    "f017_dat_dai_xay_dung_081",
    "f017_an_sinh_y_te_giao_duc_081",
    "f017_cu_tru_an_ninh_081",
    "f017_khieu_nai_to_cao_xu_phat_083",
)


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError("Golden V3 artifact has no case list")
    by_id = {str(row.get("case_id")): row for row in rows if isinstance(row, dict)}
    missing = [case_id for case_id in CASE_IDS if case_id not in by_id]
    if missing:
        raise RuntimeError(f"Golden V3 is missing cases: {missing}")
    return [by_id[case_id] for case_id in CASE_IDS]


def execute(
    *,
    base_url: str,
    golden_path: Path,
    output_path: Path,
    identifier: str,
    password: str,
) -> dict[str, Any]:
    if not password:
        raise RuntimeError("FEATURE017_CITIZEN_PASSWORD is required")
    cases = _load_cases(golden_path)
    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=180) as client:
        login = client.post(
            "/api/auth/login",
            json={"identifier": identifier, "password": password, "role": "citizen"},
        )
        login.raise_for_status()
        token = str(login.json().get("token") or "")
        if not token:
            raise RuntimeError("Citizen login returned no token")
        headers = {"Authorization": f"Bearer {token}", "X-User-Role": "citizen"}
        for golden in cases:
            started = time.perf_counter()
            response = client.post(
                "/api/search/ask/simple",
                headers=headers,
                json={
                    "question": golden["question"],
                    "role": "citizen",
                    "legal_as_of": golden["legal_as_of"],
                },
            )
            elapsed = time.perf_counter() - started
            payload = response.json()
            expected = sorted(str(value) for value in golden.get("expected_form_ids") or [])
            actual = sorted(
                str(form.get("form_id"))
                for form in payload.get("recommended_forms") or []
                if isinstance(form, dict) and form.get("form_id")
            )
            results.append(
                {
                    "case_id": golden["case_id"],
                    "domain": golden["domain"],
                    "http_status": response.status_code,
                    "elapsed_seconds": round(elapsed, 3),
                    "expected_state": golden["expected_state"],
                    "expected_form_ids": expected,
                    "actual_form_ids": actual,
                    "form_set_match": actual == expected,
                    "answer_mode": payload.get("answer_mode"),
                    "grounding_status": payload.get("grounding_status"),
                    "answer": payload.get("answer"),
                    "quality_flags": payload.get("quality_flags") or [],
                    "error": payload.get("error"),
                }
            )
    released = [row for row in results if row["expected_state"] == "released"]
    gaps = [row for row in results if row["expected_state"] != "released"]
    report = {
        "schema_version": "feature017-deepseek-exact-form-e2e-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "citizen",
        "credentials_recorded": False,
        "cases": results,
        "summary": {
            "case_count": len(results),
            "domain_count": len({row["domain"] for row in results}),
            "http_200": sum(row["http_status"] == 200 for row in results),
            "exact_form_set": sum(row["form_set_match"] for row in results),
            "released_without_provider_fallback": sum(
                row["answer_mode"] in {"normal", "grounded_answer"}
                and not row["error"]
                for row in released
            ),
            "released_count": len(released),
            "verified_gap_fail_closed": all(
                not row["actual_form_ids"] and row["answer_mode"] == "source_view_only"
                for row in gaps
            ),
            "max_elapsed_seconds": max(row["elapsed_seconds"] for row in results),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--identifier", default="citizen01")
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "outputs/feature017-golden-v3-user-journey-1000/"
            "golden-v3-1000-approved.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/feature017/deepseek-exact-form-e2e-v2.json"),
    )
    args = parser.parse_args()
    report = execute(
        base_url=args.base_url,
        golden_path=args.golden,
        output_path=args.output,
        identifier=args.identifier,
        password=os.getenv("FEATURE017_CITIZEN_PASSWORD", ""),
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
