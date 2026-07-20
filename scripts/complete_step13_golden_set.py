"""Complete the minimum Step 13 legal-quality golden set.

The cases are test questions, not legal source data. Expected citations are
left empty when the local corpus must decide the exact active source; this
prevents the benchmark itself from inventing a legal authority.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "notebook_data" / "legal-golden-set.json"

CASES = [
    {
        "id": "s13_ht_001", "domain": "ho_tich_chung_thuc", "topic": "chung_thuc",
        "question_citizen": "Tôi cần chứng thực bản sao giấy tờ tại phường Hải Phòng thì phải chuẩn bị gì và nộp ở đâu?",
        "question_officer": "Phân tích thẩm quyền, hồ sơ và quy trình chứng thực bản sao tại UBND cấp xã/phường.",
        "expected_citations": [], "critical_facts": ["nơi tiếp nhận", "hồ sơ", "không tự bịa lệ phí"],
    },
    {
        "id": "s13_ht_002", "domain": "ho_tich_chung_thuc", "topic": "cai_chinh_ho_tich",
        "question_citizen": "Tôi muốn cải chính ngày sinh trên giấy khai sinh thì cần làm gì trước?",
        "question_officer": "Nêu điểm cần xác minh khi xử lý hồ sơ cải chính hộ tịch và thẩm quyền hiện hành.",
        "expected_citations": [], "critical_facts": ["giấy tờ chứng minh", "thẩm quyền", "không kết luận khi thiếu hồ sơ"],
    },
    {
        "id": "s13_dd_001", "domain": "dat_dai_xay_dung", "topic": "dat_dai",
        "question_citizen": "Tôi muốn đăng ký biến động đất đai ở Hải Phòng thì cần chuẩn bị những giấy tờ nào?",
        "question_officer": "Kiểm tra thẩm quyền và thành phần hồ sơ đăng ký biến động đất đai theo nguồn đang có hiệu lực.",
        "expected_citations": [], "critical_facts": ["cơ quan tiếp nhận", "hồ sơ", "hiệu lực văn bản"],
    },
    {
        "id": "s13_dd_002", "domain": "dat_dai_xay_dung", "topic": "xay_dung",
        "question_citizen": "Nhà tôi sửa chữa nhỏ ở phường thì có phải xin giấy phép xây dựng không?",
        "question_officer": "Phân biệt sửa chữa, cải tạo và xây dựng mới; nêu dữ liệu cần xác minh trước khi kết luận.",
        "expected_citations": [], "critical_facts": ["hiện trạng", "quy hoạch", "không bịa điều kiện"],
    },
    {
        "id": "s13_ct_001", "domain": "cu_tru_an_ninh", "topic": "cu_tru",
        "question_citizen": "Đăng ký tạm trú tại phường Hải Phòng cần làm qua đâu và chuẩn bị thông tin gì?",
        "question_officer": "Kiểm tra cơ quan có thẩm quyền và quy trình đăng ký tạm trú trên dữ liệu hiện hành.",
        "expected_citations": [], "critical_facts": ["cơ quan tiếp nhận", "thông tin cư trú", "không bịa thời hạn"],
    },
    {
        "id": "s13_ct_002", "domain": "cu_tru_an_ninh", "topic": "an_ninh_trat_tu",
        "question_citizen": "Tôi muốn phản ánh một vụ việc gây mất trật tự ở khu dân cư thì liên hệ đâu?",
        "question_officer": "Phân loại thẩm quyền tiếp nhận phản ánh về an ninh trật tự tại địa bàn phường.",
        "expected_citations": [], "critical_facts": ["kênh tiếp nhận", "mức độ khẩn cấp", "cảnh báo gọi cơ quan khẩn cấp khi cần"],
    },
    {
        "id": "s13_kn_001", "domain": "khieu_nai_to_cao_xu_phat", "topic": "khieu_nai",
        "question_citizen": "Tôi không đồng ý quyết định xử phạt thì cần xem lại và gửi đơn ở đâu?",
        "question_officer": "Phân biệt khiếu nại và phản ánh đối với quyết định xử phạt; cần kiểm tra thời hạn và thẩm quyền nào.",
        "expected_citations": [], "critical_facts": ["quyết định bị khiếu nại", "người có thẩm quyền", "không bịa thời hạn"],
    },
    {
        "id": "s13_kn_002", "domain": "khieu_nai_to_cao_xu_phat", "topic": "to_cao",
        "question_citizen": "Muốn tố cáo hành vi vi phạm của cán bộ phường thì tôi cần làm gì?",
        "question_officer": "Nêu các điểm phải xác minh khi tiếp nhận và phân loại đơn tố cáo thuộc thẩm quyền.",
        "expected_citations": [], "critical_facts": ["nội dung tố cáo", "thẩm quyền", "bảo mật thông tin"],
    },
    {
        "id": "s13_as_001", "domain": "an_sinh_y_te_giao_duc", "topic": "tro_giup_xa_hoi",
        "question_citizen": "Gia đình tôi muốn xin trợ giúp xã hội tại phường thì cần hỏi và chuẩn bị giấy tờ gì?",
        "question_officer": "Kiểm tra điều kiện, hồ sơ và cơ quan xử lý hồ sơ trợ giúp xã hội cấp xã.",
        "expected_citations": [], "critical_facts": ["đối tượng", "hồ sơ", "không bịa mức tiền"],
    },
    {
        "id": "s13_as_002", "domain": "an_sinh_y_te_giao_duc", "topic": "y_te_giao_duc",
        "question_citizen": "Tôi cần hỏi thủ tục chính sách y tế hoặc giáo dục ở phường thì bắt đầu từ bộ phận nào?",
        "question_officer": "Phân loại yêu cầu y tế, giáo dục và xác định cơ quan cấp xã cần phối hợp.",
        "expected_citations": [], "critical_facts": ["phân loại lĩnh vực", "cơ quan phối hợp", "nêu phần cần xác minh"],
    },
]

PILOT_DOMAIN_LABELS = {
    "ho_tich_chung_thuc": "hộ tịch hoặc chứng thực",
    "dat_dai_xay_dung": "đất đai hoặc xây dựng",
    "cu_tru_an_ninh": "cư trú hoặc an ninh trật tự",
    "khieu_nai_to_cao_xu_phat": "khiếu nại, tố cáo hoặc xử phạt",
    "an_sinh_y_te_giao_duc": "an sinh xã hội, y tế hoặc giáo dục",
}

PILOT_TOPICS = [
    "hồ sơ cần chuẩn bị",
    "nơi tiếp nhận hồ sơ",
    "thẩm quyền giải quyết",
    "cách nộp hồ sơ trực tuyến",
    "cách nộp hồ sơ trực tiếp",
    "giấy tờ cần xuất trình",
    "trường hợp cần bổ sung hồ sơ",
    "trường hợp hồ sơ bị từ chối tiếp nhận",
    "cách nhận kết quả",
    "cách xin bản sao kết quả",
    "cách ủy quyền cho người khác",
    "điều kiện để được giải quyết",
    "thông tin cần xác minh trước khi nộp",
    "cách tra cứu tình trạng hồ sơ",
    "cách sửa thông tin trong hồ sơ",
    "cách rút hồ sơ",
    "cách phản ánh việc chậm giải quyết",
    "cách nhận thông báo bổ sung",
    "trường hợp không có giấy tờ gốc",
    "trường hợp giấy tờ bị mất",
    "trường hợp có người đại diện",
    "trường hợp người yêu cầu ở ngoài địa bàn",
    "trường hợp có tranh chấp",
    "trường hợp có yếu tố nước ngoài",
    "trường hợp người yêu cầu là người chưa thành niên",
    "trường hợp cần phối hợp với cơ quan khác",
    "cách xác định biểu mẫu tương ứng",
    "cách kiểm tra văn bản đang có hiệu lực",
    "cách xác định thời hạn khi nguồn có quy định",
    "cách xác định lệ phí khi nguồn có quy định",
]


def expand_pilot_cases(existing: dict[str, dict]) -> None:
    """Ensure the pilot has 30 neutral test cases in each canonical domain.

    These prompts intentionally ask what must be looked up. They do not embed
    guessed article numbers, deadlines, fees, or authorities.
    """
    for domain, domain_label in PILOT_DOMAIN_LABELS.items():
        current = sum(1 for item in existing.values() if item.get("domain") == domain)
        for index, topic in enumerate(PILOT_TOPICS, start=1):
            if current >= 30:
                break
            case_id = f"pilot_{domain}_{index:03d}"
            if case_id in existing:
                continue
            existing[case_id] = {
                "id": case_id,
                "domain": domain,
                "topic": topic,
                "question_citizen": f"Tôi cần hỏi về {topic} đối với thủ tục {domain_label} tại phường Hải Phòng. Tôi phải làm gì?",
                "question_officer": f"Đề nghị xác định căn cứ, thẩm quyền và điểm cần xác minh khi xử lý yêu cầu về {topic} trong lĩnh vực {domain_label} tại cấp xã/phường.",
                "expected_citations": [],
                "critical_facts": ["nguồn pháp lý đang hiệu lực", "thẩm quyền", "hồ sơ hoặc dữ kiện cần xác minh"],
                "expected_forms": [],
                "no_fake_deadline": True,
                "no_fake_fee": True,
                "allow_missing_form": True,
                "expected_authority_cues": [],
                "evaluation_hints": {
                    "citizen": "Trả lời dễ hiểu, nêu việc cần làm và phần cần xác minh.",
                    "officer": "Nêu căn cứ, thẩm quyền và điểm cần kiểm tra; không suy diễn.",
                },
            }
            current += 1


def repair_mojibake(value: str) -> str:
    markers = ("Ã", "Â", "Ä", "áº", "á»", "Æ")
    if sum(value.count(x) for x in markers) < 10:
        return value
    try:
        fixed = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return fixed if sum(fixed.count(x) for x in markers) < sum(value.count(x) for x in markers) else value


def main() -> int:
    raw = repair_mojibake(PATH.read_text(encoding="utf-8-sig"))
    data = json.loads(raw)
    existing = {item.get("id"): item for item in data.get("questions", []) if isinstance(item, dict)}
    for case in CASES:
        item = dict(case)
        item.setdefault("expected_forms", [])
        item.setdefault("no_fake_deadline", True)
        item.setdefault("no_fake_fee", True)
        item.setdefault("allow_missing_form", True)
        item.setdefault("expected_authority_cues", [])
        item.setdefault("evaluation_hints", {
            "citizen": "Trả lời đơn giản, nêu rõ việc cần làm và phần cần xác minh.",
            "officer": "Nêu thẩm quyền, căn cứ và điểm cần kiểm tra; không suy diễn.",
        })
        existing[case["id"]] = item
    expand_pilot_cases(existing)
    data["questions"] = list(existing.values())
    data["canonical_domains"] = [
        "ho_tich_chung_thuc", "dat_dai_xay_dung", "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat", "an_sinh_y_te_giao_duc",
    ]
    data["step13_minimum_cases_per_domain"] = 30
    data["pilot_case_count_target"] = 150
    data["version"] = "1.2.0-step13"
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({"questions": len(data["questions"]), "added_or_updated": len(CASES), "path": str(PATH)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
