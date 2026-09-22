"""Candidate-only duplicate review. No corpus, chunk or vector writes here."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from api.legal_document_identity import (
    candidate_identity_revision,
    compare_legal_documents,
    legal_identity,
    number_search_key,
)
from open_notebook.database.repository import ensure_record_id, repo_query

LIST_DUPLICATE_CHECK_TIMEOUT_SECONDS = 1.5


class DuplicateCheckUnavailable(RuntimeError):
    pass


class CandidateChanged(ValueError):
    pass


def candidate_record_id(value: Any):
    value = str(value or "")
    if ":" not in value:
        value = f"legal_crawl_candidate:{value}"
    if not value.startswith("legal_crawl_candidate:"):
        raise ValueError("Định danh candidate không hợp lệ.")
    return ensure_record_id(value)


async def update_candidate_if_current(candidate: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Optimistic ownership boundary shared by review/archive/enqueue/metadata."""
    params = {
        "id": candidate_record_id(candidate["id"]),
        "expected_status": candidate.get("status"),
        "data": {**updates, "updated": datetime.now(timezone.utc)},
    }
    if candidate.get("updated") is not None:
        revision_clause = "updated = $expected_updated"
        params["expected_updated"] = candidate["updated"]
    else:
        revision_clause = "updated = NONE"
    rows = await repo_query(
        "UPDATE legal_crawl_candidate MERGE $data WHERE id = $id "
        f"AND status = $expected_status AND {revision_clause} RETURN AFTER;",
        params,
    )
    if not rows:
        raise CandidateChanged("Candidate đã thay đổi hoặc đang được xử lý. Hãy làm mới và đối chiếu lại.")
    return rows[0]


async def warehouse_duplicate_rows(candidates: list[dict[str, Any]], management_url: str) -> list[dict[str, Any]]:
    numbers = list(dict.fromkeys(str(row.get("law_number") or (row.get("raw_metadata") or {}).get("law_number") or "") for row in candidates))
    numbers = [number for number in numbers if number_search_key(number)]
    if not numbers:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(f"{management_url}/management/duplicates/check", json={"law_numbers": numbers[:200]})
        response.raise_for_status()
        payload = response.json()
        if payload.get("complete") is not True or not isinstance(payload.get("documents"), list):
            raise DuplicateCheckUnavailable("Chưa kiểm tra được đầy đủ kho văn bản.")
        return [row for row in payload["documents"] if isinstance(row, dict)]
    except DuplicateCheckUnavailable:
        raise
    except Exception as exc:
        raise DuplicateCheckUnavailable("Không kết nối được dịch vụ kiểm tra trùng; chưa thể kết luận văn bản mới.") from exc


async def candidate_duplicate_rows(candidates: list[dict[str, Any]], *, query: Any = None) -> list[dict[str, Any]]:
    identities = [legal_identity(row) for row in candidates]
    numbers = sorted({number_search_key(identity["number"]) for identity in identities} - {""})
    urls = []
    for identity in identities:
        if identity["source_url"]:
            parts = urlsplit(identity["source_url"])
            urls.append(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))
    hashes = sorted({identity["content_sha256"] for identity in identities} - {""})
    if not numbers and not urls and not hashes:
        return []
    # Broad metadata prefilter for old rows as well as new canonical identities.
    # Compare full records afterwards: same path/printed number is not a match.
    rows = await (query or repo_query)(
        "SELECT * FROM legal_crawl_candidate WHERE "
        "string::replace(string::replace(string::replace(string::uppercase(law_number ?? ''), 'Đ', 'D'), /[^A-Z0-9]/, ''), /^0+/, '') IN $number_keys "
        "OR raw_metadata.legal_identity.number_key IN $number_keys "
        "OR string::split(source_url ?? '', '?')[0] IN $url_bases "
        "OR raw_metadata.legal_identity.content_sha256 IN $content_hashes "
        "OR content_hash IN $content_hashes LIMIT 1001;",
        {"number_keys": numbers, "url_bases": sorted(set(urls)), "content_hashes": hashes},
    )
    if len(rows) > 1000:
        raise DuplicateCheckUnavailable("Có quá nhiều candidate cần đối chiếu; chưa kiểm tra được đầy đủ.")
    return rows


