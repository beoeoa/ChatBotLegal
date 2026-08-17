"""Generate deterministic acceptance evidence for M4 query understanding."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_query_understanding import (
    M4_INTENTS,
    classify_legal_query,
    validate_m4_query_classification,
)


REPORT_DIR = ROOT / "reports" / "m4-query-understanding"
SCHEMA_PATH = (
    ROOT
    / "specs"
    / "018-production-release-readiness"
    / "contracts"
    / "legal-query-classification-m4-v1.schema.json"
)
TODAY = date(2026, 8, 15)
INTENT_CASES = {
    "PROCEDURE": "Thủ tục đăng ký khai sinh thế nào?",
    "ELIGIBILITY": "Điều kiện đăng ký kết hôn là gì?",
    "REQUIRED_DOCUMENTS": "Đăng ký khai sinh cần giấy tờ gì?",
    "AUTHORITY": "Cơ quan nào có thẩm quyền chứng thực?",
    "PROCESS": "Các bước giải quyết hồ sơ ra sao?",
    "DEADLINE": "Thời hạn giải quyết là bao lâu?",
    "FEE": "Lệ phí chứng thực bao nhiêu?",
    "FORM": "Tải mẫu tờ khai đăng ký khai sinh",
    "LEGAL_BASIS": "Căn cứ pháp lý của thủ tục này?",
    "VALIDITY": "Văn bản này còn hiệu lực không?",
    "SPECIFIC_DOCUMENT": "Điều 16 Luật Hộ tịch quy định gì?",
    "HISTORICAL": "Quy định tại ngày 01/06/2020 là gì?",
    "COMPARISON": "So sánh thường trú và tạm trú",
    "COMPLAINT": "Tôi muốn khiếu nại quyết định hành chính",
    "OUT_OF_SCOPE": "Tư vấn thủ tục đăng kiểm ô tô",
    "UNKNOWN": "Tôi cần hỏi một việc",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    classifications: dict[str, dict] = {}
    for expected, query in INTENT_CASES.items():
        value = classify_legal_query(query, today=TODAY)
        validate_m4_query_classification(value)
        jsonschema.validate(value, schema)
        classifications[expected] = value

    current = classify_legal_query("Hiện nay đăng ký khai sinh cần gì?", today=TODAY)
    historical = classify_legal_query(
        "Ngày 01/06/2020 đăng ký khai sinh cần gì?", today=TODAY
    )
    year_only = classify_legal_query(
        "Năm 2020 đăng ký khai sinh cần gì?", today=TODAY
    )
    ambiguous = classify_legal_query(
        "Trước đây đăng ký khai sinh cần gì?", today=TODAY
    )
    conflict = classify_legal_query(
        "Hiện nay đăng ký khai sinh cần gì?",
        as_of=date(2020, 6, 1),
        as_of_explicit=True,
        today=TODAY,
    )
    active_pointer_path = (
        ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt"
    )
    active_pointer = active_pointer_path.read_text(encoding="utf-8").strip()
    ask_router_text = (ROOT / "api" / "routers" / "search.py").read_text(
        encoding="utf-8"
    )

    observed_intents = {value["intent"] for value in classifications.values()}
    gates = {
        "structured_contract_valid": len(classifications) == 16,
        "all_16_required_intents_reachable": observed_intents == M4_INTENTS,
        "current_distinguished": current["temporal_scope"] == "current"
        and current["retrieval_allowed"],
        "historical_exact_date_enforced": historical["temporal_scope"] == "historical"
        and historical["retrieval_as_of"] == "2020-06-01",
        "historical_year_only_fails_closed": year_only["temporal_scope"] == "historical"
        and not year_only["retrieval_allowed"]
        and year_only["temporal_error_code"] == "HISTORICAL_AS_OF_REQUIRED",
        "unknown_temporal_fails_closed": ambiguous["temporal_scope"] == "unknown"
        and not ambiguous["retrieval_allowed"],
        "current_historical_conflict_fails_closed": not conflict["retrieval_allowed"]
        and conflict["temporal_error_code"] == "TEMPORAL_AS_OF_CONFLICT",
        "active_baseline_pointer_unchanged": active_pointer
        == "legal_chunks_vnlegal_lal_haiphong_unified_v1",
        "ask_api_propagates_as_of_explicitness": ask_router_text.count(
            '"as_of_explicit"'
        ) >= 5,
    }
    status = "pass" if all(gates.values()) else "fail"
    report = {
        "schema_version": "legal-m4-query-understanding-acceptance-v1",
        "status": status,
        "reference_date": TODAY.isoformat(),
        "intent_count": len(observed_intents),
        "intents": sorted(observed_intents),
        "temporal_cases": {
            "current": current,
            "historical_exact_date": historical,
            "historical_year_only": year_only,
            "unknown_ambiguous": ambiguous,
            "conflict": conflict,
        },
        "gates": gates,
        "active_pointer": active_pointer,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "m4_acceptance_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    artifact_paths = [
        ROOT / "api" / "legal_query_understanding.py",
        ROOT / "scripts" / "legal_search_server.py",
        ROOT / "api" / "routers" / "search.py",
        ROOT / "tests" / "test_m4_query_understanding.py",
        SCHEMA_PATH,
        report_path,
    ]
    checksums = {
        str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
        for path in artifact_paths
    }
    (REPORT_DIR / "m4_checksums.json").write_text(
        json.dumps(checksums, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
