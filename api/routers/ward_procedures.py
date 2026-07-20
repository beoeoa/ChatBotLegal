from collections import Counter
import json
from pathlib import Path
import re
import unicodedata
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from api.auth import get_request_role, get_request_user_id
from api.observability import telemetry
from api.data_paths import notebook_data_dir
from time import perf_counter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/procedures", tags=["ward-procedures"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORMS_DATA_DIR = notebook_data_dir() / "forms"
FORMS_MANIFEST_PATH = FORMS_DATA_DIR / "forms_manifest.json"
FORMS_STATUS_PATH = FORMS_DATA_DIR / "forms_download_status.json"
OFFICIAL_FORMS_CANDIDATE_PATH = (
    FORMS_DATA_DIR / "haiphong_official_candidates.json"
)
CLASSIFIED_FORMS_CANDIDATES_PATH = (
    FORMS_DATA_DIR / "official_forms_candidates_classified.json"
)
OFFICIAL_FORMS_INDEX_PATH = (
    FORMS_DATA_DIR / "haiphong_official_form_index.json"
)
OFFICIAL_FORMS_CATALOG_PATH = (
    FORMS_DATA_DIR / "haiphong_official_forms_catalog.json"
)
PRIORITY_FORMS_SUPPLEMENT_PATH = (
    FORMS_DATA_DIR / "priority_official_forms.json"
)
PRIORITY_FORMS_CATALOG_PATH = (
    FORMS_DATA_DIR / "priority_200_forms.json"
)
OFFICIAL_FORMS_FILES_DIR = (
    PROJECT_ROOT / "data" / "uploads" / "forms" / "official_candidates"
)
PRIORITY_FORMS_FILES_DIR = (
    PROJECT_ROOT / "data" / "uploads" / "forms" / "priority_official"
)
FORMS_QUARANTINE_DIR = PROJECT_ROOT / "data" / "quarantine" / "forms_synthetic"

class FormTemplate(BaseModel):
    name: str = Field(..., description="Tên biểu mẫu (ví dụ: Tờ khai đăng ký kết hôn)")
    file_type: str = Field("docx", description="Loại file (docx, pdf)")
    download_url: str = Field(..., description="Đường dẫn tải biểu mẫu")
    # Fail closed when a legacy ward_procedure row does not carry review
    # metadata.  Official/approved status is only granted by the separately
    # reviewed official-form catalog, never inferred from a missing field.
    official_level: str = Field("reference", description="official | reference")
    review_status: str = Field(
        "candidate_pending_review",
        description="approved | candidate_pending_review",
    )

class Procedure(BaseModel):
    id: str = Field(..., description="ID quy trình")
    name: str = Field(..., description="Tên thủ tục hành chính")
    department: str = Field(..., description="Phòng ban phụ trách")
    domain_slug: str = Field(..., description="Mã lĩnh vực")
    steps: List[str] = Field(..., description="Các bước thực hiện")
    documents_required: List[str] = Field(..., description="Hồ sơ cần nộp")
    duration: str = Field(..., description="Thời gian giải quyết")
    fee: str = Field(..., description="Lệ phí giải quyết")
    forms: List[FormTemplate] = Field(default_factory=list, description="Danh sách biểu mẫu trống đính kèm")
from datetime import datetime
from open_notebook.database.repository import repo_query, repo_create, repo_update

