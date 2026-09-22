"""Issue-aware content relevance without changing legal authority metadata."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from api.legal_taxonomy import classify_topic, topics_allow_source, LegalTopic as _LegalTopic, TOPIC_DOMAIN_MAP as _TOPIC_DOMAIN_MAP


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.replace("đ", "d").split())


def _searchable(row: Mapping[str, Any]) -> str:
    return _fold(
        " ".join(
            str(row.get(field) or "")
            for field in (
                "clean_content",
                "content",
                "article_title",
                "chunk_heading",
                "document_title",
            )
        )
    )


def _normative_content(row: Mapping[str, Any]) -> str:
    """Return only legal body text, excluding document-title years/metadata."""

    for field in (
        "clean_matched_child_content",
        "matched_child_content",
        "clean_content",
        "content",
    ):
        value = str(row.get(field) or "").strip()
        if value:
            return _fold(value)
    return ""


def is_foreign_civil_reregistration_provision(row: Mapping[str, Any]) -> bool:
    """Articles 40–42 inherit the overseas/foreign scope of Article 40.

    Source: https://vbpl.vn/TW/Pages/vbpq-print.aspx?ItemID=92897
    This is an applicability filter, not an assertion of current effectivity.
    """
    law = _fold(row.get("law_number")).replace(" ", "")
    article = _fold(row.get("article_number"))
    match = re.fullmatch(r"(?:dieu\s*)?(40|41|42)[.]?", article)
    return law == "123/2015/nd-cp" and match is not None


def _temporal_mismatch(query: str, content: str) -> bool:
    query_years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", query)]
    if not query_years:
        return False
    # Context may contain both the statutory cutoff (2014) and an earlier user
    # event (2009). Use the earlier year as the factual event for applicability.
    event_year = min(query_years)
    before_years = [
        int(value)
        for value in re.findall(
            r"(?:truoc|den truoc)\s+(?:ngay\s+)?(?:\d{1,2}\s+thang\s+\d{1,2}\s+nam\s+)?((?:19|20)\d{2})",
            content,
        )
    ]
    after_years = [
        int(value)
        for value in re.findall(
            r"(?:ke tu|tu|sau)\s+(?:ngay\s+)?(?:\d{1,2}\s+thang\s+\d{1,2}\s+nam\s+)?((?:19|20)\d{2})",
            content,
        )
    ]
    # One matching temporal branch is enough to retain the child. A cutoff
    # before 2014 applies to a 2009 transaction; a cutoff before 1993 does not.
    before_matches = [event_year < cutoff for cutoff in before_years]
    after_matches = [event_year >= cutoff for cutoff in after_years]
    conditions = before_matches + after_matches
    return bool(conditions) and not any(conditions)


def _procedure_mismatch(query: str, content: str) -> bool:
    asks_first_issue = any(
        marker in query
        for marker in (
            "cap lan dau",
            "dang ky dat dai lan dau",
            "chua co so do",
            "chua co giay chung nhan",
        )
    ) or (
        "lan dau" in query
        and any(
            marker in query
            for marker in ("giay chung nhan", "dang ky dat dai", "so do")
        )
    )
    if not asks_first_issue:
        return False
    replacement_only = any(
        marker in content
        for marker in (
            "cap doi",
            "cap lai",
            "dinh chinh giay chung nhan",
            "dang ky bien dong",
            "xac nhan thay doi",
        )
    ) and not any(
        marker in content
        for marker in (
            "cap lan dau",
            "dang ky lan dau",
            "chua duoc cap giay chung nhan",
        )
    )
    return replacement_only


def _special_issue_mismatch(
    *,
    issue_intent: str = "unknown",
    relevance_topics: Sequence[str] = (),
    query: str = "",
    content: str = "",
) -> str | None:
    topics = {str(item).casefold() for item in relevance_topics}

    if "handwritten_paper" in topics:
        # This issue asks whether the earlier transfer transaction can be
        # handled, not merely who prints or issues a certificate.  Require the
        # legal body itself to address a transfer/transaction or the
        # incomplete transfer procedure.
        if not any(
            marker in content
            for marker in (
                "nhan chuyen quyen",
                "chuyen nhuong",
                "chuyen quyen su dung dat",
                "mua ban quyen su dung dat",
                "hop dong chuyen nhuong",
                "giao dich ve quyen su dung dat",
                "chua thuc hien thu tuc chuyen quyen",
            )
        ):
            return "missing_handwritten_transaction_support"

    if "land_boundary_dispute" in topics or issue_intent == "dispute":
        if not any(
            marker in content
            for marker in (
                "tranh chap",
                "ranh gioi",
                "hoa giai",
                "khieu kien",
                "don tranh chap",
            )
        ):
            return "missing_dispute_support"

    if "noncooperation" in topics:
        if not any(
            marker in content
            for marker in (
                "khong co tranh chap",
                "don tranh chap",
                "don de nghi giai quyet tranh chap",
                "hoa giai",
                "thu ly tranh chap",
                "phan doi quyen su dung dat",
                "khong hop tac",
                "tu choi ky",
            )
        ):
            return "missing_noncooperation_branch_support"

    if "deceased_transferor" in topics:
        if not any(
            marker in content
            for marker in (
                "ben chuyen quyen da chet",
                "nguoi chuyen quyen da chet",
                "nguoi ban da mat",
                "nguoi thua ke",
            )
        ):
            return "missing_deceased_transferor_support"

    if "planning" in topics:
        if not any(
            marker in content
            for marker in (
                "quy hoach",
                "ke hoach su dung dat",
                "thu hoi dat",
            )
        ):
            return "missing_planning_support"

    return None


def _authority_key(row: Mapping[str, Any]) -> tuple[int, int]:
    verified = 1 if str(row.get("authority_confidence") or "").casefold() == "verified" else 0
    try:
        rank = int(row.get("authority_rank"))
    except (TypeError, ValueError):
        rank = -1
    return verified, rank


def _issue_tokens(value: str) -> set[str]:
    stopwords = {
        "bao", "can", "cho", "cua", "duoc", "gi", "la", "lam", "nao",
        "nhung", "the", "thi", "toi", "trong", "va", "ve", "voi",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _fold(value))
        if len(token) >= 3 and token not in stopwords
    }


def _effectivity_key(row: Mapping[str, Any]) -> int:
    """Prefer current material inside the same legal-authority group."""

    status = _fold(
        " ".join(
            str(row.get(name) or "")
            for name in (
                "effectivity_status", "effective_status", "legal_status",
                "status", "validity_status",
            )
        )
    )
    if any(marker in status for marker in (
        "het hieu luc", "expired", "repealed", "bai bo", "superseded",
    )):
        return -1
    if any(marker in status for marker in (
        "con hieu luc", "dang hieu luc", "effective", "current", "active",
    )):
        return 1
    return 0


def _phrase_match_score(query: str, row: Mapping[str, Any]) -> int:
    """Score exact query phrases in article headings and normative text.

    This is a tie-breaker within authority/effectivity groups. It cannot make
    a lower-validity document outrank a superior legal source.
    """

    generic = {
        "anh", "chi", "toi", "mot", "nhung", "truong", "hop", "nao",
        "duoc", "phai", "khi", "thi", "the", "va", "hoac", "cho",
        "cua", "trong", "tren", "ve", "voi", "lam", "gi",
    }
    words = [
        token for token in re.findall(r"[a-z0-9]+", query)
        if len(token) >= 2 and token not in generic
    ]
    title = _fold(
        " ".join(
            str(row.get(name) or "")
            for name in ("article_title", "chunk_heading")
        )
    )
    body = _normative_content(row)
    score = 0
    seen: set[str] = set()
    for size in (5, 4, 3, 2):
        for offset in range(max(0, len(words) - size + 1)):
            phrase = " ".join(words[offset : offset + size])
            if phrase in seen:
                continue
            seen.add(phrase)
            if phrase in title:
                score += size * size * 4
            elif phrase in body:
                score += size * size
    title_tokens = set(re.findall(r"[a-z0-9]+", title))
    body_tokens = set(re.findall(r"[a-z0-9]+", body))
    distinctive = set(words)
    score += 3 * len(distinctive & title_tokens)
    score += len(distinctive & body_tokens)

    requested_article = re.search(r"\bdieu\s+(\d+[a-z]?)\b", query)
    row_article = re.search(r"(?:dieu\s*)?(\d+[a-z]?)", _fold(row.get("article_number")))
    if requested_article and row_article and requested_article.group(1) == row_article.group(1):
        score += 250
    return score


def rank_issue_evidence(
    issue_query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    issue_intent: str = "unknown",
    relevance_topics: Sequence[str] = (),
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """Reject explicit subject mismatches and rank only within authority groups.

    Authority confidence/rank remain the leading sort keys.  A content penalty
    therefore cannot promote a lower-authority instrument over a superior one.
    """

    query = _fold(issue_query)
    asks_community = any(
        marker in query
        for marker in ("cong dong dan cu", "dinh", "den", "mieu", "nha tho ho")
    )
    asks_house_right = any(
        marker in query
        for marker in ("quyen so huu nha o", "so huu nha o", "nha o")
    )
    asks_land_right = any(
        marker in query
        for marker in (
            "quyen su dung dat", "thua dat", "so do", "giay chung nhan",
            "dat dai", "mua dat", "chuyen nhuong dat",
        )
    )
    asks_individual = any(
        marker in query
        for marker in (
            "ca nhan", "ho gia dinh", "nguoi mua", "nguoi ban",
            "nguoi chuyen quyen", "nguoi su dung dat",
        )
    )
    query_tokens = _issue_tokens(issue_query)
    accepted: list[tuple[int, Mapping[str, Any], float, int, int]] = []
    decisions: list[dict[str, Any]] = []
    for index, row in enumerate(candidates):
        searchable = _searchable(row)
        normative_content = _normative_content(row)
        source_id = str(row.get("chunk_id") or row.get("source_id") or row.get("id") or index)
        reason = "accepted"
        decision = "keep"
        penalty = 0.0

        community_only = "cong dong dan cu" in searchable and any(
            marker in searchable for marker in ("dinh", "den", "mieu", "am", "nha tho ho")
        )
        house_only = (
            "nha o" in searchable
            and not any(
                marker in searchable
                for marker in ("quyen su dung dat", "thua dat", "dat dai", "dia chinh")
            )
        )
        organization_only = (
            asks_individual
            and any(
                marker in normative_content
                for marker in (
                    "to chuc trong nuoc",
                    "to chuc kinh te co von dau tu nuoc ngoai",
                    "doanh nghiep co von dau tu nuoc ngoai",
                )
            )
            and not any(
                marker in normative_content
                for marker in ("ca nhan", "ho gia dinh")
            )
        )
        investor_project_only = (
            asks_individual
            and any(
                marker in normative_content
                for marker in ("du an dau tu", "nha dau tu", "chu dau tu")
            )
            and not any(
                marker in normative_content
                for marker in ("ca nhan", "ho gia dinh")
            )
        )
        planning_certificate_effect_missing = (
            asks_land_right
            and "quy hoach" in query
            and any(
                marker in normative_content
                for marker in ("quy hoach", "ke hoach su dung dat", "thu hoi dat")
            )
            and not any(
                marker in normative_content
                for marker in (
                    "cap giay chung nhan",
                    "dang ky dat dai",
                    "quyen cua nguoi su dung dat",
                    "tiep tuc thuc hien quyen",
                )
            )
        )
        inheritance_transaction_mismatch = (
            any(
                marker in query
                for marker in ("mua dat", "chuyen nhuong", "nhan chuyen quyen")
            )
            and "nhan thua ke" in normative_content
            and not any(
                marker in normative_content
                for marker in ("chuyen nhuong", "mua ban", "nhan chuyen quyen")
            )
        )
        internal_cadastral_record_mismatch = (
            any(
                marker in query
                for marker in ("ho so", "chung cu", "giay to", "noi nop")
            )
            and "ho so dia chinh" in normative_content
            and any(
                marker in normative_content
                for marker in ("ban do dia chinh", "so muc ke", "so dia chinh")
            )
            and not any(
                marker in normative_content
                for marker in ("ho so nop", "nguoi su dung dat nop", "thanh phan ho so")
            )
        )
        internal_form_distribution_mismatch = (
            issue_intent == "documents"
            and any(
                marker in normative_content
                for marker in (
                    "in va cung cap to khai",
                    "cap phat cho nguoi su dung dat",
                    "cap phat day du to khai",
                    "co quan lam nhiem vu in",
                    "cung cap to khai cho cac co quan nhan ho so",
                    "huong dan nguoi su dung dat ke khai",
                )
            )
            and not any(
                marker in normative_content
                for marker in (
                    "thanh phan ho so",
                    "ho so gom",
                    "ho so bao gom",
                    "nguoi su dung dat nop",
                    "nguoi yeu cau nop",
                )
            )
        )
        internal_tax_transfer_mismatch = (
            issue_intent == "documents"
            and "co quan thue" in normative_content
            and any(
                marker in normative_content
                for marker in (
                    "chuyen giao cho co quan thue",
                    "giay to chuyen giao cho co quan thue",
                )
            )
            and not any(
                marker in normative_content
                for marker in (
                    "nguoi su dung dat nop",
                    "nguoi yeu cau nop",
                    "thanh phan ho so",
                    "ho so gom",
                    "ho so bao gom",
                )
            )
        )
        first_registration_document_support_missing = (
            issue_intent == "documents"
            and any(
                marker in query
                for marker in (
                    "cap giay chung nhan quyen su dung dat lan dau",
                    "cap giay chung nhan lan dau",
                    "dang ky dat dai lan dau",
                )
            )
            and not any(
                marker in normative_content
                for marker in (
                    "thanh phan ho so",
                    "ho so gom",
                    "ho so bao gom",
                    "ho so nop",
                    "nguoi su dung dat nop",
                    "nguoi yeu cau nop",
                    "don dang ky dat dai",
                    "nop tai",
                    "co quan tiep nhan",
                    "co chu ky cua cac ben lien quan",
                    "hop dong hoac van ban ve chuyen quyen",
                    "ho so",
                    "don dang ky",
                    "don de nghi",
                    "chung tu",
                    "xac nhan",
                    "so dia chinh",
                    "dang ky",
                    "cap giay chung nhan",
                )
            )
        )
        deadline_type_mismatch = (
            any(marker in query for marker in ("thoi han", "bao lau"))
            and "thoi han su dung dat" in normative_content
            and not any(
                marker in normative_content
                for marker in ("thoi han giai quyet", "ngay lam viec", "tiep nhan ho so")
            )
        )
        first_registration_deadline_missing = (
            any(marker in query for marker in ("thoi han", "bao lau"))
            and any(
                marker in query
                for marker in (
                    "cap giay chung nhan lan dau",
                    "cap lan dau",
                    "dang ky dat dai lan dau",
                    "chua co so do",
                    "chua co giay chung nhan",
                )
            )
            and not any(
                marker in normative_content
                for marker in (
                    "cap giay chung nhan",
                    "dang ky dat dai",
                    "dang ky lan dau",
                    "giay chung nhan quyen su dung dat",
                )
            )
        )
        land_price_uses = sum(
            marker in normative_content
            for marker in (
                "tinh tien thue dat",
                "tinh thue su dung dat",
                "tinh thue thu nhap",
                "tinh le phi",
                "tinh tien xu phat",
                "tinh gia khoi diem",
            )
        )
        finance_context_mismatch = (
            any(marker in query for marker in ("nghia vu tai chinh", "le phi", "chi phi"))
            and land_price_uses >= 4
            and not any(
                marker in normative_content
                for marker in ("cap giay chung nhan", "dang ky dat dai")
            )
        )
        agricultural_transfer_limit_mismatch = (
            any(
                marker in query
                for marker in (
                    "mua dat",
                    "giay viet tay",
                    "nhan chuyen quyen",
                    "cap giay chung nhan",
                    "chua co so do",
                )
            )
            and "dat nong nghiep" in normative_content
            and any(
                marker in normative_content
                for marker in (
                    "vuot han muc nhan chuyen quyen",
                    "han muc nhan chuyen quyen",
                )
            )
            and not any(
                marker in query
                for marker in (
                    "dat nong nghiep",
                    "han muc nhan chuyen quyen",
                    "vuot han muc",
                )
            )
        )
        state_allocation_origin_mismatch = (
            any(
                marker in query
                for marker in ("mua dat", "chuyen nhuong", "nhan chuyen quyen")
            )
            and any(
                marker in normative_content
                for marker in (
                    "nha nuoc giao dat",
                    "nha nuoc cho thue dat",
                    "chuyen muc dich su dung dat",
                    "trung dau gia quyen su dung dat",
                )
            )
            and not any(
                marker in normative_content
                for marker in ("chuyen nhuong", "mua ban", "nhan chuyen quyen")
            )
        )
        wildlife_field_mismatch = (
            asks_land_right
            and any(
                marker in normative_content
                for marker in ("mau vat", "loai hoang da", "danh muc loai")
            )
        )
        state_capital_field_mismatch = (
            asks_land_right
            and any(
                marker in normative_content
                for marker in ("phan von cua nha nuoc", "von nha nuoc", "von gop vao doanh nghiep")
            )
            and not any(
                marker in normative_content
                for marker in ("quyen su dung dat", "dang ky dat dai", "giay chung nhan")
            )
        )
        area_increase_case_mismatch = (
            any(
                marker in query
                for marker in (
                    "chua co so do", "chua co giay chung nhan", "cap lan dau",
                    "cap giay chung nhan lan dau", "dang ky dat dai lan dau",
                )
            )
            and any(
                marker in normative_content
                for marker in ("dien tich tang them", "thua dat goc da co giay chung nhan")
            )
        )
        natural_erosion_case_mismatch = (
            any(
                marker in query
                for marker in ("mua dat", "chuyen nhuong", "nhan chuyen quyen", "ho so")
            )
            and any(
                marker in normative_content
                for marker in ("sat lo tu nhien", "giam dien tich thua dat")
            )
        )
        planning_recovery_compensation_mismatch = (
            "quy hoach" in query
            and any(
                marker in normative_content
                for marker in (
                    "khi nha nuoc thu hoi dat", "boi thuong ve dat",
                    "ho tro khi nha nuoc thu hoi", "tai dinh cu",
                )
            )
            and not any(
                marker in normative_content
                for marker in ("quy hoach", "ke hoach su dung dat")
            )
        )
        certificate_display_document_mismatch = (
            any(marker in query for marker in ("ho so", "chung cu", "giay to", "noi nop"))
            and any(
                marker in normative_content
                for marker in (
                    "hinh thuc the hien thong tin",
                    "the hien thong tin cu the tren giay chung nhan",
                    "mau so 04/dk-gcn",
                )
            )
            and not any(
                marker in normative_content
                for marker in (
                    "thanh phan ho so", "nguoi su dung dat nop", "ho so nop",
                    "nop tai", "co quan tiep nhan",
                )
            )
        )
        complaint_stage_mismatch = (
            "khieu nai lan dau" in query
            and "khieu nai lan hai" in normative_content
            and "khieu nai lan dau" not in normative_content
        )
        time_limited_permit_mismatch = (
            "giay phep xay dung" in query
            and "co thoi han" not in query
            and "giay phep xay dung co thoi han" in normative_content
        )
        residence_renewal_mismatch = (
            "tam tru" in query
            and "dang ky" in query
            and "gia han" not in query
            and "gia han tam tru" in normative_content
        )
        minor_only_mismatch = (
            "chua thanh nien" in normative_content
            and not any(
                marker in query
                for marker in ("chua thanh nien", "tre em", "con toi", "con nho")
            )
            and not any(
                marker in normative_content
                for marker in (
                    "ho so dang ky tam tru",
                    "to khai thay doi thong tin cu tru",
                    "ca nhan, ho gia dinh",
                    "cong dan",
                )
            )
        )
        permit_revocation_mismatch = (
            "giay phep xay dung" in query
            and "thu hoi" not in query
            and "thu hoi giay phep" in normative_content
            and "da cap" in normative_content
        )
        reply_branch_deadline_mismatch = (
            "tinh trang hon nhan" in query
            and "van ban tra loi" in normative_content
            and "nhan duoc van ban tra loi" in normative_content
            and not any(
                marker in query
                for marker in (
                    "van ban tra loi", "thuong tru tai nhieu noi",
                    "cu tru tai nhieu noi", "xac minh noi cu tru",
                )
            )
        )
        residence_deletion_mismatch = (
            "dang ky tam tru" in query
            and "xoa dang ky" not in query
            and "xoa dang ky tam tru" in normative_content
        )
        residence_cancellation_mismatch = (
            "dang ky tam tru" in query
            and "huy bo" not in query
            and any(
                marker in normative_content
                for marker in ("huy bo viec dang ky", "quyet dinh huy bo")
            )
        )
        residence_database_definition_mismatch = (
            "dang ky tam tru" in query
            and "co so du lieu ve cu tru la" in normative_content
        )
        foreign_only_mismatch = (
            any(
                marker in normative_content
                for marker in ("cong dan nuoc ngoai", "nguoi khong quoc tich")
            )
            and not any(
                marker in query
                for marker in (
                    "cong dan nuoc ngoai", "nguoi nuoc ngoai",
                    "nguoi khong quoc tich", "yeu to nuoc ngoai",
                )
            )
            and not any(
                marker in normative_content
                for marker in ("cong dan viet nam", "nguoi viet nam")
            )
        )
        foreign_reregistration_mismatch = (
            is_foreign_civil_reregistration_provision(row)
            and "dang ky lai" in query
            and any(term in query for term in ("khai sinh", "ket hon", "khai tu"))
            and not any(term in query for term in ("nuoc ngoai", "viet kieu", "quoc tich"))
            and not re.search(r"\bdieu\s+(?:40|41|42)\b", query)
        )
        internal_consultation_mismatch = (
            any(
                marker in query
                for marker in ("giay phep xay dung", "cap phep xay dung")
            )
            and "co quan" in normative_content
            and "duoc hoi y kien" in normative_content
            and "tra loi bang van ban" in normative_content
            and not any(
                marker in query
                for marker in ("hoi y kien", "lay y kien", "phoi hop", "co quan chuyen mon")
            )
        )
        residence_temporary_absence_mismatch = (
            "dang ky tam tru" in query
            and any(
                marker in normative_content
                for marker in ("khai bao tam vang", "tam vang")
            )
            and not any(
                marker in query
                for marker in ("khai bao tam vang", "tam vang")
            )
        )
        residence_duration_deadline_mismatch = (
            issue_intent == "deadline"
            and "tam tru" in query
            and any(
                marker in query
                for marker in ("thoi han giai quyet", "bao lau", "may ngay")
            )
            and "thoi han tam tru toi da" in normative_content
            and not any(
                marker in normative_content
                for marker in ("ngay lam viec", "nhan ho so", "ho so day du")
            )
        )
        form_template_mismatch = (
            issue_intent != "form"
            and "cong hoa xa hoi chu nghia viet nam" in normative_content
            and any(marker in normative_content for marker in ("to khai", "don "))
            and "kinh gui" in normative_content
        )
        social_housing_permit_mismatch = (
            "giay phep xay dung" in query
            and "nha o rieng le" in query
            and "nha o xa hoi" not in query
            and "nha o xa hoi" in searchable
        )
        condition_dossier_mismatch = (
            issue_intent == "condition"
            and "ho so" in normative_content
            and any(
                marker in normative_content
                for marker in ("ho so de nghi", "ho so gom", "ho so bao gom")
            )
            and not any(
                marker in normative_content
                for marker in ("dieu kien cap", "dieu kien chung", "dap ung dieu kien")
            )
        )
        asks_identity = any(
            marker in query
            for marker in (
                "can cuoc",
                "the can cuoc",
                "can cuoc cong dan",
                "cccd",
                "dinh danh",
                "dinh danh dien tu",
                "vneid",
                "chung minh nhan dan",
                "cmnd",
                "so dinh danh",
            )
        )
        asks_residence = any(
            marker in query
            for marker in (
                "thuong tru",
                "tam tru",
                "cu tru",
                "noi cu tru",
                "luu tru",
                "tam vang",
                "ho khau",
                "so ho khau",
                "dang ky cu tru",
                "xoa dang ky",
                "thong tin cu tru",
            )
        )
        law_num_norm = str(row.get("law_number") or "").upper().replace("Đ", "D")
        is_residence_law = (
            "68/2020/QH14" in law_num_norm
            or "luat cu tru" in _fold(row.get("document_title") or "")
        )
        residence_only_content = (
            any(
                marker in normative_content
                for marker in (
                    "dang ky thuong tru",
                    "dang ky tam tru",
                    "thong bao luu tru",
                    "khai bao tam vang",
                    "xoa dang ky thuong tru",
                    "xoa dang ky tam tru",
                    "noi cu tru",
                )
            )
            and not any(
                marker in normative_content
                for marker in (
                    "can cuoc",
                    "the can cuoc",
                    "can cuoc cong dan",
                    "dinh danh",
                    "cccd",
                    "vneid",
                    "chung minh nhan dan",
                )
            )
        )
        residence_law_mismatch = (
            not asks_residence
            and "68/2020" not in query
            and "luat cu tru" not in query
            and (is_residence_law or (asks_identity and residence_only_content))
        )
        special_mismatch = _special_issue_mismatch(
            issue_intent=issue_intent,
            relevance_topics=relevance_topics,
            query=query,
            content=normative_content,
        )
        
        chunk_topics = set(t.value for t in classify_topic(searchable))
        query_topics = {
            topic
            for topic in relevance_topics
            if topic in _LegalTopic._value2member_map_
        }
        _src_domain = row.get("domain") or row.get("legal_domain")
        topic_mismatch = False
        if query_topics and _src_domain:
            _q_topic_objs = [_LegalTopic(t) for t in query_topics]
            if not topics_allow_source(_q_topic_objs, _src_domain):
                topic_mismatch = True

        if topic_mismatch:
            decision = "reject"
            reason = "taxonomy_topic_mismatch"
            penalty = 0.8
        elif community_only and not asks_community:
            decision = "reject"
            reason = "subject_mismatch_community"
        elif organization_only:
            decision = "reject"
            reason = "subject_mismatch_organization"
        elif investor_project_only:
            decision = "reject"
            reason = "subject_mismatch_investor_project"
        elif asks_land_right and house_only and not asks_house_right:
            decision = "reject"
            reason = "relation_mismatch_house_only"
        elif _temporal_mismatch(query, normative_content):
            decision = "reject"
            reason = "time_condition_mismatch"
        elif _procedure_mismatch(query, normative_content):
            decision = "reject"
            reason = "procedure_mismatch_first_vs_replacement"
        elif planning_certificate_effect_missing:
            decision = "reject"
            reason = "planning_not_certificate_effect"
        elif inheritance_transaction_mismatch:
            decision = "reject"
            reason = "transaction_type_mismatch_inheritance"
        elif internal_cadastral_record_mismatch:
            decision = "reject"
            reason = "document_actor_mismatch_internal_cadastral_record"
        elif internal_form_distribution_mismatch:
            decision = "reject"
            reason = "document_actor_mismatch_internal_form_distribution"
        elif internal_tax_transfer_mismatch:
            decision = "reject"
            reason = "document_actor_mismatch_internal_tax_transfer"
        elif first_registration_document_support_missing:
            decision = "reject"
            reason = "missing_first_registration_document_support"
        elif deadline_type_mismatch:
            decision = "reject"
            reason = "deadline_type_mismatch_land_tenure"
        elif first_registration_deadline_missing:
            decision = "reject"
            reason = "deadline_not_first_registration_specific"
        elif finance_context_mismatch:
            decision = "reject"
            reason = "finance_context_mismatch_land_price_uses"
        elif agricultural_transfer_limit_mismatch:
            decision = "reject"
            reason = "transaction_case_mismatch_agricultural_transfer_limit"
        elif state_allocation_origin_mismatch:
            decision = "reject"
            reason = "transaction_origin_mismatch_state_allocation"
        elif wildlife_field_mismatch:
            decision = "reject"
            reason = "field_mismatch_wildlife"
        elif state_capital_field_mismatch:
            decision = "reject"
            reason = "finance_field_mismatch_state_capital"
        elif area_increase_case_mismatch:
            decision = "reject"
            reason = "procedure_case_mismatch_area_increase"
        elif natural_erosion_case_mismatch:
            decision = "reject"
            reason = "procedure_case_mismatch_natural_erosion"
        elif planning_recovery_compensation_mismatch:
            decision = "reject"
            reason = "planning_case_mismatch_recovery_compensation"
        elif certificate_display_document_mismatch:
            decision = "reject"
            reason = "document_case_mismatch_certificate_display"
        elif complaint_stage_mismatch:
            decision = "reject"
            reason = "complaint_stage_mismatch_second_instance"
        elif time_limited_permit_mismatch:
            decision = "reject"
            reason = "permit_type_mismatch_time_limited"
        elif residence_renewal_mismatch:
            decision = "reject"
            reason = "residence_case_mismatch_renewal"
        elif minor_only_mismatch:
            decision = "reject"
            reason = "subject_mismatch_minor_only"
        elif permit_revocation_mismatch:
            decision = "reject"
            reason = "permit_case_mismatch_revocation"
        elif reply_branch_deadline_mismatch:
            decision = "reject"
            reason = "deadline_case_mismatch_reply_branch"
        elif residence_deletion_mismatch:
            decision = "reject"
            reason = "residence_case_mismatch_deletion"
        elif residence_cancellation_mismatch:
            decision = "reject"
            reason = "residence_case_mismatch_cancellation"
        elif residence_database_definition_mismatch:
            decision = "reject"
            reason = "residence_case_mismatch_database_definition"
        elif foreign_reregistration_mismatch:
            decision = "reject"
            reason = "subject_mismatch_foreign_reregistration"
        elif foreign_only_mismatch:
            decision = "reject"
            reason = "subject_mismatch_foreign_only"
        elif internal_consultation_mismatch:
            decision = "reject"
            reason = "procedure_actor_mismatch_internal_consultation"
        elif residence_temporary_absence_mismatch:
            decision = "reject"
            reason = "residence_case_mismatch_temporary_absence"
        elif residence_duration_deadline_mismatch:
            decision = "reject"
            reason = "deadline_type_mismatch_residence_duration"
        elif form_template_mismatch:
            decision = "reject"
            reason = "presentation_mismatch_form_template"
        elif social_housing_permit_mismatch:
            decision = "reject"
            reason = "permit_project_mismatch_social_housing"
        elif condition_dossier_mismatch:
            decision = "reject"
            reason = "facet_mismatch_dossier_for_condition"
        elif residence_law_mismatch:
            decision = "reject"
            reason = "residence_law_mismatch_without_residence_intent"
        elif special_mismatch:
            # Branch-specific questions require direct evidence.  Generic
            # same-domain passages remain useful for retrieval diagnostics but
            # must not turn a missing branch into an "Đã xác minh" answer.
            decision = "reject"
            reason = special_mismatch
        else:
            source_tokens = set(re.findall(r"[a-z0-9]+", searchable))
            overlap = len(query_tokens & source_tokens)
            if query_tokens and not overlap:
                penalty = 0.15
                reason = "indirect_subject_overlap"
            phrase_score = _phrase_match_score(query, row)
            effectivity = _effectivity_key(row)
            accepted.append((index, row, penalty, phrase_score, effectivity))

        decisions.append(
            {
                "source_id": source_id,
                "decision": decision,
                "reason": reason,
                "penalty": penalty,
                "phrase_match_score": (
                    _phrase_match_score(query, row) if decision == "keep" else 0
                ),
                "effectivity_rank": (
                    _effectivity_key(row) if decision == "keep" else 0
                ),
            }
        )

    ordered = sorted(
        accepted,
        key=lambda item: (
            -_authority_key(item[1])[0],
            -_authority_key(item[1])[1],
            -item[4],
            -item[3],
            -(float(item[1].get("score") or 0.0) - item[2]),
            item[0],
        ),
    )
    return [row for _, row, _, _, _ in ordered], decisions
