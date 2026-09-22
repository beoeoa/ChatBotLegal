"""Resolve answer sources against the serving catalog before generation."""
from datetime import date
from typing import Any, Mapping, Sequence
from api.legal_official_procedure_evidence import official_procedure_document


def _active_release_form_is_bound(
    row: Mapping[str, Any],
    *,
    audience: str,
    as_of: date | str | None,
) -> bool:
    """Revalidate local form text against the active immutable release."""

    if str(row.get("evidence_kind") or "") not in {
        "active_release_form_text",
        "active_release_form_comparison",
    }:
        return False
    form_id = str(row.get("form_id") or "").strip()
    procedure_id = str(row.get("procedure_id") or "").strip()
    checksum = str(row.get("source_checksum") or "").strip().casefold()
    source_url = str(row.get("source_url") or "").strip()
    if not (form_id and procedure_id and len(checksum) == 64 and source_url):
        return False
    try:
        active_date = (
            as_of if isinstance(as_of, date)
            else date.fromisoformat(str(as_of or date.today().isoformat())[:10])
        )
        from api.form_governance_service import get_form_governance_service
        from api.form_router_v3 import _audience_is_allowed, _effective
        from api.official_source_adapters import is_allowlisted_official_url

        release = get_form_governance_service().repository.active_release()
        manifest = (release or {}).get("manifest") or release or {}
        asset = next(
            (
                item for item in manifest.get("assets") or []
                if str(item.get("form_id") or "") == form_id
            ),
            None,
        )
        if not isinstance(asset, Mapping):
            return False
        if (
            str(asset.get("coverage_status") or "") != "released"
            or str(asset.get("source_checksum") or "").casefold() != checksum
            or str(asset.get("source_url") or "").strip() != source_url
            or not is_allowlisted_official_url(source_url)
            or not _audience_is_allowed(asset.get("audiences"), audience)
            or not _effective(asset, active_date)
        ):
            return False
        return any(
            str(binding.get("form_id") or "") == form_id
            and str(binding.get("procedure_id") or "") == procedure_id
            and str(binding.get("coverage_status") or "") == "released"
            and _audience_is_allowed(binding.get("audience"), audience)
            and _effective(binding, active_date)
            for binding in manifest.get("bindings") or []
            if isinstance(binding, Mapping)
        )
    except (LookupError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _numeric_document_id(value: Any) -> str:
    raw = str(value or "").strip()
    for prefix in ("legal_document:", "legal_documents:", "legal:"):
        if raw.casefold().startswith(prefix):
            raw = raw[len(prefix):]
            break
    return raw if raw.isdigit() and int(raw) > 0 else ""


async def bind_serving_sources(
    rows: Sequence[Mapping[str, Any]],
    *,
    audience: str,
    temporal_scope: str = "current",
    as_of: date | str | None = None,
):
    from api.routers.legal_search import _request, LEGAL_MANAGEMENT_URL
    document_ids = list(dict.fromkeys(
        identity
        for row in rows
        if (identity := _numeric_document_id(row.get("document_id") or row.get("doc_id")))
    ))
    # Number-only lookup is retained solely for old release rows without a
    # stable ID. It must never replace an existing row's document identity.
    numbers = list(dict.fromkeys(
        str(row.get("law_number") or "").strip()
        for row in rows
        if row.get("law_number")
        and not _numeric_document_id(row.get("document_id") or row.get("doc_id"))
    ))
    indexed_ids: dict[str, Mapping[str, Any]] = {}
    indexed_numbers: dict[str, Mapping[str, Any]] = {}
    if document_ids or numbers:
        params: dict[str, Any] = {
            "audience": audience,
            "temporal_scope": (
                "historical" if str(temporal_scope).casefold() == "historical" else "current"
            ),
        }
        if as_of:
            params["as_of"] = str(as_of)[:10]
        payload = await _request("POST", "/documents/lookup-batch", base_url=LEGAL_MANAGEMENT_URL,
            timeout_seconds=3.0, params=params,
            json={"document_ids": document_ids, "law_numbers": numbers})
        documents = [row for row in payload.get("documents", []) if isinstance(row, Mapping)]
        indexed_ids = {str(row.get("id") or "").strip(): row for row in documents}
        # Results are newest-first. Preserve that deterministic choice only
        # for ID-less legacy evidence.
        for document in documents:
            number = str(document.get("law_number") or "").strip().casefold()
            if number:
                indexed_numbers.setdefault(number, document)
    result = []
    for source in rows:
        row = dict(source)
        if _active_release_form_is_bound(
            row,
            audience=audience,
            as_of=as_of,
        ):
            # This is locally extracted from checksum-bound release bytes. It
            # is not a legal-document row and therefore has no numeric corpus
            # document ID, but it has passed an equivalent active-release
            # identity, audience, date and official-source gate above.
            result.append(row)
            continue
        number = str(row.get("law_number") or "").strip().casefold()
        raw_document_id = str(row.get("document_id") or row.get("doc_id") or "").strip()
        if raw_document_id.startswith("dvc:"):
            if not official_procedure_document(raw_document_id[4:]):
                continue
            document_id = raw_document_id
        elif (document_id := _numeric_document_id(raw_document_id)):
            document = indexed_ids.get(document_id)
            if not document:
                continue
            catalog_number = str(document.get("law_number") or "").strip().casefold()
            if number and catalog_number and number != catalog_number:
                continue
        elif number:
            document = indexed_numbers.get(number)
            if not document:
                continue
            document_id = str(document.get("id") or "").strip()
            if not document_id:
                continue
        else:
            # Non-instrument sources must have their own locally readable publication.
            continue
        row.update(document_id=document_id, doc_id=document_id)
        row.pop("internal_url", None)
        row.pop("viewer_url", None)
        result.append(row)
    return result
