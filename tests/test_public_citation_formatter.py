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


def test_public_citation_maps_runtime_document_status_to_contract_field():
    public = format_public_citation(
        {
            "document_title": "Luật Hộ tịch",
            "law_number": "60/2014/QH13",
            "article_number": "16",
            "document_status": "active",
            "source_url": "https://vbpl.vn/60-2014",
        }
    )

    assert public["effective_status"] == "active"


def test_public_citation_exposes_only_safe_validity_sync_fields():
    public = format_public_citation(
        {
            "document_title": "Luật Đất đai",
            "law_number": "31/2024/QH15",
            "validity_sync": {
                "status": "active",
                "serving_action": "allow",
                "verified_at": "2026-08-08T01:00:00Z",
                "source_url": "https://vbpl.vn/van-ban/example",
                "effective_from": "2024-08-01",
                "effective_to": None,
                "warning_code": None,
                "actor_user_id": "admin-secret",
                "event_id": "legal_validity_event:secret",
                "decision_reason": "private audit reason",
            },
        }
    )

    assert public["validity_sync"] == {
        "status": "active",
        "serving_action": "allow",
        "verified_at": "2026-08-08T01:00:00Z",
        "source_url": "https://vbpl.vn/van-ban/example",
        "effective_from": "2024-08-01",
        "effective_to": None,
        "warning_code": None,
    }
    assert "admin-secret" not in str(public)
    assert "private audit reason" not in str(public)


def test_router_citation_sanitizes_validity_sync_admin_data():
    from api.routers.search import _build_citations_from_retrieval

    citations = _build_citations_from_retrieval(
        [
            {
                "chunk_id": "active-article",
                "doc_id": "20",
                "law_number": "60/2014/QH13",
                "article_number": "13",
                "document_status": "active",
                "validity_sync": {
                    "status": "active",
                    "serving_action": "allow",
                    "verified_at": "2026-08-08T01:00:00Z",
                    "source_url": "https://vbpl.vn/van-ban/example",
                    "warning_code": "validity_snapshot_stale",
                    "event_id": "legal_validity_event:secret",
                    "actor_user_id": "admin-secret",
                },
            }
        ]
    )

    assert citations[0]["validity_sync"]["status"] == "active"
    assert citations[0]["validity_sync"]["warning_code"] == "validity_snapshot_stale"
    assert "event_id" not in citations[0]["validity_sync"]
    assert "actor_user_id" not in citations[0]["validity_sync"]


def test_public_citation_exposes_only_safe_authority_label_fields():
    public = format_public_citation(
        {
            "document_title": "Luật Đất đai",
            "law_number": "31/2024/QH15",
            "authority_level": "national_assembly",
            "authority_label": "Luật, bộ luật hoặc nghị quyết của Quốc hội",
            "authority_rank": 130,
            "authority_reason_code": "AUTHORITY_NATIONAL_ASSEMBLY_LAW_EXACT",
            "hierarchy_position": 1,
            "hierarchy_rule": "authority_rank",
            "score": 0.91,
        }
    )

    assert public["authority_level"] == "national_assembly"
    assert public["authority_label"] == "Luật, bộ luật hoặc nghị quyết của Quốc hội"
    assert "authority_rank" not in public
    assert "authority_reason_code" not in public
    assert "hierarchy_position" not in public
    assert "hierarchy_rule" not in public
    assert "score" not in public


def test_router_citation_builds_public_authority_projection_from_retrieval():
    from api.routers.search import _build_citations_from_retrieval

    citations = _build_citations_from_retrieval(
        [
            {
                "chunk_id": "law-article",
                "doc_id": "31",
                "law_number": "31/2024/QH15",
                "document_title": "Luật Đất đai",
                "article_number": "137",
                "document_status": "active",
                "authority_level": "national_assembly",
                "authority_label": "Luật, bộ luật hoặc nghị quyết của Quốc hội",
                "authority_rank": 130,
                "authority_reason_code": "AUTHORITY_NATIONAL_ASSEMBLY_LAW_EXACT",
            }
        ]
    )

    assert citations[0]["authority_level"] == "national_assembly"
    assert citations[0]["authority_label"].startswith("Luật")
    assert "authority_rank" not in citations[0]
    assert "authority_reason_code" not in citations[0]
