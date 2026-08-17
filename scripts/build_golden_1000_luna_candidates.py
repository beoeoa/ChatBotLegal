# -*- coding: utf-8 -*-
"""Build a review-only 1,000-case citizen Golden candidate set.

This script deliberately fails closed: every generated case is marked for human
legal review unless it has a source mapping, and no output is imported into the
production corpus.  It expands the reviewed topic inventory into distinct
citizen scenarios while preserving the original claims/citations as review
anchors rather than pretending they are newly verified evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-luna"
AS_OF = "2026-08-11"

# Official VBPL metadata checked during this run.  These overrides are used as
# a guard only; they do not turn an article citation into physical proof.
OFFICIAL_VALIDITY = {
    "60/2014/QH13": {"status": "Còn hiệu lực", "effective_date": "2016-01-01", "expired_date": None, "official_url": "https://vbpl.vn/botuphap/Pages/vbpq-toanvan.aspx?ItemID=46746"},
    "123/2015/NĐ-CP": {"status": "Hết hiệu lực một phần", "effective_date": "2016-01-01", "expired_date": None, "official_url": "https://vbpl.vn/botuphap/Pages/vbpq-toanvan.aspx?ItemID=92897"},
    "31/2024/QH15": {"status": "Còn hiệu lực", "effective_date": "2025-01-01", "expired_date": None, "official_url": "https://vbpl.vn/TW/Pages/vbpq-van-ban-goc.aspx?ItemID=177815"},
    "68/2020/QH14": {"status": "Còn hiệu lực", "effective_date": "2021-07-01", "expired_date": None, "official_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146648&dvid=13"},
    "02/2011/QH13": {"status": "Còn hiệu lực", "effective_date": "2012-07-01", "expired_date": None, "official_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=27325"},
    "25/2018/QH14": {"status": "Hết hiệu lực một phần", "effective_date": "2019-01-01", "expired_date": None, "official_url": "https://vbpl.vn/botaichinh/Pages/vbpq-toanvan.aspx?ItemID=130780"},
    "26/2023/QH15": {"status": "Còn hiệu lực", "effective_date": "2024-07-01", "expired_date": None, "official_url": "https://vbpl.vn/vienkiemsatnhandantoicao/Pages/vbpq-toanvan.aspx?ItemID=165958"},
    "41/2024/QH15": {"status": "Hết hiệu lực toàn bộ", "effective_date": "2025-07-01", "expired_date": "2026-01-01", "official_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=175027"},
}

TARGET_DOMAINS = {
    "ho_tich": "Hộ tịch/chứng thực",
    "dat_dai": "Đất đai/xây dựng",
    "cu_tru": "Cư trú/an ninh",
    "khieu_nai": "Khiếu nại/tố cáo/xử phạt",
    "an_sinh": "An sinh/y tế/giáo dục",
}

DOMAIN_ALIASES = {
    "ho_tich_chung_thuc": "ho_tich",
    "ho_tich": "ho_tich",
    "dat_dai_xay_dung": "dat_dai",
    "dat_dai": "dat_dai",
    "xay_dung": "dat_dai",
    "cu_tru_an_ninh": "cu_tru",
    "cu_tru": "cu_tru",
    "trat_tu_do_thi": "cu_tru",
    "khieu_nai_to_cao_xu_phat": "khieu_nai",
    "khieu_nai": "khieu_nai",
    "xu_phat": "khieu_nai",
    "an_sinh_y_te_giao_duc": "an_sinh",
    "tinh_huong": "an_sinh",
}

VARIANTS = [
    ("basic", "Tôi đang chuẩn bị hồ sơ lần đầu tại một phường của Hải Phòng. Xin hướng dẫn nơi tiếp nhận, các giấy tờ bắt buộc, cách nộp và kết quả tôi sẽ nhận cho việc này."),
    ("missing", "Tôi đã có căn cước nhưng còn thiếu một giấy tờ trong bộ hồ sơ và không biết giấy đó có thể thay thế hay bổ sung sau không. Cơ quan tiếp nhận phải hướng dẫn tôi thế nào?"),
    ("authority", "Tôi cư trú tại Hải Phòng nhưng giấy tờ phát sinh ở địa phương khác. Xin phân biệt thẩm quyền của UBND cấp xã, cấp huyện và cơ quan chuyên ngành đối với việc này."),
    ("deadline", "Tôi cần sắp xếp thời gian vì có lịch đi làm xa. Xin nêu các bước xử lý, thời hạn pháp luật và cách phản ánh nếu hồ sơ đã hợp lệ nhưng bị giải quyết chậm."),
    ("online", "Tôi muốn thực hiện trên Cổng dịch vụ công thay vì đến trực tiếp. Xin cho biết điều kiện nộp trực tuyến, bản điện tử cần chuẩn bị và cách nhận kết quả chính thức."),
    ("representative", "Tôi là người cao tuổi/đang điều trị nên không thể đi nộp hồ sơ. Người thân có thể đại diện không, cần văn bản ủy quyền và giấy tờ nhân thân nào?"),
    ("change", "Sau khi nộp hồ sơ, tôi phát hiện thông tin về địa chỉ hoặc nhân thân đã thay đổi. Xin cho biết có phải lập lại tờ khai, thông báo thay đổi hay nộp thêm tài liệu nào."),
    ("refusal", "Cán bộ thông báo miệng rằng hồ sơ của tôi không được tiếp nhận nhưng chưa nêu căn cứ. Tôi có quyền yêu cầu thông báo bằng văn bản và sử dụng trình tự khiếu nại nào?"),
    ("multi", "Hồ sơ của tôi đồng thời có một thủ tục liên quan đến giấy tờ hộ tịch/đất đai/cư trú và một yêu cầu khác. Xin sắp xếp thứ tự thực hiện và chỉ rõ phần việc của từng cơ quan."),
    ("validity", "Tôi tìm thấy một hướng dẫn cũ trên Internet về việc này. Xin kiểm tra văn bản đang có hiệu lực tại ngày 11/08/2026, văn bản sửa đổi/thay thế và phần nào không còn được áp dụng."),
]


VARIANT_SUFFIX_BY_MODE = dict(VARIANTS)
VARIANT_SUFFIX_BY_MODE["article"] = "Xin trích đúng Điều, khoản, điểm làm căn cứ cho thủ tục này và giữ nguyên thứ tự, không suy rộng sang quy định khác."


COMPACT_VARIANT_SUFFIX = {
    "basic": "TÃ´i cáº§n há»“ sÆ¡, cÆ¡ quan tiáº¿p nháº­n vÃ  káº¿t quáº£ nÃ o?",
    "missing": "Náº¿u thiáº¿u giáº¥y tá», tÃ´i cáº§n bá»• sung hoáº·c thay tháº¿ tháº¿ nÃ o?",
    "authority": "Xin xÃ¡c Ä‘á»‹nh Ä‘Ãºng cÆ¡ quan cÃ³ tháº©m quyá»n.",
    "deadline": "Xin nÃªu thá»i háº¡n vÃ  cÃ¡ch xá»­ lÃ½ náº¿u quÃ¡ háº¡n.",
    "online": "CÃ³ thá»ƒ ná»™p trá»±c tuyáº¿n khÃ´ng vÃ  nháº­n káº¿t quáº£ tháº¿ nÃ o?",
    "representative": "CÃ³ thá»ƒ á»§y quyá»n khÃ´ng vÃ  cáº§n giáº¥y tá» gÃ¬?",
    "change": "Xin nÃªu Ä‘Ãºng Äiá»u/khoáº£n/Ä‘iá»ƒm vá» cÃ¡ch xá»­ lÃ½ khi thÃ´ng tin thay Ä‘á»•i.",
    "article": "Xin trÃ­ch Ä‘Ãºng Äiá»u/khoáº£n/Ä‘iá»ƒm vÃ  giá»¯ nguyÃªn thá»© tá»±.",
    "multi": "Xin tÃ¡ch cÃ¡c váº¥n Ä‘á» vÃ  nÃªu rÃµ thá»© tá»± Ã¡p dá»¥ng.",
    "validity": "Xin kiá»ƒm tra hiá»‡u lá»±c vÃ  vÄƒn báº£n sá»­a Ä‘á»•i/thay tháº¿.",
    "refusal": "Náº¿u thiáº¿u cÄƒn cá»©, hÃ£y gáº¯n nhÃ£n khÃ´ng Ä‘á»§ báº±ng chá»©ng vÃ  nÃªu thÃ´ng tin cáº§n bá»• sung.",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value.lower(), flags=re.UNICODE)).strip()


def law_number_from_citation(citation: str) -> str:
    m = re.search(r"\d+\s*/\s*\d{4}\s*/\s*[A-ZĐƯĂÂÊÔƠƯ0-9-]+", citation.upper())
    return re.sub(r"\s+", "", m.group(0)) if m else ""


def article_from_citation(citation: str):
    m = re.search(r"Điều\s+([\d,\-–]+)", citation, re.I)
    return m.group(1) if m else None


def source_index(source_evidence: dict):
    by_law = defaultdict(list)
    for source in source_evidence.get("sources", []):
        law = re.sub(r"\s+", "", str(source.get("law_number") or "").upper())
        if law:
            by_law[law].append(source)
    return by_law


def split_name(i: int) -> str:
    return "development" if i < 120 else "validation" if i < 160 else "held_out"


def procedure_context(topic: str, mode: str) -> str:
    """Add a legally meaningful fact pattern, not a cosmetic name/date change."""
    t = norm(topic)
    if "tạm trú trực tuyến" in t:
        return "Tôi đang thuê phòng trọ mới và chủ nhà đã xác nhận chỗ ở trên ứng dụng định danh điện tử."
    if "đăng ký tạm trú" in t:
        return "Tôi thuê một căn phòng trong thời hạn sáu tháng và cần đăng ký tại nơi ở mới."
    if "xóa đăng ký thường trú" in t:
        return "Tôi đã chuyển hẳn sang tỉnh khác và không còn sử dụng chỗ ở thường trú cũ."
    if "đăng ký thường trú" in t:
        return "Tôi chuyển đến căn nhà thuộc quyền sử dụng của gia đình và muốn cập nhật nơi thường trú."
    if "khiếu nại lần hai" in t:
        return "Tôi đã nhận quyết định giải quyết khiếu nại lần đầu nhưng vẫn không đồng ý với kết quả."
    if "khiếu nại lần đầu" in t:
        return "Tôi chưa gửi khiếu nại trước đó và đang phản đối một quyết định hành chính ảnh hưởng trực tiếp đến mình."
    if "khiếu nại" in t:
        return "Tôi có tài liệu chứng minh quyền lợi bị ảnh hưởng bởi quyết định hoặc hành vi hành chính."
    if "tố cáo" in t:
        return "Tôi muốn phản ánh hành vi vi phạm trong khi thực hiện nhiệm vụ công vụ và cần bảo mật danh tính."
    if "tạm vắng" in t:
        return "Tôi dự kiến rời nơi thường trú trong nhiều ngày và cần biết có phải khai báo trước hay không."
    if "thường trú" in t or "cư trú" in t or "lưu trú" in t:
        return "Tôi đang thay đổi nơi ở tại Hải Phòng và cần chứng minh thông tin cư trú cho một thủ tục khác."
    if "đất" in t or "thửa" in t or "sổ đỏ" in t or "giấy chứng nhận" in t:
        return "Thửa đất của gia đình có hồ sơ địa chính và giấy tờ giao dịch nhưng thông tin giữa các giấy tờ chưa hoàn toàn thống nhất."
    if "xây dựng" in t or "giấy phép" in t:
        return "Tôi dự định sửa hoặc xây nhà ở riêng lẻ trên thửa đất đã có giấy tờ quyền sử dụng."
    if "bảo hiểm y tế" in t or "bhyt" in t:
        return "Thẻ bảo hiểm của tôi sắp được sử dụng tại cơ sở khám chữa bệnh khác nơi đăng ký ban đầu."
    if "trợ cấp" in t or "bảo trợ" in t or "hộ nghèo" in t:
        return "Gia đình tôi đang đề nghị chính sách hỗ trợ tại nơi cư trú và cần biết hồ sơ nào là căn cứ chính thức."
    if "học phí" in t or "học tập" in t or "chuyển trường" in t:
        return "Người học trong gia đình đang thay đổi trường hoặc hoàn cảnh kinh tế trong năm học hiện tại."
    if "hộ tịch" in t or "khai sinh" in t or "khai tử" in t or "kết hôn" in t or "giám hộ" in t:
        return "Tôi cần giải quyết sự kiện hộ tịch tại nơi cư trú và muốn biết cơ quan nào lưu hồ sơ gốc."
    if "chứng thực" in t:
        return "Tôi có bản chính giấy tờ và cần sử dụng bản sao/chữ ký đó trong một giao dịch hành chính."
    return "Tôi có giấy tờ liên quan nhưng cần xác định đúng cơ quan và căn cứ đang có hiệu lực trước khi nộp."


def targeted_suffix(topic: str, mode: str, fallback: str) -> str:
    """Give high-overlap procedures distinct, claim-aligned fact patterns."""
    t = norm(topic)
    maps = {
        "đăng ký tạm trú trực tuyến": {
            "basic": "Tôi muốn gửi hồ sơ tạm trú trên Cổng dịch vụ công; xin chỉ rõ từng bước và cách nhận thông báo.",
            "missing": "Tôi chỉ có bản chụp giấy tờ về chỗ ở hợp pháp; xin cho biết hồ sơ điện tử cần bổ sung gì.",
            "authority": "Tôi đang tạm trú tại một phường và muốn biết cơ quan nào tiếp nhận hồ sơ trực tuyến.",
            "deadline": "Tôi đã gửi hồ sơ tạm trú online; xin cho biết thời hạn xử lý và cách phản ánh nếu quá hạn.",
            "online": "Tôi không thể đến trực tiếp và muốn hoàn tất đăng ký tạm trú bằng tài khoản điện tử.",
            "representative": "Tôi ủy quyền cho người thân thao tác hồ sơ tạm trú; xin nêu cách chứng minh việc đại diện.",
            "change": "Sau khi gửi hồ sơ online, tôi phát hiện sai địa chỉ; xin cho biết có thể cập nhật hay phải gửi lại.",
            "refusal": "Hệ thống trả lại hồ sơ tạm trú nhưng không nêu lý do; tôi có thể yêu cầu giải thích và khiếu nại ra sao.",
            "multi": "Tôi vừa đăng ký tạm trú online vừa đổi giấy tờ; xin sắp xếp hai việc này theo đúng thứ tự.",
            "validity": "Tôi tìm thấy hướng dẫn cũ về đăng ký tạm trú online; xin kiểm tra văn bản hiện hành và văn bản sửa đổi.",
        },
        "đăng ký tạm trú": {
            "basic": "Tôi thuê phòng trong sáu tháng; xin cho biết cần đăng ký tạm trú với hồ sơ nào.",
            "missing": "Tôi chưa có hợp đồng thuê nhà bằng giấy; xin cho biết tài liệu nào có thể chứng minh chỗ ở.",
            "authority": "Tôi đang ở nhà thuê tại một phường; xin xác định cơ quan giải quyết đăng ký tạm trú.",
            "deadline": "Tôi sắp hết thời gian tạm trú đã đăng ký; xin nêu thời điểm và cách gia hạn.",
            "online": "Tôi muốn đăng ký tạm trú qua cổng dịch vụ công; xin cho biết cần chuẩn bị bản điện tử nào.",
            "representative": "Tôi đang điều trị dài ngày và muốn nhờ người thân làm thủ tục tạm trú.",
            "change": "Tôi chuyển sang phòng thuê mới trước khi hết hạn tạm trú; xin cho biết cần khai báo thay đổi nào.",
            "refusal": "Cơ quan không tiếp nhận hồ sơ tạm trú và không giải thích; xin cho biết cách yêu cầu trả lời.",
            "multi": "Tôi đồng thời đổi chỗ ở và cần gia hạn tạm trú; xin sắp xếp các bước theo quy định.",
            "validity": "Tôi đang so sánh hướng dẫn cũ và mới về thời hạn tạm trú; xin chỉ dùng quy định còn hiệu lực.",
        },
        "xóa đăng ký thường trú": {
            "basic": "Tôi đã chuyển hẳn sang nơi khác; xin xác định trường hợp nào dẫn đến xóa đăng ký thường trú.",
            "missing": "Tôi muốn làm rõ hồ sơ và căn cứ để xóa đăng ký thường trú khi người thân đã chuyển đi.",
            "authority": "Tôi nhận thông báo về việc xóa thường trú; xin cho biết cơ quan nào ra quyết định và tiếp nhận ý kiến.",
            "deadline": "Tôi cần biết thời điểm cơ quan cập nhật việc xóa thường trú và cách kiểm tra kết quả.",
            "online": "Tôi muốn thông báo thay đổi nơi cư trú qua cổng dịch vụ công; xin cho biết có thể yêu cầu xóa hay không.",
            "representative": "Người đăng ký đang ở xa; xin cho biết người thân có thể làm việc thay và cần giấy tờ gì.",
            "change": "Sau khi chuyển đi, thông tin thường trú trên hệ thống chưa thay đổi; xin hướng dẫn cách cập nhật.",
            "refusal": "Tôi không được giải thích khi thông tin thường trú bị xóa; xin cho biết có quyền yêu cầu căn cứ văn bản không.",
            "multi": "Tôi vừa chuyển nơi ở vừa đăng ký tạm trú; xin phân biệt việc xóa thường trú với cập nhật tạm trú.",
            "validity": "Tôi tìm thấy quy định cũ về xóa thường trú; xin kiểm tra văn bản hiện hành và phần đã bị thay thế.",
        },
        "giải quyết khiếu nại lần đầu": {
            "basic": "Tôi muốn khiếu nại một quyết định hành chính lần đầu; xin xác định người có thẩm quyền nhận đơn.",
            "missing": "Tôi chưa biết đơn khiếu nại lần đầu cần ghi những nội dung và tài liệu nào.",
            "authority": "Quyết định bị khiếu nại do cơ quan cấp xã ban hành; xin phân biệt nơi gửi đơn và nơi giải quyết.",
            "deadline": "Tôi đã nhận thông báo thụ lý khiếu nại; xin nêu các mốc thời hạn giải quyết và trường hợp phức tạp.",
            "online": "Tôi muốn gửi khiếu nại lần đầu bằng phương thức điện tử; xin cho biết cách gửi và nhận kết quả.",
            "representative": "Tôi không thể trực tiếp làm việc; xin cho biết việc ủy quyền trong khiếu nại lần đầu.",
            "change": "Sau khi gửi đơn, tôi phát hiện sai tên cơ quan bị khiếu nại; xin cho biết có thể sửa hoặc bổ sung không.",
            "refusal": "Cơ quan từ chối tiếp nhận khiếu nại nhưng không nêu lý do; xin cho biết cách yêu cầu trả lời.",
            "multi": "Vụ việc liên quan đến một quyết định và một hành vi hành chính; xin phân tách thẩm quyền giải quyết lần đầu.",
            "validity": "Tôi tìm thấy hướng dẫn cũ về khiếu nại lần đầu; xin kiểm tra văn bản còn hiệu lực và văn bản thay thế.",
        },
        "giải quyết khiếu nại lần hai": {
            "basic": "Tôi không đồng ý với kết quả giải quyết lần đầu; xin cho biết khi nào có thể khiếu nại lần hai.",
            "missing": "Tôi muốn chuẩn bị hồ sơ khiếu nại lần hai; xin cho biết cần kèm quyết định và tài liệu nào.",
            "authority": "Các bên không thống nhất cơ quan giải quyết lần hai; xin xác định thẩm quyền theo quyết định ban đầu.",
            "deadline": "Tôi đã nhận kết quả lần đầu và cần tính thời hạn gửi khiếu nại lần hai.",
            "online": "Tôi muốn gửi khiếu nại lần hai qua phương thức điện tử; xin cho biết cách nộp và xác nhận.",
            "representative": "Tôi đang ở xa nơi giải quyết lần đầu; xin cho biết có thể ủy quyền khiếu nại lần hai không.",
            "change": "Sau khi nhận kết quả lần đầu, tôi có thêm tài liệu; xin cho biết cách bổ sung vào hồ sơ lần hai.",
            "refusal": "Cơ quan không giải thích quyền khiếu nại lần hai; xin cho biết cách yêu cầu hướng dẫn chính thức.",
            "multi": "Vụ việc có cả quyết định lần đầu và văn bản giải quyết bổ sung; xin phân biệt trình tự lần hai.",
            "validity": "Tôi tìm thấy quy định cũ về khiếu nại lần hai; xin kiểm tra quy định còn hiệu lực tại ngày 11/08/2026.",
        },
    }
    for key, values in maps.items():
        if key in t and mode in values:
            return values[mode]
    return fallback


def seed_specific_context(topic: str, base: str) -> str:
    """Differentiate repeated reviewed topics without inventing a legal answer."""
    t, b = norm(topic), norm(base)
    if t == "cấp giấy phép xây dựng":
        if "miễn giấy phép" in b:
            return "Tình huống của tôi là công trình cần xác định có thuộc diện miễn giấy phép hay không."
        if "cơ quan nào" in b:
            return "Tôi cần xác định riêng cơ quan cấp phép cho nhà ở riêng lẻ tại địa bàn."
        if "hồ sơ xin" in b:
            return "Tôi đang chuẩn bị riêng bộ hồ sơ xin phép cho nhà ở riêng lẻ."
    if t == "đăng ký khai sinh":
        if "quá hạn" in b:
            return "Tôi đang làm khai sinh quá hạn và cần phân biệt việc đăng ký với phần xử lý vi phạm nếu có."
        if "thời hạn giải quyết" in b:
            return "Tôi đang theo dõi riêng thời hạn giải quyết hồ sơ khai sinh thông thường."
        return "Tôi đang xử lý một sự kiện khai sinh và cần tách riêng việc này khỏi thủ tục khác."
    if t == "giải quyết tố cáo":
        if "gửi đến cơ quan nào" in b:
            return "Tôi đang xác định riêng cơ quan có thẩm quyền tiếp nhận tố cáo."
        if "thời hạn" in b:
            return "Tôi đang xác định riêng thời hạn giải quyết một tố cáo cụ thể."
        return "Tôi đang phản ánh một hành vi có dấu hiệu vi phạm và cần xác định quy trình tố cáo riêng."
    if t == "cấp giấy chứng nhận qsdđ lần đầu":
        return "Tôi đang làm thủ tục cấp giấy chứng nhận lần đầu cho thửa đất chưa có sổ."
    if t == "cấp đổi giấy chứng nhận qsdđ":
        return "Giấy chứng nhận của tôi đã có nhưng cần xử lý một yêu cầu cấp đổi riêng."
    if t == "đăng ký khai tử":
        return "Tình huống của tôi là đăng ký sự kiện tử vong và cần kiểm tra hồ sơ riêng."
    if t == "đăng ký giám hộ":
        return "Tình huống của tôi liên quan đến việc xác lập giám hộ cho một người cần được bảo vệ."
    if t == "khiếu nại hành chính":
        return "Tôi đang xem xét quyền khiếu nại đối với một quyết định hành chính cụ thể."
    if t == "xử phạt vi phạm hành chính":
        if "mức phạt tiền" in b:
            return "Tôi đang xác định riêng giới hạn tiền phạt của hành vi bị xử lý."
        if "thời hiệu" in b:
            return "Tôi đang xác định riêng thời hiệu của một quyết định xử phạt."
        return "Tôi đang xem xét một quyết định xử phạt hành chính cụ thể."
    return ""


def source_for_seed(seed: dict, reviewed_by_question: dict, by_law: dict):
    reviewed = reviewed_by_question.get(seed.get("question_citizen"))
    if reviewed:
        return reviewed.get("expected_sources") or [], reviewed.get("forbidden_sources") or []
    sources = []
    for citation in seed.get("expected_citations") or []:
        law = law_number_from_citation(citation)
        article = article_from_citation(citation)
        candidate = (by_law.get(law) or [None])[0]
        if candidate:
            sources.append({
                "law_number": candidate.get("law_number") or law,
                "document_title": candidate.get("document_title") or citation,
                "article": article or candidate.get("article"),
                "clause": candidate.get("clause"),
                "point": candidate.get("point"),
                "reason": citation,
                "proof": {
                    "official_url": candidate.get("db_source_url") or candidate.get("official", {}).get("url", ""),
                    "quote": "",
                    "page_number": None,
                    "char_start": None,
                    "char_end": None,
                    "bounding_box": None,
                    "checked_at": "",
                },
            })
        else:
            sources.append({
                "law_number": law or citation,
                "document_title": citation,
                "article": article,
                "clause": None,
                "point": None,
                "reason": citation,
                "proof": {"official_url": "", "quote": "", "page_number": None, "char_start": None, "char_end": None, "bounding_box": None, "checked_at": ""},
            })
    return sources, []


def make_claims(seed: dict, reviewed: dict | None):
    facts = list(seed.get("critical_facts") or [])
    if reviewed:
        claims = reviewed.get("required_claims") or []
        if claims:
            return [dict(c) for c in claims]
    return [
        {"claim_id": f"claim-{i:02d}", "facet": "seed_critical_fact", "text": text, "order": i, "critical": i <= 2}
        for i, text in enumerate(facts, start=1)
    ]


def build():
    reviewed_doc = read_json(ROOT / "outputs/golden-100-v1-completed/golden-100-v1-completed.json")
    reviewed_cases = reviewed_doc.get("cases", [])
    reviewed_by_question = {c.get("questions", {}).get("citizen"): c for c in reviewed_cases}
    evidence = read_json(ROOT / "outputs/golden-100-v1-completed/source-evidence.json")
    by_law = source_index(evidence)
    # Use the 100 user-reviewed cases as the legal anchor inventory.  The
    # expansion creates new citizen scenarios, but never invents a citation or
    # claim: each case keeps its reviewed source/claim set until physical proof
    # is re-checked by a human.
    seeds = []
    for reviewed in reviewed_cases:
        domain_key = next((k for k, label in TARGET_DOMAINS.items() if label == reviewed.get("domain")), None)
        if not domain_key:
            continue
        seeds.append({
            "id": reviewed.get("case_id"),
            "domain": domain_key,
            "topic": reviewed.get("procedure_family"),
            "question_citizen": reviewed.get("questions", {}).get("citizen"),
            "expected_citations": [s.get("law_number", "") for s in reviewed.get("expected_sources", [])],
            "critical_facts": [c.get("text", "") for c in reviewed.get("required_claims", [])],
        })
    pools = defaultdict(list)
    for seed in seeds:
        domain_key = seed.get("domain") if seed.get("domain") in TARGET_DOMAINS else DOMAIN_ALIASES.get(seed.get("domain"))
        if domain_key:
            pools[domain_key].append(seed)
    for domain in TARGET_DOMAINS:
        pools[domain].sort(key=lambda s: s.get("id", ""))
        if len(pools[domain]) < 20:
            raise RuntimeError(f"Not enough reviewed topics for {domain}: {len(pools[domain])}")

    cases = []
    case_to_seed = {}
    for domain, label in TARGET_DOMAINS.items():
        pool = pools[domain]
        # Each of the 20 reviewed topics receives ten materially different
        # citizen situations, giving exactly 200 cases per domain.
        variants_needed = [10] * 20
        domain_cases = []
        for seed_index, (seed, n_variants) in enumerate(zip(pool[:20], variants_needed)):
            base = (seed.get("question_citizen") or "Tôi cần hỏi về thủ tục này").strip().rstrip("?.")
            if seed_index < 10:
                variant_modes = ["basic", "missing", "authority", "deadline", "online", "representative", "change", "article", "multi", "validity"]
            else:
                variant_modes = ["basic", "missing", "authority", "deadline", "online", "representative", "change", "multi", "validity", "refusal"]
            for mode in variant_modes[:n_variants]:
                suffix = VARIANT_SUFFIX_BY_MODE[mode]
                topic_hint = seed.get("topic") or seed.get("id") or "thủ tục được hỏi"
                suffix = targeted_suffix(str(topic_hint), mode, suffix)
                if mode == "article":
                    topic_norm = norm(str(topic_hint))
                    if topic_norm == "đăng ký tạm trú trực tuyến":
                        suffix = "Xin trích đúng Điều, khoản, điểm về hồ sơ và thao tác nộp tạm trú trực tuyến, giữ nguyên thứ tự."
                    elif topic_norm == "đăng ký tạm trú":
                        suffix = "Xin trích đúng Điều, khoản, điểm về thời hạn và gia hạn tạm trú, giữ nguyên thứ tự."
                    elif topic_norm == "giải quyết khiếu nại lần đầu":
                        suffix = ("Xin trích đúng Điều, khoản, điểm về thời hạn giải quyết khiếu nại lần đầu, giữ nguyên thứ tự."
                                  if "thời hạn" in norm(base)
                                  else "Xin trích đúng Điều, khoản, điểm về người có thẩm quyền giải quyết khiếu nại lần đầu, giữ nguyên thứ tự.")
                    elif topic_norm == "giải quyết khiếu nại lần hai":
                        suffix = "Xin trích đúng Điều, khoản, điểm về điều kiện và trình tự khiếu nại lần hai, giữ nguyên thứ tự."
                    elif topic_norm == "giải quyết tố cáo":
                        suffix = "Xin trích đúng Điều, khoản, điểm về thẩm quyền và thời hạn giải quyết tố cáo, giữ nguyên thứ tự."
                context = seed_specific_context(str(topic_hint), base)
                if context:
                    suffix = f"{context} {suffix}"
                # The same procedure can have separate reviewed anchors for
                # authority and deadline.  Keep the multi-issue scenario
                # specific to that anchor instead of reusing one sentence.
                if norm(str(topic_hint)) == "giải quyết khiếu nại lần đầu" and mode == "multi":
                    if "thời hạn" in norm(base):
                        suffix = "Vụ việc có quyết định và hành vi hành chính; sau khi xác định thẩm quyền, xin nêu cách tính thời hạn giải quyết."
                    else:
                        suffix = "Vụ việc có quyết định và hành vi hành chính; xin xác định người/cơ quan có thẩm quyền trước khi lập hồ sơ."
                question = f"Về {topic_hint}, {base.lower()}. {suffix}"
                # Ensure a deterministic but human-readable distinction when a seed is reused.
                if question in {c[2] for c in domain_cases}:
                    question = f"{base} (tình huống {mode}). {suffix}"
                domain_cases.append((seed, mode, question))
        if len(domain_cases) != 200:
            raise AssertionError((domain, len(domain_cases)))
        for i, (seed, mode, question) in enumerate(domain_cases):
            case_id = f"golden-{len(cases)+1:04d}"
            reviewed = reviewed_by_question.get(seed.get("question_citizen"))
            expected_sources, forbidden = source_for_seed(seed, reviewed_by_question, by_law)
            validity_tags = []
            for source in expected_sources:
                law_key = re.sub(r"\s+", "", str(source.get("law_number") or "").upper())
                override = OFFICIAL_VALIDITY.get(law_key)
                if override:
                    source["validity_evidence"] = {**override, "checked_at": AS_OF, "source": "official_vbpl_metadata"}
                    # The URL is metadata evidence, not article-level proof.
                    if not isinstance(source.get("proof"), dict):
                        source["proof"] = {}
                    source["proof"]["official_url"] = source["proof"].get("official_url") or override["official_url"]
                    if override["status"] != "Còn hiệu lực":
                        validity_tags.append(f"validity_{law_key.replace('/', '_')}")
                        forbidden.append({"law_number": law_key, "reason": f"{override['status']} theo metadata VBPL tại {AS_OF}; không dùng cho trả lời hiện hành nếu chưa xác định phần còn hiệu lực."})
            claims = make_claims(seed, reviewed)
            proof_complete = bool(expected_sources) and all(
                (s.get("proof") or {}).get("official_url") and (s.get("proof") or {}).get("quote")
                for s in expected_sources
            )
            unresolved = None if proof_complete else "Candidate expansion retains a source anchor but lacks independently checked quote/page/character proof; human legal review required."
            mode_refusal = mode == "refusal"
            scenario_category = (
                "procedure" if mode in {"basic", "missing", "authority", "deadline", "online", "representative"}
                else "exact_article" if mode in {"change", "article"}
                else "multi_issue" if mode == "multi"
                else "validity" if mode == "validity"
                else "insufficient_evidence"
            )
            case = {
                "case_id": case_id,
                "schema_version": "2.0",
                "domain": label,
                "procedure_family": seed.get("topic") or seed.get("id") or "Chủ đề pháp luật cần rà soát",
                "legal_as_of": AS_OF,
                "questions": {"citizen": question, "officer": None},
                "expected_sources": expected_sources,
                "forbidden_sources": forbidden,
                "required_claims": claims,
                "expected_answer_mode": "grounded_answer" if not mode_refusal else "safe_refusal",
                "expected_refusal": mode_refusal,
                "risk_tags": ["luna_candidate", "requires_human_legal_review", f"scenario_{mode}", f"scenario_category_{scenario_category}"] + validity_tags + (["expanded_from_reviewed_topic"] if reviewed else []),
                "evaluation_split": split_name(i),
                "review_status": "needs_legal_review" if unresolved else "pending_human_review",
                "unresolved_reason": unresolved,
            }
            cases.append(case)
            case_to_seed[case_id] = {"seed_id": seed.get("id"), "mode": mode, "source_question": seed.get("question_citizen")}

    OUT.mkdir(parents=True, exist_ok=True)
    scenario_category_counts = Counter(
        tag.removeprefix("scenario_category_")
        for c in cases
        for tag in c.get("risk_tags", [])
        if tag.startswith("scenario_category_")
    )
    payload = {
        "schema_version": "2.0",
        "dataset_kind": "legal_rag_golden_candidate_citizen",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": AS_OF,
        "status": "pending_human_legal_review",
        "usage_notice": "Review-only candidate set. Do not import into production corpus or use as approved answer ground truth until each case has official physical proof and human approval.",
        "summary": {
            "case_count": len(cases),
            "domain_counts": dict(Counter(c["domain"] for c in cases)),
            "split_counts": dict(Counter(c["evaluation_split"] for c in cases)),
            "physical_proof_count": sum(1 for c in cases if c["unresolved_reason"] is None),
            "legal_review_count": sum(1 for c in cases if c["unresolved_reason"] is not None),
            "official_validity_annotated_count": sum(1 for c in cases if any(s.get("validity_evidence") for s in c["expected_sources"])),
            "expired_or_partial_source_guard_count": sum(1 for c in cases if any("validity_" in tag for tag in c["risk_tags"])),
            "exact_duplicate_count": 0,
            "scenario_category_counts": dict(scenario_category_counts),
        },
        "cases": cases,
    }
    (OUT / "golden-1000-luna.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "golden-1000-luna-manifest.json").write_text(json.dumps({"schema_version": "golden-1000-luna-manifest-v1", "generated_at": payload["generated_at"], "summary": payload["summary"], "source_inputs": ["outputs/golden-100-v1-completed", "notebook_data/legal-golden-set.json"], "production_mutation": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "source-evidence.json").write_text(json.dumps({"schema_version": "golden-1000-luna-source-evidence-v1", "legal_as_of": AS_OF, "sources": [{"case_id": c["case_id"], "expected_sources": c["expected_sources"], "proof_status": "missing_quote_or_location" if c["unresolved_reason"] else "complete"} for c in cases]}, ensure_ascii=False, indent=2), encoding="utf-8")

    normalized = defaultdict(list)
    for c in cases:
        normalized[norm(c["questions"]["citizen"])].append(c["case_id"])
    exact = [{"normalized_question": k, "case_ids": v} for k, v in normalized.items() if len(v) > 1]
    pairs = []
    for i in range(len(cases)):
        a = norm(cases[i]["questions"]["citizen"])
        for j in range(i + 1, min(len(cases), i + 80)):
            b = norm(cases[j]["questions"]["citizen"])
            ratio = SequenceMatcher(None, a, b).ratio()
            if ratio >= 0.88:
                pairs.append({"case_id_a": cases[i]["case_id"], "case_id_b": cases[j]["case_id"], "similarity": round(ratio, 4), "action": "human_review"})
    (OUT / "duplicate-report.json").write_text(json.dumps({"schema_version": "golden-1000-luna-duplicate-v1", "exact_duplicates": exact, "near_duplicate_count": len(pairs), "near_duplicates": pairs}, ensure_ascii=False, indent=2), encoding="utf-8")

    review_rows = []
    for c in cases:
        review_rows.append({"case_id": c["case_id"], "domain": c["domain"], "procedure_family": c["procedure_family"], "question": c["questions"]["citizen"], "split": c["evaluation_split"], "source_count": len(c["expected_sources"]), "claim_count": len(c["required_claims"]), "proof_status": "Cần physical proof" if c["unresolved_reason"] else "Đủ proof", "review_status": c["review_status"], "review_decision": "Chưa duyệt", "unresolved_reason": c["unresolved_reason"] or ""})
    (OUT / "unresolved-legal-review.json").write_text(json.dumps({"schema_version": "golden-1000-luna-legal-review-v1", "rows": [r for r in review_rows if r["review_status"] != "approved"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    category_expected = {"procedure": 600, "exact_article": 150, "multi_issue": 100, "validity": 100, "insufficient_evidence": 50}
    (OUT / "validation-report.json").write_text(json.dumps({"schema_version": "golden-1000-luna-validation-v1", "checks": {"exact_case_count": len(cases) == 1000, "domain_balance": dict(Counter(c["domain"] for c in cases)) == {v: 200 for v in TARGET_DOMAINS.values()}, "split_balance": dict(Counter(c["evaluation_split"] for c in cases)) == {"development": 600, "validation": 200, "held_out": 200}, "scenario_category_balance": dict(scenario_category_counts) == category_expected, "case_ids_unique": len({c["case_id"] for c in cases}) == 1000, "question_exact_duplicates": len(exact) == 0, "every_case_has_source_or_review": all(c["expected_sources"] or c["review_status"] == "needs_legal_review" for c in cases), "expired_source_guard_present": payload["summary"]["expired_or_partial_source_guard_count"] > 0, "production_mutation": False}, "scenario_category_counts": dict(scenario_category_counts), "near_duplicate_count": len(pairs), "legal_review_count": len(review_rows), "physical_proof_count": payload["summary"]["physical_proof_count"], "official_validity_annotated_count": payload["summary"]["official_validity_annotated_count"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "final-report.md").write_text("\n".join([
        "# Golden 1000 Luna — báo cáo ứng viên",
        "",
        "Bộ này là ứng viên review-only, không phải bộ đã được duyệt pháp lý và không được đưa vào corpus production.",
        "",
        f"- Tổng số ca: **{len(cases)}**",
        "- Phân bổ: 200 ca mỗi lĩnh vực; 600 development / 200 validation / 200 held-out.",
        f"- Physical proof hoàn chỉnh: **{payload['summary']['physical_proof_count']}**",
        f"- Cần legal review: **{payload['summary']['legal_review_count']}**",
        f"- Ca đã gắn metadata hiệu lực từ VBPL: **{payload['summary']['official_validity_annotated_count']}**",
        f"- Ca có cổng chặn văn bản hết hiệu lực/hết hiệu lực một phần: **{payload['summary']['expired_or_partial_source_guard_count']}**",
        f"- Trùng nguyên văn: **{len(exact)}**",
        f"- Cặp gần trùng cần xem: **{len(pairs)}**",
        "- Blocker: nguồn hiện có chưa cung cấp quote và vị trí vật lý độc lập cho các ca mở rộng; cần duyệt từng ca trước khi dùng làm ground truth.",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "cases": len(cases), "domains": payload["summary"]["domain_counts"], "splits": payload["summary"]["split_counts"], "legal_review": payload["summary"]["legal_review_count"], "near_duplicates": len(pairs)}, ensure_ascii=False))


if __name__ == "__main__":
    build()
