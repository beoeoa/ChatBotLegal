from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openpyxl import Workbook

from scripts.build_feature016_regression import build_regression_dataset


HEADERS = [
    "STT log",
    "Role",
    "Lĩnh vực",
    "Câu hỏi",
    "Câu trả lời trên giao diện",
    "Trạng thái",
    "Thời gian (giây)",
    "Dự phòng",
    "Lỗi",
    "Có nguồn",
    "Mã câu",
    "Ghi chú",
]


def _write_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Hỏi đáp chi tiết"
    worksheet.append(HEADERS)
    worksheet.append(
        [
            1,
            "citizen",
            "Hộ tịch/chứng thực",
            "  Hồ sơ đăng ký kết hôn gồm gì?  ",
            'region "Ask Response": câu trả lời lẫn toàn bộ DOM',
            "completed",
            2.0,
            "Không",
            "Không",
            "Có",
            1,
            None,
        ]
    )
    worksheet.append(
        [
            2,
            "officer_hotich",
            "Hộ tịch/chứng thực",
            "Hồ sơ đăng ký kết hôn gồm gì?",
            "Một câu trả lời khác",
            "completed",
            4.0,
            "Có",
            "Không",
            "Có",
            1,
            "quan sát",
        ]
    )
    worksheet.append(
        [
            3,
            "citizen",
            "Cư trú/an ninh",
            "Đăng ký thường trú cần gì?",
            "Không có nguồn",
            "error",
            8.0,
            "Không",
            "Có",
            "Không",
            2,
            None,
        ]
    )
    workbook.save(path)


def test_builder_is_deterministic_and_does_not_copy_dom_answers(tmp_path: Path):
    source = tmp_path / "qa-log.xlsx"
    output = tmp_path / "regression.json"
    _write_workbook(source)

    first = build_regression_dataset(
        source,
        output,
        expected_questions=2,
        expected_per_domain=None,
    )
    first_bytes = output.read_bytes()
    second = build_regression_dataset(
        source,
        output,
        expected_questions=2,
        expected_per_domain=None,
    )

    assert first == second
    assert first_bytes == output.read_bytes()
    assert first["source"]["source_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert first["unique_question_count"] == 2
    assert first["domain_counts"] == {
        "Cư trú/an ninh": 1,
        "Hộ tịch/chứng thực": 1,
    }
    assert "answer_text" not in json.dumps(first, ensure_ascii=False).lower()
    assert "Ask Response" not in json.dumps(first, ensure_ascii=False)

    marriage = first["cases"][0]
    assert marriage["case_id"] == "web-001"
    assert marriage["observed_roles"] == ["citizen", "officer_hotich"]
    assert marriage["baseline"]["run_count"] == 2
    assert marriage["baseline"]["source_flag_count"] == 2
    assert marriage["baseline"]["fallback_count"] == 1
    assert marriage["baseline"]["latency_p50_seconds"] == 3.0
    assert marriage["baseline"]["latency_p95_seconds"] == 3.9


def test_builder_rejects_unexpected_question_distribution(tmp_path: Path):
    source = tmp_path / "qa-log.xlsx"
    _write_workbook(source)

    try:
        build_regression_dataset(
            source,
            tmp_path / "out.json",
            expected_questions=100,
            expected_per_domain=20,
        )
    except ValueError as exc:
        assert "unique questions" in str(exc)
    else:
        raise AssertionError("distribution mismatch must fail closed")
