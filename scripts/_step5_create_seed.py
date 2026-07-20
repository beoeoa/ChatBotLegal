import json
from pathlib import Path

# Create FAQ seed data
faq_seed_data = [
    {
        "id": "faq_001",
        "question": "Tôi muốn đăng ký khai sinh cho con, hồ sơ gồm những giấy tờ gì?",
        "answer": "Để đăng ký khai sinh, bạn cần chuẩn bị:\n- Giấy khai sinh (theo mẫu)\n- Giấy chứng sinh của bệnh viện (nếu có)\n- Giấy tờ tùy thân của cha/mẹ (CCCD/CMND)\n- Sổ hộ khẩu gia đình (nếu có)\n\nNộp tại Bộ phận Một cửa của UBND phường nơi mẹ sinh sống.",
        "steps": [
            "Chuẩn bị hồ sơ theo danh mục",
            "Đến Bộ phận Một cửa UBND phường",
            "Nộp hồ sơ và nhận phiếu tiếp nhận",
            "Theo dõi tiến trình giải quyết",
            "Nhận kết quả (Giấy khai sinh)"
        ],
        "form_ids": ["form_khai_sinh_001"],
        "domain": "ho_tich_chung_thuc",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_002",
        "question": "Quy trình sang tên sổ đỏ gồm những bước nào?",
        "answer": "Quy trình sang tên sổ đỏ (đăng ký biến động đất đai):\n1. Lập văn bản thỏa thuận/chuyển nhượng công chứng\n2. Chuẩn bị hồ sơ (sổ đỏ cũ, CCCD, giấy tờ liên quan)\n3. Nộp tại Văn phòng đăng ký đất đai hoặc Bộ phận Một cửa\n4. Tiếp nhận và xử lý hồ sơ\n5. Nhận sổ đỏ mới\n\nThời hạn giải quyết: 15 ngày làm việc kể từ ngày nộp hồ sơ.",
        "steps": [
            "Công chứng văn bản chuyển nhượng/thỏa thuận",
            "Chuẩn bị hồ sơ đầy đủ",
            "Nộp hồ sơ tại cơ quan có thẩm quyền",
            "Thanh toán lệ phí (nếu có)",
            "Nhận kết quả"
        ],
        "form_ids": ["form_bien_dong_dat_001"],
        "domain": "dat_dai_xay_dung",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_003",
        "question": "Tôi bị phạt đỗ xe nơi cấm, phải làm gì?",
        "answer": "Đối với hành vi đỗ xe nơi có biển cấm:\n- Mức phạt: 600.000 - 800.000 đồng (NĐ 168/2024)\n- Không bị tước GPLX\n- Cần nộp phạt trong 15 ngày kể từ ngày lập biên bản\n\nNơi nộp phạt: Ngân hàng Agribank hoặc Kho bạc Nhà nước gần nhất.",
        "steps": [
            "Nhận quyết định xử phạt từ CSGT",
            "Nộp phạt tại ngân hàng/Kho bạc",
            "Giữ lại giấy biên lai nộp phạt",
            "Hoàn thành nghĩa vụ tài chính"
        ],
        "form_ids": [],
        "domain": "trat_tu_do_thi",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_004",
        "question": "Làm thế nào để đăng ký tạm trú cho người nước ngoài?",
        "answer": "Chủ nhà hoặc người nước ngoài có trách nhiệm thông báo lưu trú:\n- Trong vòng 24 giờ kể từ khi người nước ngoài đến\n- Nộp tại Công an quận/huyện nơi lưu trú\n- Hồ sơ: Hộ chiếu còn hạn, giấy cam kết lưu trú\n\nLưu ý: Người nước ngoài cần visa/hộ chiếu hợp lệ.",
        "steps": [
            "Chuẩn bị hộ chiếu còn hạn",
            "Viết giấy cam kết lưu trú",
            "Nộp tại Công an quận/huyện",
            "Nhận xác nhận lưu trú"
        ],
        "form_ids": [],
        "domain": "cu_tru",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_005",
        "question": "Thủ tục xin cấp giấy chứng nhận đủ điều kiện kinh doanh vận tải?",
        "answer": "Để xin cấp giấy chứng nhận ĐKDN vận tải:\n- Nộp tại Sở GTVT Hải Phòng\n- Hồ sơ gồm: Đơn đề nghị, giấy ĐKDN, đăng kiểm xe, bằng lái...\n- Thời hạn: 03 ngày làm việc\n\nKhông thu lệ phí.",
        "steps": [
            "Chuẩn bị hồ sơ theo yêu cầu",
            "Nộp tại Sở GTVT Hải Phòng",
            "Tiếp nhận và xử lý",
            "Nhận kết quả"
        ],
        "form_ids": [],
        "domain": "hanh_chinh_cong",
        "ward_scope": "Hai Phong",
        "review_status": "approved"
    },
    {
        "id": "faq_006",
        "question": "Tôi muốn kết hôn với người nước ngoài, cần làm gì?",
        "answer": "Điều kiện kết hôn giữa công dân Việt Nam và người nước ngoài:\n- Cả hai tự nguyện, đủ tuổi kết hôn (nam ≥20, nữ ≥18)\n- Không thuộc các trường hợp cấm kết hôn\n- Có giấy tờ hợp pháp từ cơ quan có thẩm quyền\n\nNộp tại Phòng Tư pháp UBND quận/huyện nơi một bên cư trú.",
        "steps": [
            "Chuẩn bị hồ sơ (hộ khẩu, CCCD, giấy tờ nước ngoài)",
            "Hợp pháp hóa lãnh sự giấy tờ nước ngoài",
            "Nộp tại Phòng Tư pháp UBND",
            "Tiếp nhận và công bố",
            "Lễ cưới và nhận giấy chứng nhận"
        ],
        "form_ids": [],
        "domain": "ho_tich_chung_thuc",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_007",
        "question": "Quy trình xin phép xây dựng nhà ở riêng lẻ?",
        "answer": "Để xin phép xây dựng:\n- Nộp tại UBND phường/quận\n- Hồ sơ: Đơn đề nghị, giấy tờ đất đai, bản vẽ thiết kế\n- Thời hạn: 15 ngày làm việc\n- Lệ phí: Theo quy định của thành phố\n\nLưu ý: Phải tuân thủ quy hoạch chi tiết 1/500.",
        "steps": [
            "Chuẩn bị hồ sơ (giấy tờ đất, bản vẽ)",
            "Nộp tại UBND phường/quận",
            "Tiếp nhận và thẩm tra",
            "Xem xét và quyết định",
            "Nhận giấy phép xây dựng"
        ],
        "form_ids": ["form_xep_ho_001"],
        "domain": "dat_dai_xay_dung",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_008",
        "question": "Làm sao để đăng ký hộ kinh doanh cá thể?",
        "answer": "Đăng ký hộ kinh doanh:\n- Nộp tại Phòng Đăng ký kinh doanh - Sở KH&ĐT Hải Phòng\n- Hồ sơ: Đơn đăng ký, CCCD, giấy tờ liên quan\n- Thời hạn: 03 ngày làm việc\n- Lệ phí: 100.000 đồng\n\nSau khi đăng ký, nhận mã số thuế và giấy chứng nhận.",
        "steps": [
            "Chuẩn bị hồ sơ đăng ký",
            "Nộp tại Sở KH&ĐT Hải Phòng",
            "Tiếp nhận hồ sơ",
            "Xử lý và cấp giấy chứng nhận",
            "Đăng báo và hoàn tất"
        ],
        "form_ids": [],
        "domain": "hanh_chinh_cong",
        "ward_scope": "Hai Phong",
        "review_status": "approved"
    },
    {
        "id": "faq_009",
        "question": "Tôi muốn khiếu nại quyết định xử phạt vi phạm hành chính?",
        "answer": "Quy trình khiếu nại:\n- Thời hạn: 90 ngày kể từ ngày nhận quyết định\n- Nộp tại cơ quan ra quyết định hoặc Tòa án\n- Hồ sơ: Đơn khiếu nại, quyết định xử phạt, bằng chứng\n\nLưu ý: Có thể khiếu nại hoặc khởi kiện nhưng không đồng thời.",
        "steps": [
            "Soạn đơn khiếu nại",
            "Thu thập bằng chứng liên quan",
            "Nộp tại cơ quan có thẩm quyền",
            "Tiếp nhận và giải quyết",
            "Nhận kết quả giải quyết"
        ],
        "form_ids": [],
        "domain": "khiem_nai_to_cao",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    },
    {
        "id": "faq_010",
        "question": "Thủ tục cấp lại giấy chứng tử khi bị mất?",
        "answer": "Cấp lại giấy chứng tử:\n- Nộp tại UBND phường nơi đã cấp lần đầu\n- Hồ sơ: Đơn đề nghị, giấy tờ tùy thân, lý do cấp lại\n- Không thu lệ phí\n\nThời gian: Trong ngày làm việc.",
        "steps": [
            "Chuẩn bị đơn đề nghị",
            "Mang theo CCCD/CMND",
            "Nộp tại UBND phường",
            "Tiếp nhận và xác minh",
            "Nhận giấy chứng tử mới"
        ],
        "form_ids": [],
        "domain": "ho_tich_chung_thuc",
        "ward_scope": "Le Chan",
        "review_status": "approved"
    }
]

# Write FAQ seed
faq_seed_path = Path("notebook_data/faq_seed.json")
faq_seed_path.write_text(json.dumps(faq_seed_data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(f"OK: Created {faq_seed_path} ({faq_seed_path.stat().st_size} bytes)")
print(f"Total FAQs: {len(faq_seed_data)}")