def identity_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    identity = legal_identity(candidate)
    return {**identity, "number_key": number_search_key(identity["number"])}


def public_duplicate_match(candidate: dict[str, Any], target: dict[str, Any], target_type: str) -> dict[str, Any] | None:
    if target_type == "candidate" and str(target.get("id")) == str(candidate.get("id")):
        return None
    own_document = candidate.get("document_id") or (candidate.get("imported_document") or {}).get("document_id") or (candidate.get("imported_document") or {}).get("id")
    if candidate.get("status") == "imported" and target_type == "document" and str(own_document) == str(target.get("id")):
        return None
    comparison = compare_legal_documents(candidate, target)
    if comparison["kind"] == "unrelated":
        return None
    raw = target.get("raw_metadata") or {}
    candidate_target_usable = target_type == "document" or (
        target.get("status") not in {"duplicate_archived", "replacement_review", "rejected"}
        and (str(target.get("created") or ""), str(target.get("id"))) < (str(candidate.get("created") or ""), str(candidate.get("id")))
    )
    return {
        **comparison, "target_type": target_type, "id": str(target.get("id")),
        "title": target.get("title"), "law_number": target.get("law_number"),
        "issuing_agency": target.get("issuing_agency"),
        "issued_date": str(target.get("issued_date") or raw.get("issued_date") or "") or None,
        "source_url": legal_identity(target)["source_url"] or None, "status": target.get("status"),
        "target_revision": candidate_identity_revision(target),
        "can_archive": comparison["kind"] in {"duplicate_content", "identity_match"} and candidate_target_usable,
        "can_review_replacement": target_type == "document",
    }


async def annotate_candidate_duplicates(candidates: list[dict[str, Any]], management_url: str) -> list[dict[str, Any]]:
    """Bounded batch, no write and no per-row HTTP request."""
    check_status = "complete"
    errors: list[str] = []
    # List hints have a short shared budget; a slow auxiliary check must not
    # hide the queue. Mutations always perform a fresh, longer check afterwards.
    results = await asyncio.gather(
        asyncio.wait_for(
            warehouse_duplicate_rows(candidates, management_url),
            timeout=LIST_DUPLICATE_CHECK_TIMEOUT_SECONDS,
        ),
        asyncio.wait_for(
            candidate_duplicate_rows(candidates),
            timeout=LIST_DUPLICATE_CHECK_TIMEOUT_SECONDS,
        ),
        return_exceptions=True,
    )
    if isinstance(results[0], BaseException):
        warehouse = []
        check_status = "unavailable"
        errors.append("warehouse_duplicate_check_unavailable")
    else:
        warehouse = results[0]
    if isinstance(results[1], BaseException):
        queue = []
        check_status = "unavailable"
        errors.append("candidate_duplicate_check_unavailable")
    else:
        queue = results[1]
    projected = []
    priority = {"metadata_conflict": 0, "content_changed": 1, "duplicate_content": 2, "identity_match": 3}
    for candidate in candidates:
        matches = [match for target_type, records in (("document", warehouse), ("candidate", queue))
                   for target in records if (match := public_duplicate_match(candidate, target, target_type))]
        matches.sort(key=lambda match: (priority[match["kind"]], match["target_type"] != "document", str(match["id"])))
        document_match = next((match for match in matches if match["target_type"] == "document"), None)
        projected.append({
            **candidate,
            "comparison_status": matches[0]["kind"] if matches else ("new" if check_status == "complete" else "unchecked"),
            "duplicate_matches": matches,
            "duplicate_check_status": check_status,
            "duplicate_check_errors": errors,
            "duplicate_check_revision": candidate_identity_revision(candidate),
            "duplicate_runtime_document": document_match,
            "duplicate_resolution": (candidate.get("raw_metadata") or {}).get("duplicate_resolution"),
        })
    return projected


