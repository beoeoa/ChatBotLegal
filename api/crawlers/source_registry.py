"""
Source Registry - Central configuration for all legal data sources.

Defines crawl sources with URL patterns, crawl strategies, metadata mapping,
and scheduling configuration. All sources feed into the candidate review queue.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from bs4 import BeautifulSoup
from loguru import logger


class SourceType(str, Enum):
    VBPL = "vbpl"                    # VBPL.vn - Thư viện pháp luật (reference)
    DVC_NATIONAL = "dvc_national"   # Cổng Dịch vụ công Quốc gia
    DVC_HAIPHONG = "dvc_haiphong"   # Cổng Dịch vụ công Hải Phòng
    UBND_HAIPHONG = "ubnd_haiphong" # Cổng thông tin UBND Hải Phòng
    SO_NGANH = "so_nganh"           # Website Sở/Ban/Ngành Hải Phòng
    QUAN_HUYEN = "quan_huyen"       # UBND quận/huyện
    PHUONG_XA = "phuong_xa"         # UBND phường/xã


class OfficialLevel(str, Enum):
    OFFICIAL = "official"           # Nguồn chính thức
    SEMI_OFFICIAL = "semi_official" # Trang cơ quan có độ tin cậy
    REFERENCE = "reference"         # Nguồn tham khảo/tổng hợp
    UNVERIFIED = "unverified"       # Chưa duyệt


@dataclass
class LegalSource:
    """Configuration for one legal data source."""

    source_id: str                          # Unique ID
    source_type: SourceType                 # Type of source
    name: str                               # Human-readable name
    base_url: str                           # Base URL for crawling
    official_level: OfficialLevel           # Trust level
    scope: str                              # central / haiphong / local
    domain: str                             # Primary legal domain
    enabled: bool = True

    # Crawl strategy
    crawl_strategy: str = "sitemap"         # sitemap / list_page / single_page / api
    sitemap_url: str | None = None          # Sitemap URL if strategy=sitemap
    list_urls: list[str] = field(default_factory=list)  # List page URLs
    article_pattern: str = ""               # Regex or CSS selector for article links

    # Schedule
    interval_minutes: int = 1440            # Default: daily
    lookback_days: int = 30                 # How far back to look
    max_per_run: int = 20                   # Max documents per crawl run

    # Content extraction
    title_selector: str = "h1"             # CSS selector for title
    content_selector: str = ""             # CSS selector for main content
    metadata_selectors: dict[str, str] = field(default_factory=dict)

    # JS rendering flag
    needs_js: bool = False            # True if page requires headless browser render
    
    # Metadata defaults
    default_issuing_agency: str = ""
    default_document_type: str = "VanBanPhapLuat"
    default_sector: str = ""

    # Status tracking
    last_crawl_at: str | None = None
    last_crawl_status: str | None = None
    last_crawl_count: int = 0
    total_crawled: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ====================================================================
# Pre-configured sources for Hai Phong legal assistant
# ====================================================================

DEFAULT_SOURCES: list[LegalSource] = [
    # 1. VBPL.vn - Thư viện pháp luật (reference, largest corpus)
    LegalSource(
        source_id="vbpl_main",
        source_type=SourceType.VBPL,
        name="VBPL.vn - Thư viện Pháp luật",
        base_url="https://vbpl.vn",
        official_level=OfficialLevel.REFERENCE,
        scope="central",
        domain="tat_ca",
        crawl_strategy="sitemap",
        sitemap_url="https://vbpl.vn/sitemap.xml",
        article_pattern="/[A-Z]+/Pages/vbpq-toanvan.aspx",
        interval_minutes=360,          # 6 hours
        lookback_days=7,
        max_per_run=50,
        title_selector="h1.title",
        content_selector="#contentPrint, .content",
        metadata_selectors={
            "law_number": ".sohieu",
            "issuing_agency": ".coquanbanhanh",
            "issued_date": ".ngaybanhanh",
            "effective_date": ".ngaycohieuluc",
        },
        default_sector="PhapLuat",
        needs_js=True,
    ),
    
    # 2. Cổng Dịch vụ công Quốc gia
    LegalSource(
        source_id="dvc_national",
        source_type=SourceType.DVC_NATIONAL,
        name="Cổng Dịch vụ công Quốc gia",
        base_url="https://dichvucong.gov.vn",
        official_level=OfficialLevel.OFFICIAL,
        scope="central",
        domain="thu_tuc_hanh_chinh",
        crawl_strategy="list_page",
        list_urls=[
            "https://dichvucong.gov.vn/p/home/dvc-thu-tuc-hanh-chinh.html",
        ],
        interval_minutes=720,          # 12 hours
        lookback_days=90,
        max_per_run=3,
        title_selector="h1",
        content_selector="#main-content, .thutuc-content",
        default_issuing_agency="Chính phủ Việt Nam",
        default_document_type="ThuTucHanhChinh",
        default_sector="HanhChinhCong",
        needs_js=True,

    ),
    
    # 3. Cổng Dịch vụ công Hải Phòng
    LegalSource(
        source_id="dvc_haiphong",
        source_type=SourceType.DVC_HAIPHONG,
        name="Cổng Dịch vụ công TP Hải Phòng",
        base_url="https://dichvucong.haiphong.gov.vn",
        official_level=OfficialLevel.OFFICIAL,
        scope="haiphong",
        domain="thu_tuc_hanh_chinh",
        crawl_strategy="list_page",
        list_urls=[
            "https://dichvucong.haiphong.gov.vn/danh-muc-thu-tuc",
            "https://dichvucong.haiphong.gov.vn/thu-tuc-hanh-chinh",
        ],
        interval_minutes=720,
        lookback_days=90,
        max_per_run=3,
        title_selector="h1, .title",
        content_selector="#main-content, .content",
        default_issuing_agency="UBND TP Hải Phòng",
        default_document_type="ThuTucHanhChinh",
        default_sector="HanhChinhCong",
        needs_js=True,

    ),
    
    # 4. Cổng thông tin UBND Hải Phòng
    LegalSource(
        source_id="ubnd_haiphong",
        source_type=SourceType.UBND_HAIPHONG,
        name="Cổng thông tin điện tử UBND TP Hải Phòng",
        base_url="https://haiphong.gov.vn",
        official_level=OfficialLevel.OFFICIAL,
        scope="haiphong",
        domain="tat_ca",
        crawl_strategy="list_page",
        list_urls=[
            "https://haiphong.gov.vn/Van-ban-quy-pham-phap-luat/",
            "https://haiphong.gov.vn/Van-ban-chi-dao-dieu-hanh/",
        ],
        interval_minutes=480,          # 8 hours
        lookback_days=60,
        max_per_run=3,
        title_selector="h1, .title, .detail-title",
        content_selector="#main-content, .detail-content, .article-content",
        default_issuing_agency="UBND TP Hải Phòng",
        default_sector="HanhChinhCong",
        needs_js=True,

    ),
    
    # 5. Sở Tư pháp Hải Phòng
    LegalSource(
        source_id="so_tu_phap_hp",
        source_type=SourceType.SO_NGANH,
        name="Sở Tư pháp TP Hải Phòng",
        base_url="https://sotuphap.haiphong.gov.vn",
        official_level=OfficialLevel.OFFICIAL,
        scope="haiphong",
        domain="tu_phap",
        crawl_strategy="list_page",
        list_urls=[
            "https://sotuphap.haiphong.gov.vn/van-ban-phap-luat/",
            "https://sotuphap.haiphong.gov.vn/thu-tuc-hanh-chinh/",
        ],
        interval_minutes=720,
        lookback_days=60,
        max_per_run=3,
        title_selector="h1, .title",
        content_selector="#main-content, .content",
        default_issuing_agency="Sở Tư pháp TP Hải Phòng",
        default_sector="TuPhap",
        needs_js=True,
    ),
    
    # 6. UBND quận Lê Chân (pilot ward)
    LegalSource(
        source_id="ubnd_quan_le_chan",
        source_type=SourceType.QUAN_HUYEN,
        name="UBND Quận Lê Chân - Hải Phòng",
        base_url="https://lechan.haiphong.gov.vn",
        official_level=OfficialLevel.OFFICIAL,
        scope="local",
        domain="tat_ca",
        crawl_strategy="list_page",
        list_urls=[
            "https://lechan.haiphong.gov.vn/van-ban/",
            "https://lechan.haiphong.gov.vn/thong-bao/",
        ],
        interval_minutes=720,
        lookback_days=60,
        max_per_run=3,
        title_selector="h1, .title",
        content_selector="#main-content, .content",
        default_issuing_agency="UBND Quận Lê Chân",
        default_sector="HanhChinhCong",
        needs_js=True,

    ),
]


class SourceRegistry:
    """Manages crawl sources and their configurations."""

    _sources: dict[str, LegalSource] = {}

    @classmethod
    def initialize(cls) -> None:
        """Load default sources if not already loaded."""
        if cls._sources:
            return
        for source in DEFAULT_SOURCES:
            cls._sources[source.source_id] = source
        logger.info(f"Initialized SourceRegistry with {len(cls._sources)} sources")

    @classmethod
    def get_all(cls) -> list[LegalSource]:
        cls.initialize()
        return list(cls._sources.values())

    @classmethod
    def get(cls, source_id: str) -> LegalSource | None:
        cls.initialize()
        return cls._sources.get(source_id)

    @classmethod
    def get_by_type(cls, source_type: SourceType) -> list[LegalSource]:
        cls.initialize()
        return [s for s in cls._sources.values() if s.source_type == source_type]

    @classmethod
    def get_enabled(cls) -> list[LegalSource]:
        cls.initialize()
        return [s for s in cls._sources.values() if s.enabled]

    @classmethod
    def get_due(cls) -> list[LegalSource]:
        """Get sources that are due for crawling."""
        cls.initialize()
        now = datetime.now(timezone.utc)
        due = []
        for s in cls._sources.values():
            if not s.enabled:
                continue
            if s.last_crawl_at is None:
                due.append(s)
                continue
            last = datetime.fromisoformat(s.last_crawl_at)
            if now - last > timedelta(minutes=s.interval_minutes):
                due.append(s)
        return due

    @classmethod
    def update_status(cls, source_id: str, status: str, count: int) -> None:
        source = cls._sources.get(source_id)
        if source:
            source.last_crawl_at = datetime.now(timezone.utc).isoformat()
            source.last_crawl_status = status
            source.last_crawl_count = count
            source.total_crawled += count
            source.updated_at = datetime.now(timezone.utc).isoformat()
