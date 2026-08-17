import json

from api.crawlers.legal_document_pipeline import (
    _discover_vbpl_detail_action_hash,
    _parse_vbpl_server_action_payload,
)


def test_discovers_current_vbpl_detail_server_action_without_hard_coding_hash():
    detail_hash = "0fb12b3561faa05adec51a82efb3e4f4f427f07b"
    javascript = (
        'var i=(0,l.$)("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),'
        f'c=(0,l.$)("{detail_hash}");'
        'let A=e=>query({queryKey:["documents","detail",e],'
        'queryFn:async()=>{if(!e)throw Error("required");return c(e)}});'
    )

    assert _discover_vbpl_detail_action_hash([javascript]) == detail_hash


def test_parses_vbpl_server_action_text_and_evidenced_metadata():
    html = (
        "<html><body><p>Số: 66/2026/QĐ-UBND</p>"
        "<p>Điều 1. Phạm vi điều chỉnh của văn bản và các dịch vụ giáo dục "
        "mầm non, giáo dục phổ thông, giáo dục thường xuyên công lập trên "
        "địa bàn thành phố Hải Phòng.</p></body></html>"
    )
    record = {
        "id": "1a38a430-90eb-11f1-856e-83c91c23259c",
        "title": "Quyết định số 66/2026/QĐ-UBND",
        "docNum": "66/2026/QĐ-UBND",
        "issueDate": "2026-08-04T00:00:00",
        "effFrom": "2026-08-16T00:00:00",
        "effTo": None,
        "agencyName": "UBND Thành phố Hải Phòng",
        "docType": {"name": "Quyết định"},
        "documentContent": {"content": "$2"},
    }
    payload = (
        '0:["$@1",[]]\n'
        f"2:T{len(html.encode('utf-8')):x},{html}\n"
        f"1:{json.dumps(record, ensure_ascii=False)}\n"
    )

    parsed = _parse_vbpl_server_action_payload(payload)

    assert parsed["html"] == html
    assert parsed["law_number"] == "66/2026/QĐ-UBND"
    assert parsed["issued_date"] == "2026-08-04"
    assert parsed["effective_date"] == "2026-08-16"
    assert parsed["issuing_agency"] == "UBND Thành phố Hải Phòng"
    assert parsed["document_type"] == "Quyết định"