async def resolve_candidate_duplicate(
    candidate: dict[str, Any], *, action: str, target_type: str, target_id: str,
    expected_revision: str, target_revision: str, reason: str,
    confirmed_same_document: bool, actor_id: str, management_url: str,
) -> dict[str, Any]:
    if action not in {"archive_duplicate", "review_replacement"} or target_type not in {"document", "candidate"}:
        raise ValueError("Thao tác xử lý trùng không hợp lệ.")
    reason = reason.strip()
    if len(reason) < 10:
        raise ValueError("Cần ghi lý do đối chiếu ít nhất 10 ký tự.")
    raw = dict(candidate.get("raw_metadata") or {})
    receipt = raw.get("duplicate_resolution") or {}
    if receipt.get("action") == action and str(receipt.get("target_id")) == str(target_id) and receipt.get("target_type") == target_type and receipt.get("before_revision") == expected_revision:
        return {"candidate": candidate, "resolution": receipt, "idempotent": True}
    if candidate.get("status") not in {"pending", "changes_requested", "import_failed", "approved", "rejected"}:
        raise CandidateChanged("Không thể lưu trữ/đối chiếu candidate đang nhập, đã nhập hoặc đã xử lý.")
    if candidate_identity_revision(candidate) != expected_revision:
        raise CandidateChanged("Thông tin đối chiếu đã thay đổi. Hãy làm mới trước khi xác nhận.")
    jobs = await repo_query(
        "SELECT id FROM legal_import_job WHERE candidate = $candidate AND status IN ['queued', 'running'] LIMIT 1;",
        {"candidate": candidate_record_id(candidate["id"])},
    )
    if jobs:
        raise CandidateChanged("Candidate đang có tác vụ nhập kho. Không thể lưu trữ khi tác vụ chưa kết thúc.")
    if target_type == "document":
        targets = await warehouse_duplicate_rows([candidate], management_url)
        target = next((row for row in targets if str(row.get("id")) == str(target_id)), None)
    else:
        targets = await repo_query("SELECT * FROM legal_crawl_candidate WHERE id = $id LIMIT 1;", {"id": candidate_record_id(target_id)})
        target = targets[0] if targets else None
        # Keep the older candidate. This deterministic ordering prevents two
        # concurrent A→B / B→A archives from leaving no surviving candidate.
        if target and (str(target.get("created") or ""), str(target.get("id"))) >= (str(candidate.get("created") or ""), str(candidate.get("id"))):
            raise ValueError("Giữ candidate được tạo trước; hãy lưu trữ bản tạo sau.")
        if target and target.get("status") in {"duplicate_archived", "rejected", "replacement_review"}:
            raise ValueError("Bản giữ lại không còn ở trạng thái phù hợp. Hãy đối chiếu lại.")
    if not target or str(target.get("id")) == str(candidate.get("id")):
        raise ValueError("Không tìm thấy bản giữ lại hợp lệ.")
    if candidate_identity_revision(target) != target_revision:
        raise CandidateChanged("Bản giữ lại đã thay đổi. Hãy tải lại kết quả đối chiếu.")
    comparison = compare_legal_documents(candidate, target)
    kind = comparison["kind"]
    if kind == "unrelated":
        raise ValueError("Không có căn cứ xác định hai bản liên quan; không được lưu trữ như bản trùng.")
    if action == "archive_duplicate":
        if kind not in {"duplicate_content", "identity_match"}:
            raise ValueError("Toàn văn hoặc metadata có thay đổi; cần đối chiếu/thay thế, không lưu trữ như bản trùng.")
        if kind == "identity_match" and not confirmed_same_document:
            raise ValueError("Cùng định danh chưa chứng minh trùng toàn văn. Admin phải xác nhận đã đối chiếu bản gốc.")
    elif target_type != "document":
        raise ValueError("Luồng thay thế phải chọn văn bản đã có trong kho.")
    receipt = {
        "action": action, "target_type": target_type, "target_id": str(target_id),
        "target_title": target.get("title"), "target_revision": target_revision,
        "before_revision": expected_revision, "reason": reason, "actor_id": actor_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "comparison": comparison, "confirmed_same_document": confirmed_same_document,
        "candidate_identity": identity_metadata(candidate), "corpus_changed": False,
    }
    raw["duplicate_resolution"] = receipt
    raw["legal_identity"] = identity_metadata(candidate)
    state = "duplicate_archived" if action == "archive_duplicate" else "replacement_review"
    updated = await update_candidate_if_current(candidate, {
        "status": state, "review_status": state, "suggested_action": action,
        "review_note": reason, "raw_metadata": raw,
    })
    return {"candidate": updated, "resolution": receipt, "idempotent": False}
