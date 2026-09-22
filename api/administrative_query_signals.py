"""Deterministic administrative subjects shared by routing and context bounds."""
from __future__ import annotations

import re
import unicodedata


def fold(text: str) -> str:
    value = "".join(
        c
        for c in unicodedata.normalize("NFD", text.casefold())
        if unicodedata.category(c) != "Mn"
    ).replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


_SUBJECTS: dict[str, tuple[str, ...]] = {
    "ho_tich_chung_thuc": (
        r"dang ky lai khai sinh", r"nhan cha(?:,?\s*me)?(?:,?\s*con)?",
        r"cai chinh ho tich", r"trich luc ho tich", r"giay chung sinh",
        r"tinh trang hon nhan", r"doi ten", r"khai sinh", r"khai tu", r"ho tich",
        r"ket hon", r"ly hon", r"chung thuc", r"quoc tich",
        r"hop phap hoa giay to",
    ),
    "dat_dai_xay_dung": (
        r"giay chung nhan quyen su dung dat", r"giay chung nhan qsd[dđ]?",
        r"cap lai giay chung nhan", r"mat giay chung nhan",
        r"dang ky bien dong", r"thua ke dat", r"tranh chap ranh gioi",
        r"xoa dang ky the chap", r"the chap tren so dat", r"so dat",
        r"nha o xa hoi", r"thue bao ve moi truong", r"phi bao ve moi truong",
        r"dang ky moi truong", r"giay phep xay dung", r"vi pham xay dung",
        r"xay lan chi gioi", r"lan chi gioi",
        r"quy dinh quan ly theo quy hoach", r"quy hoach do thi",
        r"quy hoach nong thon", r"an toan cong trinh thuy loi",
        r"cong trinh thuy loi", r"chuyen muc dich", r"tach thua",
        r"hop thua", r"lan ranh", r"ranh gioi", r"quyen su dung dat",
        r"dat dai", r"dat vuon", r"dat o", r"thua dat", r"so do",
        r"so hong", r"mien giay phep", r"thay mai", r"sua chua nha",
        r"ket cau chiu luc", r"nuoc thai", r"o nhiem", r"thuy loi",
    ),
    "an_sinh_y_te_giao_duc": (
        r"bao hiem y te", r"bao luc hoc duong", r"tai nan lao dong",
        r"tro cap huu tri xa hoi", r"tro cap xa hoi", r"bao tro xa hoi",
        r"no luong", r"tien luong", r"hoc phi", r"khong to chuc day hoc",
        r"khuyet tat", r"ho ngheo", r"tro cap", r"huu tri xa hoi",
        r"mai tang", r"lao dong", r"nhap hoc", r"chuyen truong",
        r"xac nhan dang hoc", r"hoc sinh", r"mam non", r"dai hoc",
        r"truong pho thong", r"truong cong lap", r"co so giao duc",
        r"noi tru", r"xa bien gioi", r"bien gioi dat lien",
        r"trach nhiem giai trinh", r"kham chua benh", r"tram y te",
        r"kham suc khoe", r"an toan thuc pham", r"bieu dien nghe thuat",
        r"cau lac bo van hoa", r"gia dinh van hoa", r"di tich",
        r"quang cao ngoai troi", r"nha van hoa",
        r"diem (?:cung cap dich vu )?tro choi dien tu cong cong", r"bhyt",
    ),
    "kinh_te": (
        r"giay chung nhan dang ky kinh doanh", r"dang ky kinh doanh",
        r"ho kinh doanh", r"hop tac xa", r"to hop tac",
    ),
    "cu_tru_an_ninh": (
        r"giay chung nhan du dieu kien ve an ninh trat tu",
        r"du dieu kien ve an ninh trat tu", r"an ninh trat tu",
        r"thong bao luu tru", r"khai bao luu tru", r"luu tru qua dem",
        r"chu so huu cho o", r"cho o hop phap", r"hop dong thue",
        r"van ban cho thue", r"xac nhan nhan than", r"tach ho",
        r"tach thanh ho(?: rieng)?",
        r"tam vang", r"tam tru", r"thuong tru", r"can cuoc", r"cccd",
        r"chu ho", r"tien an", r"tien su", r"mat giay to",
        r"ct\s*0?1", r"ct\s*0?2",
    ),
    "trat_tu_do_thi": (
        r"trat tu do thi", r"long duong", r"he pho", r"via he",
        r"vach son", r"do xe", r"cay xanh", r"hang rong", r"den duong",
    ),
    "quoc_phong_quan_su": (
        r"nghia vu quan su", r"giay goi kham", r"lenh kham", r"nhap ngu",
        r"ngach du bi", r"quan nhan du bi", r"dang ky phuc vu",
        r"quoc phong", r"liet si",
    ),
    "khieu_nai_to_cao_xu_phat": (
        r"bao ve nguoi to cao", r"tam dinh chi thi hanh",
        r"doi thoai khieu nai", r"rut khieu nai", r"uy quyen khieu nai",
        r"phan anh kien nghi", r"thai do phuc vu", r"bao luc gia dinh",
        r"kiem soat tai san", r"tiep cong dan", r"tiep dan", r"khieu nai",
        r"to cao", r"nop don sai co quan", r"co quan nhan don",
    ),
    "hanh_chinh_cong": (
        r"thu tuc hanh chinh", r"bo phan mot cua", r"dich vu cong",
        r"ho so truc tuyen", r"giai quyet ho so", r"van ban den",
        r"van ban di", r"dau treo", r"dau giap lai", r"xu ly van ban",
        r"nop ho so bang van ban", r"ho so luu tru", r"sao luc ho so",
        r"thoi han bao quan", r"mot cua",
    ),
}
_PATTERNS = {
    domain: tuple(re.compile(r"(?<!\w)(?:" + term + r")(?!\w)") for term in terms)
    for domain, terms in _SUBJECTS.items()
}

