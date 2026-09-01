#!/usr/bin/env python3
"""Generate >=1000 labeled router cases for ChatBotLegal Phase B.

Splits: train / dev / holdout (private). Holdout is eval-only; never train on it.
Schema matches tests/fixtures/router_dataset_v1/*.jsonl
"""
from __future__ import annotations

import json
import random
from pathlib import Path

RNG = random.Random(20260901)
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "router_dataset_v1"
if __name__ != "__main__":
    pass

LAWS = [
    ("60/2014/QH13", "ho_tich_chung_thuc", "luật hộ tịch"),
    ("31/2024/QH15", "dat_dai_xay_dung", "luật đất đai"),
    ("101/2024/NĐ-CP", "ho_tich_chung_thuc", "nghị định hộ tịch"),
    ("123/2015/NĐ-CP", "ho_tich_chung_thuc", "nghị định hộ tịch"),
    ("154/2024/NĐ-CP", "an_sinh_y_te_giao_duc", "nghị định bảo hiểm xã hội"),
    ("20/2021/NĐ-CP", "dat_dai_xay_dung", "nghị định xây dựng"),
    ("176/2025/NĐ-CP", "ho_tich_chung_thuc", "nghị định hộ tịch"),
]

PROCEDURES = [
    ("khai sinh", "ho_tich_chung_thuc", "direct_procedure_lookup"),
    ("đăng ký kết hôn", "ho_tich_chung_thuc", "direct_procedure_lookup"),
    ("khai tử", "ho_tich_chung_thuc", "direct_procedure_lookup"),
    ("chứng thực chữ ký", "ho_tich_chung_thuc", "direct_procedure_lookup"),
    ("sang tên sổ đỏ", "dat_dai_xay_dung", "direct_procedure_lookup"),
    ("cấp giấy phép xây dựng nhà ở", "dat_dai_xay_dung", "direct_procedure_lookup"),
    ("tách thửa đất ở", "dat_dai_xay_dung", "direct_procedure_lookup"),
    ("cấp thẻ BHYT", "an_sinh_y_te_giao_duc", "direct_procedure_lookup"),
    ("hưởng trợ cấp thất nghiệp", "an_sinh_y_te_giao_duc", "direct_procedure_lookup"),
    ("đăng ký tạm trú", "ho_tich_chung_thuc", "direct_procedure_lookup"),
]

ARTICLES = [
    ("điều 8 luật hộ tịch", "ho_tich_chung_thuc"),
    ("điều 17 luật đất đai 2024", "dat_dai_xay_dung"),
    ("điều 21 nghị định 101/2024/NĐ-CP", "ho_tich_chung_thuc"),
    ("khoản 2 điều 9 luật cư trú", "ho_tich_chung_thuc"),
    ("điều 15 nghị định 154/2024/NĐ-CP", "an_sinh_y_te_giao_duc"),
]

GREETINGS = [
    "xin chào", "chào bạn", "hello", "hi", "chào bot", "alo",
    "chào buổi sáng", "good morning", "hey", "chào anh",
    "chào chị", "bạn khỏe không", "hello bạn", "chào legal bot",
]

META = [
    "bạn là ai", "bạn làm được gì", "hướng dẫn sử dụng",
    "trợ giúp", "help", "bạn trả lời dựa trên nguồn nào",
    "làm sao để hỏi về thủ tục", "cách dùng chatbot",
    "bạn có phải luật sư không", "giới thiệu về bạn",
    "bạn hỗ trợ những lĩnh vực nào", "có thể hỏi tiếng Anh không",
    "làm sao đính kèm file", "bạn lưu lịch sử chat không",
    "chính sách bảo mật của bot", "phiên bản hệ thống là gì",
]

OOS = [
    "kể chuyện cười đi", "thời tiết hải phòng hôm nay",
    "giá vàng hôm nay", "công thức phở bò", "tỉ số bóng đá",
    "viết thơ tặng crush", "dịch sang tiếng nhật giúp",
    "code python hello world", "bài hát hay nhất 2026",
    "cách nấu cơm", "so sánh iphone và samsung",
    "tin tức giải trí", "xem tử vi tuổi thìn",
    "đặt vé máy bay", "cách giảm cân",
    "bitcoin tăng hay giảm", "kể chuyện ma",
    "dịch vụ spa gần đây", "làm bài tập toán lớp 5",
]

