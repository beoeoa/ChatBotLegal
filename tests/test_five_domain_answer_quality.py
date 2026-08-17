from scripts.evaluate_five_domain_answer_quality import (
    BENCHMARK_DOMAINS,
    _VERIFIED_PROVISIONS,
    apply_cross_response_checks,
    build_benchmark_cases,
    build_expired_source_block_cases,
    build_quality_gate_cases,
    evaluate_response,
)


def _source(index: int) -> dict:
    return {
        "law_number": f"{index}/2026/NĐ-CP",
        "article_number": str(index),
        "source_url": f"https://vbpl.vn/source-{index}",
        "effective_status": "active",
    }


def _benchmark_inputs() -> tuple[dict, dict]:
    records = []
    retrieval_cases = []
    for domain_index, domain in enumerate(BENCHMARK_DOMAINS, start=1):
        for case_index in range(1, 26):
            case_id = f"d{domain_index}-{case_index:02d}"
            required_facts = [
                "thẩm quyền",
                "hồ sơ",
                *( ["thời hạn", "điều kiện", "ngoại lệ"] if case_index > 15 else []),
            ]
            for role in ("citizen", "officer"):
                records.append(
                    {
                        "review_id": f"{case_id}:{role}",
                        "case_id": case_id,
                        "domain": domain,
                        "role": role,
                        "citizen_question": (
                            f"Hồ sơ thủ tục {case_index} trong lĩnh vực {domain} gồm gì?"
                        ),
                        "officer_question": (
                            f"Xác định thẩm quyền, hồ sơ và căn cứ thủ tục {case_index} "
                            f"trong lĩnh vực {domain}."
                        ),
                        "legal_as_of": "2026-08-09",
                        "required_facts": required_facts,
                    }
                )
            retrieval_cases.append(
                {
                    "case_id": case_id,
                    "classification": "FOUND_AND_RETRIEVED",
                    "top5": [_source(domain_index * 100 + case_index)],
                }
            )
    return {"records": records}, {"cases": retrieval_cases}


def test_build_benchmark_cases_selects_30_source_backed_questions_per_domain():
    golden, retrieval = _benchmark_inputs()

    cases = build_benchmark_cases(golden, retrieval)

    assert len(cases) == 150
    for domain in BENCHMARK_DOMAINS:
        selected = [case for case in cases if case["domain"] == domain]
        assert len(selected) == 30
        assert [case["difficulty"] for case in selected].count("easy") == 10
        assert [case["difficulty"] for case in selected].count("medium") == 10
        assert [case["difficulty"] for case in selected].count("hard") == 10
        assert all(case["questions"]["citizen"] for case in selected)
        assert all(case["questions"]["officer"] for case in selected)
        assert all(case["expected_citations_any"] for case in selected)


def test_live_catalog_uses_30_distinct_reviewed_provisions_per_domain():
    for domain in BENCHMARK_DOMAINS:
        provisions = _VERIFIED_PROVISIONS[domain]
        assert len(provisions) == 30
        assert len(set(provisions)) == 30
    social = _VERIFIED_PROVISIONS["an_sinh_y_te_giao_duc"]
    assert ("96/2014/TT-BQP", "26") not in social
    assert ("41/2024/QH15", "4") not in social
    assert ("188/2025/NĐ-CP", "43") in social


def test_quality_gate_keeps_150_positive_cases_and_adds_expired_block():
    golden, retrieval = _benchmark_inputs()

    cases = build_quality_gate_cases(golden, retrieval)

    assert len(cases) == 151
    assert sum(case.get("expected_outcome") != "blocked_expired" for case in cases) == 150
    blocked = [case for case in cases if case.get("expected_outcome") == "blocked_expired"]
    assert len(blocked) == 1
    assert blocked[0]["case_id"] == "expired-block:96-2014-tt-bqp:26"
    assert blocked[0]["forbidden_citations"][0]["law_number"] == "96/2014/TT-BQP"


def _grounded_payload(answer: str) -> dict:
    citation = {
        "document_title": "Nghị định kiểm thử",
        "law_number": "101/2026/NĐ-CP",
        "article_number": "101",
        "effective_status": "active",
        "source_url": "https://vbpl.vn/source-101",
    }
    return {
        "answer": answer,
        "grounding_status": "fully_grounded",
        "answer_completeness": {
            "status": "complete",
            "coverage_ratio": 1.0,
            "source_unit_count": 3,
            "covered_unit_count": 3,
            "required_checks": ["coverage", "order", "practical_meaning"],
            "checks": {
                "coverage": True,
                "order": True,
                "practical_meaning": True,
            },
            "reason_codes": [],
        },
        "citations": [citation],
        "claim_validation": [
            {
                "claim_type": "citation",
                "status": "verified",
                "evidence_ids": ["chunk-101"],
            }
        ],
        "quality_flags": [],
        "answer_sections": [
            {
                "title": "Kết luận và căn cứ",
                "status": "sufficiently_evidenced",
                "answer": answer.split("\n", 1)[-1],
                "citations": [citation],
                "clarifying_question": None,
            }
        ],
    }


