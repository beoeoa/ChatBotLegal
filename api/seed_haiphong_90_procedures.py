"""Full dataset of 90 authentic ward/commune administrative procedures for Hai Phong city.

10 procedures for each of the 9 domains across 4 active departments:
1. Phòng Kinh tế - Hạ tầng - Đô thị (org-dia-chinh-xay-dung):
   - dat_dai_xay_dung (10)
   - trat_tu_do_thi (10)
2. Trung tâm Phục vụ hành chính công (org-hanh-chinh-cong):
   - hanh_chinh_cong (10)
3. Phòng Văn hóa - Xã hội (org-van-hoa-xa-hoi):
   - an_sinh_y_te_giao_duc (10)
   - van_hoa_giao_duc_y_te (10)
4. Văn phòng HĐND và UBND (org-cong-an):
   - cu_tru_an_ninh (10)
   - ho_tich_chung_thuc (10)
   - khieu_nai_to_cao_xu_phat (10)
   - quoc_phong_quan_su (10)
"""

from typing import Any, Dict, List

HAI_PHONG_90_PROCEDURES: List[Dict[str, Any]] = [
    # =========================================================================
    # 1. PHÒNG KINH TẾ - HẠ TẦNG - ĐÔ THỊ (org-dia-chinh-xay-dung)
    # 1.1. Lĩnh vực: dat_dai_xay_dung (10 TTHC)
    # =========================================================================
    {
        "id": "sang_ten_so_do",
        "name": "Đăng ký biến động quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất (Sang tên sổ đỏ)",
        "department": "Địa chính - Xây dựng - Đô thị - Môi trường",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Hộ gia đình, cá nhân nộp hồ sơ tại Bộ phận Tiếp nhận và Trả kết quả cấp xã/Chi nhánh Văn phòng Đăng ký đất đai.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra tính hợp lệ của hồ sơ, viết phiếu hẹn trả kết quả và chuyển thông tin địa chính cho cơ quan thuế.",
            "Bước 3: Người sử dụng đất thực hiện nghĩa vụ tài chính theo thông báo nộp thuế, lệ phí của cơ quan thuế.",
            "Bước 4: Nhận Giấy chứng nhận đã đăng ký biến động hoặc Giấy chứng nhận mới tại Bộ phận Tiếp nhận và Trả kết quả."
        ],
        "documents_required": [
            "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 11/ĐK ban hành kèm Thông tư 10/2024/TT-BTNMT).",
            "Bản gốc Giấy chứng nhận quyền sử dụng đất đã cấp.",
            "Hợp đồng, văn bản về việc chuyển nhượng, tặng cho, thừa kế quyền sử dụng đất có công chứng, chứng thực.",
            "Bản sao Căn cước công dân của các bên tham gia giao dịch."
        ],
        "guidance": "Công dân chuẩn bị đầy đủ hợp đồng chuyển nhượng/tặng cho đã được công chứng/chứng thực trước khi nộp hồ sơ đăng ký biến động. Đảm bảo nộp đủ nghĩa vụ tài chính đúng thời hạn theo thông báo của cơ quan thuế.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã hoặc Chi nhánh Văn phòng Đăng ký đất đai cấp huyện.",
        "legal_basis": [
            "Luật Đất đai năm 2024",
            "Nghị định số 101/2024/NĐ-CP ngày 29/7/2024 của Chính phủ",
            "Thông tư số 10/2024/TT-BTNMT ngày 31/7/2024 của Bộ Tài nguyên và Môi trường"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "forms": [
            {
                "name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 09/ĐK)",
                "file_type": "docx",
                "download_url": "/api/procedures/sang_ten_so_do/forms/0",
                "official_level": "reference",
                "review_status": "candidate_pending_review",
            }
        ],
        "catalog_status": "approved",
    },
    {
        "id": "cap_gpxd_nha_o_rieng_le_do_thi",
        "name": "Cấp giấy phép xây dựng nhà ở riêng lẻ đô thị (Thẩm quyền cấp quận/huyện tiếp nhận hướng dẫn tại phường)",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Chủ đầu tư nộp 02 bộ hồ sơ đề nghị cấp giấy phép xây dựng tại Bộ phận Một cửa UBND cấp xã/phường để được hướng dẫn, chuyển giao quận/huyện.",
            "Bước 2: Bộ phận tiếp nhận kiểm tra hồ sơ; nếu đủ điều kiện cấp giấy biên nhận, nếu chưa hợp lệ hướng dẫn hoàn thiện hồ sơ.",
            "Bước 3: Phòng chuyên môn thụ lý hồ sơ, kiểm tra thực địa, đối chiếu quy hoạch phân khu và quy chế quản lý kiến trúc đô thị.",
            "Bước 4: Chủ đầu tư nhận Giấy phép xây dựng kèm hồ sơ thiết kế đã đóng dấu tại Bộ phận tiếp nhận và trả kết quả."
        ],
        "documents_required": [
            "Đơn đề nghị cấp giấy phép xây dựng theo Mẫu số 01 Phụ lục II ban hành kèm Nghị định số 15/2021/NĐ-CP.",
            "Bản sao một trong những giấy tờ chứng minh quyền sử dụng đất theo quy định của pháp luật về đất đai.",
            "02 bộ bản vẽ thiết kế xây dựng kèm theo Giấy chứng nhận đăng ký kinh doanh/chứng chỉ hành nghề của tổ chức, cá nhân thiết kế.",
            "Bản cam kết bảo đảm an toàn đối với công trình liền kề (trường hợp có công trình liền kề)."
        ],
        "guidance": "Bản vẽ thiết kế cần thể hiện rõ mặt bằng công trình trên lô đất tỷ lệ 1/50 - 1/500, mặt bằng các tầng, mặt đứng và mặt cắt chính tỷ lệ 1/100, sơ đồ đấu nối hạ tầng kỹ thuật.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã/phường (hướng dẫn tiếp nhận ban đầu) hoặc Bộ phận Một cửa UBND cấp quận/huyện.",
        "legal_basis": [
            "Luật Xây dựng năm 2014 và Luật Xây dựng sửa đổi năm 2020",
            "Nghị định số 15/2021/NĐ-CP ngày 03/3/2021 của Chính phủ quy định chi tiết một số nội dung về quản lý dự án đầu tư xây dựng",
            "Nghị định số 35/2023/NĐ-CP ngày 20/6/2023 của Chính phủ"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "50.000 VNĐ / giấy phép.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_gpxd_moi_cong_trinh_cap3_cap4",
        "name": "Cấp giấy phép xây dựng mới đối với công trình cấp III, cấp IV và nhà ở riêng lẻ",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Chủ đầu tư nộp 02 bộ hồ sơ đề nghị cấp giấy phép xây dựng trực tiếp tại Bộ phận Một cửa hoặc nộp trực tuyến qua Cổng Dịch vụ công.",
            "Bước 2: Công chức tiếp nhận kiểm tra hồ sơ, trao giấy tiếp nhận hồ sơ và hẹn ngày trả kết quả.",
            "Bước 3: Phòng Kinh tế - Hạ tầng - Đô thị thẩm định hồ sơ, kiểm tra điều kiện cấp phép tại thực địa trong thời hạn quy định.",
            "Bước 4: Người nộp hồ sơ nhận kết quả Giấy phép xây dựng kèm theo hồ sơ thiết kế đã được phê duyệt và đóng dấu."
        ],
        "documents_required": [
            "Đơn đề nghị cấp giấy phép xây dựng mới theo mẫu quy định.",
            "Bản sao giấy tờ chứng minh quyền sử dụng đất hợp pháp.",
            "02 bộ bản vẽ thiết kế xây dựng triển khai sau thiết kế cơ sở được duyệt theo quy định của pháp luật về xây dựng.",
            "Văn bản chấp thuận biện pháp thi công móng đảm bảo an toàn cho công trình lân cận (nếu có)."
        ],
        "guidance": "Công trình đề nghị cấp phép phải phù hợp với quy hoạch chi tiết xây dựng đã được phê duyệt và quy chuẩn kỹ thuật quốc gia về quy hoạch xây dựng.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả (Một cửa) cấp xã/phường hoặc Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Luật Xây dựng năm 2014, sửa đổi bổ sung năm 2020",
            "Nghị định số 15/2021/NĐ-CP của Chính phủ",
            "Quyết định phê duyệt quy trình nội bộ TTHC ngành xây dựng TP Hải Phòng"
        ],
        "duration": "15 ngày làm việc đối với nhà ở riêng lẻ; 20 ngày làm việc đối với công trình cấp III, cấp IV.",
        "fee": "50.000 VNĐ đối với nhà ở riêng lẻ; 100.000 VNĐ đối với công trình khác.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_gpxd_sua_chua_cai_tao",
        "name": "Cấp giấy phép xây dựng sửa chữa, cải tạo đối với công trình cấp III, cấp IV và nhà ở riêng lẻ",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Chủ đầu tư nộp hồ sơ đề nghị cấp phép sửa chữa, cải tạo tại Bộ phận Một cửa.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra tính đầy đủ của hồ sơ, viết giấy hẹn trả kết quả.",
            "Bước 3: Cơ quan chuyên môn kiểm tra hiện trạng công trình, đối chiếu quy chuẩn xây dựng và an toàn kết cấu.",
            "Bước 4: Trả kết quả Giấy phép xây dựng sửa chữa, cải tạo tại Bộ phận Một cửa."
        ],
        "documents_required": [
            "Đơn đề nghị cấp giấy phép sửa chữa, cải tạo công trình/nhà ở riêng lẻ.",
            "Bản sao giấy tờ chứng minh về quyền sở hữu công trình hoặc quyền sử dụng đất.",
            "Bản vẽ hiện trạng của bộ phận, hạng mục công trình sửa chữa, cải tạo và bản vẽ thiết kế cải tạo.",
            "Ảnh chụp hiện trạng công trình (kích thước tối thiểu 9x12cm) trước khi sửa chữa."
        ],
        "guidance": "Việc sửa chữa, cải tạo không được làm thay đổi kiến trúc mặt ngoài tiếp giáp với đường trong đô thị có yêu cầu về quản lý kiến trúc, không làm ảnh hưởng đến kết cấu chịu lực của công trình.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả Một cửa cấp xã hoặc cấp quận/huyện theo phân cấp.",
        "legal_basis": [
            "Luật Xây dựng năm 2014 và Luật Xây dựng sửa đổi năm 2020",
            "Nghị định số 15/2021/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "50.000 VNĐ / giấy phép.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_dieu_chinh_gpxd",
        "name": "Cấp điều chỉnh giấy phép xây dựng đối với công trình cấp III, cấp IV và nhà ở riêng lẻ",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Chủ đầu tư nộp hồ sơ đề nghị điều chỉnh giấy phép xây dựng trước khi thi công xây dựng theo nội dung điều chỉnh.",
            "Bước 2: Cán bộ Một cửa tiếp nhận hồ sơ, vào sổ theo dõi và gửi giấy tiếp nhận hồ sơ.",
            "Bước 3: Phòng Kinh tế - Hạ tầng - Đô thị xem xét nội dung điều chỉnh về quy mô, kiến trúc, kết cấu công trình.",
            "Bước 4: Nhận Giấy phép xây dựng điều chỉnh hoặc văn bản trả lời nếu không đủ điều kiện."
        ],
        "documents_required": [
            "Đơn đề nghị điều chỉnh giấy phép xây dựng theo mẫu.",
            "Bản chính Giấy phép xây dựng đã được cấp.",
            "02 bộ bản vẽ thiết kế xây dựng trong hồ sơ thiết kế điều chỉnh theo quy định.",
            "Báo cáo kết quả thẩm tra thiết kế điều chỉnh của tổ chức tư vấn đủ điều kiện năng lực (nếu có)."
        ],
        "guidance": "Thực hiện thủ tục điều chỉnh khi có thay đổi về quy mô công trình, vị trí xây dựng, cốt nền, hoặc thay đổi kiến trúc mặt ngoài đối với công trình trong đô thị.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả (Một cửa) cấp có thẩm quyền đã cấp giấy phép ban đầu.",
        "legal_basis": [
            "Luật Xây dựng năm 2014, Luật số 62/2020/QH14",
            "Nghị định số 15/2021/NĐ-CP ngày 03/3/2021 của Chính phủ"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "50.000 VNĐ / lần cấp điều chỉnh.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "gia_han_gpxd",
        "name": "Gia hạn giấy phép xây dựng đối với công trình cấp III, cấp IV và nhà ở riêng lẻ",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Trước thời điểm giấy phép xây dựng hết hiệu lực khởi công 30 ngày, chủ đầu tư nộp hồ sơ đề nghị gia hạn.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra hồ sơ, cấp phiếu tiếp nhận và hẹn trả kết quả.",
            "Bước 3: Cơ quan cấp phép kiểm tra thời hiệu của giấy phép và lý do gia hạn.",
            "Bước 4: Nhận kết quả xác nhận gia hạn giấy phép xây dựng (mỗi giấy phép được gia hạn tối đa 02 lần, mỗi lần 12 tháng)."
        ],
        "documents_required": [
            "Đơn đề nghị gia hạn giấy phép xây dựng theo mẫu quy định.",
            "Bản chính Giấy phép xây dựng đã được cấp."
        ],
        "guidance": "Công dân cần lưu ý nộp hồ sơ trước khi hết hạn giấy phép tối thiểu 30 ngày. Nếu giấy phép đã hết hiệu lực thì phải làm thủ tục cấp phép xây dựng mới.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả nơi đã cấp Giấy phép xây dựng.",
        "legal_basis": [
            "Luật Xây dựng năm 2014, sửa đổi năm 2020",
            "Nghị định số 15/2021/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "15.000 VNĐ / lần gia hạn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_lai_gpxd",
        "name": "Cấp lại giấy phép xây dựng đối với công trình cấp III, cấp IV và nhà ở riêng lẻ",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Chủ đầu tư nộp đơn đề nghị cấp lại giấy phép xây dựng trong trường hợp bị rách, nát hoặc bị mất.",
            "Bước 2: Bộ phận Một cửa tiếp nhận hồ sơ, đối chiếu hồ sơ lưu trữ tại cơ quan cấp phép.",
            "Bước 3: Thẩm định hồ sơ và lập quyết định cấp lại bản sao/bản chính giấy phép xây dựng theo quy định.",
            "Bước 4: Nhận Giấy phép xây dựng được cấp lại tại Bộ phận Một cửa."
        ],
        "documents_required": [
            "Đơn đề nghị cấp lại giấy phép xây dựng, trong đó giải trình rõ lý do bị rách, nát hoặc mất.",
            "Bản chính giấy phép xây dựng bị rách, nát (trường hợp bị rách, nát)."
        ],
        "guidance": "Trường hợp bị mất giấy phép, đơn đề nghị cấp lại cần nêu rõ quá trình quản lý, sử dụng và cam kết chịu trách nhiệm trước pháp luật về việc làm mất.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả cơ quan đã cấp Giấy phép xây dựng.",
        "legal_basis": [
            "Luật Xây dựng năm 2014, Luật Xây dựng sửa đổi năm 2020",
            "Nghị định số 15/2021/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "15.000 VNĐ / lần cấp lại.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cung_cap_thong_tin_quy_hoach_xay_dung",
        "name": "Cung cấp thông tin về quy hoạch xây dựng thuộc thẩm quyền của UBND cấp xã",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Cơ quan, tổ chức, cá nhân nộp phiếu yêu cầu cung cấp thông tin quy hoạch tại UBND cấp xã.",
            "Bước 2: Cán bộ địa chính - xây dựng kiểm tra vị trí khu đất trên bản đồ quy hoạch chi tiết xây dựng hoặc quy hoạch điểm dân cư nông thôn đã được phê duyệt.",
            "Bước 3: Soạn thảo văn bản cung cấp thông tin quy hoạch (chỉ giới đường đỏ, chỉ giới xây dựng, mật độ, tầng cao tối đa).",
            "Bước 4: Lãnh đạo UBND cấp xã ký văn bản và trả kết quả cho công dân."
        ],
        "documents_required": [
            "Phiếu yêu cầu cung cấp thông tin quy hoạch xây dựng (theo mẫu).",
            "Bản sao Giấy chứng nhận quyền sử dụng đất hoặc sơ đồ vị trí khu đất đề nghị cung cấp thông tin."
        ],
        "guidance": "Thông tin cung cấp chỉ có giá trị tham khảo về chỉ tiêu kiến trúc, quy hoạch xây dựng, không thay thế cho giấy phép xây dựng hoặc quyết định giao đất.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã/phường.",
        "legal_basis": [
            "Luật Xây dựng năm 2014 và Luật Quy hoạch đô thị năm 2009",
            "Luật sửa đổi, bổ sung một số điều của 37 luật có liên quan đến quy hoạch năm 2018",
            "Nghị định số 44/2015/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn phí cung cấp thông tin theo quy định hành chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cong_nhan_ban_quan_tri_nha_chung_cu",
        "name": "Công nhận Ban quản trị nhà chung cư",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Ban quản trị nhà chung cư nộp hồ sơ đề nghị công nhận tại UBND cấp quận/huyện hoặc cấp xã theo phân cấp trong thời hạn 10 ngày kể từ ngày được hội nghị bầu.",
            "Bước 2: Bộ phận tiếp nhận kiểm tra hồ sơ và bàn giao cho bộ phận chuyên môn quản lý đô thị.",
            "Bước 3: Cơ quan có thẩm quyền thẩm tra biên bản hội nghị nhà chung cư, quy chế hoạt động của Ban quản trị.",
            "Bước 4: Ban hành Quyết định công nhận Ban quản trị nhà chung cư để Ban quản trị mở tài khoản hoạt động và khắc con dấu."
        ],
        "documents_required": [
            "Văn bản đề nghị công nhận Ban quản trị của Trưởng ban quản trị.",
            "Biên bản cuộc họp Hội nghị nhà chung cư về việc bầu Ban quản trị.",
            "Danh sách các thành viên Ban quản trị (họ tên, căn cước công dân, chức danh).",
            "Quy chế hoạt động và Quy chế thu chi tài chính của Ban quản trị đã được Hội nghị nhà chung cư thông qua."
        ],
        "guidance": "Hội nghị nhà chung cư bầu Ban quản trị phải đáp ứng đầy đủ điều kiện về tỷ lệ đại diện chủ sở hữu tham dự theo quy định của Quy chế quản lý, sử dụng nhà chung cư.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp có thẩm quyền phân cấp quản lý chung cư trên địa bàn.",
        "legal_basis": [
            "Luật Nhà ở năm 2023",
            "Nghị định số 95/2024/NĐ-CP ngày 24/7/2024 của Chính phủ quy định chi tiết một số điều của Luật Nhà ở",
            "Thông tư số 05/2024/TT-BXD ngày 31/7/2024 của Bộ Xây dựng"
        ],
        "duration": "Không quá 07 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Không thu phí, lệ phí.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_bien_phap_bao_dam_dat_dai",
        "name": "Đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Người yêu cầu đăng ký (bên thế chấp hoặc bên nhận thế chấp) nộp hồ sơ tại Bộ phận Một cửa hoặc Chi nhánh Văn phòng Đăng ký đất đai.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra nội dung hợp đồng thế chấp và tính hợp lệ của hồ sơ.",
            "Bước 3: Ghi nội dung đăng ký thế chấp vào Sổ địa chính và Giấy chứng nhận quyền sử dụng đất.",
            "Bước 4: Trả kết quả Giấy chứng nhận đã được chứng nhận nội dung đăng ký thế chấp cho người yêu cầu đăng ký."
        ],
        "documents_required": [
            "Phiếu yêu cầu đăng ký biện pháp bảo đảm (01 bản chính theo mẫu).",
            "Hợp đồng thế chấp quyền sử dụng đất, tài sản gắn liền với đất đã được công chứng hoặc chứng thực (01 bản chính hoặc 01 bản sao có chứng thực).",
            "Bản chính Giấy chứng nhận quyền sử dụng đất, quyền sở hữu nhà ở và tài sản khác gắn liền với đất."
        ],
        "guidance": "Hồ sơ đăng ký thế chấp quyền sử dụng đất có thể nộp trực tiếp, gửi qua đường bưu điện hoặc nộp trực tuyến qua Cổng Dịch vụ công khi hệ thống đã kết nối.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã (tiếp nhận chuyển tiếp) hoặc Chi nhánh Văn phòng Đăng ký đất đai.",
        "legal_basis": [
            "Bộ luật Dân sự năm 2015",
            "Nghị định số 99/2022/NĐ-CP ngày 30/11/2022 của Chính phủ về đăng ký biện pháp bảo đảm",
            "Thông tư của Bộ Tư pháp hướng dẫn về đăng ký biện pháp bảo đảm"
        ],
        "duration": "Giải quyết trong ngày nhận hồ sơ hợp lệ; nếu nhận sau 15 giờ thì giải quyết trong ngày làm việc tiếp theo.",
        "fee": "80.000 VNĐ / hồ sơ.",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 1.2. Lĩnh vực: trat_tu_do_thi (10 TTHC)
    # =========================================================================
    {
        "id": "cap_phep_su_dung_tam_thoi_long_duong_via_he_dam_cuoi_tang",
        "name": "Cấp phép sử dụng tạm thời một phần lòng đường, hè phố để trông giữ xe phục vụ đám cưới, đám tang",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Hộ gia đình nộp đơn đề nghị sử dụng tạm thời hè phố tại Bộ phận Một cửa UBND cấp xã/phường trước khi tổ chức sự kiện.",
            "Bước 2: Cán bộ quản lý trật tự đô thị phối hợp Công an phường kiểm tra thực địa, khảo sát khả năng lưu thông của đoạn đường.",
            "Bước 3: Lãnh đạo UBND cấp xã ký Giấy phép sử dụng tạm thời một phần hè phố, lòng đường (quy định rõ phạm vi, thời gian, diện tích).",
            "Bước 4: Nhận Giấy phép và cam kết tự thu dọn, hoàn trả nguyên trạng mặt bằng ngay sau khi kết thúc việc cưới, việc tang."
        ],
        "documents_required": [
            "Đơn đề nghị sử dụng tạm thời một phần hè phố/lòng đường (theo mẫu quy định).",
            "Sơ đồ vị trí mặt bằng đề nghị sử dụng tạm thời (thể hiện rõ chiều rộng hè phố, khoảng cách dành cho người đi bộ tối thiểu 1.5m).",
            "Bản sao Căn cước công dân của người đại diện hộ gia đình."
        ],
        "guidance": "Việc sử dụng tạm thời hè phố phục vụ việc cưới không quá 48 giờ, việc tang không quá 72 giờ; bắt buộc phải dành tối thiểu 1.5m bề rộng hè phố thông thoáng cho người đi bộ.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND xã/phường nơi có hè phố đề nghị sử dụng.",
        "legal_basis": [
            "Luật Giao thông đường bộ năm 2008",
            "Nghị định số 100/2013/NĐ-CP của Chính phủ sửa đổi Nghị định 11/2010/NĐ-CP về quản lý và bảo vệ kết cấu hạ tầng giao thông đường bộ",
            "Quy chế quản lý trật tự đô thị, sử dụng lòng đường, hè phố trên địa bàn thành phố Hải Phòng"
        ],
        "duration": "Không quá 02 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn thu phí đối với hoạt động phục vụ việc tang; việc cưới thu theo quy định của HĐND thành phố.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_phep_su_dung_he_pho_chua_vat_lieu_xay_dung",
        "name": "Cấp phép sử dụng tạm thời một phần hè phố để chứa vật liệu xây dựng phục vụ sửa chữa, cải tạo công trình",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Tổ chức, cá nhân có công trình sửa chữa nộp hồ sơ đề nghị cấp phép tại UBND cấp xã/phường.",
            "Bước 2: Tổ Quản lý trật tự đô thị cấp xã kiểm tra hiện trường, phương án che chắn chống bụi bẩn và rác thải xây dựng.",
            "Bước 3: UBND cấp xã phê duyệt Giấy phép sử dụng tạm thời kèm phương án bảo đảm an toàn giao thông và vệ sinh môi trường.",
            "Bước 4: Nhận giấy phép và nộp phí sử dụng tạm thời lòng đường, hè phố theo quy định."
        ],
        "documents_required": [
            "Đơn đề nghị cấp phép sử dụng tạm thời hè phố để tập kết vật liệu.",
            "Bản sao Giấy phép xây dựng hoặc Giấy phép sửa chữa, cải tạo công trình được cấp có thẩm quyền phê duyệt.",
            "Phương án che chắn, bảo đảm trật tự an toàn giao thông và vệ sinh môi trường đô thị."
        ],
        "guidance": "Khu vực tập kết vật liệu phải có rào chắn, bạt che phủ kín chống bụi, không làm hư hại cây xanh đô thị, rãnh thoát nước và phải dọn dẹp sạch sẽ hàng ngày.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã/phường.",
        "legal_basis": [
            "Nghị định số 100/2013/NĐ-CP của Chính phủ",
            "Nghị quyết của HĐND thành phố Hải Phòng về mức thu phí sử dụng tạm thời lòng đường, hè phố"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Thu theo m2 sử dụng/tháng theo biểu mức phí của UBND thành phố Hải Phòng.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "gia_han_giay_phep_su_dung_long_duong_he_pho",
        "name": "Gia hạn giấy phép sử dụng tạm thời một phần lòng đường, hè phố vào mục đích khác",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Tổ chức, cá nhân nộp đơn đề nghị gia hạn giấy phép trước khi hết hạn tối thiểu 05 ngày.",
            "Bước 2: Cán bộ Một cửa tiếp nhận hồ sơ, kiểm tra việc chấp hành quy định trong thời gian sử dụng trước đó.",
            "Bước 3: Cơ quan chuyên môn kiểm tra thực tế, xem xét lý do gia hạn.",
            "Bước 4: Cấp văn bản gia hạn giấy phép sử dụng tạm thời lòng đường, hè phố."
        ],
        "documents_required": [
            "Đơn đề nghị gia hạn giấy phép sử dụng tạm thời lòng đường, hè phố.",
            "Bản chính Giấy phép sử dụng tạm thời lòng đường, hè phố đã được cấp trước đó."
        ],
        "guidance": "Nếu trong thời gian sử dụng trước đó có vi phạm về trật tự đô thị, bị lập biên bản nhắc nhở nhiều lần mà chưa khắc phục thì sẽ bị từ chối gia hạn.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả cơ quan đã cấp giấy phép ban đầu.",
        "legal_basis": [
            "Luật Giao thông đường bộ",
            "Quy chế quản lý trật tự đô thị thành phố Hải Phòng"
        ],
        "duration": "Không quá 02 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Thu phí gia hạn theo quy định.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_gpxd_thi_cong_cong_trinh_duong_bo_cap_xa",
        "name": "Cấp giấy phép thi công công trình trên đường bộ đang khai thác thuộc thẩm quyền quản lý cấp xã",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Chủ đầu tư công trình nộp hồ sơ đề nghị cấp phép thi công tại UBND cấp xã quản lý tuyến đường giao thông.",
            "Bước 2: Bộ phận tiếp nhận kiểm tra hồ sơ và bàn giao cho bộ phận phụ trách giao thông, hạ tầng kỹ thuật đô thị.",
            "Bước 3: Khảo sát hiện trường, kiểm tra phương án tổ chức giao thông và biện pháp bảo đảm an toàn khi thi công.",
            "Bước 4: Trả Giấy phép thi công công trình trên đường bộ đang khai thác."
        ],
        "documents_required": [
            "Đơn đề nghị cấp giấy phép thi công công trình đường bộ (theo mẫu).",
            "Văn bản chấp thuận thiết kế hoặc phương án thi công của cấp có thẩm quyền.",
            "Phương án tổ chức bảo đảm an toàn giao thông, biển cảnh báo, đèn tín hiệu ban đêm khi thi công.",
            "Bản cam kết hoàn trả mặt bằng đường giao thông đúng tiêu chuẩn kỹ thuật sau khi thi công xong."
        ],
        "guidance": "Khi thi công phải có biển báo công trường, rào chắn và người hướng dẫn điều tiết giao thông trong suốt quá trình đào đắp, hoàn thiện mặt đường.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã quản lý tuyến đường.",
        "legal_basis": [
            "Luật Giao thông đường bộ năm 2008",
            "Thông tư số 50/2015/TT-BGTVT và Thông tư số 35/2017/TT-BGTVT của Bộ Giao thông vận tải"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Không thu lệ phí cấp phép thi công.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_phuong_tien_vui_choi_giai_tri_duoi_nuoc_lan_dau",
        "name": "Đăng ký phương tiện hoạt động vui chơi, giải trí dưới nước lần đầu tại địa bàn xã, phường",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Chủ phương tiện nộp 01 bộ hồ sơ đề nghị đăng ký phương tiện tại UBND cấp xã nơi có vùng nước hoạt động.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra tính hợp lệ của hồ sơ đăng ký.",
            "Bước 3: Kiểm tra thông số kỹ thuật, giấy chứng nhận an toàn kỹ thuật phương tiện thủy nội địa/thiết bị vui chơi dưới nước.",
            "Bước 4: Cấp Giấy chứng nhận đăng ký phương tiện hoạt động vui chơi, giải trí dưới nước."
        ],
        "documents_required": [
            "Đơn đề nghị đăng ký phương tiện hoạt động vui chơi, giải trí dưới nước (theo mẫu).",
            "Bản sao giấy tờ hợp pháp về quyền sở hữu phương tiện (hóa đơn mua bán, hợp đồng tặng cho, thừa kế).",
            "Bản sao Giấy chứng nhận an toàn kỹ thuật và bảo vệ môi trường của phương tiện (đối với phương tiện thuộc diện đăng kiểm).",
            "Ảnh chụp phương tiện nhìn từ hai bên mạn (kích thước 10x15cm)."
        ],
        "guidance": "Phương tiện đưa vào hoạt động phải trang bị đầy đủ áo phao, thiết bị cứu sinh cứu đắm theo đúng quy chuẩn an toàn đường thủy.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã có vùng hoạt động vui chơi giải trí dưới nước.",
        "legal_basis": [
            "Nghị định số 48/2019/NĐ-CP ngày 05/6/2019 của Chính phủ quy định về quản lý hoạt động của phương tiện phục vụ vui chơi, giải trí dưới nước",
            "Nghị định số 19/2024/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Theo mức thu lệ phí đăng ký phương tiện thủy nội địa của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_lai_phuong_tien_vui_choi_duoi_nuoc",
        "name": "Đăng ký lại phương tiện hoạt động vui chơi, giải trí dưới nước",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Chủ phương tiện nộp hồ sơ đăng ký lại khi có sự thay đổi về chủ sở hữu, tên phương tiện hoặc vùng hoạt động.",
            "Bước 2: Cán bộ tiếp nhận đối chiếu thông tin trong Sổ đăng ký phương tiện.",
            "Bước 3: Thẩm tra các giấy tờ chuyển quyền sở hữu hoặc giấy tờ cải hoán kỹ thuật.",
            "Bước 4: Thu hồi giấy chứng nhận cũ và cấp Giấy chứng nhận đăng ký mới cho chủ phương tiện."
        ],
        "documents_required": [
            "Đơn đề nghị đăng ký lại phương tiện (theo mẫu).",
            "Bản chính Giấy chứng nhận đăng ký phương tiện đã được cấp trước đó.",
            "Văn bản chứng minh chuyển quyền sở hữu hoặc văn bản chấp thuận thay đổi công năng, cải hoán."
        ],
        "guidance": "Trong thời hạn 30 ngày kể từ ngày chuyển quyền sở hữu, chủ mới của phương tiện phải làm thủ tục đăng ký lại theo quy định.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi phương tiện đăng ký.",
        "legal_basis": [
            "Nghị định số 48/2019/NĐ-CP của Chính phủ",
            "Thông tư của Bộ Giao thông vận tải hướng dẫn đăng ký phương tiện thủy"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Theo quy định của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_lai_giay_chung_nhan_phuong_tien_vui_choi_duoi_nuoc",
        "name": "Cấp lại Giấy chứng nhận đăng ký phương tiện hoạt động vui chơi, giải trí dưới nước",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Chủ phương tiện nộp đơn đề nghị cấp lại Giấy chứng nhận do bị mất hoặc hư hỏng.",
            "Bước 2: Cơ quan đăng ký kiểm tra hồ sơ lưu trữ và đối chiếu thông tin phương tiện.",
            "Bước 3: Lập hồ sơ cấp lại Giấy chứng nhận đăng ký phương tiện.",
            "Bước 4: Trao Giấy chứng nhận đăng ký phương tiện cấp lại cho công dân."
        ],
        "documents_required": [
            "Đơn đề nghị cấp lại Giấy chứng nhận đăng ký phương tiện (nêu rõ lý do mất, hỏng).",
            "Bản chính Giấy chứng nhận đăng ký phương tiện bị hư hỏng (trường hợp bị hỏng)."
        ],
        "guidance": "Trường hợp mất giấy chứng nhận, đơn đề nghị cần có cam đoan về tính trung thực và chịu trách nhiệm pháp lý nếu khai báo gian dối.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã quản lý phương tiện.",
        "legal_basis": [
            "Nghị định số 48/2019/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 02 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Theo quy định mức thu lệ phí cấp lại của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_phep_dao_duong_dao_he_pho_dau_noi_ha_tang",
        "name": "Cấp phép đào đường, đào hè phố để đấu nối kỹ thuật hạ tầng đô thị cấp xã/phường",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Tổ chức, hộ gia đình nộp hồ sơ xin phép đào đường/hè phố để đấu nối cấp điện, cấp thoát nước, viễn thông.",
            "Bước 2: Cán bộ địa chính - xây dựng - đô thị kiểm tra hiện trường hướng tuyến đấu nối ngầm.",
            "Bước 3: Thẩm định phương án thi công, biện pháp hạn chế ách tắc giao thông và phương án hoàn trả kết cấu áo đường/gạch lát vỉa hè.",
            "Bước 4: Lãnh đạo UBND cấp xã ký Giấy phép đào đường, hè phố."
        ],
        "documents_required": [
            "Đơn đề nghị cấp phép đào đường, hè phố (theo mẫu quy định).",
            "Bản vẽ sơ đồ hướng tuyến đấu nối công trình ngầm.",
            "Văn bản thỏa thuận đấu nối kỹ thuật của đơn vị quản lý chuyên ngành (Công ty Cấp nước, Điện lực, Viễn thông).",
            "Bản cam kết hoàn trả mặt bằng hè phố đúng kết cấu ban đầu trong thời hạn cam kết."
        ],
        "guidance": "Thời gian thi công đào đường, vỉa hè phải thực hiện vào khung giờ ít phương tiện qua lại (ưu tiên ban đêm trong khu vực đô thị đông dân cư).",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND xã/phường.",
        "legal_basis": [
            "Luật Giao thông đường bộ năm 2008",
            "Nghị định số 11/2010/NĐ-CP và Nghị định số 100/2013/NĐ-CP của Chính phủ",
            "Quy định của UBND thành phố Hải Phòng về quản lý, vận hành và khai thác hạ tầng kỹ thuật đô thị"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Không thu lệ phí; chủ đầu tư có trách nhiệm nộp tiền ký quỹ hoặc cam kết chi phí hoàn trả mặt bằng.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "kiem_tra_nghiem_thu_hoan_tra_mat_bang_he_pho",
        "name": "Kiểm tra, nghiệm thu hoàn trả mặt bằng hè phố, lòng đường sau khi thi công hạ tầng",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Sau khi hoàn thành việc đầm nén, thảm bê tông nhựa/lát lại gạch vỉa hè, đơn vị thi công gửi thông báo đề nghị nghiệm thu.",
            "Bước 2: Tổ công tác liên ngành của xã/phường (Địa chính - Đô thị, Công an xã, Tổ dân phố) kiểm tra hiện trường.",
            "Bước 3: Kiểm tra độ bằng phẳng, độ dốc thoát nước, chất lượng vật liệu hoàn trả và mức độ an toàn giao thông.",
            "Bước 4: Lập Biên bản nghiệm thu bàn giao mặt bằng hoàn trả và xác nhận hoàn thành nghĩa vụ."
        ],
        "documents_required": [
            "Văn bản thông báo hoàn thành thi công và đề nghị nghiệm thu hoàn trả mặt bằng.",
            "Bản sao Giấy phép đào đường, hè phố đã được cấp.",
            "Biên bản tự nghiệm thu chất lượng hoàn trả của đơn vị thi công kèm ảnh chụp hoàn thiện."
        ],
        "guidance": "Nếu chất lượng hoàn trả không đảm bảo (gồ ghề, lún nứt, dùng sai loại gạch vỉa hè), đơn vị thi công phải làm lại trong 24 giờ trước khi được nghiệm thu.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã.",
        "legal_basis": [
            "Luật Giao thông đường bộ",
            "Quy chuẩn kỹ thuật quốc gia về công trình hạ tầng kỹ thuật đô thị"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận thông báo hoàn thành.",
        "fee": "Không thu phí.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cam_ket_trat_tu_van_minh_do_thi_mat_duong",
        "name": "Đăng ký cam kết chấp hành quy định về trật tự văn minh đô thị, không lấn chiếm lòng lề đường",
        "department": "Phòng Kinh tế - Hạ tầng - Đô thị",
        "primary_organization_unit_id": "org-dia-chinh-xay-dung",
        "domain_slug": "trat_tu_do_thi",
        "steps": [
            "Bước 1: Hộ gia đình, cá nhân kinh doanh mặt phố nộp Bản cam kết trật tự đô thị tại UBND xã/phường hoặc ký kết tại đợt vận động của Tổ dân phố.",
            "Bước 2: Công chức Địa chính - Đô thị và Công an xã tiếp nhận, rà soát phạm vi vạch sơn chỉ giới hè phố được phép để xe máy.",
            "Bước 3: Xác nhận Bản cam kết và lưu trữ trong hồ sơ quản lý tuyến phố văn minh đô thị.",
            "Bước 4: Trao 01 bản cam kết có xác nhận của UBND cấp xã cho hộ kinh doanh niêm yết tại cơ sở."
        ],
        "documents_required": [
            "Bản cam kết chấp hành quy định trật tự đô thị, văn minh thương mại và vệ sinh môi trường (theo mẫu chuẩn của địa phương).",
            "Bản sao Giấy chứng nhận đăng ký hộ kinh doanh/doanh nghiệp (nếu có)."
        ],
        "guidance": "Các hộ kinh doanh cam kết: không bày bán hàng hóa tràn lan ngoài vạch sơn quy định, xếp xe máy của khách gọn gàng thành một hàng theo quy chuẩn, không lắp đặt mái che mái vẩy gây mất mỹ quan đô thị.",
        "submission_place": "Bộ phận Một cửa hoặc Tổ Quản lý trật tự đô thị UBND xã/phường.",
        "legal_basis": [
            "Quy định của UBND thành phố Hải Phòng về xây dựng nếp sống văn minh đô thị",
            "Nghị định số 144/2021/NĐ-CP của Chính phủ về xử phạt VPHC trong lĩnh vực ANTT, ATXH"
        ],
        "duration": "Giải quyết ngay trong ngày tiếp nhận.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 2. TRUNG TÂM PHỤC VỤ HÀNH CHÍNH CÔNG (org-hanh-chinh-cong)
    # 2.1. Lĩnh vực: hanh_chinh_cong (10 TTHC)
    # =========================================================================
    {
        "id": "dang_ky_thanh_lap_ho_kinh_doanh",
        "name": "Đăng ký thành lập Hộ kinh doanh cá thể",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Cá nhân hoặc người đại diện hộ gia đình nộp hồ sơ tại Bộ phận Một cửa / Trung tâm Phục vụ hành chính công hoặc nộp trực tuyến.",
            "Bước 2: Bộ phận tiếp nhận kiểm tra hồ sơ và cấp Giấy tiếp nhận hồ sơ và hẹn trả kết quả.",
            "Bước 3: Cơ quan đăng ký kinh doanh cấp huyện/xã kiểm tra điều kiện về tên hộ kinh doanh, ngành nghề đăng ký kinh doanh và thông tin kê khai thuế.",
            "Bước 4: Nhận Giấy chứng nhận đăng ký hộ kinh doanh tại Bộ phận Một cửa."
        ],
        "documents_required": [
            "Giấy đề nghị đăng ký hộ kinh doanh (theo mẫu ban hành kèm Thông tư số 02/2023/TT-BKHĐT).",
            "Bản sao Căn cước công dân của chủ hộ kinh doanh, các thành viên hộ gia đình tham gia đăng ký hộ kinh doanh.",
            "Bản sao biên bản họp thành viên hộ gia đình về việc thành lập hộ kinh doanh (nếu các thành viên cùng thành lập).",
            "Bản sao văn bản ủy quyền của các thành viên hộ gia đình cho một thành viên làm chủ hộ kinh doanh."
        ],
        "guidance": "Tên hộ kinh doanh bao gồm hai thành tố: Loại hình 'Hộ kinh doanh' và Tên riêng của hộ kinh doanh. Tên riêng không được trùng với tên riêng của hộ kinh doanh đã đăng ký trong phạm vi cấp huyện.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả (Một cửa) / Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Luật Doanh nghiệp năm 2020",
            "Nghị định số 01/2021/NĐ-CP ngày 04/01/2021 của Chính phủ về đăng ký doanh nghiệp",
            "Thông tư số 02/2023/TT-BKHĐT của Bộ Kế hoạch và Đầu tư"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "100.000 VNĐ / lần cấp.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_thay_doi_noi_dung_ho_kinh_doanh",
        "name": "Đăng ký thay đổi nội dung đăng ký Hộ kinh doanh",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Hộ kinh doanh nộp hồ sơ thông báo thay đổi nội dung đăng ký hộ kinh doanh trong thời hạn 10 ngày kể từ ngày có thay đổi.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra hồ sơ, trao giấy biên nhận.",
            "Bước 3: Thẩm tra các nội dung thay đổi (địa chỉ điểm kinh doanh, ngành nghề, vốn, thông tin người đại diện).",
            "Bước 4: Cấp Giấy chứng nhận đăng ký hộ kinh doanh mới ghi nhận nội dung thay đổi."
        ],
        "documents_required": [
            "Thông báo thay đổi nội dung đăng ký hộ kinh doanh do chủ hộ kinh doanh ký (theo mẫu quy định).",
            "Bản sao biên bản họp của các thành viên hộ gia đình về việc đăng ký thay đổi (nếu có).",
            "Bản sao Căn cước công dân mới (trường hợp thay đổi thông tin cá nhân của chủ hộ kinh doanh)."
        ],
        "guidance": "Trường hợp chuyển địa chỉ sang quận/huyện khác, hộ kinh doanh phải nộp hồ sơ đăng ký thay đổi địa chỉ tại cơ quan đăng ký kinh doanh nơi dự định đặt địa chỉ mới.",
        "submission_place": "Trung tâm Phục vụ hành chính công / Bộ phận Tiếp nhận và Trả kết quả Một cửa.",
        "legal_basis": [
            "Nghị định số 01/2021/NĐ-CP của Chính phủ",
            "Thông tư số 02/2023/TT-BKHĐT của Bộ Kế hoạch và Đầu tư"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận hồ sơ hợp lệ.",
        "fee": "100.000 VNĐ / lần thay đổi.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tam_ngung_kinh_doanh_ho_kinh_doanh",
        "name": "Tạm ngừng kinh doanh, tiếp tục kinh doanh trước thời hạn đối với Hộ kinh doanh",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Hộ kinh doanh gửi thông báo bằng văn bản cho cơ quan đăng ký kinh doanh ít nhất 03 ngày làm việc trước khi tạm ngừng hoặc tiếp tục kinh doanh trước thời hạn.",
            "Bước 2: Cán bộ Một cửa tiếp nhận thông báo, kiểm tra chữ ký và con dấu (nếu có).",
            "Bước 3: Nhập thông tin vào Hệ thống thông tin đăng ký hộ kinh doanh và chuyển dữ liệu sang cơ quan thuế.",
            "Bước 4: Cấp Giấy xác nhận về việc hộ kinh doanh đăng ký tạm ngừng kinh doanh/tiếp tục kinh doanh trước thời hạn."
        ],
        "documents_required": [
            "Thông báo về việc tạm ngừng kinh doanh/tiếp tục kinh doanh trước thời hạn đã thông báo của hộ kinh doanh (theo mẫu chuẩn).",
            "Bản sao biên bản họp của các thành viên hộ gia đình về việc tạm ngừng hoặc tiếp tục kinh doanh (nếu có)."
        ],
        "guidance": "Thời hạn tạm ngừng kinh doanh của mỗi lần thông báo không được quá 01 năm. Hộ kinh doanh phải thực hiện đầy đủ nghĩa vụ thuế phát sinh trước thời điểm tạm ngừng.",
        "submission_place": "Trung tâm Phục vụ hành chính công / Bộ phận Một cửa.",
        "legal_basis": [
            "Nghị định số 01/2021/NĐ-CP của Chính phủ",
            "Thông tư số 02/2023/TT-BKHĐT của Bộ Kế hoạch và Đầu tư"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cham_dut_hoat_dong_ho_kinh_doanh",
        "name": "Chấm dứt hoạt động Hộ kinh doanh",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Hộ kinh doanh hoàn thành nghĩa vụ thuế tại Chi cục Thuế và nộp hồ sơ chấm dứt hoạt động tại Bộ phận Một cửa.",
            "Bước 2: Tiếp nhận hồ sơ, kiểm tra văn bản xác nhận hoàn thành nghĩa vụ thuế của cơ quan thuế.",
            "Bước 3: Cơ quan đăng ký kinh doanh xóa tên hộ kinh doanh trong Hệ thống thông tin đăng ký hộ kinh doanh.",
            "Bước 4: Trả Thông báo về việc chấm dứt hoạt động hộ kinh doanh cho công dân."
        ],
        "documents_required": [
            "Thông báo về việc chấm dứt hoạt động hộ kinh doanh (do chủ hộ kinh doanh ký).",
            "Bản gốc Giấy chứng nhận đăng ký hộ kinh doanh đã được cấp.",
            "Văn bản xác nhận của cơ quan thuế về việc hộ kinh doanh đã hoàn thành nghĩa vụ nộp thuế và đóng mã số thuế."
        ],
        "guidance": "Chủ hộ kinh doanh chịu trách nhiệm thanh toán đầy đủ các khoản nợ, gồm cả nợ thuế và nghĩa vụ tài chính chưa thực hiện sau khi chấm dứt hoạt động.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả Một cửa / Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Nghị định số 01/2021/NĐ-CP ngày 04/01/2021 của Chính phủ",
            "Thông tư số 02/2023/TT-BKHĐT của Bộ Kế hoạch và Đầu tư"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_lai_giay_chung_nhan_ho_kinh_doanh",
        "name": "Cấp lại Giấy chứng nhận đăng ký Hộ kinh doanh",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Hộ kinh doanh nộp Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh do bị mất, cháy, rách nát.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra thông tin hộ kinh doanh trên Cơ sở dữ liệu đăng ký kinh doanh.",
            "Bước 3: In và trình lãnh đạo ký cấp lại Giấy chứng nhận đăng ký hộ kinh doanh.",
            "Bước 4: Trả Giấy chứng nhận đăng ký hộ kinh doanh cấp lại tại Bộ phận Một cửa."
        ],
        "documents_required": [
            "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh theo mẫu.",
            "Bản gốc Giấy chứng nhận đăng ký hộ kinh doanh bị rách nát (nếu có)."
        ],
        "guidance": "Giấy chứng nhận được cấp lại sẽ giữ nguyên mã số hộ kinh doanh và các thông tin đã đăng ký trước đó, có ghi rõ 'Cấp lại lần...' trên phôi giấy.",
        "submission_place": "Trung tâm Phục vụ hành chính công / Bộ phận Một cửa nơi cấp giấy ban đầu.",
        "legal_basis": [
            "Nghị định số 01/2021/NĐ-CP của Chính phủ",
            "Thông tư số 02/2023/TT-BKHĐT"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận hồ sơ.",
        "fee": "50.000 VNĐ / lần cấp lại.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_cap_tai_khoan_dvc_dinh_danh_dien_tu",
        "name": "Đăng ký cấp tài khoản và kích hoạt định danh điện tử, chữ ký số công dân tại Bộ phận Một cửa",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Công dân xuất trình Căn cước công dân gắn chip tại quầy hướng dẫn của Trung tâm Phục vụ hành chính công.",
            "Bước 2: Cán bộ Một cửa hỗ trợ quét thông tin thẻ CCCD, thu nhận số điện thoại chính chủ và địa chỉ email.",
            "Bước 3: Hướng dẫn công dân cài đặt ứng dụng VNeID, kích hoạt tài khoản định danh mức độ 2 và chữ ký số từ xa miễn phí.",
            "Bước 4: Hoàn thành đăng ký và hướng dẫn công dân cách nộp hồ sơ dịch vụ công trực tuyến."
        ],
        "documents_required": [
            "Thẻ Căn cước công dân gắn chip hoặc Căn cước (bản chính).",
            "Số điện thoại di động chính chủ đã đăng ký thông tin thuê bao với nhà mạng."
        ],
        "guidance": "Công dân nên chuẩn bị điện thoại thông minh kết nối internet để được cán bộ hỗ trợ cài đặt ứng dụng và kích hoạt chữ ký số số hóa hồ sơ tại chỗ.",
        "submission_place": "Bộ phận Hỗ trợ dịch vụ công trực tuyến - Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Luật Căn cước năm 2023",
            "Luật Giao dịch điện tử năm 2023",
            "Nghị định số 59/2022/NĐ-CP ngày 05/9/2022 của Chính phủ về định danh và xác thực điện tử",
            "Đề án 06/CP của Thủ tướng Chính phủ"
        ],
        "duration": "Giải quyết ngay tại chỗ (từ 10 đến 15 phút/lượt tiếp nhận).",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "chung_thuc_ban_sao_dien_tu_tu_ban_chinh",
        "name": "Chứng thực bản sao điện tử từ bản chính trên Cổng Dịch vụ công quốc gia",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Công dân nộp bản chính giấy tờ tại Bộ phận Một cửa hoặc yêu cầu cấp bản sao điện tử khi làm thủ tục chứng thực.",
            "Bước 2: Công chức Một cửa scan bản chính, nhập thông tin hồ sơ lên Hệ thống chứng thực điện tử của Cổng Dịch vụ công quốc gia.",
            "Bước 3: Người có thẩm quyền kiểm tra tính chính xác của bản scan so với bản chính và ký số chứng thực điện tử.",
            "Bước 4: Bản sao chứng thực điện tử được tự động gửi vào kho dữ liệu cá nhân của công dân trên Cổng Dịch vụ công quốc gia."
        ],
        "documents_required": [
            "Bản chính giấy tờ, văn bản do cơ quan, tổ chức có thẩm quyền của Việt Nam hoặc nước ngoài cấp hợp pháp.",
            "Tài khoản Cổng Dịch vụ công quốc gia/VNeID của người yêu cầu để nhận kết quả điện tử."
        ],
        "guidance": "Bản sao điện tử được ký số có giá trị sử dụng thay thế hoàn toàn bản chính trong các thủ tục hành chính trên môi trường điện tử mà không cần xuất trình bản giấy.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả (Một cửa) / Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Nghị định số 45/2020/NĐ-CP ngày 08/4/2020 của Chính phủ về thực hiện thủ tục hành chính trên môi trường điện tử",
            "Nghị định số 23/2015/NĐ-CP của Chính phủ về cấp bản sao từ sổ gốc, chứng thực bản sao từ bản chính"
        ],
        "duration": "Giải quyết ngay trong ngày làm việc; tối đa không quá 01 ngày làm việc.",
        "fee": "2.000 VNĐ / trang; từ trang thứ ba trở đi thu 1.000 VNĐ / trang, tối đa không quá 200.000 VNĐ / bản.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cung_cap_thong_tin_theo_yeu_cau_cong_dan",
        "name": "Cung cấp thông tin theo yêu cầu của công dân tại Trung tâm Phục vụ hành chính công",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Công dân nộp Phiếu yêu cầu cung cấp thông tin trực tiếp hoặc qua dịch vụ bưu chính đến Trung tâm Phục vụ hành chính công.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra tính hợp lệ của phiếu yêu cầu và ghi vào Sổ theo dõi cung cấp thông tin.",
            "Bước 3: Đơn vị quản lý thông tin tra cứu, sao chép hoặc trích lục dữ liệu thông tin được phép cung cấp theo quy định.",
            "Bước 4: Trả kết quả văn bản cung cấp thông tin hoặc thông báo lý do từ chối (nếu thuộc danh mục bí mật nhà nước)."
        ],
        "documents_required": [
            "Phiếu yêu cầu cung cấp thông tin theo Mẫu số 01 ban hành kèm Nghị định số 13/2018/NĐ-CP.",
            "Bản sao Căn cước công dân của người yêu cầu cung cấp thông tin."
        ],
        "guidance": "Thông tin yêu cầu cung cấp phải thuộc phạm vi thông tin do cơ quan nhà nước tạo ra hoặc nắm giữ mà pháp luật không cấm tiếp cận.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả / Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Luật Tiếp cận thông tin năm 2016",
            "Nghị định số 13/2018/NĐ-CP ngày 23/01/2018 của Chính phủ quy định chi tiết và biện pháp thi hành Luật Tiếp cận thông tin"
        ],
        "duration": "Không quá 03 ngày làm việc đối với thông tin có sẵn; không quá 10 ngày làm việc đối với thông tin phức tạp cần tập hợp.",
        "fee": "Miễn phí cung cấp thông tin; người yêu cầu trả chi phí in ấn, sao chụp theo quy định của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tiep_nhan_xu_ly_phan_anh_kien_nghi_tthc",
        "name": "Tiếp nhận, xử lý phản ánh, kiến nghị về thủ tục hành chính và hành vi hành chính",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Người dân, doanh nghiệp gửi phản ánh, kiến nghị qua Hệ thống tiếp nhận PAKN Cổng DVC quốc gia hoặc nộp phiếu tại Bộ phận Một cửa.",
            "Bước 2: Trung tâm Phục vụ hành chính công tiếp nhận, phân loại nội dung phản ánh thuộc thẩm quyền giải quyết.",
            "Bước 3: Chuyển cơ quan chuyên môn xác minh, kiểm tra hành vi hành chính hoặc quy định TTHC bị phản ánh.",
            "Bước 4: Ban hành văn bản trả lời công khai trên Cổng DVC và thông báo trực tiếp cho cá nhân, tổ chức phản ánh."
        ],
        "documents_required": [
            "Văn bản phản ánh, kiến nghị hoặc phiếu tiếp nhận PAKN (nêu rõ họ tên, địa chỉ, số điện thoại, nội dung phản ánh cụ thể).",
            "Bản sao giấy tờ, tài liệu, hình ảnh hoặc bằng chứng liên quan đến hành vi, quy định hành chính bị phản ánh (nếu có)."
        ],
        "guidance": "Nội dung phản ánh về sự không phù hợp, bất cập của quy định TTHC hoặc hành vi chậm trễ, gây phiền hà, sách nhiễu của cán bộ, công chức giải quyết TTHC.",
        "submission_place": "Hệ thống Phản ánh kiến nghị Cổng Dịch vụ công Quốc gia hoặc Quầy tiếp nhận PAKN tại Trung tâm Phục vụ hành chính công.",
        "legal_basis": [
            "Nghị định số 20/2008/NĐ-CP ngày 14/02/2008 của Chính phủ về tiếp nhận, xử lý phản ánh, kiến nghị của cá nhân, tổ chức về quy định hành chính",
            "Nghị định số 92/2017/NĐ-CP của Chính phủ",
            "Quyết định của UBND thành phố Hải Phòng về quy chế phối hợp xử lý phản ánh kiến nghị"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày tiếp nhận phản ánh, kiến nghị.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "chuyen_phat_ket_qua_tthc_qua_buu_chinh_cong_ich",
        "name": "Chuyển phát kết quả giải quyết thủ tục hành chính qua dịch vụ bưu chính công ích",
        "department": "Trung tâm Phục vụ hành chính công",
        "primary_organization_unit_id": "org-hanh-chinh-cong",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Khi nộp hồ sơ TTHC tại Bộ phận Một cửa hoặc trực tuyến, công dân tích chọn đăng ký nhận kết quả tại nhà qua bưu điện.",
            "Bước 2: Nhân viên Bưu điện tại Trung tâm Phục vụ hành chính công lập phiếu gửi, thu cước dịch vụ và cấp mã vận đơn theo dõi.",
            "Bước 3: Sau khi cơ quan nhà nước hoàn thành giải quyết TTHC, bàn giao kết quả bản gốc/giấy tờ kèm theo cho Bưu điện.",
            "Bước 4: Bưu tá phát chuyển kết quả tận tay công dân tại địa chỉ đã đăng ký, thu lại giấy hẹn trả kết quả."
        ],
        "documents_required": [
            "Phiếu đăng ký dịch vụ chuyển phát kết quả TTHC qua bưu chính công ích (điền thông tin người nhận, số điện thoại, địa chỉ nhận thư).",
            "Giấy tiếp nhận hồ sơ và hẹn trả kết quả."
        ],
        "guidance": "Khi nhận kết quả từ bưu tá, người nhận phải xuất trình Căn cước công dân hoặc giấy tờ tùy thân hợp pháp và ký nhận vào sổ phát bưu gửi.",
        "submission_place": "Quầy giao dịch Bưu điện đặt tại Trung tâm Phục vụ hành chính công / Bộ phận Một cửa.",
        "legal_basis": [
            "Quyết định số 45/2016/QĐ-TTg ngày 19/10/2016 của Thủ tướng Chính phủ về việc tiếp nhận hồ sơ, trả kết quả giải quyết thủ tục hành chính qua dịch vụ bưu chính công ích",
            "Thông tư của Bộ Thông tin và Truyền thông quy định về giá cước bưu chính công ích"
        ],
        "duration": "Nội thành: từ 01 đến 02 ngày làm việc; Ngoại thành, hải đảo: từ 02 đến 03 ngày làm việc sau khi nhận kết quả từ cơ quan giải quyết.",
        "fee": "Cước bưu chính theo quy định giá cước dịch vụ bưu chính công ích (khoảng 20.000 - 30.000 VNĐ/bưu gửi).",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 3. PHÒNG VĂN HÓA - XÃ HỘI (org-van-hoa-xa-hoi)
    # 3.1. Lĩnh vực: an_sinh_y_te_giao_duc (10 TTHC)
    # =========================================================================
    {
        "id": "tro_cap_xa_hoi_hang_thang",
        "name": "Thủ tục đề nghị hưởng trợ cấp xã hội hàng tháng (Cấp xã)",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Đối tượng hoặc người giám hộ nộp hồ sơ tại Bộ phận Một cửa UBND cấp xã/phường nơi cư trú.",
            "Bước 2: Công chức Văn hóa - Xã hội (LĐ-TB&XH) tiếp nhận hồ sơ, kiểm tra điều kiện tiêu chuẩn đối tượng bảo trợ xã hội.",
            "Bước 3: Hội đồng xét duyệt trợ cấp xã hội cấp xã họp xét duyệt, niêm yết công khai danh sách tại trụ sở UBND cấp xã trong 02 ngày.",
            "Bước 4: Chủ tịch UBND cấp xã ban hành văn bản đề nghị kèm hồ sơ trình Chủ tịch UBND cấp quận/huyện ra Quyết định trợ cấp hàng tháng."
        ],
        "documents_required": [
            "Tờ khai đề nghị trợ cấp xã hội hàng tháng theo Mẫu số 01 ban hành kèm Nghị định số 20/2021/NĐ-CP.",
            "Bản sao Giấy khai sinh đối với trẻ em, hoặc bản sao Giấy xác nhận mức độ khuyết tật, hoặc kết luận của cơ quan y tế.",
            "Bản sao Căn cước công dân của đối tượng hoặc người giám hộ."
        ],
        "guidance": "Áp dụng cho các nhóm đối tượng bảo trợ xã hội theo quy định tại Điều 5 Nghị định 20/2021/NĐ-CP (trẻ mồ côi, người khuyết tật nặng/đặc biệt nặng, người cao tuổi neo đơn nghèo...).",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi đối tượng cư trú.",
        "legal_basis": [
            "Nghị định số 20/2021/NĐ-CP ngày 15/3/2021 của Chính phủ quy định chính sách trợ giúp xã hội đối với đối tượng bảo trợ xã hội",
            "Thông tư số 02/2021/TT-BLĐTBXH của Bộ Lao động - Thương binh và Xã hội",
            "Nghị quyết của HĐND thành phố Hải Phòng quy định chính sách bảo trợ xã hội đặc thù"
        ],
        "duration": "Trong vòng 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "ho_tro_chi_phi_mai_tang_btxh",
        "name": "Thủ tục hỗ trợ chi phí mai táng cho đối tượng bảo trợ xã hội",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Cơ quan, tổ chức, gia đình hoặc cá nhân tổ chức mai táng nộp hồ sơ tại Bộ phận Một cửa UBND cấp xã.",
            "Bước 2: Công chức phụ trách công tác lao động - xã hội kiểm tra hồ sơ, đối chiếu danh sách đối tượng bảo trợ xã hội đang hưởng trợ cấp.",
            "Bước 3: Chủ tịch UBND cấp xã có văn bản đề nghị gửi Phòng Lao động - TB&XH cấp quận/huyện.",
            "Bước 4: Chủ tịch UBND cấp huyện ra Quyết định hỗ trợ chi phí mai táng và chi trả tiền hỗ trợ cho người mai táng."
        ],
        "documents_required": [
            "Tờ khai đề nghị hỗ trợ chi phí mai táng theo Mẫu số 04 kèm Nghị định 20/2021/NĐ-CP.",
            "Bản sao Giấy chứng tử của đối tượng bảo trợ xã hội đã chết.",
            "Bản sao Căn cước công dân của người đứng ra tổ chức mai táng."
        ],
        "guidance": "Mức hỗ trợ chi phí mai táng đối với đối tượng bảo trợ xã hội tối thiểu bằng 20 lần chuẩn trợ giúp xã hội theo quy định hiện hành.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi đối tượng cư trú trước khi chết.",
        "legal_basis": [
            "Nghị định số 20/2021/NĐ-CP của Chính phủ",
            "Thông tư số 02/2021/TT-BLĐTBXH của Bộ LĐ-TB&XH"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "xac_dinh_muc_do_khuyet_tat_cap_giay_chung_nhan",
        "name": "Xác định mức độ khuyết tật và cấp Giấy xác nhận mức độ khuyết tật",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Người khuyết tật hoặc người đại diện hợp pháp nộp hồ sơ đề nghị xác định mức độ khuyết tật tại UBND cấp xã.",
            "Bước 2: Trong thời hạn 20 ngày làm việc, Hội đồng xác định mức độ khuyết tật cấp xã tổ chức họp đánh giá, phỏng vấn thực tế.",
            "Bước 3: Lập hồ sơ, biên bản kết luận dạng khuyết tật và mức độ khuyết tật (đặc biệt nặng, nặng, nhẹ).",
            "Bước 4: Niêm yết công khai và cấp Giấy xác nhận mức độ khuyết tật cho người khuyết tật."
        ],
        "documents_required": [
            "Đơn đề nghị xác định, xác định lại mức độ khuyết tật và cấp Giấy xác nhận mức độ khuyết tật theo mẫu.",
            "Bản sao các giấy tờ y tế chứng minh về khuyết tật: bệnh án, giấy ra viện, kết luận của cơ sở y tế (nếu có).",
            "Bản sao Căn cước công dân hoặc Giấy khai sinh của người khuyết tật."
        ],
        "guidance": "Trường hợp Hội đồng không xác định được mức độ khuyết tật thì hướng dẫn chuyển Hội đồng giám định y khoa để giám định chuyên sâu.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã.",
        "legal_basis": [
            "Luật Người khuyết tật năm 2010",
            "Nghị định số 28/2012/NĐ-CP của Chính phủ",
            "Thông tư số 01/2019/TT-BLĐTBXH ngày 02/01/2019 của Bộ Lao động - Thương binh và Xã hội"
        ],
        "duration": "Không quá 30 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_the_bhyt_doi_tuong_chinh_sach_btxh",
        "name": "Cấp thẻ bảo hiểm y tế cho đối tượng bảo trợ xã hội và hộ nghèo, cận nghèo",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Người thuộc diện được ngân sách nhà nước đóng BHYT nộp hồ sơ tại UBND cấp xã.",
            "Bước 2: Cán bộ Văn hóa - Xã hội kiểm tra, rà soát danh sách phê duyệt hộ nghèo, cận nghèo hoặc quyết định hưởng trợ cấp xã hội.",
            "Bước 3: Lập danh sách đề nghị cấp thẻ BHYT gửi Phòng LĐ-TB&XH duyệt chuyển cơ quan Bảo hiểm xã hội quận/huyện.",
            "Bước 4: Cơ quan BHXH in thẻ (hoặc đồng bộ mã BHYT điện tử trên VNeID/VssID) và trả kết quả cho công dân."
        ],
        "documents_required": [
            "Tờ khai tham gia, điều chỉnh thông tin bảo hiểm y tế (Mẫu TK1-TS).",
            "Bản sao Quyết định công nhận hộ nghèo/cận nghèo hoặc Quyết định hưởng trợ cấp xã hội hàng tháng.",
            "Bản sao Căn cước công dân hoặc Giấy khai sinh."
        ],
        "guidance": "Hiện nay thẻ BHYT đã được tích hợp trực tiếp vào thẻ Căn cước công dân gắn chip và ứng dụng VNeID, công dân có thể sử dụng CCCD để đi khám chữa bệnh ngay khi thông tin được kích hoạt.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã hoặc thông qua cán bộ phụ trách lao động thương binh xã hội tại cơ sở.",
        "legal_basis": [
            "Luật Bảo hiểm y tế năm 2008, sửa đổi bổ sung năm 2014",
            "Nghị định số 146/2018/NĐ-CP và Nghị định số 75/2023/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày cơ quan BHXH nhận đủ danh sách hợp lệ.",
        "fee": "Miễn phí (ngân sách nhà nước đóng 100% hoặc hỗ trợ theo quy định).",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tro_cap_hang_thang_nguoi_cao_tuoi_khong_luong_huu",
        "name": "Trợ cấp xã hội hàng tháng cho người cao tuổi từ đủ 80 tuổi trở lên không có lương hưu",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Người cao tuổi hoặc thân nhân nộp hồ sơ tại Bộ phận Một cửa UBND cấp xã khi công dân đủ 80 tuổi.",
            "Bước 2: Cán bộ Văn hóa - Xã hội kiểm tra thông tin năm sinh và xác minh tình trạng không có lương hưu, trợ cấp BHXH hàng tháng.",
            "Bước 3: UBND cấp xã lập danh sách gửi Phòng LĐ-TB&XH thẩm tra trình UBND cấp huyện ra Quyết định trợ cấp.",
            "Bước 4: Chi trả trợ cấp xã hội hàng tháng và cấp thẻ BHYT người cao tuổi miễn phí."
        ],
        "documents_required": [
            "Tờ khai đề nghị trợ giúp xã hội cho người cao tuổi (theo mẫu Nghị định 20/2021/NĐ-CP).",
            "Bản sao Căn cước công dân của người cao tuổi (chứng minh độ tuổi từ đủ 80 tuổi trở lên).",
            "Bản cam kết không hưởng lương hưu, trợ cấp bảo hiểm xã hội hàng tháng."
        ],
        "guidance": "Thủ tục có thể nộp trước tháng sinh nhật tròn 80 tuổi của công dân 30 ngày để kịp thời hưởng trợ cấp ngay từ tháng đủ tuổi.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi người cao tuổi thường trú.",
        "legal_basis": [
            "Luật Người cao tuổi năm 2009",
            "Nghị định số 20/2021/NĐ-CP của Chính phủ",
            "Nghị quyết của HĐND thành phố Hải Phòng"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "ho_tro_khan_cap_thien_tai_hoa_hoan",
        "name": "Hỗ trợ khẩn cấp đối với hộ gia đình, cá nhân gặp khó khăn do thiên tai, hỏa hoạn, dịch bệnh",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Trưởng thôn/Tổ trưởng dân phố hoặc đại diện hộ gia đình bị nạn báo cáo ngay cho UBND cấp xã sau khi xảy ra sự cố.",
            "Bước 2: Ban Chỉ huy Phòng chống thiên tai và Tìm kiếm cứu nạn cấp xã phối hợp Công chức Văn hóa - Xã hội kiểm tra thực địa, đánh giá mức độ thiệt hại.",
            "Bước 3: Lập biên bản xác nhận thiệt hại về người, nhà ở bị đổ, sập, trôi, cháy hoàn toàn.",
            "Bước 4: Chủ tịch UBND cấp xã quyết định chi hỗ trợ khẩn cấp từ nguồn ngân sách địa phương hoặc quỹ cứu trợ trong vòng 24 - 48 giờ."
        ],
        "documents_required": [
            "Đơn đề nghị hỗ trợ khẩn cấp của hộ gia đình (hoặc biên bản xác nhận của Tổ dân phố/Thôn).",
            "Biên bản kiểm tra thực địa xác nhận mức độ thiệt hại của cơ quan chức năng cấp xã.",
            "Bản sao Căn cước công dân của chủ hộ (nếu còn lưu giữ được)."
        ],
        "guidance": "Thủ tục hỗ trợ khẩn cấp được ưu tiên giải quyết ngay tại chỗ để bảo đảm chỗ ở tạm thời, lương thực và nhu yếu phẩm thiết yếu cho người dân bị nạn.",
        "submission_place": "UBND cấp xã hoặc Ban Chỉ đạo cứu trợ khẩn cấp địa phương.",
        "legal_basis": [
            "Nghị định số 20/2021/NĐ-CP ngày 15/3/2021 của Chính phủ",
            "Nghị định số 66/2021/NĐ-CP của Chính phủ về phòng, chống thiên tai"
        ],
        "duration": "Trong vòng 02 đến 03 ngày kể từ khi xảy ra sự cố.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tiep_nhan_ho_so_mai_tang_phi_nguoi_co_cong",
        "name": "Tiếp nhận hồ sơ giải quyết chế độ trợ cấp mai táng cho thân nhân người có công với cách mạng",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Thân nhân người có công từ trần nộp hồ sơ tại Bộ phận Một cửa UBND cấp xã.",
            "Bước 2: Cán bộ chuyên trách người có công kiểm tra hồ sơ liệt sĩ, thương binh, bệnh binh, cựu chiến binh được tặng huân huy chương.",
            "Bước 3: UBND cấp xã xác nhận vào bản khai và chuyển hồ sơ về Phòng LĐ-TB&XH cấp huyện.",
            "Bước 4: Sở LĐ-TB&XH ra Quyết định hưởng trợ cấp mai táng và chi trả chế độ theo quy định."
        ],
        "documents_required": [
            "Bản khai đề nghị hưởng chế độ trợ cấp mai táng của thân nhân người có công.",
            "Bản sao Trích lục khai tử hoặc Giấy chứng tử của người có công.",
            "Bản sao hồ sơ chứng minh công lao người có công (Huân chương, Huy chương, Quyết định phục viên, thẻ thương bệnh binh...)."
        ],
        "guidance": "Hồ sơ cần nộp trong thời hạn 90 ngày kể từ ngày người có công từ trần để kịp thời giải quyết chế độ trợ cấp mai táng và tiền tuất một lần (nếu có).",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi người có công thường trú khi qua đời.",
        "legal_basis": [
            "Pháp lệnh Ưu đãi người có công với cách mạng năm 2020",
            "Nghị định số 131/2021/NĐ-CP ngày 30/12/2021 của Chính phủ quy định chi tiết và biện pháp thi hành Pháp lệnh Ưu đãi người có công"
        ],
        "duration": "Cấp xã: 03 ngày làm việc; Tổng thời gian toàn trình: không quá 12 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "ra_soat_cong_nhan_ho_ngheo_ho_can_ngheo_hang_nam",
        "name": "Rà soát, công nhận hộ nghèo, hộ cận nghèo định kỳ hoặc thường xuyên hàng năm",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Hộ gia đình có đơn đề nghị hoặc thuộc diện rà soát định kỳ hàng năm của Ban chỉ đạo giảm nghèo cấp xã.",
            "Bước 2: Rà soát viên thôn/tổ dân phố tiến hành thu thập thông tin, chấm điểm theo phiếu đánh giá chuẩn nghèo đa chiều.",
            "Bước 3: Tổ chức họp thôn/tổ dân phố để thống nhất danh sách hộ nghèo, hộ cận nghèo; niêm yết công khai tại nhà văn hóa trong 03 ngày.",
            "Bước 4: Chủ tịch UBND cấp xã ra Quyết định công nhận danh sách hộ nghèo, hộ cận nghèo và cấp Giấy chứng nhận hộ nghèo."
        ],
        "documents_required": [
            "Giấy đề nghị rà soát hộ nghèo, hộ cận nghèo (áp dụng cho trường hợp rà soát phát sinh trong năm).",
            "Phiếu khảo sát, xác định thông tin hộ gia đình theo chuẩn nghèo đa chiều (Mẫu B1/B2 theo Thông tư của Bộ LĐ-TB&XH)."
        ],
        "guidance": "Đánh giá dựa trên chuẩn nghèo đa chiều giai đoạn 2021 - 2025 gồm: tiêu chí về thu nhập và tiêu chí đo lường mức độ thiếu hụt các dịch vụ xã hội cơ bản (việc làm, y tế, giáo dục, nhà ở, nước sinh hoạt và vệ sinh, thông tin).",
        "submission_place": "Trưởng thôn/Tổ trưởng dân phố hoặc Bộ phận Một cửa UBND cấp xã.",
        "legal_basis": [
            "Nghị định số 07/2021/NĐ-CP ngày 27/01/2021 của Chính phủ quy định chuẩn nghèo đa chiều giai đoạn 2021 - 2025",
            "Quyết định số 24/2021/QĐ-TTg của Thủ tướng Chính phủ",
            "Thông tư số 07/2021/TT-BLĐTBXH của Bộ Lao động - Thương binh và Xã hội"
        ],
        "duration": "Rà soát thường xuyên: không quá 15 ngày làm việc; Rà soát định kỳ: theo kế hoạch hàng năm của UBND thành phố.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "vay_von_ho_tro_tao_viec_lam_tu_quy_quoc_gia",
        "name": "Vay vốn hỗ trợ tạo việc làm, duy trì và mở rộng việc làm từ Quỹ quốc gia về việc làm đối với người lao động",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Người lao động lập hồ sơ vay vốn nộp cho Tổ tiết kiệm và vay vốn hoặc UBND cấp xã nơi thực hiện dự án.",
            "Bước 2: UBND cấp xã kiểm tra, xác nhận về nơi cư trú và dự án tạo việc làm của người lao động trong thời hạn 02 ngày.",
            "Bước 3: Ngân hàng Chính sách xã hội quận/huyện thẩm định điều kiện vay vốn, tài sản bảo đảm (nếu có).",
            "Bước 4: Ngân hàng CSXH giải ngân vốn vay cho người lao động tại Điểm giao dịch xã theo lịch định kỳ."
        ],
        "documents_required": [
            "Giấy đề nghị vay vốn hỗ trợ tạo việc làm, duy trì và mở rộng việc làm theo Mẫu số 1a ban hành kèm Nghị định 74/2019/NĐ-CP.",
            "Bản sao Căn cước công dân của người vay vốn.",
            "Bản sao giấy tờ chứng minh đối tượng ưu tiên (người khuyết tật, người dân tộc thiểu số, thanh niên hoàn thành nghĩa vụ quân sự... nếu có)."
        ],
        "guidance": "Mức vay tối đa là 100 triệu đồng đối với một người lao động. Thời hạn vay vốn tối đa không quá 120 tháng với lãi suất ưu đãi theo quy định của Thủ tướng Chính phủ.",
        "submission_place": "Tổ Tiết kiệm và Vay vốn tại thôn/tổ dân phố hoặc Điểm giao dịch Ngân hàng Chính sách xã hội tại UBND cấp xã.",
        "legal_basis": [
            "Luật Việc làm năm 2013",
            "Nghị định số 61/2015/NĐ-CP và Nghị định số 74/2019/NĐ-CP của Chính phủ về chính sách hỗ trợ tạo việc làm",
            "Văn bản hướng dẫn nghiệp vụ của Ngân hàng Chính sách xã hội Việt Nam"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí nộp hồ sơ vay vốn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "ho_tro_dao_tao_nghe_cho_lao_dong_nong_thon",
        "name": "Hỗ trợ đào tạo nghề cho người lao động ở khu vực nông thôn, thanh niên tại địa phương",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Người lao động có nhu cầu học nghề đăng ký tại UBND cấp xã hoặc Trung tâm Giáo dục nghề nghiệp - GDTX.",
            "Bước 2: Cán bộ Văn hóa - Xã hội tổng hợp danh sách theo nghề đăng ký (nông nghiệp, phi nông nghiệp) và đối tượng ưu tiên.",
            "Bước 3: UBND cấp xã phê duyệt danh sách người đủ điều kiện và gửi cơ sở đào tạo nghề liên kết.",
            "Bước 4: Cơ sở đào tạo mở lớp học nghề tại địa phương và chi trả hỗ trợ tiền ăn, tiền đi lại cho học viên theo quy định."
        ],
        "documents_required": [
            "Đơn đăng ký học nghề trình độ sơ cấp hoặc đào tạo dưới 03 tháng (theo mẫu).",
            "Bản sao Căn cước công dân của người lao động.",
            "Bản sao Giấy chứng nhận hộ nghèo, cận nghèo hoặc giấy tờ chứng nhận đối tượng chính sách (để hưởng định mức hỗ trợ cao hơn)."
        ],
        "guidance": "Mỗi người lao động được hỗ trợ đào tạo nghề trình độ sơ cấp 01 lần từ nguồn ngân sách nhà nước theo Quyết định 46/2015/QĐ-TTg.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã hoặc Ban Nhân dân thôn/tổ dân phố.",
        "legal_basis": [
            "Luật Giáo dục nghề nghiệp năm 2014",
            "Quyết định số 46/2015/QĐ-TTg ngày 28/9/2015 của Thủ tướng Chính phủ quy định chính sách hỗ trợ đào tạo trình độ sơ cấp, đào tạo dưới 03 tháng",
            "Kế hoạch đào tạo nghề hàng năm của UBND thành phố Hải Phòng"
        ],
        "duration": "Tổng hợp xét duyệt theo các đợt mở lớp hàng quý (thời gian xét hồ sơ không quá 07 ngày làm việc).",
        "fee": "Học viên được miễn 100% học phí và nhận hỗ trợ chi phí theo định mức quy định.",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 3.2. Lĩnh vực: van_hoa_giao_duc_y_te (10 TTHC)
    # =========================================================================
    {
        "id": "tiep_nhan_dang_ky_le_hoi_cap_xa",
        "name": "Tiếp nhận hồ sơ đăng ký tổ chức lễ hội quy mô cấp xã",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Ban tổ chức lễ hội nộp hồ sơ đăng ký tổ chức lễ hội tại UBND cấp xã trước ngày dự kiến khai mạc ít nhất 30 ngày.",
            "Bước 2: Cán bộ công chức Văn hóa - Xã hội tiếp nhận hồ sơ, kiểm tra tính hợp lệ và trao giấy biên nhận.",
            "Bước 3: Thẩm định hồ sơ, kiểm tra nguồn gốc lịch sử lễ hội, kịch bản chương trình phần lễ và phần hội, phương án bảo đảm an ninh trật tự, PCCC.",
            "Bước 4: UBND cấp xã ban hành văn bản chấp thuận tổ chức lễ hội hoặc thông báo lý do không chấp thuận trong thời hạn quy định."
        ],
        "documents_required": [
            "Văn bản đăng ký tổ chức lễ hội (theo Mẫu số 01 Nghị định 110/2018/NĐ-CP).",
            "Đề án tổ chức lễ hội (nêu rõ nguồn gốc, kịch bản lễ hội, thời gian, địa điểm, thành lập Ban tổ chức).",
            "Phương án bảo đảm an ninh trật tự, an toàn giao thông, phòng cháy chữa cháy, bảo vệ môi trường và vệ sinh an toàn thực phẩm."
        ],
        "guidance": "Lễ hội phải được tổ chức trang trọng, tiết kiệm, văn minh; nghiêm cấm các hành vi mê tín dị đoan, cờ bạc trá hình và thương mại hóa lễ hội.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi tổ chức lễ hội.",
        "legal_basis": [
            "Nghị định số 110/2018/NĐ-CP ngày 29/8/2018 của Chính phủ quy định về quản lý và tổ chức lễ hội",
            "Quyết định số 945/QĐ-SVHTTDL của Sở Văn hóa, Thể thao và Du lịch Hải Phòng"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "thong_bao_to_chuc_le_hoi_cap_xa",
        "name": "Thông báo tổ chức lễ hội quy mô cấp xã",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Cơ quan, đơn vị tổ chức lễ hội gửi văn bản thông báo tổ chức lễ hội đến UBND cấp xã trước ngày mở hội ít nhất 20 ngày.",
            "Bước 2: Cán bộ Văn hóa tiếp nhận văn bản thông báo, vào sổ theo dõi quản lý lễ hội truyền thống trên địa bàn.",
            "Bước 3: Rà soát nội dung thông báo, trường hợp có nội dung chưa phù hợp gửi văn bản yêu cầu điều chỉnh trong thời hạn 05 ngày làm việc.",
            "Bước 4: Ban hành văn bản tiếp nhận thông báo và phân công cán bộ kiểm tra, giám sát quá trình diễn ra lễ hội."
        ],
        "documents_required": [
            "Văn bản thông báo tổ chức lễ hội (theo Mẫu số 02 ban hành kèm Nghị định số 110/2018/NĐ-CP).",
            "Kế hoạch tổ chức lễ hội và danh sách thành viên Ban tổ chức lễ hội."
        ],
        "guidance": "Áp dụng đối với lễ hội truyền thống, lễ hội ngành nghề được tổ chức định kỳ nhưng không thuộc diện phải làm thủ tục đăng ký cấp phép quy định tại Điều 9 Nghị định 110/2018/NĐ-CP.",
        "submission_place": "Bộ phận Một cửa hoặc Văn phòng UBND cấp xã.",
        "legal_basis": [
            "Nghị định số 110/2018/NĐ-CP của Chính phủ",
            "Quy định của UBND thành phố Hải Phòng về quản lý lễ hội văn hóa"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận được văn bản thông báo.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_giay_chung_nhan_tro_choi_dien_tu_cong_cong",
        "name": "Cấp Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Tổ chức, cá nhân nộp hồ sơ tại Bộ phận Một cửa UBND cấp xã/phường nơi đặt địa điểm kinh doanh internet, trò chơi điện tử.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra hồ sơ, gửi giấy tiếp nhận và hẹn ngày kiểm tra thực tế.",
            "Bước 3: Đoàn kiểm tra liên ngành cấp xã kiểm tra thực tế: diện tích phòng máy, khoảng cách đến trường học (tối thiểu 200m), nội quy, thiết bị PCCC.",
            "Bước 4: Cấp Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng."
        ],
        "documents_required": [
            "Đơn đề nghị cấp Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng (theo mẫu quy định).",
            "Bản sao Giấy chứng nhận đăng ký hộ kinh doanh hoặc đăng ký doanh nghiệp.",
            "Bản sao Căn cước công dân của chủ cơ sở hoặc người quản lý điểm kinh doanh.",
            "Bản sao hợp đồng thuê địa điểm kinh doanh (nếu thuê địa điểm)."
        ],
        "guidance": "Địa điểm cung cấp dịch vụ phải cách cổng các trường tiểu học, THCS, THPT từ 200m trở lên; diện tích tối thiểu của phòng máy từ 50m2 đối với khu vực đô thị; có đầy đủ trang thiết bị PCCC theo quy định.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã/quận theo phân cấp quản lý của thành phố.",
        "legal_basis": [
            "Nghị định số 72/2013/NĐ-CP và Nghị định số 27/2018/NĐ-CP của Chính phủ",
            "Quyết định số 945/QĐ-SVHTTDL của Sở Văn hóa, Thể thao và Du lịch thành phố Hải Phòng"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Theo mức thu phí thẩm định điểm cung cấp dịch vụ trò chơi điện tử công cộng của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "sua_doi_giay_chung_nhan_tro_choi_dien_tu_cong_cong",
        "name": "Sửa đổi, bổ sung Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Chủ điểm kinh doanh nộp hồ sơ khi có thay đổi tên điểm, thay đổi chủ cơ sở hoặc thay đổi số lượng máy tính.",
            "Bước 2: Cán bộ chuyên môn kiểm tra nội dung sửa đổi, bổ sung so với hồ sơ gốc.",
            "Bước 3: Thực hiện kiểm tra thực tế nếu có thay đổi về quy mô diện tích hoặc bổ sung dàn máy.",
            "Bước 4: Cấp Giấy chứng nhận đã được sửa đổi, bổ sung nội dung."
        ],
        "documents_required": [
            "Đơn đề nghị sửa đổi, bổ sung Giấy chứng nhận đủ điều kiện hoạt động (theo mẫu).",
            "Bản chính Giấy chứng nhận đủ điều kiện hoạt động đã được cấp.",
            "Các giấy tờ chứng minh sự thay đổi nội dung (Đăng ký kinh doanh mới, hợp đồng chuyển nhượng...)."
        ],
        "guidance": "Trường hợp thay đổi địa điểm kinh doanh sang vị trí mới, chủ cơ sở phải làm thủ tục đề nghị cấp Giấy chứng nhận mới thay vì thủ tục sửa đổi.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả cơ quan đã cấp Giấy chứng nhận ban đầu.",
        "legal_basis": [
            "Nghị định số 72/2013/NĐ-CP và Nghị định số 27/2018/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Theo quy định của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "gia_han_giay_chung_nhan_tro_choi_dien_tu",
        "name": "Gia hạn Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Trước khi hết hạn Giấy chứng nhận tối thiểu 20 ngày, chủ điểm kinh doanh nộp hồ sơ xin gia hạn.",
            "Bước 2: Tiếp nhận hồ sơ, đối chiếu thời hạn hoạt động của giấy chứng nhận cũ (thời hạn mỗi giấy phép là 03 năm).",
            "Bước 3: Rà soát quá trình hoạt động, kiểm tra việc tuân thủ giờ mở cửa (từ 08h00 đến 22h00 hàng ngày) và không vi phạm trật tự.",
            "Bước 4: Trả Giấy chứng nhận đủ điều kiện hoạt động đã gia hạn thời hạn."
        ],
        "documents_required": [
            "Đơn đề nghị gia hạn Giấy chứng nhận đủ điều kiện hoạt động theo mẫu.",
            "Bản chính Giấy chứng nhận đủ điều kiện hoạt động đang có hiệu lực.",
            "Bản sao Giấy chứng nhận đăng ký kinh doanh còn hiệu lực."
        ],
        "guidance": "Mỗi giấy chứng nhận được gia hạn nhiều lần, mỗi lần gia hạn có thời hạn bằng thời hạn cấp mới (tối đa 03 năm).",
        "submission_place": "Bộ phận Một cửa cơ quan cấp phép.",
        "legal_basis": [
            "Nghị định số 72/2013/NĐ-CP và Nghị định số 27/2018/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Theo mức phí thẩm định gia hạn của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cap_lai_giay_chung_nhan_tro_choi_dien_tu",
        "name": "Cấp lại Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Chủ điểm kinh doanh nộp đơn đề nghị cấp lại do bị mất hoặc bị hư hỏng, rách nát.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra thông tin lưu trữ trong sổ đăng ký của cơ quan cấp phép.",
            "Bước 3: Lập hồ sơ in lại Giấy chứng nhận có ghi dòng chữ 'Cấp lại'.",
            "Bước 4: Trao Giấy chứng nhận cấp lại cho chủ cơ sở."
        ],
        "documents_required": [
            "Đơn đề nghị cấp lại Giấy chứng nhận đủ điều kiện hoạt động điểm cung cấp dịch vụ trò chơi điện tử công cộng (theo mẫu).",
            "Bản gốc Giấy chứng nhận bị rách nát, hư hỏng (nếu có)."
        ],
        "guidance": "Thời hạn của Giấy chứng nhận cấp lại có giá trị bằng thời hạn còn lại của Giấy chứng nhận đã được cấp trước đó.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả nơi cấp giấy phép.",
        "legal_basis": [
            "Nghị định số 72/2013/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đơn hợp lệ.",
        "fee": "Theo quy định của Bộ Tài chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cam_tiep_xuc_theo_quyet_dinh_ubnd_xa",
        "name": "Cấm tiếp xúc theo Quyết định của Chủ tịch Ủy ban nhân dân cấp xã (Phòng chống bạo lực gia đình)",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Nạn nhân bạo lực gia đình, người giám hộ hoặc cơ quan, tổ chức có thẩm quyền nộp đơn yêu cầu áp dụng biện pháp cấm tiếp xúc.",
            "Bước 2: Chủ tịch UBND cấp xã phân công Công an xã, công chức Văn hóa - Xã hội xác minh mức độ tổn hại thể xác, tinh thần của nạn nhân.",
            "Bước 3: Trong thời hạn 12 giờ kể từ khi nhận được yêu cầu, Chủ tịch UBND cấp xã ra Quyết định cấm tiếp xúc đối với người có hành vi bạo lực.",
            "Bước 4: Tống đạt Quyết định cấm tiếp xúc cho người có hành vi bạo lực, nạn nhân và Công an xã để tổ chức giám sát thực hiện."
        ],
        "documents_required": [
            "Đơn yêu cầu áp dụng biện pháp cấm tiếp xúc (theo mẫu quy định).",
            "Bản sao giấy tờ tùy thân của người yêu cầu.",
            "Tài liệu, chứng cứ về hành vi bạo lực gia đình hoặc xác nhận của cơ sở y tế về thương tích, tổn hại sức khỏe (nếu có)."
        ],
        "guidance": "Quyết định cấm tiếp xúc có hiệu lực ngay sau khi ký và thời hạn cấm tiếp xúc không quá 03 ngày; người bị áp dụng biện pháp phải giữ khoảng cách tối thiểu 30m với nạn nhân.",
        "submission_place": "Trực tiếp tại Trụ sở UBND cấp xã hoặc Công an xã, phường.",
        "legal_basis": [
            "Luật Phòng, chống bạo lực gia đình năm 2022",
            "Nghị định số 76/2023/NĐ-CP ngày 01/11/2023 của Chính phủ quy định chi tiết một số điều của Luật Phòng, chống bạo lực gia đình"
        ],
        "duration": "Trong thời hạn 12 giờ kể từ khi nhận được yêu cầu.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "huy_bo_quyet_dinh_cam_tiep_xuc",
        "name": "Hủy bỏ Quyết định cấm tiếp xúc theo Quyết định của Chủ tịch UBND cấp xã",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Nạn nhân bạo lực gia đình nộp đơn yêu cầu hủy bỏ Quyết định cấm tiếp xúc.",
            "Bước 2: Chủ tịch UBND cấp xã xem xét tính tự nguyện của nạn nhân và bảo đảm không có sự ép buộc, đe dọa từ người bạo lực.",
            "Bước 3: Ban hành Quyết định hủy bỏ biện pháp cấm tiếp xúc trong thời hạn quy định.",
            "Bước 4: Gửi Quyết định hủy bỏ cho người bị cấm tiếp xúc, nạn nhân và Công an xã."
        ],
        "documents_required": [
            "Đơn đề nghị hủy bỏ Quyết định cấm tiếp xúc của người bị bạo lực gia đình (hoặc người đại diện hợp pháp).",
            "Bản sao Quyết định cấm tiếp xúc đang có hiệu lực."
        ],
        "guidance": "Quyết định hủy bỏ biện pháp cấm tiếp xúc chỉ được ban hành khi có sự tự nguyện của chính nạn nhân bị bạo lực gia đình.",
        "submission_place": "UBND cấp xã hoặc Công an xã nơi ban hành quyết định.",
        "legal_basis": [
            "Luật Phòng, chống bạo lực gia đình năm 2022",
            "Nghị định số 76/2023/NĐ-CP của Chính phủ"
        ],
        "duration": "Trong thời hạn 12 giờ kể từ khi nhận được đơn đề nghị hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cong_nhan_cau_lac_bo_the_thao_co_so",
        "name": "Công nhận câu lạc bộ thể dục thể thao cơ sở",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Ban chủ nhiệm câu lạc bộ thể thao nộp hồ sơ đề nghị công nhận tại UBND cấp xã.",
            "Bước 2: Công chức Văn hóa - Xã hội kiểm tra danh sách hội viên, điều lệ hoạt động và cơ sở vật chất, sân bãi luyện tập thể thao.",
            "Bước 3: Thẩm định điều kiện thành lập câu lạc bộ thể thao cơ sở theo quy định của Bộ Văn hóa, Thể thao và Du lịch.",
            "Bước 4: Chủ tịch UBND cấp xã ký Quyết định công nhận câu lạc bộ thể dục thể thao cơ sở."
        ],
        "documents_required": [
            "Đơn đề nghị công nhận câu lạc bộ thể thao cơ sở theo mẫu quy định.",
            "Quy chế hoặc Điều lệ hoạt động của câu lạc bộ (nêu rõ mục đích, quyền và nghĩa vụ của hội viên).",
            "Danh sách Ban chủ nhiệm và danh sách hội viên câu lạc bộ (tối thiểu từ 10 - 15 hội viên trở lên).",
            "Bản kê khai địa điểm, cơ sở vật chất, trang thiết bị phục vụ tập luyện thể dục thể thao."
        ],
        "guidance": "Câu lạc bộ thể dục thể thao cơ sở hoạt động theo nguyên tắc tự nguyện, tự quản, tự chịu trách nhiệm về tài chính và tuân thủ pháp luật.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã.",
        "legal_basis": [
            "Luật Thể dục, thể thao năm 2006, sửa đổi bổ sung năm 2018",
            "Thông tư số 18/2011/TT-BVHTTDL ngày 02/12/2011 của Bộ Văn hóa, Thể thao và Du lịch quy định mẫu về tổ chức và hoạt động của câu lạc bộ thể dục thể thao cơ sở"
        ],
        "duration": "Không quá 07 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cong_nhan_ban_van_dong_thanh_lap_hoi_cap_xa",
        "name": "Công nhận Ban vận động thành lập hội hoạt động trong phạm vi cấp xã",
        "department": "Phòng Văn hóa - Xã hội",
        "primary_organization_unit_id": "org-van-hoa-xa-hoi",
        "domain_slug": "van_hoa_giao_duc_y_te",
        "steps": [
            "Bước 1: Trưởng ban vận động thành lập hội nộp hồ sơ tại Bộ phận Một cửa UBND cấp xã.",
            "Bước 2: Công chức Văn hóa - Xã hội chủ trì phối hợp với các ban ngành liên quan thẩm tra lý lịch công dân của các sáng lập viên.",
            "Bước 3: Kiểm tra tôn chỉ, mục đích hoạt động của hội dự kiến thành lập, bảo đảm không trái thuần phong mỹ tục và quy định pháp luật.",
            "Bước 4: Chủ tịch UBND cấp xã ban hành Quyết định công nhận Ban vận động thành lập hội."
        ],
        "documents_required": [
            "Đơn đề nghị công nhận Ban vận động thành lập hội (theo mẫu quy định).",
            "Danh sách các thành viên Ban vận động thành lập hội (kèm trích ngang lý lịch cá nhân).",
            "Dự thảo Điều lệ hội và phương hướng hoạt động của hội dự kiến thành lập."
        ],
        "guidance": "Ban vận động có trách nhiệm vận động hội viên gia nhập hội và chuẩn bị các điều kiện để tổ chức Đại hội thành lập hội theo đúng quy định.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã.",
        "legal_basis": [
            "Nghị định số 126/2024/NĐ-CP ngày 08/10/2024 của Chính phủ quy định về tổ chức, hoạt động và quản lý hội",
            "Quyết định số 945/QĐ-SVHTTDL của UBND thành phố Hải Phòng"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 4. VĂN PHÒNG HĐND VÀ UBND (org-cong-an)
    # 4.1. Lĩnh vực: cu_tru_an_ninh (10 TTHC)
    # =========================================================================
    {
        "id": "dang_ky_thuong_tru",
        "name": "Đăng ký thường trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Công dân nộp hồ sơ trực tiếp tại Công an xã, phường hoặc nộp trực tuyến qua Cổng Dịch vụ công Bộ Công an / Cổng DVC quốc gia.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra hồ sơ, đối chiếu dữ liệu công dân trong Cơ sở dữ liệu quốc gia về dân cư.",
            "Bước 3: Công an cấp xã thẩm tra chỗ ở hợp pháp và điều kiện đăng ký thường trú theo Luật Cư trú.",
            "Bước 4: Cập nhật thông tin nơi thường trú mới của công dân vào CSDL về cư trú và thông báo kết quả bằng văn bản (Mẫu CT08)."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01 ban hành kèm Thông tư số 66/2023/TT-BCA).",
            "Giấy tờ, tài liệu chứng minh chỗ ở hợp pháp (Sổ đỏ, Hợp đồng mua bán nhà, Giấy phép xây dựng, Hợp đồng thuê nhà có công chứng...).",
            "Văn bản đồng ý của chủ hộ, chủ sở hữu chỗ ở hợp pháp (nếu đăng ký vào chỗ ở hợp pháp của người khác)."
        ],
        "guidance": "Hiện nay việc đăng ký thường trú đã được số hóa hoàn toàn, không cấp sổ hộ khẩu giấy mới; thông tin cư trú được tra cứu và xác thực trực tiếp qua CSDL quốc gia về dân cư và ứng dụng VNeID.",
        "submission_place": "Công an xã, phường, thị trấn nơi công dân đăng ký thường trú hoặc Cổng Dịch vụ công quản lý cư trú.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA của Bộ Công an",
            "Nghị định số 62/2021/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 07 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Trực tiếp: 20.000 VNĐ / lần; Trực tuyến: 10.000 VNĐ / lần. Miễn phí cho người có công, hộ nghèo, trẻ em, người cao tuổi.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "xoa_dang_ky_thuong_tru",
        "name": "Xóa đăng ký thường trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Cơ quan, hộ gia đình hoặc cá nhân nộp hồ sơ xóa đăng ký thường trú tại Công an cấp xã khi thuộc một trong các trường hợp quy định tại Điều 24 Luật Cư trú.",
            "Bước 2: Công an cấp xã kiểm tra hồ sơ, đối chiếu dữ liệu cư trú.",
            "Bước 3: Thực hiện xóa đăng ký thường trú trên Cơ sở dữ liệu về cư trú.",
            "Bước 4: Cấp Thông báo về việc xóa đăng ký thường trú cho người nộp hồ sơ."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01).",
            "Giấy tờ chứng minh thuộc diện xóa đăng ký thường trú (Giấy chứng tử đối với người chết, Quyết định hủy bỏ đăng ký thường trú, văn bản chứng minh định cư ở nước ngoài...)."
        ],
        "guidance": "Trong thời hạn 01 ngày kể từ ngày nhận đủ hồ sơ hoặc nhận được văn bản thông báo của cơ quan nhà nước, Công an cấp xã phải thực hiện xóa đăng ký thường trú.",
        "submission_place": "Công an cấp xã hoặc Cổng Dịch vụ công quản lý cư trú.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA"
        ],
        "duration": "Không quá 01 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_tam_tru",
        "name": "Đăng ký tạm trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Công dân đến sinh sống tại chỗ ở hợp pháp ngoài phạm vi đơn vị hành chính cấp xã nơi thường trú từ 30 ngày trở lên nộp hồ sơ đăng ký tạm trú.",
            "Bước 2: Công an xã/phường tiếp nhận hồ sơ trực tiếp hoặc qua Cổng Dịch vụ công Bộ Công an.",
            "Bước 3: Thẩm tra điều kiện tạm trú và xác nhận chỗ ở hợp pháp của người thuê/mượn nhà.",
            "Bước 4: Cập nhật thông tin tạm trú vào Cơ sở dữ liệu cư trú và thông báo kết quả qua VNeID hoặc văn bản."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01).",
            "Giấy tờ chứng minh chỗ ở hợp pháp (Hợp đồng thuê nhà, mượn nhà, ở nhờ có xác nhận của chủ nhà hoặc giấy tờ quyền sở hữu nhà).",
            "Bản sao Căn cước công dân của người đăng ký tạm trú."
        ],
        "guidance": "Thời hạn tạm trú tối đa là 02 năm và có thể gia hạn nhiều lần. Đăng ký tạm trú là nghĩa vụ bắt buộc của công dân khi sinh sống ổn định ngoài nơi đăng ký thường trú.",
        "submission_place": "Công an cấp xã/phường nơi dự kiến tạm trú.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Nghị định số 62/2021/NĐ-CP của Chính phủ",
            "Thông tư số 66/2023/TT-BCA của Bộ Công an"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Trực tiếp: 15.000 VNĐ; Trực tuyến: 7.000 VNĐ. Miễn phí cho người có công, hộ nghèo, người cao tuổi.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "gia_han_tam_tru",
        "name": "Gia hạn tạm trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Trong thời hạn 15 ngày trước ngày kết thúc thời hạn tạm trú đã đăng ký, công dân nộp hồ sơ gia hạn tạm trú.",
            "Bước 2: Cán bộ Công an cấp xã tiếp nhận hồ sơ, đối chiếu dữ liệu tạm trú hiện có.",
            "Bước 3: Kiểm tra hợp đồng thuê nhà hoặc sự đồng ý tiếp tục cho thuê/mượn của chủ nhà.",
            "Bước 4: Cập nhật gia hạn thời hạn tạm trú trên hệ thống CSDL cư trú."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01).",
            "Giấy tờ chứng minh chỗ ở hợp pháp hoặc hợp đồng thuê nhà được gia hạn/còn hiệu lực."
        ],
        "guidance": "Nếu không làm thủ tục gia hạn tạm trú trong thời hạn quy định mà vẫn tiếp tục sinh sống, công dân sẽ bị xóa đăng ký tạm trú và phải làm lại thủ tục đăng ký mới từ đầu.",
        "submission_place": "Công an cấp xã/phường nơi đang tạm trú hoặc Cổng Dịch vụ công.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Trực tiếp: 15.000 VNĐ; Trực tuyến: 7.000 VNĐ.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "xoa_dang_ky_tam_tru",
        "name": "Xóa đăng ký tạm trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Cá nhân, hộ gia đình hoặc chủ cơ sở cho thuê trọ nộp hồ sơ xóa đăng ký tạm trú khi công dân chết, chuyển đi nơi khác hoặc hết hạn tạm trú mà không gia hạn.",
            "Bước 2: Công an cấp xã tiếp nhận hồ sơ, kiểm tra căn cứ xóa tạm trú.",
            "Bước 3: Xóa thông tin tạm trú của công dân trong CSDL về cư trú.",
            "Bước 4: Thông báo kết quả cho người nộp hồ sơ hoặc cập nhật trạng thái trên Cổng Dịch vụ công."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01).",
            "Giấy tờ chứng minh công dân thuộc diện xóa đăng ký tạm trú (Giấy chứng tử, Hợp đồng thuê nhà đã thanh lý, văn bản thông báo chấm dứt cư trú của chủ nhà...)."
        ],
        "guidance": "Chủ nhà trọ, cơ sở lưu trú có trách nhiệm thông báo và phối hợp với cơ quan công an làm thủ tục xóa tạm trú đối với người đã chuyển đi khỏi cơ sở.",
        "submission_place": "Công an cấp xã hoặc Cổng Dịch vụ công quản lý cư trú.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA"
        ],
        "duration": "Không quá 01 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "khai_bao_tam_vang",
        "name": "Khai báo tạm vắng",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Công dân thuộc diện quy định tại Điều 31 Luật Cư trú đến khai báo tạm vắng tại Công an cấp xã hoặc khai báo trực tuyến qua VNeID.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra nội dung khai báo tạm vắng, lý do và thời gian tạm vắng.",
            "Bước 3: Ghi nhận thông tin tạm vắng vào Cơ sở dữ liệu về cư trú.",
            "Bước 4: Cấp Phiếu khai báo tạm vắng (Mẫu CT05) cho công dân (trường hợp nộp trực tiếp)."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01) hoặc Đề nghị khai báo tạm vắng.",
            "Bản sao Căn cước công dân hoặc giấy tờ chứng minh lý do tạm vắng (nếu thuộc diện đang bị áp dụng biện pháp tư pháp, quản chế...)."
        ],
        "guidance": "Công dân vắng mặt tại nơi cư trú từ 12 tháng trở lên mà không đăng ký tạm trú ở nơi khác bắt buộc phải khai báo tạm vắng để tránh bị xóa đăng ký thường trú.",
        "submission_place": "Công an cấp xã/phường nơi thường trú hoặc ứng dụng VNeID.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA"
        ],
        "duration": "Giải quyết ngay trong ngày làm việc (không quá 01 ngày làm việc).",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "thong_bao_luu_tru",
        "name": "Thông báo lưu trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Đại diện cơ sở lưu trú (khách sạn, nhà nghỉ, nhà trọ, bệnh viện) hoặc cá nhân, chủ hộ gia đình có người đến lưu trú thực hiện thông báo lưu trú.",
            "Bước 2: Gửi thông báo trực tuyến qua phần mềm Quản lý lưu trú ASM, Cổng Dịch vụ công Bộ Công an hoặc qua ứng dụng VNeID trước 23 giờ trong ngày.",
            "Bước 3: Hệ thống tự động tiếp nhận, đồng bộ dữ liệu vào Cơ sở dữ liệu cư trú.",
            "Bước 4: Cơ quan Công an cấp xã tiếp nhận, kiểm tra và xác nhận lưu trú trên môi trường điện tử."
        ],
        "documents_required": [
            "Thông tin Căn cước công dân/Hộ chiếu của người lưu trú.",
            "Thời gian bắt đầu và thời gian dự kiến kết thúc lưu trú, số phòng hoặc địa chỉ cụ thể."
        ],
        "guidance": "Việc thông báo lưu trú phải thực hiện trước 23 giờ của ngày đến lưu trú; trường hợp người đến lưu trú sau 23 giờ thì thông báo trước 08 giờ ngày hôm sau.",
        "submission_place": "Trực tuyến qua ứng dụng VNeID, Cổng DVC Bộ Công an, hệ thống phần mềm ASM hoặc trực tiếp tại Công an cấp xã.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA"
        ],
        "duration": "Xác nhận tự động / giải quyết ngay khi nhận được thông báo.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "xac_nhan_thong_tin_ve_cu_tru",
        "name": "Xác nhận thông tin về cư trú (Mẫu CT07)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Công dân nộp phiếu yêu cầu xác nhận thông tin về cư trú trực tiếp tại Công an cấp xã bất kỳ hoặc trực tuyến trên Cổng Dịch vụ công.",
            "Bước 2: Cán bộ tiếp nhận tra cứu dữ liệu dân cư trên hệ thống CSDL quốc gia về dân cư.",
            "Bước 3: In Giấy xác nhận thông tin về cư trú (Mẫu CT07) có đầy đủ thông tin chủ hộ, các thành viên cùng cư trú.",
            "Bước 4: Lãnh đạo Công an cấp xã ký, đóng dấu và trả kết quả cho công dân."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01) ghi rõ nội dung yêu cầu xác nhận cư trú.",
            "Xuất trình Căn cước công dân của người yêu cầu."
        ],
        "guidance": "Xác nhận thông tin về cư trú có giá trị sử dụng 01 năm kể từ ngày cấp; hoặc có giá trị đến thời điểm thay đổi thông tin về cư trú nếu thông tin có biến động.",
        "submission_place": "Bất kỳ Công an cấp xã/phường trên toàn quốc hoặc nộp qua Cổng DVC Bộ Công an.",
        "legal_basis": [
            "Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA của Bộ Công an"
        ],
        "duration": "Không quá 01 ngày làm việc đối với thông tin đã có trong CSDL; không quá 03 ngày nếu cần kiểm tra xác minh.",
        "fee": "Trực tiếp: 10.000 VNĐ; Trực tuyến: 5.000 VNĐ.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tach_ho_khau_trong_cung_cho_o",
        "name": "Tách hộ trong cùng một chỗ ở hợp pháp",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Thành viên hộ gia đình có đủ năng lực hành vi dân sự và đáp ứng điều kiện tách hộ nộp hồ sơ tại Công an cấp xã.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra điều kiện tách hộ (có năng lực hành vi dân sự, được chủ hộ và các thành viên đồng ý, có chỗ ở hợp pháp).",
            "Bước 3: Cập nhật điều chỉnh dữ liệu thành viên hộ mới trên CSDL quốc gia về cư trú.",
            "Bước 4: Cấp Thông báo kết quả giải quyết thủ tục về cư trú xác nhận việc tách hộ thành công."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01), trong đó có ý kiến đồng ý của chủ hộ và chủ sở hữu chỗ ở hợp pháp.",
            "Giấy tờ chứng minh việc phân chia tài sản hoặc văn bản thỏa thuận của các thành viên về việc tách hộ (nếu cần)."
        ],
        "guidance": "Điều kiện tách hộ: người có năng lực hành vi dân sự đầy đủ và có chỗ ở hợp pháp độc lập hoặc được chủ hộ và chủ sở hữu chỗ ở hợp pháp đồng ý cho tách hộ.",
        "submission_place": "Công an cấp xã/phường nơi thường trú.",
        "legal_basis": [
            "Điều 25 Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Trực tiếp: 10.000 VNĐ; Trực tuyến: 5.000 VNĐ.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dieu_chinh_thong_tin_ve_cu_tru_trong_csdl",
        "name": "Điều chỉnh thông tin về cư trú trong Cơ sở dữ liệu về cư trú",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "cu_tru_an_ninh",
        "steps": [
            "Bước 1: Công dân nộp hồ sơ khi có sự thay đổi về chủ hộ, quan hệ với chủ hộ, thay đổi thông tin hộ tịch hoặc địa chỉ số nhà.",
            "Bước 2: Công an cấp xã tiếp nhận hồ sơ, đối chiếu giấy tờ pháp lý chứng minh sự thay đổi.",
            "Bước 3: Thực hiện cập nhật, điều chỉnh thông tin chính xác trên Cơ sở dữ liệu về cư trú.",
            "Bước 4: Cấp thông báo xác nhận đã điều chỉnh thông tin cư trú cho công dân."
        ],
        "documents_required": [
            "Tờ khai thay đổi thông tin cư trú (Mẫu CT01).",
            "Giấy tờ, tài liệu chứng minh sự thay đổi: Trích lục cải chính hộ tịch, Quyết định đổi tên, Văn bản cử chủ hộ mới có ý kiến của các thành viên trong hộ gia đình, Quyết định điều chỉnh số nhà của UBND cấp xã/huyện..."
        ],
        "guidance": "Trong thời hạn 30 ngày kể từ ngày có quyết định thay đổi thông tin hộ tịch hoặc thay đổi chủ hộ, người dân phải làm thủ tục điều chỉnh thông tin cư trú.",
        "submission_place": "Công an cấp xã/phường nơi công dân đang đăng ký thường trú hoặc tạm trú.",
        "legal_basis": [
            "Điều 26 Luật Cư trú năm 2020",
            "Thông tư số 55/2021/TT-BCA và Thông tư số 66/2023/TT-BCA"
        ],
        "duration": "Không quá 03 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Trực tiếp: 10.000 VNĐ; Trực tuyến: 5.000 VNĐ.",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 4.2. Lĩnh vực: ho_tich_chung_thuc (10 TTHC)
    # =========================================================================
    {
        "id": "dang_ky_khai_sinh",
        "name": "Đăng ký khai sinh (Thẩm quyền cấp xã)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người đi đăng ký khai sinh nộp hồ sơ trực tiếp tại Bộ phận Một cửa của UBND cấp xã hoặc nộp trực tuyến qua Cổng dịch vụ công.",
            "Bước 2: Công chức Tư pháp - Hộ tịch tiếp nhận hồ sơ, đối chiếu thông tin trong CSDL quốc gia về dân cư để cấp Số định danh cá nhân cho trẻ.",
            "Bước 3: Công chức ghi nội dung khai sinh vào Sổ đăng ký khai sinh, cùng người đi đăng ký khai sinh ký tên vào Sổ hộ tịch.",
            "Bước 4: Chủ tịch UBND cấp xã ký Giấy khai sinh bản chính cấp cho công dân."
        ],
        "documents_required": [
            "Tờ khai đăng ký khai sinh theo mẫu ban hành kèm Thông tư 04/2020/TT-BTP.",
            "Giấy chứng sinh (do cơ sở y tế nơi trẻ sinh ra cấp) hoặc văn bản xác nhận của người làm chứng về việc sinh.",
            "Trường hợp cha, mẹ đã kết hôn thì xuất trình Giấy chứng nhận kết hôn."
        ],
        "guidance": "Trong thời hạn 60 ngày kể từ ngày sinh con, cha hoặc mẹ có trách nhiệm đăng ký khai sinh cho con. Có thể thực hiện liên thông 3 thủ tục: Đăng ký khai sinh - Đăng ký thường trú - Cấp thẻ BHYT cho trẻ dưới 6 tuổi.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi cư trú của người cha hoặc người mẹ.",
        "legal_basis": [
            "Luật Hộ tịch năm 2014",
            "Nghị định số 123/2015/NĐ-CP của Chính phủ",
            "Thông tư số 04/2020/TT-BTP của Bộ Tư pháp"
        ],
        "duration": "Giải quyết ngay trong ngày làm việc. Nếu nhận hồ sơ sau 15 giờ thì trả kết quả vào ngày làm việc tiếp theo.",
        "fee": "Miễn phí hoàn toàn đối với đăng ký khai sinh đúng hạn cho trẻ em Việt Nam.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_ket_hon",
        "name": "Đăng ký kết hôn trong nước (Cấp xã)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Hai bên nam, nữ trực tiếp có mặt tại Bộ phận Một cửa UBND cấp xã nơi một trong hai bên thường trú hoặc tạm trú để nộp hồ sơ.",
            "Bước 2: Cán bộ hộ tịch tiếp nhận hồ sơ, kiểm tra điều kiện kết hôn theo Luật Hôn nhân và Gia đình.",
            "Bước 3: Sau khi xác nhận hai bên hoàn toàn tự nguyện và đủ điều kiện, công chức hộ tịch ghi việc kết hôn vào Sổ hộ tịch.",
            "Bước 4: Hai bên nam, nữ ký vào Giấy chứng nhận kết hôn và Sổ hộ tịch; Chủ tịch UBND xã ký và trao Giấy chứng nhận kết hôn cho hai bên."
        ],
        "documents_required": [
            "Tờ khai đăng ký kết hôn theo mẫu (hai bên có thể khai chung vào một tờ khai).",
            "Giấy xác nhận tình trạng hôn nhân do UBND cấp xã nơi thường trú trước đó cấp (nếu người yêu cầu cư trú ngoài địa bàn đăng ký kết hôn).",
            "Xuất trình Căn cước công dân của hai bên nam, nữ."
        ],
        "guidance": "Khi đăng ký kết hôn, bắt buộc cả hai bên nam và nữ phải cùng có mặt tại trụ sở UBND cấp xã. Nam từ đủ 20 tuổi trở lên, nữ từ đủ 18 tuổi trở lên.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã nơi cư trú của một trong hai bên nam, nữ.",
        "legal_basis": [
            "Luật Hôn nhân và Gia đình năm 2014",
            "Luật Hộ tịch năm 2014",
            "Nghị định số 123/2015/NĐ-CP của Chính phủ"
        ],
        "duration": "Giải quyết ngay trong ngày nhận đủ hồ sơ hợp lệ; trường hợp cần xác minh thì không quá 03 ngày làm việc.",
        "fee": "Miễn phí đăng ký kết hôn đối với công dân Việt Nam cư trú trong nước.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "xac_nhan_doc_than",
        "name": "Cấp Giấy xác nhận tình trạng hôn nhân (Giấy xác nhận độc thân)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Công dân nộp hồ sơ trực tiếp tại Bộ phận Một cửa của UBND cấp xã nơi thường trú hoặc nộp trực tuyến qua Cổng dịch vụ công.",
            "Bước 2: Công chức tư pháp - hộ tịch kiểm tra Sổ đăng ký kết hôn và đối chiếu thông tin trong CSDL hộ tịch điện tử.",
            "Bước 3: Lập Giấy xác nhận tình trạng hôn nhân theo mẫu quy định trình Chủ tịch UBND cấp xã ký.",
            "Bước 4: Trao Giấy xác nhận tình trạng hôn nhân cho người nộp hồ sơ."
        ],
        "documents_required": [
            "Tờ khai cấp Giấy xác nhận tình trạng hôn nhân (theo mẫu ban hành kèm Thông tư số 04/2020/TT-BTP).",
            "Bản sao bản án hoặc quyết định ly hôn đã có hiệu lực pháp luật (nếu đã ly hôn).",
            "Bản sao Giấy chứng tử của vợ/chồng (nếu vợ hoặc chồng đã chết)."
        ],
        "guidance": "Giấy xác nhận tình trạng hôn nhân có giá trị 06 tháng kể từ ngày cấp hoặc đến thời điểm thay đổi tình trạng hôn nhân; ghi rõ mục đích sử dụng (kết hôn, mua bán nhà đất, vay vốn ngân hàng...).",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi thường trú.",
        "legal_basis": [
            "Luật Hộ tịch năm 2014",
            "Nghị định số 123/2015/NĐ-CP",
            "Thông tư số 04/2020/TT-BTP của Bộ Tư pháp"
        ],
        "duration": "Không quá 03 ngày làm việc; trường hợp cần xác minh qua các địa phương khác thì không quá 10 ngày làm việc.",
        "fee": "15.000 VNĐ / bản xác nhận. Miễn lệ phí đối với hộ nghèo, người cao tuổi, người khuyết tật.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_khai_tu",
        "name": "Đăng ký khai tử (Cấp xã)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Thân nhân của người chết nộp hồ sơ đăng ký khai tử tại UBND cấp xã nơi cư trú cuối cùng của người chết.",
            "Bước 2: Công chức tư pháp - hộ tịch tiếp nhận hồ sơ, kiểm tra Giấy báo tử hoặc giấy tờ thay thế Giấy báo tử.",
            "Bước 3: Ghi nội dung khai tử vào Sổ đăng ký khai tử và cập nhật trạng thái tử trên Cơ sở dữ liệu hộ tịch.",
            "Bước 4: Chủ tịch UBND cấp xã ký Trích lục khai tử (bản chính) cấp cho thân nhân."
        ],
        "documents_required": [
            "Tờ khai đăng ký khai tử theo mẫu quy định.",
            "Giấy báo tử do Thủ trưởng cơ sở y tế cấp (nếu chết tại bệnh viện) hoặc xác nhận của UBND cấp xã (nếu chết tại nhà).",
            "Bản chính Căn cước công dân của người chết (để làm thủ tục thu hồi theo quy định)."
        ],
        "guidance": "Trong thời hạn 15 ngày kể từ ngày có người chết, người thân thích có trách nhiệm đi đăng ký khai tử. Có thể làm liên thông thủ tục Khai tử - Xóa thường trú - Giải quyết mai táng phí.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã nơi cư trú cuối cùng của người chết.",
        "legal_basis": [
            "Luật Hộ tịch năm 2014",
            "Nghị định số 123/2015/NĐ-CP của Chính phủ",
            "Thông tư số 04/2020/TT-BTP"
        ],
        "duration": "Giải quyết ngay trong ngày tiếp nhận hồ sơ; nếu sau 15 giờ thì giải quyết vào ngày làm việc tiếp theo.",
        "fee": "Miễn phí hoàn toàn đối với trường hợp đăng ký khai tử đúng hạn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_nhan_cha_me_con",
        "name": "Đăng ký nhận cha, mẹ, con (Thẩm quyền cấp xã)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người yêu cầu đăng ký nhận cha, mẹ, con nộp hồ sơ tại UBND cấp xã nơi cư trú của người nhận hoặc người được nhận.",
            "Bước 2: Công chức tư pháp - hộ tịch kiểm tra hồ sơ, đối chiếu chứng cứ chứng minh quan hệ cha, mẹ, con.",
            "Bước 3: Niêm yết việc nhận cha, mẹ, con tại trụ sở UBND cấp xã trong thời hạn quy định nếu cần thiết.",
            "Bước 4: Các bên ký tên vào Sổ hộ tịch; Chủ tịch UBND cấp xã ký cấp Trích lục đăng ký nhận cha, mẹ, con."
        ],
        "documents_required": [
            "Tờ khai đăng ký nhận cha, mẹ, con theo mẫu Thông tư 04/2020/TT-BTP.",
            "Chứng cứ chứng minh quan hệ cha mẹ con: Kết luận giám định ADN của tổ chức có thẩm quyền hoặc văn bản cam đoan có người làm chứng.",
            "Bản sao Căn cước công dân của các bên tham gia."
        ],
        "guidance": "Áp dụng trong trường hợp việc nhận cha, mẹ, con không có tranh chấp giữa các bên. Nếu có tranh chấp thuộc thẩm quyền giải quyết của Tòa án nhân dân.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi cư trú của người nhận hoặc người được nhận.",
        "legal_basis": [
            "Luật Hộ tịch năm 2014",
            "Thông tư số 04/2020/TT-BTP của Bộ Tư pháp"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ; nếu cần xác minh thì không quá 08 ngày làm việc.",
        "fee": "Miễn phí lệ phí cho công dân Việt Nam cư trú tại địa phương.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_giam_ho",
        "name": "Đăng ký giám hộ (Thẩm quyền cấp xã)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người yêu cầu đăng ký giám hộ cử nộp hồ sơ tại UBND cấp xã nơi người được giám hộ hoặc người giám hộ cư trú.",
            "Bước 2: Công chức tư pháp - hộ tịch kiểm tra tư cách của người giám hộ theo quy định của Bộ luật Dân sự.",
            "Bước 3: Ghi vào Sổ đăng ký giám hộ và cùng người giám hộ ký vào Sổ hộ tịch.",
            "Bước 4: Chủ tịch UBND cấp xã ký cấp Trích lục đăng ký giám hộ."
        ],
        "documents_required": [
            "Tờ khai đăng ký giám hộ theo mẫu.",
            "Văn bản cử người giám hộ theo quy định của Bộ luật Dân sự (có xác nhận của các thành viên gia đình).",
            "Giấy tờ chứng minh điều kiện giám hộ và giấy tờ chứng minh người được giám hộ thuộc diện cần giám hộ."
        ],
        "guidance": "Người giám hộ phải có năng lực hành vi dân sự đầy đủ, có tư cách đạo đức tốt và có điều kiện thực tế để chăm sóc, giáo dục người được giám hộ.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã.",
        "legal_basis": [
            "Bộ luật Dân sự năm 2015",
            "Luật Hộ tịch năm 2014",
            "Thông tư số 04/2020/TT-BTP"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_cham_dut_giam_ho",
        "name": "Đăng ký chấm dứt giám hộ (Thẩm quyền cấp xã)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người yêu cầu nộp hồ sơ chấm dứt giám hộ khi người được giám hộ đã đủ 18 tuổi hoặc đã hồi phục năng lực hành vi dân sự.",
            "Bước 2: Công chức tư pháp - hộ tịch kiểm tra căn cứ chấm dứt giám hộ theo quy định của pháp luật dân sự.",
            "Bước 3: Ghi việc chấm dứt giám hộ vào Sổ đăng ký hộ tịch.",
            "Bước 4: Chủ tịch UBND cấp xã ký Trích lục đăng ký chấm dứt giám hộ cấp cho người yêu cầu."
        ],
        "documents_required": [
            "Tờ khai đăng ký chấm dứt giám hộ theo mẫu.",
            "Bản chính Trích lục đăng ký giám hộ đã cấp trước đây.",
            "Giấy tờ chứng minh căn cứ chấm dứt giám hộ (Quyết định của Tòa án, Giấy khai sinh chứng minh đủ 18 tuổi...)."
        ],
        "guidance": "Giám hộ chấm dứt trong các trường hợp: Người được giám hộ đã có năng lực hành vi dân sự đầy đủ; người được giám hộ chết; cha mẹ của người được giám hộ đã có đủ điều kiện nuôi dưỡng.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã nơi đã đăng ký giám hộ trước đó.",
        "legal_basis": [
            "Bộ luật Dân sự năm 2015",
            "Luật Hộ tịch năm 2014"
        ],
        "duration": "Trong thời hạn 02 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "thay_doi_cai_chinh_bo_sung_ho_tich",
        "name": "Thay đổi, cải chính, bổ sung hộ tịch, xác định lại dân tộc cho người dưới 14 tuổi",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Cha, mẹ hoặc người giám hộ của người dưới 14 tuổi nộp hồ sơ tại UBND cấp xã nơi đã đăng ký hộ tịch trước đây hoặc nơi cư trú.",
            "Bước 2: Công chức tư pháp - hộ tịch kiểm tra hồ sơ, đối chiếu sai sót trong sổ hộ tịch gốc.",
            "Bước 3: Lập biên bản thẩm tra, trình Chủ tịch UBND cấp xã ký Trích lục thay đổi, cải chính hộ tịch.",
            "Bước 4: Ghi chú nội dung thay đổi vào Sổ hộ tịch gốc và cấp Trích lục cho người yêu cầu."
        ],
        "documents_required": [
            "Tờ khai cải chính, thay đổi, bổ sung hộ tịch theo mẫu quy định.",
            "Bản chính giấy tờ hộ tịch cần cải chính, thay đổi (Giấy khai sinh gốc...).",
            "Giấy tờ, tài liệu làm căn cứ chứng minh sai sót do công chức làm hộ tịch hoặc người đăng ký trước đây ghi sai."
        ],
        "guidance": "Cải chính hộ tịch cấp xã chỉ áp dụng cho người dưới 14 tuổi cư trú trong nước và chỉ giải quyết sai sót khi có đủ căn cứ chứng minh do lỗi ghi chép.",
        "submission_place": "Bộ phận Một cửa UBND cấp xã nơi đã đăng ký hộ tịch trước đây hoặc nơi cư trú hiện tại.",
        "legal_basis": [
            "Luật Hộ tịch năm 2014",
            "Nghị định số 123/2015/NĐ-CP của Chính phủ"
        ],
        "duration": "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ; nếu cần xác minh thì không quá 06 ngày làm việc.",
        "fee": "15.000 VNĐ / trường hợp. Miễn phí cho người thuộc hộ nghèo, người có công.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "chung_thuc_ban_sao_tu_ban_chinh",
        "name": "Chứng thực bản sao từ bản chính các giấy tờ, văn bản do cơ quan có thẩm quyền cấp",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Cá nhân, tổ chức xuất trình bản chính giấy tờ, văn bản và bản sao cần chứng thực tại Bộ phận Một cửa.",
            "Bước 2: Người thực hiện chứng thực kiểm tra bản chính, đối chiếu từng trang của bản sao với bản chính.",
            "Bước 3: Đóng dấu chứng thực, ghi số chứng thực và trình lãnh đạo UBND cấp xã (hoặc công chức được ủy quyền) ký chứng thực.",
            "Bước 4: Đóng dấu cơ quan và trả kết quả bản sao đã chứng thực cho công dân sau khi thu lệ phí."
        ],
        "documents_required": [
            "Bản chính giấy tờ, văn bản do cơ quan, tổ chức có thẩm quyền cấp hợp pháp.",
            "Bản photo/bản in cần chứng thực (trường hợp người yêu cầu không tự photo, cơ quan thực hiện chứng thực hỗ trợ sao chụp tính phí photo)."
        ],
        "guidance": "Không được chứng thực bản sao trong các trường hợp: Bản chính bị tẩy xóa, sửa chữa, rách nát không xác định được nội dung; bản chính có nội dung bí mật nhà nước; bản chính bị giả mạo.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã bất kỳ (không phụ thuộc vào nơi cư trú).",
        "legal_basis": [
            "Nghị định số 23/2015/NĐ-CP ngày 16/02/2015 của Chính phủ về cấp bản sao từ sổ gốc, chứng thực bản sao từ bản chính, chứng thực chữ ký",
            "Thông tư số 01/2020/TT-BTP của Bộ Tư pháp"
        ],
        "duration": "Giải quyết ngay trong ngày tiếp nhận hồ sơ; nếu số lượng từ 20 bản sao trở lên thì trả kết quả trong ngày làm việc tiếp theo.",
        "fee": "2.000 VNĐ / trang; từ trang thứ 3 trở đi thu 1.000 VNĐ / trang, tối đa 200.000 VNĐ / bản.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "chung_thuc_chu_ky_trong_van_ban",
        "name": "Chứng thực chữ ký trong các giấy tờ, văn bản (bao gồm điểm chỉ)",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người yêu cầu chứng thực chữ ký trực tiếp xuất trình Căn cước công dân và văn bản cần ký tại Bộ phận Một cửa.",
            "Bước 2: Người thực hiện chứng thực kiểm tra giấy tờ tùy thân, nhận thức và sự minh mẫn của người yêu cầu.",
            "Bước 3: Người yêu cầu chứng thực ký hoặc điểm chỉ trực tiếp trước mặt người tiếp nhận chứng thực.",
            "Bước 4: Ghi lời chứng thực chữ ký, trình ký chứng thực, đóng dấu và thu lệ phí theo quy định."
        ],
        "documents_required": [
            "Bản chính Căn cước công dân hoặc Hộ chiếu còn giá trị sử dụng.",
            "Giấy tờ, văn bản mà mình sẽ ký vào (để trống chữ ký, không ký trước)."
        ],
        "guidance": "Người yêu cầu phải ký trước mặt người tiếp nhận. Không chứng thực chữ ký vào văn bản có nội dung trái pháp luật, đạo đức xã hội, hoặc hợp đồng giao dịch mà luật quy định phải công chứng.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã bất kỳ.",
        "legal_basis": [
            "Nghị định số 23/2015/NĐ-CP của Chính phủ",
            "Thông tư số 01/2020/TT-BTP của Bộ Tư pháp"
        ],
        "duration": "Giải quyết ngay trong ngày tiếp nhận.",
        "fee": "10.000 VNĐ / trường hợp (trường hợp được tính theo một hoặc nhiều chữ ký trong cùng một văn bản của một người).",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 4.3. Lĩnh vực: khieu_nai_to_cao_xu_phat (10 TTHC)
    # =========================================================================
    {
        "id": "tiep_cong_dan_dinh_ky_va_dot_xuat_cap_xa",
        "name": "Thủ tục tiếp công dân định kỳ và đột xuất tại Trụ sở tiếp công dân / UBND cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Công dân đến Phòng Tiếp công dân UBND cấp xã, xuất trình giấy tờ tùy thân và đăng ký nội dung trình bày.",
            "Bước 2: Cán bộ tiếp công dân lắng nghe, ghi chép nội dung khiếu nại, tố cáo, kiến nghị, phản ánh vào Sổ tiếp công dân.",
            "Bước 3: Hướng dẫn công dân viết đơn đúng quy định hoặc giải thích, tuyên truyền chính sách pháp luật trực tiếp tại chỗ.",
            "Bước 4: Báo cáo Chủ tịch UBND cấp xã tiếp công dân định kỳ (ít nhất 01 ngày/tuần) hoặc đột xuất theo quy định và lập Phiếu tiếp nhận đơn."
        ],
        "documents_required": [
            "Bản chính Căn cước công dân hoặc giấy tờ tùy thân hợp pháp.",
            "Đơn khiếu nại, tố cáo, kiến nghị, phản ánh hoặc tài liệu, chứng cứ liên quan đến nội dung trình bày (nếu có).",
            "Văn bản ủy quyền hợp pháp (trường hợp đại diện theo ủy quyền)."
        ],
        "guidance": "Người tiếp công dân có quyền từ chối tiếp người say rượu bia, người mất năng lực hành vi dân sự, người có hành vi đe dọa, xúc phạm hoặc vụ việc đã được giải quyết đúng pháp luật và có thông báo đình chỉ giải quyết.",
        "submission_place": "Phòng Tiếp công dân UBND cấp xã.",
        "legal_basis": [
            "Luật Tiếp công dân năm 2013",
            "Nghị định số 64/2014/NĐ-CP của Chính phủ quy định chi tiết thi hành một số điều của Luật Tiếp công dân",
            "Thông tư số 04/2021/TT-TTCP ngày 01/10/2021 của Thanh tra Chính phủ"
        ],
        "duration": "Tiếp nhận trực tiếp trong giờ hành chính; Trả lời hoặc thông báo xử lý đơn trong vòng 10 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tiep_nhan_phan_loai_xu_ly_don_thu",
        "name": "Thủ tục tiếp nhận, phân loại và xử lý đơn khiếu nại, tố cáo, kiến nghị, phản ánh",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Tiếp nhận đơn gửi qua đường bưu chính, nộp trực tiếp tại Bộ phận Một cửa/Tiếp dân hoặc qua Cổng DVC.",
            "Bước 2: Cán bộ tiếp nhận kiểm tra điều kiện xử lý đơn, tính hợp lệ của chữ ký và nội dung yêu cầu.",
            "Bước 3: Phân loại đơn: đơn khiếu nại, đơn tố cáo, đơn phản ánh kiến nghị; xác định đơn thuộc hay không thuộc thẩm quyền giải quyết cấp xã.",
            "Bước 4: Ban hành Thông báo thụ lý (đối với đơn thuộc thẩm quyền) hoặc Phiếu hướng dẫn/Phiếu chuyển đơn (đối với đơn không thuộc thẩm quyền)."
        ],
        "documents_required": [
            "Đơn khiếu nại, đơn tố cáo hoặc đơn kiến nghị, phản ánh có ghi rõ ngày tháng năm, họ tên, địa chỉ và chữ ký/điểm chỉ của người viết đơn.",
            "Các tài liệu, chứng cứ kèm theo chứng minh nội dung khiếu nại, tố cáo (nếu có)."
        ],
        "guidance": "Không xử lý đơn không ghi rõ họ tên, địa chỉ người gửi (đơn nặc danh, mạo danh), đơn rách nát chữ viết không rõ hoặc đơn đã được cơ quan có thẩm quyền giải quyết dứt điểm.",
        "submission_place": "Bộ phận Tiếp công dân hoặc Bộ phận Văn thư UBND cấp xã.",
        "legal_basis": [
            "Luật Khiếu nại năm 2011",
            "Luật Tố cáo năm 2018",
            "Thông tư số 05/2021/TT-TTCP ngày 01/10/2021 của Thanh tra Chính phủ quy định quy trình xử lý đơn khiếu nại, đơn tố cáo, đơn kiến nghị, phản ánh"
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận được đơn.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "giai_quyet_khieu_nai_lan_dau_chu_tich_xa",
        "name": "Thủ tục giải quyết khiếu nại lần đầu thuộc thẩm quyền của Chủ tịch UBND cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Thụ lý khiếu nại và ban hành Thông báo thụ lý giải quyết khiếu nại gửi người khiếu nại trong thời hạn 10 ngày.",
            "Bước 2: Xác minh nội dung khiếu nại: làm việc với người khiếu nại, người bị khiếu nại, kiểm tra hồ sơ địa chính, quản lý nhà nước liên quan.",
            "Bước 3: Tổ chức đối thoại trực tiếp giữa Chủ tịch UBND cấp xã với người khiếu nại và các bên có quyền lợi liên quan.",
            "Bước 4: Ban hành Quyết định giải quyết khiếu nại lần đầu và gửi cho người khiếu nại trong thời hạn 03 ngày làm việc kể từ ngày ký."
        ],
        "documents_required": [
            "Đơn khiếu nại hoặc Biên bản ghi nhận nội dung khiếu nại trực tiếp.",
            "Bản sao Quyết định hành chính hoặc tài liệu chứng minh hành vi hành chính của UBND/Chủ tịch UBND cấp xã bị khiếu nại.",
            "Chứng cứ, tài liệu liên quan đến nội dung khiếu nại."
        ],
        "guidance": "Thời hiệu khiếu nại là 90 ngày kể từ ngày nhận được quyết định hành chính hoặc biết được hành vi hành chính. Quá thời hiệu sẽ không được thụ lý giải quyết.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả / Bộ phận Tiếp công dân UBND cấp xã.",
        "legal_basis": [
            "Luật Khiếu nại năm 2011",
            "Nghị định số 124/2020/NĐ-CP ngày 19/10/2020 của Chính phủ quy định chi tiết một số điều và biện pháp thi hành Luật Khiếu nại"
        ],
        "duration": "Không quá 30 ngày làm việc kể từ ngày thụ lý; vùng sâu vùng xa không quá 45 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "giai_quyet_to_cao_thuoc_tham_quyen_chu_tich_xa",
        "name": "Thủ tục thụ lý và giải quyết tố cáo thuộc thẩm quyền của Chủ tịch UBND cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Tiếp nhận đơn tố cáo cán bộ, công chức thuộc quyền quản lý của UBND cấp xã; xác minh họ tên, địa chỉ người tố cáo.",
            "Bước 2: Ban hành Quyết định thụ lý tố cáo và thành lập Tổ xác minh nội dung tố cáo.",
            "Bước 3: Tổ xác minh thu thập tài liệu, làm việc với người bị tố cáo, lập Báo cáo kết quả xác minh nội dung tố cáo.",
            "Bước 4: Chủ tịch UBND cấp xã ban hành Kết luận nội dung tố cáo và thực hiện xử lý cán bộ vi phạm (nếu có)."
        ],
        "documents_required": [
            "Đơn tố cáo (ghi rõ họ tên, địa chỉ, nội dung vi phạm của cán bộ, công chức cấp xã).",
            "Tài liệu, chứng cứ ban đầu chứng minh hành vi vi phạm pháp luật của người bị tố cáo."
        ],
        "guidance": "Cơ quan thụ lý có trách nhiệm giữ bí mật thông tin cá nhân của người tố cáo và áp dụng các biện pháp bảo vệ người tố cáo theo quy định pháp luật.",
        "submission_place": "Bộ phận Tiếp công dân hoặc gửi đơn trực tiếp đến Chủ tịch UBND cấp xã.",
        "legal_basis": [
            "Luật Tố cáo năm 2018",
            "Nghị định số 31/2019/NĐ-CP ngày 10/4/2019 của Chính phủ quy định chi tiết một số điều và biện pháp thi hành Luật Tố cáo"
        ],
        "duration": "Không quá 30 ngày kể từ ngày thụ lý tố cáo; vụ việc phức tạp có thể gia hạn 01 lần không quá 30 ngày.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "hoa_giai_tranh_chap_dat_dai_cap_xa",
        "name": "Hòa giải tranh chấp đất đai tại Ủy ban nhân dân cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Các bên tranh chấp nộp đơn yêu cầu hòa giải tranh chấp đất đai tại UBND cấp xã nơi có đất tranh chấp.",
            "Bước 2: Cán bộ Địa chính phối hợp MTTQ xã thẩm tra nguồn gốc đất, hiện trạng sử dụng đất và thu thập ý kiến các hộ giáp ranh.",
            "Bước 3: Chủ tịch UBND cấp xã thành lập Hội đồng hòa giải tranh chấp đất đai và tổ chức cuộc họp hòa giải có mặt đầy đủ các bên.",
            "Bước 4: Lập Biên bản hòa giải thành hoặc Biên bản hòa giải không thành và gửi các bên trong thời hạn 03 ngày làm việc."
        ],
        "documents_required": [
            "Đơn yêu cầu hòa giải tranh chấp đất đai (nêu rõ ranh giới, diện tích, nguồn gốc đất tranh chấp).",
            "Giấy tờ về quyền sử dụng đất hoặc giấy tờ chứng minh quá trình quản lý, sử dụng thửa đất.",
            "Sơ đồ hiện trạng vị trí thửa đất tranh chấp."
        ],
        "guidance": "Hòa giải tranh chấp đất đai tại UBND cấp xã là thủ tục tiền tố bắt buộc trước khi khởi kiện ra Tòa án nhân dân đối với tranh chấp ai là người có quyền sử dụng đất.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã nơi có đất tranh chấp.",
        "legal_basis": [
            "Luật Đất đai năm 2024",
            "Nghị định số 102/2024/NĐ-CP ngày 30/7/2024 của Chính phủ quy định chi tiết thi hành một số điều của Luật Đất đai"
        ],
        "duration": "Không quá 30 ngày làm việc kể từ ngày UBND cấp xã nhận được đơn yêu cầu.",
        "fee": "Không thu lệ phí hòa giải tranh chấp đất đai.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "hoa_giai_mau_thuan_tranh_chap_dan_su_co_so",
        "name": "Hòa giải mâu thuẫn, tranh chấp dân sự tại Tổ hòa giải cơ sở",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Tổ trưởng Tổ hòa giải tiếp nhận yêu cầu hòa giải hoặc chủ động phân công hòa giải viên khi phát hiện mâu thuẫn.",
            "Bước 2: Hòa giải viên gặp gỡ từng bên, tìm hiểu nguyên nhân mâu thuẫn và vận dụng tình làng nghĩa xóm, quy định pháp luật để thuyết phục.",
            "Bước 3: Tổ chức buổi hòa giải trực tiếp giữa các bên tranh chấp tại Nhà văn hóa thôn/tổ dân phố.",
            "Bước 4: Lập Biên bản hòa giải thành hoặc ghi nhận kết quả vào Sổ theo dõi hoạt động hòa giải ở cơ sở."
        ],
        "documents_required": [
            "Đơn yêu cầu hòa giải hoặc đề nghị miệng của một hoặc các bên mâu thuẫn.",
            "Các tài liệu liên quan đến mâu thuẫn, xích mích (nếu có)."
        ],
        "guidance": "Tổ hòa giải hòa giải các mâu thuẫn gia đình, tranh chấp ranh giới ngõ xóm, đòi nợ dân sự nhỏ, va chạm giao thông nhẹ... Không hòa giải các vụ việc có dấu hiệu hình sự hoặc vi phạm pháp luật nghiêm trọng.",
        "submission_place": "Tổ hòa giải thôn, tổ dân phố nơi các bên cư trú.",
        "legal_basis": [
            "Luật Hòa giải ở cơ sở năm 2013",
            "Nghị định số 15/2014/NĐ-CP của Chính phủ hướng dẫn Luật Hòa giải ở cơ sở"
        ],
        "duration": "Giải quyết trong thời hạn nhanh nhất (thông thường từ 03 đến 07 ngày làm việc).",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "nop_tien_phat_chap_hanh_qd_xu_phat_vphc",
        "name": "Thủ tục nộp tiền phạt và chấp hành quyết định xử phạt vi phạm hành chính tại cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Cá nhân, tổ chức vi phạm nhận Quyết định xử phạt vi phạm hành chính do Chủ tịch UBND cấp xã ban hành.",
            "Bước 2: Nộp tiền phạt trực tiếp tại Kho bạc Nhà nước, ngân hàng thương mại được ủy nhiệm hoặc nộp trực tuyến qua Cổng DVC quốc gia.",
            "Bước 3: Xuất trình biên lai hoặc chứng từ nộp phạt điện tử cho người có thẩm quyền xử phạt tại UBND cấp xã.",
            "Bước 4: Nhận lại tang vật, phương tiện, giấy tờ bị tạm giữ (nếu có) sau khi đã chấp hành xong toàn bộ quyết định xử phạt."
        ],
        "documents_required": [
            "Bản chính Quyết định xử phạt vi phạm hành chính.",
            "Biên lai thu tiền phạt hoặc chứng từ giao dịch nộp phạt thành công trên Cổng DVC.",
            "Biên bản tạm giữ tang vật, phương tiện, giấy phép (nếu có)."
        ],
        "guidance": "Thời hạn nộp tiền phạt là 10 ngày kể từ ngày nhận quyết định xử phạt. Quá thời hạn sẽ bị tính tiền chậm nộp 0,05%/ngày trên tổng số tiền phạt chưa nộp và bị cưỡng chế thi hành.",
        "submission_place": "Bộ phận Tiếp nhận và Trả kết quả UBND cấp xã hoặc nộp trực tuyến qua Cổng Dịch vụ công quốc gia.",
        "legal_basis": [
            "Luật Xử lý vi phạm hành chính năm 2012, sửa đổi bổ sung năm 2020",
            "Nghị định số 118/2021/NĐ-CP ngày 23/12/2021 của Chính phủ quy định chi tiết một số điều và biện pháp thi hành Luật Xử lý vi phạm hành chính"
        ],
        "duration": "Giải quyết trả lại giấy tờ, tang vật ngay sau khi xuất trình chứng từ nộp phạt hợp lệ.",
        "fee": "Theo số tiền phạt ghi trong Quyết định xử phạt vi phạm hành chính.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "hoan_mien_giam_tien_phat_vphc",
        "name": "Thủ tục hoãn, miễn, giảm tiền phạt vi phạm hành chính thuộc thẩm quyền UBND cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Cá nhân bị xử phạt có hoàn cảnh kinh tế đặc biệt khó khăn nộp đơn đề nghị hoãn, miễn, giảm tiền phạt trước khi hết hạn thi hành.",
            "Bước 2: Cán bộ thụ lý kiểm tra hồ sơ, đối chiếu giấy tờ xác nhận hoàn cảnh khó khăn đột xuất.",
            "Bước 3: Chủ tịch UBND cấp xã xem xét điều kiện theo quy định tại Điều 76, 77 Luật Xử lý vi phạm hành chính.",
            "Bước 4: Ban hành Quyết định hoãn, miễn, giảm tiền phạt hoặc thông báo bằng văn bản nêu rõ lý do không đồng ý."
        ],
        "documents_required": [
            "Đơn đề nghị hoãn/miễn/giảm tiền phạt vi phạm hành chính (nêu rõ lý do).",
            "Giấy xác nhận của UBND cấp xã nơi cư trú về hoàn cảnh kinh tế khó khăn đặc biệt do thiên tai, hỏa hoạn, tai nạn, bệnh hiểm nghèo.",
            "Bản sao Quyết định xử phạt vi phạm hành chính."
        ],
        "guidance": "Hoãn thi hành quyết định phạt tiền áp dụng đối với cá nhân bị phạt từ 2.000.000 đồng trở lên khi đang gặp khó khăn đặc biệt về kinh tế. Thời hạn hoãn không quá 03 tháng.",
        "submission_place": "UBND cấp xã nơi đã ban hành quyết định xử phạt vi phạm hành chính.",
        "legal_basis": [
            "Luật Xử lý vi phạm hành chính năm 2012, sửa đổi bổ sung năm 2020",
            "Nghị định số 118/2021/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 05 ngày làm việc kể từ ngày nhận được đơn đề nghị.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "giai_quyet_kien_nghi_phan_anh_hanh_vi_hanh_chinh",
        "name": "Giải quyết kiến nghị, phản ánh của cá nhân, tổ chức về thủ tục hành chính và hành vi hành chính",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Công dân gửi phản ánh kiến nghị về hành vi chậm trễ, gây khó khăn hoặc yêu cầu thêm giấy tờ ngoài quy định của cán bộ cấp xã.",
            "Bước 2: Văn phòng HĐND và UBND cấp xã tiếp nhận, báo cáo Chủ tịch UBND cấp xã chỉ đạo làm rõ.",
            "Bước 3: Kiểm tra camera giám sát Bộ phận Một cửa, đối chiếu phiếu kiểm soát quá trình giải quyết hồ sơ.",
            "Bước 4: Trả lời bằng văn bản cho công dân và yêu cầu cán bộ vi phạm xin lỗi công khai nếu phản ánh đúng sự thật."
        ],
        "documents_required": [
            "Phiếu phản ánh kiến nghị hoặc văn bản trình bày rõ thời gian, địa điểm, họ tên cán bộ và nội dung hành vi hành chính bị phản ánh.",
            "Giấy tiếp nhận hồ sơ hẹn trả kết quả hoặc tài liệu làm căn cứ (nếu có)."
        ],
        "guidance": "Mọi phản ánh kiến nghị chính đáng của người dân đều được xác minh minh bạch, khách quan và công khai kết quả xử lý trên bảng tin cơ quan.",
        "submission_place": "Bộ phận Tiếp dân hoặc Hòm thư góp ý đặt tại Trung tâm Một cửa UBND cấp xã.",
        "legal_basis": [
            "Nghị định số 20/2008/NĐ-CP của Chính phủ",
            "Nghị định số 61/2018/NĐ-CP và Nghị định số 107/2021/NĐ-CP của Chính phủ về thực hiện cơ chế một cửa, một cửa liên thông"
        ],
        "duration": "Không quá 07 ngày làm việc kể từ ngày tiếp nhận phản ánh.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "cong_khai_ket_luan_to_cao_quyet_dinh_khieu_nai",
        "name": "Công khai kết luận nội dung tố cáo và quyết định giải quyết khiếu nại tại UBND cấp xã",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "khieu_nai_to_cao_xu_phat",
        "steps": [
            "Bước 1: Sau khi ban hành Quyết định giải quyết khiếu nại hoặc Kết luận nội dung tố cáo, cán bộ thụ lý lập kế hoạch công khai.",
            "Bước 2: Lựa chọn hình thức công khai: công bố tại cuộc họp cơ quan, niêm yết tại trụ sở UBND cấp xã hoặc đăng trên Trang thông tin điện tử.",
            "Bước 3: Thực hiện niêm yết công khai trong thời hạn ít nhất 15 ngày liên tục (đối với quyết định khiếu nại) theo luật định.",
            "Bước 4: Lập biên bản niêm yết công khai và biên bản kết thúc thời gian niêm yết lưu hồ sơ kiểm tra."
        ],
        "documents_required": [
            "Bản chính Quyết định giải quyết khiếu nại hoặc Kết luận nội dung tố cáo đã có hiệu lực.",
            "Biên bản niêm yết công khai tại trụ sở UBND cấp xã."
        ],
        "guidance": "Việc công khai kết luận tố cáo phải đảm bảo không tiết lộ họ tên, địa chỉ, bút tích và thông tin cá nhân khác của người tố cáo.",
        "submission_place": "Bảng niêm yết công khai trụ sở UBND cấp xã và Trang thông tin điện tử phường/xã.",
        "legal_basis": [
            "Điều 40 Luật Khiếu nại năm 2011",
            "Điều 40 Luật Tố cáo năm 2018",
            "Nghị định số 124/2020/NĐ-CP của Chính phủ"
        ],
        "duration": "Trong thời hạn 15 ngày kể từ ngày ban hành quyết định giải quyết hoặc kết luận.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },

    # =========================================================================
    # 4.4. Lĩnh vực: quoc_phong_quan_su (10 TTHC)
    # =========================================================================
    {
        "id": "dang_ky_nghia_vu_quan_su_lan_dau",
        "name": "Đăng ký nghĩa vụ quân sự lần đầu cho công dân nam đủ 17 tuổi trong năm",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Ban Chỉ huy quân sự cấp xã gửi Lệnh gọi đăng ký nghĩa vụ quân sự đến công dân nam đủ 17 tuổi trong năm trước 10 ngày.",
            "Bước 2: Công dân trực tiếp đến Ban Chỉ huy quân sự cấp xã nơi cư trú để làm thủ tục đăng ký.",
            "Bước 3: Ban Chỉ huy quân sự cấp xã đối chiếu bản gốc Căn cước công dân/Giấy khai sinh; hướng dẫn kê khai Phiếu tự khai sức khỏe.",
            "Bước 4: Cấp Giấy chứng nhận đăng ký nghĩa vụ quân sự cho công dân ngay sau khi đăng ký xong."
        ],
        "documents_required": [
            "Phiếu tự khai sức khỏe nghĩa vụ quân sự theo mẫu.",
            "Bản chụp Căn cước công dân hoặc Giấy khai sinh (mang theo bản chính để đối chiếu).",
            "Lệnh gọi đăng ký nghĩa vụ quân sự của Chỉ huy trưởng Ban Chỉ huy quân sự cấp huyện."
        ],
        "guidance": "Đăng ký nghĩa vụ quân sự lần đầu được tổ chức vào tháng 4 hàng năm theo lệnh gọi của Ban Chỉ huy quân sự cấp huyện.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi công dân thường trú.",
        "legal_basis": [
            "Luật Nghĩa vụ quân sự ngày 19 tháng 6 năm 2015",
            "Nghị định số 13/2016/NĐ-CP ngày 19/02/2016 của Chính phủ quy định trình tự, thủ tục đăng ký và chế độ chính sách của công dân trong thời gian đăng ký, khám sức khỏe nghĩa vụ quân sự"
        ],
        "duration": "01 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_phuc_vu_trong_ngach_du_bi",
        "name": "Đăng ký phục vụ trong ngạch dự bị đối với hạ sĩ quan, binh sĩ xuất ngũ",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Trong thời hạn 15 ngày kể từ ngày xuất ngũ về địa phương, công dân mang hồ sơ đến Ban Chỉ huy quân sự cấp xã để đăng ký.",
            "Bước 2: Cán bộ quân sự tiếp nhận phiếu quân nhân dự bị, đối chiếu Quyết định xuất ngũ.",
            "Bước 3: Ghi danh sách công dân vào Sổ đăng ký quân nhân dự bị cấp xã.",
            "Bước 4: Cấp Giấy chứng nhận đăng ký quân nhân dự bị cho công dân."
        ],
        "documents_required": [
            "Phiếu quân nhân dự bị do đơn vị quân đội cấp khi xuất ngũ.",
            "Bản chụp Quyết định xuất ngũ (kèm bản chính để đối chiếu).",
            "Giấy chứng nhận đăng ký nghĩa vụ quân sự đã cấp trước đó."
        ],
        "guidance": "Hạ sĩ quan, binh sĩ xuất ngũ có trách nhiệm đăng ký vào ngạch dự bị để xếp vào các đơn vị dự bị động viên theo chỉ tiêu của địa phương.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi công dân cư trú.",
        "legal_basis": [
            "Luật Nghĩa vụ quân sự năm 2015",
            "Luật Lực lượng dự bị động viên năm 2019",
            "Nghị định số 13/2016/NĐ-CP của Chính phủ"
        ],
        "duration": "01 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_nghia_vu_quan_su_bo_sung",
        "name": "Đăng ký nghĩa vụ quân sự bổ sung khi thay đổi nơi cư trú, học vấn, sức khỏe",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Công dân đã đăng ký NVQS khi có thay đổi về chức vụ công tác, trình độ học vấn, chuyên môn hoặc tình trạng sức khỏe nộp hồ sơ đăng ký bổ sung.",
            "Bước 2: Ban Chỉ huy quân sự cấp xã đối chiếu các văn bằng, chứng chỉ, hồ sơ y tế mới phát sinh.",
            "Bước 3: Cập nhật thông tin thay đổi vào Sổ đăng ký công dân sẵn sàng nhập ngũ.",
            "Bước 4: Xác nhận nội dung bổ sung vào Giấy chứng nhận đăng ký NVQS của công dân."
        ],
        "documents_required": [
            "Bản chụp các giấy tờ chứng minh sự thay đổi: Bằng tốt nghiệp Đại học/Cao đẳng, Giấy chứng nhận sức khỏe mới...",
            "Bản chính Giấy chứng nhận đăng ký nghĩa vụ quân sự hoặc Giấy chứng nhận đăng ký quân nhân dự bị."
        ],
        "guidance": "Thời gian đăng ký bổ sung thực hiện vào tháng 4 hàng năm hoặc đăng ký thường xuyên khi có thay đổi thông tin quan trọng.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã.",
        "legal_basis": [
            "Luật Nghĩa vụ quân sự năm 2015",
            "Nghị định số 13/2016/NĐ-CP của Chính phủ"
        ],
        "duration": "01 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_nghia_vu_quan_su_chuyen_di",
        "name": "Đăng ký nghĩa vụ quân sự chuyển đi khi thay đổi nơi cư trú hoặc nơi làm việc, học tập",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Công dân trước khi chuyển nơi cư trú hoặc nơi làm việc từ 03 tháng trở lên nộp hồ sơ tại Ban CHQS cấp xã nơi đang đăng ký.",
            "Bước 2: Cán bộ quân sự kiểm tra hồ sơ nghĩa vụ quân sự và lý do chuyển đi.",
            "Bước 3: Lập Phiếu chuyển đăng ký nghĩa vụ quân sự và ghi giảm trong sổ quản lý nguồn sẵn sàng nhập ngũ.",
            "Bước 4: Cấp Giấy giới thiệu đăng ký nghĩa vụ quân sự chuyển đi cho công dân."
        ],
        "documents_required": [
            "Giấy giới thiệu di chuyển đăng ký nghĩa vụ quân sự hoặc Giấy báo trúng tuyển Đại học, Cao đẳng, Quyết định tuyển dụng công tác...",
            "Bản chính Giấy chứng nhận đăng ký nghĩa vụ quân sự.",
            "Bản sao Thông báo thay đổi nơi cư trú (nếu chuyển hộ khẩu thường trú)."
        ],
        "guidance": "Trong thời hạn 10 ngày làm việc kể từ ngày đến nơi cư trú hoặc nơi làm việc mới, công dân phải đến Ban CHQS nơi mới để làm thủ tục đăng ký chuyển đến.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi chuyển đi.",
        "legal_basis": [
            "Luật Nghĩa vụ quân sự năm 2015",
            "Nghị định số 13/2016/NĐ-CP của Chính phủ"
        ],
        "duration": "01 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_nghia_vu_quan_su_chuyen_den",
        "name": "Đăng ký nghĩa vụ quân sự chuyển đến khi thay đổi nơi cư trú hoặc nơi làm việc, học tập",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Trong thời hạn 10 ngày làm việc kể từ ngày đến nơi cư trú mới, công dân nộp hồ sơ tại Ban CHQS cấp xã nơi mới.",
            "Bước 2: Cán bộ quân sự tiếp nhận Giấy giới thiệu di chuyển NVQS và đối chiếu hồ sơ.",
            "Bước 3: Ghi tên công dân vào Sổ đăng ký công dân sẵn sàng nhập ngũ của địa phương mới.",
            "Bước 4: Cấp Giấy xác nhận đăng ký nghĩa vụ quân sự chuyển đến cho công dân."
        ],
        "documents_required": [
            "Giấy giới thiệu di chuyển đăng ký nghĩa vụ quân sự do Ban CHQS cấp xã nơi chuyển đi cấp.",
            "Bản chính Giấy chứng nhận đăng ký nghĩa vụ quân sự.",
            "Bản sao Căn cước công dân hoặc Thông báo xác nhận cư trú tại địa phương mới."
        ],
        "guidance": "Công dân không làm thủ tục đăng ký nghĩa vụ quân sự chuyển đến sẽ bị xử phạt vi phạm hành chính trong lĩnh vực quốc phòng theo quy định.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi chuyển đến.",
        "legal_basis": [
            "Luật Nghĩa vụ quân sự năm 2015",
            "Nghị định số 13/2016/NĐ-CP"
        ],
        "duration": "01 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_nghia_vu_quan_su_tam_vang",
        "name": "Đăng ký nghĩa vụ quân sự tạm vắng",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Công dân đã đăng ký nghĩa vụ quân sự khi vắng mặt tại nơi cư trú từ 03 tháng liên tục trở lên làm thủ tục đăng ký tạm vắng.",
            "Bước 2: Ban CHQS cấp xã tiếp nhận đơn, kiểm tra lý do tạm vắng (đi làm ăn xa, học nghề ngắn hạn...).",
            "Bước 3: Vào Sổ theo dõi công dân tạm vắng trong độ tuổi gọi nhập ngũ.",
            "Bước 4: Cấp Giấy đăng ký nghĩa vụ quân sự tạm vắng cho công dân."
        ],
        "documents_required": [
            "Đơn xin đăng ký nghĩa vụ quân sự tạm vắng (nêu rõ lý do, địa chỉ nơi đến tạm trú và thời gian dự kiến vắng mặt).",
            "Bản chính Giấy chứng nhận đăng ký nghĩa vụ quân sự.",
            "Bản sao Căn cước công dân."
        ],
        "guidance": "Khi có Lệnh gọi khám sức khỏe hoặc Lệnh gọi nhập ngũ, công dân có trách nhiệm trở về địa phương đúng thời gian quy định để thi hành nghĩa vụ quân sự.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi công dân thường trú.",
        "legal_basis": [
            "Luật Nghĩa vụ quân sự năm 2015",
            "Nghị định số 13/2016/NĐ-CP của Chính phủ"
        ],
        "duration": "01 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_tam_hoan_va_mien_goi_nhap_ngu",
        "name": "Đăng ký tạm hoãn gọi nhập ngũ và miễn gọi nhập ngũ thời bình",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Công dân thuộc diện được tạm hoãn hoặc miễn gọi nhập ngũ nộp hồ sơ tại Ban CHQS cấp xã trước thời điểm khám tuyển NVQS.",
            "Bước 2: Hội đồng Nghĩa vụ quân sự cấp xã kiểm tra hồ sơ, đối chiếu tiêu chuẩn theo quy định tại Điều 41 Luật Nghĩa vụ quân sự.",
            "Bước 3: Lập danh sách công dân đủ điều kiện tạm hoãn, miễn nhập ngũ niêm yết công khai tại trụ sở UBND cấp xã trong 20 ngày.",
            "Bước 4: Báo cáo Hội đồng NVQS cấp huyện phê duyệt và thông báo kết quả cho công dân."
        ],
        "documents_required": [
            "Đơn đề nghị tạm hoãn hoặc miễn gọi nhập ngũ (theo mẫu).",
            "Giấy tờ chứng minh thuộc diện tạm hoãn: Giấy xác nhận học sinh, sinh viên đang học hệ chính quy tại các trường đại học, cao đẳng; giấy chứng nhận là lao động duy nhất phải trực tiếp nuôi dưỡng thân nhân không còn khả năng lao động...",
            "Giấy xác nhận người tàn tật, người mắc bệnh hiểm nghèo hoặc con liệt sĩ, con thương binh hạng một (đối với diện miễn nhập ngũ)."
        ],
        "guidance": "Công dân đang theo học đại học, cao đẳng hệ chính quy chỉ được tạm hoãn gọi nhập ngũ trong một khóa đào tạo của một trình độ đào tạo.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã hoặc Hội đồng Nghĩa vụ quân sự cấp xã.",
        "legal_basis": [
            "Điều 41 Luật Nghĩa vụ quân sự năm 2015",
            "Thông tư số 148/2018/TT-BQP ngày 04/10/2018 của Bộ Quốc phòng quy định tuyển chọn và gọi công dân nhập ngũ"
        ],
        "duration": "Xét duyệt theo đợt tuyển quân hàng năm của Hội đồng Nghĩa vụ quân sự địa phương.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tro_cap_dan_quan_bi_om_tai_nan",
        "name": "Trợ cấp đối với dân quân tự vệ bị ốm đau, tai nạn khi thực hiện nhiệm vụ",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Dân quân hoặc thân nhân nộp hồ sơ đề nghị trợ cấp tại Ban Chỉ huy quân sự cấp xã sau khi điều trị ổn định thương tật, ốm đau.",
            "Bước 2: Ban CHQS cấp xã kiểm tra hồ sơ, xác nhận việc dân quân bị ốm đau hoặc tai nạn trong khi thực hiện nhiệm vụ quốc phòng, quân sự.",
            "Bước 3: Lập hồ sơ trình Chủ tịch UBND cấp xã có văn bản gửi Ban CHQS cấp huyện.",
            "Bước 4: Chủ tịch UBND cấp huyện ra Quyết định chi trả trợ cấp tiền ăn, chi phí khám chữa bệnh cho dân quân."
        ],
        "documents_required": [
            "Đơn đề nghị trợ cấp của dân quân tự vệ hoặc thân nhân (theo mẫu).",
            "Giấy ra viện hoặc bản tóm tắt hồ sơ bệnh án của cơ sở khám bệnh, chữa bệnh.",
            "Biên bản tai nạn do Ban Chỉ huy quân sự cấp xã lập (trường hợp bị tai nạn).",
            "Quyết định điều động hoặc kế hoạch giao nhiệm vụ của cấp có thẩm quyền."
        ],
        "guidance": "Chế độ áp dụng cho dân quân tự vệ trong thời gian thực hiện nhiệm vụ theo quyết định điều động của cấp có thẩm quyền mà chưa tham gia bảo hiểm y tế.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi dân quân công tác.",
        "legal_basis": [
            "Luật Dân quân tự vệ ngày 22 tháng 11 năm 2019",
            "Nghị định số 72/2020/NĐ-CP ngày 30/6/2020 của Chính phủ quy định chi tiết một số điều của Luật Dân quân tự vệ về tổ chức xây dựng lực lượng và chế độ, chính sách đối với Dân quân tự vệ"
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "tro_cap_than_nhan_dan_quan_chet_khi_lam_nhiem_vu",
        "name": "Trợ cấp đối với thân nhân khi dân quân tự vệ bị tai nạn dẫn đến chết trong khi làm nhiệm vụ",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Đại diện thân nhân nộp hồ sơ đề nghị trợ cấp tại Ban Chỉ huy quân sự cấp xã.",
            "Bước 2: Ban CHQS cấp xã chủ trì phối hợp với các ban ngành kiểm tra, hoàn thiện hồ sơ xác nhận dân quân hy sinh/tử vong khi làm nhiệm vụ.",
            "Bước 3: UBND cấp xã gửi hồ sơ về UBND cấp huyện thẩm định.",
            "Bước 4: Chủ tịch UBND cấp huyện ban hành Quyết định chi trả trợ cấp tiền tuất một lần và chi phí mai táng cho thân nhân."
        ],
        "documents_required": [
            "Đơn đề nghị trợ cấp của thân nhân dân quân tự vệ (theo mẫu Nghị định 72/2020/NĐ-CP).",
            "Biên bản tai nạn dẫn đến chết của Ban Chỉ huy quân sự cấp xã.",
            "Bản sao Giấy chứng tử hoặc Trích lục khai tử.",
            "Quyết định điều động làm nhiệm vụ quốc phòng, an ninh, phòng chống thiên tai."
        ],
        "guidance": "Thân nhân được hưởng tiền trợ cấp một lần và trợ cấp mai táng bằng mức hỗ trợ đối với người lao động tham gia BHXH bắt buộc theo quy định hiện hành.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi dân quân cư trú trước khi hy sinh.",
        "legal_basis": [
            "Luật Dân quân tự vệ năm 2019",
            "Nghị định số 72/2020/NĐ-CP của Chính phủ"
        ],
        "duration": "Không quá 20 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    },
    {
        "id": "dang_ky_tham_gia_dan_quan_tu_ve_nong_cot",
        "name": "Đăng ký tham gia lực lượng Dân quân tự vệ nòng cốt tại xã, phường, thị trấn",
        "department": "Văn phòng HĐND và UBND",
        "primary_organization_unit_id": "org-cong-an",
        "domain_slug": "quoc_phong_quan_su",
        "steps": [
            "Bước 1: Công dân nam từ đủ 18 tuổi đến hết 45 tuổi, công dân nữ từ đủ 18 tuổi đến hết 40 tuổi nộp đơn tự nguyện tham gia Dân quân tự vệ tại Ban CHQS cấp xã.",
            "Bước 2: Ban CHQS cấp xã phối hợp Công an xã rà soát lý lịch chính trị, sức khỏe và phẩm chất đạo đức của công dân.",
            "Bước 3: Ban Chỉ huy quân sự cấp xã lập danh sách báo cáo Chủ tịch UBND cấp xã phê duyệt kết nạp.",
            "Bước 4: Tổ chức lễ kết nạp và trao Quyết định công nhận công dân hoàn thành nghĩa vụ tham gia Dân quân tự vệ nòng cốt."
        ],
        "documents_required": [
            "Đơn tình nguyện tham gia lực lượng Dân quân tự vệ theo mẫu.",
            "Bản sao Căn cước công dân.",
            "Sơ yếu lý lịch có dán ảnh 4x6cm và có xác nhận của UBND cấp xã nơi cư trú."
        ],
        "guidance": "Thời hạn thực hiện nghĩa vụ tham gia Dân quân tự vệ tại chỗ là 04 năm; Dân quân tự vệ cơ động, thường trực là 03 năm.",
        "submission_place": "Ban Chỉ huy quân sự cấp xã nơi công dân cư trú hoặc làm việc.",
        "legal_basis": [
            "Luật Dân quân tự vệ năm 2019",
            "Thông tư số 29/2020/TT-BQP của Bộ Quốc phòng quy định chi tiết một số điều của Luật Dân quân tự vệ"
        ],
        "duration": "Tổng hợp và xét kết nạp vào tháng 4 hàng năm; thời gian thẩm định hồ sơ không quá 10 ngày làm việc.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [],
        "catalog_status": "approved",
    }
]
