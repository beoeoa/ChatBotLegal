# -*- coding: utf-8 -*-
"""Expand legal golden set for Step 11 quality benchmark cases."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "notebook_data" / "legal-golden-set.json"


def main() -> int:
    data = json.loads(PATH.read_text(encoding="utf-8-sig"))
    questions = list(data.get("questions") or [])
    by_id = {q.get("id"): q for q in questions if isinstance(q, dict)}

    # Fix mojibake / incomplete required cases and enrich form expectations.
    required = {
        "ht_002": {
            "id": "ht_002",
            "domain": "ho_tich",
            "topic": "khai_sinh_nuoc_ngoai",
            "question_citizen": "Trẻ em sinh ở nước ngoài chưa đăng ký khai sinh, cha cư trú Hải Phòng thì nộp hồ sơ ở đâu và cần giấy tờ gì?",
            "question_officer": "Thẩm quyền đăng ký khai sinh cho trẻ em sinh ở nước ngoài theo Điều 35 Luật Hộ tịch? Phân biệt với Điều 13.",
            "expected_citations": [
                "Luật Hộ tịch 60/2014/QH13",
                "Điều 35",
                "Điều 13",
                "Nghị định 123/2015/NĐ-CP",
            ],
            "expected_scope": "central",
            "critical_facts": [
                "UBND cấp huyện có thẩm quyền",
                "Không phải UBND cấp xã tiếp nhận trực tiếp theo Điều 35",
                "Cần giấy khai sinh nước ngoài / giấy tờ chứng minh việc sinh",
            ],
            "expected_authority_cues": ["ubnd cap huyen", "cap huyen", "dieu 35"],
            "forbidden_authority_cues": ["ubnd cap xa co tham quyen dang ky truc tiep theo dieu 35"],
            "expected_forms": [
                {
                    "name": "Tờ khai đăng ký khai sinh",
                    "procedure_id": "dang_ky_khai_sinh_nuoc_ngoai",
                    "official_only": True,
                }
            ],
            "no_fake_deadline": True,
            "no_fake_fee": True,
            "evaluation_hints": {
                "citizen": "Phải chỉ rõ cấp huyện, không nói phường tự đăng ký theo Điều 35.",
                "officer": "Phân biệt Điều 13 và Điều 35; nêu căn cứ thẩm quyền huyện.",
            },
        },
        "ht_004": {
            "id": "ht_004",
            "domain": "ho_tich",
            "topic": "xac_nhan_tinh_trang_hon_nhan",
            "question_citizen": "Tôi cần xin Giấy xác nhận tình trạng hôn nhân (độc thân) tại Hải Phòng thì nộp ở đâu, mang giấy tờ gì?",
            "question_officer": "Thẩm quyền và hồ sơ cấp Giấy xác nhận tình trạng hôn nhân theo Nghị định 123/2015/NĐ-CP?",
            "expected_citations": [
                "Nghị định 123/2015/NĐ-CP",
                "Luật Hộ tịch 60/2014/QH13",
                "Điều 21",
                "Điều 22",
                "Điều 23",
            ],
            "expected_scope": "central",
            "critical_facts": [
                "UBND cấp xã/phường nơi thường trú có thẩm quyền cấp trong nhiều trường hợp",
                "Tờ khai theo mẫu",
                "Không bịa thời hạn/lệ phí nếu nguồn chưa nêu",
            ],
            "expected_authority_cues": ["ubnd cap xa", "ubnd phuong", "tinh trang hon nhan"],
            "expected_forms": [
                {
                    "name": "Tờ khai cấp Giấy xác nhận tình trạng hôn nhân",
                    "procedure_id": "xac_nhan_tinh_trang_hon_nhan",
                    "official_only": True,
                }
            ],
            "no_fake_deadline": True,
            "no_fake_fee": True,
            "evaluation_hints": {
                "citizen": "Nêu nơi nộp, hồ sơ, biểu mẫu chính thức nếu có.",
                "officer": "Nêu căn cứ NĐ 123, thẩm quyền, không dùng form seed giả.",
            },
        },
        "dd_001": {
            "id": "dd_001",
            "domain": "dat_dai",
            "topic": "sang_ten_so_do",
            "question_citizen": "Sang tên sổ đỏ tại Hải Phòng cần làm gì, nộp ở đâu và cần biểu mẫu nào?",
            "question_officer": "Thủ tục và thẩm quyền đăng ký biến động đất đai (sang tên sổ đỏ) theo Luật Đất đai hiện hành?",
            "expected_citations": [
                "Luật Đất đai",
                "Nghị định",
                "biến động đất đai",
            ],
            "expected_scope": "central",
            "critical_facts": [
                "Văn phòng đăng ký đất đai / cơ quan đăng ký đất đai",
                "Hợp đồng công chứng/chứng thực theo quy định",
                "Đơn đăng ký biến động đất đai",
            ],
            "expected_authority_cues": ["van phong dang ky dat dai", "co quan dang ky dat dai", "bien dong"],
            "expected_forms": [
                {
                    "name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 09/ĐK)",
                    "procedure_id": "sang_ten_so_do",
                    "official_only": True,
                }
            ],
            "no_fake_deadline": True,
            "no_fake_fee": True,
            "evaluation_hints": {
                "citizen": "Hướng dẫn nơi nộp, hồ sơ, biểu mẫu; không bịa thời hạn/lệ phí.",
                "officer": "Nêu thẩm quyền, căn cứ, không dùng form seed giả làm official.",
            },
        },
        "tt_001": {
            "id": "tt_001",
            "domain": "trat_tu_do_thi",
            "topic": "cam_do_xe_via_he",
            "question_citizen": "Tại sao tôi bị cấm đỗ xe ở vỉa hè trước nhà và căn cứ xử lý là gì?",
            "question_officer": "Căn cứ pháp lý cấm đỗ xe trên vỉa hè khu vực đô thị và chế tài xử lý?",
            "expected_citations": [
                "Luật Giao thông đường bộ",
                "Nghị định 100/2019/NĐ-CP",
            ],
            "expected_scope": "central",
            "critical_facts": [
                "Vỉa hè không phải nơi đỗ xe theo quy định giao thông",
                "Có thể bị xử phạt vi phạm hành chính",
                "Có thể có quy định địa phương bổ sung",
            ],
            "expected_authority_cues": ["cong an", "ubnd", "trat tu", "xu phat"],
            "expected_forms": [],
            "no_fake_deadline": True,
            "no_fake_fee": True,
            "allow_missing_form": True,
            "evaluation_hints": {
                "citizen": "Giải thích vì sao cấm, căn cứ, không bịa mức phạt nếu nguồn thiếu.",
                "officer": "Nêu căn cứ luật/NĐ, thẩm quyền xử lý, không bịa số tiền.",
            },
        },
        "dd_002": {
            "id": "dd_002",
            "domain": "xay_dung",
            "topic": "phat_xay_khong_phep",
            "question_citizen": "Xây nhà không phép bị xử lý thế nào, ai có thẩm quyền và có bị phạt không?",
            "question_officer": "Thẩm quyền và căn cứ xử lý hành vi xây dựng công trình không phép theo quy định hiện hành?",
            "expected_citations": [
                "Luật Xây dựng",
                "Nghị định",
            ],
            "expected_scope": "central",
            "critical_facts": [
                "Xây dựng không phép là vi phạm trật tự xây dựng",
                "Có thẩm quyền xử lý của UBND cấp có thẩm quyền",
                "Không bịa mức phạt cụ thể nếu nguồn chưa đủ",
            ],
            "expected_authority_cues": ["ubnd", "xay dung khong phep", "xu phat"],
            "expected_forms": [],
            "no_fake_deadline": True,
            "no_fake_fee": True,
            "allow_missing_form": True,
            "evaluation_hints": {
                "citizen": "Nêu hướng xử lý, cơ quan có thẩm quyền; không bịa mức tiền.",
                "officer": "Nêu căn cứ, thẩm quyền; không bịa mức phạt khi thiếu nguồn.",
            },
        },
    }

    # Keep other existing questions but repair obvious mojibake ones if present.
    for qid, payload in required.items():
        by_id[qid] = payload

    # Preserve non-required existing questions.
    ordered_ids = []
    for q in questions:
        qid = q.get("id")
        if qid and qid not in ordered_ids:
            ordered_ids.append(qid)
    for qid in required:
        if qid not in ordered_ids:
            ordered_ids.append(qid)

    new_questions = []
    for qid in ordered_ids:
        item = by_id.get(qid)
        if not item:
            continue
        # ensure required scoring fields exist for all
        item.setdefault("expected_forms", [])
        item.setdefault("no_fake_deadline", True)
        item.setdefault("no_fake_fee", True)
        item.setdefault("expected_authority_cues", [])
        item.setdefault("allow_missing_form", False)
        new_questions.append(item)

    data["questions"] = new_questions
    data["version"] = "1.1.0-step11"
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["description"] = (
        "Legal QA Golden Set for ChatBotLegal - expanded for Step 11 quality/form benchmark"
    )
    data["criteria"] = {
        "authority_correct": "Correct authority / jurisdiction cues",
        "citation_supported": "Answer cites supported law/article evidence",
        "no_fake_deadline": "No fabricated deadlines",
        "no_fake_fee": "No fabricated fees/amounts",
        "form_recommendation_correct": "Recommended forms match expected procedure",
        "no_seed_form_as_official": "Seed/synthetic forms not presented as official",
        "role_appropriate": "Tone/detail matches citizen or officer role",
        "retrieval_score": "0-10 retrieval quality",
        "grounding_score": "0-10 citation/grounding quality",
        "form_score": "0-10 form recommendation quality",
        "safety_score": "0-10 no fake fee/deadline/authority safety",
        "total_score": "0-10 weighted overall; fail if < 8.0",
    }
    data["benchmark_required_topics"] = [
        "khai_sinh_nuoc_ngoai",
        "xac_nhan_tinh_trang_hon_nhan",
        "sang_ten_so_do",
        "cam_do_xe_via_he",
        "phat_xay_khong_phep",
    ]
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "questions": len(new_questions),
        "required_present": sorted(required.keys()),
        "path": str(PATH),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
