#!/usr/bin/env python
"""Generate the Stage E candidate pool with local Ollama and resumable custody.

The script never approves a legal case.  Development candidates are written in
the repository; sealed holdout candidates are written only under an external
custody root.  Ragas and DeepEval alternate as local model adapters.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.retrieval_eval_generation import (  # noqa: E402
    DOMAINS,
    build_candidate,
    build_generation_plan,
    build_generation_provenance,
    ensure_external_holdout_path,
    normalize_question,
    require_local_ollama_url,
    validate_generated_question,
)


GOLDEN_PATH = REPO_ROOT / "notebook_data/feature016-golden-1000-approved.json"
HARD_NEGATIVE_PATH = REPO_ROOT / "reports/feature016/phase-c/hard-negatives-v1.json"
REVIEW_QUEUE_PATH = REPO_ROOT / "reports/retrieval-release-v2/retrieval-eval-review-queue-v1.json"
INVENTORY_PATH = REPO_ROOT / "reports/retrieval-release-v2/source-inventory-reconciliation-v6-attested.json"
DEFAULT_DEVELOPMENT_PATH = REPO_ROOT / "reports/retrieval-release-v2/retrieval-eval-candidates-development-v1.jsonl"
DEFAULT_STATE_PATH = REPO_ROOT / "reports/retrieval-release-v2/retrieval-eval-generation-state-v1.json"
DEFAULT_REJECTED_PATH = REPO_ROOT / "reports/retrieval-release-v2/retrieval-eval-generation-rejected-v1.jsonl"

DOMAIN_ALIASES = {
    "Hộ tịch/chứng thực": "Hộ tịch/chứng thực",
    "Đất đai/xây dựng": "Đất đai/xây dựng/môi trường",
    "Đất đai/xây dựng/môi trường": "Đất đai/xây dựng/môi trường",
    "Cư trú/an ninh": "Cư trú/căn cước/an ninh",
    "Cư trú/căn cước/an ninh": "Cư trú/căn cước/an ninh",
    "Khiếu nại/tố cáo/xử phạt": "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "Khiếu nại/tố cáo/tiếp công dân/xử phạt": "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "An sinh/y tế/giáo dục": "An sinh/y tế/giáo dục",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_first_observation(queue_case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for proposal in queue_case.get("positive_source_proposals", []):
        for observation in proposal.get("inventory_observations", []):
            if observation.get("document_id") is not None:
                return proposal.get("requested", {}), observation
    return None


def _source_seed_catalog() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    golden_root = _load(GOLDEN_PATH)
    golden_cases = golden_root["cases"]
    hard_cases = _load(HARD_NEGATIVE_PATH)["examples"]
    queue_root = _load(REVIEW_QUEUE_PATH)
    queue_by_id = {str(item["case_id"]): item for item in queue_root["cases"]}
    inventory_root = _load(INVENTORY_PATH)
    inventory_by_id = {str(item["document_id"]): item for item in inventory_root["documents"]}

    quote_by_reference: dict[tuple[str, str], str] = {}
    quote_by_law: dict[str, str] = {}
    for item in golden_cases:
        for expected in item.get("expected_sources", []):
            quote = str(expected.get("proof", {}).get("quote") or "").strip()
            law = str(expected.get("law_number") or "").strip()
            article = str(expected.get("article") or "").strip()
            if quote and law:
                quote_by_reference.setdefault((law, article), quote)
                quote_by_law.setdefault(law, quote)

    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    legacy_rows: list[tuple[str, dict[str, Any], str]] = []
    for item in golden_cases:
        legacy_rows.append(("golden", item, str(item.get("questions", {}).get("citizen") or "")))
    for item in hard_cases:
        legacy_rows.append(("hard-negative", item, str(item.get("question") or "")))

    for origin, item, question in legacy_rows:
        queue_case = queue_by_id.get(str(item.get("case_id")))
        pair = _extract_first_observation(queue_case or {})
        if not pair:
            continue
        requested, observation = pair
        inventory = inventory_by_id.get(str(observation.get("document_id")))
        if not inventory or inventory.get("legal_review_required") or inventory.get("legal_review_status") != "owner_attested":
            continue
        law = str(requested.get("law_number") or inventory.get("law_number") or "").strip()
        article = str(requested.get("article") or "").strip()
        quote = quote_by_reference.get((law, article)) or quote_by_law.get(law)
        if not quote:
            continue
        domain = DOMAIN_ALIASES.get(str(item.get("domain") or queue_case.get("domain_observed") or ""))
        if domain not in DOMAINS:
            continue
        source_url = str(inventory.get("source_url") or inventory.get("metadata_evidence_url") or "").strip()
        if not source_url:
            continue
        pools[domain].append(
            {
                "origin": origin,
                "legacy_case_id": str(item.get("case_id")),
                "legacy_question": question.strip(),
                "legacy_expected_refusal": bool(item.get("expected_refusal", False)),
                "legacy_tags": list(item.get("risk_tags", [])),
                "context": quote,
                "legal_as_of": str(item.get("legal_as_of") or inventory_root.get("legal_as_of")),
                "source": {
                    "document_id": str(inventory["document_id"]),
                    "law_number": law,
                    "article": article or None,
                    "paragraph": requested.get("paragraph"),
                    "point": requested.get("point"),
                    "official_source_url": source_url,
                    "source_snapshot_sha256": str(inventory_root["source_snapshot_sha256"]),
                    "passage_sha256": _sha256_text(quote),
                    "metadata_attested": True,
                    "jurisdiction": "Việt Nam",
                    "validity_from": inventory.get("effective_date"),
                    "validity_to": inventory.get("expired_date"),
                    "serving_state": inventory.get("serving_state"),
                    "metadata_attestation_sha256": inventory.get("metadata_attestation_sha256"),
                },
            }
        )

    missing_domains = [domain for domain in DOMAINS if not pools.get(domain)]
    if missing_domains:
        raise RuntimeError(f"no attested source seeds for domains: {missing_domains}")
    metadata = {
        "source_snapshot_sha256": inventory_root["source_snapshot_sha256"],
        "source_seed_counts": {domain: len(pools[domain]) for domain in DOMAINS},
        "input_checksums": {
            str(path.relative_to(REPO_ROOT)): _file_sha256(path)
            for path in (GOLDEN_PATH, HARD_NEGATIVE_PATH, REVIEW_QUEUE_PATH, INVENTORY_PATH)
        },
    }
    return dict(pools), metadata


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def _rewrite_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def _extract_questions(raw: str) -> list[str]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    starts = [position for position in (text.find("{"), text.find("[")) if position >= 0]
    if starts:
        text = text[min(starts) :]
    for end_char in ("}", "]"):
        end = text.rfind(end_char)
        if end >= 0:
            candidate = text[: end + 1]
            try:
                payload = json.loads(candidate)
                if isinstance(payload, dict):
                    payload = payload.get("questions", [])
                if isinstance(payload, list):
                    return [str(item).strip() for item in payload if str(item).strip()]
            except json.JSONDecodeError:
                continue
    return [line.strip(" -\t0123456789.)") for line in raw.splitlines() if "?" in line]


def _generation_prompt(seed: dict[str, Any], slots: list[dict[str, Any]]) -> str:
    focus_by_scenario = {
        "current_answer": ("đối tượng", "điều kiện", "hồ sơ", "thẩm quyền", "trình tự", "thời hạn", "kết quả", "ngoại lệ"),
        "historical_answer": ("mốc hiệu lực", "quy định thay thế", "quy định tại thời điểm phát sinh", "giai đoạn chuyển tiếp"),
        "temporal_refusal": ("thiếu ngày phát sinh", "hai mốc thời gian mâu thuẫn", "không rõ hỏi hiện hành hay lịch sử"),
        "insufficient_facts_refusal": ("thiếu nơi cư trú", "thiếu đối tượng thực hiện", "thiếu loại giấy tờ", "thiếu tình trạng pháp lý"),
        "out_of_scope_refusal": ("tư vấn mua hàng", "dự báo thời tiết", "lịch thi đấu", "nấu ăn", "sửa lỗi phần mềm", "du lịch", "giải trí", "đầu tư cá nhân", "quan hệ tình cảm", "bài toán số học"),
    }
    specs = []
    for index, slot in enumerate(slots):
        required_anchors: list[str] = []
        if "exact_law_article" in slot["coverage_tags"]:
            required_anchors.extend(
                [str(seed["source"]["law_number"]), f"Điều {seed['source'].get('article')}"]
            )
        if slot["scenario"] == "historical_answer":
            required_anchors.append(
                f"mốc lịch sử có thật lấy từ hiệu lực {seed['source'].get('validity_from')}"
            )
        if slot["scenario"] == "temporal_refusal":
            required_anchors.append("cụm 'không nhớ trước hay sau ngày hiệu lực'")
        if slot["scenario"] == "insufficient_facts_refusal":
            required_anchors.append("nêu rõ một dữ kiện người hỏi không biết/không rõ")
        specs.append({
            "index": index + 1,
            "variation_key": slot["case_id"],
            "scenario": slot["scenario"],
            "coverage_tags": slot["coverage_tags"],
            "required_anchors": required_anchors,
            "unique_focus": focus_by_scenario[slot["scenario"]][
                sum(ord(char) for char in slot["case_id"]) % len(focus_by_scenario[slot["scenario"]])
            ],
        })
    return f"""Bạn tạo câu hỏi kiểm thử truy xuất pháp luật Việt Nam, không tạo câu trả lời.
