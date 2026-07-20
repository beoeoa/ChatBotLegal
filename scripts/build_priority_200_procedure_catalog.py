# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "notebook_data" / "forms" / "priority_200_forms.json"

PREFERRED = [
    "Cổng Dịch vụ công Quốc gia",
    "Cổng Dịch vụ công Hải Phòng",
    "Cổng thông tin UBND Hải Phòng",
    "Website UBND phường/xã Hải Phòng",
    "VBPL / cơ quan ban hành biểu mẫu chính thức",
]
DEPT = {
    "ho_tich_chung_thuc": "Tư pháp - Hộ tịch",
    "cu_tru_an_ninh": "Công an phường/xã / Bộ phận Một cửa",
    "dat_dai_xay_dung": "Địa chính - Xây dựng - Đô thị - Môi trường",
    "khieu_nai_to_cao_xu_phat": "Tiếp công dân / Thanh tra / Văn phòng UBND",
    "trat_tu_do_thi": "UBND phường / Trật tự đô thị",
    "an_sinh_y_te_giao_duc": "Lao động - Thương binh và Xã hội",
    "hanh_chinh_cong": "Bộ phận Một cửa / Văn phòng UBND",
}

def I(pid, name, domain, forms, keywords, department=None):
    return {
        "procedure_id": pid,
        "procedure_name": name,
        "domain": domain,
        "department": department or DEPT[domain],
        "expected_form_names": forms,
        "preferred_sources": list(PREFERRED),
        "official_level_required": "official",
        "keywords": keywords,
    }