# These multi-word anchors describe the requested legal object, so they must
# beat incidental facts such as ``đã kết hôn`` in a social-housing question.
_STRONG_ANCHORS: dict[str, tuple[str, ...]] = {
    "ho_tich_chung_thuc": (
        "nhan cha me con", "cai chinh ho tich", "trich luc ho tich",
        "hop phap hoa giay to", "quoc tich",
    ),
    "dat_dai_xay_dung": (
        "nha o xa hoi", "giay chung nhan quyen su dung dat",
        "dang ky bien dong", "thue bao ve moi truong",
        "phi bao ve moi truong", "dang ky moi truong",
    ),
    "an_sinh_y_te_giao_duc": (
        "bao hiem y te", "hoc phi", "tai nan lao dong",
        "bao luc hoc duong", "tro cap huu tri xa hoi",
    ),
    "cu_tru_an_ninh": (
        "giay chung nhan du dieu kien ve an ninh trat tu",
        "an ninh trat tu",
    ),
    "khieu_nai_to_cao_xu_phat": (
        "bao ve nguoi to cao", "tam dinh chi thi hanh",
        "doi thoai khieu nai", "thai do phuc vu", "bao luc gia dinh",
        "kiem soat tai san",
    ),
}


def administrative_domain_scores(question: str) -> dict[str, int]:
    """Score reviewed administrative phrases without inventing a domain.

    Longer action/object phrases are deliberately stronger than generic nouns.
    The returned map is useful for diagnostics; callers should normally use
    :func:`administrative_domain`.
    """

    text = fold(question)
    scores: dict[str, int] = {}
    for domain, patterns in _PATTERNS.items():
        score = 0
        seen: set[tuple[int, int]] = set()
        for pattern in patterns:
            for match in pattern.finditer(text):
                span = match.span()
                if span in seen:
                    continue
                seen.add(span)
                words = max(1, len(re.findall(r"[a-z0-9]+", match.group(0))))
                score += words * words
        score += 25 * sum(anchor in text for anchor in _STRONG_ANCHORS.get(domain, ()))
        if score:
            scores[domain] = score
    return scores


def administrative_domain(question: str) -> str | None:
    scores = administrative_domain_scores(question)
    if not scores:
        return None
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return None
    return ordered[0][0]


