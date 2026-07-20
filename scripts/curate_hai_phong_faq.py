"""Curate practical Hai Phong ward/commune FAQs and repair form references.

This is deliberately deterministic: FAQ entries may reference only approved
official form IDs already present in the local forms index. The script does
not invent form IDs, URLs, deadlines, fees, or legal citations.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAQ_PATH = ROOT / "notebook_data/faq_store.json"
FORMS_PATH = ROOT / "notebook_data/forms/haiphong_official_form_index.json"


def faq(
    ident: str,
    question: str,
    answer: str,
    domain: str,
    steps: list[str],
    form_ids: list[str] | None = None,
    submission_place: str = "Bộ phận Một cửa UBND cấp xã/phường có thẩm quyền hoặc cổng dịch vụ công theo thủ tục.",
) -> dict:
    return {
        "id": ident,
        "question": question,
        "answer": answer,
        "submission_place": submission_place,
        "legal_basis": [],
        "guidance_label": "Thông tin hướng dẫn tham khảo; cần đối chiếu căn cứ hiện hành khi giải quyết hồ sơ cụ thể.",
        "requires_forms": bool(form_ids),
        "steps": steps,
        "form_ids": form_ids or [],
        "domain": domain,
        "ward_scope": "Hai Phong",
        "review_status": "approved",
        "approved_by": "admin_curated",
    }


FAQ_ADDITIONS = [
    faq("faq_hp_051", "Đăng ký lại khai sinh bị mất giấy khai sinh cần chuẩn bị gì?", "Chuẩn bị tờ khai đăng ký lại khai sinh và giấy tờ còn lưu để chứng minh thông tin hộ tịch. Cơ quan đăng ký sẽ kiểm tra dữ liệu, sổ hộ tịch và yêu cầu bổ sung nếu hồ sơ chưa đủ.", "ho_tich_chung_thuc", ["Chuẩn bị giấy tờ nhân thân và tài liệu hộ tịch còn có", "Điền tờ khai đăng ký lại khai sinh", "Nộp tại Bộ phận Một cửa UBND cấp xã có thẩm quyền", "Nhận phiếu tiếp nhận và bổ sung nếu được yêu cầu"], ["4df56b79b93c805b88842bd2"]),
    faq("faq_hp_052", "Đăng ký kết hôn tại phường cần mang theo giấy tờ gì?", "Hai bên cần chuẩn bị giấy tờ nhân thân và thông tin cư trú theo yêu cầu của cơ quan đăng ký. Cả hai phải tự nguyện có mặt để đăng ký, trừ trường hợp thủ tục hiện hành cho phép hình thức khác.", "ho_tich_chung_thuc", ["Kiểm tra điều kiện đăng ký kết hôn", "Chuẩn bị giấy tờ nhân thân và tờ khai", "Hai bên đến Bộ phận Một cửa UBND cấp xã có thẩm quyền", "Kiểm tra thông tin, ký và nhận kết quả theo phiếu hẹn"], ["58c16d327d044c72a9380ce3"]),
    faq("faq_hp_053", "Xin giấy xác nhận tình trạng hôn nhân để đăng ký kết hôn làm thế nào?", "Bạn nộp yêu cầu xác nhận tình trạng hôn nhân tại cơ quan đăng ký hộ tịch có thẩm quyền. Nếu dữ liệu cư trú hoặc tình trạng hôn nhân chưa đủ, cơ quan tiếp nhận có thể yêu cầu thông tin, giấy tờ để xác minh.", "ho_tich_chung_thuc", ["Xác định mục đích xin xác nhận", "Chuẩn bị giấy tờ nhân thân và thông tin cư trú", "Nộp tờ khai tại Bộ phận Một cửa hoặc trực tuyến nếu thủ tục hỗ trợ", "Kiểm tra nội dung giấy xác nhận khi nhận kết quả"], ["4a9876e21d3d8c180b0a34fc"]),
    faq("faq_hp_054", "Đăng ký giám hộ cho trẻ em hoặc người mất năng lực hành vi cần làm gì?", "Người yêu cầu cần chứng minh nhân thân, nơi cư trú và căn cứ xác lập việc giám hộ. Tùy trường hợp là giám hộ đương nhiên hay được cử, cơ quan hộ tịch sẽ yêu cầu tài liệu tương ứng.", "ho_tich_chung_thuc", ["Xác định loại giám hộ và người giám hộ", "Chuẩn bị tờ khai cùng giấy tờ chứng minh điều kiện giám hộ", "Nộp hồ sơ tại UBND cấp xã có thẩm quyền", "Nhận trích lục hoặc thông báo bổ sung hồ sơ"], ["6399849585d6d785bd11ca0f"]),
    faq("faq_hp_055", "Muốn chấm dứt việc giám hộ phải nộp hồ sơ ở đâu?", "Bạn nộp hồ sơ đăng ký chấm dứt giám hộ tại UBND cấp xã có thẩm quyền đăng ký hộ tịch. Hồ sơ phải thể hiện việc giám hộ đã đăng ký và lý do chấm dứt.", "ho_tich_chung_thuc", ["Chuẩn bị tờ khai và giấy tờ liên quan đến việc giám hộ", "Nêu rõ lý do chấm dứt", "Nộp tại Bộ phận Một cửa UBND cấp xã", "Nhận kết quả hoặc bổ sung theo yêu cầu"], ["fc17b68e383ada3edaba4525"]),
    faq("faq_hp_056", "Nhận cha, mẹ, con khi cha mẹ chưa đăng ký kết hôn cần làm gì?", "Người yêu cầu cần chuẩn bị tờ khai và giấy tờ chứng minh quan hệ cha, mẹ, con theo trường hợp cụ thể. Cơ quan hộ tịch sẽ kiểm tra hồ sơ và hướng dẫn nếu cần xác minh hoặc giám định.", "ho_tich_chung_thuc", ["Chuẩn bị giấy tờ nhân thân và chứng cứ về quan hệ", "Điền tờ khai nhận cha, mẹ, con", "Nộp hồ sơ tại UBND cấp xã có thẩm quyền", "Nhận kết quả sau khi hồ sơ được kiểm tra"], ["b99f479f95fc4efaa98a02b4"]),
    faq("faq_hp_057", "Đăng ký tạm trú tại phường cần thực hiện như thế nào?", "Bạn chuẩn bị thông tin cư trú và giấy tờ chứng minh chỗ ở hợp pháp theo yêu cầu của cơ quan công an. Có thể thực hiện trên cổng dịch vụ công nếu thủ tục đang được cung cấp trực tuyến.", "cu_tru_an_ninh", ["Chuẩn bị thông tin cá nhân và chỗ ở", "Khai báo yêu cầu đăng ký tạm trú", "Nộp trực tuyến hoặc tại cơ quan tiếp nhận theo hướng dẫn", "Theo dõi và nhận kết quả trên hệ thống"], ["d14384374f14f83655d50b67"]),
    faq("faq_hp_058", "Xin xác nhận thông tin về cư trú thì dùng mẫu nào?", "Bạn có thể yêu cầu xác nhận thông tin cư trú theo mẫu và phương thức mà cơ quan công an hoặc cổng dịch vụ công đang cung cấp. Chỉ sử dụng mẫu CT02 đã được kiểm tra trong kho của hệ thống.", "cu_tru_an_ninh", ["Xác định nội dung cư trú cần xác nhận", "Chuẩn bị giấy tờ nhân thân", "Nộp yêu cầu theo kênh được hướng dẫn", "Kiểm tra thông tin trên kết quả xác nhận"], ["3f6656e2d37e1909dd1e74f4"]),
    faq("faq_hp_059", "Thay đổi thông tin cư trú trên hệ thống cần làm gì?", "Khi thông tin cư trú thay đổi, bạn cần khai báo thông tin mới và cung cấp tài liệu chứng minh nếu cơ quan tiếp nhận yêu cầu. Không nên tự dùng mẫu cũ nếu cổng dịch vụ công đã thay bằng biểu mẫu điện tử.", "cu_tru_an_ninh", ["Kiểm tra thông tin cần thay đổi", "Chuẩn bị giấy tờ chứng minh thay đổi", "Khai báo trên cổng dịch vụ công hoặc nộp theo hướng dẫn", "Theo dõi kết quả cập nhật"], ["d14384374f14f83655d50b67"]),
    faq("faq_hp_060", "Bị mất thẻ căn cước hoặc giấy tờ tùy thân phải báo ở đâu?", "Bạn cần liên hệ cơ quan công an hoặc điểm tiếp nhận căn cước được thông báo tại địa phương để khai báo và làm thủ tục cấp lại. Mang theo thông tin định danh còn nhớ hoặc giấy tờ khác để được tra cứu.", "cu_tru_an_ninh", []),
    faq("faq_hp_061", "Muốn khiếu nại quyết định xử phạt hành chính thì nộp đơn ở đâu?", "Thông thường, đơn khiếu nại lần đầu gửi đến người đã ra quyết định hoặc cơ quan có thẩm quyền giải quyết theo quy định. Cần kèm quyết định bị khiếu nại và tài liệu chứng minh; thẩm quyền cụ thể phải kiểm tra theo vụ việc.", "khieu_nai_to_cao_xu_phat", ["Đọc quyết định để xác định cơ quan và người ra quyết định", "Nêu rõ quyết định/hành vi bị khiếu nại và yêu cầu", "Kèm tài liệu, chứng cứ liên quan", "Nộp và giữ giấy tiếp nhận hoặc bằng chứng gửi đơn"], ["8700f1c936663ff169a6dbe1"]),
    faq("faq_hp_062", "Phân biệt khiếu nại, tố cáo và phản ánh như thế nào?", "Khiếu nại nhằm đề nghị xem xét lại quyết định hoặc hành vi ảnh hưởng trực tiếp đến quyền, lợi ích của người khiếu nại. Tố cáo phản ánh hành vi vi phạm pháp luật của cơ quan, tổ chức hoặc cá nhân. Phản ánh, kiến nghị là đề xuất xử lý hoặc cải thiện vấn đề; cơ quan tiếp nhận sẽ phân loại theo nội dung thực tế.", "khieu_nai_to_cao_xu_phat", ["Ghi đúng nội dung và mục đích gửi đơn", "Nêu họ tên, địa chỉ liên hệ nếu pháp luật yêu cầu", "Kèm tài liệu, chứng cứ đang có", "Gửi đúng cơ quan có thẩm quyền hoặc cơ quan tiếp nhận"], []),
    faq("faq_hp_063", "Đơn khiếu nại cần ghi những nội dung chính nào?", "Đơn nên ghi ngày tháng, cơ quan/người nhận, thông tin người khiếu nại, quyết định hoặc hành vi bị khiếu nại, nội dung và yêu cầu, tài liệu kèm theo. Không điền nội dung suy đoán hoặc thông tin không kiểm chứng.", "khieu_nai_to_cao_xu_phat", ["Ghi thông tin người khiếu nại và nơi nhận", "Mô tả quyết định hoặc hành vi bị khiếu nại", "Nêu yêu cầu giải quyết", "Ký tên và lưu bản sao hồ sơ"], ["8700f1c936663ff169a6dbe1"]),
    faq("faq_hp_064", "Phản ánh vi phạm trật tự xây dựng ở phường bằng cách nào?", "Bạn nên ghi rõ địa chỉ, thời điểm, nội dung vi phạm và gửi hình ảnh hoặc tài liệu nếu có. Có thể gửi tại UBND phường hoặc kênh tiếp nhận phản ánh chính thức của địa phương; cơ quan tiếp nhận sẽ phân loại và xử lý theo thẩm quyền.", "khieu_nai_to_cao_xu_phat", ["Ghi địa chỉ và mô tả hành vi", "Đính kèm hình ảnh/tài liệu nếu có", "Gửi đến UBND phường hoặc kênh phản ánh chính thức", "Lưu mã hoặc giấy tiếp nhận để theo dõi"], []),
    faq("faq_hp_065", "Khi bị lập biên bản vi phạm hành chính cần kiểm tra gì?", "Bạn cần kiểm tra thông tin cá nhân, thời gian, địa điểm, hành vi, ý kiến của mình và chữ ký trong biên bản. Nếu có ý kiến khác, ghi rõ trước khi ký; việc ký biên bản không đồng nghĩa với việc nhận mình có lỗi.", "khieu_nai_to_cao_xu_phat", ["Đọc toàn bộ biên bản", "Yêu cầu ghi ý kiến hoặc sửa thông tin sai", "Giữ bản sao hoặc ảnh chụp biên bản", "Theo dõi quyết định xử lý và thời hạn thực hiện"], []),
    faq("faq_hp_066", "Người cao tuổi hoặc người khuyết tật muốn hỏi trợ cấp xã hội ở đâu?", "Bạn liên hệ UBND phường nơi cư trú hoặc bộ phận phụ trách an sinh xã hội để được kiểm tra nhóm đối tượng, hồ sơ và mức hỗ trợ đang áp dụng. Không nên tự kết luận đủ điều kiện chỉ từ tuổi hoặc tình trạng sức khỏe.", "an_sinh_y_te_giao_duc", ["Chuẩn bị giấy tờ nhân thân và giấy tờ chứng minh hoàn cảnh", "Liên hệ UBND phường nơi cư trú", "Nộp hồ sơ theo danh mục được hướng dẫn", "Theo dõi kết quả và yêu cầu bổ sung nếu có"], []),
    faq("faq_hp_067", "Trẻ em dưới 6 tuổi đăng ký khai sinh và cấp thẻ bảo hiểm y tế thế nào?", "Bạn hỏi bộ phận hộ tịch về thủ tục liên thông đang áp dụng tại địa phương. Chuẩn bị giấy chứng sinh, giấy tờ của cha mẹ và thông tin cư trú; biểu mẫu chỉ được tải khi hồ sơ trong kho có file chính thức hợp lệ.", "an_sinh_y_te_giao_duc", ["Chuẩn bị giấy chứng sinh và giấy tờ cha mẹ", "Nộp yêu cầu liên thông tại nơi được hướng dẫn", "Kiểm tra thông tin trẻ trên kết quả", "Liên hệ cơ quan bảo hiểm nếu thẻ chưa được cập nhật"], []),
]


def main() -> int:
    data = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
    index = json.loads(FORMS_PATH.read_text(encoding="utf-8-sig"))
    valid_ids = {
        str(row.get("id"))
        for row in index.get("forms", [])
        if row.get("official_level") == "official"
        and (row.get("review_status") == "approved" or row.get("is_approved") is True)
        and any(str(row.get(k) or "").replace("\\", "/").startswith("data/uploads/forms/") for k in ("local_path", "priority_path", "source_package_path"))
    }
    faqs = data.get("faqs", [])
    repaired = 0
    for item in faqs:
        original = list(item.get("form_ids") or [])
        item["form_ids"] = [str(form_id) for form_id in original if str(form_id) in valid_ids]
        item["requires_forms"] = bool(item["form_ids"])
        if item["form_ids"] != original:
            repaired += 1
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing_ids = {str(item.get("id")) for item in faqs}
    added = 0
    for item in FAQ_ADDITIONS:
        item["form_ids"] = [form_id for form_id in item["form_ids"] if form_id in valid_ids]
        item["requires_forms"] = bool(item["form_ids"])
        if item["id"] not in existing_ids:
            now = datetime.now(timezone.utc).isoformat()
            item["created_at"] = now
            item["updated_at"] = now
            faqs.append(item)
            added += 1
    data["faqs"] = faqs
    data["version"] = int(data.get("version") or 1) + 1
    data["curation"] = {"updated_at": datetime.now(timezone.utc).isoformat(), "added": added, "repaired_form_references": repaired, "form_policy": "approved_official_local_file_only"}
    FAQ_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": len(faqs), "added": added, "repaired_form_references": repaired, "valid_form_ids": len(valid_ids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