# Seed data for ALL of Hai Phong city procedures and forms
HAI_PHONG_PROCEDURES_SEED: List[Dict[str, Any]] = [
    {
        "id": "dang_ky_khai_sinh",
        "name": "Đăng ký khai sinh (Thẩm quyền cấp xã)",
        "department": "Tư pháp - Hộ tịch",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người đi đăng ký khai sinh nộp hồ sơ trực tiếp tại Bộ phận Một cửa của UBND xã/phường/thị trấn trên địa bàn TP Hải Phòng hoặc nộp trực tuyến qua Cổng dịch vụ công Hải Phòng.",
            "Bước 2: Công chức Tư pháp - Hộ tịch tiếp nhận hồ sơ, đối chiếu thông tin trong CSDL quốc gia về dân cư.",
            "Bước 3: Công chức ghi nội dung khai sinh vào Sổ đăng ký khai sinh, cùng người đi đăng ký khai sinh ký tên vào Sổ hộ tịch.",
            "Bước 4: Chủ tịch UBND cấp xã phê duyệt, ký Giấy khai sinh bản chính cấp cho công dân."
        ],
        "documents_required": [
            "Tờ khai đăng ký khai sinh theo mẫu ban hành kèm Thông tư 04/2020/TT-BTP.",
            "Giấy chứng sinh (do cơ sở y tế nơi trẻ sinh ra cấp). Nếu không có giấy chứng sinh thì nộp văn bản của người làm chứng xác nhận về việc sinh.",
            "Trường hợp cha, mẹ đã kết hôn thì phải xuất trình Giấy chứng nhận kết hôn."
        ],
        "duration": "Giải quyết ngay trong ngày tiếp nhận hồ sơ. Nếu nhận hồ sơ sau 15 giờ thì trả kết quả vào ngày làm việc tiếp theo.",
        "fee": "Miễn phí hoàn toàn đối với việc đăng ký khai sinh đúng hạn, trẻ em dưới 6 tuổi, người có công.",
        "forms": [
            {
                "name": "Tờ khai đăng ký khai sinh (Thông tư 04/2020/TT-BTP)",
                "file_type": "docx",
                "download_url": "/api/procedures/dang_ky_khai_sinh/forms/0"
            }
        ]
    },
    {
        "id": "dang_ky_ket_hon",
        "name": "Đăng ký kết hôn trong nước (Cấp xã)",
        "department": "Tư pháp - Hộ tịch",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Hai bên nam, nữ trực tiếp có mặt tại Bộ phận Một cửa UBND cấp xã nơi một trong hai bên thường trú hoặc tạm trú để nộp hồ sơ.",
            "Bước 2: Cán bộ hộ tịch tiếp nhận hồ sơ, kiểm tra điều kiện kết hôn theo Luật Hôn nhân và Gia đình.",
            "Bước 3: Sau khi xác nhận hai bên hoàn toàn tự nguyện và đủ điều kiện kết hôn, công chức hộ tịch ghi việc kết hôn vào Sổ hộ tịch.",
            "Bước 4: Nam, nữ ký tên vào Giấy chứng nhận kết hôn và Sổ hộ tịch. Chủ tịch UBND xã ký và trao Giấy chứng nhận kết hôn cho hai bên."
        ],
        "documents_required": [
            "Tờ khai đăng ký kết hôn theo mẫu (hai bên có thể khai chung vào một tờ khai).",
            "Giấy xác nhận tình trạng hôn nhân (Giấy độc thân) do UBND cấp xã nơi cư trú trước đó cấp nếu cư trú ngoài địa bàn đăng ký kết hôn.",
            "Căn cước công dân của hai bên nam, nữ."
        ],
        "duration": "Trong thời hạn 3 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ. Trường hợp cần xác minh thêm điều kiện kết hôn thì thời hạn không quá 5 ngày làm việc.",
        "fee": "Miễn phí lệ phí đăng ký kết hôn đối với công dân Việt Nam cư trú trong nước.",
        "forms": [
            {
                "name": "Tờ khai đăng ký kết hôn chuẩn quốc gia",
                "file_type": "docx",
                "download_url": "/api/procedures/dang_ky_ket_hon/forms/0"
            }
        ]
    },
    {
        "id": "xac_nhan_doc_than",
        "name": "Cấp Giấy xác nhận tình trạng hôn nhân (Giấy xác nhận độc thân)",
        "department": "Tư pháp - Hộ tịch",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Công dân nộp hồ sơ trực tiếp tại Bộ phận Một cửa của UBND cấp xã nơi cư trú hoặc nộp trực tuyến qua Cổng dịch vụ công trực tuyến Hải Phòng.",
            "Bước 2: Công chức tư pháp - hộ tịch kiểm tra thông tin hộ tịch, sổ đăng ký kết hôn để xác minh tình trạng.",
            "Bước 3: Trình Chủ tịch UBND xã/phường phê duyệt và ký Giấy xác nhận tình trạng hôn nhân.",
            "Bước 4: Trả kết quả Giấy xác nhận tình trạng hôn nhân cho công dân (Giấy có giá trị sử dụng 6 tháng kể từ ngày cấp)."
        ],
        "documents_required": [
            "Tờ khai cấp Giấy xác nhận tình trạng hôn nhân theo mẫu quy định.",
            "Bản sao CCCD hoặc hộ chiếu của người yêu cầu.",
            "Trường hợp đã ly hôn hoặc vợ/chồng đã mất thì phải nộp bản án ly hôn có hiệu lực hoặc Giấy chứng tử của vợ/chồng."
        ],
        "duration": "Không quá 3 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ. Trường hợp cần xác minh qua các địa phương khác thì không quá 10 ngày làm việc.",
        "fee": "15.000 VNĐ / bản xác nhận. Miễn lệ phí đối với hộ nghèo, người cao tuổi, người khuyết tật.",
        "forms": [
            {
                "name": "Tờ khai cấp Giấy xác nhận tình trạng hôn nhân",
                "file_type": "docx",
                "download_url": "/api/procedures/xac_nhan_doc_than/forms/0"
            }
        ]
    },
    {
        "id": "dang_ky_khai_tu",
        "name": "Đăng ký khai tử (Cấp xã)",
        "department": "Tư pháp - Hộ tịch",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": [
            "Bước 1: Người có trách nhiệm đi đăng ký khai tử nộp hồ sơ tại Bộ phận Một cửa của UBND cấp xã nơi người chết cư trú cuối cùng.",
            "Bước 2: Công chức Tư pháp - Hộ tịch kiểm tra hồ sơ, đối chiếu thông tin và ghi nội dung khai tử vào Sổ hộ tịch.",
            "Bước 3: Người đi khai tử ký tên vào Sổ hộ tịch. Chủ tịch UBND xã ký và cấp Trích lục khai tử cho gia đình."
        ],
        "documents_required": [
            "Tờ khai đăng ký khai tử theo mẫu quy định.",
            "Giấy báo tử hoặc giấy tờ thay thế Giấy báo tử do cơ quan có thẩm quyền cấp (Ví dụ: Giấy xác nhận của công an nếu chết do tai nạn, bản án của Tòa án).",
            "Căn cước công dân của người chết (nếu có) để thu hồi hoặc cập nhật trạng thái."
        ],
        "duration": "Giải quyết ngay trong ngày làm việc khi tiếp nhận hồ sơ. Nếu nộp sau 15 giờ thì trả kết quả vào ngày làm việc tiếp theo.",
        "fee": "Miễn phí hoàn toàn đối với việc đăng ký khai tử đúng hạn.",
        "forms": [
            {
                "name": "Tờ khai đăng ký khai tử (Thông tư 04/2020/TT-BTP)",
                "file_type": "docx",
                "download_url": "/api/procedures/dang_ky_khai_tu/forms/0"
            }
        ]
    },
    {
        "id": "cap_giay_phep_xay_dung",
        "name": "Cấp giấy phép xây dựng nhà ở riêng lẻ đô thị (Thẩm quyền cấp quận/huyện tiếp nhận hướng dẫn tại phường)",
        "department": "Địa chính - Xây dựng - Đô thị - Môi trường",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Chủ đầu tư nộp hồ sơ đề nghị cấp phép tại Bộ phận Một cửa UBND cấp xã để kiểm tra hiện trạng đất đai, hoặc nộp trực tiếp tại UBND cấp Quận/Huyện.",
            "Bước 2: Cán bộ Địa chính - Xây dựng phường/xã kiểm tra quy hoạch đô thị, hành lang chỉ giới giao thông đường bộ và cam kết an toàn liền kề.",
            "Bước 3: Hồ sơ chuyển phòng Quản lý đô thị cấp Quận/Huyện thẩm định thiết kế bản vẽ thi công.",
            "Bước 4: Nhận Giấy phép xây dựng kèm bản vẽ thiết kế có đóng dấu phê duyệt."
        ],
        "documents_required": [
            "Đơn đề nghị cấp giấy phép xây dựng nhà ở riêng lẻ (Mẫu ban hành kèm Nghị định 15/2021/NĐ-CP).",
            "Bản sao Sổ đỏ (Giấy chứng nhận quyền sử dụng đất, quyền sở hữu nhà ở).",
            "02 bộ bản vẽ thiết kế xây dựng kèm theo phương án móng, mặt bằng, mặt đứng, mặt cắt.",
            "Bản cam kết bảo đảm an toàn đối với công trình liền kề, lân cận."
        ],
        "duration": "Không quá 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "50.000 VNĐ / giấy phép.",
        "forms": [
            {
                "name": "Đơn đề nghị cấp giấy phép xây dựng nhà ở riêng lẻ",
                "file_type": "docx",
                "download_url": "/api/procedures/cap_giay_phep_xay_dung/forms/0"
            },
            {
                "name": "Bản cam kết bảo đảm an toàn cho công trình liền kề",
                "file_type": "docx",
                "download_url": "/api/procedures/cap_giay_phep_xay_dung/forms/1"
            }
        ]
    },
    {
        "id": "tro_cap_xa_hoi",
        "name": "Thủ tục đề nghị hưởng trợ cấp xã hội hàng tháng (Cấp xã)",
        "department": "Lao động - Thương binh và Xã hội",
        "domain_slug": "an_sinh_y_te_giao_duc",
        "steps": [
            "Bước 1: Đối tượng hoặc người giám hộ nộp hồ sơ trực tiếp tại UBND cấp xã nơi cư trú thường trú.",
            "Bước 2: Cán bộ Lao động - Thương binh & Xã hội tiếp nhận, đối chiếu các điều kiện bảo trợ theo Nghị định 20/2021/NĐ-CP.",
            "Bước 3: Hội đồng xét duyệt trợ giúp xã hội cấp xã họp xét duyệt và niêm yết công khai danh sách tại trụ sở UBND xã/phường trong 7 ngày.",
            "Bước 4: Chuyển hồ sơ lên phòng Lao động - Thương binh & Xã hội cấp Quận/Huyện quyết định chi trả trợ cấp hàng tháng."
        ],
        "documents_required": [
            "Tờ khai đề nghị trợ giúp xã hội theo Mẫu số 1a, 1b ban hành kèm theo Nghị định 20/2021/NĐ-CP.",
            "Bản sao CCCD hoặc giấy tờ xác nhận thông tin cư trú của đối tượng.",
            "Giấy tờ chứng minh hoàn cảnh đặc biệt (Giấy xác nhận khuyết tật, quyết định nuôi dưỡng trẻ mồ côi...)."
        ],
        "duration": "Trong vòng 15 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Miễn phí hoàn toàn.",
        "forms": [
            {
                "name": "Tờ khai đề nghị trợ giúp xã hội (Mẫu 1a/1b)",
                "file_type": "docx",
                "download_url": "/api/procedures/tro_cap_xa_hoi/forms/0"
            }
        ]
    },
    {
        "id": "dang_ky_ho_kinh_doanh",
        "name": "Đăng ký thành lập Hộ kinh doanh cá thể",
        "department": "Tài chính - Kế hoạch (Tiếp nhận hướng dẫn tại phường)",
        "domain_slug": "hanh_chinh_cong",
        "steps": [
            "Bước 1: Cá nhân hoặc đại diện hộ gia đình chuẩn bị hồ sơ và nộp tại Bộ phận Một cửa của UBND cấp Quận/Huyện (hoặc nộp bản khai thông tin hướng dẫn tại phường).",
            "Bước 2: Cơ quan đăng ký kinh doanh cấp huyện thụ lý, kiểm tra tính hợp lệ của ngành nghề kinh doanh và tên hộ kinh doanh.",
            "Bước 3: Cấp Giấy chứng nhận đăng ký hộ kinh doanh cho công dân.",
            "Bước 4: Công dân nhận giấy chứng nhận và thực hiện nghĩa vụ đăng ký thuế ban đầu."
        ],
        "documents_required": [
            "Giấy đề nghị đăng ký hộ kinh doanh theo mẫu quy định tại Thông tư 01/2021/TT-BKHĐT.",
            "Danh sách các cá nhân thành viên hộ gia đình đăng ký thành lập hộ kinh doanh (nếu có).",
            "Bản sao hợp lệ CCCD của cá nhân tham gia hộ kinh doanh.",
            "Bản sao hợp đồng thuê nhà hoặc giấy chứng nhận quyền sở hữu địa điểm kinh doanh."
        ],
        "duration": "Trong vòng 3 ngày làm việc kể từ ngày nhận hồ sơ hợp lệ.",
        "fee": "100.000 VNĐ / lần cấp.",
        "forms": [
            {
                "name": "Giấy đề nghị đăng ký hộ kinh doanh",
                "file_type": "docx",
                "download_url": "/api/procedures/dang_ky_ho_kinh_doanh/forms/0"
            }
        ]
    },
    {
        "id": "sang_ten_so_do",
        "name": "Đăng ký biến động quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất (Sang tên sổ đỏ)",
        "department": "Địa chính - Xây dựng - Đô thị - Môi trường",
        "domain_slug": "dat_dai_xay_dung",
        "steps": [
            "Bước 1: Các bên chuẩn bị Hợp đồng chuyển nhượng/tặng cho quyền sử dụng đất được công chứng tại Văn phòng công chứng.",
            "Bước 2: Người sử dụng đất nộp hồ sơ tại Bộ phận Một cửa của UBND cấp xã hoặc Chi nhánh Văn phòng đăng ký đất đai cấp huyện.",
            "Bước 3: Công chức Địa chính - Xây dựng xã tiếp nhận, kiểm tra hiện trạng sử dụng đất, nguồn gốc đất và xác nhận hồ sơ đủ điều kiện biến động.",
            "Bước 4: Hồ sơ chuyển đến Văn phòng đăng ký đất đai để cập nhật trang 4 Sổ đỏ hoặc cấp Sổ đỏ mới cho người mua."
        ],
        "documents_required": [
            "Tờ khai đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 09/ĐK).",
            "Hợp đồng chuyển nhượng, tặng cho quyền sử dụng đất đã được công chứng.",
            "Bản gốc Giấy chứng nhận quyền sử dụng đất (Sổ đỏ) đã cấp.",
            "Tờ khai thuế thu nhập cá nhân và Tờ khai lệ phí trước bạ."
        ],
        "duration": "Không quá 10 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ.",
        "fee": "Lệ phí địa chính: 15.000 VNĐ; Lệ phí trước bạ: 0.5% giá trị chuyển nhượng; Thuế thu nhập cá nhân: 2% giá trị chuyển nhượng (nếu không được miễn).",
        "forms": [
            {
                "name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 09/ĐK)",
                "file_type": "docx",
                "download_url": "/api/procedures/sang_ten_so_do/forms/0",
                "official_level": "reference",
                "review_status": "candidate_pending_review"
            }
        ]
    }
]

@router.get("", response_model=List[Procedure])
async def list_procedures(
    department: Optional[str] = Query(None, description="Lọc theo phòng ban"),
    domain: Optional[str] = Query(None, description="Lọc theo mã lĩnh vực (domain slug)"),
    query: Optional[str] = Query(None, description="Tìm kiếm theo tên thủ tục")
):
    try:
        # Load from SurrealDB dynamically
        db_procs = await repo_query("SELECT * FROM ward_procedure ORDER BY name ASC;", {})
        if not db_procs:
            # Fallback to seed list if database is empty
            db_procs = HAI_PHONG_PROCEDURES_SEED
        
        results = []
        for proc_data in db_procs:
            # Trích xuất ID gốc từ download_url của forms nếu có
            proc_id = str(proc_data.get("id") or "")
            forms_list = proc_data.get("forms") or []
            if forms_list and len(forms_list) > 0:
                url = forms_list[0].get("download_url") or ""
                match = re.search(r"/api/procedures/([^/]+)/forms", url)
                if match:
                    proc_id = match.group(1)
            if ":" in proc_id:
                proc_id = proc_id.split(":", 1)[1]
                
            proc = Procedure(
                id=proc_id,
                name=proc_data.get("name"),
                department=proc_data.get("department"),
                domain_slug=proc_data.get("domain_slug"),
                steps=proc_data.get("steps") or [],
                documents_required=proc_data.get("documents_required") or [],
                duration=proc_data.get("duration"),
                fee=proc_data.get("fee"),
                forms=[FormTemplate(**normalize_form_record(f)) for f in (proc_data.get("forms") or [])]
            )
            
            if department and department.lower() not in proc.department.lower():
                continue
            if domain and domain != proc.domain_slug:
                continue
            if query and query.lower() not in proc.name.lower():
                continue
            results.append(proc)
        return results
    except Exception as exc:
        # Robust fallback
        results = []
        for proc_data in HAI_PHONG_PROCEDURES_SEED:
            proc = Procedure(
                id=proc_data["id"],
                name=proc_data["name"],
                department=proc_data["department"],
                domain_slug=proc_data["domain_slug"],
                steps=proc_data["steps"],
                documents_required=proc_data["documents_required"],
                duration=proc_data["duration"],
                fee=proc_data["fee"],
                forms=[FormTemplate(**normalize_form_record(f)) for f in proc_data["forms"]]
            )
            if department and department.lower() not in proc.department.lower():
                continue
            if domain and domain != proc.domain_slug:
                continue
            if query and query.lower() not in proc.name.lower():
                continue
            results.append(proc)
        return results





