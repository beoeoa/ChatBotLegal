"""Feature 005 public-citation regression requirements.

The formatter implementation belongs to T031.  This test is intentionally
written first so it must fail until the public-only projection is implemented.
"""

from api.legal_section_grounding import format_public_citation
from api.routers.search import _sanitize_answer_citation_display


def test_public_citation_formatter_never_exposes_internal_markers():
    public = format_public_citation(
        {
            "document_title": "Luật Đất đai",
            "law_number": "31/2024/QH15",
            "article_number": "137",
            "effective_status": "active",
            "source_url": "https://vbpl.vn/31-2024",
            "source_id": "legal:127345",
            "chunk_id": "chunk-201",
            "trace_id": "trace-1",
            "packet_id": "packet-1",
            "citation_marker": "#ref-source-127345",
        }
    )

    serialized = str(public)
    assert public["document_title"] == "Luật Đất đai"
    assert public["article_number"] == "137"
    assert all(marker not in serialized for marker in ("#ref-source", "legal:", "chunk-", "trace-", "packet-"))


def test_router_sanitizer_removes_residual_internal_marker_syntax():
    answer = _sanitize_answer_citation_display(
        "Nội dung #ref-source-12 chunk_id=chunk-12 trace_id=trace-1 packet_id=packet-1 legal:12"
    )

    assert all(marker not in answer for marker in ("#ref-source", "chunk_id", "trace_id", "packet_id", "legal:"))