def _case() -> dict:
    return {
        "case_id": "case-1",
        "domain": BENCHMARK_DOMAINS[0],
        "difficulty": "easy",
        "questions": {
            "citizen": "Điều 101 quy định nội dung gì?",
            "officer": "Tóm lược Điều 101.",
        },
        "expected_citations_any": [
            {"law_number": "101/2026/NĐ-CP", "article_number": "101"}
        ],
        "legal_as_of": "2026-08-10",
    }


def test_evaluate_response_accepts_complete_grounded_readable_answer():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Người dân thực hiện theo Điều 101 và đối chiếu đúng "
        "phạm vi áp dụng ghi trong văn bản."
    )

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is True
    assert result["checks"]["no_forbidden_fallback"] is True
    assert result["checks"]["presentation_clear"] is True
    assert result["review_comment"].startswith("Đạt")


def test_evaluate_response_rejects_forbidden_fallback_and_repeated_labels():
    payload = _grounded_payload(
        "## Hồ sơ\n"
        "- Hồ sơ cần chuẩn bị: Tờ khai.\n"
        "- Hồ sơ cần chuẩn bị: Giấy chứng nhận.\n"
        "Chưa tìm thấy dữ liệu để tổng hợp."
    )

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["no_forbidden_fallback"] is False
    assert result["checks"]["no_repeated_labels"] is False
    assert "câu thoái lui" in result["review_comment"]


def test_evaluate_response_rejects_grounded_but_incomplete_answer():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Đây chỉ là một đoạn có nguồn nhưng chưa bao phủ toàn bộ điều luật."
    )
    payload["answer_completeness"] = {
        "status": "incomplete",
        "coverage_ratio": 0.25,
    }

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["fully_grounded"] is True
    assert result["checks"]["answer_complete"] is False


def test_evaluate_response_rejects_single_copied_fragment_even_if_marked_complete():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Đây là một đoạn nhỏ có căn cứ nhưng không đại diện cho toàn bộ "
        "các khoản và điểm của Điều 101; người dân cần kiểm tra đủ nội dung trước khi thực hiện."
    )
    payload["answer_completeness"].update(
        {
            "status": "complete",
            "coverage_ratio": 1.0,
            "source_unit_count": 4,
            "covered_unit_count": 1,
        }
    )

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["answer_complete"] is True
    assert result["checks"]["coverage_complete"] is False


def test_evaluate_response_rejects_complete_label_without_structural_unit_proof():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Câu trả lời không được coi là đầy đủ khi hệ thống chưa xác định "
        "được tổng số khoản hoặc điểm cần kiểm tra, dù tỷ lệ tự khai đang là một trăm phần trăm."
    )
    payload["answer_completeness"].update(
        {
            "status": "complete",
            "coverage_ratio": 1.0,
            "source_unit_count": 0,
            "covered_unit_count": 0,
        }
    )

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["coverage_complete"] is False


def test_evaluate_response_rejects_wrong_document_or_article():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Điều được viện dẫn có nội dung áp dụng thực tế cho người dân, "
        "nhưng bộ chấm phải đối chiếu chính xác cả số văn bản và số điều trước khi chấp nhận."
    )
    wrong = {**payload["citations"][0], "article_number": "999"}
    payload["citations"] = [wrong]
    payload["answer_sections"][0]["citations"] = [wrong]

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["correct_document_article"] is False


def test_evaluate_response_rejects_expired_source_in_positive_answer():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Nội dung có diễn giải thực tế nhưng nguồn đã hết hiệu lực thì "
        "không được dùng làm căn cứ trả lời hiện hành cho người dân."
    )
    expired = {
        **payload["citations"][0],
        "effective_status": "expired",
        "effective_to": "2026-01-01",
    }
    payload["citations"] = [expired]
    payload["answer_sections"][0]["citations"] = [expired]

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["no_expired_source"] is False