def normalize_form_display_name(value: Any, fallback: str = "Biểu mẫu") -> str:
    """Return a citizen-friendly form title from metadata or raw filenames.

    Priority: form_title/detected_form_name/title/name first.
    If the preferred value already looks human-readable, keep it.
    Only apply filename cleanup for raw slugs/paths/urls.
    """
    preferred_keys = (
        "form_title",
        "detected_form_name",
        "title",
        "name",
        "display_name",
    )
    filename_keys = (
        "file_name",
        "source_package_title",
        "source_package_path",
        "local_path",
        "source_url",
        "download_url",
    )

    def _looks_human_readable(text: str) -> bool:
        value = (text or "").strip()
        if not value:
            return False
        lower = value.casefold()
        if ".signed" in lower:
            return False
        if re.search(r"(?i)\.(?:pdf|docx?|xlsx?|xls|zip|rar|html?)$", value):
            return False
        if re.search(r"(?i)\b[0-9a-f]{16,}\b", value):
            return False
        # Human titles usually have spaces or Vietnamese diacritics.
        if " " in value or re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", lower):
            return True
        # Short clean labels without separators are also acceptable.
        if re.fullmatch(r"[A-Za-zÀ-ỹĐđ0-9().,;:/+\- ]{3,80}", value) and " " in value:
            return True
        return False

    def _cleanup_filename_like(raw: str) -> str:
        from urllib.parse import unquote, urlparse

        text = unquote(raw or "").replace("\\", "/").strip()
        parsed = urlparse(text)
        if parsed.scheme or parsed.netloc or text.startswith("/"):
            text = parsed.path or text
        text = text.split("?")[0].split("#")[0].rstrip("/")
        if "/" in text:
            text = text.rsplit("/", 1)[-1]

        text = re.sub(r"(?i)\.signed\d*.*$", "", text)
        text = re.sub(r"(?i)(?:\.(?:pdf|docx?|xlsx?|xls|zip|rar|html?))+$", "", text)
        text = re.sub(r"(?i)\b[0-9a-f]{16,}\b", "", text)
        text = re.sub(r"(?i)^[0-9a-f]{12,}[-_]+", "", text)
        text = re.sub(r"\b\d{15,}\b", "", text)
        text = re.sub(r"\b20\d{6,}\b", "", text)
        text = text.replace("..", " ")
        text = re.sub(r"[_\-.]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" -_.,;:")
        if not text:
            return ""

        lower = text.casefold()
        phrase_replacements = [
            ("qd", "Quyết định"),
            ("qđ", "Quyết định"),
            ("ubnd", "UBND"),
            ("tthc", "thủ tục hành chính"),
            ("tt btp", "TT-BTP"),
            ("bo tu phap", "Bộ Tư pháp"),
            ("hai phong", "Hải Phòng"),
            ("to khai", "Tờ khai"),
            ("dang ky", "đăng ký"),
            ("khai sinh", "khai sinh"),
            ("khai tu", "khai tử"),
            ("ket hon", "kết hôn"),
            ("xac nhan", "xác nhận"),
            ("tinh trang hon nhan", "tình trạng hôn nhân"),
            ("cu tru", "cư trú"),
            ("dat dai", "đất đai"),
            ("xay dung", "xây dựng"),
            ("linh vuc", "lĩnh vực"),
            ("giao duc", "giáo dục"),
            ("dao tao", "đào tạo"),
            ("nuoc ngoai", "nước ngoài"),
            ("thu tuc", "thủ tục"),
        ]
        for src, dst in phrase_replacements:
            lower = re.sub(rf"\b{re.escape(src)}\b", dst, lower, flags=re.IGNORECASE)

        def smart_word(word: str) -> str:
            if not word:
                return word
            if word.isupper() or any(ch.isdigit() for ch in word):
                return word.upper() if word.casefold() in {"ubnd", "tt-btp"} else word
            if word.casefold() in {"và", "về", "của", "cho", "tại", "theo", "với", "trong", "ngoài"}:
                return word.casefold()
            return word[:1].upper() + word[1:]

        words = [smart_word(part) for part in lower.split()]
        name = re.sub(r"\s+", " ", " ".join(words)).strip()
        return name

    preferred = ""
    filename_like = ""
    if isinstance(value, dict):
        record = value
        preferred = next(
            (str(record.get(key) or "").strip() for key in preferred_keys if str(record.get(key) or "").strip()),
            "",
        )
        filename_like = next(
            (str(record.get(key) or "").strip() for key in filename_keys if str(record.get(key) or "").strip()),
            "",
        )
        # If preferred is still raw slug/filename, treat as filename-like.
        if preferred and not _looks_human_readable(preferred):
            filename_like = preferred or filename_like
            preferred = ""
    else:
        raw = str(value or "").strip()
        if _looks_human_readable(raw):
            preferred = raw
        else:
            filename_like = raw

    if preferred and _looks_human_readable(preferred):
        return re.sub(r"\s+", " ", preferred).strip() or fallback

    cleaned = _cleanup_filename_like(filename_like or preferred)
    return cleaned or fallback

FORM_DOMAIN_ALIASES: Dict[str, str] = {
    "ho_tich": "ho_tich_chung_thuc",
    "chung_thuc": "ho_tich_chung_thuc",
    "cu_tru": "hanh_chinh_cong",
    "cu_tru_an_ninh": "hanh_chinh_cong",
    "khieu_nai": "khieu_nai_to_cao_xu_phat",
    "to_cao": "khieu_nai_to_cao_xu_phat",
    "xu_phat": "khieu_nai_to_cao_xu_phat",
    "khieu_nai_to_cao": "khieu_nai_to_cao_xu_phat",
}


def normalize_form_domain(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return FORM_DOMAIN_ALIASES.get(text, text)


def infer_form_domain(record: Dict[str, Any]) -> str | None:
    text = _normalized_search_text(
        " ".join(
            str(record.get(key) or "")
            for key in (
                "form_title",
                "detected_form_name",
                "source_package_title",
                "source_package_path",
                "local_path",
                "file_name",
            )
        )
    )
    if any(
        phrase in text
        for phrase in (
            "khai sinh",
            "ket hon",
            "khai tu",
            "tinh trang hon nhan",
            "nhan cha me con",
            "ho tich",
            "chung thuc",
        )
    ):
        return "ho_tich_chung_thuc"
    if any(phrase in text for phrase in ("dat dai", "bien dong dat dai", "so do", "xay dung")):
        return "dat_dai_xay_dung"
    if any(phrase in text for phrase in ("khieu nai", "to cao", "xu phat")):
        return "khieu_nai_to_cao_xu_phat"
    if any(phrase in text for phrase in ("cu tru", "tam tru", "thuong tru", "ct01", "ct02", "ho kinh doanh")):
        return "hanh_chinh_cong"
    if any(phrase in text for phrase in ("tro cap", "bao tro", "y te", "giao duc")):
        return "an_sinh_y_te_giao_duc"
    return None


def normalize_form_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Copy a form record and normalize user-facing display fields."""
    normalized = dict(record or {})
    display_name = normalize_form_display_name(normalized)
    normalized["name"] = display_name
    normalized["form_title"] = display_name
    normalized["display_name"] = display_name
    normalized_domain = normalize_form_domain(normalized.get("domain"))
    if not normalized_domain or normalized_domain == "unknown":
        normalized_domain = infer_form_domain(normalized)
    normalized["domain"] = normalized_domain or normalized.get("domain")
    if normalized.get("suggested_domain"):
        normalized["suggested_domain"] = normalize_form_domain(normalized.get("suggested_domain")) or normalized.get("suggested_domain")
    return normalized


def normalize_form_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_form_record(record) for record in (records or [])]

def _find_seed_procedure(proc_id: str) -> Dict[str, Any] | None:
    for proc in HAI_PHONG_PROCEDURES_SEED:
        if proc.get("id") == proc_id:
            return proc
    return None


def _safe_download_filename(value: str, extension: str = "docx") -> str:
    value = unicodedata.normalize("NFKD", value or "bieu-mau")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:80] or "bieu-mau"
    extension = (extension or "docx").lstrip(".") or "docx"
    return f"{value}.{extension}"

FORM_FILE_ALIASES: Dict[str, List[str]] = {
    "xac_nhan_doc_than": [
        "xac_nhan_doc_than.doc",
        "priority_official/to-khai-xac-nhan-tinh-trang-hon-nhan.pdf",
        "priority_official/hai-phong-xac-nhan-tinh-trang-hon-nhan-source.pdf",
    ],
    "dang_ky_ket_hon": [
        "priority_official/to-khai-dang-ky-ket-hon.pdf",
        "priority_official/hai-phong-dang-ky-ket-hon-source.pdf",
    ],
    "dang_ky_khai_sinh": [
        "priority_official/to-khai-dang-ky-khai-sinh.pdf",
    ],
    "dang_ky_khai_tu": [
        "phu_luc_bieu_mau_ho_tich.doc",
    ],
    "sang_ten_so_do": [
        "sang_ten_so_do_form_0.docx",
    ],
}


def _validate_form_file_integrity(path_value: str | Path) -> tuple[bool, str]:
    """Validate form file magic/container before download.

    Returns (is_valid, reason).
    - PDF must start with %PDF
    - DOCX must be a ZIP with [Content_Types].xml
    - DOC expects OLE magic; HTML/error pages are invalid
    """
    import zipfile

    path_obj = Path(path_value)
    if not path_obj.is_absolute():
        path_obj = PROJECT_ROOT / path_obj
    if not path_obj.exists() or not path_obj.is_file():
        return False, "missing_file"
    try:
        size = path_obj.stat().st_size
    except OSError:
        return False, "stat_error"
    if size < 256:
        return False, f"too_small:{size}"

    try:
        head = path_obj.read_bytes()[:256]
    except OSError:
        return False, "read_error"

    lowered = head.lower()
    if b"mock word document" in lowered or b"seed form" in lowered:
        return False, "mock_or_seed_placeholder"
    if b"<!doctype html" in lowered or b"<html" in lowered:
        return False, "html_error_page"

    name_lower = path_obj.name.lower()
    # PDF
    if name_lower.endswith(".pdf") or name_lower.endswith(".pdf.pdf"):
        if head.startswith(b"%PDF"):
            return True, "ok_pdf"
        return False, "bad_pdf_magic"
    # DOCX
    if name_lower.endswith(".docx") or name_lower.endswith(".docx.docx"):
        try:
            if not zipfile.is_zipfile(path_obj):
                return False, "not_zip_container"
            with zipfile.ZipFile(path_obj, "r") as zf:
                names = set(zf.namelist())
            if "[Content_Types].xml" not in names:
                return False, "missing_content_types_xml"
            if not any(n.startswith("word/") for n in names):
                return False, "missing_word_parts"
            return True, "ok_docx"
        except Exception as exc:  # noqa: BLE001
            return False, f"docx_open_error:{exc}"
    # DOC (OLE)
    if name_lower.endswith(".doc") or name_lower.endswith(".doc.doc"):
        if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return True, "ok_doc_ole"
        if head.startswith(b"PK"):
            return False, "zip_container_but_doc_extension"
        return False, "bad_doc_magic"

    # Unknown extension: allow only if not HTML/mock
    return True, "ok_other"


def _invalid_form_file_response(
    *,
    procedure_id: str | None = None,
    form_index: int | None = None,
    form_id: str | None = None,
    form_name: str | None = None,
    reason: str | None = None,
):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=422,
        content={
            "code": "form_file_invalid",
            "detail": "Biểu mẫu chưa có file hợp lệ, cần admin cập nhật",
            "message": "Biểu mẫu chưa có file hợp lệ, cần admin cập nhật",
            "needs_official_file": True,
            "procedure_id": procedure_id,
            "form_index": form_index,
            "form_id": form_id,
            "form_name": form_name,
            "reason": reason,
        },
    )


def _is_real_form_file(path_value: str | None) -> bool:
    if not path_value:
        return False
    ok, _reason = _validate_form_file_integrity(path_value)
    return ok



def _iter_form_file_candidates(procedure_id: str, form_index: int, form: Dict[str, Any] | None = None) -> list[str]:
    form = form or {}
    candidates: List[str] = []
    for key in ("local_path", "file_path", "path"):
        value = form.get(key)
        if value and isinstance(value, str):
            candidates.append(value)
    for alias in FORM_FILE_ALIASES.get(procedure_id, []):
        candidates.append(str(PROJECT_ROOT / "data" / "uploads" / "forms" / alias))
    for ext in ("docx", "doc", "pdf"):
        candidates.append(str(PROJECT_ROOT / "data" / "uploads" / "forms" / f"{procedure_id}_form_{form_index}.{ext}"))
        candidates.append(str(PROJECT_ROOT / "data" / "uploads" / "forms" / "priority_official" / f"{procedure_id}_form_{form_index}.{ext}"))
    return candidates


def _resolve_form_file_candidate(
    procedure_id: str,
    form_index: int,
    form: Dict[str, Any] | None = None,
) -> tuple[str | None, bool, str]:
    """Return a candidate file path and integrity status.

    Prefer a valid file when one exists; otherwise return the first existing invalid
    candidate so the download endpoint can report 422 instead of pretending the
    form is missing.
    """
    first_invalid: tuple[str, str] | None = None
    for candidate in _iter_form_file_candidates(procedure_id, form_index, form):
        p = Path(candidate)
        absolute = p if p.is_absolute() else PROJECT_ROOT / p
        if not absolute.exists() or not absolute.is_file():
            continue
        ok, reason = _validate_form_file_integrity(absolute)
        resolved = str(absolute.resolve())
        if ok:
            return resolved, True, reason
        if first_invalid is None:
            first_invalid = (resolved, reason)
    if first_invalid:
        return first_invalid[0], False, first_invalid[1]
    return None, False, "missing_file"


def _resolve_real_form_file(procedure_id: str, form_index: int, form: Dict[str, Any] | None = None) -> str | None:
    path, ok, _reason = _resolve_form_file_candidate(procedure_id, form_index, form)
    return path if ok else None



@router.get("/{proc_id}/forms/{form_index}")
async def download_seed_procedure_form(proc_id: str, form_index: int):
    """Serve only real uploaded/official form files.

    Seed metadata may still describe a form, but temporary generated content
    must never be returned as an official downloadable template.
    """
    import os
    from fastapi.responses import FileResponse, JSONResponse

    proc = _find_seed_procedure(proc_id)
    if not proc:
        return JSONResponse(
            status_code=404,
            content={
                "code": "form_unavailable",
                "message": "Biểu mẫu này chưa có file chính thức đã duyệt",
                "needs_official_file": True,
                "procedure_id": proc_id,
                "form_index": form_index,
            },
        )

    forms = proc.get("forms") or []
    if form_index < 0 or form_index >= len(forms):
        return JSONResponse(
            status_code=404,
            content={
                "code": "form_unavailable",
                "message": "Biểu mẫu này chưa có file chính thức đã duyệt",
                "needs_official_file": True,
                "procedure_id": proc_id,
                "form_index": form_index,
            },
        )

    form = forms[form_index]
    real_filepath, file_ok, reason = _resolve_form_file_candidate(proc_id, form_index, form)
    if real_filepath and not file_ok:
        return _invalid_form_file_response(
            procedure_id=proc_id,
            form_index=form_index,
            form_name=form.get("name"),
            reason=reason,
        )
    if not real_filepath:
        return JSONResponse(
            status_code=404,
            content={
                "code": "form_unavailable",
                "message": "Biểu mẫu chưa có file hợp lệ, cần admin cập nhật",
                "needs_official_file": True,
                "procedure_id": proc_id,
                "form_index": form_index,
                "form_name": form.get("name"),
            },
        )

    ext = os.path.splitext(real_filepath)[1].lstrip(".").lower() or str(form.get("file_type") or "docx").lstrip(".")
    media_type = {
        "txt": "text/plain; charset=utf-8",
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext, "application/octet-stream")
    filename = _safe_download_filename(str(form.get("name") or proc.get("name") or proc_id), ext)
    ok, reason = _validate_form_file_integrity(real_filepath)
    if not ok:
        return _invalid_form_file_response(
            procedure_id=proc_id,
            form_index=form_index,
            form_name=form.get("name"),
            reason=reason,
        )
    return FileResponse(path=real_filepath, media_type=media_type, filename=filename)


@router.post("/import-default", status_code=201)
async def import_default_procedures():
    """Nạp toàn bộ danh mục thủ tục hành chính & biểu mẫu của Thành phố Hải Phòng vào database."""
    try:
        count = 0
        for proc in HAI_PHONG_PROCEDURES_SEED:
            existing = await repo_query("SELECT id FROM ward_procedure WHERE id = $id OR id = $rec_id;", {"id": proc["id"], "rec_id": f"ward_procedure:{proc['id']}"})
            if existing:
                await repo_update(
                    "ward_procedure",
                    f"ward_procedure:{proc['id']}",
                    {
                        "name": proc["name"],
                        "department": proc["department"],
                        "domain_slug": proc["domain_slug"],
                        "steps": proc["steps"],
                        "documents_required": proc["documents_required"],
                        "duration": proc["duration"],
                        "fee": proc["fee"],
                        "forms": proc["forms"],
                        "updated": datetime.now()
                    }
                )
            else:
                await repo_create(
                    "ward_procedure",
                    {
                        "id": proc["id"],
                        "name": proc["name"],
                        "department": proc["department"],
                        "domain_slug": proc["domain_slug"],
                        "steps": proc["steps"],
                        "documents_required": proc["documents_required"],
                        "duration": proc["duration"],
                        "fee": proc["fee"],
                        "forms": proc["forms"],
                        "created": datetime.now(),
                        "updated": datetime.now()
                    }
                )
            count += 1
        return {"success": True, "message": f"Đã nạp thành công {count} thủ tục hành chính Hải Phòng."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Không thể import dữ liệu: {str(exc)}")

def _load_forms_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resolve_form_source_path(relative_path: str) -> Path:
    """Resolve catalog paths produced on either Windows or Linux."""
    normalized_relative_path = str(relative_path).replace("\\", "/")
    return (PROJECT_ROOT / normalized_relative_path).resolve()


def _normalized_search_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("Đ", "D").replace("đ", "d").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _get_approved_form_ids() -> set[str]:
    """IDs that are safe for citizen/officer official form surfaces.

    Sources:
    - forms_manifest.json review_status=approved
    - haiphong_official_form_index.json review_status=approved
    - classified candidates review_status=approved
    Pending/rejected never included.
    """
    approved_ids: set[str] = set()
    manifest = _load_forms_json(FORMS_MANIFEST_PATH, {})
    for _domain, forms in (manifest.get("forms") or {}).items():
        for form in forms or []:
            if form.get("review_status") == "approved":
                approved_ids.add(str(form.get("id")))

    index_payload = _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": []})
    for form in index_payload.get("forms") or []:
        if form.get("review_status") == "approved" or form.get("is_approved") is True:
            approved_ids.add(str(form.get("id")))

    classified = _load_forms_json(
        CLASSIFIED_FORMS_CANDIDATES_PATH,
        {"records": []},
    )
    for form in classified.get("records") or []:
        if form.get("review_status") == "approved" or form.get("is_approved") is True:
            approved_ids.add(str(form.get("id")))
    return approved_ids


def search_official_forms(
    query: str,
    domain: Optional[str] = None,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """Return canonical official source packages matching a user question."""
    # The approved priority supplement contains curated page slices and takes
    # precedence over broad catalog/index metadata for the same ID. Load it
    # last so a generic slug or stale remote path cannot replace its validated
    # local PDF/title.
    source_payloads = [
        _load_forms_json(OFFICIAL_FORMS_CATALOG_PATH, {"forms": []}),
        _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": []}),
        _load_forms_json(PRIORITY_FORMS_SUPPLEMENT_PATH, {"forms": []}),
    ]
    by_id: Dict[str, Dict[str, Any]] = {}
    for source_payload in source_payloads:
        for form in source_payload.get("forms", []) or []:
            form_id = str(form.get("id") or "")
            if form_id:
                by_id[form_id] = {**by_id.get(form_id, {}), **form}
    # Form titles are shown to citizens and are also used for deterministic
    # matching. Normalize raw slugs before scoring, never exposing filenames.
    payload = {"forms": normalize_form_records(list(by_id.values()))}
    
    approved_ids = _get_approved_form_ids()
    filtered_forms = []
    for form in payload.get("forms", []):
        if str(form.get("id")) in approved_ids:
            filtered_forms.append(form)
    payload["forms"] = filtered_forms
    ignored = {
        "bieu",
        "mau",
        "thu",
        "tuc",
        "ho",
        "so",
        "cho",
        "toi",
        "can",
        "xin",
        "tai",
        "va",
        "nhung",
        "thi",
        "o",
        "tai",
        "cua",
    }
    query_normalized = _normalized_search_text(query)
    query_tokens = set(query_normalized.split())
    normalized_domain_filter = normalize_form_domain(domain) or domain
    terms = [
        term
        for term in query_normalized.split()
        if len(term) >= 3 and term not in ignored
    ]
    if not terms:
        return []
    query_phrase = " ".join(terms)

    scored: List[tuple[int, Dict[str, Any]]] = []
    for record in payload.get("forms", []):
        if record.get("catalog_status") != "available_official_source":
            continue
        if not record.get("is_canonical"):
            continue
        record_domain = normalize_form_domain(record.get("domain")) or record.get("domain")
        if normalized_domain_filter and record_domain != normalized_domain_filter:
            continue
        title = _normalized_search_text(str(record.get("form_title") or ""))
        package = _normalized_search_text(
            str(record.get("source_package_title") or "")
        )
        title_tokens = set(title.split())
        package_tokens = set(package.split())
        
        # Check if form matches query based on important keywords
        title_important_terms = [t for t in title.split() if len(t) >= 3 and t not in ignored]
        package_important_terms = [t for t in package.split() if len(t) >= 3 and t not in ignored]
        
        is_match = False
        if title_important_terms and all(t in query_tokens for t in title_important_terms):
            is_match = True
        elif package_important_terms and all(t in query_tokens for t in package_important_terms):
            is_match = True
        else:
            # Fallback to token hit counts
            title_hits = sum(1 for term in terms if term in title_tokens)
            if title_hits >= 2 or (len(title_tokens) <= 2 and title_hits >= 1):
                is_match = True
                
        if not is_match:
            continue
            
        title_hits = sum(1 for term in terms if term in title_tokens)
        package_hits = sum(1 for term in terms if term in package_tokens)
        phrase_in_title = title in query_normalized or query_phrase in title
        phrase_in_package = package in query_normalized or query_phrase in package
        
        score = (
            title_hits * 10
            + package_hits * 3
            + (150 if phrase_in_title else 0)
            + (50 if phrase_in_package else 0)
        )
        scored.append((score, record))
        
    scored.sort(
        key=lambda item: (
            item[0],
            int(item[1].get("source_page") or 0),
        ),
        reverse=True,
    )
    return [record for _, record in scored[:limit]]


def build_official_form_context(
    query: str,
    domain: Optional[str] = None,
    limit: int = 4,
) -> str:
    matches = search_official_forms(query, domain=domain, limit=limit)
    if not matches:
        return ""
    lines = [
        "### BIỂU MẪU TỪ NGUỒN CHÍNH THỨC HẢI PHÒNG:",
        (
            "Chỉ giới thiệu đúng các mục dưới đây. Tệp tải là gói nguồn chính thức "
            "có chứa biểu mẫu; không được gọi là mẫu độc lập nếu nguồn không tách file."
        ),
    ]
    import os
    backend_url = os.getenv("API_URL") or "http://localhost:5055"
    for record in matches:
        form_id = str(record["id"])
        page = (
            f", trang {record['source_page']}"
            if record.get("source_page")
            else ""
        )
        exact_file = (
            record.get("is_verbatim_page_slice")
            or "priority_official" in str(record.get("source_package_path") or "")
        )
        download_label = "Tải biểu mẫu" if exact_file else "Tải gói nguồn"
        lines.append(
            (
                f"- {record['form_title']} (nguồn: "
                f"{record.get('source_package_title')}{page}) "
                f"-> [{download_label}]({backend_url}/api/procedures/forms-catalog/official/"
                f"{form_id}/download)"
            )
        )
    lines.append(
        "Các nguồn này vẫn phải được cán bộ đối chiếu hiệu lực trước khi dùng để nộp hồ sơ."
    )
    return "\n".join(lines)


@router.get("/forms-catalog/status")
async def get_forms_catalog_status(
    status: Optional[str] = Query(None, description="Lọc theo trạng thái tải"),
    domain: Optional[str] = Query(None, description="Lọc theo lĩnh vực biểu mẫu"),
    limit: int = Query(500, ge=1, le=1000),
):
    manifest = _load_forms_json(FORMS_MANIFEST_PATH, {})
    download_payload = _load_forms_json(FORMS_STATUS_PATH, {"records": []})
    official_candidates = _load_forms_json(
        OFFICIAL_FORMS_CANDIDATE_PATH,
        {"summary": {}},
    )
    official_index = _load_forms_json(
        OFFICIAL_FORMS_INDEX_PATH,
        {"summary": {}},
    )
    official_catalog = _load_forms_json(
        OFFICIAL_FORMS_CATALOG_PATH,
        {"summary": {}},
    )
    priority_supplement = _load_forms_json(
        PRIORITY_FORMS_SUPPLEMENT_PATH,
        {"summary": {}},
    )
    priority_catalog = _load_forms_json(
        PRIORITY_FORMS_CATALOG_PATH,
        {"summary": {}},
    )
    status_by_id = {
        str(record.get("id")): record
        for record in download_payload.get("records", [])
        if record.get("id")
    }

    records: List[Dict[str, Any]] = []
    for manifest_domain, forms in (manifest.get("forms") or {}).items():
        for form in forms or []:
            form_id = str(form.get("id") or "")
            download_record = status_by_id.get(form_id, {})
            effective_status = download_record.get("status") or "not_checked"
            if (
                effective_status == "detail_request_failed"
                and download_record.get("http_status") == 429
            ):
                effective_status = "rate_limited"
            record = {
                "id": form_id,
                "title": form.get("title"),
                "domain": normalize_form_domain(form.get("domain") or manifest_domain) or (form.get("domain") or manifest_domain),
                "detail_url": form.get("full_url"),
                "has_download": bool(form.get("has_download")),
                "updated_date": form.get("updated_date"),
                "form_type": form.get("form_type"),
                "status": effective_status,
                "http_status": download_record.get("http_status"),
                "download_url": download_record.get("download_url"),
                "local_path": download_record.get("local_path"),
                "file_type": download_record.get("file_type"),
                "size_bytes": download_record.get("size_bytes"),
                "sha256": download_record.get("sha256"),
                "checked_at": download_record.get("checked_at"),
                "official_level": form.get("official_level", "reference"),
                "review_status": form.get("review_status", "candidate_pending_review"),
                "source_url": form.get("source_url") or form.get("full_url"),
                "department": form.get("department"),
                "ward_scope": form.get("ward_scope", True),
                "version": form.get("version", "1.0"),
            }
            if status and effective_status != status:
                continue
            normalized_domain_filter = normalize_form_domain(domain) or domain
            if normalized_domain_filter and record["domain"] != normalized_domain_filter:
                continue
            records.append(record)

    all_statuses = Counter(
        (
            "rate_limited"
            if record.get("status") == "detail_request_failed"
            and record.get("http_status") == 429
            else record.get("status") or "not_checked"
        )
        for record in status_by_id.values()
    )
    manifest_domains: Dict[str, int] = {}
    for key, value in (manifest.get("forms") or {}).items():
        normalized_key = normalize_form_domain(key) or key
        manifest_domains[normalized_key] = manifest_domains.get(normalized_key, 0) + len(value or [])
    quarantined_count = (
        len(list(FORMS_QUARANTINE_DIR.glob("*")))
        if FORMS_QUARANTINE_DIR.exists()
        else 0
    )
    return {
        "summary": {
            "manifest_total": sum(manifest_domains.values()),
            "domain_counts": manifest_domains,
            "status_counts": dict(all_statuses),
            "verified_official_files": all_statuses.get("downloaded_verified", 0),
            "quarantined_synthetic_files": quarantined_count,
            "generated_at": download_payload.get("generated_at"),
            "hai_phong_official": {
                **official_candidates.get("summary", {}),
                **official_index.get("summary", {}),
                **official_catalog.get("summary", {}),
                "generated_at": official_catalog.get("generated_at"),
                "priority_supplement": priority_supplement.get("summary", {}),
                "priority_200": priority_catalog.get("summary", {}),
            },
        },
        "records": records[:limit],
    }


@router.get("/forms-catalog/official")
async def list_official_forms(
    query: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    """Public official forms: approved only. Pending candidates never returned."""
    payload = _load_forms_json(
        OFFICIAL_FORMS_CATALOG_PATH,
        {"summary": {}, "forms": []},
    )
    index_payload = _load_forms_json(
        OFFICIAL_FORMS_INDEX_PATH,
        {"summary": {}, "forms": []},
    )
    approved_ids = _get_approved_form_ids()
    normalized_domain_filter = normalize_form_domain(domain) or domain
    if query:
        records = search_official_forms(query, domain=normalized_domain_filter, limit=limit)
    else:
        by_id: Dict[str, Dict[str, Any]] = {}
        for record in (payload.get("forms") or []) + (index_payload.get("forms") or []):
            rid = str(record.get("id") or "")
            if not rid or rid not in approved_ids:
                continue
            record_domain = normalize_form_domain(record.get("domain")) or record.get("domain")
            if normalized_domain_filter and record_domain != normalized_domain_filter:
                continue
            if status and record.get("catalog_status") and record.get("catalog_status") != status:
                continue
            # prefer richer/later record
            by_id[rid] = {**by_id.get(rid, {}), **record}
        records = list(by_id.values())
    # harden: strip any non-approved just in case
    records = [r for r in records if str(r.get("id")) in approved_ids and r.get("review_status", "approved") == "approved"]
    records = normalize_form_records(records)
    return {
        "summary": {
            **(payload.get("summary") or {}),
            "approved_returned": len(records[:limit]),
        },
        "generated_at": payload.get("generated_at") or index_payload.get("generated_at"),
        "records": records[:limit],
    }


@router.get("/forms-catalog/priority")
async def list_priority_forms(
    query: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
):
    payload = _load_forms_json(
        PRIORITY_FORMS_CATALOG_PATH,
        {"summary": {}, "forms": []},
    )
    approved_ids = _get_approved_form_ids()
    normalized_domain_filter = normalize_form_domain(domain) or domain
    if query:
        records = search_official_forms(query, domain=normalized_domain_filter, limit=limit)
    else:
        records = [
            record
            for record in payload.get("forms", [])
            if str(record.get("id")) in approved_ids
            and (not normalized_domain_filter or (normalize_form_domain(record.get("domain")) or record.get("domain")) == normalized_domain_filter)
            and (not tier or record.get("priority_tier") == tier)
        ]
    records = normalize_form_records(records)
    return {
        "summary": payload.get("summary", {}),
        "generated_at": payload.get("generated_at"),
        "records": records[:limit],
    }


@router.get("/forms-catalog/official/{form_id}/download")
async def download_official_form_source(form_id: str):
    """Serve only approved official form files.

    Pending/rejected candidates must never be downloadable here.
    """
    started = perf_counter()
    approved_ids = _get_approved_form_ids()
    if str(form_id) not in approved_ids:
        telemetry.record_issue("broken_form_url", category="form_download", status_code=404, error_class="not_approved")
        raise HTTPException(
            status_code=404,
            detail="Biểu mẫu chưa được duyệt hoặc không tồn tại.",
        )

    curated_priority_payload = _load_forms_json(PRIORITY_FORMS_SUPPLEMENT_PATH, {"forms": []})
    priority_payload = _load_forms_json(PRIORITY_FORMS_CATALOG_PATH, {"forms": []})
    broad_payload = _load_forms_json(OFFICIAL_FORMS_CATALOG_PATH, {"forms": []})
    index_payload = _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": []})
    classified = _load_forms_json(CLASSIFIED_FORMS_CANDIDATES_PATH, {"records": []})

    # First matching ID wins below. Curated priority page slices are verified
    # local files, so they must beat package-level catalog entries.
    pool: list[dict] = []
    pool.extend(curated_priority_payload.get("forms") or [])
    pool.extend(priority_payload.get("forms") or [])
    pool.extend(broad_payload.get("forms") or [])
    pool.extend(index_payload.get("forms") or [])
    # Map classified approved records into downloadable shape
    for rec in classified.get("records") or []:
        if str(rec.get("id")) != str(form_id):
            continue
        if rec.get("review_status") != "approved" and rec.get("is_approved") is not True:
            continue
        pool.append(
            {
                "id": rec.get("id"),
                "review_status": "approved",
                "catalog_status": "available_official_source",
                "source_package_path": rec.get("priority_path")
                or rec.get("local_path")
                or rec.get("file_path"),
                "local_path": rec.get("priority_path") or rec.get("local_path") or rec.get("file_path"),
            }
        )

    record = None
    for item in pool:
        if str(item.get("id")) == str(form_id):
            path_hint = str(item.get("source_package_path") or item.get("local_path") or "")
            normalized_hint = path_hint.replace("\\", "/")
            # Exact curated page slices are preferred over package source PDFs
            # and broad catalog aliases for this same form ID.
            if item.get("is_verbatim_page_slice") and "priority_official" in normalized_hint:
                record = item
                break
            if record is None:
                record = item
    if not record:
        telemetry.record_issue("broken_form_url", category="form_download", status_code=404, error_class="catalog_missing")
        raise HTTPException(status_code=404, detail="Khong tim thay bieu mau.")

    # Approved-only gate (catalog_status may be missing on older records)
    if record.get("review_status") not in (None, "approved") and record.get("is_approved") is not True:
        raise HTTPException(
            status_code=409,
            detail="Nguon bieu mau nay chua dat dieu kien cung cap cho nguoi dung.",
        )
    if record.get("catalog_status") not in (None, "available_official_source", "approved"):
        # still allow if explicitly approved
        if record.get("review_status") != "approved" and record.get("is_approved") is not True:
            raise HTTPException(
                status_code=409,
                detail="Nguon bieu mau nay chua dat dieu kien cung cap cho nguoi dung.",
            )

    relative_path = (
        record.get("source_package_path")
        or record.get("local_path")
        or record.get("priority_path")
        or record.get("file_path")
    )
    if not relative_path:
        telemetry.record_issue("broken_form_url", category="form_download", status_code=404, error_class="source_missing")
        raise HTTPException(status_code=404, detail="Khong tim thay tep nguon.")
    source_path = _resolve_form_source_path(str(relative_path))
    allowed_dirs = {
        OFFICIAL_FORMS_FILES_DIR.resolve(),
        PRIORITY_FORMS_FILES_DIR.resolve(),
    }
    if not any(
        allowed_dir == source_path.parent or allowed_dir in source_path.parents
        for allowed_dir in allowed_dirs
    ):
        raise HTTPException(status_code=400, detail="Duong dan tep khong hop le.")
    if not source_path.is_file():
        telemetry.record_issue("broken_form_url", category="form_download", status_code=404, error_class="file_missing")
        raise HTTPException(status_code=404, detail="Tep nguon khong con ton tai.")

    ok, reason = _validate_form_file_integrity(source_path)
    if not ok:
        return _invalid_form_file_response(
            form_id=str(form_id),
            form_name=normalize_form_display_name(record, fallback=str(form_id)),
            reason=reason,
        )

    media_types = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    telemetry.record_operation(
        category="form_download",
        route="/api/procedures/forms-catalog/official/{form_id}/download",
        duration_ms=(perf_counter() - started) * 1000,
        metadata={"origin": "official-file"},
    )
    return FileResponse(
        path=source_path,
        media_type=media_types.get(source_path.suffix.lower(), "application/octet-stream"),
        filename=_safe_download_filename(normalize_form_display_name(record, fallback=source_path.stem), source_path.suffix.lstrip(".") or "pdf"),
    )


@router.get("/forms-catalog/official-candidates")
async def get_official_form_candidates(
    domain: Optional[str] = Query(None),
    review_status: Optional[str] = Query("candidate_pending_review"),
    title_quality: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    payload = _load_forms_json(
        OFFICIAL_FORMS_INDEX_PATH,
        {"summary": {}, "forms": []},
    )
    records: List[Dict[str, Any]] = []
    for record in payload.get("forms", []):
        if domain and record.get("domain") != domain:
            continue
        if review_status and record.get("review_status") != review_status:
            continue
        if title_quality and record.get("title_quality") != title_quality:
            continue
        records.append(normalize_form_record(record))
    return {
        "summary": payload.get("summary", {}),
        "generated_at": payload.get("generated_at"),
        "records": records[:limit],
    }


@router.get("/{procedure_id}", response_model=Procedure)
async def get_procedure_details(procedure_id: str):
    try:
        rows = await repo_query("SELECT * FROM ward_procedure;", {})
        proc_data = None
        for row in rows:
            proc_id = str(row.get("id") or "")
            forms_list = row.get("forms") or []
            if forms_list and len(forms_list) > 0:
                url = forms_list[0].get("download_url") or ""
                match = re.search(r"/api/procedures/([^/]+)/forms", url)
                if match:
                    proc_id = match.group(1)
            if ":" in proc_id:
                proc_id = proc_id.split(":", 1)[1]
            if proc_id == procedure_id:
                proc_data = row
                break
                
        if not proc_data:
            # Check in memory seed list
            seed_match = next((p for p in HAI_PHONG_PROCEDURES_SEED if p["id"] == procedure_id), None)
            if not seed_match:
                raise HTTPException(status_code=404, detail="Không tìm thấy quy trình thủ tục này.")
            proc_data = seed_match
            
        # Trích xuất ID gốc
        proc_id = str(proc_data.get("id") or "")
        forms_list = proc_data.get("forms") or []
        if forms_list and len(forms_list) > 0:
            url = forms_list[0].get("download_url") or ""
            match = re.search(r"/api/procedures/([^/]+)/forms", url)
            if match:
                proc_id = match.group(1)
        if ":" in proc_id:
            proc_id = proc_id.split(":", 1)[1]
            
        return Procedure(
            id=proc_id,
            name=proc_data.get("name"),
            department=proc_data.get("department"),
            domain_slug=proc_data.get("domain_slug"),
            steps=proc_data.get("steps") or [],
            documents_required=proc_data.get("documents_required") or [],
            duration=proc_data.get("duration"),
            fee=proc_data.get("fee"),
            forms=[FormTemplate(**normalize_form_record(f)) for f in (proc_data.get("forms") or [])]
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/{procedure_id}/forms/{form_index}")
async def download_form_template(procedure_id: str, form_index: int):
    """Prefer real uploaded/official form files only.

    Temporary seed content is never returned as an official downloadable form.
    """
    import os
    from fastapi.responses import FileResponse, JSONResponse

    # Resolve procedure metadata from DB, then seed (metadata only).
    proc_data = None
    try:
        rows = await repo_query("SELECT * FROM ward_procedure;", {})
        for row in rows:
            proc_id = str(row.get("id") or "")
            forms_list = row.get("forms") or []
            if forms_list and len(forms_list) > 0:
                url = forms_list[0].get("download_url") or ""
                match = re.search(r"/api/procedures/([^/]+)/forms", url)
                if match:
                    proc_id = match.group(1)
            if ":" in proc_id:
                proc_id = proc_id.split(":", 1)[1]
            if proc_id == procedure_id:
                proc_data = row
                break
    except Exception:
        proc_data = None

    if not proc_data:
        seed_match = next((p for p in HAI_PHONG_PROCEDURES_SEED if p.get("id") == procedure_id), None)
        if seed_match:
            proc_data = seed_match

    forms = (proc_data or {}).get("forms") or []
    form = forms[form_index] if 0 <= form_index < len(forms) else {}

    real_filepath = _resolve_real_form_file(procedure_id, form_index, form if isinstance(form, dict) else None)
    if not real_filepath:
        return JSONResponse(
            status_code=409,
            content={
                "code": "form_unavailable",
                "message": "Biểu mẫu này chưa có file chính thức đã duyệt",
                "needs_official_file": True,
                "procedure_id": procedure_id,
                "form_index": form_index,
                "form_name": form.get("name") if isinstance(form, dict) else None,
            },
        )

    ext = os.path.splitext(real_filepath)[1].lstrip(".").lower() or str((form or {}).get("file_type") or "docx").lstrip(".")
    media_type = {
        "txt": "text/plain; charset=utf-8",
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext, "application/octet-stream")
    filename = _safe_download_filename(
        normalize_form_display_name(form if isinstance(form, dict) else None, fallback=str((proc_data or {}).get("name") or procedure_id)),
        ext,
    )
    ok, reason = _validate_form_file_integrity(real_filepath)
    if not ok:
        return _invalid_form_file_response(
            procedure_id=procedure_id,
            form_index=form_index,
            form_name=normalize_form_display_name(form if isinstance(form, dict) else None),
            reason=reason,
        )
    return FileResponse(path=real_filepath, media_type=media_type, filename=filename)


class FormReviewRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    review_note: str = Field(default="", max_length=2000)
    # Optional metadata overrides before approve
    form_name: Optional[str] = Field(default=None, max_length=500)
    procedure_id: Optional[str] = Field(default=None, max_length=200)
    domain: Optional[str] = Field(default=None, max_length=100)
    reason: Optional[str] = Field(default=None, max_length=2000)



def _load_classified_candidates() -> Dict[str, Any]:
    payload = _load_forms_json(
        CLASSIFIED_FORMS_CANDIDATES_PATH,
        {"summary": {}, "records": [], "top10": [], "notes": []},
    )
    if not isinstance(payload, dict):
        return {"summary": {}, "records": [], "top10": [], "notes": []}
    payload.setdefault("summary", {})
    payload.setdefault("records", [])
    return payload


def _save_classified_candidates(payload: Dict[str, Any]) -> None:
    CLASSIFIED_FORMS_CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLASSIFIED_FORMS_CANDIDATES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_classified_candidate(form_id: str) -> tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[int]]:
    payload = _load_classified_candidates()
    for idx, rec in enumerate(payload.get("records") or []):
        if str(rec.get("id")) == str(form_id):
            return rec, payload, idx
    return None, payload, None


def _candidate_source_path(record: Dict[str, Any]) -> Optional[Path]:
    rel = str(record.get("file_path") or "").replace("\\", "/").lstrip("./")
    if not rel:
        return None
    path = (PROJECT_ROOT / rel).resolve()
    try:
        path.relative_to(OFFICIAL_FORMS_FILES_DIR.resolve())
    except Exception:
        # allow absolute-ish under project root only
        try:
            path.relative_to(PROJECT_ROOT.resolve())
        except Exception:
            return None
    return path if path.is_file() else None


def _copy_candidate_to_priority(record: Dict[str, Any]) -> Dict[str, str]:
    import shutil
    from datetime import datetime, timezone

    source = _candidate_source_path(record)
    if not source:
        raise HTTPException(
            status_code=404,
            detail="Khong tim thay file candidate de duyet. Can nạp file truoc khi approve.",
        )
    PRIORITY_FORMS_FILES_DIR.mkdir(parents=True, exist_ok=True)
    # keep original extension, prefix with form id for stable lookup
    dest_name = f"{record.get('id')}-{source.name}"
    dest = PRIORITY_FORMS_FILES_DIR / dest_name
    if not dest.exists():
        shutil.copy2(source, dest)
    rel = str(dest.relative_to(PROJECT_ROOT)).replace("\\", "/")
    return {
        "source_package_path": rel,
        "local_path": rel,
        "file_name": dest.name,
        "copied_at": datetime.now(timezone.utc).isoformat(),
    }


def _upsert_official_index_from_candidate(
    record: Dict[str, Any],
    *,
    decision: str,
    review_note: str,
    form_name: Optional[str],
    procedure_id: Optional[str],
    domain: Optional[str],
    package_paths: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    from datetime import datetime, timezone

    index_payload = _load_forms_json(
        OFFICIAL_FORMS_INDEX_PATH,
        {"generated_at": None, "summary": {}, "forms": [], "errors": []},
    )
    forms = list(index_payload.get("forms") or [])
    form_id = str(record.get("id"))
    existing = next((f for f in forms if str(f.get("id")) == form_id), None)
    title = normalize_form_display_name({**record, "form_title": form_name or record.get("form_title")}, fallback=form_id).strip()
    domain_val = (domain or record.get("suggested_domain") or "unknown")
    proc_id = procedure_id if procedure_id is not None else record.get("suggested_procedure_id")
    package_paths = package_paths or {}
    source_package_path = package_paths.get("source_package_path") or (
        str(record.get("file_path") or "").replace("\\", "/")
    )
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "id": form_id,
        "form_title": title,
        "source_page": None,
        "source_paragraph": None,
        "domain": domain_val,
        "procedure_id": proc_id,
        "source_package_title": title,
        "source_package_path": source_package_path,
        "source_page_url": record.get("page_url") or record.get("source_url"),
        "source_download_url": record.get("source_url"),
        "source_sha256": record.get("sha256"),
        "publisher": record.get("source_name") or "Hai Phong official candidate",
        "locality": "Hai Phong",
        "administrative_level": "ward_candidate",
        "review_status": "approved" if decision == "approved" else "rejected",
        "review_note": review_note or record.get("reason") or "",
        "legal_status": "admin_reviewed" if decision == "approved" else "rejected",
        "effectivity_flags": [],
        "title_quality": "usable",
        "duplicate_group": form_id[:16],
        "local_path": package_paths.get("local_path") or source_package_path,
        "approved_at": now if decision == "approved" else None,
        "rejected_at": now if decision == "rejected" else None,
        "is_approved": decision == "approved",
        "catalog_status": "available_official_source" if decision == "approved" else "rejected",
        "is_canonical": decision == "approved",
        "file_name": package_paths.get("file_name") or record.get("file_name"),
        "confidence": record.get("confidence"),
        "source_id": record.get("source_id"),
    }
    if existing:
        existing.update(entry)
        updated = existing
    else:
        forms.append(entry)
        updated = entry
    index_payload["forms"] = forms
    index_payload["generated_at"] = now
    # recompute summary counts
    status_counts: Dict[str, int] = {}
    domain_counts: Dict[str, int] = {}
    for f in forms:
        rs = str(f.get("review_status") or "unknown")
        status_counts[rs] = status_counts.get(rs, 0) + 1
        dom = str(f.get("domain") or "unknown")
        domain_counts[dom] = domain_counts.get(dom, 0) + 1
    index_payload["summary"] = {
        **(index_payload.get("summary") or {}),
        "form_source_references": len(forms),
        "review_status_counts": status_counts,
        "domain_counts": domain_counts,
        "approved_count": status_counts.get("approved", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "pending_count": status_counts.get("candidate_pending_review", 0),
    }
    OFFICIAL_FORMS_INDEX_PATH.write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Keep catalog in sync for official listing/download
    catalog_payload = _load_forms_json(
        OFFICIAL_FORMS_CATALOG_PATH,
        {"generated_at": None, "summary": {}, "forms": []},
    )
    cforms = list(catalog_payload.get("forms") or [])
    c_existing = next((f for f in cforms if str(f.get("id")) == form_id), None)
    catalog_entry = {
        **entry,
        "catalog_status": "available_official_source" if decision == "approved" else "rejected",
        "is_canonical": decision == "approved",
    }
    if c_existing:
        c_existing.update(catalog_entry)
    else:
        cforms.append(catalog_entry)
    catalog_payload["forms"] = cforms
    catalog_payload["generated_at"] = now
    catalog_payload["summary"] = {
        **(catalog_payload.get("summary") or {}),
        "total": len(cforms),
        "approved_count": sum(1 for f in cforms if f.get("review_status") == "approved"),
    }
    OFFICIAL_FORMS_CATALOG_PATH.write_text(
        json.dumps(catalog_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return updated


def _update_manifest_review(
    form_id: str,
    *,
    decision: str,
    review_note: str,
    form_name: Optional[str] = None,
    procedure_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> bool:
    manifest = _load_forms_json(FORMS_MANIFEST_PATH, {})
    found = False
    if "forms" in manifest:
        for domain_key, forms in (manifest.get("forms") or {}).items():
            for form in forms or []:
                if str(form.get("id")) == str(form_id):
                    form["review_status"] = "approved" if decision == "approved" else "rejected"
                    form["review_note"] = review_note
                    if form_name:
                        form["title"] = form_name
                    if procedure_id is not None:
                        form["procedure_id"] = procedure_id
                    if domain:
                        form["domain"] = domain
                    found = True
                    break
            if found:
                break
    if found:
        FORMS_MANIFEST_PATH.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return found


def _is_domain_allowed(domain_slug: str | None, allowed_domains: list[str]) -> bool:
    if not domain_slug:
        return True
    slug_parts = set(domain_slug.split("_"))
    for allowed in allowed_domains:
        allowed_parts = set(allowed.split("_"))
        if slug_parts.intersection(allowed_parts):
            return True
        if allowed == domain_slug:
            return True
    return False


@router.get("/forms-catalog/candidates-full")
async def list_classified_form_candidates(
    request: Request,
    domain: Optional[str] = Query(None),
    review_status: Optional[str] = Query("candidate_pending_review"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Candidate queue for admin/officer from crawl+classify pipeline."""
    role = get_request_role(request)
    user_id = get_request_user_id(request)
    if role == "citizen":
        raise HTTPException(status_code=403, detail="Chỉ admin hoặc cán bộ chuyên trách mới được xem danh sách này.")

    allowed_domains = []
    if role == "officer" and user_id:
        from api.user_service import get_user_profile
        profile = await get_user_profile(user_id)
        if profile:
            allowed_domains = profile.get("allowed_domains") or []

    payload = _load_classified_candidates()
    records: List[Dict[str, Any]] = []
    for rec in payload.get("records") or []:
        if review_status and rec.get("review_status") != review_status:
            continue
        rec_domain = rec.get("suggested_domain") or rec.get("domain")
        if role == "officer" and rec_domain:
            if not _is_domain_allowed(rec_domain, allowed_domains):
                continue
        if domain and rec_domain != domain:
            continue
        # Never expose pending candidates as downloadable official forms here.
        item = dict(rec)
        item["download_url"] = None
        item["is_public"] = False
        records.append(item)
    sliced = records[offset : offset + limit]
    return {
        "summary": {
            **(payload.get("summary") or {}),
            "filtered_total": len(records),
            "returned": len(sliced),
            "offset": offset,
            "limit": limit,
        },
        "generated_at": payload.get("generated_at"),
        "records": sliced,
    }




@router.post("/forms-catalog/upload")
async def upload_official_form(
    request: Request,
    file: UploadFile = File(...),
    form_title: str = Query(..., min_length=3, max_length=300),
    domain: str = Query("unknown"),
    procedure_id: Optional[str] = Query(None),
    review_status: str = Query("approved", pattern="^(approved|candidate_pending_review|rejected)$"),
    official_level: str = Query("official", pattern="^(official|reference)$"),
):
    """Admin-only upload for real form files.

    - approved + official => copy into priority_official and become downloadable
    - otherwise keep in official_candidates pending review
    """
    role = get_request_role(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin mới được upload biểu mẫu")

    filename = file.filename or "form.bin"
    ext = Path(filename).suffix.lower()
    if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf", ".zip"}:
        raise HTTPException(status_code=400, detail="Định dạng file không được hỗ trợ")

    content = await file.read()
    if not content or len(content) < 1024:
        raise HTTPException(status_code=400, detail="File quá nhỏ hoặc rỗng; cần file biểu mẫu thật")

    import hashlib
    from datetime import datetime, timezone
    import re as _re

    digest = hashlib.sha256(content).hexdigest()
    form_id = digest[:24]
    safe = _re.sub(r"[^\w\-.]+", "-", filename).strip("-._") or "form"
    target_dir = PRIORITY_FORMS_FILES_DIR if (review_status == "approved" and official_level == "official") else OFFICIAL_FORMS_FILES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{form_id}-{safe}"
    dest = target_dir / dest_name
    dest.write_bytes(content)
    rel = str(dest.relative_to(PROJECT_ROOT)).replace("\\", "/")
    now = datetime.now(timezone.utc).isoformat()

    record = {
        "id": form_id,
        "form_title": form_title,
        "domain": domain,
        "procedure_id": procedure_id,
        "file_name": dest_name,
        "file_path": rel,
        "local_path": rel,
        "source_package_path": rel,
        "size_bytes": len(content),
        "sha256": digest,
        "review_status": review_status,
        "official_level": official_level,
        "is_approved": review_status == "approved",
        "catalog_status": "available_official_source" if review_status == "approved" else "candidate_pending_review",
        "is_canonical": review_status == "approved" and official_level == "official",
        "publisher": "admin_upload",
        "locality": "Hai Phong",
        "administrative_level": "commune_relevant",
        "retrieved_at": now,
        "uploaded_at": now,
        "uploaded_by_role": role,
    }

    # Upsert classified candidates
    classified = _load_forms_json(CLASSIFIED_FORMS_CANDIDATES_PATH, {"records": [], "summary": {}})
    records = list(classified.get("records") or [])
    replaced = False
    for i, rec in enumerate(records):
        if str(rec.get("id")) == form_id or str(rec.get("sha256")) == digest:
            records[i] = {**rec, **record, "detected_form_name": form_title, "suggested_domain": domain, "suggested_procedure_id": procedure_id}
            replaced = True
            break
    if not replaced:
        records.append({**record, "detected_form_name": form_title, "suggested_domain": domain, "suggested_procedure_id": procedure_id})
    classified["records"] = records
    status_counts = {}
    for rec in records:
        rs = str(rec.get("review_status") or "unknown")
        status_counts[rs] = status_counts.get(rs, 0) + 1
    classified["summary"] = {**(classified.get("summary") or {}), "review_status_counts": status_counts, "total": len(records)}
    _save_classified_candidates(classified)

    # If approved official, upsert priority + index
    if review_status == "approved" and official_level == "official":
        priority_payload = _load_forms_json(PRIORITY_FORMS_SUPPLEMENT_PATH, {"forms": [], "summary": {}})
        pforms = list(priority_payload.get("forms") or [])
        if not any(str(f.get("id")) == form_id for f in pforms):
            pforms.append(record)
        else:
            pforms = [record if str(f.get("id")) == form_id else f for f in pforms]
        priority_payload["forms"] = pforms
        priority_payload["generated_at"] = now
        priority_payload["summary"] = {
            **(priority_payload.get("summary") or {}),
            "total_forms": len(pforms),
            "approved_forms": sum(1 for f in pforms if f.get("review_status") == "approved"),
        }
        PRIORITY_FORMS_SUPPLEMENT_PATH.write_text(
            json.dumps(priority_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        index_payload = _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": [], "summary": {}})
        iforms = list(index_payload.get("forms") or [])
        if not any(str(f.get("id")) == form_id for f in iforms):
            iforms.append(record)
        else:
            iforms = [record if str(f.get("id")) == form_id else f for f in iforms]
        index_payload["forms"] = iforms
        index_payload["generated_at"] = now
        OFFICIAL_FORMS_INDEX_PATH.write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "id": form_id,
        "form_title": form_title,
        "domain": domain,
        "review_status": review_status,
        "official_level": official_level,
        "downloadable": review_status == "approved" and official_level == "official",
        "local_path": rel,
        "size_bytes": len(content),
        "message": "Upload thành công" if review_status == "approved" else "Upload thành công, chờ duyệt",
    }


@router.post("/forms-catalog/candidates-full/{form_id}/review")
async def review_classified_form_candidate(
    form_id: str,
    request: FormReviewRequest,
    req_obj: Request,
):
    """Approve/reject a classified candidate form.

    Approve:
    - set review_status=approved
    - copy file to data/uploads/forms/priority_official
    - update haiphong_official_form_index.json (+ catalog)
    Reject:
    - set review_status=rejected
    - keep reason/review_note
    """
    record, payload, idx = _find_classified_candidate(form_id)
    if not record or idx is None:
        raise HTTPException(status_code=404, detail=f"Khong tim thay candidate form id={form_id}")

    role = get_request_role(req_obj)
    user_id = get_request_user_id(req_obj)
    if role == "citizen":
        raise HTTPException(status_code=403, detail="Chỉ admin hoặc cán bộ chuyên trách mới được duyệt biểu mẫu.")

    if role == "officer" and user_id:
        from api.user_service import get_user_profile
        profile = await get_user_profile(user_id)
        if profile:
            allowed_domains = profile.get("allowed_domains") or []
            rec_domain = record.get("suggested_domain") or record.get("domain")
            if rec_domain and not _is_domain_allowed(rec_domain, allowed_domains):
                raise HTTPException(
                    status_code=403,
                    detail="Bạn không có quyền duyệt biểu mẫu thuộc lĩnh vực này."
                )

    decision = request.decision
    review_note = (request.review_note or request.reason or record.get("reason") or "").strip()
    form_name = (request.form_name or record.get("detected_form_name") or record.get("file_name") or form_id).strip()
    procedure_id = request.procedure_id if request.procedure_id is not None else record.get("suggested_procedure_id")
    domain = request.domain if request.domain is not None else record.get("suggested_domain")

    package_paths: Dict[str, str] = {}
    if decision == "approved":
        package_paths = _copy_candidate_to_priority(record)

    # update classified record
    record = dict(record)
    record["detected_form_name"] = form_name
    record["suggested_procedure_id"] = procedure_id
    record["suggested_domain"] = domain
    record["review_status"] = "approved" if decision == "approved" else "rejected"
    record["is_approved"] = decision == "approved"
    record["review_note"] = review_note
    record["reason"] = review_note or record.get("reason")
    if package_paths:
        record["priority_path"] = package_paths.get("source_package_path")
        record["local_path"] = package_paths.get("local_path")
    from datetime import datetime, timezone
    record["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    payload["records"][idx] = record
    # refresh summary counts
    status_counts: Dict[str, int] = {}
    for rec in payload.get("records") or []:
        rs = str(rec.get("review_status") or "unknown")
        status_counts[rs] = status_counts.get(rs, 0) + 1
    payload["summary"] = {
        **(payload.get("summary") or {}),
        "review_status_counts": status_counts,
        "auto_approved": 0,
    }
    _save_classified_candidates(payload)

    official = _upsert_official_index_from_candidate(
        record,
        decision=decision,
        review_note=review_note,
        form_name=form_name,
        procedure_id=procedure_id,
        domain=domain,
        package_paths=package_paths,
    )
    _update_manifest_review(
        form_id,
        decision=decision,
        review_note=review_note,
        form_name=form_name,
        procedure_id=procedure_id,
        domain=domain,
    )

    return {
        "status": "success",
        "form_id": form_id,
        "review_status": record["review_status"],
        "is_approved": record["is_approved"],
        "priority_path": package_paths.get("source_package_path"),
        "download_url": (
            f"/api/procedures/forms-catalog/official/{form_id}/download"
            if decision == "approved"
            else None
        ),
        "record": record,
        "official_index_entry": official,
    }


@router.post("/forms-catalog/candidates/{form_id}/review")
async def review_form_candidate(
    form_id: str,
    request: FormReviewRequest,
    req_obj: Request,
):
    """Legacy review endpoint for index/manifest candidates.

    Prefer /forms-catalog/candidates-full/{id}/review for crawl+classify queue.
    If the id exists in classified candidates, reuse that full pipeline.
    """
    record, _payload, idx = _find_classified_candidate(form_id)
    if record is not None and idx is not None:
        return await review_classified_form_candidate(form_id, request, req_obj)

    role = get_request_role(req_obj)
    user_id = get_request_user_id(req_obj)
    if role == "citizen":
        raise HTTPException(status_code=403, detail="Chỉ admin hoặc cán bộ chuyên trách mới được duyệt biểu mẫu.")

    decision = request.decision
    review_note = (request.review_note or request.reason or "").strip()
    form_name = request.form_name
    procedure_id = request.procedure_id
    domain = request.domain

    # Update index first
    index_payload = _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": []})
    
    # Check domain permission for officer
    if role == "officer" and user_id:
        form_rec = None
        for form in index_payload.get("forms") or []:
            if str(form.get("id")) == str(form_id):
                form_rec = form
                break
        from api.user_service import get_user_profile
        profile = await get_user_profile(user_id)
        if profile:
            allowed_domains = profile.get("allowed_domains") or []
            rec_domain = (form_rec or {}).get("domain") or domain
            if rec_domain and not _is_domain_allowed(rec_domain, allowed_domains):
                raise HTTPException(
                    status_code=403,
                    detail="Bạn không có quyền duyệt biểu mẫu thuộc lĩnh vực này."
                )
    found = False
    updated = None
    for form in index_payload.get("forms") or []:
        if str(form.get("id")) == str(form_id):
            form["review_status"] = "approved" if decision == "approved" else "rejected"
            form["review_note"] = review_note
            if form_name:
                form["form_title"] = form_name
            if procedure_id is not None:
                form["procedure_id"] = procedure_id
            if domain:
                form["domain"] = domain
            form["is_approved"] = decision == "approved"
            form["catalog_status"] = (
                "available_official_source" if decision == "approved" else "rejected"
            )
            form["is_canonical"] = decision == "approved"
            # copy package into priority_official when possible
            if decision == "approved":
                rel = str(form.get("source_package_path") or "").replace("\\", "/")
                if rel:
                    src_path = _resolve_form_source_path(rel)
                    if src_path.is_file():
                        import shutil
                        PRIORITY_FORMS_FILES_DIR.mkdir(parents=True, exist_ok=True)
                        dest = PRIORITY_FORMS_FILES_DIR / f"{form_id}-{src_path.name}"
                        if not dest.exists():
                            shutil.copy2(src_path, dest)
                        form["source_package_path"] = str(dest.relative_to(PROJECT_ROOT)).replace("\\", "/")
                        form["local_path"] = form["source_package_path"]
            found = True
            updated = form
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"Khong tim thay bieu mau candidate voi ID {form_id}")

    OFFICIAL_FORMS_INDEX_PATH.write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # sync catalog entry
    catalog_payload = _load_forms_json(OFFICIAL_FORMS_CATALOG_PATH, {"forms": []})
    c_found = False
    for form in catalog_payload.get("forms") or []:
        if str(form.get("id")) == str(form_id):
            form.update(updated or {})
            c_found = True
            break
    if not c_found and updated:
        catalog_payload.setdefault("forms", []).append(updated)
    OFFICIAL_FORMS_CATALOG_PATH.write_text(
        json.dumps(catalog_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _update_manifest_review(
        form_id,
        decision=decision,
        review_note=review_note,
        form_name=form_name,
        procedure_id=procedure_id,
        domain=domain,
    )
    return {
        "status": "success",
        "form_id": form_id,
        "review_status": "approved" if decision == "approved" else "rejected",
        "download_url": (
            f"/api/procedures/forms-catalog/official/{form_id}/download"
            if decision == "approved"
            else None
        ),
        "record": updated,
    }
