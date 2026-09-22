"""Shared deterministic legal-domain normalization.

The public UI, imported corpus and Feature 017 catalog historically used
different slugs for the same commune-level domain.  This module is the single
translation boundary; callers retain the original value for audit and use the
canonical value for retrieval/authorization decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


CANONICAL_DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ho_tich_chung_thuc": (
        "ho_tich_chung_thuc",
        "ho_tich",
        "chung_thuc",
        "tu_phap_ho_tich",
        "civil_status",
    ),
    "dat_dai_xay_dung": (
        "dat_dai_xay_dung",
        "dat_dai",
        "xay_dung",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
        # UI/router labels historically included the full grouped display
        # name.  Keep those labels in the same canonical bucket as corpus
        # slugs such as ``dat_dai_moi_truong``.
        "dat_dai_xay_dung_moi_truong",
        "land",
    ),
    "an_sinh_y_te_giao_duc": (
        "an_sinh_y_te_giao_duc",
        "an_sinh",
        "an_sinh_y_te",
        "y_te",
        "giao_duc",
        "giao_duc_van_hoa",
        "lao_dong",
        "social_welfare",
        "health",
        "education",
        "van_hoa_giao_duc_y_te",
        "y_te_co_so",
    ),
    "cu_tru_an_ninh": (
        "cu_tru_an_ninh",
        "cu_tru",
        "an_ninh",
        "cu_tru_can_cuoc_an_ninh",
        "residence",
    ),
    "khieu_nai_to_cao_xu_phat": (
        "khieu_nai_to_cao_xu_phat",
        "khieu_nai",
        "to_cao",
        "xu_phat",
        "khieu_nai_to_cao_tiep_cong_dan_xu_phat",
        "complaint",
        "denunciation",
        "sanction",
    ),
    "hanh_chinh_cong": (
        "hanh_chinh_cong",
        "hanh_chinh",
        "noi_vu_hanh_chinh",
        "public_administration",
    ),
    # ``kinh_te`` is an active locality routing domain.  Older reviewed rows
    # used the more specific ``kinh_te_tai_chinh`` slug; treating it as an
    # unknown value made valid records disappear from the dashboard's
    # classification coverage and prevented the configured Kinh tế filter
    # from matching them.
    "kinh_te": (
        "kinh_te",
        "kinh_te_tai_chinh",
        "tai_chinh",
        "economy",
        "economic",
    ),
    "quoc_phong_quan_su": ("quoc_phong_quan_su", "quoc_phong", "nghia_vu_quan_su"),
    "trat_tu_do_thi": (
        "trat_tu_do_thi",
        "trat_tu",
        "urban_order",
    ),
}

_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in CANONICAL_DOMAIN_ALIASES.items()
    for alias in aliases
}


def _domain_slug(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


@dataclass(frozen=True)
class CanonicalDomainDecision:
    original_domain: str | None
    canonical_domain: str | None
    mapping_reason: str


def canonical_domain_decision(value: Any) -> CanonicalDomainDecision:
    original = str(value or "").strip() or None
    if original is None:
        return CanonicalDomainDecision(None, None, "missing")
    normalized = _domain_slug(original)
    canonical = _ALIAS_TO_CANONICAL.get(normalized, normalized or None)
    reason = "canonical" if canonical == normalized else "legacy_alias"
    return CanonicalDomainDecision(original, canonical, reason)


def canonicalize_legal_domain(value: Any) -> str | None:
    return canonical_domain_decision(value).canonical_domain


def legal_domain_values(value: Any) -> tuple[str, ...]:
    canonical = canonicalize_legal_domain(value)
    if not canonical:
        return ()
    return CANONICAL_DOMAIN_ALIASES.get(canonical, (canonical,))
