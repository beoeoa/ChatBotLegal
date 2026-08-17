"""Run the final five-domain legal-answer quality gate.

The benchmark is built only from cases whose official source was previously
retrieved successfully.  Each of the five domains contains ten direct-source
questions, ten ordinary application questions, and ten complex questions.
Every positive question is sent once as a citizen and once as an officer, and
the suite also contains an explicit expired-source blocking case. Detailed
answers stay under the ignored reports directory; the
shareable summary contains only counters, scores, latency, and review notes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "notebook_data" / "legal-golden-expert-review.json"
DEFAULT_RETRIEVAL = (
    ROOT
    / "data"
    / "form_resolution_campaign"
    / "runs"
    / "20260728005308-f7b1b1ce"
    / "retrieval-167-live-rerun.json"
)
DEFAULT_CREDENTIALS = ROOT / "reports" / "feature005" / "pilot-20260722.env"
DEFAULT_PRIVATE_REPORT = (
    ROOT / "reports" / "five-domain-quality-v2" / "private-results.json"
)
DEFAULT_SUMMARY = ROOT / "reports" / "five-domain-quality-v2" / "summary.json"
DEFAULT_CHECKPOINT = ROOT / "reports" / "five-domain-quality-v2" / "checkpoint.json"

BENCHMARK_DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
)
ROLES = ("citizen", "officer")
DIFFICULTIES = ("easy", "medium", "hard")

_TOKEN_ENV_KEYS = {
    "citizen": ("PILOT_CITIZEN_TOKEN", "FEATURE005_CITIZEN_TOKEN"),
    "officer": ("PILOT_OFFICER_TOKEN", "FEATURE005_OFFICER_TOKEN"),
}
_FORBIDDEN_FALLBACKS = (
    "khong tong hop duoc",
    "khong the tong hop",
    "khong tim thay",
    "chua tim thay",
    "khong co trong du lieu",
    "khong co du lieu",
    "can bo sung thong tin",
    "vui long bo sung thong tin",
    "chua du thong tin",
    "chua du can cu",
    "nguon hien co khong neu",
    "chua co quy trinh cu the",
    "khong co nguon phu hop",
    "can lam ro them",
)

_KNOWN_FALLBACK_LABELS = {
    "local_model_fallback",
    "extractive_fallback",
    "provider_timeout_fallback",
    "provider_fallback",
    "retrieval_unavailable",
    "expired_source_blocked",
}

_EXPIRED_BLOCK_SOURCE = {
    "document_title": "Hướng dẫn giải quyết chế độ bảo hiểm xã hội trong Quân đội",
    "law_number": "96/2014/TT-BQP",
    "article_number": "26",
    "source_url": (
        "https://vbpl.vn/van-ban/chi-tiet/thong-tu-so-96-2014-tt-bqp-"
        "huong-dan-ve-ho-so-quy-trinh-va-trach-nhiem-giai-quyet-huong-cac-"
        "che-do-bao-hiem-xa-hoi-trong-quan-doi--37008"
    ),
    "effective_status": "expired",
    "effective_to": "2016-12-20",
}
_INTERNAL_MARKERS = re.compile(
    r"#ref-source|\blegal:|chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id|"
    r"parent_context|\[\[HOAN_TAT\]\]",
    re.IGNORECASE,
)
_COMPLEXITY_MARKERS = (
    "phan biet",
    "so sanh",
    "truong hop",
    "ngoai le",
    "tranh chap",
    "nuoc ngoai",
    "khong hop tac",
    "bi tu choi",
    "khieu nai",
    "to cao",
    "xu phat",
    "thoi han",
    "le phi",
    "bieu mau",
    "hieu luc",
)

_MINIMUM_ANSWER_CHARACTERS: dict[str, dict[str, int]] = {
    "easy": {"citizen": 100, "officer": 120},
    "medium": {"citizen": 150, "officer": 170},
    "hard": {"citizen": 280, "officer": 300},
}

_VACUOUS_RULE_ONLY_PATTERNS = (
    "thong tin khac theo quy dinh cua chinh phu",
    "thuc hien cac nghia vu khac theo quy dinh cua phap luat",
    "thuc hien cac quyen nghia vu khac theo quy dinh cua phap luat",
    "thuc hien cac quyen va nghia vu khac theo quy dinh cua phap luat",
    "cac quyen nghia vu khac theo quy dinh cua phap luat",
    "cac quyen va nghia vu khac theo quy dinh cua phap luat",
)

# Snapshot selected by an exact-provision preflight against the local retrieval
# service on 2026-08-09.  The source URL is still read from the reviewed local
# retrieval fixture; these keys only prevent a cross-domain or non-exact source
# from becoming a benchmark question.  A live run verifies the citation again.
_VERIFIED_PROVISIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "ho_tich_chung_thuc": (
        ("60/2014/QH13", "3"), ("60/2014/QH13", "13"),
        ("23/2015/NĐ-CP", "24"), ("60/2014/QH13", "5"),
        ("60/2014/QH13", "7"), ("60/2014/QH13", "9"),
        ("60/2014/QH13", "70"), ("23/2015/NĐ-CP", "5"),
        ("23/2015/NĐ-CP", "36"), ("60/2014/QH13", "17"),
        ("60/2014/QH13", "73"), ("23/2015/NĐ-CP", "20"),
        ("60/2014/QH13", "16"), ("23/2015/NĐ-CP", "18"),
        ("60/2014/QH13", "10"), ("60/2014/QH13", "1"),
        ("60/2014/QH13", "35"), ("23/2015/NĐ-CP", "22"),
        ("60/2014/QH13", "49"), ("60/2014/QH13", "18"),
        ("60/2014/QH13", "47"), ("06/2012/NĐ-CP", "1"),
        ("60/2014/QH13", "54"), ("23/2015/NĐ-CP", "32"),
        ("60/2014/QH13", "75"), ("60/2014/QH13", "28"),
        ("01/2022/TT-BTP", "8"), ("60/2014/QH13", "21"),
        ("60/2014/QH13", "22"), ("60/2014/QH13", "29"),
    ),
    "dat_dai_xay_dung": (
        ("31/2024/QH15", "40"), ("31/2024/QH15", "53"),
        ("31/2024/QH15", "133"), ("31/2024/QH15", "131"),
        ("31/2024/QH15", "22"), ("31/2024/QH15", "177"),
        ("175/2024/NĐ-CP", "54"), ("50/2014/QH13", "95"),
        ("50/2014/QH13", "3"), ("175/2024/NĐ-CP", "59"),
        ("175/2024/NĐ-CP", "55"), ("31/2024/QH15", "10"),
        ("31/2024/QH15", "24"), ("175/2024/NĐ-CP", "67"),
        ("101/2024/NĐ-CP", "22"), ("10  /2024/TT-BTNMT", "13"),
        ("101/2024/NĐ-CP", "33"), ("50/2014/QH13", "102"),
        ("167/2008/QĐ-TTg", "6"), ("15/2012/QH13", "125"),
        ("101/2024/NĐ-CP", "43"), ("101/2024/NĐ-CP", "41"),
        ("175/2024/NĐ-CP", "53"), ("175/2024/NĐ-CP", "7"),
        ("175/2024/NĐ-CP", "6"), ("175/2024/NĐ-CP", "51"),
        ("145/2025/NĐ-CP", "3"), ("50/2014/QH13", "96"),
        ("15/2012/QH13", "16"), ("101/2024/NĐ-CP", "42"),
    ),
    "cu_tru_an_ninh": (
        ("26/2023/QH15", "5"), ("68/2020/QH14", "7"),
        ("68/2020/QH14", "32"), ("26/2023/QH15", "18"),
        ("68/2020/QH14", "20"), ("68/2020/QH14", "8"),
        ("58/2026/NĐ-CP", "24"), ("26/2023/QH15", "23"),
        ("68/2020/QH14", "37"), ("17/2024/TT-BCA", "4"),
        ("154/2024/NĐ-CP", "6"), ("167/2013/NĐ-CP", "5"),
        ("58/2026/NĐ-CP", "2"), ("58/2026/NĐ-CP", "9"),
        ("154/2024/NĐ-CP", "7"), ("68/2020/QH14", "22"),
        ("68/2020/QH14", "21"), ("154/2024/NĐ-CP", "5"),
        ("58/2026/NĐ-CP", "4"), ("154/2024/NĐ-CP", "3"),
        ("68/2020/QH14", "28"), ("68/2020/QH14", "26"),
        ("17/2024/TT-BCA", "12"), ("154/2024/NĐ-CP", "8"),
        ("154/2024/NĐ-CP", "4"), ("58/2026/NĐ-CP", "5"),
        ("68/2020/QH14", "38"), ("68/2020/QH14", "33"),
        ("58/2026/NĐ-CP", "12"), ("26/2023/QH15", "27"),
    ),
    "khieu_nai_to_cao_xu_phat": (
        ("02/2011/QH13", "26"), ("88/2025/QH15", "70"),
        ("02/2011/QH13", "11"), ("15/2012/QH13", "5"),
        ("15/2012/QH13", "28"), ("02/2011/QH13", "29"),
        ("02/2011/QH13", "56"), ("15/2012/QH13", "63"),
        ("02/2011/QH13", "43"), ("15/2012/QH13", "62"),
        ("02/2011/QH13", "34"), ("02/2011/QH13", "2"),
        ("02/2011/QH13", "3"), ("02/2011/QH13", "65"),
        ("62/2015/NĐ-CP", "38"), ("02/2011/QH13", "13"),
        ("02/2011/QH13", "68"), ("02/2011/QH13", "66"),
        ("02/2011/QH13", "5"), ("02/2011/QH13", "10"),
        ("02/2011/QH13", "8"), ("02/2011/QH13", "58"),
        ("02/2011/QH13", "7"), ("02/2011/QH13", "52"),
        ("02/2011/QH13", "62"), ("02/2011/QH13", "60"),
        ("15/2012/QH13", "56"), ("02/2011/QH13", "23"),
        ("02/2011/QH13", "57"), ("02/2011/QH13", "33"),
    ),
    "an_sinh_y_te_giao_duc": (
        ("20/2021/NĐ-CP", "27"), ("41/2024/QH15", "61"),
        ("27/2013/TT-BCT", "17"), ("27/2013/TT-BCT", "16"),
        ("188/2025/NĐ-CP", "37"), ("27/2013/TT-BCT", "35"),
        ("29/2022/NĐ-CP", "13"), ("29/2022/NĐ-CP", "9"),
        ("41/2024/QH15", "130"), ("08/2015/QĐ-TTg", "21"),
        ("188/2025/NĐ-CP", "43"), ("21/2026/NQ-CP", "4"),
        ("64/2009/NĐ-CP", "5"), ("23/2018/TT-BYT", "5"),
        ("20/2021/NĐ-CP", "28"), ("41/2024/QH15", "92"),
        ("41/2024/QH15", "113"), ("04/2017/TT-BCA", "7"),
        ("188/2025/NĐ-CP", "11"), ("12/2025/TT-BGDĐT", "12"),
        ("41/2024/QH15", "3"), ("164/2025/NĐ-CP", "3"),
        ("68/2013/QH13", "2"), ("26/2025/TT-BGDĐT", "4"),
        ("15/2023/QH15", "8"), ("08/2015/QĐ-TTg", "15"),
        ("86 /2025/TT-BCA", "7"), ("64/2009/NĐ-CP", "6"),
        ("11/2025/TT-BGDĐT", "10"), ("188/2025/NĐ-CP", "56"),
    ),
}

_LIVE_VERIFIED_ADDITIONAL_PROVISIONS: dict[
    tuple[str, str], dict[str, str]
] = {
    ("29/2022/NĐ-CP", "9"): {
        "document_title": "Quy định cơ chế, chính sách trong lĩnh vực y tế phục vụ phòng, chống dịch COVID-19",
        "law_number": "29/2022/NĐ-CP",
        "article_number": "9",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=159333",
        "effective_status": "active",
    },
    ("29/2022/NĐ-CP", "13"): {
        "document_title": "Quy định cơ chế, chính sách trong lĩnh vực y tế phục vụ phòng, chống dịch COVID-19",
        "law_number": "29/2022/NĐ-CP",
        "article_number": "13",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=159333",
        "effective_status": "active",
    },
    ("41/2024/QH15", "3"): {
        "document_title": "Bảo hiểm xã hội",
        "law_number": "41/2024/QH15",
        "article_number": "3",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=175027",
        "effective_status": "active",
    },
    ("41/2024/QH15", "4"): {
        "document_title": "Luật Bảo hiểm xã hội",
        "law_number": "41/2024/QH15",
        "article_number": "4",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=175027",
        "effective_status": "active",
    },
    ("188/2025/NĐ-CP", "43"): {
        "document_title": "Hướng dẫn Luật Bảo hiểm y tế",
        "law_number": "188/2025/NĐ-CP",
        "article_number": "43",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=179711",
        "effective_status": "active",
    },
    ("12/2025/TT-BGDĐT", "12"): {
        "document_title": "Phân quyền, phân cấp quản lý nhà nước trong lĩnh vực nhà giáo và cán bộ quản lý cơ sở giáo dục",
        "law_number": "12/2025/TT-BGDĐT",
        "article_number": "12",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=179379",
        "effective_status": "active",
    },
    ("26/2025/TT-BGDĐT", "4"): {
        "document_title": "Sửa đổi các thông tư về phân quyền, phân cấp trong lĩnh vực giáo dục",
        "law_number": "26/2025/TT-BGDĐT",
        "article_number": "4",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=185886",
        "effective_status": "active",
    },
}


class BenchmarkConfigurationError(RuntimeError):
    """Raised when the local source-backed dataset cannot satisfy the gate."""


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkConfigurationError(f"Không đọc được dữ liệu: {path}") from exc
    if not isinstance(payload, dict):
        raise BenchmarkConfigurationError(f"Dữ liệu phải là JSON object: {path}")
    return payload


def _usable_source(source: Mapping[str, Any]) -> bool:
    law_number = str(source.get("law_number") or "").strip()
    status = str(source.get("effective_status") or "active").casefold()
    url = str(source.get("source_url") or "").strip()
    return bool(
        law_number
        and _fold(law_number) not in {"khong so", "unknown", "n/a"}
        and status in {"active", "current", "partially_active"}
        and url.startswith(("http://", "https://"))
    )


def _source_projection(source: Mapping[str, Any]) -> dict[str, str]:
    return {
        "law_number": str(source.get("law_number") or "").strip(),
        "article_number": str(source.get("article_number") or "").strip(),
        "source_url": str(source.get("source_url") or "").strip(),
    }


def _source_key(source: Mapping[str, Any]) -> tuple[str, str, str]:
    item = _source_projection(source)
    return (
        _fold(item["law_number"]),
        item["article_number"],
        item["source_url"],
    )


def _complexity_score(case: Mapping[str, Any]) -> tuple[int, str]:
    questions = case.get("questions") or {}
    text = _fold(
        f"{questions.get('citizen', '')} {questions.get('officer', '')}"
    )
    required_facts = [
        str(item) for item in (case.get("required_facts") or []) if str(item).strip()
    ]
    score = min(30, len(text.split()) // 3)
    score += max(0, len(required_facts) - 2) * 5
    score += sum(4 for marker in _COMPLEXITY_MARKERS if marker in text)
    score += 4 if case.get("event_date") else 0
    return score, str(case.get("case_id") or "")


def _record_pairs(golden: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    pairs: dict[str, dict[str, Any]] = {}
    for raw in golden.get("records") or []:
        if not isinstance(raw, Mapping):
            continue
        case_id = str(raw.get("case_id") or "").strip()
        domain = str(raw.get("domain") or "").strip()
        role = str(raw.get("role") or "").strip()
        if not case_id or domain not in BENCHMARK_DOMAINS or role not in ROLES:
            continue
        pair = pairs.setdefault(
            case_id,
            {
                "case_id": case_id,
                "domain": domain,
                "questions": {},
                "required_facts": list(raw.get("required_facts") or []),
                "legal_as_of": str(raw.get("legal_as_of") or "2026-08-09"),
                "event_date": raw.get("event_date"),
            },
        )
        pair["questions"][role] = str(
            raw.get(f"{role}_question") or raw.get("question") or ""
        ).strip()
    return {
        case_id: pair
        for case_id, pair in pairs.items()
        if all(str(pair["questions"].get(role) or "").strip() for role in ROLES)
    }


def _source_backed_case(
    *,
    domain: str,
    index: int,
    difficulty: str,
    source: Mapping[str, Any],
    legal_as_of: str,
) -> dict[str, Any]:
    projected = _source_projection(source)
    provision = (
        f"Điều {projected['article_number']} của văn bản {projected['law_number']}"
        if projected["article_number"]
        else f"văn bản {projected['law_number']}"
    )
    if difficulty == "easy":
        citizen_question = (
            f"{provision} quy định nội dung gì? Hãy trình bày lần lượt, giải thích "
            "bằng từ dễ hiểu và nêu ý nghĩa thực tế cho người dân tại Hải Phòng."
        )
        officer_question = (
            f"Tóm lược lần lượt, chính xác nội dung của {provision} để hướng dẫn "
            "người dân; nêu ý nghĩa thực tế và chỉ sử dụng căn cứ đang có hiệu lực."
        )
    elif difficulty == "medium":
        citizen_question = (
            f"Theo {provision}, người dân cần hiểu những nội dung chính nào? "
            "Hãy trình bày lần lượt và nêu ý nghĩa thực tế bằng lời dễ hiểu."
        )
        officer_question = (
            f"Phân tích chính xác các ý chính của {provision} để cán bộ dùng khi "
            "hướng dẫn; trình bày lần lượt, tách rõ quy tắc, ý nghĩa thực tế và lưu ý "
            "nghiệp vụ, không suy diễn ngoài nguồn."
        )
    else:
        citizen_question = (
            f"Hãy giải thích chi tiết, có hệ thống {provision}: từng quy tắc nói gì, "
            "vận dụng thực tế ra sao và người dân Hải Phòng cần lưu ý gì để không hiểu sai."
        )
        officer_question = (
            f"Lập bản giải thích chuyên sâu {provision}: phân tách từng quy tắc, phạm "
            "vi nội dung, ý nghĩa thực tế và lưu ý khi hướng dẫn người dân; chỉ kết "
            "luận trong giới hạn điều khoản đang có hiệu lực."
        )
    return {
        "case_id": f"{domain}:{difficulty}:{index:02d}",
        "domain": domain,
        "difficulty": difficulty,
        "legal_as_of": legal_as_of,
        "questions": {
            "citizen": citizen_question,
            "officer": officer_question,
        },
        "required_facts": [],
        "expected_citations_any": [projected],
        "source_case_id": None,
    }


def build_benchmark_cases(
    golden: Mapping[str, Any], retrieval: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Build exactly 30 source-backed questions for each required domain."""

    pairs = _record_pairs(golden)
    retrieval_by_id = {
        str(item.get("case_id") or ""): item
        for item in (retrieval.get("cases") or [])
        if isinstance(item, Mapping)
        and item.get("classification") == "FOUND_AND_RETRIEVED"
    }
    candidates_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case_id, pair in pairs.items():
        retrieved = retrieval_by_id.get(case_id)
        if not retrieved:
            continue
        sources = [
            _source_projection(item)
            for item in (retrieved.get("top5") or [])
            if isinstance(item, Mapping) and _usable_source(item)
        ]
        if not sources:
            continue
        candidate = dict(pair)
        candidate["expected_citations_any"] = list(
            {_source_key(source): source for source in sources}.values()
        )
        candidates_by_domain[candidate["domain"]].append(candidate)

    selected: list[dict[str, Any]] = []
    for domain in BENCHMARK_DOMAINS:
        candidates = candidates_by_domain.get(domain, [])
        if len(candidates) < 20:
            raise BenchmarkConfigurationError(
                f"{domain} chỉ có {len(candidates)} ca nguồn đạt, cần tối thiểu 20."
            )
        source_pool: list[dict[str, str]] = []
        seen_sources: set[tuple[str, str, str]] = set()
        for candidate in candidates:
            for source in candidate["expected_citations_any"]:
                key = _source_key(source)
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                source_pool.append(source)
        if len(source_pool) < 20:
            raise BenchmarkConfigurationError(
                f"{domain} chỉ có {len(source_pool)} căn cứ trực tiếp duy nhất, cần 20."
            )

        by_provision = {
            (_fold(source["law_number"]), source["article_number"]): source
            for source in source_pool
        }
        verified_keys = _VERIFIED_PROVISIONS[domain]
        verified_sources = [
            by_provision.get((_fold(law_number), article_number))
            or _LIVE_VERIFIED_ADDITIONAL_PROVISIONS.get(
                (law_number, article_number)
            )
            for law_number, article_number in verified_keys
        ]
        if all(verified_sources):
            selected_sources = [dict(source) for source in verified_sources if source]
        else:
            # Synthetic unit fixtures and future reviewed snapshots can still
            # exercise the builder without pretending their sources passed the
            # 2026-08-09 live preflight. Production data must resolve all 30.
            selected_sources = [
                source_pool[index % len(source_pool)] for index in range(30)
            ]

        legal_as_of = "2026-08-09"
        domain_cases = []
        for offset, difficulty in enumerate(DIFFICULTIES):
            for index, source in enumerate(
                selected_sources[offset * 10 : (offset + 1) * 10], start=1
            ):
                domain_cases.append(
                    _source_backed_case(
                        domain=domain,
                        index=index,
                        difficulty=difficulty,
                        source=source,
                        legal_as_of=legal_as_of,
                    )
                )
        if len({item["questions"]["citizen"] for item in domain_cases}) != 30:
            raise BenchmarkConfigurationError(f"{domain} có câu hỏi người dân bị lặp.")
        if len({item["questions"]["officer"] for item in domain_cases}) != 30:
            raise BenchmarkConfigurationError(f"{domain} có câu hỏi cán bộ bị lặp.")
        selected.extend(domain_cases)
    return selected


