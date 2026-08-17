"""LegalTopicTaxonomyV1 — Phân loại chủ đề pháp luật theo 5 lĩnh vực công khai.

Cascading Intent Router (Feature 019):
  1. Exact match (số hiệu văn bản / điều khoản)
  2. Procedure ID đã xác nhận
  3. Topic / Procedure Family (qua LegalTopicTaxonomyV1 bên dưới)
  4. Domain chung
  5. Câu hỏi làm rõ — TUYỆT ĐỐI KHÔNG suy đoán bằng domain rộng

Không có network, model hay DB dependency — pure deterministic logic.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel


# ─── Enums ───────────────────────────────────────────────────────────────────

class LegalDomain(str, Enum):
    CU_TRU_AN_NINH = "cu_tru_an_ninh"
    KHIEU_NAI_TO_CAO_XU_PHAT = "khieu_nai_to_cao_xu_phat"
    HO_TICH_CHUNG_THUC = "ho_tich_chung_thuc"
    DAT_DAI_XAY_DUNG = "dat_dai_xay_dung"
    AN_SINH_Y_TE_GIAO_DUC = "an_sinh_y_te_giao_duc"


class LegalTopic(str, Enum):
    # Cư trú - An ninh
    THUONG_TRU = "thuong_tru"
    TAM_TRU = "tam_tru"
    LUU_TRU = "luu_tru"
    CAN_CUOC_DINH_DANH = "can_cuoc_dinh_danh"
    VNEID = "vneid"
    HO_CHIEU = "ho_chieu"
    XUAT_NHAP_CANH = "xuat_nhap_canh"
    AN_NINH_TRAT_TU = "an_ninh_trat_tu"

    # Khiếu nại - Tố cáo - Xử phạt
    KHIEU_NAI_LAN_1 = "khieu_nai_lan_1"
    KHIEU_NAI_LAN_2 = "khieu_nai_lan_2"
    TO_CAO = "to_cao"
    THAM_QUYEN_GIAI_QUYET = "tham_quyen_giai_quyet"
    THOI_HAN_KHIEU_NAI = "thoi_han_khieu_nai"
    TAM_DINH_CHI = "tam_dinh_chi"
    NOP_PHAT_VPHC = "nop_phat_vphc"
    CUONG_CHE_THI_HANH = "cuong_che_thi_hanh"
    GIAI_TRINH = "giai_trinh"

    # Hộ tịch - Chứng thực
    KHAI_SINH = "khai_sinh"
    KET_HON = "ket_hon"
    KHAI_TU = "khai_tu"
    NHAN_CHA_ME_CON = "nhan_cha_me_con"
    GIAM_HO = "giam_ho"
    CHUNG_THUC_CHU_KY = "chung_thuc_chu_ky"
    CHUNG_THUC_BAN_SAO = "chung_thuc_ban_sao"
    CHUNG_THUC_HOP_DONG = "chung_thuc_hop_dong"

    # Đất đai - Xây dựng
    CAP_GCN_LAN_DAU = "cap_gcn_lan_dau"
    DANG_KY_BIEN_DONG = "dang_ky_bien_dong"
    CHUYEN_MUC_DICH = "chuyen_muc_dich"
    TRANH_CHAP_DAT_DAI = "tranh_chap_dat_dai"
    CAP_PHEP_XAY_DUNG = "cap_phep_xay_dung"
    QUY_HOACH_CHI_GIOI = "quy_hoach_chi_gioi"

    # An sinh - Y tế - Giáo dục
    BAO_HIEM_Y_TE = "bao_hiem_y_te"
    TRO_CAP_XA_HOI = "tro_cap_xa_hoi"
    NGUOI_CO_CONG = "nguoi_co_cong"
    HO_NGHEO = "ho_ngheo"
    NHAP_HOC_CHUYEN_TRUONG = "nhap_hoc_chuyen_truong"
    TIEM_CHUNG = "tiem_chung"


# ─── Topic → Domain mapping ───────────────────────────────────────────────────

TOPIC_DOMAIN_MAP: Dict[LegalTopic, LegalDomain] = {
    LegalTopic.THUONG_TRU: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.TAM_TRU: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.LUU_TRU: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.CAN_CUOC_DINH_DANH: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.VNEID: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.HO_CHIEU: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.XUAT_NHAP_CANH: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.AN_NINH_TRAT_TU: LegalDomain.CU_TRU_AN_NINH,
    LegalTopic.KHIEU_NAI_LAN_1: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.KHIEU_NAI_LAN_2: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.TO_CAO: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.THAM_QUYEN_GIAI_QUYET: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.THOI_HAN_KHIEU_NAI: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.TAM_DINH_CHI: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.NOP_PHAT_VPHC: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.CUONG_CHE_THI_HANH: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.GIAI_TRINH: LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT,
    LegalTopic.KHAI_SINH: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.KET_HON: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.KHAI_TU: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.NHAN_CHA_ME_CON: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.GIAM_HO: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.CHUNG_THUC_CHU_KY: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.CHUNG_THUC_BAN_SAO: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.CHUNG_THUC_HOP_DONG: LegalDomain.HO_TICH_CHUNG_THUC,
    LegalTopic.CAP_GCN_LAN_DAU: LegalDomain.DAT_DAI_XAY_DUNG,
    LegalTopic.DANG_KY_BIEN_DONG: LegalDomain.DAT_DAI_XAY_DUNG,
    LegalTopic.CHUYEN_MUC_DICH: LegalDomain.DAT_DAI_XAY_DUNG,
    LegalTopic.TRANH_CHAP_DAT_DAI: LegalDomain.DAT_DAI_XAY_DUNG,
    LegalTopic.CAP_PHEP_XAY_DUNG: LegalDomain.DAT_DAI_XAY_DUNG,
    LegalTopic.QUY_HOACH_CHI_GIOI: LegalDomain.DAT_DAI_XAY_DUNG,
    LegalTopic.BAO_HIEM_Y_TE: LegalDomain.AN_SINH_Y_TE_GIAO_DUC,
    LegalTopic.TRO_CAP_XA_HOI: LegalDomain.AN_SINH_Y_TE_GIAO_DUC,
    LegalTopic.NGUOI_CO_CONG: LegalDomain.AN_SINH_Y_TE_GIAO_DUC,
    LegalTopic.HO_NGHEO: LegalDomain.AN_SINH_Y_TE_GIAO_DUC,
    LegalTopic.NHAP_HOC_CHUYEN_TRUONG: LegalDomain.AN_SINH_Y_TE_GIAO_DUC,
    LegalTopic.TIEM_CHUNG: LegalDomain.AN_SINH_Y_TE_GIAO_DUC,
}

DOMAIN_TOPICS_MAPPING: Dict[LegalDomain, List[LegalTopic]] = {
    LegalDomain.CU_TRU_AN_NINH: [
        LegalTopic.THUONG_TRU, LegalTopic.TAM_TRU, LegalTopic.LUU_TRU,
        LegalTopic.CAN_CUOC_DINH_DANH, LegalTopic.VNEID, LegalTopic.HO_CHIEU,
        LegalTopic.XUAT_NHAP_CANH, LegalTopic.AN_NINH_TRAT_TU,
    ],
    LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT: [
        LegalTopic.KHIEU_NAI_LAN_1, LegalTopic.KHIEU_NAI_LAN_2, LegalTopic.TO_CAO,
        LegalTopic.THAM_QUYEN_GIAI_QUYET, LegalTopic.THOI_HAN_KHIEU_NAI, LegalTopic.TAM_DINH_CHI,
        LegalTopic.NOP_PHAT_VPHC, LegalTopic.CUONG_CHE_THI_HANH, LegalTopic.GIAI_TRINH,
    ],
    LegalDomain.HO_TICH_CHUNG_THUC: [
        LegalTopic.KHAI_SINH, LegalTopic.KET_HON, LegalTopic.KHAI_TU,
        LegalTopic.NHAN_CHA_ME_CON, LegalTopic.GIAM_HO, LegalTopic.CHUNG_THUC_CHU_KY,
        LegalTopic.CHUNG_THUC_BAN_SAO, LegalTopic.CHUNG_THUC_HOP_DONG,
    ],
    LegalDomain.DAT_DAI_XAY_DUNG: [
        LegalTopic.CAP_GCN_LAN_DAU, LegalTopic.DANG_KY_BIEN_DONG, LegalTopic.CHUYEN_MUC_DICH,
        LegalTopic.TRANH_CHAP_DAT_DAI, LegalTopic.CAP_PHEP_XAY_DUNG, LegalTopic.QUY_HOACH_CHI_GIOI,
    ],
    LegalDomain.AN_SINH_Y_TE_GIAO_DUC: [
        LegalTopic.BAO_HIEM_Y_TE, LegalTopic.TRO_CAP_XA_HOI, LegalTopic.NGUOI_CO_CONG,
        LegalTopic.HO_NGHEO, LegalTopic.NHAP_HOC_CHUYEN_TRUONG, LegalTopic.TIEM_CHUNG,
    ],
}


# ─── Taxonomy Rule ────────────────────────────────────────────────────────────

class TaxonomyRule(BaseModel):
    topic: LegalTopic
    keywords: List[str]
    forbidden_keywords: List[str] = []
    procedure_families: List[str] = []
    priority: int = 0


# ─── Full TAXONOMY_RULES — all 5 domains × topics ─────────────────────────────

TAXONOMY_RULES: List[TaxonomyRule] = [
    # ── Cư trú - An ninh ─────────────────────────────────────────────────────
    TaxonomyRule(
        topic=LegalTopic.CAN_CUOC_DINH_DANH, priority=10,
        keywords=[
            "can cuoc", "cccd", "chung minh nhan dan", "cmnd", "dinh danh",
            "the can cuoc", "cap the can cuoc", "doi the can cuoc",
            "ma dinh danh", "so dinh danh ca nhan", "luat can cuoc",
            "lam can cuoc", "gia han can cuoc", "can cuoc cong dan",
            "thu tuc cap can cuoc", "thu tuc cap the can cuoc",
        ],
        forbidden_keywords=[
            "thuong tru", "tam tru", "luu tru", "ho khau", "dang ky cu tru",
            "khieu nai", "to cao", "xu phat", "khai sinh", "ket hon",
        ],
        procedure_families=["cap_can_cuoc", "doi_can_cuoc", "dinh_danh_dien_tu"],
    ),
    TaxonomyRule(
        topic=LegalTopic.VNEID, priority=10,
        keywords=[
            "vneid", "vne id", "dinh danh dien tu", "tai khoan dinh danh",
            "ung dung dinh danh", "cap tai khoan vneid", "kich hoat vneid",
            "xac thuc sinh trac", "qr code can cuoc",
        ],
        forbidden_keywords=["thuong tru", "tam tru", "khieu nai"],
        procedure_families=["vneid_registration", "vneid_activation"],
    ),
    TaxonomyRule(
        topic=LegalTopic.THUONG_TRU, priority=5,
        keywords=[
            "thuong tru", "nhap ho khau", "dang ky thuong tru", "cat khau",
            "chuyen khau", "nhap khau", "xac nhan thuong tru", "so ho khau",
            "noi thuong tru", "ho khau thuong tru",
        ],
        forbidden_keywords=["can cuoc", "cccd", "cmnd", "dinh danh", "tam tru"],
        procedure_families=["dang_ky_thuong_tru", "chuyen_khau"],
    ),
    TaxonomyRule(
        topic=LegalTopic.TAM_TRU, priority=5,
        keywords=[
            "tam tru", "dang ky tam tru", "so tam tru", "gia han tam tru",
            "xac nhan tam tru", "noi tam tru",
        ],
        forbidden_keywords=["can cuoc", "cccd", "thuong tru", "nhap ho khau"],
        procedure_families=["dang_ky_tam_tru"],
    ),
    TaxonomyRule(
        topic=LegalTopic.LUU_TRU, priority=5,
        keywords=["luu tru", "dang ky luu tru", "khai bao luu tru", "noi luu tru"],
        forbidden_keywords=["can cuoc", "thuong tru", "tam tru"],
        procedure_families=["khai_bao_luu_tru"],
    ),
    TaxonomyRule(
        topic=LegalTopic.HO_CHIEU, priority=5,
        keywords=[
            "ho chieu", "passport", "cap ho chieu", "gia han ho chieu",
            "thu tuc ho chieu", "xin ho chieu",
        ],
        forbidden_keywords=["can cuoc", "thuong tru"],
        procedure_families=["cap_ho_chieu"],
    ),
    TaxonomyRule(
        topic=LegalTopic.XUAT_NHAP_CANH, priority=5,
        keywords=[
            "xuat nhap canh", "visa", "thi thuc", "nhap canh", "xuat canh",
            "cho o", "thi thuc nhap canh", "cap visa",
        ],
        forbidden_keywords=["ho chieu", "thuong tru"],
        procedure_families=["cap_visa", "xin_thi_thuc"],
    ),
    TaxonomyRule(
        topic=LegalTopic.AN_NINH_TRAT_TU, priority=3,
        keywords=[
            "an ninh trat tu", "giay phep kinh doanh dich vu",
            "dich vu co dieu kien an ninh", "giay phep vu khi",
            "phong chay chua chay",
        ],
        forbidden_keywords=[],
        procedure_families=["dich_vu_an_ninh"],
    ),
    # ── Khiếu nại - Tố cáo - Xử phạt ────────────────────────────────────────
    TaxonomyRule(
        topic=LegalTopic.KHIEU_NAI_LAN_1, priority=8,
        keywords=[
            "khieu nai lan dau", "khieu nai lan thu nhat", "khieu nai lan 1",
            "gui khieu nai", "don khieu nai", "khieu nai quyet dinh hanh chinh",
            "khieu nai hanh vi hanh chinh", "nop don khieu nai",
        ],
        forbidden_keywords=["to cao", "xu phat", "nop phat", "can cuoc"],
        procedure_families=["khieu_nai_lan_1"],
    ),
    TaxonomyRule(
        topic=LegalTopic.KHIEU_NAI_LAN_2, priority=8,
        keywords=[
            "khieu nai lan 2", "khieu nai lan thu hai", "tiep tuc khieu nai",
            "khieu nai khong dong y",
        ],
        forbidden_keywords=["to cao", "khieu nai lan dau"],
        procedure_families=["khieu_nai_lan_2"],
    ),
    TaxonomyRule(
        topic=LegalTopic.TO_CAO, priority=8,
        keywords=[
            "to cao", "don to cao", "gui to cao", "to cao hanh vi vi pham",
            "to cao tham nhung", "bao ve nguoi to cao", "gui don to cao",
            "thu tuc to cao",
        ],
        forbidden_keywords=["khieu nai", "xu phat", "nop phat"],
        procedure_families=["to_cao"],
    ),
    TaxonomyRule(
        topic=LegalTopic.THAM_QUYEN_GIAI_QUYET, priority=7,
        keywords=[
            "tham quyen giai quyet", "ai giai quyet", "co quan nao giai quyet",
            "thu truong giai quyet", "cap nao giai quyet khieu nai",
        ],
        forbidden_keywords=[],
        procedure_families=["tham_quyen_khieu_nai"],
    ),
    TaxonomyRule(
        topic=LegalTopic.THOI_HAN_KHIEU_NAI, priority=7,
        keywords=[
            "thoi han khieu nai", "han giai quyet khieu nai", "thoi gian khieu nai",
            "thoi hieu khieu nai", "bao nhieu ngay giai quyet",
        ],
        forbidden_keywords=[],
        procedure_families=["thoi_han_khieu_nai"],
    ),
    TaxonomyRule(
        topic=LegalTopic.NOP_PHAT_VPHC, priority=8,
        keywords=[
            "nop phat", "phat hanh chinh", "xu phat vi pham hanh chinh",
            "bien ban vi pham", "quyet dinh xu phat", "xu phat hanh chinh",
            "nop tien phat",
        ],
        forbidden_keywords=["khieu nai", "to cao"],
        procedure_families=["nop_phat_vphc"],
    ),
    TaxonomyRule(
        topic=LegalTopic.CUONG_CHE_THI_HANH, priority=7,
        keywords=[
            "cuong che", "thi hanh quyet dinh xu phat", "cuong che hanh chinh",
            "khau tru luong", "ke bien tai san",
        ],
        forbidden_keywords=[],
        procedure_families=["cuong_che_thi_hanh"],
    ),
    TaxonomyRule(
        topic=LegalTopic.TAM_DINH_CHI, priority=7,
        keywords=[
            "tam dinh chi", "dinh chi giai quyet khieu nai", "rut khieu nai",
            "rut don khieu nai",
        ],
        forbidden_keywords=[],
        procedure_families=["tam_dinh_chi_khieu_nai"],
    ),
    TaxonomyRule(
        topic=LegalTopic.GIAI_TRINH, priority=7,
        keywords=["giai trinh", "yeu cau giai trinh", "quyen giai trinh"],
        forbidden_keywords=[],
        procedure_families=["giai_trinh"],
    ),
    # ── Hộ tịch - Chứng thực ─────────────────────────────────────────────────
    TaxonomyRule(
        topic=LegalTopic.KHAI_SINH, priority=8,
        keywords=[
            "khai sinh", "dang ky khai sinh", "giay khai sinh", "khai sinh muon",
            "thu tuc khai sinh", "tre so sinh", "dang ky sinh",
        ],
        forbidden_keywords=["khai tu", "ket hon", "can cuoc"],
        procedure_families=["dang_ky_khai_sinh"],
    ),
    TaxonomyRule(
        topic=LegalTopic.KET_HON, priority=8,
        keywords=[
            "ket hon", "dang ky ket hon", "giay dang ky ket hon", "hon nhan",
            "thu tuc ket hon", "dang ky hon nhan",
        ],
        forbidden_keywords=["khai sinh", "khai tu"],
        procedure_families=["dang_ky_ket_hon"],
    ),
    TaxonomyRule(
        topic=LegalTopic.KHAI_TU, priority=8,
        keywords=[
            "khai tu", "dang ky khai tu", "giay bao tu", "thu tuc khai tu",
            "khai bao tu vong", "chung tu", "giay chung tu",
        ],
        forbidden_keywords=["khai sinh", "ket hon"],
        procedure_families=["dang_ky_khai_tu"],
    ),
    TaxonomyRule(
        topic=LegalTopic.NHAN_CHA_ME_CON, priority=7,
        keywords=[
            "nhan cha", "nhan me", "nhan con", "xac nhan cha con",
            "thu tuc nhan cha me con", "quyen nuoi con",
        ],
        forbidden_keywords=[],
        procedure_families=["nhan_cha_me_con"],
    ),
    TaxonomyRule(
        topic=LegalTopic.GIAM_HO, priority=7,
        keywords=["giam ho", "nguoi giam ho", "dang ky giam ho", "giam ho tre em"],
        forbidden_keywords=[],
        procedure_families=["dang_ky_giam_ho"],
    ),
    TaxonomyRule(
        topic=LegalTopic.CHUNG_THUC_CHU_KY, priority=7,
        keywords=["chung thuc chu ky", "cong chung chu ky", "xac nhan chu ky"],
        forbidden_keywords=["chung thuc ban sao", "chung thuc hop dong"],
        procedure_families=["chung_thuc_chu_ky"],
    ),
    TaxonomyRule(
        topic=LegalTopic.CHUNG_THUC_BAN_SAO, priority=7,
        keywords=[
            "chung thuc ban sao", "cong chung ban sao", "xac thuc ban sao",
            "sao y ban chinh", "chung thuc photocopy",
        ],
        forbidden_keywords=["chung thuc chu ky", "chung thuc hop dong"],
        procedure_families=["chung_thuc_ban_sao"],
    ),
    TaxonomyRule(
        topic=LegalTopic.CHUNG_THUC_HOP_DONG, priority=7,
        keywords=[
            "chung thuc hop dong", "cong chung hop dong", "cong chung giao dich",
            "cong chung van ban", "hop dong mua ban",
        ],
        forbidden_keywords=["chung thuc chu ky", "chung thuc ban sao"],
        procedure_families=["chung_thuc_hop_dong"],
    ),
    # ── Đất đai - Xây dựng ───────────────────────────────────────────────────
    TaxonomyRule(
        topic=LegalTopic.CAP_GCN_LAN_DAU, priority=8,
        keywords=[
            "so do", "gcn quyen su dung dat", "cap giay chung nhan",
            "cap so do lan dau", "thu tuc cap so do", "dang ky quyen su dung dat",
            "cap gcn", "giay chung nhan quyen su dung dat",
        ],
        forbidden_keywords=["xay dung", "cap phep xay dung"],
        procedure_families=["cap_gcn_lan_dau"],
    ),
    TaxonomyRule(
        topic=LegalTopic.DANG_KY_BIEN_DONG, priority=8,
        keywords=[
            "bien dong dat", "sang ten dat", "chuyen nhuong dat", "thua ke dat",
            "tang cho dat", "tach thua", "hop thua", "dang ky bien dong",
        ],
        forbidden_keywords=["xay dung", "cap phep"],
        procedure_families=["dang_ky_bien_dong"],
    ),
    TaxonomyRule(
        topic=LegalTopic.CHUYEN_MUC_DICH, priority=7,
        keywords=[
            "chuyen muc dich su dung dat", "xin chuyen dat",
            "chuyen dat nong nghiep", "chuyen dat o",
        ],
        forbidden_keywords=[],
        procedure_families=["chuyen_muc_dich_su_dung_dat"],
    ),
    TaxonomyRule(
        topic=LegalTopic.TRANH_CHAP_DAT_DAI, priority=7,
        keywords=[
            "tranh chap dat", "giai quyet tranh chap dat",
            "tranh chap quyen su dung dat",
        ],
        forbidden_keywords=[],
        procedure_families=["giai_quyet_tranh_chap_dat"],
    ),
    TaxonomyRule(
        topic=LegalTopic.CAP_PHEP_XAY_DUNG, priority=8,
        keywords=[
            "giay phep xay dung", "cap phep xay dung", "xin phep xay dung",
            "thu tuc xay dung nha o", "xay dung nha", "giay phep xay dung nha",
        ],
        forbidden_keywords=["so do", "gcn", "quyen su dung dat"],
        procedure_families=["cap_phep_xay_dung"],
    ),
    TaxonomyRule(
        topic=LegalTopic.QUY_HOACH_CHI_GIOI, priority=6,
        keywords=[
            "quy hoach", "chi gioi xay dung", "chi gioi duong do",
            "xin thong tin quy hoach", "tra cuu quy hoach",
        ],
        forbidden_keywords=[],
        procedure_families=["tra_cuu_quy_hoach"],
    ),
    # ── An sinh - Y tế - Giáo dục ─────────────────────────────────────────────
    TaxonomyRule(
        topic=LegalTopic.BAO_HIEM_Y_TE, priority=8,
        keywords=[
            "bao hiem y te", "bhyt", "the bhyt", "cap the bao hiem y te",
            "muc huong bao hiem y te", "thanh toan bao hiem y te",
            "kham chua benh bhyt",
        ],
        forbidden_keywords=["tro cap xa hoi", "nguoi co cong"],
        procedure_families=["cap_the_bhyt", "dang_ky_bhyt"],
    ),
    TaxonomyRule(
        topic=LegalTopic.TRO_CAP_XA_HOI, priority=8,
        keywords=[
            "tro cap xa hoi", "tro cap hang thang", "tro cap tai nan",
            "tro cap nguoi tan tat", "tro cap nguoi cao tuoi", "ho tro xa hoi",
        ],
        forbidden_keywords=["bao hiem y te", "bhyt"],
        procedure_families=["tro_cap_xa_hoi"],
    ),
    TaxonomyRule(
        topic=LegalTopic.NGUOI_CO_CONG, priority=8,
        keywords=[
            "nguoi co cong", "liet si", "thuong binh", "benh binh",
            "me viet nam anh hung", "che do nguoi co cong",
            "tro cap nguoi co cong", "xac nhan nguoi co cong",
        ],
        forbidden_keywords=[],
        procedure_families=["che_do_nguoi_co_cong"],
    ),
    TaxonomyRule(
        topic=LegalTopic.HO_NGHEO, priority=8,
        keywords=[
            "ho ngheo", "ho can ngheo", "thoat ngheo", "xet ho ngheo",
            "danh sach ho ngheo", "cap giay chung nhan ho ngheo",
        ],
        forbidden_keywords=["tro cap", "nguoi co cong"],
        procedure_families=["xet_ho_ngheo"],
    ),
    TaxonomyRule(
        topic=LegalTopic.NHAP_HOC_CHUYEN_TRUONG, priority=7,
        keywords=[
            "nhap hoc", "chuyen truong", "dang ky nhap hoc", "xin chuyen truong",
            "ho so nhap hoc", "thu tuc nhap hoc",
        ],
        forbidden_keywords=[],
        procedure_families=["nhap_hoc", "chuyen_truong"],
    ),
    TaxonomyRule(
        topic=LegalTopic.TIEM_CHUNG, priority=7,
        keywords=[
            "tiem chung", "vaccine", "tiem vac xin", "lich tiem chung",
            "tiem phong", "thu tuc tiem chung",
        ],
        forbidden_keywords=[],
        procedure_families=["tiem_chung"],
    ),
]

# Sort by priority descending so highest-priority rules are checked first
TAXONOMY_RULES.sort(key=lambda r: r.priority, reverse=True)


# ─── Normalization ────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    """Normalize Vietnamese text: NFD → remove diacritics → casefold → replace đ→d."""
    normalized = unicodedata.normalize("NFD", str(text or "").casefold())
    normalized = normalized.replace("\u0111", "d").replace("\u0110", "d")
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


# ─── TopicClassification dataclass ───────────────────────────────────────────

@dataclass
class TopicClassification:
    topics: List[LegalTopic] = field(default_factory=list)
    domain: Optional[LegalDomain] = None
    confidence: str = "none"  # "topic" | "domain" | "none"
    matched_keywords: List[str] = field(default_factory=list)
    needs_clarification: bool = False


# ─── Domain fallback keyword map ──────────────────────────────────────────────

_DOMAIN_FALLBACK_KEYWORDS: Dict[LegalDomain, List[str]] = {
    LegalDomain.CU_TRU_AN_NINH: [
        "cu tru", "ho khau", "co tru", "dan cu", "an ninh",
    ],
    LegalDomain.KHIEU_NAI_TO_CAO_XU_PHAT: [
        "khieu nai", "to cao", "xu phat", "vi pham hanh chinh", "phat tien",
    ],
    LegalDomain.HO_TICH_CHUNG_THUC: [
        "ho tich", "chung thuc", "khai sinh", "ket hon", "cong chung",
    ],
    LegalDomain.DAT_DAI_XAY_DUNG: [
        "dat dai", "xay dung", "quyen su dung dat", "so do", "nha dat",
    ],
    LegalDomain.AN_SINH_Y_TE_GIAO_DUC: [
        "bao hiem", "y te", "giao duc", "xa hoi", "tro cap", "hoc sinh",
    ],
}


def _infer_domain_fallback(folded: str) -> Optional[LegalDomain]:
    for domain, keywords in _DOMAIN_FALLBACK_KEYWORDS.items():
        if any(_fold(kw) in folded for kw in keywords):
            return domain
    return None


# ─── Main classifiers ─────────────────────────────────────────────────────────

def classify_topic_v1(query: str) -> TopicClassification:
    """
    LegalTopicTaxonomyV1 — Cascading Intent Router (step 3: topic classification).

    Returns TopicClassification with matched topics and confidence level.
    """
    folded = _fold(query)
    matched: List[Tuple[LegalTopic, int, List[str]]] = []

    for rule in TAXONOMY_RULES:
        # Hard-negative guard
        if any(_fold(fk) in folded for fk in rule.forbidden_keywords):
            continue
        hits = [kw for kw in rule.keywords if _fold(kw) in folded]
        if hits:
            matched.append((rule.topic, rule.priority, hits))

    if not matched:
        domain = _infer_domain_fallback(folded)
        if domain:
            return TopicClassification(
                domain=domain, confidence="domain", needs_clarification=True,
            )
        return TopicClassification(confidence="none", needs_clarification=True)

    seen: Set[LegalTopic] = set()
    unique_topics: List[LegalTopic] = []
    all_kw: List[str] = []
    for topic, _, hits in matched:
        if topic not in seen:
            seen.add(topic)
            unique_topics.append(topic)
            all_kw.extend(hits)

    primary_domain = TOPIC_DOMAIN_MAP.get(unique_topics[0]) if unique_topics else None

    return TopicClassification(
        topics=unique_topics,
        domain=primary_domain,
        confidence="topic",
        matched_keywords=list(dict.fromkeys(all_kw)),
        needs_clarification=False,
    )


def classify_topic(query: str) -> List[LegalTopic]:
    """Backward-compatible wrapper: returns matched topics list for a query."""
    return classify_topic_v1(query).topics


# ─── Evidence gate helpers ────────────────────────────────────────────────────

def topics_allow_source(
    question_topics: List[LegalTopic],
    source_domain: Optional[str],
    source_topics: Optional[List[str]] = None,
) -> bool:
    """
    Check if a retrieval source is allowed for the given question topics.
    Returns False when the source domain conflicts with question topic domains.
    """
    if not question_topics:
        return True
    if not source_domain:
        return True

    question_domains: Set[LegalDomain] = {
        TOPIC_DOMAIN_MAP[t] for t in question_topics if t in TOPIC_DOMAIN_MAP
    }
    try:
        src_domain = LegalDomain(source_domain)
    except ValueError:
        return True  # Unknown domain string — allow conservatively

    return src_domain in question_domains


def get_forbidden_topics_for(topic: LegalTopic) -> List[LegalTopic]:
    """Return topics forbidden (cross-contamination) for the given topic."""
    domain = TOPIC_DOMAIN_MAP.get(topic)
    if not domain:
        return []

    forbidden: Set[LegalTopic] = {
        t for t, d in TOPIC_DOMAIN_MAP.items() if t != topic and d != domain
    }

    # Intra-domain hard-negative guards
    if topic == LegalTopic.CAN_CUOC_DINH_DANH:
        forbidden |= {LegalTopic.THUONG_TRU, LegalTopic.TAM_TRU, LegalTopic.LUU_TRU}
    elif topic in (LegalTopic.THUONG_TRU, LegalTopic.TAM_TRU, LegalTopic.LUU_TRU):
        forbidden.add(LegalTopic.CAN_CUOC_DINH_DANH)

    return list(forbidden)
