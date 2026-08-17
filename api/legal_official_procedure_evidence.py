"""Deterministic, issue-bound evidence from reviewed official procedure sources.

This module is intentionally small and local-first.  It reads the frozen
National Public Service Portal snapshot already captured by the release data
pipeline and projects only the exact procedure/facet requested by one issue.
It never calls a model, changes stored legal text, or participates in legal
authority ordering.  The resulting rows are added only after the normal
validity/hierarchy/diversity gates have selected corpus evidence.

The current building-permit provisions are a reviewed code-owned overlay
because the 2026-07-30 procedure snapshot still contains references to the
superseded Decree 175/2024/ND-CP.  The overlay is effective only from
2026-07-01 and reproduces bounded passages from Decree 217/2026/ND-CP.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from api.legal_section_grounding import LegalIssue

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DVC_SNAPSHOT = (
    ROOT
    / "data"
    / "source_cache"
    / "form_requirements"
    / "dvc-form-requirements-2026-07-30.json"
)

_DVC_SOURCE = (
    "https://dichvucong.gov.vn/api/v1/submitting/"
    "formality/list-all-public-formality-by-citizen"
)
_DVC_PAGE = "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/{procedure_id}"
_DECREE_217_PAGE = (
    "https://vanban.chinhphu.vn/?docid=218509&orggroupid=2&pageid=27160"
)
_DECREE_217_PDF = (
    "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/6/217-ndcp.signed.pdf"
)
_DECREE_217_APPENDIX = (
    "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/6/pl217.pdf"
)
_DECREE_217_EFFECTIVE_FROM = date(2026, 7, 1)
_COMPLAINT_LAW_PAGE = (
    "https://vanban.chinhphu.vn/default.aspx?docid=162374&pageid=27160"
)
_COMPLAINT_FORM_PAGE = (
    "https://vanban.chinhphu.vn/default.aspx?docid=201363&pageid=27160"
)
_COMPLAINT_FORM_PDF = (
    "https://datafiles.chinhphu.vn/cpp/files/vbpq/2020/10/124.signed.pdf"
)


@dataclass(frozen=True)
class _ProcedureRoute:
    code: str
    procedure_id: str
    canonical_procedure_id: str
    exact_name: str
    domain: str
    topic_markers: tuple[str, ...]
    form_markers: tuple[str, ...]
    reviewed_deadline: str | None = None


_PROCEDURE_ROUTES = (
    _ProcedureRoute(
        code="1.001193",
        procedure_id="019d2bfd-3fe0-70ac-b9d6-5e9e20d6eef7",
        canonical_procedure_id="dang_ky_khai_sinh",
        exact_name="Thủ tục đăng ký khai sinh",
        domain="ho_tich_chung_thuc",
        topic_markers=("dang ky khai sinh",),
        form_markers=("to khai dang ky khai sinh",),
        reviewed_deadline=(
            "Ngay trong ngày tiếp nhận yêu cầu; trường hợp nhận hồ sơ sau "
            "15 giờ mà không giải quyết được ngay thì trả kết quả trong ngày "
            "làm việc tiếp theo."
        ),
    ),
    _ProcedureRoute(
        code="1.004873",
        procedure_id="019d2bfd-6eb3-7019-bf3f-fc58c9ee44b9",
        canonical_procedure_id="xac_nhan_tinh_trang_hon_nhan",
        exact_name="Thủ tục cấp Giấy xác nhận tình trạng hôn nhân",
        domain="ho_tich_chung_thuc",
        topic_markers=("xac nhan tinh trang hon nhan",),
        form_markers=("to khai cap giay xac nhan tinh trang hon nhan",),
        reviewed_deadline=(
            "03 ngày làm việc; trường hợp phải xác minh thì thời hạn giải "
            "quyết không quá 23 ngày."
        ),
    ),
    _ProcedureRoute(
        code="1.004194",
        procedure_id="019d2bf7-770b-734d-b7fb-5e1995f194f4",
        canonical_procedure_id="1.004194",
        exact_name="Đăng ký tạm trú",
        domain="cu_tru_an_ninh",
        topic_markers=("dang ky tam tru",),
        form_markers=("mau ct01", "to khai thay doi thong tin cu tru"),
    ),
    _ProcedureRoute(
        code="1.014027",
        procedure_id="019d2bff-2d80-74d8-8515-90f6f61822f0",
        canonical_procedure_id="1.014027",
        exact_name="Thực hiện, điều chỉnh, thôi hưởng trợ cấp hưu trí xã hội",
        domain="an_sinh_y_te_giao_duc",
        topic_markers=("tro cap huu tri xa hoi",),
        form_markers=("mau so 01", "van ban de nghi huong tro cap huu tri xa hoi"),
    ),
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.replace("\u0111", "d").split())


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _preserve_lines(value: Any) -> str:
    return "\n".join(
        " ".join(line.split())
        for line in str(value or "").splitlines()
        if line.strip()
    )


def _as_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return date.today()


@lru_cache(maxsize=8)
def _load_snapshot_version(
    path_text: str,
    mtime_ns: int,
    size: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load one immutable file version while preserving change detection."""

    del mtime_ns, size
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None, "snapshot_unavailable"
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None, "snapshot_schema_invalid"
    if str(payload.get("source") or "") != _DVC_SOURCE:
        return None, "snapshot_source_unverified"
    if not str(payload.get("legal_as_of") or "") or not str(
        payload.get("retrieved_at") or ""
    ):
        return None, "snapshot_metadata_incomplete"
    if not isinstance(payload.get("details"), dict):
        return None, "snapshot_details_invalid"
    return payload, None