def build_expired_source_block_cases() -> list[dict[str, Any]]:
    """Return the negative gate proving an expired document is never served."""

    return [
        {
            "case_id": "expired-block:96-2014-tt-bqp:26",
            "domain": "an_sinh_y_te_giao_duc",
            "difficulty": "easy",
            "legal_as_of": "2026-08-10",
            "questions": {
                "citizen": (
                    "Điều 26 của văn bản 96/2014/TT-BQP hiện còn được dùng để tư vấn "
                    "không? Nếu hết hiệu lực, hãy nói rõ không dùng để trả lời hiện hành."
                ),
                "officer": (
                    "Kiểm tra Điều 26 văn bản 96/2014/TT-BQP để hướng dẫn nghiệp vụ "
                    "hiện hành; phải chặn nếu văn bản đã hết hiệu lực."
                ),
            },
            "required_facts": [],
            "expected_citations_any": [],
            "forbidden_citations": [dict(_EXPIRED_BLOCK_SOURCE)],
            "expected_outcome": "blocked_expired",
            "source_case_id": None,
        }
    ]


def build_quality_gate_cases(
    golden: Mapping[str, Any], retrieval: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return 150 active-source cases plus the explicit expired-source gate."""

    return [
        *build_benchmark_cases(golden, retrieval),
        *build_expired_source_block_cases(),
    ]


def _all_citations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("citations") or []:
        if isinstance(item, Mapping):
            rows.append(dict(item))
    for section in payload.get("answer_sections") or []:
        if not isinstance(section, Mapping):
            continue
        for item in section.get("citations") or []:
            if isinstance(item, Mapping):
                rows.append(dict(item))
    return list({_source_key(item): item for item in rows}.values())


def _citation_matches(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    actual_law, actual_article, _ = _source_key(actual)
    expected_law, expected_article, _ = _source_key(expected)
    return bool(
        actual_law
        and actual_law == expected_law
        and (not expected_article or actual_article == expected_article)
    )


def _has_repeated_labels_or_claims(answer: str) -> bool:
    labels: Counter[str] = Counter()
    claims: Counter[str] = Counter()
    for raw_line in str(answer or "").splitlines():
        line = raw_line.strip()
        if not line.startswith(('-', '*')):
            continue
        bullet = re.sub(r"^[-*+]\s+", "", line).strip()
        label_match = re.match(r"(?:\*\*)?([^:*]{2,80}):(?:\*\*)?", bullet)
        if label_match:
            labels[_fold(label_match.group(1))] += 1
        claim = re.sub(r"^\*\*[^*]+:\*\*\s*", "", bullet).strip()
        if len(_fold(claim)) >= 12:
            claims[_fold(claim)] += 1
    return any(count > 1 for count in labels.values()) or any(
        count > 1 for count in claims.values()
    )


def _minimum_answer_characters(case: Mapping[str, Any], role: str) -> int:
    difficulty = str(case.get("difficulty") or "easy")
    return _MINIMUM_ANSWER_CHARACTERS.get(
        difficulty, _MINIMUM_ANSWER_CHARACTERS["easy"]
    ).get(role, _MINIMUM_ANSWER_CHARACTERS["easy"]["citizen"])


def _is_substantive_answer(answer: str) -> bool:
    folded = re.sub(r"[^a-z0-9 ]+", " ", _fold(answer))
    folded = re.sub(r"\s+", " ", folded).strip()
    return not any(
        pattern in folded and len(folded) <= len(pattern) + 80
        for pattern in _VACUOUS_RULE_ONLY_PATTERNS
    )


def _source_is_current(source: Mapping[str, Any], *, legal_as_of: str) -> bool:
    status = str(
        source.get("effective_status")
        or source.get("document_status")
        or ""
    ).strip().casefold()
    if status not in {"active", "current", "partially_active"}:
        return False
    effective_to = str(
        source.get("effective_to") or source.get("expired_date") or ""
    ).strip()[:10]
    return not (effective_to and effective_to <= str(legal_as_of)[:10])


def _completeness_gate(payload: Mapping[str, Any]) -> dict[str, bool]:
    report = payload.get("answer_completeness")
    if not isinstance(report, Mapping):
        return {
            "answer_complete": False,
            "coverage_complete": False,
            "order_correct": False,
            "practical_interpretation": False,
        }
    checks = report.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    source_units = int(report.get("source_unit_count") or 0)
    covered_units = int(report.get("covered_unit_count") or 0)
    measured_ratio = covered_units / source_units if source_units > 0 else 0.0
    coverage_complete = bool(
        report.get("status") == "complete"
        and source_units > 0
        and float(report.get("coverage_ratio") or 0.0) >= 0.9
        and measured_ratio >= 0.9
        and checks.get("coverage") is True
    )
    return {
        "answer_complete": report.get("status") == "complete",
        "coverage_complete": coverage_complete,
        "order_correct": coverage_complete and checks.get("order") is True,
        "practical_interpretation": checks.get("practical_meaning") is True,
    }


def _claims_are_verified(payload: Mapping[str, Any], *, answer: str) -> bool:
    claims = payload.get("claim_validation")
    if not isinstance(claims, list) or not claims:
        return False
    folded_answer = _fold(answer)
    for claim in claims:
        if not isinstance(claim, Mapping):
            return False
        if claim.get("status") == "verified":
            continue
        original = _fold(str(claim.get("original") or "").strip())
        if claim.get("status") != "rejected" or not original or original in folded_answer:
            return False
    return True


def _fallback_label_is_correct(payload: Mapping[str, Any]) -> bool:
    flags = {
        str(flag).strip().casefold()
        for flag in (payload.get("quality_flags") or [])
        if str(flag).strip()
    }
    fallback_flags = {flag for flag in flags if "fallback" in flag or "blocked" in flag}
    if fallback_flags and not fallback_flags <= _KNOWN_FALLBACK_LABELS:
        return False
    if "provider_fallback" in flags:
        error = payload.get("error")
        return bool(
            str(payload.get("answer_mode") or "")
            in {"verified_source_condensed", "source_view_only"}
            and isinstance(error, Mapping)
            and error.get("code") == "AI_PROVIDER_FALLBACK"
        )
    return True


def _evaluate_expired_block_response(
    *,
    case: Mapping[str, Any],
    payload: Mapping[str, Any],
    http_status: int,
    elapsed_seconds: float,
    transport_error: str | None,
) -> dict[str, Any]:
    answer = str(payload.get("answer") or "").strip()
    folded = _fold(answer)
    citations = _all_citations(payload)
    forbidden = [
        item
        for item in (case.get("forbidden_citations") or [])
        if isinstance(item, Mapping)
    ]
    expired_citation_absent = not any(
        _citation_matches(actual, blocked)
        for actual in citations
        for blocked in forbidden
    )
    explicit_expired_label = bool(
        "het hieu luc" in folded
        and bool(re.search(r"\bkhong(?:\s+duoc)?\s+(?:dung|su dung)\b", folded))
        and "hien hanh" in folded
    )
    checks = {
        "http_ok": http_status == 200 and not transport_error,
        "answer_present": bool(answer),
        "expired_source_blocked": expired_citation_absent,
        "expired_status_labeled": explicit_expired_label,
        "no_expired_source": all(
            _source_is_current(
                citation,
                legal_as_of=str(case.get("legal_as_of") or "2026-08-10"),
            )
            for citation in citations
        ),
        "fallback_label_correct": (
            explicit_expired_label and _fallback_label_is_correct(payload)
        ),
        "no_internal_marker": not bool(_INTERNAL_MARKERS.search(answer)),
        "within_response_budget": elapsed_seconds <= 900.0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "pass": not failed,
        "score": round(10.0 * sum(checks.values()) / len(checks), 2),
        "checks": checks,
        "review_comment": (
            "Đạt: đã chặn 96/2014/TT-BQP và ghi rõ không dùng cho trả lời hiện hành."
            if not failed
            else "Chưa đạt ca chặn hết hiệu lực: " + ", ".join(failed) + "."
        ),
        "failed_checks": failed,
        "citation_count": len(citations),
        "section_count": len(payload.get("answer_sections") or []),
    }


def evaluate_response(
    *,
    case: Mapping[str, Any],
    role: str,
    payload: Mapping[str, Any],
    http_status: int,
    elapsed_seconds: float,
    transport_error: str | None,
) -> dict[str, Any]:
    """Return a strict role-specific assessment and plain-Vietnamese note."""

    if case.get("expected_outcome") == "blocked_expired":
        return _evaluate_expired_block_response(
            case=case,
            payload=payload,
            http_status=http_status,
            elapsed_seconds=elapsed_seconds,
            transport_error=transport_error,
        )

    answer = str(payload.get("answer") or "").strip()
    folded = _fold(answer)
    sections = [
        dict(item)
        for item in (payload.get("answer_sections") or [])
        if isinstance(item, Mapping)
    ]
    citations = _all_citations(payload)
    expected = [
        item
        for item in (case.get("expected_citations_any") or [])
        if isinstance(item, Mapping)
    ]
    citations_valid = bool(citations) and all(_usable_source(item) for item in citations)
    expected_citation_present = bool(expected) and any(
        _citation_matches(actual, wanted)
        for actual in citations
        for wanted in expected
    )
    sections_complete = bool(sections) and all(
        section.get("status") == "sufficiently_evidenced"
        and bool(str(section.get("answer") or "").strip())
        and bool(section.get("citations"))
        for section in sections
    )
    no_clarification = all(
        not str(section.get("clarifying_question") or "").strip()
        for section in sections
    )
    no_forbidden = not any(phrase in folded for phrase in _FORBIDDEN_FALLBACKS)
    presentation_clear = bool(
        re.search(r"(?m)^##\s+\S", answer)
        and re.search(r"(?m)^-\s+\S", answer)
        and not re.search(r"(?m)^#{1,6}\s*$", answer)
    )
    role_blob = _fold(
        " ".join(
            [answer, *[str(section.get("title") or "") for section in sections]]
        )
    )
    role_appropriate = (
        any(
            cue in role_blob
            for cue in (
                "ket luan chuyen mon",
                "tham quyen",
                "nghiep vu",
                "quy trinh xu ly",
                "can cu",
            )
        )
        if role == "officer"
        else not any(
            marker in role_blob
            for marker in ("coverage", "provenance", "packet id", "trace id")
        )
    )
    completeness_checks = _completeness_gate(payload)
    checks = {
        "http_ok": http_status == 200 and not transport_error,
        "answer_present": bool(answer),
        "difficulty_detail": len(answer) >= _minimum_answer_characters(case, role),
        "substantive_answer": _is_substantive_answer(answer),
        "fully_grounded": payload.get("grounding_status") == "fully_grounded",
        **completeness_checks,
        "sections_complete": sections_complete,
        "citations_valid": citations_valid,
        "correct_document_article": expected_citation_present,
        "no_expired_source": all(
            _source_is_current(
                citation,
                legal_as_of=str(case.get("legal_as_of") or "2026-08-10"),
            )
            for citation in citations
        ),
        "no_fabricated_claim": _claims_are_verified(payload, answer=answer),
        "fallback_label_correct": _fallback_label_is_correct(payload),
        "no_forbidden_fallback": no_forbidden,
        "no_clarifying_question": no_clarification,
        "no_internal_marker": not bool(_INTERNAL_MARKERS.search(answer)),
        "no_repeated_labels": not _has_repeated_labels_or_claims(answer),
        "presentation_clear": presentation_clear,
        "role_appropriate": role_appropriate,
        "within_response_budget": elapsed_seconds <= 900.0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    labels = {
        "http_ok": "lỗi kết nối/API",
        "answer_present": "câu trả lời rỗng",
        "difficulty_detail": "độ chi tiết chưa tương xứng với mức câu hỏi",
        "substantive_answer": "nội dung chỉ là dẫn chiếu chung, chưa giải thích được quy tắc",
        "fully_grounded": "chưa được kiểm chứng đầy đủ",
        "answer_complete": "chưa hoàn tất toàn bộ yêu cầu của câu hỏi",
        "coverage_complete": "chỉ sao chép một phần, chưa đủ khoản/điểm quan trọng",
        "order_correct": "các khoản/điểm chưa được trình bày đúng thứ tự",
        "practical_interpretation": "chưa có diễn giải ý nghĩa thực tế",
        "sections_complete": "còn mục chưa có câu trả lời",
        "citations_valid": "trích dẫn thiếu metadata chính thức",
        "correct_document_article": "sai văn bản hoặc sai điều",
        "no_expired_source": "đã sử dụng văn bản hết hiệu lực",
        "no_fabricated_claim": "còn nhận định chưa được kiểm chứng",
        "fallback_label_correct": "chế độ dự phòng chưa được gắn nhãn đúng",
        "no_forbidden_fallback": "còn câu thoái lui bị cấm",
        "no_clarifying_question": "còn yêu cầu bổ sung thông tin",
        "no_internal_marker": "lộ mã kỹ thuật nội bộ",
        "no_repeated_labels": "lặp nhãn hoặc lặp ý",
        "presentation_clear": "trình bày chưa rõ theo đề mục",
        "role_appropriate": "chưa phù hợp vai trò",
        "within_response_budget": "vượt ngân sách 900 giây",
    }
    passed = not failed
    comment = (
        f"Đạt mức {case.get('difficulty')}: {len(answer)} ký tự, "
        f"{len(sections)} mục có căn cứ, {len(citations)} trích dẫn; "
        f"trình bày rõ và phù hợp vai trò {role}."
        if passed
        else "Chưa đạt: " + "; ".join(labels[name] for name in failed) + "."
    )
    return {
        "pass": passed,
        "score": round(10.0 * sum(checks.values()) / len(checks), 2),
        "checks": checks,
        "review_comment": comment,
        "failed_checks": failed,
        "citation_count": len(citations),
        "section_count": len(sections),
    }


def _load_tokens(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise BenchmarkConfigurationError(f"Không đọc được tệp token: {path}") from exc
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    tokens: dict[str, str] = {}
    for role, keys in _TOKEN_ENV_KEYS.items():
        token = next((values.get(key, "") for key in keys if values.get(key, "")), "")
        if not token:
            raise BenchmarkConfigurationError(f"Thiếu token cho vai trò {role}.")
        tokens[role] = token
    return tokens


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def apply_cross_response_checks(rows: Sequence[dict[str, Any]]) -> None:
    """Reject repeated public answers across distinct benchmark questions."""

    by_answer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        answer = str((row.get("response") or {}).get("answer") or "")
        normalized = re.sub(r"\s+", " ", _fold(answer)).strip()
        if normalized:
            by_answer[normalized].append(row)

    repeated_ids = {
        str(row.get("response_id") or "")
        for grouped in by_answer.values()
        if len({str(item.get("case_id") or "") for item in grouped}) > 1
        for row in grouped
    }
    for row in rows:
        evaluation = row.get("evaluation") or {}
        checks = dict(evaluation.get("checks") or {})
        unique = str(row.get("response_id") or "") not in repeated_ids
        checks["unique_answer"] = unique
        failed = [name for name, passed in checks.items() if not passed]
        evaluation["checks"] = checks
        evaluation["failed_checks"] = failed
        evaluation["pass"] = not failed
        evaluation["score"] = round(
            10.0 * sum(bool(value) for value in checks.values()) / len(checks), 2
        )
        if not unique:
            evaluation["review_comment"] = (
                "Chưa đạt: câu trả lời trùng nguyên văn với một câu hỏi khác."
            )
        row["evaluation"] = evaluation


def _summary(cases: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_group: dict[str, dict[str, Any]] = {}
    for domain in BENCHMARK_DOMAINS:
        for difficulty in DIFFICULTIES:
            for role in ROLES:
                group_rows = [
                    row
                    for row in rows
                    if row.get("domain") == domain
                    and row.get("difficulty") == difficulty
                    and row.get("role") == role
                ]
                key = f"{domain}:{difficulty}:{role}"
                by_group[key] = {
                    "count": len(group_rows),
                    "pass_count": sum(1 for row in group_rows if row.get("evaluation", {}).get("pass")),
                    "average_score": round(
                        statistics.mean(
                            float(row.get("evaluation", {}).get("score") or 0.0)
                            for row in group_rows
                        ),
                        2,
                    ) if group_rows else 0.0,
                }
    latencies = [float(row.get("elapsed_seconds") or 0.0) for row in rows]
    failures = Counter(
        check
        for row in rows
        for check in row.get("evaluation", {}).get("failed_checks") or []
    )
    expected_response_count = len(cases) * len(ROLES)
    pass_count = sum(1 for row in rows if row.get("evaluation", {}).get("pass"))
    domain_role_reviews: dict[str, dict[str, Any]] = {}
    for domain in BENCHMARK_DOMAINS:
        for role in ROLES:
            review_rows = [
                row
                for row in rows
                if row.get("domain") == domain and row.get("role") == role
            ]
            review_passes = sum(
                1 for row in review_rows if row.get("evaluation", {}).get("pass")
            )
            answer_lengths = [
                len(str((row.get("response") or {}).get("answer") or ""))
                for row in review_rows
            ]
            average_characters = round(statistics.mean(answer_lengths), 1) if answer_lengths else 0.0
            key = f"{domain}:{role}"
            domain_role_reviews[key] = {
                "count": len(review_rows),
                "pass_count": review_passes,
                "average_answer_characters": average_characters,
                "review_comment": (
                    f"Đạt {review_passes}/{len(review_rows)}; câu trả lời trung bình "
                    f"{average_characters} ký tự, có căn cứ trực tiếp, đúng vai trò và không trùng nguyên văn."
                    if review_rows and review_passes == len(review_rows)
                    else f"Chưa đạt {len(review_rows) - review_passes}/{len(review_rows)} lượt; xem failed_check_counts."
                ),
            }
    return {
        "schema_version": "five-domain-quality-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_question_count": len(cases),
        "positive_question_count": sum(
            1 for case in cases if case.get("expected_outcome") != "blocked_expired"
        ),
        "expired_block_question_count": sum(
            1 for case in cases if case.get("expected_outcome") == "blocked_expired"
        ),
        "expected_response_count": expected_response_count,
        "completed_response_count": len(rows),
        "pass_count": pass_count,
        "failure_count": len(rows) - pass_count,
        "all_answers_pass": len(rows) == expected_response_count and pass_count == expected_response_count,
        "domain_question_counts": {
            domain: sum(1 for case in cases if case.get("domain") == domain)
            for domain in BENCHMARK_DOMAINS
        },
        "difficulty_question_counts": {
            difficulty: sum(1 for case in cases if case.get("difficulty") == difficulty)
            for difficulty in DIFFICULTIES
        },
        "role_response_counts": {
            role: sum(1 for row in rows if row.get("role") == role)
            for role in ROLES
        },
        "latency_seconds": {
            "median": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(_percentile(latencies, 0.95), 3),
            "maximum": round(max(latencies), 3) if latencies else 0.0,
        },
        "failed_check_counts": dict(sorted(failures.items())),
        "groups": by_group,
        "domain_role_reviews": domain_role_reviews,
        "reviews": [
            {
                "case_id": row.get("case_id"),
                "domain": row.get("domain"),
                "difficulty": row.get("difficulty"),
                "role": row.get("role"),
                "pass": row.get("evaluation", {}).get("pass"),
                "score": row.get("evaluation", {}).get("score"),
                "review_comment": row.get("evaluation", {}).get("review_comment"),
            }
            for row in rows
        ],
    }


async def run_live(
    *,
    cases: Sequence[Mapping[str, Any]],
    base_url: str,
    tokens: Mapping[str, str],
    timeout_seconds: float,
    concurrency: int,
    checkpoint_path: Path,
    resume: bool,
) -> list[dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if resume and checkpoint_path.exists():
        checkpoint = _read_json(checkpoint_path)
        completed = {
            str(row.get("response_id")): dict(row)
            for row in checkpoint.get("responses") or []
            if isinstance(row, Mapping) and row.get("response_id")
        }
    semaphore = asyncio.Semaphore(max(1, concurrency))
    checkpoint_lock = asyncio.Lock()
    timeout = httpx.Timeout(timeout_seconds)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        async def execute(case: Mapping[str, Any], role: str) -> None:
            response_id = f"{case['case_id']}:{role}"
            if response_id in completed:
                return
            async with semaphore:
                started = time.perf_counter()
                payload: dict[str, Any] = {}
                status = 0
                transport_error: str | None = None
                try:
                    response = await client.post(
                        "/api/search/ask/simple",
                        headers={"Authorization": f"Bearer {tokens[role]}"},
                        json={
                            "question": case["questions"][role],
                            "role": role,
                            "domain": case["domain"],
                            "legal_as_of": case.get("legal_as_of") or "2026-08-09",
                            "idempotency_key": f"five-domain-quality-v2-{uuid.uuid4().hex}",
                        },
                    )
                    status = response.status_code
                    if response.headers.get("content-type", "").startswith("application/json"):
                        parsed = response.json()
                        payload = parsed if isinstance(parsed, dict) else {}
                    if status != 200:
                        transport_error = f"http_{status}"
                except httpx.TimeoutException:
                    transport_error = "timeout"
                except httpx.HTTPError:
                    transport_error = "transport_error"
                elapsed = time.perf_counter() - started
                evaluation = evaluate_response(
                    case=case,
                    role=role,
                    payload=payload,
                    http_status=status,
                    elapsed_seconds=elapsed,
                    transport_error=transport_error,
                )
                row = {
                    "response_id": response_id,
                    "case_id": case["case_id"],
                    "source_case_id": case.get("source_case_id"),
                    "domain": case["domain"],
                    "difficulty": case["difficulty"],
                    "role": role,
                    "question": case["questions"][role],
                    "required_facts": case.get("required_facts") or [],
                    "expected_citations_any": case["expected_citations_any"],
                    "http_status": status,
                    "transport_error": transport_error,
                    "elapsed_seconds": round(elapsed, 3),
                    "response": payload,
                    "evaluation": evaluation,
                }
                async with checkpoint_lock:
                    completed[response_id] = row
                    _write_json(
                        checkpoint_path,
                        {
                            "schema_version": "five-domain-quality-checkpoint-v2",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "responses": list(completed.values()),
                        },
                    )

        await asyncio.gather(
            *(execute(case, role) for case in cases for role in ROLES)
        )
    return [completed[key] for key in sorted(completed)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--credentials-file", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--private-report", type=Path, default=DEFAULT_PRIVATE_REPORT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Run sequentially by default because the local SurrealDB websocket is shared.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--difficulty", choices=DIFFICULTIES)
    parser.add_argument("--max-cases-per-domain", type=int)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Validate and summarize the quality-gate plan without network calls.",
    )
    return parser


def _filter_cases(cases: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected_ids = set(args.case_ids or ())
    selected = [
        case
        for case in cases
        if (not selected_ids or case["case_id"] in selected_ids)
        and (not args.difficulty or case["difficulty"] == args.difficulty)
    ]
    if selected_ids:
        unknown = selected_ids - {case["case_id"] for case in selected}
        if unknown:
            raise BenchmarkConfigurationError(f"Mã ca không tồn tại: {sorted(unknown)}")
    if args.max_cases_per_domain is not None:
        limit = max(0, args.max_cases_per_domain)
        counts: Counter[str] = Counter()
        limited = []
        for case in selected:
            if case.get("expected_outcome") == "blocked_expired":
                limited.append(case)
                continue
            if counts[case["domain"]] >= limit:
                continue
            counts[case["domain"]] += 1
            limited.append(case)
        selected = limited
    return selected


def main() -> int:
    args = build_parser().parse_args()
    cases = build_quality_gate_cases(_read_json(args.golden), _read_json(args.retrieval))
    selected = _filter_cases(cases, args)
    if args.plan_only:
        plan = {
            "base_question_count": len(selected),
            "response_count": len(selected) * len(ROLES),
            "domains": dict(Counter(case["domain"] for case in selected)),
            "difficulties": dict(Counter(case["difficulty"] for case in selected)),
            "roles": list(ROLES),
        }
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    tokens = _load_tokens(args.credentials_file)
    rows = asyncio.run(
        run_live(
            cases=selected,
            base_url=args.base_url,
            tokens=tokens,
            timeout_seconds=max(30.0, min(900.0, args.timeout_seconds)),
            concurrency=max(1, min(20, args.concurrency)),
            checkpoint_path=args.checkpoint,
            resume=args.resume,
        )
    )
    apply_cross_response_checks(rows)
    _write_json(
        args.checkpoint,
        {
            "schema_version": "five-domain-quality-checkpoint-v2",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "responses": rows,
        },
    )
    private = {
        "schema_version": "five-domain-quality-private-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": selected,
        "responses": rows,
    }
    summary = _summary(selected, rows)
    _write_json(args.private_report, private)
    _write_json(args.summary, summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "base_question_count",
                    "expected_response_count",
                    "completed_response_count",
                    "pass_count",
                    "failure_count",
                    "all_answers_pass",
                    "latency_seconds",
                    "failed_check_counts",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["all_answers_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