def test_evaluate_response_requires_order_and_practical_interpretation():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Câu trả lời có đủ độ dài và nguồn nhưng báo cáo máy xác nhận "
        "chưa giữ đúng thứ tự và chưa giải thích ý nghĩa áp dụng cho người dân."
    )
    payload["answer_completeness"]["checks"]["order"] = False
    payload["answer_completeness"]["checks"]["practical_meaning"] = False

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["order_correct"] is False
    assert result["checks"]["practical_interpretation"] is False


def test_evaluate_response_rejects_unverified_claim_and_unknown_fallback_label():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Nội dung này có vẻ đầy đủ nhưng vẫn phải bị loại nếu còn một "
        "nhận định chưa được nguồn xác minh hoặc dùng nhãn dự phòng không hợp lệ."
    )
    payload["claim_validation"][0]["status"] = "rejected"
    payload["quality_flags"] = ["model_fallback"]

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["no_fabricated_claim"] is False
    assert result["checks"]["fallback_label_correct"] is False


def test_evaluate_response_accepts_explicit_provider_fallback_label():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Nội dung được rút gọn từ nguồn đã xác minh và vẫn nêu rõ giới hạn áp dụng thực tế."
    )
    payload["quality_flags"] = ["provider_fallback", "verified_source_condensed"]
    payload["answer_mode"] = "verified_source_condensed"
    payload["error"] = {"code": "AI_PROVIDER_FALLBACK"}

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["checks"]["fallback_label_correct"] is True


def test_evaluate_response_rejects_provider_fallback_without_public_mode_metadata():
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Nội dung dự phòng không công bố chế độ và mã lỗi tương ứng."
    )
    payload["quality_flags"] = ["provider_fallback"]

    result = evaluate_response(
        case=_case(), role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["checks"]["fallback_label_correct"] is False


def test_expired_96_2014_case_passes_only_when_blocked_and_labeled():
    case = build_expired_source_block_cases()[0]
    payload = {
        "answer": (
            "## Trạng thái hiệu lực\n"
            "- Văn bản 96/2014/TT-BQP đã hết hiệu lực và không được sử dụng để trả lời hiện hành."
        ),
        "citations": [],
        "answer_sections": [],
        "quality_flags": ["expired_source_blocked"],
    }

    result = evaluate_response(
        case=case, role="citizen", payload=payload, http_status=200,
        elapsed_seconds=1.0, transport_error=None,
    )

    assert result["pass"] is True
    assert result["checks"]["expired_source_blocked"] is True
    assert result["checks"]["expired_status_labeled"] is True


def test_expired_96_2014_case_rejects_answer_that_serves_the_expired_article():
    case = build_expired_source_block_cases()[0]
    expired = dict(case["forbidden_citations"][0])
    payload = {
        "answer": (
            "## Nội dung Điều 26\n"
            "- Điều này được áp dụng để hướng dẫn hiện hành và đây là nội dung trả lời cho người dân."
        ),
        "citations": [expired],
        "answer_sections": [],
        "quality_flags": [],
    }

    result = evaluate_response(
        case=case, role="citizen", payload=payload, http_status=200,
        elapsed_seconds=1.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["expired_source_blocked"] is False
    assert result["checks"]["expired_status_labeled"] is False


def test_evaluate_response_requires_detail_proportional_to_difficulty():
    case = {**_case(), "difficulty": "hard"}
    payload = _grounded_payload(
        "## Kết luận và căn cứ\n"
        "- **Kết luận:** Nội dung này đúng nguồn nhưng chưa đủ chi tiết cho câu hỏi khó."
    )

    result = evaluate_response(
        case=case, role="citizen", payload=payload, http_status=200,
        elapsed_seconds=42.0, transport_error=None,
    )

    assert result["pass"] is False
    assert result["checks"]["difficulty_detail"] is False


def test_cross_response_check_rejects_identical_answers_for_distinct_questions():
    answer = "## Kết luận và căn cứ\n- **Kết luận:** " + ("Nội dung có căn cứ. " * 12)
    rows = []
    for index in (1, 2):
        result = evaluate_response(
            case=_case(), role="citizen", payload=_grounded_payload(answer),
            http_status=200, elapsed_seconds=42.0, transport_error=None,
        )
        rows.append(
            {
                "response_id": f"case-{index}:citizen",
                "case_id": f"case-{index}",
                "response": {"answer": answer},
                "evaluation": result,
            }
        )

    apply_cross_response_checks(rows)

    assert all(row["evaluation"]["pass"] is False for row in rows)
    assert all(
        row["evaluation"]["checks"]["unique_answer"] is False for row in rows
    )
