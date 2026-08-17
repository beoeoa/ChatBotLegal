"""Deterministic canonicalization for the three-tier official form inventory.

The module has no network or model dependency.  Crawlers provide facts from
official sources; this module applies fail-closed gates, deduplication and
source hierarchy.  It never creates a legal approval decision.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date
from typing import Any, Iterable

from api.form_source_resolution import extract_strict_form_code

CURRENT_PORTAL_STATES = {"ACTIVE", "UPDATED"}
EXPIRED_PORTAL_STATES = {"EXPIRED", "INACTIVE", "REVOKED", "SUPERSEDED"}
SOURCE_TIERS = ("central", "hai_phong_override", "le_chan_local")
SOURCE_TIER_PRIORITY = {value: index for index, value in enumerate(SOURCE_TIERS)}
DOMAIN_CATEGORY_TERMS = {
    "ho_tich_chung_thuc": {
        "ho tich",
        "chung thuc",
        "nuoi con nuoi",
        "ly lich tu phap",
        "cong chung",
        "quoc tich",
    },
    "cu_tru_an_ninh": {
        "dang ky quan ly cu tru",
        "cap quan ly can cuoc",
        "dinh danh va xac thuc dien tu",
        "quan ly xuat nhap canh",
        "quan ly nganh nghe dau tu kinh doanh co dieu kien ve an ninh trat tu",
    },
    "dat_dai_xay_dung": {
        "dat dai",
        "hoat dong xay dung",
        "nha o va cong so",
        "quy hoach do thi va nong thon",
        "ha tang ky thuat",
        "quan ly chat luong cong trinh xay dung",
        "kien truc",
        "phat trien do thi",
        "vat lieu xay dung",
        "kinh doanh bat dong san",
    },
    "khieu_nai_to_cao_xu_phat": {
        "khieu nai to cao",
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
        "giam ngheo",
        "bao hiem y te",
        "bao hiem xa hoi",
        "thuc hien chinh sach bhyt",
        "thuc hien chinh sach bhxh",
        "chi tra cac che do bhxh",
        "giao duc mam non",
        "giao duc tieu hoc",
        "giao duc trung hoc",
        "giao duc thuong xuyen",
        "giao duc dan toc",
        "giao duc nghe nghiep",
        "giao duc va dao tao thuoc he thong giao duc quoc dan",
        "kham benh chua benh",
        "duoc pham",
        "phong benh",
        "dan so ba me tre em",
        "an toan thuc pham",
        "y duoc co truyen",
        "thiet bi y te",
    },
}


def fold(value: Any) -> str:
    text = unicodedata.normalize(
        "NFD",
        str(value or "").casefold().replace("đ", "d"),
    )
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c))).split()
    )


def classify_domain(categories: Iterable[Any]) -> str | None:
    folded_categories = {fold(value) for value in categories if fold(value)}
    matches = [
        domain
        for domain, terms in DOMAIN_CATEGORY_TERMS.items()
        if folded_categories & terms
    ]
    return matches[0] if len(matches) == 1 else None


def infer_executing_level(departments: Iterable[Any]) -> str | None:
    text = fold(" ".join(str(value or "") for value in departments))
    if any(
        token in text
        for token in (
            "cap xa",
            "phuong",
            "uy ban nhan dan xa",
            "uy ban nhan dan thi tran",
            "cong an xa",
        )
    ):
        return "commune"
    if any(
        token in text
        for token in (
            "cap tinh",
            "uy ban nhan dan tinh",
            "uy ban nhan dan thanh pho",
            "so ",
            "trung tam phuc vu hanh chinh cong",
        )
    ):
        return "province"
    if any(token in text for token in ("bo ", "cuc ", "co quan trung uong")):
        return "central"
    return None


def classify_component_kind(component: dict[str, Any]) -> str:
    if component.get("isProcessingResult") is True:
        return "official_result"
    name = fold(component.get("name"))
    # These are commonly listed in a dossier but are evidence or an existing
    # document, not a blank template the applicant needs from the chatbot.
    non_form_nouns = (
        "anh ",
        "ban sao ",
        "ban chup ",
        "giay chung nhan ",
        "chung chi ",
        "ban do ",
        "so do ",
        "mau nhan ",
        "mau san pham ",
        "nhan san pham ",
        "phieu ly lich tu phap",
        "phieu hen ",
        "phieu ket qua ",
        "phieu kiem nghiem ",
        "danh sach ho so trang thiet bi ",
    )
    if name.startswith(non_form_nouns):
        return "supporting_document"

    semantic_name = re.sub(r"^(?:\d+\s+|[a-z]\s+)+", "", name)
    prescribed_form_nouns = (
        "to khai ",
        "don ",
        "phieu ",
        "ban khai ",
        "giay de nghi ",
        "van ban de nghi ",
        "danh sach ",
        "bang ke ",
        "bao cao ",
        "ban ve ",
        "so do ",
    )
    interactive_form_prefixes = (
        "mau ho tich dien tu tuong tac ",
        "mau dien tu tuong tac ",
        "bieu mau dien tu tuong tac ",
    )
    form_signals = (
        bool(extract_strict_form_code(component.get("name"))),
        semantic_name.startswith("mau so "),
        semantic_name.startswith(interactive_form_prefixes),
        semantic_name.startswith(prescribed_form_nouns)
        and " theo mau " in f" {semantic_name} ",
    )
    if any(form_signals):
        return "applicant_form"

    # A self-authored application/document is still a dossier requirement,
    # but without an explicit prescribed template it must not inflate the
    # official form catalog.  The chatbot can explain required contents while
    # clearly stating that no mandatory standard file is listed.
    applicant_submission_nouns = (
        "to khai ",
        "don ",
        "phieu khai ",
        "phieu de nghi ",
        "phieu dang ky ",
        "ban khai ",
        "giay de nghi ",
        "van ban de nghi ",
        "danh sach ",
        "bang ke ",
    )
    if name.startswith(applicant_submission_nouns):
        return "applicant_submission"
    return "supporting_document"


def extract_form_code(name: Any) -> str | None:
    code = extract_strict_form_code(name)
    if code is None:
        return None
    text = str(name or "")
    if re.search(r"(?iu)\bmẫu\s+số\s+", text):
        return f"Mẫu số {code}"
    if re.search(r"(?iu)\bmẫu\s+", text):
        return f"Mẫu {code}"
    return code


def classify_source_tier(
    *,
    formality_type: str | None,
    publisher: str | None,
    executing_level: str | None,
) -> str | None:
    """Classify an official fact without inferring missing geography."""

    kind = str(formality_type or "").upper()
    issuer = fold(publisher)
    level = fold(executing_level)
    if "le chan" in issuer and level in {"commune", "ward", "xa", "phuong"}:
        return "le_chan_local"
    if (
        ("hai phong" in issuer or "haiphong" in issuer)
        and kind == "SPECIFIC"
    ):
        return "hai_phong_override"
    if kind == "STANDARD":
        return "central"
    return None


def _iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def evaluate_candidate(
    candidate: dict[str, Any],
    *,
    legal_as_of: str,
) -> dict[str, Any]:
    """Return a fail-closed disposition for one discovered candidate."""

    base = {
        "candidate_id": candidate.get("candidate_id"),
        "approved": False,
        "runtime_eligible": False,
    }
    if (
        candidate.get("is_seed") is True
        or candidate.get("is_demo") is True
        or candidate.get("is_quarantined") is True
    ):
        return {
            **base,
            "disposition": "EXCLUDED",
            "reason_code": "SEED_OR_DEMO_BLOCKED",
        }
    component_kind = str(candidate.get("component_kind") or "")
    if component_kind == "official_result":
        return {
            **base,
            "disposition": "EXCLUDED",
            "reason_code": "OFFICIAL_RESULT_NOT_TEMPLATE",
        }
    if component_kind == "supporting_document":
        return {
            **base,
            "disposition": "EXCLUDED",
            "reason_code": "SUPPORTING_DOCUMENT_NOT_TEMPLATE",
        }
    state = str(candidate.get("portal_state") or "").upper()
    if state == "SUPERSEDED":
        return {
            **base,
            "disposition": "EXCLUDED",
            "reason_code": "FORM_SUPERSEDED",
        }
    if state in EXPIRED_PORTAL_STATES:
        return {
            **base,
            "disposition": "EXCLUDED",
            "reason_code": "FORM_EXPIRED",
        }
    effective_to = _iso_date(candidate.get("effective_to"))
    as_of = _iso_date(legal_as_of)
    if effective_to and as_of and effective_to < as_of:
        return {
            **base,
            "disposition": "EXCLUDED",
            "reason_code": "FORM_EXPIRED",
        }
    gap_reasons: list[str] = []
    if not str(candidate.get("procedure_id") or "").strip():
        gap_reasons.append("MISSING_PROCEDURE_ID")
    if not (
        str(candidate.get("official_download_url") or "").strip()
        and str(candidate.get("local_path") or "").strip()
        and re.fullmatch(r"[0-9a-fA-F]{64}", str(candidate.get("sha256") or ""))
    ):
        gap_reasons.append("MISSING_OFFICIAL_FILE")
    if not _iso_date(candidate.get("effective_from")):
        gap_reasons.append("MISSING_EFFECTIVITY")
    if not list(candidate.get("issuing_instruments") or []):
        gap_reasons.append("MISSING_ISSUING_INSTRUMENT")
    if not str(candidate.get("official_source_page") or "").strip():
        gap_reasons.append("MISSING_OFFICIAL_SOURCE_PAGE")
    if not list(candidate.get("provenance") or []):
        gap_reasons.append("MISSING_PROVENANCE")
    if str(candidate.get("source_tier") or "") not in SOURCE_TIERS:
        gap_reasons.append("UNVERIFIED_SOURCE_TIER")
    if gap_reasons:
        return {
            **base,
            "disposition": "VERIFIED_DATA_GAP",
            "reason_code": gap_reasons[0],
            "reason_codes": gap_reasons,
        }
    return {
        **base,
        "disposition": "READY_FOR_HUMAN_ATTESTATION",
        "reason_code": "ALL_AUTOMATED_HARD_GATES_PASSED",
    }


def _instrument_key(candidate: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                fold(value)
                for value in candidate.get("issuing_instruments") or []
                if fold(value)
            }
        )
    )


def _same_form(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_sha = str(left.get("sha256") or "").casefold()
    right_sha = str(right.get("sha256") or "").casefold()
    if left_sha and right_sha:
        if left_sha != right_sha:
            return False
        left_code = fold(left.get("form_code"))
        right_code = fold(right.get("form_code"))
        if left_code or right_code:
            return bool(left_code and left_code == right_code)
        return bool(
            fold(left.get("form_name"))
            and fold(left.get("form_name")) == fold(right.get("form_name"))
        )
    left_code = fold(left.get("form_code"))
    right_code = fold(right.get("form_code"))
    return bool(
        left_code
        and left_code == right_code
        and _instrument_key(left)
        and _instrument_key(left) == _instrument_key(right)
    )


def _public_mirror(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "source_tier": candidate.get("source_tier"),
        "official_source_page": candidate.get("official_source_page"),
        "official_download_url": candidate.get("official_download_url"),
        "sha256": candidate.get("sha256"),
    }


def canonicalize_form_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    legal_as_of: str,
) -> dict[str, Any]:
    """Gate and deduplicate candidates while retaining full audit evidence."""

    excluded: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = dict(raw)
        decision = evaluate_candidate(candidate, legal_as_of=legal_as_of)
        item = {**candidate, **decision}
        if decision["disposition"] == "EXCLUDED":
            excluded.append(item)
        elif decision["disposition"] == "VERIFIED_DATA_GAP":
            gaps.append(item)
        else:
            eligible.append(item)

    eligible.sort(
        key=lambda item: (
            SOURCE_TIER_PRIORITY.get(str(item.get("source_tier")), 99),
            fold(item.get("form_code")),
            fold(item.get("form_name")),
            str(item.get("candidate_id") or ""),
        )
    )
    canonical: list[dict[str, Any]] = []
    duplicates_collapsed = 0
    for candidate in eligible:
        duplicate = next(
            (existing for existing in canonical if _same_form(existing, candidate)),
            None,
        )
        if duplicate is None:
            canonical.append({**candidate, "source_mirrors": []})
            continue
        duplicate["source_mirrors"].append(_public_mirror(candidate))
        duplicates_collapsed += 1

    exclusion_counts = Counter(
        str(item.get("reason_code") or "UNKNOWN") for item in excluded
    )
    gap_counts = Counter(
        reason
        for item in gaps
        for reason in (
            item.get("reason_codes")
            or [str(item.get("reason_code") or "UNKNOWN")]
        )
    )
    tier_counts = Counter(str(item.get("source_tier") or "unknown") for item in canonical)
    domain_counts = Counter(str(item.get("domain") or "unknown") for item in canonical)
    summary = {
        "input_candidate_count": len(excluded) + len(gaps) + len(eligible),
        "canonical_count": len(canonical),
        "ready_for_human_attestation_count": sum(
            item.get("prior_approval_status") is not True for item in canonical
        ),
        "runtime_approved_count": sum(
            item.get("prior_approval_status") is True for item in canonical
        ),
        "excluded_count": len(excluded),
        "verified_data_gap_count": len(gaps),
        "duplicates_collapsed": duplicates_collapsed,
        "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        "gap_reason_counts": dict(sorted(gap_counts.items())),
        "canonical_by_source_tier": dict(sorted(tier_counts.items())),
        "canonical_by_domain": dict(sorted(domain_counts.items())),
        "auto_approved_count": 0,
    }
    return {
        "schema_version": 1,
        "legal_as_of": legal_as_of,
        "candidate_only": True,
        "canonical_forms": canonical,
        "review_shortlist": [
            item
            for item in canonical
            if item.get("prior_approval_status") is not True
        ],
        "excluded": excluded,
        "verified_data_gaps": gaps,
        "summary": summary,
    }


def build_privacy_safe_summary(
    inventory: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Create an aggregate release artifact without raw titles, URLs or files."""

    summary = dict(inventory.get("summary") or {})
    return {
        "schema_version": 1,
        "run_id": run_id,
        "legal_as_of": inventory.get("legal_as_of"),
        **summary,
        "candidate_only": True,
        "legal_review_required": True,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }
