import os
import json
from sqlalchemy import create_engine, text

def main():
    env_url = os.environ.get("LEGAL_RELEASE_DATABASE_URL", "postgresql+psycopg2://postgres:123456@localhost:5432/legal_chatbot")
    database_url = env_url.replace("host.docker.internal", "localhost")
    engine = create_engine(database_url)
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS legal_topics (
                id VARCHAR(100) PRIMARY KEY,
                domain VARCHAR(100) NOT NULL,
                name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS legal_topic_rules (
                topic_id VARCHAR(100) REFERENCES legal_topics(id) ON DELETE CASCADE,
                keyword VARCHAR(255) NOT NULL,
                is_forbidden BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (topic_id, keyword)
            );
        """))

        # Seed data
        topics = [
            ("can_cuoc_dinh_danh", "cu_tru_an_ninh", "Căn cước / Định danh"),
            ("thuong_tru", "cu_tru_an_ninh", "Thường trú"),
            ("tam_tru", "cu_tru_an_ninh", "Tạm trú"),
            ("luu_tru", "cu_tru_an_ninh", "Lưu trú"),
            ("vneid", "cu_tru_an_ninh", "VNeID"),
            ("ho_chieu", "cu_tru_an_ninh", "Hộ chiếu"),
            ("xuat_nhap_canh", "cu_tru_an_ninh", "Xuất nhập cảnh"),
            ("an_ninh_trat_tu", "cu_tru_an_ninh", "An ninh trật tự"),
            
            # Khiếu nại - Tố cáo - Xử phạt
            ("khieu_nai_lan_1", "khieu_nai_to_cao_xu_phat", "Khiếu nại lần 1"),
            ("khieu_nai_lan_2", "khieu_nai_to_cao_xu_phat", "Khiếu nại lần 2"),
            ("to_cao", "khieu_nai_to_cao_xu_phat", "Tố cáo"),
            ("tham_quyen_giai_quyet", "khieu_nai_to_cao_xu_phat", "Thẩm quyền giải quyết"),
            ("thoi_han_khieu_nai", "khieu_nai_to_cao_xu_phat", "Thời hạn khiếu nại"),
            ("tam_dinh_chi", "khieu_nai_to_cao_xu_phat", "Tạm đình chỉ"),
            ("nop_phat_vphc", "khieu_nai_to_cao_xu_phat", "Nộp phạt VPHC"),
            ("cuong_che_thi_hanh", "khieu_nai_to_cao_xu_phat", "Cưỡng chế thi hành"),
            ("giai_trinh", "khieu_nai_to_cao_xu_phat", "Giải trình"),
        ]

        for topic_id, domain, name in topics:
            conn.execute(text("""
                INSERT INTO legal_topics (id, domain, name) 
                VALUES (:id, :domain, :name)
                ON CONFLICT (id) DO UPDATE SET domain = EXCLUDED.domain, name = EXCLUDED.name
            """), {"id": topic_id, "domain": domain, "name": name})

        rules = [
            ("can_cuoc_dinh_danh", "căn cước", False),
            ("can_cuoc_dinh_danh", "cccd", False),
            ("can_cuoc_dinh_danh", "chứng minh nhân dân", False),
            ("can_cuoc_dinh_danh", "cmnd", False),
            ("can_cuoc_dinh_danh", "định danh", False),
            ("can_cuoc_dinh_danh", "mã định danh", False),
            ("can_cuoc_dinh_danh", "thường trú", True),
            ("can_cuoc_dinh_danh", "tạm trú", True),
            ("can_cuoc_dinh_danh", "lưu trú", True),
            ("can_cuoc_dinh_danh", "hộ khẩu", True),
            
            ("thuong_tru", "thường trú", False),
            ("thuong_tru", "nhập hộ khẩu", False),
            ("thuong_tru", "đăng ký thường trú", False),
            ("thuong_tru", "cắt khẩu", False),
            ("thuong_tru", "chuyển khẩu", False),
            ("thuong_tru", "căn cước", True),
            ("thuong_tru", "cccd", True),
            ("thuong_tru", "chứng minh nhân dân", True),
            ("thuong_tru", "cmnd", True),

            ("tam_tru", "tạm trú", False),
            ("tam_tru", "đăng ký tạm trú", False),
            ("tam_tru", "sổ tạm trú", False),
            ("tam_tru", "gia hạn tạm trú", False),
            ("tam_tru", "xác nhận tạm trú", False),
            ("tam_tru", "căn cước", True),
            ("tam_tru", "cccd", True),
            ("tam_tru", "thường trú", True),
            ("tam_tru", "nhập hộ khẩu", True),
        ]

        for topic_id, keyword, is_forbidden in rules:
            conn.execute(text("""
                INSERT INTO legal_topic_rules (topic_id, keyword, is_forbidden) 
                VALUES (:topic_id, :keyword, :is_forbidden)
                ON CONFLICT (topic_id, keyword) DO UPDATE SET is_forbidden = EXCLUDED.is_forbidden
            """), {"topic_id": topic_id, "keyword": keyword, "is_forbidden": is_forbidden})

    print("Created and seeded Taxonomy V1 tables successfully.")

if __name__ == "__main__":
    main()
