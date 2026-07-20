from pathlib import Path

# Create FAQ router
faq_router_code = '''"""
FAQ API Router - Quản lý câu hỏi thường gặp cho phường/xã
Chỉ admin mới có quyền viết (POST/PUT/DELETE), citizen/officer chỉ đọc (GET).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
import json
import os

router = APIRouter(prefix="/api/faq", tags=["FAQ"])

# --- Models ---

class FaqCreate(BaseModel):
    question: str = Field(..., min_length=5, max_length=500)
    answer: str = Field(..., min_length=10)
    steps: List[str] = Field(default_factory=list)
    form_ids: List[str] = Field(default_factory=list)
    domain: str = Field(..., description="Lĩnh vực: ho_tich, dat_dai, trat_tu, hanh_chinh_cong, etc.")
    ward_scope: Optional[str] = Field(None, description="Phường áp dụng (ví dụ: Le Chan)")
    review_status: str = Field(default="draft", regex="^(draft|approved|rejected)$")

class FaqUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    steps: Optional[List[str]] = None
    form_ids: Optional[List[str]] = None
    domain: Optional[str] = None
    ward_scope: Optional[str] = None
    review_status: Optional[str] = None

class FaqResponse(BaseModel):
    id: str
    question: str
    answer: str
    steps: List[str]
    form_ids: List[str]
    domain: str
    ward_scope: Optional[str]
    review_status: str
    created_at: str
    updated_at: str
    approved_by: Optional[str] = None

class FaqListResponse(BaseModel):
    total: int
    items: List[FaqResponse]

# --- Data Store (JSON file-based for simplicity) ---

FAQ_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "notebook_data", "faq_store.json")

def _load_faq_data() -> dict:
    """Load FAQ data from JSON file."""
    if os.path.exists(FAQ_DATA_FILE):
        with open(FAQ_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"faqs": [], "version": 1}

def _save_faq_data(data: dict):
    """Save FAQ data to JSON file."""
    os.makedirs(os.path.dirname(FAQ_DATA_FILE), exist_ok=True)
    with open(FAQ_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _generate_id() -> str:
    """Generate simple ID for FAQ."""
    import uuid
    return str(uuid.uuid4())[:8]

# --- Dependencies ---

def get_current_user_role():
    """Mock dependency - in real app, use JWT/auth middleware."""
    # For now, return a mock role
    # In production, this should be replaced with actual auth
    return {"role": "citizen", "username": "anonymous"}

def require_admin(user=Depends(get_current_user_role)):
    """Require admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ admin mới có quyền thao tác với FAQ"
        )
    return user

# --- Endpoints ---

@router.get("/", response_model=FaqListResponse)
async def list_faqs(
    domain: Optional[str] = None,
    ward_scope: Optional[str] = None,
    review_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """
    Lấy danh sách FAQ đã được phê duyệt.
    - Citizen/Officer: chỉ thấy approved
    - Admin: có thể filter theo status
    """
    data = _load_faq_data()
    faqs = data.get("faqs", [])
    
    # Filter
    if domain:
        faqs = [f for f in faqs if f.get("domain") == domain]
    if ward_scope:
        faqs = [f for f in faqs if f.get("ward_scope") == ward_scope]
    if review_status:
        faqs = [f for f in faqs if f.get("review_status") == review_status]
    else:
        # Default: only show approved for non-admin
        faqs = [f for f in faqs if f.get("review_status") == "approved"]
    
    # Sort by updated_at desc
    faqs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    
    # Pagination
    total = len(faqs)
    faqs = faqs[offset:offset + limit]
    
    return FaqListResponse(total=total, items=[FaqResponse(**f) for f in faqs])

@router.get("/{faq_id}", response_model=FaqResponse)
async def get_faq(faq_id: str):
    """Lấy chi tiết một FAQ."""
    data = _load_faq_data()
    faqs = data.get("faqs", [])
    
    for faq in faqs:
        if faq.get("id") == faq_id:
            # Check if approved
            if faq.get("review_status") != "approved":
                raise HTTPException(status_code=404, detail="FAQ không tồn tại hoặc chưa được phê duyệt")
            return FaqResponse(**faq)
    
    raise HTTPException(status_code=404, detail="FAQ không tồn tại")

@router.post("/", response_model=FaqResponse, status_code=201)
async def create_faq(
    faq_data: FaqCreate,
    user=Depends(require_admin)
):
    """
    Tạo FAQ mới (chỉ admin).
    """
    data = _load_faq_data()
    faqs = data.get("faqs", [])
    
    new_faq = {
        "id": _generate_id(),
        "question": faq_data.question,
        "answer": faq_data.answer,
        "steps": faq_data.steps,
        "form_ids": faq_data.form_ids,
        "domain": faq_data.domain,
        "ward_scope": faq_data.ward_scope,
        "review_status": faq_data.review_status or "draft",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "approved_by": None,
    }
    
    faqs.append(new_faq)
    data["faqs"] = faqs
    _save_faq_data(data)
    
    return FaqResponse(**new_faq)

@router.put("/{faq_id}", response_model=FaqResponse)
async def update_faq(
    faq_id: str,
    faq_update: FaqUpdate,
    user=Depends(require_admin)
):
    """
    Cập nhật FAQ (chỉ admin).
    """
    data = _load_faq_data()
    faqs = data.get("faqs", [])
    
    for i, faq in enumerate(faqs):
        if faq.get("id") == faq_id:
            # Update fields
            update_dict = faq_update.dict(exclude_unset=True)
            for key, value in update_dict.items():
                faqs[i][key] = value
            faqs[i]["updated_at"] = datetime.now().isoformat()
            
            # If approving, set approved_by
            if update_dict.get("review_status") == "approved":
                faqs[i]["approved_by"] = user.get("username", "admin")
            
            data["faqs"] = faqs
            _save_faq_data(data)
            return FaqResponse(**faqs[i])
    
    raise HTTPException(status_code=404, detail="FAQ không tồn tại")

@router.delete("/{faq_id}", status_code=204)
async def delete_faq(
    faq_id: str,
    user=Depends(require_admin)
):
    """
    Xóa FAQ (chỉ admin).
    """
    data = _load_faq_data()
    faqs = data.get("faqs", [])
    
    new_faqs = [f for f in faqs if f.get("id") != faq_id]
    if len(new_faqs) == len(faqs):
        raise HTTPException(status_code=404, detail="FAQ không tồn tại")
    
    data["faqs"] = new_faqs
    _save_faq_data(data)
    return None

@router.post("/seed", status_code=201)
async def seed_faqs(user=Depends(require_admin)):
    """
    Seed FAQ mẫu từ file notebook_data/faq_seed.json (chỉ admin).
    """
    seed_file = os.path.join(os.path.dirname(__file__), "..", "..", "notebook_data", "faq_seed.json")
    
    if not os.path.exists(seed_file):
        raise HTTPException(status_code=404, detail="File faq_seed.json không tồn tại")
    
    with open(seed_file, "r", encoding="utf-8") as f:
        seed_data = json.load(f)
    
    data = _load_faq_data()
    existing_faqs = data.get("faqs", [])
    existing_ids = {f.get("id") for f in existing_faqs}
    
    imported_count = 0
    for item in seed_data:
        if item.get("id") not in existing_ids:
            new_faq = {
                "id": item.get("id", _generate_id()),
                "question": item["question"],
                "answer": item["answer"],
                "steps": item.get("steps", []),
                "form_ids": item.get("form_ids", []),
                "domain": item["domain"],
                "ward_scope": item.get("ward_scope"),
                "review_status": item.get("review_status", "approved"),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "approved_by": "system_seed",
            }
            existing_faqs.append(new_faq)
            imported_count += 1
    
    data["faqs"] = existing_faqs
    _save_faq_data(data)
    
    return {"imported": imported_count, "total": len(existing_faqs)}
'''

