"""Discover official procedure/form source candidates from the national portal.

This command is deliberately candidate-only.  It writes versioned source
mapping artifacts, but it never changes review status to approved and never
mutates imported legal records or vector collections.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import fold_text


FORMS_DIR = ROOT / "notebook_data" / "forms"
REPORT_DIR = ROOT / "reports" / "feature005" / "forms-completion-20260724"
PORTAL_ORIGIN = "https://dichvucong.gov.vn"
SEARCH_ENDPOINT = (
    f"{PORTAL_ORIGIN}/api/v1/submitting/"
    "formality/list-all-public-formality-by-citizen"
)
DETAIL_ENDPOINT = (
    f"{PORTAL_ORIGIN}/api/v1/configuring/"
    "formality/get-formality-by-citizen"
)
USER_AGENT = "ChatBotLegal-FormSourceDiscovery/1.0"
CURRENT_STATES = {"ACTIVE", "UPDATED"}

OFFICIAL_TITLE_SEARCH_ALIASES = {
    # Search-only aliases mirror current official portal titles. They never
    # populate canonical metadata or create an approval decision.
    "cai_chinh_ho_tich": [
        "thay doi cai chinh bo sung thong tin ho tich",
    ],
    "cap_doi_giay_chung_nhan_dat": [
        "cap doi giay chung nhan quyen su dung dat",
    ],
    "dang_ky_the_chap_quyen_su_dung_dat": [
        "bien phap bao dam bang quyen su dung dat",
    ],
    "dang_ky_nuoi_con_nuoi": [
        "dang ky viec nuoi con nuoi trong nuoc",
    ],
    "dieu_chinh_thong_tin_cu_tru": [
        "dieu chinh thong tin ve cu tru",
    ],
    "ho_tro_nguoi_khuyet_tat": [
        (
            "xac dinh xac dinh lai muc do khuyet tat va cap "
            "giay xac nhan khuyet tat"
        ),
    ],
    "chung_thuc_hop_dong_giao_dich": [
        (
            "chung thuc giao dich lien quan den tai san la dong san "
            "quyen su dung dat nha o"
        ),
    ],
    "xoa_the_chap_dat_dai": [
        "xoa dang ky bien phap bao dam bang quyen su dung dat",
        "bien phap bao dam bang quyen su dung dat",
    ],
}

DOMAIN_CATEGORY_TERMS = {
    "ho_tich_chung_thuc": {
        "ho tich",
        "chung thuc",
        "nuoi con nuoi",
        "ly lich tu phap",
    },
    "cu_tru_an_ninh": {"cu tru", "dang ky quan ly cu tru"},
    "dat_dai_xay_dung": {
        "dat dai",
        "xay dung",
        "nha o",
        "ha tang ky thuat",
    },
    "khieu_nai_to_cao_xu_phat": {
        "khieu nai",
        "to cao",
        "tiep cong dan",
        "xu ly vi pham hanh chinh",
    },
    "an_sinh_y_te_giao_duc": {
        "bao tro xa hoi",
        "nguoi co cong",
        "tre em",
        "nguoi cao tuoi",
        "nguoi khuyet tat",
        "bao hiem y te",
        "giam ngheo",
    },
}

GENERIC_TOKENS = {
    "thu",
    "tuc",
    "huong",
    "dan",
    "de",
    "nghi",
    "mau",
    "don",
    "to",
    "khai",
}

CONFLICTING_ACTION_MODIFIERS = {
    "cap doi",
    "cap lai",
    "dang ky lai",
    "dieu chinh",
    "gia han",
    "ghi vao so",
    "huy",
    "thu hoi",
    "xoa",
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _tokens(value: str | None) -> set[str]:
    return {
        token
        for token in fold_text(value).split()
        if len(token) > 1 and token not in GENERIC_TOKENS
    }


def procedure_queries(
    procedure: dict[str, Any],
    *,
    limit: int = 5,
) -> list[str]:
    """Return bounded deterministic search variants for the official portal."""

    name = str(procedure.get("name") or "").strip()
    simplified_name = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    simplified_name = re.split(r"\s[/|]\s", simplified_name, maxsplit=1)[0]
    values = [
        name,
        simplified_name.strip(),
        *OFFICIAL_TITLE_SEARCH_ALIASES.get(
            str(procedure.get("procedure_id") or ""),
            [],
        ),
        *(str(value).strip() for value in procedure.get("aliases") or []),
        str(procedure.get("procedure_id") or "").replace("_", " ").strip(),
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        folded = fold_text(value)
        if len(folded) < 4 or folded in seen:
            continue
        seen.add(folded)
        unique.append(value)
        if len(unique) >= max(1, min(limit, 5)):
            break
    return unique


def _procedure_phrases(procedure: dict[str, Any]) -> list[str]:
    """Return reviewed identity phrases, including explicit slash branches."""

    values = [
        str(procedure.get("name") or "").strip(),
        *OFFICIAL_TITLE_SEARCH_ALIASES.get(
            str(procedure.get("procedure_id") or ""),
            [],
        ),
        *(str(value).strip() for value in procedure.get("aliases") or []),
        str(procedure.get("procedure_id") or "").replace("_", " ").strip(),
    ]
    expanded: list[str] = []
    for value in values:
        if not value:
            continue
        expanded.append(value)
        without_parenthetical = re.sub(r"\s*\([^)]*\)\s*", " ", value).strip()
        if without_parenthetical:
            expanded.append(without_parenthetical)
        expanded.extend(
            part.strip()
            for part in re.split(r"\s+[/|]\s+", without_parenthetical)
            if part.strip()
        )
        without_guidance = re.sub(
            r"^\s*(?:hướng dẫn|thu tuc|thủ tục)\s+",
            "",
            without_parenthetical,
            flags=re.IGNORECASE,
        ).strip()
        if without_guidance:
            expanded.append(without_guidance)
    unique: list[str] = []
    seen: set[str] = set()
    for value in expanded:
        folded = fold_text(value)
        if len(folded) < 4 or folded in seen:
            continue
        seen.add(folded)
        unique.append(value)
    return unique


def _procedure_identity(value: str | None) -> str:
    folded = fold_text(value)
    folded = re.sub(r"^\s*thu tuc\s+", "", folded)
    folded = re.sub(r"\([^)]*\)", " ", folded)
    folded = re.sub(r"[^0-9a-z\s]", " ", folded)
    return " ".join(folded.split())


def _has_conflicting_action_modifier(
    phrases: Iterable[str],
    candidate_name: str | None,
) -> bool:
    expected = " | ".join(fold_text(value) for value in phrases)
    candidate = fold_text(candidate_name)
    return any(
        modifier in candidate and modifier not in expected
        for modifier in CONFLICTING_ACTION_MODIFIERS
    )


def _text_similarity(left: str | None, right: str | None) -> float:
    a = fold_text(left)
    b = fold_text(right)
    if not a or not b:
        return 0.0
    a_tokens = _tokens(a)
    b_tokens = _tokens(b)
    coverage = len(a_tokens & b_tokens) / max(1, len(a_tokens))
    reverse_coverage = len(a_tokens & b_tokens) / max(1, len(b_tokens))
    sequence = SequenceMatcher(None, a, b).ratio()
    containment = 1.0 if a in b or b in a else 0.0
    return min(
        1.0,
        0.45 * coverage
        + 0.15 * reverse_coverage
        + 0.30 * sequence
        + 0.10 * containment,
    )


def _domain_matches(procedure: dict[str, Any], candidate: dict[str, Any]) -> bool:
    expected = DOMAIN_CATEGORY_TERMS.get(str(procedure.get("domain") or ""), set())
    if not expected:
        return True
    categories = " ".join(str(value) for value in candidate.get("categories") or [])
    folded = fold_text(categories)
    return any(term in folded for term in expected)


def score_formality_candidate(
    procedure: dict[str, Any],
    candidate: dict[str, Any],
) -> float:
    """Score a portal result without model inference."""

    phrases = _procedure_phrases(procedure)
    score = max(
        (_text_similarity(phrase, candidate.get("name")) for phrase in phrases),
        default=0.0,
    )
    candidate_identity = _procedure_identity(candidate.get("name"))
    if candidate_identity and any(
        candidate_identity == _procedure_identity(phrase) for phrase in phrases
    ):
        score += 0.14
    state = str(candidate.get("state") or "").upper()
    if state in CURRENT_STATES:
        score += 0.04
    elif state:
        score -= 0.20
    departments = fold_text(
        " ".join(str(value) for value in candidate.get("departments") or [])
    )
    if "cap xa" in departments or "cong an xa" in departments:
        score += 0.04
    if _domain_matches(procedure, candidate):
        score += 0.04
    else:
        score -= 0.18
    jurisdiction = fold_text(procedure.get("jurisdiction"))
    promulgating_department = fold_text(candidate.get("departmentPromulgate"))
    if (
        jurisdiction
        and jurisdiction not in {"viet nam", "toan quoc"}
        and jurisdiction in promulgating_department
    ):
        score += 0.08
    return round(max(0.0, min(score, 1.0)), 6)


def select_formality_candidate(
    procedure: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    *,
    minimum_score: float = 0.62,
) -> dict[str, Any] | None:
    scored: list[dict[str, Any]] = []
    phrases = _procedure_phrases(procedure)
    expects_foreign = any("nuoc ngoai" in fold_text(value) for value in phrases)
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("id"):
            continue
        if _has_conflicting_action_modifier(phrases, candidate.get("name")):
            continue
        candidate_folded = fold_text(candidate.get("name"))
        candidate_tokens = _tokens(candidate.get("name"))
        canonical_coverage = max(
            (
                len(_tokens(phrase) & candidate_tokens)
                / max(1, len(_tokens(phrase)))
                for phrase in phrases
            ),
            default=0.0,
        )
        if canonical_coverage < 0.60:
            continue
        if "nuoc ngoai" in candidate_folded and not expects_foreign:
            continue
        issuer_scope = fold_text(
            " ".join(
                [
                    str(candidate.get("departmentPromulgate") or ""),
                    *(str(value) for value in candidate.get("categories") or []),
                ]
            )
        )
        if (
            procedure.get("domain") == "khieu_nai_to_cao_xu_phat"
            and ("bo quoc phong" in issuer_scope or "bqp" in issuer_scope.split())
        ):
            continue
        scored.append(
            {
                **candidate,
                "match_score": score_formality_candidate(procedure, candidate),
            }
        )
    scored.sort(
        key=lambda item: (
            -float(item["match_score"]),
            str(item.get("code") or ""),
            str(item.get("id") or ""),
        )
    )
    if not scored or float(scored[0]["match_score"]) < minimum_score:
        return None
    if (
        len(scored) > 1
        and abs(
            float(scored[0]["match_score"])
            - float(scored[1]["match_score"])
        )
        < 0.015
        and str(scored[0].get("code") or "")
        != str(scored[1].get("code") or "")
    ):
        return None
    return scored[0]


def _compatible_form_domain(canonical: str, candidate: str) -> bool:
    expected = fold_text(canonical)
    actual = fold_text(candidate)
    compatible = {
        "ho tich chung thuc": {
            "ho tich",
            "chung thuc",
            "ho tich chung thuc",
        },
        "cu tru an ninh": {"cu tru", "cu tru an ninh"},
        "dat dai xay dung": {"dat dai xay dung", "dat dai", "xay dung"},
        "khieu nai to cao xu phat": {
            "khieu nai to cao",
            "khieu nai to cao xu phat",
            "xu phat",
        },
        "an sinh y te giao duc": {"an sinh y te giao duc"},
    }
    return actual in compatible.get(expected, {expected})


def select_legacy_form_candidate(
    form: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    *,
    minimum_score: float = 0.70,
) -> dict[str, Any] | None:
    """Select an existing official-asset record as a pending candidate."""

    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("has_official_file") is not True:
            continue
        source_page = str(
            candidate.get("source_page_url")
            or candidate.get("source_url")
            or ""
        )
        if not _official_download(source_page):
            continue
        score = _text_similarity(
            form.get("canonical_name"),
            candidate.get("form_title") or candidate.get("canonical_name"),
        )
        code = fold_text(form.get("form_code"))
        if code and code in fold_text(candidate.get("form_title")):
            score = max(score, 1.0)
        candidate_domain = str(candidate.get("domain") or "")
        if not _compatible_form_domain(
            str(form.get("domain") or ""),
            candidate_domain,
        ):
            if fold_text(candidate_domain) != "unknown" or score < 0.88:
                continue
        scored.append({**candidate, "match_score": round(score, 6)})
    scored.sort(
        key=lambda item: (
            -float(item["match_score"]),
            str(item.get("id") or ""),
        )
    )
    if not scored or float(scored[0]["match_score"]) < minimum_score:
        return None
    if (
        len(scored) > 1
        and abs(
            float(scored[0]["match_score"])
            - float(scored[1]["match_score"])
        )
        < 0.02
        and str(scored[0].get("source_sha256") or "")
        != str(scored[1].get("source_sha256") or "")
    ):
        return None
    return scored[0]


def collect_profile_components(detail: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    groups = [detail.get("profileComponents") or []]
    groups.extend(
        case.get("profileComponents") or []
        for case in detail.get("executionCases") or []
        if isinstance(case, dict)
    )
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            identity = str(
                item.get("profileComponentId")
                or item.get("id")
                or item.get("code")
                or fold_text(item.get("name"))
            )
            if not identity or identity in seen:
                continue
            seen.add(identity)
            components.append(dict(item))
    return components


def _attachment_url(attachment: dict[str, Any]) -> str | None:
    for key in (
        "downloadUrl",
        "downloadURL",
        "fileUrl",
        "fileURL",
        "url",
        "path",
    ):
        value = str(attachment.get(key) or "").strip()
        if value:
            return urljoin(PORTAL_ORIGIN, value)
    return None


def _official_download(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = str(parsed.hostname or "").casefold()
    return (
        parsed.scheme in {"http", "https"}
        and (
            host == "dichvucong.gov.vn"
            or host.endswith(".dichvucong.gov.vn")
            or host.endswith(".gov.vn")
        )
    )


def classify_form_source(
    *,
    form: dict[str, Any],
    components: Iterable[dict[str, Any]],
    source_page: str,
) -> dict[str, Any]:
    """Classify one form against the official detail response, never approve."""

    form_name = str(form.get("canonical_name") or "")
    form_code = fold_text(form.get("form_code"))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for component in components:
        name = str(component.get("name") or "")
        score = _text_similarity(form_name, name)
        code = fold_text(component.get("code"))
        if form_code and (form_code == code or form_code in fold_text(name)):
            score = max(score, 1.0)
        ranked.append((score, component))
    ranked.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("code") or ""),
            fold_text(item[1].get("name")),
        )
    )
    base = {
        "source_page": source_page,
        "review_status": "candidate_pending_review",
        "approved": False,
    }
    if not ranked or ranked[0][0] < 0.54:
        return {
            **base,
            "status": "NO_PUBLIC_DOWNLOAD_VERIFIED",
            "reason_code": "FORM_NOT_LISTED_IN_OFFICIAL_PROCEDURE",
            "match_score": round(ranked[0][0], 6) if ranked else 0.0,
        }
    score, component = ranked[0]
    attachments = [
        item
        for item in component.get("attachments") or []
        if isinstance(item, dict)
    ]
    urls = [
        value
        for value in (_attachment_url(item) for item in attachments)
        if _official_download(value)
    ]
    common = {
        **base,
        "matched_component_name": component.get("name"),
        "matched_component_code": component.get("code"),
        "match_score": round(score, 6),
    }
    if urls:
        return {
            **common,
            "status": "AVAILABLE_OFFICIAL_FILE",
            "reason_code": "OFFICIAL_COMPONENT_ATTACHMENT",
            "download_url": urls[0],
        }
    if component.get("hasElectronicForm") is True:
        return {
            **common,
            "status": "OFFICIAL_EFORM",
            "reason_code": "OFFICIAL_ELECTRONIC_FORM",
            "eform_url": source_page,
        }
    return {
        **common,
        "status": "OFFICIAL_PACKAGE_PAGE",
        "reason_code": "FORM_LISTED_WITHOUT_STANDALONE_DOWNLOAD",
    }


class PortalClient:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def search(self, query: str) -> list[dict[str, Any]]:
        response = self.client.post(
            SEARCH_ENDPOINT,
            json={
                "limit": 50,
                "lastId": "",
                "q": query,
                "categoryId": "",
                "departmentCode": "",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "OK":
            raise RuntimeError("OFFICIAL_PORTAL_SEARCH_FAILED")
        data = payload.get("data") or {}
        return [
            item
            for item in data.get("items") or []
            if isinstance(item, dict)
        ]

    def detail(self, formality_id: str) -> dict[str, Any]:
        response = self.client.post(
            DETAIL_ENDPOINT,
            json={"id": formality_id},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "OK" or not isinstance(payload.get("data"), dict):
            raise RuntimeError("OFFICIAL_PORTAL_DETAIL_FAILED")
        return payload["data"]


def discover(
    *,
    network: bool = True,
    max_items: int | None = None,
    rate_limit_seconds: float = 0.1,
) -> dict[str, Any]:
    procedures_payload = _load(FORMS_DIR / "canonical_procedures_v1.json")
    forms_payload = _load(FORMS_DIR / "canonical_forms_catalog_v1.json")
    legacy_payloads = [
        _load(FORMS_DIR / "haiphong_official_forms_catalog.json"),
        _load(FORMS_DIR / "haiphong_official_form_index.json"),
        _load(FORMS_DIR / "priority_official_forms.json"),
    ]
    procedures = list(procedures_payload.get("procedures") or [])
    if max_items is not None:
        procedures = procedures[: max(0, int(max_items))]
    forms_by_procedure: dict[str, list[dict[str, Any]]] = {}
    for form in forms_payload.get("forms") or []:
        for procedure_id in form.get("procedure_ids") or []:
            forms_by_procedure.setdefault(str(procedure_id), []).append(form)

    procedure_sources: list[dict[str, Any]] = []
    form_findings_by_id: dict[str, dict[str, Any]] = {}
    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30,
    )
    portal = PortalClient(client)
    try:
        for procedure in procedures:
            procedure_id = str(procedure.get("procedure_id") or "")
            if not network:
                procedure_sources.append(
                    {
                        "procedure_id": procedure_id,
                        "status": "NETWORK_NOT_PROBED",
                        "review_status": "candidate_pending_review",
                        "approved": False,
                    }
                )
                continue
            try:
                candidates_by_id: dict[str, dict[str, Any]] = {}
                for query in procedure_queries(procedure):
                    for candidate in portal.search(query):
                        candidate_id = str(candidate.get("id") or "")
                        if candidate_id:
                            candidates_by_id[candidate_id] = candidate
                candidates = list(candidates_by_id.values())
                selected = select_formality_candidate(procedure, candidates)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                procedure_sources.append(
                    {
                        "procedure_id": procedure_id,
                        "status": "BLOCKED_EXTERNAL",
                        "reason_code": type(exc).__name__,
                        "review_status": "candidate_pending_review",
                        "approved": False,
                    }
                )
                continue
            if selected is None:
                procedure_sources.append(
                    {
                        "procedure_id": procedure_id,
                        "status": "NEEDS_SOURCE_MAPPING",
                        "reason_code": "NO_UNAMBIGUOUS_OFFICIAL_MATCH",
                        "candidate_count": len(candidates),
                        "review_status": "candidate_pending_review",
                        "approved": False,
                    }
                )
                continue
            source_page = (
                f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/{selected['id']}"
            )
            try:
                detail = portal.detail(str(selected["id"]))
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                procedure_sources.append(
                    {
                        "procedure_id": procedure_id,
                        "status": "BLOCKED_EXTERNAL",
                        "reason_code": type(exc).__name__,
                        "official_procedure_code": selected.get("code"),
                        "official_procedure_url": source_page,
                        "review_status": "candidate_pending_review",
                        "approved": False,
                    }
                )
                continue
            components = collect_profile_components(detail)
            legal_basis = [
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                }
                for item in detail.get("legalBasisesDetails") or []
                if isinstance(item, dict)
            ]
            procedure_sources.append(
                {
                    "procedure_id": procedure_id,
                    "status": "OFFICIAL_PROCEDURE_MATCHED",
                    "official_formality_id": selected.get("id"),
                    "official_procedure_code": selected.get("code"),
                    "official_name": detail.get("name") or selected.get("name"),
                    "official_procedure_url": source_page,
                    "portal_state": detail.get("state") or selected.get("state"),
                    "category": (detail.get("category") or {}).get("name"),
                    "is_ward": detail.get("isWard"),
                    "match_score": selected.get("match_score"),
                    "legal_basis": legal_basis,
                    "review_status": "candidate_pending_review",
                    "approved": False,
                }
            )
            for form in forms_by_procedure.get(procedure_id, []):
                finding = classify_form_source(
                    form=form,
                    components=components,
                    source_page=source_page,
                )
                form_findings_by_id[str(form.get("form_id") or "")] = {
                    "form_id": form.get("form_id"),
                    "procedure_id": procedure_id,
                    "canonical_name": form.get("canonical_name"),
                    "official_procedure_code": selected.get("code"),
                    "legal_basis": legal_basis,
                    **finding,
                }
            time.sleep(max(0.0, rate_limit_seconds))
    finally:
        client.close()

    legacy_candidates = [
        item
        for payload in legacy_payloads
        for item in payload.get("forms") or []
        if isinstance(item, dict)
    ]
    for form in forms_payload.get("forms") or []:
        form_id = str(form.get("form_id") or "")
        current = form_findings_by_id.get(form_id)
        legacy = select_legacy_form_candidate(form, legacy_candidates)
        if legacy is not None:
            local_path = str(
                legacy.get("local_path")
                or legacy.get("priority_path")
                or legacy.get("source_package_path")
                or ""
            ).replace("\\", "/")
            download_url = str(
                legacy.get("source_download_url")
                or legacy.get("official_download_url")
                or ""
            )
            form_findings_by_id[form_id] = {
                "form_id": form_id,
                "procedure_id": (form.get("procedure_ids") or [None])[0],
                "canonical_name": form.get("canonical_name"),
                "status": "AVAILABLE_OFFICIAL_FILE",
                "reason_code": "EXISTING_OFFICIAL_CATALOG_CANDIDATE",
                "source_page": legacy.get("source_page_url")
                or legacy.get("source_url"),
                "download_url": download_url or None,
                "local_path": local_path or None,
                "file_format": legacy.get("file_type")
                or Path(local_path).suffix.lstrip(".")
                or None,
                "sha256": legacy.get("source_sha256")
                or legacy.get("sha256"),
                "legacy_source_id": legacy.get("id"),
                "match_score": legacy.get("match_score"),
                "legal_basis": current.get("legal_basis", []) if current else [],
                "review_status": "candidate_pending_review",
                "approved": False,
            }
        elif current is None:
            form_findings_by_id[form_id] = {
                "form_id": form_id,
                "procedure_id": (form.get("procedure_ids") or [None])[0],
                "canonical_name": form.get("canonical_name"),
                "status": "NEEDS_SOURCE_MAPPING",
                "reason_code": "NO_OFFICIAL_FORM_CANDIDATE",
                "review_status": "candidate_pending_review",
                "approved": False,
            }

    form_findings = sorted(
        form_findings_by_id.values(),
        key=lambda item: str(item.get("form_id") or ""),
    )
    procedure_counts: dict[str, int] = {}
    for item in procedure_sources:
        status = str(item.get("status") or "UNKNOWN")
        procedure_counts[status] = procedure_counts.get(status, 0) + 1
    form_counts: dict[str, int] = {}
    for item in form_findings:
        status = str(item.get("status") or "UNKNOWN")
        form_counts[status] = form_counts.get(status, 0) + 1
    generated_at = datetime.now(timezone.utc).isoformat()
    procedure_output = {
        "schema_version": 1,
        "generated_at": generated_at,
        "candidate_only": True,
        "procedures": procedure_sources,
    }
    forms_output = {
        "schema_version": 1,
        "generated_at": generated_at,
        "candidate_only": True,
        "findings": form_findings,
    }
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "network_enabled": network,
        "candidate_only": True,
        "auto_approved_count": 0,
        "procedure_count": len(procedure_sources),
        "procedure_status_counts": dict(sorted(procedure_counts.items())),
        "form_finding_count": len(form_findings),
        "form_status_counts": dict(sorted(form_counts.items())),
        "needs_source_mapping_count": procedure_counts.get(
            "NEEDS_SOURCE_MAPPING", 0
        ),
        "blocked_external_count": procedure_counts.get("BLOCKED_EXTERNAL", 0),
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }
    _write(
        FORMS_DIR / "canonical_procedure_sources_v1.json",
        procedure_output,
    )
    _write(
        FORMS_DIR / "canonical_form_source_findings_v1.json",
        forms_output,
    )
    _write(REPORT_DIR / "f2-official-source-discovery.json", manifest)
    return {
        "manifest": manifest,
        "procedures": procedure_output,
        "forms": forms_output,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover official procedure/form source candidates"
    )
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--rate-limit", type=float, default=0.1)
    args = parser.parse_args()
    result = discover(
        network=not args.no_network,
        max_items=args.max_items,
        rate_limit_seconds=args.rate_limit,
    )
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