def has_administrative_subject(question: str) -> bool:
    return bool(administrative_domain_scores(question))


def has_administrative_request(question: str) -> bool:
    text = fold(question)
    if not has_administrative_subject(question):
        return False
    # A subject alone is not a request for law (e.g. a poem about trees).
    if re.search(r"\b(?:viet|ke|sang tac)\b.*\b(?:tho|truyen|bai hat)\b", text):
        return False
    return bool(re.search(
        r"\b(?:thu tuc|ho so|giay to|dang ky|xin|cap phep|xac nhan|"
        r"quy dinh|dieu kien|quyen|nghia vu|chinh sach|xu ly|phan anh|"
        r"bao cho|co quan|don vi|trinh bao|dong y|hoa giai|khieu nai|"
        r"to cao|tra cuu|thuc hien|can|phai|duoc|nop|the nao|ra sao|"
        r"truong hop nao|o dau|bieu mau|mau phieu|ap dung|muc thu|"
        r"xac dinh|cap doi|cap lai|mua|thue mua|dich|hop phap hoa)\b", text
    ))


def contextual_form_question(question: str, history) -> str:
    """Anchor only elliptical form requests to the nearest user subject.

    Never infer a procedure from an assistant answer or from a bare form code.
    Stop at a newer substantive topic instead of reaching across it.
    """
    text = fold(question).strip().rstrip(".!? ")

    def recent_messages(max_user_turns: int = 5):
        """Keep metadata from at most the newest five user turns.

        Assistant form/procedure fields are backend-owned snapshots written by
        the serving path.  They are safer identity anchors than assistant prose
        and let a later field/version question retain the released form even
        when the original procedure wording has left the user-only window.
        """

        selected = []
        user_turns = 0
        for message in reversed(list(history or [])):
            role = str(
                message.get("role") or message.get("sender_role") or ""
            ).casefold()
            if role in {"user", "human"}:
                user_turns += 1
                if user_turns > max_user_turns:
                    break
            selected.append(message)
        return list(reversed(selected))

    def released_metadata_anchor() -> str | None:
        wanted_codes: set[str] = set()
        for match in re.finditer(r"\bct\s*0?(\d{1,3})\b", text):
            wanted_codes.add(f"CT{int(match.group(1)):02d}")
        for match in re.finditer(r"\bmau\s+(?:so\s+)?(\d{1,3})\b", text):
            wanted_codes.add(str(int(match.group(1))).zfill(2))
        generic_followup = bool(
            re.search(r"\b(?:mau|bieu mau|to khai|file mau|tren mau)\b", text)
            and re.search(
                r"\b(?:nay|do|vua neu|phien ban|hien hanh|hieu luc|"
                r"huong dan|kiem tra|tung truong|dien|noi dung bat buoc|"
                r"ho ten|ngay sinh|noi cu tru|chu ky|ky)\b",
                text,
            )
        )
        if not wanted_codes and not generic_followup:
            return None
        for message in reversed(recent_messages()):
            if str(
                message.get("role") or message.get("sender_role") or ""
            ).casefold() != "assistant":
                continue
            detail = message.get("procedure_detail")
            detail = detail if isinstance(detail, dict) else {}
            forms = [
                item
                for item in (
                    list(message.get("recommended_forms") or [])
                    + list(detail.get("recommended_forms") or [])
                    + list(detail.get("forms") or [])
                )
                if isinstance(item, dict)
            ]
            matching = forms
            if wanted_codes:
                matching = [
                    item
                    for item in forms
                    if str(item.get("form_code") or "")
                    .replace(" ", "")
                    .upper()
                    in wanted_codes
                ]
            if not matching:
                continue
            procedure_name = str(
                next(
                    (
                        item.get("procedure_name")
                        for item in matching
                        if item.get("procedure_name")
                    ),
                    None,
                )
                or detail.get("name")
                or ""
            ).strip()
            if procedure_name:
                return procedure_name
        return None

    def is_form_reference(value: str) -> bool:
        previous_text = fold(value)
        return bool(
            re.search(
                r"\b(?:mau|bieu mau|to khai|ct\s*0?1|mau don|mau\s+so\s+\d+)\b",
                previous_text,
            )
        )

    def has_strong_procedure_anchor(value: str) -> bool:
        previous_text = fold(value)
        return bool(re.search(
            r"\b(?:dang ky (?:khai sinh|tam tru|thuong tru)|"
            r"tro cap huu tri xa hoi|chuyen muc dich su dung dat|"
            r"xin chuyen muc dich|xoa dang ky|gia han tam tru|"
            r"dang ky bien dong)\b",
            previous_text,
        ))
    # The published birth-registration form is bound to procedure 1.001193.
    # Citizens often omit the words "đăng ký" in a follow-up ("mẫu tờ khai
    # khai sinh ký thế nào?").  Add the reviewed procedure phrase for identity
    # matching only; the form catalog still decides whether an asset is
    # released and eligible.
    if re.search(r"\b(?:mau\s+)?to\s+khai\s+khai\s+sinh\b", text) and "dang ky lai" not in text:
        return f"Đang làm thủ tục đăng ký khai sinh. Yêu cầu tiếp theo: {question}"

    # A request that already names its procedure is self-contained.  Do not
    # attach a previous topic merely because the phrase "đăng ký" contains the
    # short field word "ký" used by the form-follow-up detector below.
    if has_strong_procedure_anchor(question):
        return question

    metadata_anchor = released_metadata_anchor()
    if metadata_anchor:
        return f"Đang làm thủ tục {metadata_anchor}. Yêu cầu tiếp theo: {question}"

    # A code or generic form noun with a procedural verb is an elliptical
    # continuation even when it has more words than the compact examples
    # below (for example: "Mẫu CT01 dùng để làm gì và ai phải ký?").  A form
    # field such as "chủ hộ" or "căn cước" is not, by itself, a new procedure
    # topic.  Pronouns/version/filling language therefore keeps the nearest
    # explicit user procedure as the identity anchor.
    form_marker = re.search(
        r"\b(?:mau|bieu mau|to khai|ct\s*0?1|mau don|mau\s+so\s+\d+)\b",
        text,
    )
    explicit_form_followup = bool(
        form_marker
        and (
            re.search(r"\b(?:nay|do|vua neu|vua noi|tren mau|tren to khai)\b", text)
            or re.search(r"\b(?:phien ban|con hieu luc|hieu luc tu|huong dan|dien tung|kiem tra tung|dong y|chu ky|ky|chu ho|chu so huu)\b", text)
        )
    )
    if (
        form_marker
        and (explicit_form_followup or not has_administrative_subject(question))
    ):
        users = [str(m.get("content") or "").strip() for m in recent_messages()
                 if str(m.get("role") or m.get("sender_role") or "") == "user"][-5:]
        for previous in reversed(users):
            if previous == question.strip():
                continue
            if is_form_reference(previous) and not has_strong_procedure_anchor(previous):
                continue
            if has_administrative_subject(previous):
                return f"{previous}\nYêu cầu tiếp theo: {question}"
            if len(previous.split()) > 8:
                break

    if not re.fullmatch(
        r"(?:(?:cho toi|gui toi|gui|cho minh|xin|tai|can|con|vay|the)\s+)?"
        r"(?:mau(?:\s+ct\s*0?1)?|bieu mau|to khai|giay to|ho so)"
        r"(?:\s+(?:nay|do|nao|gi|di|voi|kem theo|can chuan bi|gom nhung gi))*", text
    ):
        return question
    users = [str(m.get("content") or "").strip() for m in recent_messages()
             if str(m.get("role") or m.get("sender_role") or "") == "user"][-5:]
    for previous in reversed(users):
        if previous == question.strip():
            continue
        if is_form_reference(previous) and not has_strong_procedure_anchor(previous):
            continue
        if has_administrative_subject(previous):
            return f"{previous}\nYêu cầu tiếp theo: {question}"
        # A new substantive question without a recognized subject is a boundary.
        if len(previous.split()) > 8:
            break
    return question
