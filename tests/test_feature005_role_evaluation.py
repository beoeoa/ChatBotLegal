from argparse import Namespace
from pathlib import Path

from scripts.evaluate_feature005_role_matrix import (
    _public_evaluation,
    _report_outputs,
    score_response,
)


def test_role_evaluator_can_omit_private_artifact():
    args = Namespace(
        summary=Path("summary.json"),
        private_report=Path("private.json"),
        no_private_report=True,
    )

    assert _report_outputs(args, {"sensitive": True}, {"safe": True}) == [
        (Path("summary.json"), {"safe": True})
    ]


def test_score_response_requires_coverage_role_fit_and_claim_citations():
    case = {
        "role": "citizen",
        "required_facets": ["documents", "deadline"],
        "requests_form": False,
    }
    payload = {
        "answer": "Hồ sơ cần chuẩn bị và thời gian giải quyết đã được xác minh.",
        "answer_sections": [
            {
                "title": "Hồ sơ, giấy tờ",
                "status": "sufficiently_evidenced",
                "answer": "Hồ sơ cần chuẩn bị: Tờ khai.",
                "citations": [{"law_number": "60/2014/QH13"}],
            },
            {
                "title": "Thời hạn",
                "status": "sufficiently_evidenced",
                "answer": "Thời gian giải quyết: trong ngày.",
                "citations": [{"law_number": "60/2014/QH13"}],
            },
        ],
    }

    result = score_response(case, payload)

    assert result["pass"] is True
    assert result["coverage_ratio"] == 1.0
    assert result["claim_source_ok"] is True


def test_public_role_summary_omits_expected_citation_details():
    evaluation = {
        "pass": False,
        "critical_failure": True,
        "clarification_ok": False,
        "missing_expected_citations": ["60/2014/QH13#16"],
        "root_causes": ["model"],
    }

    public = _public_evaluation(evaluation)

    assert public["clarification_ok"] is False
    assert "missing_expected_citations" not in public


def test_score_response_flags_invalid_form_and_internal_marker_as_critical():
    case = {
        "role": "admin",
        "required_facets": ["form"],
        "requests_form": True,
    }
    payload = {
        "answer": "Dùng biểu mẫu [legal:123].",
        "answer_sections": [],
        "recommended_forms": [
            {
                "official_level": "reference",
                "review_status": "candidate_pending_review",
                "has_official_file": False,
            }
        ],
    }

    result = score_response(case, payload)

    assert result["critical_failure"] is True
    assert "renderer" in result["root_causes"]
    assert "form catalog" in result["root_causes"]


def test_score_response_treats_explicit_unavailable_facet_as_contract_handled():
    case = {
        "role": "citizen",
        "required_facets": ["documents", "form"],
        "requests_form": True,
    }
    payload = {
        "answer": "Hồ sơ đã được xác minh; biểu mẫu chưa có nguồn chính thức.",
        "answer_sections": [
            {
                "title": "Hồ sơ, giấy tờ",
                "status": "sufficiently_evidenced",
                "answer": "Hồ sơ cần chuẩn bị: Tờ khai.",
                "citations": [{"law_number": "60/2014/QH13"}],
            },
            {
                "title": "Biểu mẫu",
                "status": "insufficiently_evidenced",
                "answer": "",
                "limitation": "Chưa có tệp chính thức đã duyệt.",
                "citations": [],
            },
        ],
        "forms_unavailable": True,
    }

    result = score_response(case, payload)

    assert result["pass"] is True
    assert result["coverage_ratio"] == 1.0
    assert result["unavailable_facets"] == ["form"]


def test_score_response_fails_when_required_official_form_is_unavailable():
    case = {
        "role": "citizen",
        "required_facets": ["documents", "form"],
        "requests_form": True,
        "requires_official_form": True,
    }
    payload = {
        "answer": "Hồ sơ đã được xác minh; biểu mẫu chưa có nguồn chính thức.",
        "answer_sections": [
            {
                "title": "Hồ sơ, giấy tờ",
                "status": "sufficiently_evidenced",
                "answer": "Hồ sơ cần chuẩn bị: Tờ khai.",
                "citations": [{"law_number": "60/2014/QH13"}],
            },
            {
                "title": "Biểu mẫu",
                "status": "insufficiently_evidenced",
                "answer": "",
                "limitation": "Chưa có tệp chính thức đã duyệt.",
                "citations": [],
            },
        ],
        "forms_unavailable": True,
    }

    result = score_response(case, payload)

    assert result["pass"] is False
    assert result["critical_failure"] is True
    assert result["form_status"] == "missing"
    assert result["missing_facets"] == ["form"]
    assert "form catalog" in result["root_causes"]


def test_score_response_accepts_an_expected_verified_form_gap():
    case = {
        "role": "citizen",
        "required_facets": ["form"],
        "requests_form": True,
        "requires_official_form": True,
        "allows_verified_form_gap": True,
    }
    payload = {
        "answer": (
            "Việc cần làm: biểu mẫu đã hỏi chưa có bản chính thức còn hiệu lực."
        ),
        "answer_sections": [
            {
                "title": "Biểu mẫu",
                "status": "insufficiently_evidenced",
                "answer": "",
                "limitation": "Khoảng trống dữ liệu đã được xác minh.",
                "citations": [],
            },
        ],
        "forms_unavailable": True,
    }

    result = score_response(case, payload)

    assert result["pass"] is True
    assert result["critical_failure"] is False
    assert result["form_status"] == "verified_gap"
    assert result["coverage_ratio"] == 1.0