# Write FAQ router
faq_router_path = Path("api/routers/faq.py")
faq_router_path.write_text(faq_router_code, encoding="utf-8", newline="\n")
print(f"OK: Created {faq_router_path} ({faq_router_path.stat().st_size} bytes)")

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
        "answer": "Quy trình khiếu nại:\n- Thời hạn: 90 ngày kể từ ngày nhận quyết định\n- Nộp tại cơ quan ra quyết định hoặc Tòa án\n- Hồ sơ: Đơn khiếu nại, quyết định xử phạt, bằng chứng\n\nLưu ý: Có thể khiếu nại hoặc khởi kiện nhưng không同时进行.",
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

# Check if main.py needs to include faq router
main_py = Path("api/main.py")
if main_py.exists():
    main_content = main_py.read_text(encoding="utf-8")
    if "faq" not in main_content.lower():
        print("NOTE: api/main.py may need to include faq router")
        # Show current routers
        for line in main_content.splitlines():
            if "include_router" in line or "APIRouter" in line:
                print(f"  {line.strip()}")
    else:
        print("OK: faq router already included in main.py")
else:
    print("WARNING: api/main.py not found")

print("\n=== STEP 5 COMPLETE ===")
print("Created:")
print("  - api/routers/faq.py (FAQ CRUD API)")
print("  - notebook_data/faq_seed.json (10 approved FAQs)")
print("\nEndpoints:")
print("  GET  /api/faq/           - List approved FAQs")
print("  GET  /api/faq/{id}       - Get single FAQ")
print("  POST /api/faq/           - Create FAQ (admin only)")
print("  PUT  /api/faq/{id}       - Update FAQ (admin only)")
print("  DELETE /api/faq/{id}     - Delete FAQ (admin only)")
print("  POST /api/faq/seed       - Seed from file (admin only)")