FOLLOWUP_Q = [
    "còn hạn nộp nữa không", "nộp ở đâu", "cần giấy tờ gì",
    "lệ phí bao nhiêu", "mất bao lâu", "ai được ký",
    "có cần công chứng không", "nộp online được không",
    "nếu thiếu giấy thì sao", "áp dụng từ khi nào",
]

THANKS = [
    "cảm ơn", "cảm ơn nhiều nhé", "thanks", "cám ơn bạn",
    "ok cảm ơn", "tuyệt, cảm ơn",
]

DOCS = [
    {"id": "law:60/2014/QH13", "title": "Luật hộ tịch 2014"},
    {"id": "law:31/2024/QH15", "title": "Luật đất đai 2024"},
    {"id": "nd:101/2024/NĐ-CP", "title": "NĐ 101/2024/NĐ-CP"},
    {"id": "proc:khai-sinh", "title": "Thủ tục khai sinh"},
    {"id": "proc:sang-ten", "title": "Sang tên sổ đỏ"},
]


def case(cid, split, role, question, history, active_document,
         route, legal_route, domain, tags, needs_llm=False):
    return {
        "id": cid,
        "split": split,
        "role": role,
        "question": question,
        "history": history or [],
        "active_document": active_document,
        "expected_conversation_route": route,
        "expected_legal_route": legal_route,
        "expected_domain": domain,
        "tags": tags,
        "needs_llm": bool(needs_llm),
    }


def typo(s: str) -> str:
    """Mild Vietnamese typo: drop diacritic on one word or swap a letter."""
    repl = {
        "khai": "khia",
        "sinh": "sin",
        "kết": "ket",
        "hôn": "hon",
        "đất": "dat",
        "đai": "dai",
        "thủ": "thu",
        "tục": "tuc",
        "giấy": "giay",
        "phép": "phep",
        "cư": "cu",
        "trú": "tru",
        "bảo": "bao",
        "hiểm": "hiem",
        "luật": "luat",
        "điều": "dieu",
        "nghị": "nghi",
        "định": "dinh",
    }
    words = s.split()
    for i, w in enumerate(words):
        low = w.casefold()
        for src, dst in repl.items():
            if src in low:
                words[i] = w.replace(src, dst).replace(src.capitalize(), dst)
                return " ".join(words)
    if len(s) > 8:
        i = RNG.randint(3, min(len(s) - 2, 20))
        return s[:i] + s[i + 1 :]
    return s + " ah"


def abbrev(proc: str) -> str:
    table = {
        "khai sinh": "KS",
        "đăng ký kết hôn": "ĐKKH",
        "khai tử": "KT",
        "chứng thực chữ ký": "CTCK",
        "sang tên sổ đỏ": "sang ten SD",
        "cấp giấy phép xây dựng nhà ở": "GPXD",
        "tách thửa đất ở": "tach thua",
        "cấp thẻ BHYT": "BHYT",
        "hưởng trợ cấp thất nghiệp": "TCTN",
        "đăng ký tạm trú": "DKTT",
    }
    return table.get(proc, proc)


def hist(domain_label: str, q: str) -> list[dict]:
    return [
        {"role": "user", "content": q},
        {
            "role": "assistant",
            "content": f"Theo quy định thuộc lĩnh vực {domain_label}, người dân cần nộp hồ sơ tại bộ phận một cửa.",
        },
    ]


def split_for_question(question: str) -> str:
    """Same question text always lands in one split (no train/holdout leak)."""
    import hashlib
    digest = hashlib.md5(question.casefold().strip().encode("utf-8")).hexdigest()
    h = int(digest[:8], 16) % 100
    if h < 15:
        return "holdout"
    if h < 30:
        return "dev"
    return "train"