def test_score_response_reads_role_presentation_from_validated_sections():
    case = {
        "role": "officer",
        "required_facets": ["authority"],
        "requests_form": False,
    }
    payload = {
        "answer": "Nội dung đã được kiểm chứng.",
        "answer_sections": [
            {
                "title": "Thẩm quyền",
                "status": "sufficiently_evidenced",
                "answer": "Kết luận chuyên môn: Ủy ban nhân dân có thẩm quyền.",
                "citations": [{"law_number": "60/2014/QH13"}],
            }
        ],
    }

    result = score_response(case, payload)

    assert result["role_ok"] is True
    assert result["pass"] is True


def test_score_response_counts_verified_form_catalog_item_as_form_coverage():
    case = {
        "role": "citizen",
        "required_facets": ["form"],
        "requests_form": True,
    }
    payload = {
        "answer": "Việc cần làm: tải tờ khai chính thức.",
        "answer_sections": [],
        "recommended_forms": [
            {
                "official_level": "official",
                "review_status": "approved",
                "has_official_file": True,
                "download_url": "/api/forms/official/download",
                "procedure_id": "procedure-1",
            }
        ],
    }

    result = score_response(case, payload)

    assert result["covered_facets"] == ["form"]
    assert result["coverage_ratio"] == 1.0


def test_score_response_rejects_foreign_representation_source_for_domestic_case():
    case = {
        "role": "citizen",
        "question": "Đăng ký khai sinh tại Hải Phòng cần bao lâu?",
        "required_facets": ["deadline"],
        "requests_form": False,
    }
    payload = {
        "answer": "Thời gian giải quyết: 02 ngày làm việc.",
        "answer_sections": [
            {
                "title": "Thời hạn",
                "status": "sufficiently_evidenced",
                "answer": "Thời gian giải quyết: 02 ngày làm việc.",
                "citations": [
                    {
                        "document_title": (
                            "Hướng dẫn đăng ký hộ tịch tại Cơ quan đại diện "
                            "ngoại giao Việt Nam ở nước ngoài"
                        )
                    }
                ],
            }
        ],
    }

    result = score_response(case, payload)

    assert result["critical_failure"] is True
    assert result["pass"] is False
    assert "retrieval" in result["root_causes"]


def test_score_response_rejects_marriage_source_for_birth_authority():
    case = {
        "role": "officer",
        "question": "Thẩm quyền đăng ký khai sinh theo Điều 35 Luật Hộ tịch?",
        "required_facets": ["authority"],
        "requests_form": False,
    }
    payload = {
        "answer": "Thẩm quyền đã được xác định.",
        "answer_sections": [
            {
                "title": "Thẩm quyền",
                "status": "sufficiently_evidenced",
                "answer": "Thẩm quyền: người đứng đầu Cơ quan đại diện.",
                "citations": [
                    {
                        "document_title": (
                            "Quy định chi tiết Luật Hôn nhân và gia đình"
                        )
                    }
                ],
            }
        ],
    }

    result = score_response(case, payload)

    assert result["critical_failure"] is True
    assert result["pass"] is False
    assert "retrieval" in result["root_causes"]


def test_score_response_fails_when_reviewed_direct_source_is_missing():
    case = {
        "role": "officer",
        "question": "Khi nào xử phạt không lập biên bản?",
        "required_facets": ["condition"],
        "requests_form": False,
        "required_citations_all": [
            {"law_number": "15/2012/QH13", "article_number": "56"}
        ],
    }
    payload = {
        "answer": "Đã nêu quy trình khiếu nại.",
        "answer_sections": [
            {
                "title": "Điều kiện",
                "status": "sufficiently_evidenced",
                "answer": "Người khiếu nại thực hiện quyền khiếu nại.",
                "citations": [
                    {"law_number": "02/2011/QH13", "article_number": "7"}
                ],
            }
        ],
        "citations": [{"law_number": "02/2011/QH13", "article_number": "7"}],
    }

    result = score_response(case, payload)

    assert result["critical_failure"] is True
    assert result["pass"] is False
    assert result["missing_expected_citations"] == ["15/2012/QH13#56"]
    assert "retrieval" in result["root_causes"]


def test_score_response_requires_clarification_for_ambiguous_benefit():
    case = {
        "role": "admin",
        "required_facets": ["documents"],
        "requests_form": False,
        "requires_clarification": True,
    }
    wrong_payload = {
        "answer": "Hồ sơ hỗ trợ giáo dục gồm Mẫu 20.",
        "answer_sections": [
            {
                "title": "Hồ sơ",
                "status": "sufficiently_evidenced",
                "answer": "Hồ sơ hỗ trợ giáo dục gồm Mẫu 20.",
                "citations": [{"law_number": "131/2021/NĐ-CP"}],
            }
        ],
        "rag_trace": {
            "evidence_coverage": {},
            "form_provenance": {},
        },
    }
    safe_payload = {
        "answer": "Cần làm rõ loại trợ cấp.",
        "answer_sections": [
            {
                "title": "Nội dung cần làm rõ",
                "status": "insufficiently_evidenced",
                "answer": None,
                "citations": [],
                "clarifying_question": "Bạn cần loại trợ cấp người có công nào?",
            }
        ],
        "rag_trace": {
            "evidence_coverage": {},
            "form_provenance": {},
        },
    }

    wrong = score_response(case, wrong_payload)
    safe = score_response(case, safe_payload)

    assert wrong["critical_failure"] is True
    assert wrong["pass"] is False
    assert safe["clarification_ok"] is True
