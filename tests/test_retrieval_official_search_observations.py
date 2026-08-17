import json

from scripts.record_retrieval_official_search_observations_v2 import build


def test_official_search_observations_are_evidence_only_and_cover_gap_laws(tmp_path):
    gaps = tmp_path / "gaps.json"
    packet = tmp_path / "packet.json"
    output = tmp_path / "report.json"
    gaps.write_text(
        json.dumps(
            {
                "references": [
                    {"law_number": "1/2025/QH15", "classification": "not_found_in_local_inventory", "inventory_match_count": 0},
                    {"law_number": "1/2025/QH15", "classification": "not_found_in_local_inventory", "inventory_match_count": 0},
                ]
            }
        ),
        encoding="utf-8",
    )
    packet.write_text(
        json.dumps(
            {
                "observations": [
                    {
                        "law_number": "1/2025/QH15",
                        "official_source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=1",
                        "evidence_kind": "primary_document_page",
                        "observation_note": "discovery only",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = build(gaps_path=gaps, input_path=packet, output=output)
    assert report["status"] == "EVIDENCE_ONLY"
    assert report["reference_count"] == 2
    assert report["approved_for_import"] is False
    assert report["legal_review_required"] is True
    assert report["records"][0]["reference_count"] == 2
