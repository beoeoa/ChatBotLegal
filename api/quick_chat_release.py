"""Build and validate immutable quick-chat intent releases.

The managed procedure catalogue is the operational source for seven common
facets.  Curated intent rows remain explicit overrides, while deterministic
facet rows are rebuilt from the managed procedure facts.  This module is pure:
it does not activate a release or write files.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


RELEASE_SCHEMA = "quick-chat-intent-release-v2"
GENERATED_SOURCE_KIND = "managed_procedure_sourced_facet_expansion"
CORE_FACETS = (
    "documents",
    "eligibility",
    "submission_place",
    "steps",
    "duration",
    "fee",
    "forms_special",
)
FACET_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "documents": ("documents_required",),
    "eligibility": ("guidance",),
    "submission_place": ("submission_place", "receiving_authority"),
    "steps": ("steps",),
    "duration": ("duration",),
    "fee": ("fee",),
    "forms_special": ("forms", "guidance"),
    "overview": (
        "guidance",
        "submission_place",
        "receiving_authority",
        "duration",
        "fee",
    ),
}
PROCEDURE_SNAPSHOT_FIELDS = (
    "procedure_id",
    "name",
    "aliases",
    "official_procedure_code",
    "domain",
    "receiving_authority",
    "department",
    "documents_required",
    "guidance",
    "submission_place",
    "steps",
    "duration",
    "fee",
    "forms",
    "legal_basis",
    "fact_sources",
    "source_refs",
    "source_url",
    "source_status",
    "review_status",
    "approved",
    "catalog_status",
    "effective_from",
    "effective_to",
    "source_checked_at",
)
OFFICIAL_SOURCE_HOSTS = frozenset(
    {
        "dichvucong.gov.vn",
        "vbpl.vn",
        "vanban.chinhphu.vn",
        "datafiles.chinhphu.vn",
        "cdn.haiphong.gov.vn",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _clean_list(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return [deepcopy(item) for item in value if item not in (None, "")]


def _text_list(value: Any) -> list[str]:
    return [str(item).strip().rstrip(" .;") for item in _clean_list(value) if str(item).strip()]


def _no_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn").replace("đ", "d").replace("Đ", "D")


def _source_url(ref: Mapping[str, Any]) -> str:
    return str(ref.get("source_url") or ref.get("url") or "").strip()


def _source_is_official(ref: Mapping[str, Any]) -> bool:
    url = _source_url(ref)
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return False
    return host in OFFICIAL_SOURCE_HOSTS or host.endswith(".haiphong.gov.vn")


def _dedupe_sources(refs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in refs:
        if not isinstance(raw, Mapping):
            continue
        ref = dict(raw)
        url = _source_url(ref)
        if not url or url in seen:
            continue
        ref["source_url"] = url
        seen.add(url)
        output.append(ref)
    return output


def _sources_by_procedure(
    intents: Iterable[Mapping[str, Any]],
    procedures: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for procedure in procedures:
        procedure_id = str(procedure.get("procedure_id") or "").strip()
        if not procedure_id:
            continue
        refs = _clean_list(procedure.get("source_refs"))
        source_url = str(procedure.get("source_url") or "").strip()
        if source_url:
            refs.append(
                {
                    "source_url": source_url,
                    "document_title": procedure.get("name") or "Thủ tục hành chính",
                    "verification_status": procedure.get("source_status") or "managed_approved",
                }
            )
        result[procedure_id].extend(_dedupe_sources(refs))
    for intent in intents:
        procedure_id = str(intent.get("procedure_id") or "").strip()
        if not procedure_id:
            continue
        result[procedure_id].extend(_dedupe_sources(_clean_list(intent.get("source_refs"))))
    return {key: _dedupe_sources(value) for key, value in result.items()}


def _procedure_snapshot(
    procedure: Mapping[str, Any],
    *,
    sources: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    snapshot = {
        field: deepcopy(procedure.get(field))
        for field in PROCEDURE_SNAPSHOT_FIELDS
        if procedure.get(field) not in (None, "", [])
    }
    snapshot["procedure_id"] = str(procedure.get("procedure_id") or "").strip()
    snapshot["source_refs"] = _dedupe_sources(sources)
    revision_payload = {
        key: value
        for key, value in snapshot.items()
        if key not in {"source_checked_at"}
    }
    snapshot["procedure_revision_sha256"] = content_sha256(revision_payload)
    return snapshot


def _joined(values: list[str]) -> str:
    if not values:
        return "chưa có dữ kiện trong bản phát hành hiện tại"
    if len(values) == 1:
        return values[0]
    return "; ".join(values[:-1]) + "; và " + values[-1]


def canonical_question(name: str, facet: str) -> str:
    return {
        "documents": f"{name} cần chuẩn bị hồ sơ, giấy tờ gì?",
        "eligibility": f"Điều kiện hoặc đối tượng thực hiện {name} là gì?",
        "submission_place": f"{name} nộp ở đâu?",
        "steps": f"Trình tự thực hiện {name} như thế nào?",
        "duration": f"{name} được giải quyết trong bao lâu?",
        "fee": f"{name} có lệ phí hoặc chi phí gì?",
        "forms_special": f"{name} dùng biểu mẫu nào và có trường hợp đặc biệt gì?",
    }[facet]


def deterministic_answer(procedure: Mapping[str, Any], facet: str) -> str:
    """Render only facts present in the reviewed managed procedure record."""

    name = str(procedure.get("name") or "thủ tục này").strip()
    documents = _text_list(procedure.get("documents_required"))
    steps = _text_list(procedure.get("steps"))
    guidance = str(procedure.get("guidance") or "").strip().rstrip(" .")
    place = str(procedure.get("submission_place") or procedure.get("receiving_authority") or "").strip().rstrip(" .")
    duration = str(procedure.get("duration") or "").strip().rstrip(" .")
    fee = str(procedure.get("fee") or "").strip().rstrip(" .")
    forms = _text_list(procedure.get("forms"))
    if facet == "documents":
        return f"Hồ sơ {name} gồm {_joined(documents)}."
    if facet == "eligibility":
        return f"Điều kiện và lưu ý đang được công bố cho {name}: {guidance or 'chưa có nội dung riêng trong bản phát hành hiện tại'}."
    if facet == "submission_place":
        return f"Bạn nộp hồ sơ {name} tại {place or 'cơ quan tiếp nhận ghi trong bản thủ tục được công bố'}."
    if facet == "steps":
        return f"Trình tự {name}: " + " ".join(f"{index + 1}. {step}." for index, step in enumerate(steps))
    if facet == "duration":
        return f"Thời hạn giải quyết {name}: {duration or 'chưa có dữ kiện trong bản phát hành hiện tại'}."
    if facet == "fee":
        return f"Lệ phí hoặc chi phí của {name}: {fee or 'chưa có dữ kiện trong bản phát hành hiện tại'}."
    form_text = _joined(forms) if forms else "bản phát hành hiện tại chưa gắn tệp biểu mẫu riêng"
    special = f" Lưu ý: {guidance}." if guidance else ""
    return f"Biểu mẫu của {name}: {form_text}.{special}"


def question_variants(question: str, name: str, facet: str, aliases: Iterable[str]) -> list[str]:
    short = {
        "documents": f"{name} cần giấy tờ gì",
        "eligibility": f"ai được làm {name}",
        "submission_place": f"{name} nộp chỗ nào",
        "steps": f"các bước làm {name}",
        "duration": f"{name} bao lâu có kết quả",
        "fee": f"{name} hết bao nhiêu tiền",
        "forms_special": f"mẫu đơn và trường hợp đặc biệt của {name}",
    }[facet]
    values = [
        question,
        _no_accents(question).lower(),
        short,
        _no_accents(short).lower(),
        f"Cho mình hỏi {question.lower()}",
        f"Mình muốn biết {question.lower()}",
    ]
    patterns = {
        "documents": ("{alias} cần giấy tờ gì", "{alias} cần mang gì", "hồ sơ {alias} gồm gì"),
        "eligibility": ("{alias} cần điều kiện gì", "{alias} áp dụng khi nào", "trường hợp nào được {alias}"),
        "submission_place": (
            "{alias} nộp ở đâu",
            "{alias} xin ở đâu",
            "{alias} báo ở đâu",
            "{alias} ở xã được không",
            "{alias} thì xin lại ở đâu",
        ),
        "steps": (
            "{alias} làm thế nào",
            "muốn {alias} thì làm sao",
            "{alias} thủ tục thế nào",
            "{alias} xin lại thế nào",
            "{alias} phải báo trước thế nào",
        ),
        "duration": ("{alias} bao lâu có kết quả", "{alias} mất bao lâu", "{alias} mấy ngày xong"),
        "fee": ("{alias} mất bao nhiêu tiền", "{alias} có mất phí không", "lệ phí {alias}"),
        "forms_special": ("{alias} dùng mẫu nào", "{alias} có lưu ý gì", "trường hợp đặc biệt của {alias}"),
    }[facet]
    for alias in list(aliases)[:4]:
        if not str(alias).strip():
            continue
        for pattern in patterns:
            natural = pattern.format(alias=str(alias).strip())
            values.extend((natural, _no_accents(natural).lower()))
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _generated_record(
    procedure: Mapping[str, Any],
    *,
    facet: str,
    sources: list[dict[str, Any]],
    negative_name: str,
    existing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    procedure_id = str(procedure.get("procedure_id") or "").strip()
    name = str(procedure.get("name") or procedure_id).strip()
    intent_hash = hashlib.sha256(f"{procedure_id}:{facet}".encode("utf-8")).hexdigest()[:12]
    question = canonical_question(name, facet)
    row = dict(existing or {})
    # The first migration preserves every released citation byte-for-byte.
    # The canonical procedure snapshot carries the merged source set. Future
    # source replacement is therefore visible in the release diff instead of
    # silently rewriting a reviewed intent citation during bootstrap.
    released_sources = _dedupe_sources(_clean_list((existing or {}).get("source_refs")))
    prior_snapshot = (existing or {}).get("procedure_snapshot")
    if isinstance(prior_snapshot, Mapping):
        old_direct_url = str(prior_snapshot.get("source_url") or "").strip()
        new_direct_url = str(procedure.get("source_url") or "").strip()
        if old_direct_url and new_direct_url and old_direct_url != new_direct_url:
            current_refs = _dedupe_sources(_clean_list(procedure.get("source_refs")))
            released_sources = current_refs or [
                {
                    "source_url": new_direct_url,
                    "document_title": name,
                    "verification_status": procedure.get("source_status") or "managed_approved",
                }
            ]
    row.update(
        {
            "intent_id": str((existing or {}).get("intent_id") or f"FAC-{intent_hash}"),
            "canonical_question": question,
            "answer_text": deterministic_answer(procedure, facet),
            "question_variants": question_variants(question, name, facet, _text_list(procedure.get("aliases"))),
            "negative_examples": [
                canonical_question(negative_name, facet),
                f"Tôi đang hỏi một thủ tục khác, không phải {name}",
            ],
            "procedure_id": procedure_id,
            "procedure_name": name,
            "intent_type": facet,
            "answer_facet": facet,
            "source_refs": deepcopy(released_sources or sources),
            "source_status": "official_source_linked_to_managed_procedure",
            "scope": "Hai Phong; public citizen/guest",
            "review_status": "released",
            "public_state": "released",
            "review_findings": [],
            "source_cleanup_notes": ["Nội dung được dựng xác định từ bản thủ tục đang quản lý; không dùng LLM."],
            "provenance": {
                "source_kind": GENERATED_SOURCE_KIND,
                "source_name": "managed_procedures_runtime_v1.json",
            },
            "eligible_roles": ["citizen", "guest"],
            "approved_by": str((existing or {}).get("approved_by") or "project-owner"),
        }
    )
    return row


def _legal_diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, bool]:
    before = before or {}
    after = after or {}
    return {
        "answer": before.get("answer_text") != after.get("answer_text"),
        "question": before.get("canonical_question") != after.get("canonical_question"),
        "routing": before.get("question_variants") != after.get("question_variants"),
        "sources": before.get("source_refs") != after.get("source_refs"),
    }


def build_quick_chat_release(
    baseline: Mapping[str, Any],
    procedure_payload: Mapping[str, Any],
    *,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile a candidate release and a review-oriented diff report."""

    now = generated_at or datetime.now(timezone.utc).isoformat()
    baseline_rows = [dict(row) for row in baseline.get("intents") or [] if isinstance(row, Mapping)]
    procedures = [dict(row) for row in procedure_payload.get("procedures") or [] if isinstance(row, Mapping)]
    if not procedures:
        raise ValueError("Managed procedure catalogue is empty")
    procedure_by_id = {
        str(row.get("procedure_id") or "").strip(): row
        for row in procedures
        if str(row.get("procedure_id") or "").strip()
    }
    sources = _sources_by_procedure(baseline_rows, procedures)
    curated_rows = [
        row
        for row in baseline_rows
        if str((row.get("provenance") or {}).get("source_kind") or "") != GENERATED_SOURCE_KIND
    ]
    curated_pairs = {
        (str(row.get("procedure_id") or ""), str(row.get("intent_type") or row.get("answer_facet") or ""))
        for row in curated_rows
    }
    generated_existing = {
        (str(row.get("procedure_id") or ""), str(row.get("intent_type") or row.get("answer_facet") or "")): row
        for row in baseline_rows
        if str((row.get("provenance") or {}).get("source_kind") or "") == GENERATED_SOURCE_KIND
    }
    generated_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for index, procedure in enumerate(procedures):
        procedure_id = str(procedure.get("procedure_id") or "").strip()
        if not procedure_id:
            continue
        negative = procedures[(index + 1) % len(procedures)] if procedures else {}
        negative_name = str(negative.get("name") or "một thủ tục khác")
        for facet in CORE_FACETS:
            pair = (procedure_id, facet)
            if pair in curated_pairs:
                continue
            generated_by_pair[pair] = _generated_record(
                procedure,
                facet=facet,
                sources=sources.get(procedure_id, []),
                negative_name=negative_name,
                existing=generated_existing.get(pair),
            )

    output_rows: list[dict[str, Any]] = []
    emitted_pairs: set[tuple[str, str]] = set()
    for baseline_row in baseline_rows:
        procedure_id = str(baseline_row.get("procedure_id") or "").strip()
        facet = str(baseline_row.get("intent_type") or baseline_row.get("answer_facet") or "").strip()
        pair = (procedure_id, facet)
        source_kind = str((baseline_row.get("provenance") or {}).get("source_kind") or "")
        if source_kind == GENERATED_SOURCE_KIND and pair in generated_by_pair:
            row = generated_by_pair[pair]
            emitted_pairs.add(pair)
        else:
            row = dict(baseline_row)
        procedure = procedure_by_id.get(procedure_id)
        if procedure:
            snapshot = _procedure_snapshot(procedure, sources=sources.get(procedure_id, []))
            row["procedure_snapshot"] = snapshot
            row["procedure_revision_sha256"] = snapshot["procedure_revision_sha256"]
            row["projection_mode"] = (
                "generated_from_managed_procedure"
                if source_kind == GENERATED_SOURCE_KIND
                else "curated_override"
            )
            row["dependency_fields"] = list(FACET_DEPENDENCIES.get(facet, ()))
        else:
            row["projection_mode"] = "curated_extension"
        output_rows.append(row)
    for pair, row in generated_by_pair.items():
        if pair in emitted_pairs:
            continue
        procedure = procedure_by_id[pair[0]]
        snapshot = _procedure_snapshot(procedure, sources=sources.get(pair[0], []))
        row["procedure_snapshot"] = snapshot
        row["procedure_revision_sha256"] = snapshot["procedure_revision_sha256"]
        row["projection_mode"] = "generated_from_managed_procedure"
        row["dependency_fields"] = list(FACET_DEPENDENCIES.get(pair[1], ()))
        row["released_at"] = now
        output_rows.append(row)

    identity_payload = [
        {
            key: value
            for key, value in row.items()
            if key not in {"release_id", "released_at"}
        }
        for row in output_rows
    ]
    release_id = f"intent-release-{content_sha256(identity_payload)[:16]}"
    for row in output_rows:
        row["release_id"] = release_id

    before_by_id = {str(row.get("intent_id") or row.get("id") or ""): row for row in baseline_rows}
    after_by_id = {str(row.get("intent_id") or row.get("id") or ""): row for row in output_rows}
    all_ids = sorted(set(before_by_id) | set(after_by_id))
    changed = {
        "answer": [],
        "question": [],
        "routing": [],
        "sources": [],
        "added": [],
        "removed": [],
    }
    changed_dependencies: list[dict[str, Any]] = []
    procedure_fact_changes: list[dict[str, Any]] = []
    baseline_procedure_revisions = baseline.get("procedure_revisions") or {}
    if not isinstance(baseline_procedure_revisions, Mapping):
        baseline_procedure_revisions = {}
    for intent_id in all_ids:
        before = before_by_id.get(intent_id)
        after = after_by_id.get(intent_id)
        if before is None:
            changed["added"].append(intent_id)
            continue
        if after is None:
            changed["removed"].append(intent_id)
            continue
        for key, did_change in _legal_diff(before, after).items():
            if did_change:
                changed[key].append(intent_id)
        if after.get("projection_mode") == "curated_override":
            before_snapshot = before.get("procedure_snapshot")
            after_snapshot = after.get("procedure_snapshot")
            baseline_known = isinstance(before_snapshot, Mapping) or bool(
                baseline_procedure_revisions.get(str(after.get("procedure_id") or ""))
            )
            if isinstance(before_snapshot, Mapping) and isinstance(after_snapshot, Mapping):
                dependent_fields = after.get("dependency_fields") or []
                changed_fields = [
                    field
                    for field in dependent_fields
                    if before_snapshot.get(field) != after_snapshot.get(field)
                ]
                if changed_fields:
                    changed_dependencies.append(
                        {"intent_id": intent_id, "changed_fields": changed_fields}
                    )
            elif baseline_known and isinstance(after_snapshot, Mapping):
                old_revision = baseline_procedure_revisions.get(str(after.get("procedure_id") or ""))
                if old_revision and old_revision != after_snapshot.get("procedure_revision_sha256"):
                    changed_dependencies.append(
                        {"intent_id": intent_id, "changed_fields": ["procedure_revision_unknown_fields"]}
                    )
    for procedure_id, procedure in procedure_by_id.items():
        old_snapshot = next(
            (
                row.get("procedure_snapshot")
                for row in baseline_rows
                if row.get("procedure_id") == procedure_id
                and isinstance(row.get("procedure_snapshot"), Mapping)
            ),
            None,
        )
        if not isinstance(old_snapshot, Mapping):
            continue  # baseline migration: no prior managed snapshot exists
        new_snapshot = _procedure_snapshot(procedure, sources=sources.get(procedure_id, []))
        changed_fields = [
            field
            for field in PROCEDURE_SNAPSHOT_FIELDS
            if old_snapshot.get(field) != new_snapshot.get(field)
        ]
        if changed_fields:
            procedure_fact_changes.append(
                {"procedure_id": procedure_id, "changed_fields": changed_fields}
            )

    candidate = deepcopy(dict(baseline))
    extensions = dict(candidate.get("extensions") or {})
    extensions["canonical_procedure_projection"] = {
        "schema_version": RELEASE_SCHEMA,
        "baseline_release_id": baseline.get("release_id"),
        "procedure_catalog_sha256": content_sha256(procedures),
        "managed_procedure_count": len(procedure_by_id),
        "generated_intent_count": sum(row.get("projection_mode") == "generated_from_managed_procedure" for row in output_rows),
        "curated_override_count": sum(row.get("projection_mode") == "curated_override" for row in output_rows),
        "curated_extension_count": sum(row.get("projection_mode") == "curated_extension" for row in output_rows),
        "facets": list(CORE_FACETS),
    }
    candidate.update(
        {
            "schema_version": RELEASE_SCHEMA,
            "release_id": release_id,
            "serving": False,
            "activation_status": "release_candidate",
            "generated_at": now,
            "baseline_release_id": baseline.get("release_id"),
            "record_count": len(output_rows),
            "intents": output_rows,
            "procedure_revisions": {
                procedure_id: _procedure_snapshot(procedure, sources=sources.get(procedure_id, []))[
                    "procedure_revision_sha256"
                ]
                for procedure_id, procedure in procedure_by_id.items()
            },
            "extensions": extensions,
        }
    )
    report = validate_quick_chat_release(candidate, procedure_payload)
    report.update(
        {
            "schema_version": "quick-chat-release-diff-v1",
            "generated_at": now,
            "baseline_release_id": baseline.get("release_id"),
            "candidate_release_id": release_id,
            "baseline_record_count": len(baseline_rows),
            "candidate_record_count": len(output_rows),
            "legal_diff": {key: {"count": len(value), "intent_ids": value} for key, value in changed.items()},
            "curated_override_dependency_changes": changed_dependencies,
            "procedure_fact_changes": procedure_fact_changes,
        }
    )
    report["requires_legal_review"] = bool(changed_dependencies or procedure_fact_changes) or any(
        changed[key]
        for key in ("answer", "question", "sources", "added", "removed")
    )
    report["ready_for_quick_chat_test"] = bool(report["gate_passed"] and not report["requires_legal_review"])
    return candidate, report


