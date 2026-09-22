"""Deterministic, source-preserving presentation for public quick chat."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from api.data_paths import notebook_data_dir
from api.procedure_fast_path import normalize_procedure_query


_META_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greeting", re.compile(r"^(?:alo|a lo|hello|hi|hey|xin chao|chao|chao ban|xin chao ban|chao bot|xin chao bot)[.! ]*$")),
    ("thanks", re.compile(r"^(?:cam on|cam on ban|thank|thanks|thank you|ok cam on|uk cam on)[.! ]*$")),
    ("help", re.compile(r"^(?:giup|tro giup|huong dan|ban lam duoc gi|hoi gi duoc)[?!., ]*$")),
    ("goodbye", re.compile(r"^(?:tam biet|bye|goodbye|hen gap lai)[.! ]*$")),
)
_SUMMARY_TERMS = ("tom tat", "noi ngan gon", "rut gon", "tom lai")
_EXPLAIN_TERMS = ("giai thich lai", "noi de hieu", "khong hieu", "chua hieu", "noi ro hon")


def _procedure_catalog_path() -> Path:
    configured = str(os.getenv("PROCEDURE_FAST_PATH_CATALOG") or "").strip()
    if configured:
        return Path(configured)
    return notebook_data_dir() / "forms" / "managed_procedures_runtime_v1.json"


@lru_cache(maxsize=4)
def _strict_procedure_map(path_string: str, stamp: int) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(Path(path_string).read_text(encoding="utf-8-sig"))
        rows = payload.get("procedures") if isinstance(payload, Mapping) else None
        if isinstance(rows, list):
            return {
                str(row.get("procedure_id") or "").strip(): dict(row)
                for row in rows
                if isinstance(row, Mapping) and str(row.get("procedure_id") or "").strip()
            }
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    # Public presentation never falls back to the unsourced seed catalogue.
    return {}


def clear_quick_chat_presentation_cache() -> None:
    _strict_procedure_map.cache_clear()


def _managed_procedure(procedure_id: str) -> dict[str, Any] | None:
    path = _procedure_catalog_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = 0
    return _strict_procedure_map(str(path), stamp).get(procedure_id)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    output: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            text = str(
                item.get("canonical_name")
                or item.get("form_title")
                or item.get("name")
                or item.get("form_code")
                or ""
            ).strip()
        else:
            text = str(item or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _source_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        ref = dict(item)
        url = str(ref.get("source_url") or ref.get("url") or "").strip()
        if not url or url in seen:
            continue
        ref["source_url"] = url
        seen.add(url)
        refs.append(ref)
    return refs


def _procedure_snapshot(intent: Mapping[str, Any]) -> dict[str, Any] | None:
    embedded = intent.get("procedure_snapshot")
    if isinstance(embedded, Mapping):
        return dict(embedded)
    procedure_id = str(intent.get("procedure_id") or "").strip()
    if not procedure_id:
        return None
    return _managed_procedure(procedure_id)


def _citation(ref: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "document_title": ref.get("document_title") or ref.get("title") or "Nguồn thủ tục đã phát hành",
        "source_url": ref.get("source_url") or ref.get("url"),
        "label": ref.get("label") or "Nguồn chính thức",
        "verification_status": ref.get("verification_status") or "managed_approved",
    }


def _short_name(snapshot: Mapping[str, Any]) -> str:
    aliases = _strings(snapshot.get("aliases"))
    for alias in aliases:
        if 8 <= len(alias) <= 80:
            return alias
    name = str(snapshot.get("name") or "thủ tục này").strip()
    return re.sub(r"\s*\([^)]*cấp xã[^)]*\)\s*", " ", name, flags=re.IGNORECASE).strip()


def _quick_facts(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    values = (
        ("submission_place", "Nơi nộp", snapshot.get("submission_place") or snapshot.get("receiving_authority")),
        ("duration", "Thời hạn", snapshot.get("duration")),
        ("fee", "Lệ phí", snapshot.get("fee")),
    )
    return [
        {"id": key, "label": label, "value": str(value).strip()}
        for key, label, value in values
        if str(value or "").strip()
    ]


def _answer_sections(snapshot: Mapping[str, Any], facet: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    documents = _strings(snapshot.get("documents_required"))
    steps = _strings(snapshot.get("steps"))
    forms = _strings(snapshot.get("forms"))
    guidance = str(snapshot.get("guidance") or "").strip()
    if facet in {"documents", "overview"} and documents:
        sections.append(
            {
                "id": "documents",
                "title": "Hồ sơ cần chuẩn bị",
                "kind": "checklist",
                "items": documents,
            }
        )
    if facet in {"steps", "overview"} and steps:
        sections.append(
            {
                "id": "steps",
                "title": "Các bước thực hiện",
                "kind": "steps",
                "items": steps,
            }
        )
    if facet in {"eligibility", "forms_special", "overview"} and guidance:
        sections.append(
            {
                "id": "guidance",
                "title": "Điều kiện và lưu ý",
                "kind": "note",
                "body": guidance,
            }
        )
    if facet == "forms_special":
        sections.append(
            {
                "id": "forms",
                "title": "Biểu mẫu",
                "kind": "checklist" if forms else "notice",
                "items": forms,
                "body": None if forms else "Hệ thống chưa lưu tệp biểu mẫu riêng. Hãy mở nguồn chính thức bên dưới để lấy đúng mẫu hiện hành.",
            }
        )
    return sections


def _suggestions(snapshot: Mapping[str, Any], current_facet: str) -> list[str]:
    name = _short_name(snapshot)
    candidates = (
        ("documents", f"{name} cần giấy tờ gì?"),
        ("submission_place", f"{name} nộp ở đâu?"),
        ("duration", f"{name} giải quyết bao lâu?"),
        ("fee", f"{name} có mất phí không?"),
        ("steps", f"Các bước làm {name} thế nào?"),
    )
    return [question for facet, question in candidates if facet != current_facet][:4]


def enrich_intent_response(
    response: dict[str, Any],
    *,
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    """Add a progressive-disclosure card without altering the reviewed answer."""

    snapshot = _procedure_snapshot(intent)
    if not snapshot:
        response["suggested_questions"] = []
        return response
    facet = str(intent.get("intent_type") or intent.get("answer_facet") or "overview")
    procedure_id = str(intent.get("procedure_id") or "").strip()
    detail = {
        "procedure_id": procedure_id,
        "name": snapshot.get("name") or intent.get("procedure_name"),
        "domain": snapshot.get("domain"),
        "submission_place": snapshot.get("submission_place") or snapshot.get("receiving_authority"),
        "documents_required": _strings(snapshot.get("documents_required")),
        "steps": _strings(snapshot.get("steps")),
        "duration": snapshot.get("duration"),
        "fee": snapshot.get("fee"),
        "guidance": snapshot.get("guidance"),
        "forms": _strings(snapshot.get("forms")),
        "legal_basis": _strings(snapshot.get("legal_basis")),
        "source_status": snapshot.get("source_status") or intent.get("source_status"),
        "procedure_revision_sha256": snapshot.get("procedure_revision_sha256") or intent.get("procedure_revision_sha256"),
    }
    # Citations must remain the refs reviewed for this specific intent. The
    # procedure snapshot may carry additional sources for other facets; adding
    # those as answer citations would imply unsupported field-level grounding.
    reviewed_refs = _source_refs(intent.get("source_refs"))
    if reviewed_refs:
        response["citations"] = [_citation(ref) for ref in reviewed_refs]
    response.update(
        {
            "procedure_detail": detail,
            "procedure_summary": detail["name"],
            "quick_facts": _quick_facts(snapshot),
            "answer_sections": _answer_sections(snapshot, facet),
            "data_quality_notice": (
                "Các thẻ chi tiết được lấy từ bản thủ tục quản lý; nguồn chưa được gắn riêng cho từng trường. "
                "Hãy đối chiếu trang công bố chính thức trước khi nộp hồ sơ."
                if not isinstance(snapshot.get("fact_sources"), Mapping)
                else None
            ),
            "suggested_questions": _suggestions(snapshot, facet),
            "action_chips": (
                [
                    {"id": "summary", "label": "Tóm tắt lại", "question": "Tóm tắt lại"},
                    {"id": "explain", "label": "Giải thích dễ hiểu", "question": "Mình chưa hiểu, giải thích lại"},
                ]
                if len(str(response.get("answer") or "")) > 300
                and (len(_quick_facts(snapshot)) > 1 or len(_answer_sections(snapshot, facet)) > 1)
                else []
            ),
            "verification": {
                "label": "Câu trả lời từ intent đã phát hành; thẻ chi tiết từ bản thủ tục quản lý",
                "release_id": response.get("release_id") or intent.get("release_id"),
                "procedure_revision_sha256": detail.get("procedure_revision_sha256"),
                "llm_used": False,
            },
        }
    )
    return response


def detect_meta_kind(question: str) -> str | None:
    normalized = normalize_procedure_query(question)
    for kind, pattern in _META_PATTERNS:
        if pattern.fullmatch(normalized):
            return kind
    return None


def detect_followup_action(question: str) -> str | None:
    normalized = normalize_procedure_query(question)
    if any(term in normalized for term in _SUMMARY_TERMS):
        return "summary"
    if any(term in normalized for term in _EXPLAIN_TERMS):
        return "explain"
    return None


def meta_response(question: str, kind: str) -> dict[str, Any]:
    answers = {
        "greeting": "Xin chào! Bạn cứ nói tên thủ tục hoặc điều mình đang cần giải quyết, ví dụ: làm khai sinh cần giấy tờ gì, nộp ở đâu hoặc lệ phí bao nhiêu.",
        "thanks": "Rất vui vì đã giúp được bạn. Nếu cần, bạn có thể hỏi tiếp về hồ sơ, nơi nộp, thời hạn hoặc lệ phí của thủ tục vừa xem.",
        "help": "Mình hỗ trợ hỏi nhanh các thủ tục phổ biến tại xã/phường: hồ sơ cần chuẩn bị, điều kiện, nơi nộp, các bước, thời hạn, lệ phí và biểu mẫu.",
        "goodbye": "Tạm biệt bạn! Khi cần tra cứu thủ tục xã/phường, bạn cứ quay lại và hỏi bằng cách nói tự nhiên nhé.",
    }
    suggestions = [
        "Đăng ký kết hôn cần giấy tờ gì?",
        "Xin xác nhận cư trú nộp ở đâu?",
        "Cấp giấy phép xây dựng mất bao lâu?",
    ] if kind in {"greeting", "help"} else []
    return {
        "answer": answers[kind],
        "question": question,
        "citations": [],
        "grounding_status": "not_applicable",
        "answer_status": "conversational",
        "outcome": "answered",
        "reason_code": f"META_{kind.upper()}",
        "retryable": False,
        "answer_mode": "public_quick_chat_meta",
        "answer_route": "public_quick_chat_meta",
        "llm_used": False,
        "source_gap": [],
        "suggested_questions": suggestions,
        "generation_provenance": {
            "mode": "public_quick_chat_meta",
            "provider_label": "deterministic",
            "model_calls": 0,
        },
    }


def apply_followup_action(response: dict[str, Any], action: str) -> dict[str, Any]:
    facts = response.get("quick_facts") or []
    if action == "summary":
        lines = ["Tóm tắt thông tin chính:"]
        lines.extend(
            f"• {item.get('label')}: {item.get('value')}"
            for item in facts[:3]
            if item.get("label") and item.get("value")
        )
        if len(lines) == 1:
            lines.append(f"• {response.get('answer')}")
        response["answer"] = "\n".join(lines)
        response["answer_sections"] = []
        response["action_chips"] = []
        response["reason_code"] = "CONTEXT_SUMMARY"
        response["answer_facet"] = "overview"
    elif action == "explain":
        has_structure = bool(response.get("quick_facts") or response.get("answer_sections"))
        response["answer"] = (
            "Mình tách thông tin thành các mục ngắn bên dưới để bạn dễ kiểm tra. Các số liệu và giấy tờ vẫn giữ nguyên theo bản thủ tục đã phát hành."
            if has_structure
            else str(response.get("answer") or "")
        )
        response["reason_code"] = "CONTEXT_EXPLAIN"
        response["action_chips"] = []
    response["question"] = response.get("question")
    return response


__all__ = [
    "apply_followup_action",
    "clear_quick_chat_presentation_cache",
    "detect_followup_action",
    "detect_meta_kind",
    "enrich_intent_response",
    "meta_response",
]