Chỉ dựa vào đoạn nguồn và metadata bên dưới. Không bịa số hiệu, Điều, ngày, phí hay thủ tục.
Không được tự đặt tên văn bản (ví dụ Hiến pháp, Bộ luật). Nếu cần viện dẫn, chỉ dùng đúng số hiệu trong Metadata.
Với current_answer: hỏi quy định hiện hành. historical_answer: nêu rõ một mốc quá khứ cần đối chiếu.
temporal_refusal: cố ý thiếu/mâu thuẫn mốc thời gian để hệ thống phải từ chối đúng.
insufficient_facts_refusal: thiếu một dữ kiện quyết định. out_of_scope_refusal: hỏi ngoài phạm vi pháp luật hành chính cấp xã.
Tags exact_law_article/procedure/multi_issue/validity_trap phải thể hiện đúng trong câu hỏi.
Viết tự nhiên, khác nhau rõ rệt, không chép nguyên câu hỏi mẫu. Mỗi câu kết thúc bằng dấu hỏi.
Chỉ trả JSON: {{"questions":["...", "..."]}}; đúng {len(slots)} câu; không expected output.

Metadata: số hiệu {seed['source']['law_number']}; điều {seed['source'].get('article')}; hiệu lực từ {seed['source'].get('validity_from')}; hết hiệu lực {seed['source'].get('validity_to')}.
Các yêu cầu: {json.dumps(specs, ensure_ascii=False)}
Đoạn nguồn:
{seed['context'][:2400]}
"""


class LocalFrameworkGenerator:
    def __init__(self, *, base_url: str, model: str) -> None:
        from deepeval.models import OllamaModel
        from langchain_ollama import ChatOllama
        from ragas.llms import LangchainLLMWrapper

        self._base_url = require_local_ollama_url(base_url)
        self._ragas = LangchainLLMWrapper(
            ChatOllama(model=model, base_url=self._base_url, temperature=0.2, format="json")
        )
        self._deepeval = OllamaModel(
            model=model,
            base_url=self._base_url,
            temperature=0.2,
            generation_kwargs={"format": "json", "num_ctx": 4096},
        )

    def generate(self, *, framework: str, prompt: str, expected_count: int) -> list[str]:
        if framework == "ragas":
            from langchain_core.prompt_values import StringPromptValue

            response = self._ragas.generate_text(StringPromptValue(text=prompt), temperature=0.2)
            raw = str(response.generations[0][0].text)
        elif framework == "deepeval":
            response = self._deepeval.generate(prompt)
            raw = str(response[0] if isinstance(response, tuple) else response)
        else:
            raise ValueError(f"unsupported framework {framework}")
        questions = _extract_questions(raw)
        if len(questions) != expected_count:
            raise RuntimeError(f"{framework} returned {len(questions)} questions; expected {expected_count}")
        return questions


def _legacy_candidate_for_slot(
    slot: dict[str, Any],
    domain_seeds: list[dict[str, Any]],
    used_questions: set[str],
    provenance: dict[str, Any],
) -> dict[str, Any] | None:
    if slot["split"] == "production-holdout":
        return None
    if slot["split"] == "golden-regression":
        allow = slot["scenario"] in {"current_answer", "insufficient_facts_refusal"}
        required_origin = "golden"
    else:
        allow = slot["scenario"] == "current_answer"
        required_origin = "hard-negative"
    if not allow:
        return None
    for seed in domain_seeds:
        if seed["origin"] != required_origin or not seed["legacy_question"]:
            continue
        if slot["scenario"] == "insufficient_facts_refusal" and not seed["legacy_expected_refusal"]:
            continue
        normalized = normalize_question(seed["legacy_question"])
        if normalized in used_questions:
            continue
        used_questions.add(normalized)
        candidate = build_candidate(
            slot=slot,
            question=seed["legacy_question"],
            source=seed["source"],
            framework="legacy_approved_seed",
            provenance=provenance,
        )
        candidate["seed"] = {
            "legacy_case_id": seed["legacy_case_id"],
            "legacy_origin": seed["origin"],
            "legal_as_of": seed["legal_as_of"],
        }
        if validate_generated_question(candidate["question"], slot=slot, source=candidate):
            used_questions.discard(normalized)
            continue
        return candidate
    return None


def _source_preference(slot: dict[str, Any]) -> str | None:
    tags = set(slot.get("coverage_tags", []))
    if "exact_law_article" in tags:
        return "scenario_category_exact_article"
    if "procedure" in tags:
        return "scenario_category_procedure"
    if "multi_issue" in tags:
        return "scenario_category_multi_issue"
    if "validity_trap" in tags:
        return "scenario_category_validity"
    return None


def _out_of_scope_question(case_id: str) -> str:
    number = int(re.sub(r"\D", "", case_id) or "1")
    split_offset = 0 if "-GR-" in case_id else 3_000 if "-HN-" in case_id else 6_000
    unique_date = (date(2027, 1, 1) + timedelta(days=split_offset + number)).isoformat()
    topics = (
        f"Tôi có ngân sách {5 + number % 31} triệu đồng, nên mua điện thoại nào để chơi game?",
        f"Thời tiết Hải Phòng vào tuần thứ {1 + number % 52} trong năm thường như thế nào?",
        f"Lịch thi đấu bóng đá vào ngày {1 + number % 28} tháng {1 + number % 12} có trận nào đáng xem?",
        f"Tôi muốn nấu bữa tối cho {2 + number % 9} người, nên chọn món gì?",
        f"Phần mềm của tôi báo mã lỗi {100 + number}, nên sửa lỗi phần mềm này thế nào?",
        f"Tôi có {2 + number % 12} ngày nghỉ, nên lập lịch du lịch ở đâu?",
        f"Tối nay tôi muốn xem một bộ phim dài dưới {80 + number % 80} phút, nên chọn phim nào?",
        f"Với khoản tiền {1 + number % 40} triệu đồng, tôi nên đầu tư cá nhân vào đâu?",
        f"Sau {1 + number % 15} tháng quen nhau, tôi nên xử lý mâu thuẫn tình cảm thế nào?",
        f"Bài toán có {10 + number} viên bi chia cho {2 + number % 9} người thì mỗi người được bao nhiêu?",
    )
    question = topics[number % len(topics)].rstrip("?")
    return f"Tôi hỏi cho ngày {unique_date}: {question[0].lower() + question[1:]}?"


def _deterministic_question(slot: dict[str, Any], seed: dict[str, Any]) -> str:
    if slot["scenario"] == "out_of_scope_refusal":
        return _out_of_scope_question(slot["case_id"])

    source = seed["source"]
    base = re.sub(r"\s+", " ", seed["legacy_question"]).strip().rstrip(" ?")
    base = re.sub(r"\bhiện hành\b", "áp dụng tại mốc được hỏi", base, flags=re.IGNORECASE)
    law = str(source["law_number"])
    article = str(source.get("article") or "").strip()
    split_prefix = {
        "golden-regression": "",
        "hard-negative": "Khi đối chiếu với một văn bản gần giống, ",
        "production-holdout": "Trong một tình huống độc lập tại Hải Phòng, ",
    }[slot["split"]]

    scenario = slot["scenario"]
    if scenario == "current_answer":
        question = f"{split_prefix}{base}"
    elif scenario == "historical_answer":
        historical_date = source.get("validity_from") or seed.get("legal_as_of")
        question = f"Tại thời điểm {historical_date}, theo văn bản {law}, {base[0].lower() + base[1:]}"
    elif scenario == "temporal_refusal":
        effective_date = source.get("validity_from") or "văn bản có hiệu lực"
        question = (
            f"Tôi không nhớ sự việc xảy ra trước hay sau ngày hiệu lực {effective_date}; "
            f"theo văn bản {law}, {base[0].lower() + base[1:]}"
        )
    elif scenario == "insufficient_facts_refusal":
        missing = ("nơi cư trú", "đối tượng thực hiện", "loại giấy tờ", "tình trạng pháp lý")
        index = int(re.sub(r"\D", "", slot["case_id"]) or "0")
        question = f"Tôi chưa xác định {missing[index % len(missing)]}; {base[0].lower() + base[1:]}"
    else:
        raise ValueError(f"unsupported scenario {scenario}")

    tags = set(slot.get("coverage_tags", []))
    if "exact_law_article" in tags:
        question = f"Theo Điều {article} của văn bản {law}, {question[0].lower() + question[1:]}"
    if "procedure" in tags:
        question = f"Về hồ sơ, trình tự và thẩm quyền, {question[0].lower() + question[1:]}"
    if "multi_issue" in tags:
        question = f"Cần đồng thời xác định căn cứ và cách thực hiện: {question[0].lower() + question[1:]}"
    if "validity_trap" in tags and scenario == "current_answer":
        question = f"Xét hiệu lực hiện hành của nguồn, {question[0].lower() + question[1:]}"
    return question.rstrip(" ?.:") + "?"


def _deterministic_candidate_for_slot(
    slot: dict[str, Any],
    domain_seeds: list[dict[str, Any]],
    used_questions: set[str],
    provenance: dict[str, Any],
    start_index: int,
) -> tuple[dict[str, Any], int]:
    preference = _source_preference(slot)
    ordered = domain_seeds[start_index % len(domain_seeds) :] + domain_seeds[: start_index % len(domain_seeds)]
    if preference:
        ordered = sorted(ordered, key=lambda item: preference not in item.get("legacy_tags", []))
    for offset, seed in enumerate(ordered):
        question = _deterministic_question(slot, seed)
        normalized = normalize_question(question)
        if normalized in used_questions:
            continue
        defects = validate_generated_question(question, slot=slot, source=seed["source"])
        if defects:
            continue
        used_questions.add(normalized)
        candidate = build_candidate(
            slot=slot,
            question=question,
            source=seed["source"],
            framework="deterministic_grounded_transform",
            provenance=provenance,
        )
        candidate["seed"] = {
            "legacy_case_id": seed["legacy_case_id"],
            "legacy_origin": seed["origin"],
            "legal_as_of": seed["legal_as_of"],
        }
        return candidate, start_index + offset + 1
    raise RuntimeError(f"no deterministic grounded candidate passed for {slot['case_id']}")


def _state_report(
    *,
    completed: list[dict[str, Any]],
    metadata: dict[str, Any],
    provenance: dict[str, Any],
    development_path: Path,
    holdout_path: Path,
) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    frameworks: dict[str, int] = defaultdict(int)
    for item in completed:
        counts[str(item["split"])] += 1
        frameworks[str(item["generation"]["framework"])] += 1
    return {
        "schema_version": "retrieval-eval-generation-state-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if len(completed) == 2_000 else "in_progress",
        "api_cost_usd": 0,
        "completed_count": len(completed),
        "split_counts": dict(counts),
        "framework_counts": dict(frameworks),
        "development_candidate_path": str(development_path),
        "holdout_custody_path_sha256": _sha256_text(str(holdout_path)),
        "source": metadata,
        "provenance": provenance,
        "approval": {
            "automatic_approval": False,
            "owner_final_review_required": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--generation-model", default="qwen2.5:3b")
    parser.add_argument("--embedding-model", default="nomic-embed-text:latest")
    parser.add_argument("--development-output", type=Path, default=DEFAULT_DEVELOPMENT_PATH)
    parser.add_argument("--state-output", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--rejected-output", type=Path, default=DEFAULT_REJECTED_PATH)
    parser.add_argument("--custody-root", type=Path, default=Path("J:/LegalQACustody/retrieval-eval-stage-e"))
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument(
        "--generation-mode",
        choices=("deterministic", "framework"),
        default="deterministic",
        help="Deterministic is the fail-closed default; framework mode uses local Ragas/DeepEval.",
    )
    parser.add_argument("--max-new", type=int, default=0, help="0 means generate until complete")
    args = parser.parse_args()
    base_url = require_local_ollama_url(args.ollama_url)
    holdout_path = ensure_external_holdout_path(
        args.custody_root / "production-holdout-candidates-v1.jsonl", REPO_ROOT
    )

    import deepeval
    import ragas
    import urllib.request

    def ollama_json(path: str, payload: dict | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}{path}", data=data, headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - local-only
            return json.loads(response.read().decode("utf-8"))

    tags = ollama_json("/api/tags").get("models", [])
    digest_by_name = {
        str(item.get("name") or item.get("model")): str(item.get("digest") or "") for item in tags
    }
    version = str(ollama_json("/api/version").get("version") or "")
    provenance = build_generation_provenance(
        ragas_version=ragas.__version__,
        deepeval_version=deepeval.__version__,
        ollama_version=version,
        generation_model=args.generation_model,
        generation_model_digest=digest_by_name.get(args.generation_model, ""),
        embedding_model=args.embedding_model,
        embedding_model_digest=digest_by_name.get(args.embedding_model, ""),
        ollama_url=base_url,
    )
    pools, metadata = _source_seed_catalog()
    plan = build_generation_plan()
    completed = _read_jsonl(args.development_output) + _read_jsonl(holdout_path)
    plan_by_id = {str(item["case_id"]): item for item in plan}
    valid_completed: list[dict[str, Any]] = []
    rejected_development: list[dict[str, Any]] = []
    rejected_holdout: list[dict[str, Any]] = []
    for item in completed:
        slot = plan_by_id.get(str(item.get("case_id")), item)
        defects = validate_generated_question(str(item.get("question") or ""), slot=slot, source=item)
        if item.get("generation", {}).get("framework") in {"ragas", "deepeval"}:
            defects.append("local_3b_framework_proposal_not_promotable")
        if defects:
            rejection = {
                **item,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
                "generation_defects": defects,
            }
            if item.get("split") == "production-holdout":
                rejected_holdout.append(rejection)
            else:
                rejected_development.append(rejection)
        else:
            valid_completed.append(item)
    if len(valid_completed) != len(completed):
        development_valid = [item for item in valid_completed if item.get("split") != "production-holdout"]
        holdout_valid = [item for item in valid_completed if item.get("split") == "production-holdout"]
        _rewrite_jsonl(args.development_output, development_valid)
        _rewrite_jsonl(holdout_path, holdout_valid)
        if rejected_development:
            _append_jsonl(args.rejected_output, rejected_development)
        if rejected_holdout:
            _append_jsonl(args.custody_root / "production-holdout-rejected-v1.jsonl", rejected_holdout)
        completed = valid_completed
    completed_by_id = {str(item["case_id"]): item for item in completed}
    used_questions = {normalize_question(item["question"]) for item in completed}

    seed_cursor = defaultdict(int)
    new_count = 0
    generated_batch_number = sum(
        1 for item in completed if item.get("generation", {}).get("framework") in {"ragas", "deepeval"}
    ) // max(args.batch_size, 1)
    generator: LocalFrameworkGenerator | None = None
    pending_slots = [slot for slot in plan if slot["case_id"] not in completed_by_id]
    index = 0
    while index < len(pending_slots):
        slot = pending_slots[index]
        domain_seeds = pools[slot["domain"]]
        legacy = _legacy_candidate_for_slot(slot, domain_seeds, used_questions, provenance)
        if legacy is not None:
            destination = holdout_path if slot["split"] == "production-holdout" else args.development_output
            _append_jsonl(destination, [legacy])
            completed.append(legacy)
            completed_by_id[slot["case_id"]] = legacy
            new_count += 1
            index += 1
        elif args.generation_mode == "deterministic":
            row, next_cursor = _deterministic_candidate_for_slot(
                slot,
                domain_seeds,
                used_questions,
                provenance,
                seed_cursor[slot["domain"]],
            )
            seed_cursor[slot["domain"]] = next_cursor
            destination = holdout_path if slot["split"] == "production-holdout" else args.development_output
            _append_jsonl(destination, [row])
            completed.append(row)
            completed_by_id[slot["case_id"]] = row
            new_count += 1
            index += 1
        else:
            if generator is None:
                generator = LocalFrameworkGenerator(base_url=base_url, model=args.generation_model)
            batch = [slot]
            for following in pending_slots[index + 1 :]:
                if len(batch) >= args.batch_size:
                    break
                if following["domain"] == slot["domain"] and following["split"] == slot["split"]:
                    if _legacy_candidate_for_slot(following, domain_seeds, set(used_questions), provenance) is None:
                        batch.append(following)
            seed = domain_seeds[seed_cursor[slot["domain"]] % len(domain_seeds)]
            seed_cursor[slot["domain"]] += 1
            framework = "ragas" if generated_batch_number % 2 == 0 else "deepeval"
            prompt = _generation_prompt(seed, batch)
            questions: list[str] | None = None
            last_error = ""
            for attempt in range(1, 4):
                attempt_prompt = prompt + (
                    "\nMỗi variation_key bắt buộc có cách diễn đạt và tình huống riêng; "
                    f"đây là lần sinh {attempt}, không lặp bất kỳ câu nào trong cùng mảng."
                )
                try:
                    proposed = generator.generate(
                        framework=framework,
                        prompt=attempt_prompt,
                        expected_count=len(batch),
                    )
                    normalized_batch = [normalize_question(item) for item in proposed]
                    if len(normalized_batch) != len(set(normalized_batch)):
                        raise RuntimeError("model repeated a question inside the batch")
                    if any(item in used_questions for item in normalized_batch):
                        raise RuntimeError("model repeated a previously generated question")
                    batch_defects = {
                        target["case_id"]: validate_generated_question(
                            question,
                            slot=target,
                            source=seed["source"],
                        )
                        for target, question in zip(batch, proposed, strict=True)
                    }
                    batch_defects = {key: value for key, value in batch_defects.items() if value}
                    if batch_defects:
                        raise RuntimeError(
                            "deterministic generation gate rejected batch: "
                            + json.dumps(batch_defects, ensure_ascii=False, sort_keys=True)
                        )
                    questions = proposed
                    break
                except RuntimeError as exc:
                    last_error = str(exc)
            if questions is None:
                raise RuntimeError(
                    f"{framework} failed unique batch after 3 attempts: {last_error}"
                )
            rows = []
            for target, question in zip(batch, questions, strict=True):
                normalized = normalize_question(question)
                if normalized in used_questions:
                    raise RuntimeError(f"duplicate generated question for {target['case_id']}")
                used_questions.add(normalized)
                row = build_candidate(
                    slot=target,
                    question=question,
                    source=seed["source"],
                    framework=framework,
                    provenance=provenance,
                )
                row["seed"] = {
                    "legacy_case_id": seed["legacy_case_id"],
                    "legacy_origin": seed["origin"],
                    "legal_as_of": seed["legal_as_of"],
                }
                rows.append(row)
            destination = holdout_path if slot["split"] == "production-holdout" else args.development_output
            _append_jsonl(destination, rows)
            for row in rows:
                completed.append(row)
                completed_by_id[row["case_id"]] = row
            new_count += len(rows)
            generated_batch_number += 1
            batch_ids = {item["case_id"] for item in batch}
            pending_slots = [item for item in pending_slots if item["case_id"] not in batch_ids]
            index = 0

        state = _state_report(
            completed=completed,
            metadata=metadata,
            provenance=provenance,
            development_path=args.development_output,
            holdout_path=holdout_path,
        )
        args.state_output.parent.mkdir(parents=True, exist_ok=True)
        args.state_output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"completed": len(completed), "new": new_count, "cost_usd": 0}))
        if args.max_new and new_count >= args.max_new:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