def validate_quick_chat_release(
    release: Mapping[str, Any],
    procedure_payload: Mapping[str, Any],
) -> dict[str, Any]:
    rows = [dict(row) for row in release.get("intents") or [] if isinstance(row, Mapping)]
    procedures = [dict(row) for row in procedure_payload.get("procedures") or [] if isinstance(row, Mapping)]
    managed_ids = {str(row.get("procedure_id") or "").strip() for row in procedures}
    ids = [str(row.get("intent_id") or row.get("id") or "").strip() for row in rows]
    pairs = {
        (str(row.get("procedure_id") or ""), str(row.get("intent_type") or row.get("answer_facet") or ""))
        for row in rows
    }
    blockers: list[str] = []
    warnings: list[str] = []
    if not rows:
        blockers.append("INTENT_RELEASE_EMPTY")
    if any(not value for value in ids):
        blockers.append("INTENT_ID_MISSING")
    if len(set(ids)) != len(ids):
        blockers.append("INTENT_ID_DUPLICATED")
    if len(managed_ids) != len(procedures):
        blockers.append("PROCEDURE_ID_MISSING_OR_DUPLICATED")
    missing_sources = [
        intent_id
        for intent_id, row in zip(ids, rows)
        if not _dedupe_sources(_clean_list(row.get("source_refs")))
    ]
    unofficial_sources = [
        intent_id
        for intent_id, row in zip(ids, rows)
        if any(not _source_is_official(ref) for ref in _dedupe_sources(_clean_list(row.get("source_refs"))))
    ]
    empty_answers = [
        intent_id
        for intent_id, row in zip(ids, rows)
        if not str(row.get("answer_text") or row.get("answer") or "").strip()
    ]
    if missing_sources:
        blockers.append("INTENT_SOURCE_MISSING")
    if unofficial_sources:
        blockers.append("INTENT_SOURCE_NOT_OFFICIAL")
    if empty_answers:
        blockers.append("INTENT_ANSWER_EMPTY")
    missing_facets: dict[str, list[str]] = {}
    for procedure_id in sorted(managed_ids):
        absent = [facet for facet in CORE_FACETS if (procedure_id, facet) not in pairs]
        if absent:
            missing_facets[procedure_id] = absent
    if missing_facets:
        blockers.append("MANAGED_PROCEDURE_FACET_MISSING")
    direct_source_by_id = {
        str(row.get("procedure_id") or ""): str(row.get("source_url") or "").strip()
        for row in procedures
    }
    stale_generated_sources = [
        str(row.get("intent_id") or "")
        for row in rows
        if row.get("projection_mode") == "generated_from_managed_procedure"
        and direct_source_by_id.get(str(row.get("procedure_id") or ""))
        and direct_source_by_id[str(row.get("procedure_id") or "")]
        not in {
            _source_url(ref)
            for ref in _dedupe_sources(_clean_list(row.get("source_refs")))
        }
    ]
    if stale_generated_sources:
        blockers.append("GENERATED_INTENT_SOURCE_MISMATCH")
    unapproved_procedures = [
        str(row.get("procedure_id") or "")
        for row in procedures
        if row.get("approved") is False
        or str(row.get("review_status") or "approved").casefold()
        not in {"approved", "released", "published"}
        or str(row.get("source_status") or "managed_live").casefold()
        in {"expired", "withdrawn", "stale", "invalid", "excluded", "archived"}
    ]
    if unapproved_procedures:
        blockers.append("MANAGED_PROCEDURE_NOT_APPROVED_OR_STALE")
    weak_placeholder_ids = [
        intent_id
        for intent_id, row in zip(ids, rows)
        if "chưa có dữ kiện" in str(row.get("answer_text") or "").casefold()
        or "chưa có nội dung" in str(row.get("answer_text") or "").casefold()
        or "chưa gắn tệp" in str(row.get("answer_text") or "").casefold()
    ]
    if weak_placeholder_ids:
        warnings.append("ANSWER_CONTAINS_TRANSPARENT_DATA_GAP")
    fact_completeness = {
        field: sum(
            bool(_clean_list(row.get(field)))
            if field in {"documents_required", "steps", "forms"}
            else bool(str(row.get(field) or "").strip())
            for row in procedures
        )
        for field in ("documents_required", "steps", "submission_place", "duration", "fee", "forms")
    }
    if any(count < len(procedures) for count in fact_completeness.values()):
        warnings.append("MANAGED_PROCEDURE_FACTS_INCOMPLETE")
    if procedures and not all(
        isinstance(row.get("fact_sources"), Mapping)
        for row in procedures
    ):
        warnings.append("FIELD_LEVEL_SOURCE_MAPPING_NOT_COMPLETE")
    return {
        "gate_passed": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "intent_count": len(rows),
        "managed_procedure_count": len(managed_ids),
        "managed_procedure_with_all_facets_count": len(managed_ids) - len(missing_facets),
        "missing_facets": missing_facets,
        "unapproved_procedure_ids": unapproved_procedures,
        "stale_generated_source_intent_ids": stale_generated_sources,
        "procedure_fact_completeness": fact_completeness,
        "missing_source_intent_ids": missing_sources,
        "unofficial_source_intent_ids": unofficial_sources,
        "empty_answer_intent_ids": empty_answers,
        "transparent_data_gap_intent_ids": weak_placeholder_ids,
        "projection_counts": {
            mode: sum(str(row.get("projection_mode") or "") == mode for row in rows)
            for mode in (
                "generated_from_managed_procedure",
                "curated_override",
                "curated_extension",
            )
        },
    }


def activate_candidate_for_quick_chat(
    candidate: Mapping[str, Any],
    *,
    approved_by: str,
    activated_at: str | None = None,
) -> dict[str, Any]:
    """Return an active runtime payload after a caller has enforced its gate."""

    runtime = deepcopy(dict(candidate))
    now = activated_at or datetime.now(timezone.utc).isoformat()
    runtime.update(
        {
            "serving": True,
            "activation_status": "active",
            "activated_at": now,
            "approved_by": approved_by,
            "deployment_scope": "public_quick_chat_only",
        }
    )
    runtime.pop("generated_at", None)
    return runtime


__all__ = [
    "CORE_FACETS",
    "FACET_DEPENDENCIES",
    "GENERATED_SOURCE_KIND",
    "RELEASE_SCHEMA",
    "activate_candidate_for_quick_chat",
    "build_quick_chat_release",
    "canonical_question",
    "content_sha256",
    "deterministic_answer",
    "question_variants",
    "validate_quick_chat_release",
]