def _load_snapshot(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except OSError:
        return None, "snapshot_unavailable"
    return _load_snapshot_version(
        str(resolved),
        stat.st_mtime_ns,
        stat.st_size,
    )


def _matching_route(text: str, domain: str) -> _ProcedureRoute | None:
    folded = _fold(text)
    for route in _PROCEDURE_ROUTES:
        if domain and domain != "unknown" and route.domain != domain:
            continue
        if any(marker in folded for marker in route.topic_markers):
            return route
    return None


def _validated_procedure(
    payload: Mapping[str, Any], route: _ProcedureRoute
) -> dict[str, Any] | None:
    raw = (payload.get("details") or {}).get(route.procedure_id)
    if not isinstance(raw, dict):
        return None
    if (
        str(raw.get("id") or "") != route.procedure_id
        or str(raw.get("code") or "") != route.code
        or _compact(raw.get("name")) != route.exact_name
        or str(raw.get("state") or "").upper() not in {"ACTIVE", "UPDATED"}
    ):
        return None
    return dict(raw)


def _release_confirmed_snapshot_route(
    payload: Mapping[str, Any],
    *,
    procedure_id: str | None,
    domain: str,
) -> _ProcedureRoute | None:
    """Bind an active-release procedure code to one exact frozen DVC row.

    The caller may provide only an identity already confirmed by the active
    Feature 017 release.  This helper deliberately does not infer an identity
    from question text, fuzzy names or neighboring procedures.
    """

    confirmed_code = _compact(procedure_id)
    if not confirmed_code:
        return None
    matches = [
        dict(raw)
        for raw in (payload.get("details") or {}).values()
        if isinstance(raw, Mapping)
        and _compact(raw.get("code")) == confirmed_code
        and str(raw.get("state") or "").upper() in {"ACTIVE", "UPDATED"}
    ]
    if len(matches) != 1:
        return None
    procedure = matches[0]
    snapshot_id = _compact(procedure.get("id"))
    exact_name = _compact(procedure.get("name"))
    if not snapshot_id or not exact_name:
        return None
    return _ProcedureRoute(
        code=confirmed_code,
        procedure_id=snapshot_id,
        canonical_procedure_id=confirmed_code,
        exact_name=exact_name,
        domain=domain or "unknown",
        topic_markers=(),
        form_markers=(),
    )


def _components(procedure: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for case in procedure.get("executionCases") or []:
        if not isinstance(case, Mapping):
            continue
        for component in case.get("profileComponents") or []:
            if isinstance(component, Mapping) and _compact(component.get("name")):
                output.append({**component, "case_name": _compact(case.get("name"))})
    return output


def _form_component(
    procedure: Mapping[str, Any], route: _ProcedureRoute
) -> dict[str, Any] | None:
    for component in _components(procedure):
        folded = _fold(component.get("name"))
        if any(marker in folded for marker in route.form_markers):
            return component
    return None


def _document_text(procedure: Mapping[str, Any], route: _ProcedureRoute) -> str:
    all_components = _components(procedure)
    selected: list[dict[str, Any]] = []
    form = _form_component(procedure, route)
    if form:
        selected.append(form)

    # The first execution case is the ordinary applicant case in the reviewed
    # procedures.  Do not mix list/military variants into an individual
    # temporary-residence answer.
    cases = [
        case
        for case in procedure.get("executionCases") or []
        if isinstance(case, Mapping)
    ]
    ordinary = list((cases[0] if cases else {}).get("profileComponents") or [])
    if route.code in {"1.001193", "1.004873"} and len(cases) > 1:
        # Identity/residence items are separately labelled "must present" and
        # are part of the direct-submission checklist for civil-status cases.
        ordinary.extend(cases[1].get("profileComponents") or [])
    for component in ordinary:
        if not isinstance(component, Mapping) or not _compact(component.get("name")):
            continue
        if form and component.get("profileComponentId") == form.get("profileComponentId"):
            continue
        selected.append(dict(component))

    # Put general components before exceptional variants while preserving the
    # exact official component text.
    preferred = (
        "giay chung sinh",
        "giay to tuy than",
        "thong tin chung minh ve cho o hop phap",
    )
    selected.sort(
        key=lambda item: (
            0 if item is form else 1,
            next(
                (index for index, marker in enumerate(preferred) if marker in _fold(item.get("name"))),
                len(preferred),
            ),
        )
    )
    lines = []
    seen: set[str] = set()
    for component in selected:
        text = _compact(component.get("name"))
        key = _fold(text)
        if not text or key in seen:
            continue
        seen.add(key)
        lines.append(f"- {text.lstrip('- ').strip()}")
        if len(lines) >= 9:
            break
    return "Thành phần hồ sơ do Cổng Dịch vụ công Quốc gia công bố:\n" + "\n".join(lines)


def _authority_text(procedure: Mapping[str, Any]) -> str:
    agencies: list[str] = []
    for field in (
        "departmentsExecuting",
        "unitGroupsExecuting",
        "departmentsAuthority",
        "unitGroupsAuthority",
    ):
        for item in procedure.get(field) or []:
            if isinstance(item, Mapping):
                name = _compact(item.get("name"))
                if name and name not in agencies:
                    agencies.append(name)
    if not agencies:
        return ""
    return "Cơ quan thực hiện thủ tục: " + "; ".join(agencies) + "."


def _deadline_text(
    procedure: Mapping[str, Any], route: _ProcedureRoute
) -> str:
    if route.reviewed_deadline:
        return route.reviewed_deadline
    descriptions: list[str] = []
    for method in procedure.get("executionMethods") or []:
        if not isinstance(method, Mapping):
            continue
        description = _compact(method.get("description"))
        unit = str(method.get("processingTimeUnit") or "").upper()
        amount = method.get("processingTime")
        if unit == "WORKING_DAY" and isinstance(amount, (int, float)):
            if (
                description
                and "ngày làm việc" in description.casefold()
                and "thời gian tiếp nhận" not in description.casefold()
            ):
                text = description
            else:
                text = f"Thời hạn giải quyết: {amount:g} ngày làm việc."
        elif description:
            text = description
        else:
            # OTHER without a description is not a safe, user-facing unit.
            continue
        if text not in descriptions:
            descriptions.append(text)
    return descriptions[0] if descriptions else ""


def _fee_text(procedure: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for method in procedure.get("executionMethods") or []:
        if not isinstance(method, Mapping):
            continue
        method_name = str(method.get("submissionMethod") or "").upper()
        for fee in method.get("fees") or []:
            if not isinstance(fee, Mapping):
                continue
            description = _compact(fee.get("description"))
            folded = _fold(description)
            if "theo danh sach" in folded and "ca nhan, ho gia dinh" not in folded:
                continue
            if not description:
                continue
            text = f"{method_name}: {description}"
            if text not in lines:
                lines.append(text)
    return "\n".join(lines[:3])


def _procedure_text(procedure: Mapping[str, Any]) -> str:
    steps = [
        _compact(item.get("description"))
        for item in procedure.get("executionSteps") or []
        if isinstance(item, Mapping) and _compact(item.get("description"))
    ]
    return steps[0][:2400] if steps else ""


def _legal_basis_text(procedure: Mapping[str, Any]) -> str:
    """Render only legal-basis identities published with the procedure."""

    lines: list[str] = []
    seen: set[str] = set()
    for item in procedure.get("legalBasisesDetails") or []:
        if not isinstance(item, Mapping):
            continue
        code = _compact(item.get("code"))
        name = _compact(item.get("name"))
        if not code and not name:
            continue
        text = f"{code}: {name}" if code and name else code or name
        key = _fold(text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {text}")
    if not lines:
        return ""
    return (
        "C\u0103n c\u1ee9 ph\u00e1p l\u00fd do C\u1ed5ng D\u1ecbch v\u1ee5 c\u00f4ng Qu\u1ed1c gia c\u00f4ng b\u1ed1:\n"
        + "\n".join(lines)
    )


def _repair_reviewed_snapshot_artifacts(
    text: str, route: _ProcedureRoute, facet: str
) -> str:
    """Repair only audited transcription damage in the frozen DVC snapshot.

    The 2026-07-30 snapshot contains a damaged duplicate of the birth-
    registration execution text. A correct copy from the same official
    publication is also present in that captured response. Keep the corpus
    immutable and repair the two exact damaged spans only while projecting
    procedure evidence; unknown text is returned unchanged.
    """
    if route.code != "1.001193" or facet not in {"procedure", "next_action"}:
        return text
    return (
        text.replace(
            "nộp hồ sơ đăng ký khai tâm Phục vụ hành chính công",
            "nộp hồ sơ đăng ký khai sinh tại Trung tâm Phục vụ hành chính công",
        )
        .replace(
            "hoàn tất việc sinh tại Trung nộp hồ sơ",
            "hoàn tất việc nộp hồ sơ",
        )
    )


def _dvc_facet_text(
    procedure: Mapping[str, Any], route: _ProcedureRoute, facet: str
) -> tuple[str, dict[str, Any]]:
    extra: dict[str, Any] = {}
    if facet == "documents":
        return _document_text(procedure, route), extra
    if facet == "authority":
        return _authority_text(procedure), extra
    if facet == "deadline":
        return _deadline_text(procedure, route), extra
    if facet == "fee":
        return _fee_text(procedure), extra
    if facet == "condition":
        text = _preserve_lines(
            procedure.get("description") or procedure.get("requirementsAndConditions")
        )
        return text, extra
    if facet == "rule":
        return _legal_basis_text(procedure), extra
    if facet in {"procedure", "next_action"}:
        return _repair_reviewed_snapshot_artifacts(
            _procedure_text(procedure), route, facet
        ), extra
    if facet == "form":
        component = _form_component(procedure, route)
        if not component:
            return "", extra
        attachments = [
            dict(item)
            for item in component.get("attachments") or []
            if isinstance(item, Mapping)
        ]
        extra.update(
            {
                "form_code": (
                    "CT01" if route.code == "1.004194" else "01" if route.code == "1.014027" else None
                ),
                "form_name": _compact(component.get("name")),
                "official_attachment_metadata": attachments[:1],
            }
        )
        return "Biểu mẫu do Cổng Dịch vụ công Quốc gia công bố: " + _compact(
            component.get("name")
        ), extra
    return "", extra


def _dvc_row(
    *,
    issue: LegalIssue,
    route: _ProcedureRoute,
    procedure: Mapping[str, Any],
    payload: Mapping[str, Any],
    facet: str,
    content: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    source_id = f"dvc:{route.code}:{facet}:{issue.issue_id}"
    return {
        "source_id": source_id,
        "id": source_id,
        "chunk_id": source_id,
        "document_id": f"dvc:{route.code}",
        "request_id": issue.request_id,
        "issue_id": issue.issue_id,
        "domain": route.domain,
        "domain_slug": route.domain,
        "scope": "central",
        "official_level": "central",
        "authority_level": "central",
        "authority_label": "Cổng Dịch vụ công Quốc gia",
        "authority_rank": 70,
        "authority_confidence": "reviewed_official_procedure",
        "official": True,
        "provenance_verified": True,
        "approved": True,
        "source_type": "official_procedure_publication",
        "issuing_agency": "Cổng Dịch vụ công Quốc gia",
        "document_type": "Thủ tục hành chính",
        "document_title": f"Cổng Dịch vụ công Quốc gia – {route.exact_name}",
        "label": f"Mã TTHC {route.code}",
        "procedure_id": route.canonical_procedure_id,
        "procedure_code": route.code,
        "procedure_name": route.exact_name,
        "article_title": route.exact_name,
        "applicability_info": route.exact_name,
        "effective_status": "active",
        "document_status": "active",
        "article_status": "active",
        "legal_as_of": str(payload.get("legal_as_of") or ""),
        "snapshot_retrieved_at": str(payload.get("retrieved_at") or ""),
        "source_url": _DVC_PAGE.format(procedure_id=route.procedure_id),
        "source_registry_url": _DVC_SOURCE,
        "content": content,
        "matched_child_content": content,
        "matched_child_heading": f"{route.exact_name} > {facet}",
        "supported_facets": (
            ["condition", "rule"]
            if facet == "condition"
            else ["procedure", "next_action"]
            if facet == "procedure"
            else [facet]
        ),
        "score": 1.0,
        "rerank_score": 1.0,
        **dict(extra),
    }


_BUILDING_PASSAGES: dict[str, tuple[dict[str, Any], ...]] = {
    "condition": (
        {
            "article_number": "50",
            "clause_number": "1, 2(b) và 4",
            "content": (
                "Điều 50. Điều kiện cấp giấy phép xây dựng mới. "
                "1. Phù hợp với mục đích sử dụng đất theo quy định của pháp luật về đất đai "
                "được xác định tại giấy tờ hợp pháp về đất đai theo quy định tại Điều 55 Nghị định này. "
                "2. b) Công trình nhà ở riêng lẻ của hộ gia đình, cá nhân phải phù hợp với một trong "
                "các loại quy hoạch tương ứng theo quy định tại điểm g, h và i khoản 1 Điều 26 Nghị định này. "
                "4. Thiết kế xây dựng công trình đã được lập, thẩm định "
                "và phê duyệt theo quy định tại Luật Xây dựng năm 2025 và Nghị định này."
            ),
        },
    ),
    "rule": (
        {
            "article_number": "50",
            "content": (
                "Điều 50. Việc cấp phép xây dựng phải tuân thủ quy định về điều kiện để cấp giấy phép "
                "xây dựng theo quy định tại khoản 1 Điều 44 Luật Xây dựng năm 2025, một số nội dung "
                "được quy định cụ thể tại Điều này."
            ),
        },
    ),
    "authority": (
        {
            "article_number": "53",
            "clause_number": "1",
            "content": (
                "Điều 53. Thẩm quyền cấp giấy phép xây dựng. 1. Ủy ban nhân dân cấp xã cấp giấy phép "
                "xây dựng cho công trình cấp III, cấp IV, công trình nhà ở riêng lẻ của hộ gia đình, cá "
                "nhân trên địa bàn do mình quản lý (trừ các công trình quy định tại khoản 2 Điều này)."
            ),
        },
    ),
    "procedure": (
        {
            "article_number": "54",
            "clause_number": "1",
            "content": (
                "Điều 54. 1. Tổ chức, cá nhân đề nghị cấp giấy phép xây dựng nộp bộ hồ sơ cho cơ quan "
                "có thẩm quyền, được thực hiện trực tuyến tại Cổng Dịch vụ công quốc gia; trường hợp do "
                "sự cố khách quan, đột xuất không thể thực hiện trên môi trường điện tử thì thực hiện "
                "trực tiếp tại Bộ phận Một cửa hoặc thông qua dịch vụ bưu chính công ích."
            ),
        },
    ),
    "deadline": (
        {
            "article_number": "54",
            "clause_number": "1",
            "point_number": "b",
            "content": (
                "Điều 54 khoản 1 điểm b. Kể từ ngày nhận đủ hồ sơ hợp lệ, thời gian cấp giấy phép xây "
                "dựng mới, điều chỉnh, sửa chữa, cải tạo, di dời, có thời hạn của cơ quan có thẩm quyền "
                "đối với công trình nhà ở riêng lẻ trong thời hạn 07 ngày làm việc."
            ),
        },
    ),
    "documents": (
        {
            "article_number": "60",
            "clause_number": "1 và 2",
            "content": (
                "Điều 60. Hồ sơ đề nghị cấp giấy phép xây dựng đối với công trình nhà ở riêng lẻ. "
                "1. Đơn đề nghị cấp giấy phép xây dựng theo quy định tại Mẫu số 01 Phụ lục II ban hành "
                "kèm theo Nghị định này. 2. Một trong các loại giấy tờ hợp pháp về đất đai để cấp giấy "
                "phép xây dựng theo quy định tại Điều 55 Nghị định này; văn bản ý kiến của cơ quan chuyên "
                "môn về văn hóa cấp tỉnh (trường hợp pháp luật về di sản văn hóa có yêu cầu)."
            ),
        },
        {
            "article_number": "60",
            "clause_number": "3",
            "point_number": "a",
            "content": (
                "Điều 60. Hồ sơ đề nghị cấp giấy phép xây dựng đối với công trình nhà ở riêng lẻ. "
                "Khoản 3 điểm a. Đối với công trình nhà ở riêng lẻ của hộ gia đình, cá nhân: bộ "
                "bản vẽ thiết kế xây dựng kèm theo; kết quả thực hiện thủ tục hành chính theo quy định "
                "của pháp luật về phòng cháy, chữa cháy và cứu nạn, cứu hộ (nếu có yêu cầu); Báo cáo kết "
                "quả thẩm tra thiết kế xây dựng trong trường hợp pháp luật về xây dựng có yêu cầu, gồm: "
                "bản vẽ mặt bằng công trình trên lô đất kèm theo sơ đồ vị trí công trình; bản vẽ mặt bằng "
                "các tầng, các mặt đứng và mặt cắt chính; bản vẽ mặt bằng móng và mặt cắt móng kèm sơ đồ "
                "đấu nối hạ tầng kỹ thuật; bản cam kết bảo đảm an toàn đối với công trình liền kề."
            ),
        },
    ),
    "form": (
        {
            "article_number": "60",
            "clause_number": "1",
            "content": (
                "Điều 60 khoản 1: Đơn đề nghị cấp giấy phép xây dựng theo quy định tại Mẫu số 01 "
                "Phụ lục II ban hành kèm theo Nghị định này. Phụ lục II: Mẫu số 01 - Đơn đề nghị cấp "
                "giấy phép xây dựng mới."
            ),
            "form_code": "01",
            "form_name": "Mẫu số 01 - Đơn đề nghị cấp giấy phép xây dựng mới",
            "form_download_url": _DECREE_217_APPENDIX,
        },
    ),
}


_COMPLAINT_PASSAGES: dict[str, tuple[dict[str, Any], ...]] = {
    "condition": (
        {
            "law_number": "02/2011/QH13",
            "document_title": "Luật Khiếu nại",
            "document_type": "Luật",
            "article_number": "7",
            "clause_number": "1",
            "source_url": _COMPLAINT_LAW_PAGE,
            "content": (
                "Khi có căn cứ cho rằng quyết định hành chính, hành vi hành "
                "chính là trái pháp luật, xâm phạm trực tiếp đến quyền, lợi ích "
                "hợp pháp của mình thì người khiếu nại khiếu nại lần đầu đến "
                "người đã ra quyết định hành chính hoặc cơ quan có người có "
                "hành vi hành chính hoặc khởi kiện vụ án hành chính tại Tòa án "
                "theo quy định của Luật tố tụng hành chính."
            ),
        },
    ),
    "documents": (
        {
            "law_number": "02/2011/QH13",
            "document_title": "Luật Khiếu nại",
            "document_type": "Luật",
            "article_number": "8",
            "clause_number": "2",
            "source_url": _COMPLAINT_LAW_PAGE,
            "content": (
                "Trường hợp khiếu nại được thực hiện bằng đơn thì trong đơn "
                "khiếu nại phải ghi rõ ngày, tháng, năm khiếu nại; tên, địa chỉ "
                "của người khiếu nại; tên, địa chỉ của cơ quan, tổ chức, cá nhân "
                "bị khiếu nại; nội dung, lý do khiếu nại, tài liệu liên quan đến "
                "nội dung khiếu nại và yêu cầu giải quyết của người khiếu nại. "
                "Đơn khiếu nại phải do người khiếu nại ký tên hoặc điểm chỉ."
            ),
        },
    ),
    "authority": (
        {
            "law_number": "02/2011/QH13",
            "document_title": "Luật Khiếu nại",
            "document_type": "Luật",
            "article_number": "7",
            "clause_number": "1",
            "source_url": _COMPLAINT_LAW_PAGE,
            "content": (
                "Người khiếu nại khiếu nại lần đầu đến người đã ra quyết định "
                "hành chính hoặc cơ quan có người có hành vi hành chính."
            ),
        },
    ),
    "deadline": (
        {
            "law_number": "02/2011/QH13",
            "document_title": "Luật Khiếu nại",
            "document_type": "Luật",
            "article_number": "28",
            "source_url": _COMPLAINT_LAW_PAGE,
            "content": (
                "Thời hạn giải quyết khiếu nại lần đầu không quá 30 ngày, kể "
                "từ ngày thụ lý; đối với vụ việc phức tạp thì thời hạn giải "
                "quyết có thể kéo dài hơn nhưng không quá 45 ngày, kể từ ngày "
                "thụ lý. Ở vùng sâu, vùng xa đi lại khó khăn thì thời hạn giải "
                "quyết khiếu nại không quá 45 ngày, kể từ ngày thụ lý; đối với "
                "vụ việc phức tạp thì thời hạn có thể kéo dài hơn nhưng không "
                "quá 60 ngày, kể từ ngày thụ lý."
            ),
        },
    ),
    "form": (
        {
            "law_number": "124/2020/NĐ-CP",
            "document_title": "Nghị định 124/2020/NĐ-CP",
            "document_type": "Nghị định",
            "article_number": "3",
            "source_url": _COMPLAINT_FORM_PAGE,
            "content": (
                "Đơn khiếu nại được thực hiện theo Mẫu số 01 ban hành kèm theo "
                "Nghị định này."
            ),
            "form_code": "01",
            "form_name": "Mẫu số 01 - Đơn khiếu nại",
            "form_download_url": _COMPLAINT_FORM_PDF,
        },
    ),
}


def _building_matches(issue: LegalIssue, question: str) -> bool:
    text = _fold(
        " ".join((question, issue.title, issue.query_text, issue.text, issue.subject))
    )
    return "giay phep xay dung" in text and "nha o rieng le" in text


def _building_rows(
    *, issue: LegalIssue, question: str, legal_as_of: str | date | None
) -> list[dict[str, Any]]:
    if not _building_matches(issue, question) or _as_date(legal_as_of) < _DECREE_217_EFFECTIVE_FROM:
        return []
    facet = "procedure" if issue.intent == "next_action" else str(issue.intent or "")
    passages = _BUILDING_PASSAGES.get(facet, ())
    output: list[dict[str, Any]] = []
    for index, passage in enumerate(passages, start=1):
        source_id = f"nd217:{facet}:{index}:{issue.issue_id}"
        output.append(
            {
                "source_id": source_id,
                "id": source_id,
                "chunk_id": source_id,
                "document_id": "217/2026/NĐ-CP",
                "request_id": issue.request_id,
                "issue_id": issue.issue_id,
                "domain": "dat_dai_xay_dung",
                "domain_slug": "dat_dai_xay_dung",
                "scope": "central",
                "official_level": "central",
                "authority_level": "central",
                "authority_label": "Chính phủ",
                "authority_rank": 80,
                "authority_confidence": "reviewed_official_text",
                "official": True,
                "provenance_verified": True,
                "approved": True,
                "source_type": "reviewed_current_legal_overlay",
                "issuing_agency": "Chính phủ",
                "document_type": "Nghị định",
                "document_title": "Nghị định 217/2026/NĐ-CP",
                "law_number": "217/2026/NĐ-CP",
                "procedure_id": "cap_giay_phep_xay_dung",
                "article_title": "Cấp giấy phép xây dựng nhà ở riêng lẻ",
                "applicability_info": "Cấp giấy phép xây dựng mới đối với nhà ở riêng lẻ",
                "effective_status": "active",
                "document_status": "active",
                "article_status": "active",
                "effective_from": "2026-07-01",
                "legal_as_of": _as_date(legal_as_of).isoformat(),
                "source_url": _DECREE_217_PAGE,
                "source_download_url": _DECREE_217_PDF,
                "content": passage["content"],
                "matched_child_content": passage["content"],
                "matched_child_heading": (
                    f"Nghị định 217/2026/NĐ-CP > Điều {passage.get('article_number')}"
                ),
                "supported_facets": (
                    ["condition", "rule"]
                    if facet == "condition"
                    else ["procedure", "next_action"]
                    if facet == "procedure"
                    else [facet]
                ),
                "score": 1.0,
                "rerank_score": 1.0,
                "validity_sync": {
                    "status": "effective",
                    "serving_action": "serve",
                    "verified_at": "2026-08-08",
                    "source_url": _DECREE_217_PAGE,
                    "effective_from": "2026-07-01",
                },
                **dict(passage),
            }
        )
    return output


def _complaint_matches(issue: LegalIssue, question: str) -> bool:
    text = _fold(
        " ".join((question, issue.title, issue.query_text, issue.text, issue.subject))
    )
    return (
        issue.domain == "khieu_nai_to_cao_xu_phat"
        and "khieu nai lan dau" in text
        and "quyet dinh hanh chinh" in text
    )


def _complaint_rows(*, issue: LegalIssue, question: str) -> list[dict[str, Any]]:
    if not _complaint_matches(issue, question):
        return []
    facet = "procedure" if issue.intent == "next_action" else str(issue.intent or "")
    passages = _COMPLAINT_PASSAGES.get(facet, ())
    output: list[dict[str, Any]] = []
    for index, passage in enumerate(passages, start=1):
        source_id = f"complaint-current:{facet}:{index}:{issue.issue_id}"
        output.append(
            {
                "source_id": source_id,
                "id": source_id,
                "chunk_id": source_id,
                "document_id": str(passage["law_number"]),
                "request_id": issue.request_id,
                "issue_id": issue.issue_id,
                "domain": "khieu_nai_to_cao_xu_phat",
                "domain_slug": "khieu_nai_to_cao_xu_phat",
                "scope": "central",
                "official_level": "central",
                "authority_level": "central",
                "authority_label": (
                    "Quốc hội"
                    if passage["document_type"] == "Luật"
                    else "Chính phủ"
                ),
                "authority_rank": (
                    100 if passage["document_type"] == "Luật" else 80
                ),
                "authority_confidence": "reviewed_official_text",
                "official": True,
                "provenance_verified": True,
                "approved": True,
                "source_type": "reviewed_current_legal_overlay",
                "issuing_agency": (
                    "Quốc hội"
                    if passage["document_type"] == "Luật"
                    else "Chính phủ"
                ),
                "procedure_id": "khieu_nai_hanh_chinh",
                "article_title": "Khiếu nại lần đầu quyết định hành chính",
                "applicability_info": (
                    "Khiếu nại lần đầu đối với quyết định hành chính"
                ),
                "effective_status": "active",
                "document_status": "active",
                "article_status": "active",
                "effective_from": (
                    "2012-07-01"
                    if passage["law_number"] == "02/2011/QH13"
                    else "2020-12-10"
                ),
                "source_download_url": (
                    _COMPLAINT_FORM_PDF
                    if passage["law_number"] == "124/2020/NĐ-CP"
                    else None
                ),
                "content": passage["content"],
                "matched_child_content": passage["content"],
                "matched_child_heading": (
                    f"{passage['document_title']} > Điều "
                    f"{passage.get('article_number')}"
                ),
                "supported_facets": [facet],
                "score": 1.0,
                "rerank_score": 1.0,
                **dict(passage),
            }
        )
    return output


def _build_release_confirmed_procedure_evidence(
    *,
    issues: Sequence[LegalIssue],
    procedure_id: str,
    required_facets_by_issue: Mapping[str, Sequence[str]] | None,
    snapshot: Mapping[str, Any] | None,
    snapshot_error: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project only a release-confirmed code from the frozen DVC snapshot."""

    rows: list[dict[str, Any]] = []
    matched_codes: set[str] = set()
    if snapshot is not None:
        for issue in issues:
            route = _release_confirmed_snapshot_route(
                snapshot,
                procedure_id=procedure_id,
                domain=issue.domain,
            )
            if route is None:
                continue
            procedure = _validated_procedure(snapshot, route)
            if procedure is None:
                continue
            facets = list(
                dict.fromkeys(
                    str(value or "")
                    for value in (
                        (required_facets_by_issue or {}).get(issue.issue_id)
                        or [issue.intent]
                    )
                    if str(value or "")
                )
            )
            for requested_facet in facets:
                facet = (
                    "procedure"
                    if requested_facet == "next_action"
                    else requested_facet
                )
                facet_issue = (
                    issue
                    if facet == issue.intent
                    else replace(issue, intent=facet)  # type: ignore[arg-type]
                )
                content, extra = _dvc_facet_text(procedure, route, facet)
                if not _compact(content):
                    continue
                rows.append(
                    _dvc_row(
                        issue=facet_issue,
                        route=route,
                        procedure=procedure,
                        payload=snapshot,
                        facet=facet,
                        content=content,
                        extra=extra,
                    )
                )
                matched_codes.add(route.code)
    return rows, {
        "adapter": "official_procedure_evidence_v1",
        "row_count": len(rows),
        "matched_procedure_codes": sorted(matched_codes),
        "reviewed_source_ids": [],
        "identity_source": (
            "release_confirmed_procedure_id" if procedure_id in matched_codes else None
        ),
        "snapshot_error": snapshot_error,
        "snapshot_legal_as_of": (
            str(snapshot.get("legal_as_of") or "") if snapshot else None
        ),
    }


def build_official_procedure_evidence(
    *,
    issues: Sequence[LegalIssue],
    question: str,
    legal_as_of: str | date | None,
    snapshot_path: Path | None = None,
    procedure_id: str | None = None,
    required_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return reviewed evidence rows bound to the exact current request issue.

    Broad snapshot procedures require an identity already confirmed by the
    active Feature 017 release. They are never inferred from question text.
    """

    rows: list[dict[str, Any]] = []
    matched_codes: set[str] = set()
    reviewed_sources: set[str] = set()
    snapshot, snapshot_error = _load_snapshot(snapshot_path or DEFAULT_DVC_SNAPSHOT)

    if procedure_id:
        snapshot_rows, snapshot_trace = _build_release_confirmed_procedure_evidence(
            issues=issues,
            procedure_id=str(procedure_id),
            required_facets_by_issue=required_facets_by_issue,
            snapshot=snapshot,
            snapshot_error=snapshot_error,
        )
        reviewed_rows: list[dict[str, Any]] = []
        reviewed_facets: set[tuple[str, str]] = set()
        for issue in issues:
            facets = list(
                dict.fromkeys(
                    str(value or "")
                    for value in (
                        (required_facets_by_issue or {}).get(issue.issue_id)
                        or [issue.intent]
                    )
                    if str(value or "")
                )
            )
            for requested_facet in facets:
                facet = (
                    "procedure"
                    if requested_facet == "next_action"
                    else requested_facet
                )
                facet_issue = (
                    issue
                    if facet == issue.intent
                    else replace(issue, intent=facet)  # type: ignore[arg-type]
                )
                reviewed = _building_rows(
                    issue=facet_issue,
                    question=question,
                    legal_as_of=legal_as_of,
                )
                if reviewed:
                    reviewed_sources.add("217/2026/NÄ-CP")
                else:
                    reviewed = _complaint_rows(
                        issue=facet_issue,
                        question=question,
                    )
                    reviewed_sources.update(
                        str(row.get("law_number") or "") for row in reviewed
                    )
                if not reviewed:
                    continue
                reviewed_rows.extend(reviewed)
                for row in reviewed:
                    reviewed_facets.update(
                        (issue.issue_id, str(supported))
                        for supported in row.get("supported_facets") or [facet]
                    )

        # Reviewed current-law overlays are authoritative for their exact
        # facet. The release-confirmed DVC publication may fill only facets
        # that the overlay does not cover.
        snapshot_rows = [
            row
            for row in snapshot_rows
            if not any(
                (str(row.get("issue_id") or ""), str(supported))
                in reviewed_facets
                for supported in row.get("supported_facets") or []
            )
        ]
        rows = [*reviewed_rows, *snapshot_rows]
        return rows, {
            **snapshot_trace,
            "row_count": len(rows),
            "reviewed_source_ids": sorted(reviewed_sources),
        }

    for issue in issues:
        building = _building_rows(
            issue=issue,
            question=question,
            legal_as_of=legal_as_of,
        )
        if building:
            rows.extend(building)
            reviewed_sources.add("217/2026/NĐ-CP")
            continue
        complaint = _complaint_rows(issue=issue, question=question)
        if complaint:
            rows.extend(complaint)
            reviewed_sources.update(
                str(row.get("law_number") or "") for row in complaint
            )
            continue
        if snapshot is None:
            continue
        route = _matching_route(
            " ".join((question, issue.title, issue.query_text, issue.text, issue.subject)),
            issue.domain,
        )
        if route is None:
            continue
        procedure = _validated_procedure(snapshot, route)
        if procedure is None:
            continue
        facet = "procedure" if issue.intent == "next_action" else str(issue.intent or "")
        content, extra = _dvc_facet_text(procedure, route, facet)
        if not _compact(content):
            continue
        rows.append(
            _dvc_row(
                issue=issue,
                route=route,
                procedure=procedure,
                payload=snapshot,
                facet=facet,
                content=content,
                extra=extra,
            )
        )
        matched_codes.add(route.code)

    return rows, {
        "adapter": "official_procedure_evidence_v1",
        "row_count": len(rows),
        "matched_procedure_codes": sorted(matched_codes),
        "reviewed_source_ids": sorted(reviewed_sources),
        "identity_source": None,
        "snapshot_error": snapshot_error,
        "snapshot_legal_as_of": (
            str(snapshot.get("legal_as_of") or "") if snapshot else None
        ),
    }