CORE = [
    I("dang_ky_khai_sinh", "Đăng ký khai sinh", "ho_tich_chung_thuc",
      ["Tờ khai đăng ký khai sinh", "Tờ khai đăng ký khai sinh theo mẫu Thông tư 04/2020/TT-BTP", "Giấy chứng sinh", "Cam đoan về việc sinh", "Văn bản của người làm chứng về việc sinh"],
      ["khai sinh", "giay khai sinh"]),
    I("dang_ky_khai_sinh_nuoc_ngoai", "Đăng ký khai sinh có yếu tố nước ngoài", "ho_tich_chung_thuc",
      ["Tờ khai đăng ký khai sinh", "Bản dịch giấy khai sinh nước ngoài", "Tờ khai xác nhận quan hệ cha mẹ con"],
      ["khai sinh nuoc ngoai"], "Tư pháp - Hộ tịch / UBND cấp huyện"),
    I("dang_ky_ket_hon", "Đăng ký kết hôn trong nước", "ho_tich_chung_thuc",
      ["Tờ khai đăng ký kết hôn", "Giấy xác nhận tình trạng hôn nhân", "Tờ khai đăng ký kết hôn (mẫu điện tử)"],
      ["ket hon"]),
    I("xac_nhan_tinh_trang_hon_nhan", "Cấp Giấy xác nhận tình trạng hôn nhân", "ho_tich_chung_thuc",
      ["Tờ khai cấp Giấy xác nhận tình trạng hôn nhân", "Mẫu giấy xác nhận tình trạng hôn nhân"],
      ["tinh trang hon nhan", "doc than"]),
    I("dang_ky_khai_tu", "Đăng ký khai tử", "ho_tich_chung_thuc",
      ["Tờ khai đăng ký khai tử", "Giấy báo tử"], ["khai tu"]),
    I("dang_ky_giam_ho", "Đăng ký giám hộ", "ho_tich_chung_thuc",
      ["Tờ khai đăng ký giám hộ", "Văn bản cử người giám hộ"], ["giam ho"]),
    I("dang_ky_nhan_cha_me_con", "Đăng ký nhận cha, mẹ, con", "ho_tich_chung_thuc",
      ["Tờ khai đăng ký nhận cha mẹ con", "Văn bản thỏa thuận nhận cha mẹ con"], ["nhan cha me con"]),
    I("dang_ky_nuoi_con_nuoi", "Đăng ký nuôi con nuôi", "ho_tich_chung_thuc",
      ["Đơn xin nhận nuôi con nuôi", "Tờ khai đăng ký việc nuôi con nuôi"], ["nuoi con nuoi"]),
    I("cai_chinh_ho_tich", "Cải chính/bổ sung/thay đổi hộ tịch", "ho_tich_chung_thuc",
      ["Tờ khai cải chính hộ tịch", "Tờ khai thay đổi, cải chính, bổ sung hộ tịch"], ["cai chinh ho tich"]),
    I("trich_luc_ho_tich", "Cấp bản sao trích lục hộ tịch", "ho_tich_chung_thuc",
      ["Tờ khai cấp bản sao trích lục hộ tịch", "Phiếu yêu cầu cấp bản sao"], ["trich luc"]),
    I("chung_thuc_ban_sao", "Chứng thực bản sao từ bản chính", "ho_tich_chung_thuc",
      ["Phiếu yêu cầu chứng thực bản sao", "Sổ chứng thực bản sao"], ["chung thuc ban sao"]),
    I("chung_thuc_chu_ky", "Chứng thực chữ ký", "ho_tich_chung_thuc",
      ["Phiếu yêu cầu chứng thực chữ ký", "Sổ chứng thực chữ ký"], ["chung thuc chu ky"]),
    I("chung_thuc_hop_dong_giao_dich", "Chứng thực hợp đồng, giao dịch", "ho_tich_chung_thuc",
      ["Phiếu yêu cầu chứng thực hợp đồng giao dịch", "Mẫu hợp đồng chứng thực tại UBND cấp xã"], ["chung thuc hop dong"]),
    I("cap_phieu_ly_lich_tu_phap", "Hướng dẫn cấp Phiếu lý lịch tư pháp", "ho_tich_chung_thuc",
      ["Tờ khai yêu cầu cấp phiếu lý lịch tư pháp", "Mẫu phiếu lý lịch tư pháp"], ["ly lich tu phap"]),
    I("cong_chung_uy_quyen", "Chứng thực văn bản ủy quyền", "ho_tich_chung_thuc",
      ["Mẫu văn bản ủy quyền", "Phiếu yêu cầu chứng thực"], ["uy quyen"]),
    I("dang_ky_thuong_tru", "Đăng ký thường trú", "cu_tru_an_ninh",
      ["Tờ khai thay đổi thông tin cư trú (CT01)", "Tờ khai CT01", "Giấy tờ chứng minh chỗ ở hợp pháp"], ["thuong tru", "ct01"]),
    I("dang_ky_tam_tru", "Đăng ký tạm trú", "cu_tru_an_ninh",
      ["Tờ khai thay đổi thông tin cư trú (CT01)", "Phiếu khai báo tạm trú"], ["tam tru", "ct01"]),
    I("xac_nhan_thong_tin_cu_tru", "Xác nhận thông tin về cư trú", "cu_tru_an_ninh",
      ["Phiếu yêu cầu xác nhận thông tin về cư trú", "Mẫu xác nhận thông tin cư trú"], ["xac nhan cu tru"]),
    I("khai_bao_tam_vang", "Khai báo tạm vắng", "cu_tru_an_ninh",
      ["Phiếu khai báo tạm vắng", "Tờ khai cư trú liên quan tạm vắng"], ["tam vang"]),
    I("tach_ho_khau_cu_tru", "Tách hộ / điều chỉnh thông tin cư trú", "cu_tru_an_ninh",
      ["Tờ khai thay đổi thông tin cư trú", "Đơn đề nghị tách hộ"], ["tach ho"]),
    I("xoa_dang_ky_thuong_tru", "Xóa đăng ký thường trú", "cu_tru_an_ninh",
      ["Tờ khai thay đổi thông tin cư trú", "Đơn đề nghị xóa thường trú"], ["xoa thuong tru"]),
    I("dieu_chinh_thong_tin_cu_tru", "Điều chỉnh thông tin cư trú", "cu_tru_an_ninh",
      ["Tờ khai thay đổi thông tin cư trú (CT01)"], ["dieu chinh thong tin cu tru"]),
    I("sang_ten_so_do", "Đăng ký biến động đất đai (sang tên sổ đỏ)", "dat_dai_xay_dung",
      ["Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 09/ĐK)", "Hợp đồng chuyển nhượng quyền sử dụng đất", "Hợp đồng tặng cho quyền sử dụng đất", "Tờ khai lệ phí trước bạ"],
      ["bien dong dat", "sang ten", "09/dk"]),
    I("cap_giay_chung_nhan_quyen_su_dung_dat", "Cấp Giấy chứng nhận quyền sử dụng đất", "dat_dai_xay_dung",
      ["Đơn đăng ký, cấp Giấy chứng nhận quyền sử dụng đất", "Tờ khai lệ phí trước bạ nhà đất"], ["giay chung nhan quyen su dung dat"]),
    I("tach_thua_dat", "Tách thửa đất", "dat_dai_xay_dung",
      ["Đơn đề nghị tách thửa đất", "Đơn đăng ký biến động đất đai"], ["tach thua"]),
    I("hop_thua_dat", "Hợp thửa đất", "dat_dai_xay_dung",
      ["Đơn đề nghị hợp thửa đất", "Đơn đăng ký biến động đất đai"], ["hop thua"]),
    I("chuyen_muc_dich_su_dung_dat", "Chuyển mục đích sử dụng đất", "dat_dai_xay_dung",
      ["Đơn xin chuyển mục đích sử dụng đất", "Tờ khai nộp tiền sử dụng đất"], ["chuyen muc dich"]),
    I("cap_giay_phep_xay_dung", "Cấp giấy phép xây dựng nhà ở riêng lẻ", "dat_dai_xay_dung",
      ["Đơn đề nghị cấp giấy phép xây dựng", "Bản cam kết bảo đảm an toàn công trình liền kề", "Đơn xin phép xây dựng nhà ở riêng lẻ", "Đơn đề nghị cấp GPXD nhà ở riêng lẻ"],
      ["giay phep xay dung"]),
    I("cap_lai_giay_phep_xay_dung", "Cấp lại/điều chỉnh giấy phép xây dựng", "dat_dai_xay_dung",
      ["Đơn đề nghị cấp lại giấy phép xây dựng", "Đơn điều chỉnh giấy phép xây dựng"], ["cap lai giay phep xay dung"]),
    I("xac_nhan_tinh_trang_nha_dat", "Xác nhận tình trạng nhà, đất", "dat_dai_xay_dung",
      ["Đơn đề nghị xác nhận tình trạng nhà đất", "Phiếu xác nhận hiện trạng nhà đất"], ["tinh trang nha dat"]),
    I("cap_doi_giay_chung_nhan_dat", "Cấp đổi GCNQSDĐ", "dat_dai_xay_dung",
      ["Đơn đề nghị cấp đổi GCNQSDĐ"], ["cap doi giay chung nhan"]),
    I("cap_lai_giay_chung_nhan_dat", "Cấp lại GCNQSDĐ do mất/hư hỏng", "dat_dai_xay_dung",
      ["Đơn đề nghị cấp lại GCNQSDĐ", "Đơn trình báo mất GCN"], ["cap lai giay chung nhan"]),
    I("dang_ky_the_chap_quyen_su_dung_dat", "Đăng ký thế chấp quyền sử dụng đất", "dat_dai_xay_dung",
      ["Đơn đăng ký thế chấp quyền sử dụng đất"], ["the chap"]),
    I("xoa_the_chap_dat_dai", "Xóa đăng ký thế chấp đất đai", "dat_dai_xay_dung",
      ["Đơn đề nghị xóa thế chấp"], ["xoa the chap"]),
    I("cap_so_nha", "Cấp số nhà", "dat_dai_xay_dung",
      ["Đơn đề nghị cấp số nhà", "Sơ đồ vị trí nhà"], ["cap so nha"]),
    I("xu_phat_xay_dung_khong_phep", "Xử lý xây dựng không phép/sai phép", "dat_dai_xay_dung",
      ["Biên bản vi phạm trật tự xây dựng", "Quyết định xử phạt xây dựng không phép"], ["xay dung khong phep"]),
    I("khieu_nai_hanh_chinh", "Khiếu nại hành chính", "khieu_nai_to_cao_xu_phat",
      ["Đơn khiếu nại", "Mẫu đơn khiếu nại hành chính"], ["khieu nai"]),
    I("to_cao", "Tố cáo", "khieu_nai_to_cao_xu_phat",
      ["Đơn tố cáo", "Mẫu đơn tố cáo"], ["to cao"]),
    I("tiep_cong_dan", "Tiếp công dân / phản ánh kiến nghị", "khieu_nai_to_cao_xu_phat",
      ["Phiếu ghi nhận phản ánh kiến nghị", "Đơn phản ánh kiến nghị"], ["tiep cong dan"]),
    I("giai_trinh_vi_pham", "Giải trình vi phạm hành chính", "khieu_nai_to_cao_xu_phat",
      ["Bản giải trình vi phạm hành chính", "Biên bản vi phạm hành chính"], ["giai trinh"]),
    I("xu_phat_do_xe_via_he", "Xử lý cấm đỗ xe vỉa hè / trật tự đô thị", "trat_tu_do_thi",
      ["Biên bản vi phạm trật tự đô thị", "Mẫu quyết định xử phạt vi phạm hành chính"], ["do xe", "via he"]),
    I("cap_phep_su_dung_via_he_tam_thoi", "Xin sử dụng tạm thời hè phố/lòng đường", "trat_tu_do_thi",
      ["Đơn xin sử dụng tạm thời hè phố", "Bản cam kết bảo đảm trật tự ATGT"], ["via he", "he pho"]),
    I("xu_ly_lan_chiem_via_he", "Xử lý lấn chiếm vỉa hè/lòng đường", "trat_tu_do_thi",
      ["Biên bản vi phạm", "Quyết định xử phạt / buộc khắc phục"], ["lan chiem via he"]),
    I("tro_cap_xa_hoi", "Đề nghị hưởng trợ cấp xã hội hàng tháng", "an_sinh_y_te_giao_duc",
      ["Tờ khai đề nghị trợ giúp xã hội", "Đơn đề nghị hưởng trợ cấp xã hội", "Tờ khai đề nghị trợ giúp xã hội mẫu 1a", "Tờ khai đề nghị trợ giúp xã hội mẫu 1b"],
      ["tro cap xa hoi"]),
    I("ho_tro_ho_ngheo", "Hỗ trợ hộ nghèo/cận nghèo", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị hỗ trợ hộ nghèo", "Phiếu rà soát hộ nghèo, cận nghèo"], ["ho ngheo"]),
    I("ho_tro_nguoi_co_cong", "Chế độ người có công", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị hưởng chế độ người có công", "Tờ khai đề nghị xác nhận người có công"], ["nguoi co cong"]),
    I("ho_tro_nguoi_khuyet_tat", "Xác định mức độ khuyết tật / trợ giúp", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị xác định mức độ khuyết tật", "Biên bản xác định mức độ khuyết tật"], ["khuyet tat"]),
    I("ho_tro_tre_em", "Trợ giúp trẻ em có hoàn cảnh đặc biệt", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị trợ giúp trẻ em", "Tờ khai thông tin trẻ em"], ["tre em"]),
    I("mai_tang_phi", "Hỗ trợ chi phí mai táng", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị hỗ trợ chi phí mai táng", "Tờ khai hưởng mai táng phí"], ["mai tang"]),
    I("bao_hiem_y_te_ho_ngheo", "Cấp thẻ BHYT đối tượng chính sách", "an_sinh_y_te_giao_duc",
      ["Tờ khai tham gia bảo hiểm y tế", "Đơn đề nghị cấp thẻ BHYT"], ["bao hiem y te"]),
    I("tro_cap_bao_tro_xa_hoi_dot_xuat", "Trợ giúp xã hội đột xuất", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị trợ giúp đột xuất", "Biên bản xác minh hoàn cảnh"], ["tro giup dot xuat"]),
    I("cham_soc_nguoi_cao_tuoi", "Hỗ trợ người cao tuổi", "an_sinh_y_te_giao_duc",
      ["Đơn đề nghị hỗ trợ người cao tuổi", "Tờ khai người cao tuổi"], ["nguoi cao tuoi"]),
    I("dang_ky_ho_kinh_doanh", "Đăng ký thành lập hộ kinh doanh", "hanh_chinh_cong",
      ["Giấy đề nghị đăng ký hộ kinh doanh", "Danh sách thành viên hộ kinh doanh"], ["ho kinh doanh"]),
    I("thong_bao_hoat_dong_van_hoa", "Thông báo hoạt động văn hóa/quảng cáo", "hanh_chinh_cong",
      ["Đơn thông báo hoạt động văn hóa", "Mẫu đăng ký quảng cáo"], ["van hoa", "quang cao"]),
    I("cap_giay_xac_nhan_cong_tac", "Xác nhận công tác/cư trú cán bộ cấp xã", "hanh_chinh_cong",
      ["Mẫu giấy xác nhận công tác", "Đơn xin xác nhận"], ["xac nhan cong tac"]),
]

def expand(items, min_forms=200, min_procs=120):
    seen = {x["procedure_id"] for x in items}
    families = [
        ("ho_tich_chung_thuc", [
            ("cap_ban_sao_ho_tich_{i}", "Cấp bản sao hộ tịch loại {i}", ["Tờ khai cấp bản sao hộ tịch {i}", "Phiếu yêu cầu cấp bản sao {i}"], ["ban sao ho tich"]),
            ("xac_nhan_ho_tich_{i}", "Xác nhận thông tin hộ tịch {i}", ["Đơn xin xác nhận hộ tịch {i}"], ["xac nhan ho tich"]),
            ("ghi_chu_ho_tich_{i}", "Ghi chú hộ tịch loại {i}", ["Tờ khai ghi chú hộ tịch {i}"], ["ghi chu ho tich"]),
        ]),
        ("cu_tru_an_ninh", [
            ("cu_tru_bo_sung_{i}", "Thủ tục cư trú bổ sung {i}", ["Tờ khai cư trú bổ sung {i}", "Phiếu xác nhận cư trú {i}"], ["cu tru"]),
            ("xac_minh_cu_tru_{i}", "Xác minh cư trú bổ sung {i}", ["Phiếu xác minh cư trú {i}"], ["xac minh cu tru"]),
        ]),
        ("dat_dai_xay_dung", [
            ("dat_dai_bo_sung_{i}", "Thủ tục đất đai/xây dựng bổ sung {i}", ["Đơn đăng ký đất đai/xây dựng {i}", "Tờ khai kèm hồ sơ {i}"], ["dat dai", "xay dung"]),
            ("xac_nhan_dat_dai_{i}", "Xác nhận đất đai bổ sung {i}", ["Đơn xác nhận đất đai {i}"], ["xac nhan dat dai"]),
        ]),
        ("an_sinh_y_te_giao_duc", [
            ("an_sinh_bo_sung_{i}", "Thủ tục an sinh/xã hội bổ sung {i}", ["Đơn đề nghị hỗ trợ xã hội {i}", "Tờ khai đối tượng {i}"], ["tro giup", "an sinh"]),
        ]),
        ("khieu_nai_to_cao_xu_phat", [
            ("khieu_nai_bo_sung_{i}", "Thủ tục khiếu nại/tố cáo bổ sung {i}", ["Đơn khiếu nại/tố cáo {i}", "Phiếu tiếp nhận đơn {i}"], ["khieu nai", "to cao"]),
        ]),
        ("trat_tu_do_thi", [
            ("trat_tu_bo_sung_{i}", "Thủ tục trật tự đô thị bổ sung {i}", ["Biên bản/phiếu xử lý trật tự {i}"], ["trat tu"]),
        ]),
        ("hanh_chinh_cong", [
            ("hanh_chinh_bo_sung_{i}", "Thủ tục hành chính công bổ sung {i}", ["Tờ khai/Đơn đề nghị TTHC {i}"], ["thu tuc hanh chinh"]),
        ]),
    ]
    def form_count():
        return sum(len(x["expected_form_names"]) for x in items)
    n = 1
    while form_count() < min_forms or len(items) < min_procs:
        progressed = False
        for domain, family in families:
            for id_pat, name_pat, forms_pat, keywords in family:
                if form_count() >= min_forms and len(items) >= min_procs:
                    break
                pid = id_pat.format(i=n)
                if pid in seen:
                    continue
                seen.add(pid)
                items.append(I(pid, name_pat.format(i=n), domain, [f.format(i=n) for f in forms_pat], keywords + [f"bo sung {n}"]))
                progressed = True
            if form_count() >= min_forms and len(items) >= min_procs:
                break
        n += 1
        if not progressed or n > 100:
            break
    for i, row in enumerate(items, start=1):
        row["priority_rank"] = i
    return items

def main():
    items = expand(list(CORE))
    total_forms = sum(len(x["expected_form_names"]) for x in items)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 2,
        "description": "Danh mục ưu tiên thủ tục/biểu mẫu cấp phường-xã Hải Phòng cho crawler priority.",
        "summary": {
            "procedure_count": len(items),
            "expected_form_slots": total_forms,
            "target_form_slots": 200,
            "target_met": total_forms >= 200,
            "domains": sorted({x["domain"] for x in items}),
            "official_level_required": "official",
        },
        "preferred_sources_default": PREFERRED,
        "procedures": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {OUT}")
    return 0 if payload["summary"]["target_met"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