def main() -> None:
    rows: list[dict] = []
    n = 0

    def add(**kw):
        nonlocal n
        n += 1
        rows.append(case(cid=f"r-{n:04d}", split="train", **kw))

    # --- greetings / thanks -> chat_meta
    for q in GREETINGS + THANKS:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=q,
                history=[],
                active_document=None,
                route="chat_meta",
                legal_route=None,
                domain=None,
                tags=["meta", "exact", role],
            )

    # --- meta questions
    for q in META:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=q,
                history=[],
                active_document=None,
                route="chat_meta",
                legal_route=None,
                domain=None,
                tags=["meta", "exact", role],
            )

    # --- out of scope
    for q in OOS:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=q,
                history=[],
                active_document=None,
                route="out_of_scope",
                legal_route=None,
                domain=None,
                tags=["oos", "exact", role],
            )

    # --- exact law numbers -> legal_query
    for law, domain, label in LAWS:
        for role in ("citizen", "officer"):
            for tmpl in (
                "Nội dung {law} là gì",
                "{law} quy định những gì",
                "Giải thích {law}",
                "Phạm vi điều chỉnh của {law}",
                "Tóm tắt {label} {law}",
                "Điều khoản chính trong {law}",
            ):
                add(
                    role=role,
                    question=tmpl.format(law=law, label=label),
                    history=[],
                    active_document=None,
                    route="legal_query",
                    legal_route="direct_document_lookup",
                    domain=domain,
                    tags=["exact", "abbrev", role],
                )

    # --- procedures
    for proc, domain, lroute in PROCEDURES:
        for role in ("citizen", "officer"):
            for tmpl in (
                "Thủ tục {proc} gồm những gì",
                "Hồ sơ {proc} cần giấy tờ nào",
                "Lệ phí {proc} bao nhiêu",
                "Nộp hồ sơ {proc} ở đâu tại Hải Phòng",
                "Thời hạn giải quyết {proc}",
                "Ai được làm {proc}",
            ):
                add(
                    role=role,
                    question=tmpl.format(proc=proc),
                    history=[],
                    active_document=None,
                    route="legal_query",
                    legal_route=lroute,
                    domain=domain,
                    tags=["exact", role],
                )

    # --- articles
    for art, domain in ARTICLES:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=f"{art} nói gì",
                history=[],
                active_document=None,
                route="legal_query",
                legal_route="direct_article_lookup",
                domain=domain,
                tags=["exact", role],
            )
            add(
                role=role,
                question=f"Phân tích {art} cho công dân",
                history=[],
                active_document=None,
                route="legal_query",
                legal_route="direct_article_lookup",
                domain=domain,
                tags=["exact", role],
            )

    # --- typos of procedures (still legal_query)
    for proc, domain, lroute in PROCEDURES:
        for role in ("citizen", "officer"):
            q = typo(f"thủ tục {proc} cần giấy tờ gì")
            add(
                role=role,
                question=q,
                history=[],
                active_document=None,
                route="legal_query",
                legal_route=lroute,
                domain=domain,
                tags=["typo", role],
                needs_llm=True,
            )

    # --- abbreviations
    for proc, domain, lroute in PROCEDURES:
        for role in ("citizen", "officer"):
            ab = abbrev(proc)
            add(
                role=role,
                question=f"hướng dẫn {ab} tại hải phòng",
                history=[],
                active_document=None,
                route="legal_query",
                legal_route=lroute,
                domain=domain,
                tags=["abbrev", role],
                needs_llm=True,
            )

    # --- short follow-ups with active document -> document_followup
    for doc in DOCS:
        for q in FOLLOWUP_Q:
            for role in ("citizen", "officer"):
                add(
                    role=role,
                    question=q,
                    history=hist("pháp luật", "nội dung văn bản vừa xem"),
                    active_document=doc,
                    route="document_followup",
                    legal_route=None,
                    domain=None,
                    tags=["followup", role],
                )

    # --- short follow-up WITHOUT active doc: still legal if history is legal
    for proc, domain, lroute in PROCEDURES[:6]:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question="còn lệ phí nữa không",
                history=hist(domain, f"thủ tục {proc} gồm gì"),
                active_document=None,
                route="legal_query",
                legal_route=lroute,
                domain=domain,
                tags=["followup", "stale_ok", role],
                needs_llm=True,
            )

    # --- stale memory: history other domain, question is clearly another domain
    stale_pairs = [
        (
            "khai sinh cần giấy tờ gì",
            "ho_tich_chung_thuc",
            "sang tên sổ đỏ mất bao lâu",
            "dat_dai_xay_dung",
        ),
        (
            "thủ tục kết hôn",
            "ho_tich_chung_thuc",
            "cấp giấy phép xây dựng nhà ở",
            "dat_dai_xay_dung",
        ),
        (
            "luật đất đai 31/2024/QH15",
            "dat_dai_xay_dung",
            "điều kiện hưởng BHYT",
            "an_sinh_y_te_giao_duc",
        ),
        (
            "trợ cấp thất nghiệp",
            "an_sinh_y_te_giao_duc",
            "đăng ký tạm trú online",
            "ho_tich_chung_thuc",
        ),
        (
            "tách thửa đất ở",
            "dat_dai_xay_dung",
            "chứng thực chữ ký ở UBND",
            "ho_tich_chung_thuc",
        ),
    ]
    for prev_q, prev_d, new_q, new_d in stale_pairs:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=new_q,
                history=hist(prev_d, prev_q),
                active_document=None,
                route="legal_query",
                legal_route="direct_procedure_lookup",
                domain=new_d,
                tags=["stale_memory", role],
            )
            # officer with account domain = previous domain MUST still follow question
            add(
                role="officer",
                question=new_q,
                history=hist(prev_d, prev_q),
                active_document=None,
                route="legal_query",
                legal_route="direct_procedure_lookup",
                domain=new_d,
                tags=["stale_memory", "officer", "acl_must_not_overwrite"],
            )

    # --- multi-domain: clarification still legal_query (not meta)
    multi = [
        "khai sinh và sang tên đất làm cùng lúc được không",
        "kết hôn rồi có phải đăng ký tạm trú lại không",
        "GPXD và tách thửa khác nhau thế nào",
        "BHYT và trợ cấp thất nghiệp liên quan ra sao",
        "chứng thực chữ ký rồi dùng để sang tên sổ đỏ được không",
        "luật hộ tịch và luật đất đai cái nào áp dụng cho nhà ở",
        "tạm trú hết hạn thì khai sinh cho con được không",
        "công chứng hợp đồng chuyển nhượng đất cần giấy tờ hộ tịch gì",
    ]
    for q in multi:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=q,
                history=[],
                active_document=None,
                route="legal_query",
                legal_route="clarification",
                domain=None,
                tags=["multi_domain", role],
                needs_llm=True,
            )

    # --- officer vs citizen same legal question (route identical; ACL later)
    same_qs = [
        ("Người dân mất giấy khai sinh thì làm lại thế nào", "ho_tich_chung_thuc"),
        ("Công chức tiếp nhận hồ sơ kết hôn thiếu giấy tờ thì xử lý ra sao", "ho_tich_chung_thuc"),
        ("Thẩm quyền cấp GPXD nhà ở riêng lẻ tại Hải Phòng", "dat_dai_xay_dung"),
        ("Hồ sơ hưởng chế độ ốm đau BHXH", "an_sinh_y_te_giao_duc"),
        ("Căn cứ từ chối đăng ký thường trú", "ho_tich_chung_thuc"),
    ]
    for q, domain in same_qs:
        for role in ("citizen", "officer"):
            add(
                role=role,
                question=q,
                history=[],
                active_document=None,
                route="legal_query",
                legal_route="legal_rag",
                domain=domain,
                tags=["exact", role, "same_question"],
            )

    # --- greetings with legal history still meta (reset)
    for q in ("xin chào", "cảm ơn nhiều nhé"):
        add(
            role="citizen",
            question=q,
            history=hist("ho_tich_chung_thuc", "thủ tục khai sinh"),
            active_document=None,
            route="chat_meta",
            legal_route=None,
            domain=None,
            tags=["meta", "followup", "citizen"],
        )

    # --- OOS with legal history still OOS
    for q in ("kể chuyện cười đi", "thời tiết hải phòng hôm nay"):
        add(
            role="citizen",
            question=q,
            history=hist("dat_dai_xay_dung", "sang tên sổ đỏ"),
            active_document=None,
            route="out_of_scope",
            legal_route=None,
            domain=None,
            tags=["oos", "stale_memory", "citizen"],
        )

    # --- extra legal paraphrases to reach 1000+
    paraphrases = [
        "muốn làm giấy khai sinh cho con thì bắt đầu từ đâu",
        "vợ chồng muốn đăng ký kết hôn tại hải phòng",
        "nhà tôi muốn chuyển mục đích sử dụng đất",
        "xây nhà 2 tầng trên đất ở có cần phép không",
        "làm thế nào để hưởng bảo hiểm thất nghiệp sau khi nghỉ việc",
        "con tôi đi học cần thẻ bhyt thế nào",
        "mất cmnd thì đăng ký thường trú được không",
        "ủy quyền sang tên đất có bắt buộc công chứng",
        "thời hiệu khiếu nại quyết định hành chính về đất đai",
        "người nước ngoài kết hôn với người việt nam cần gì",
        "khai tử người thân mất ở bệnh viện",
        "chứng thực bản sao giấy khai sinh",
        "tách hộ khẩu sau khi kết hôn",
        "cấp lại sổ đỏ bị mất",
        "thẩm định điều kiện cấp phép xây dựng",
        "mức đóng bhxh bắt buộc năm nay",
        "thủ tục hưởng chế độ thai sản",
        "đăng ký lưu trú cho người thuê trọ",
        "hợp đồng đặt cọc mua nhà có cần công chứng",
        "tranh chấp ranh giới thửa đất giải quyết ở đâu",
    ]
    extra_domains = [
        "ho_tich_chung_thuc",
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "dat_dai_xay_dung",
        "an_sinh_y_te_giao_duc",
        "an_sinh_y_te_giao_duc",
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "dat_dai_xay_dung",
        "ho_tich_chung_thuc",
        "ho_tich_chung_thuc",
        "ho_tich_chung_thuc",
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "dat_dai_xay_dung",
        "an_sinh_y_te_giao_duc",
        "an_sinh_y_te_giao_duc",
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "dat_dai_xay_dung",
    ]
    # Keep paraphrases unique enough: 2 wrappers x 2 roles, not a cartesian blast.
    prefixes = ["", "xin hỏi "]
    suffixes = ["", " tại hải phòng"]
    for q, domain in zip(paraphrases, extra_domains):
        for pre in prefixes:
            for suf in suffixes:
                for role in ("citizen", "officer"):
                    add(
                        role=role,
                        question=f"{pre}{q}{suf}".strip(),
                        history=[],
                        active_document=None,
                        route="legal_query",
                        legal_route="legal_rag",
                        domain=domain,
                        tags=["exact", "paraphrase", role],
                    )


    # --- extra OOS unique
    extra_oos = [
        "hôm nay ăn gì", "review phim hay", "cách trồng rau",
        "tư vấn mua xe máy", "lịch chiếu rạp cgv", "công thức bánh flan",
        "dự đoán xổ số", "cách tán tỉnh crush", "làm website bán hàng",
        "giá usd hôm nay", "chơi game gì hay", "yoga buổi sáng",
        "tour du lịch hạ long", "cách nuôi mèo", "soạn cv xin việc it",
        "nên mua laptop nào", "cách đầu tư chứng khoán", "kể chuyện cổ tích",
        "dịch menu nhà hàng", "công thức cocktail",
    ]
    for q in extra_oos:
        for role in ("citizen", "officer"):
            add(role=role, question=q, history=[], active_document=None,
                route="out_of_scope", legal_route=None, domain=None,
                tags=["oos", "exact", role])

    extra_meta = [
        "bot trả lời mất bao lâu", "có chat voice được không",
        "bạn hiểu tiếng anh không", "đổi giao diện tối",
        "xóa lịch sử hội thoại", "xuất bản ghi cuộc chat",
        "bạn kết nối cơ sở dữ liệu nào", "ai vận hành hệ thống này",
        "có thu phí không", "giờ làm việc của bộ phận hỗ trợ",
    ]
    for q in extra_meta:
        for role in ("citizen", "officer"):
            add(role=role, question=q, history=[], active_document=None,
                route="chat_meta", legal_route=None, domain=None,
                tags=["meta", "exact", role])

    # extra typos: drop all diacritics-ish and swap adjacent letters
    extra_typo = [
        ("thu tuc khai sinh can giay to gi", "ho_tich_chung_thuc", "direct_procedure_lookup"),
        ("dang ky ket hon o hai phong", "ho_tich_chung_thuc", "direct_procedure_lookup"),
        ("sang ten so do mat bao lau", "dat_dai_xay_dung", "direct_procedure_lookup"),
        ("giay phep xay dung nha o", "dat_dai_xay_dung", "direct_procedure_lookup"),
        ("cap the bhyt cho tre em", "an_sinh_y_te_giao_duc", "direct_procedure_lookup"),
        ("tro cap that nghiep duoc huong bao lau", "an_sinh_y_te_giao_duc", "direct_procedure_lookup"),
        ("dang ky tam tru online", "ho_tich_chung_thuc", "direct_procedure_lookup"),
        ("tach thua dat o can dieu kien gi", "dat_dai_xay_dung", "direct_procedure_lookup"),
        ("chung thuc chu ky tai ubnd", "ho_tich_chung_thuc", "direct_procedure_lookup"),
        ("khai tu nguoi than o benh vien", "ho_tich_chung_thuc", "direct_procedure_lookup"),
        ("luat dat dai 31/2024/QH15 noi gi", "dat_dai_xay_dung", "direct_document_lookup"),
        ("nd 101/2024/ND-CP ho tich", "ho_tich_chung_thuc", "direct_document_lookup"),
        ("dieu 8 luat ho tich", "ho_tich_chung_thuc", "direct_article_lookup"),
        ("le phi dang ky thuong tru", "ho_tich_chung_thuc", "direct_procedure_lookup"),
        ("ho so huong thai san bhxh", "an_sinh_y_te_giao_duc", "direct_procedure_lookup"),
    ]
    for q, domain, lroute in extra_typo:
        for role in ("citizen", "officer"):
            add(role=role, question=q, history=[], active_document=None,
                route="legal_query", legal_route=lroute, domain=domain,
                tags=["typo", role], needs_llm=True)

    extra_abbrev = [
        ("HSLT khai sinh", "ho_tich_chung_thuc"),
        ("TT ĐKHH", "ho_tich_chung_thuc"),
        ("GPXD nha o rieng le", "dat_dai_xay_dung"),
        ("GCNQSDD sang ten", "dat_dai_xay_dung"),
        ("TCTN sau nghi viec", "an_sinh_y_te_giao_duc"),
        ("BHXH thai san", "an_sinh_y_te_giao_duc"),
        ("DKTT 30 ngay", "ho_tich_chung_thuc"),
        ("CTBS giay khai sinh", "ho_tich_chung_thuc"),
        ("NĐ 123/2015", "ho_tich_chung_thuc"),
        ("QH15 31/2024", "dat_dai_xay_dung"),
    ]
    for q, domain in extra_abbrev:
        for role in ("citizen", "officer"):
            add(role=role, question=f"{q} lam the nao", history=[], active_document=None,
                route="legal_query", legal_route="direct_procedure_lookup", domain=domain,
                tags=["abbrev", role], needs_llm=True)

    extra_follow_q = [
        "còn hiệu lực không", "ai ký duyệt", "mẫu đơn nào",
        "nộp bản sao hay bản chính", "có phải ra phường không",
        "trẻ em có khác không", "người nước ngoài thì sao",
        "nếu quá hạn thì bị gì", "có miễn lệ phí không",
        "cần giấy ủy quyền không",
    ]
    for doc in DOCS:
        for q in extra_follow_q:
            for role in ("citizen",):
                add(role=role, question=q,
                    history=hist("pháp luật", "văn bản đang mở"),
                    active_document=doc, route="document_followup",
                    legal_route=None, domain=None, tags=["followup", role])

    extra_legal = [
        ("thời hạn đăng ký khai sinh quá 60 ngày thì sao", "ho_tich_chung_thuc"),
        ("đăng ký kết hôn khi một bên đang ở nước ngoài", "ho_tich_chung_thuc"),
        ("thừa kế quyền sử dụng đất khi không có di chúc", "dat_dai_xay_dung"),
        ("xây tường rào trên đất nông nghiệp có bị phạt không", "dat_dai_xay_dung"),
        ("mức hưởng bảo hiểm thất nghiệp tối đa", "an_sinh_y_te_giao_duc"),
        ("thẻ bhyt hết hạn khi đang nằm viện", "an_sinh_y_te_giao_duc"),
        ("thay đổi họ tên trên giấy khai sinh", "ho_tich_chung_thuc"),
        ("chuyển nhượng đất đang thế chấp ngân hàng", "dat_dai_xay_dung"),
        ("cấp giấy xác nhận tình trạng hôn nhân", "ho_tich_chung_thuc"),
        ("thủ tục nhận con nuôi trong nước", "ho_tich_chung_thuc"),
        ("cấp đổi giấy phép xây dựng khi thay đổi thiết kế", "dat_dai_xay_dung"),
        ("hưởng mai táng phí bhxh", "an_sinh_y_te_giao_duc"),
        ("đăng ký thường trú khi thuê nhà không có hợp đồng", "ho_tich_chung_thuc"),
        ("giải quyết khiếu nại cấp giấy chứng nhận quyền sử dụng đất", "dat_dai_xay_dung"),
        ("chế độ ốm đau cho người lao động hợp đồng", "an_sinh_y_te_giao_duc"),
        ("công chứng di chúc tại phòng công chứng hải phòng", "ho_tich_chung_thuc"),
        ("thủ tục ly hôn thuận tình có yếu tố nước ngoài", "ho_tich_chung_thuc"),
        ("cấp giấy phép xây dựng nhà ở kết hợp kinh doanh", "dat_dai_xay_dung"),
        ("đóng bhxh tự nguyện rồi chuyển sang bắt buộc", "an_sinh_y_te_giao_duc"),
        ("khôi phục hộ tịch bị mất hồ sơ gốc", "ho_tich_chung_thuc"),
    ]
    for q, domain in extra_legal:
        for role in ("citizen", "officer"):
            add(role=role, question=q, history=[], active_document=None,
                route="legal_query", legal_route="legal_rag", domain=domain,
                tags=["exact", role])
            add(role=role, question=typo(q), history=[], active_document=None,
                route="legal_query", legal_route="legal_rag", domain=domain,
                tags=["typo", role], needs_llm=True)

    extra_multi = [
        "ly hôn rồi khai sinh cho con mang họ mẹ",
        "sang tên đất cho con sau khi nhận nuôi",
        "người đang hưởng thất nghiệp có làm GPXD được không",
        "tạm trú hết hạn thì khám bhyt được không",
        "công chứng hợp đồng tặng cho đất và cập nhật hộ tịch",
        "kết hôn với người nước ngoài rồi đăng ký thường trú",
    ]
    for q in extra_multi:
        for role in ("citizen", "officer"):
            add(role=role, question=q, history=[], active_document=None,
                route="legal_query", legal_route="clarification", domain=None,
                tags=["multi_domain", role], needs_llm=True)

    extra_stale = [
        ("cấp thẻ BHYT", "an_sinh_y_te_giao_duc", "khai tử cần giấy tờ gì", "ho_tich_chung_thuc"),
        ("GPXD nhà ở", "dat_dai_xay_dung", "hưởng trợ cấp thất nghiệp", "an_sinh_y_te_giao_duc"),
        ("đăng ký kết hôn", "ho_tich_chung_thuc", "tách thửa đất ở", "dat_dai_xay_dung"),
        ("NĐ 154/2024/NĐ-CP", "an_sinh_y_te_giao_duc", "Luật 60/2014/QH13 điều 8", "ho_tich_chung_thuc"),
    ]
    for prev_q, prev_d, new_q, new_d in extra_stale:
        for role in ("citizen", "officer"):
            add(role=role, question=new_q, history=hist(prev_d, prev_q),
                active_document=None, route="legal_query",
                legal_route="direct_procedure_lookup", domain=new_d,
                tags=["stale_memory", role])


    officer_legal = [
        ("cán bộ tiếp nhận hồ sơ khai sinh thiếu giấy chứng sinh thì trả lại thế nào", "ho_tich_chung_thuc"),
        ("thẩm quyền UBND phường cấp GPXD nhà ở riêng lẻ", "dat_dai_xay_dung"),
        ("hướng dẫn đối chiếu dữ liệu BHXH khi giải quyết thất nghiệp", "an_sinh_y_te_giao_duc"),
        ("từ chối đăng ký kết hôn khi chưa đủ tuổi thì ghi lý do ra sao", "ho_tich_chung_thuc"),
        ("luồng xử lý hồ sơ chuyển nhượng đất có tranh chấp", "dat_dai_xay_dung"),
        ("cấp lại thẻ BHYT do sai thông tin hộ tịch", "an_sinh_y_te_giao_duc"),
    ]
    for q, domain in officer_legal:
        add(role="officer", question=q, history=[], active_document=None,
            route="legal_query", legal_route="legal_rag", domain=domain,
            tags=["exact", "officer"])
        add(role="citizen", question=q, history=[], active_document=None,
            route="legal_query", legal_route="legal_rag", domain=domain,
            tags=["exact", "citizen", "same_question"])

    pad_legal = [
        "điều kiện cấp giấy xác nhận cư trú",
        "thủ tục đăng ký khai sinh quá hạn",
        "mức phạt xây nhà không phép",
        "thời hạn cấp giấy chứng nhận quyền sử dụng đất",
        "quyền lợi BHYT trái tuyến",
        "hồ sơ hưởng chế độ tai nạn lao động",
        "công chứng hợp đồng ủy quyền",
        "thủ tục giám hộ cho người chưa thành niên",
        "cấp giấy phép xây dựng tạm",
        "đăng ký kết hôn cùng giới có được không",
        "thay đổi thông tin trên sổ hộ khẩu điện tử",
        "chuyển mục đích đất trồng lúa sang đất ở",
        "mức đóng BHYT hộ gia đình",
        "thủ tục xác nhận độc thân",
        "khiếu nại quyết định thu hồi đất",
        "cấp bản sao trích lục khai sinh",
        "thẩm định thiết kế xây dựng nhà ở",
        "hưởng BHXH một lần",
        "đăng ký tạm vắng",
        "thủ tục tặng cho quyền sử dụng đất giữa cha mẹ con",
        "nộp hồ sơ khai tử quá 15 ngày",
        "cấp giấy phép quảng cáo trên công trình",
        "thẻ BHYT cho học sinh",
        "xác nhận tình trạng hôn nhân để ly hôn",
        "cấp đổi giấy chứng nhận quyền sử dụng đất do sai diện tích",
    ]
    domains_cycle = ["ho_tich_chung_thuc", "dat_dai_xay_dung", "an_sinh_y_te_giao_duc"]
    for i, q in enumerate(pad_legal):
        domain = domains_cycle[i % 3]
        for role in ("citizen", "officer"):
            add(role=role, question=q, history=[], active_document=None,
                route="legal_query", legal_route="legal_rag", domain=domain,
                tags=["exact", role])

    # Dedup then split by question hash so holdout never sees train questions.
    seen = set()
    unique = []
    for row in rows:
        hist_key = tuple((h.get("role"), h.get("content")) for h in row.get("history") or [])
        ad = (row.get("active_document") or {}).get("id")
        key = (row["role"], row["question"], hist_key, ad)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    rows = unique
    by_split = {"train": 0, "dev": 0, "holdout": 0}
    for i, row in enumerate(rows, start=1):
        split = split_for_question(row["question"])
        row["split"] = split
        row["id"] = f"{split[0]}-{i:04d}"
        by_split[split] += 1

    OUT.mkdir(parents=True, exist_ok=True)
    for split in ("train", "dev", "holdout"):
        path = OUT / f"{split}.jsonl"
        subset = [r for r in rows if r["split"] == split]
        with path.open("w", encoding="utf-8") as f:
            for r in subset:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "total": len(rows),
        "by_split": by_split,
        "by_route": {},
        "by_tag": {},
        "needs_llm": sum(1 for r in rows if r["needs_llm"]),
    }
    from collections import Counter

    summary["by_route"] = dict(Counter(r["expected_conversation_route"] for r in rows))
    tag_c: Counter[str] = Counter()
    for r in rows:
        tag_c.update(r["tags"])
    summary["by_tag"] = dict(tag_c)
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    globals()["OUT"] = Path(args.out)
    main()
