from __future__ import annotations

from datetime import date

from api.legal_claim_validation import validate_structured_claims
from api.legal_evidence_relevance import rank_issue_evidence
from api.legal_official_procedure_evidence import (
    build_official_procedure_evidence,
)
from api.legal_section_grounding import LegalIssue, select_eligible_evidence
from api.legal_structured_answer import (
    build_compact_evidence_context,
    build_extractive_structured_answer,
    build_issue_coverage_matrix,
    render_structured_answer,
)


def _issue(issue_id: str, intent: str, query: str, domain: str) -> LegalIssue:
    return LegalIssue(
        request_id="request-test",
        issue_id=issue_id,
        title=query,
        query_text=query,
        text=query,
        intent=intent,  # type: ignore[arg-type]
        domain=domain,
        subject=query,
    )


def test_birth_procedure_evidence_isolated_by_facet() -> None:
    issues = [
        _issue("issue-docs", "documents", "hồ sơ đăng ký khai sinh", "ho_tich_chung_thuc"),
        _issue("issue-auth", "authority", "nơi nộp đăng ký khai sinh", "ho_tich_chung_thuc"),
    ]

    rows, trace = build_official_procedure_evidence(
        issues=issues,
        question="Cần giấy tờ gì và nộp ở đâu để đăng ký khai sinh?",
        legal_as_of="2026-08-08",
    )

    assert trace["matched_procedure_codes"] == ["1.001193"]
    docs = next(row for row in rows if row["issue_id"] == "issue-docs")
    authority = next(row for row in rows if row["issue_id"] == "issue-auth")
    assert docs["supported_facets"] == ["documents"]
    assert "Giấy chứng sinh" in docs["content"]
    assert authority["supported_facets"] == ["authority"]
    assert "Ủy ban nhân dân cấp xã" in authority["content"]
    assert "Giấy chứng sinh" not in authority["content"]


def test_birth_procedure_projection_repairs_only_reviewed_snapshot_damage() -> None:
    issue = _issue(
        "issue-procedure",
        "procedure",
        "trình tự đăng ký khai sinh",
        "ho_tich_chung_thuc",
    )

    rows, _ = build_official_procedure_evidence(
        issues=[issue],
        question="Đăng ký khai sinh được thực hiện theo trình tự nào?",
        legal_as_of="2026-08-09",
    )

    assert len(rows) == 1
    content = rows[0]["content"]
    assert "đăng ký khai sinh tại Trung tâm Phục vụ hành chính công" in content
    assert "hoàn tất việc nộp hồ sơ" in content
    assert "đăng ký khai tâm" not in content
    assert "việc sinh tại Trung nộp hồ sơ" not in content


def test_coverage_honours_explicit_supported_facets() -> None:
    issue = _issue("issue-auth", "authority", "nơi nộp đăng ký khai sinh", "ho_tich_chung_thuc")
    dossier_row = {
        "request_id": "request-test",
        "issue_id": "issue-auth",
        "content": "Nộp tờ khai và Giấy chứng sinh cho Ủy ban nhân dân cấp xã.",
        "supported_facets": ["documents"],
    }

    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id={"evidence-1": dossier_row},
        required_facets_by_issue={"issue-auth": ["authority"]},
    )

    assert matrix["issue-auth"][0]["evidence_available"] is False
    assert matrix["issue-auth"][0]["evidence_ids"] == []


def test_claim_validator_rejects_cross_facet_claim() -> None:
    evidence = {
        "request_id": "request-test",
        "issue_id": "issue-auth",
        "content": "Ủy ban nhân dân cấp xã tiếp nhận hồ sơ đăng ký khai sinh.",
        "supported_facets": ["documents"],
    }
    result = validate_structured_claims(
        request_id="request-test",
        claims=[
            {
                "issue_id": "issue-auth",
                "claim_type": "authority",
                "claim_text": "Ủy ban nhân dân cấp xã tiếp nhận hồ sơ.",
                "evidence_id": "evidence-1",
                "support_quote": "Ủy ban nhân dân cấp xã tiếp nhận hồ sơ đăng ký khai sinh.",
            }
        ],
        evidence_by_id={"evidence-1": evidence},
    )

    assert not result.accepted
    assert result.rejected[0].reason == "claim_type_not_supported_by_evidence_facet"


def test_current_building_evidence_uses_decree_217_only() -> None:
    issues = [
        _issue(
            "issue-docs",
            "documents",
            "hồ sơ cấp giấy phép xây dựng nhà ở riêng lẻ",
            "dat_dai_xay_dung",
        ),
        _issue(
            "issue-deadline",
            "deadline",
            "thời hạn cấp giấy phép xây dựng nhà ở riêng lẻ",
            "dat_dai_xay_dung",
        ),
        _issue(
            "issue-form",
            "form",
            "Mẫu số 01 đơn đề nghị cấp giấy phép xây dựng mới",
            "dat_dai_xay_dung",
        ),
    ]

    rows, trace = build_official_procedure_evidence(
        issues=issues,
        question="Xin giấy phép xây dựng nhà ở riêng lẻ và tải Mẫu số 01.",
        legal_as_of="2026-08-08",
    )

    assert trace["reviewed_source_ids"] == ["217/2026/NĐ-CP"]
    assert rows
    assert all(row["law_number"] == "217/2026/NĐ-CP" for row in rows)
    assert all("175/2024/NĐ-CP" not in row["content"] for row in rows)
    deadline = next(row for row in rows if row["supported_facets"] == ["deadline"])
    assert "07 ngày làm việc" in deadline["content"]
    form = next(row for row in rows if row["supported_facets"] == ["form"])
    assert form["form_download_url"].endswith("/pl217.pdf")


def test_decree_217_overlay_is_not_used_before_effective_date() -> None:
    issue = _issue(
        "issue-docs",
        "documents",
        "hồ sơ cấp giấy phép xây dựng nhà ở riêng lẻ",
        "dat_dai_xay_dung",
    )
    rows, _ = build_official_procedure_evidence(
        issues=[issue],
        question="Xin giấy phép xây dựng nhà ở riêng lẻ.",
        legal_as_of=date(2026, 6, 30).isoformat(),
    )
    assert rows == []


def test_temporary_residence_general_form_survives_subject_and_authority_gates() -> None:
    issues = [
        _issue("issue-docs", "documents", "hồ sơ đăng ký tạm trú", "cu_tru_an_ninh"),
        _issue("issue-auth", "authority", "nơi nộp đăng ký tạm trú", "cu_tru_an_ninh"),
    ]
    rows, _ = build_official_procedure_evidence(
        issues=issues,
        question="Đăng ký tạm trú cần hồ sơ gì và nộp ở đâu?",
        legal_as_of="2026-08-08",
    )

    for issue in issues:
        candidates = [row for row in rows if row["issue_id"] == issue.issue_id]
        decisions = select_eligible_evidence(
            request_id="request-test",
            issue=issue,
            candidates=candidates,
            legal_as_of="2026-08-08",
        )
        eligible = [dict(item.source_metadata) for item in decisions if item.eligible]
        ranked, _ = rank_issue_evidence(
            f"{issue.title} {issue.query_text}",
            eligible,
            issue_intent=issue.intent,
        )
        assert ranked


def _render_official_fallback(
    issues: list[LegalIssue], question: str
) -> list[object]:
    rows, _ = build_official_procedure_evidence(
        issues=issues,
        question=question,
        legal_as_of="2026-08-09",
    )
    _context, evidence = build_compact_evidence_context(
        issues=issues,
        evidence_rows=rows,
        max_chars=12_000,
    )
    matrix = build_issue_coverage_matrix(
        issues=issues,
        evidence_by_id=evidence,
        required_facets_by_issue={
            issue.issue_id: [issue.intent] for issue in issues
        },
    )
    output = build_extractive_structured_answer(
        issues=issues,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    sections, _aggregate, _trace = render_structured_answer(
        request_id="request-test",
        issues=issues,
        output=output,
        evidence_by_id=evidence,
        role="citizen",
        coverage_matrix=matrix,
    )
    return sections


def test_current_building_fallback_covers_all_requested_facets() -> None:
    question = (
        "Xin giấy phép xây dựng nhà ở riêng lẻ: điều kiện, hồ sơ, nộp ở đâu, "
        "thời hạn và biểu mẫu?"
    )
    intents = ("condition", "documents", "authority", "deadline", "form")
    issues = [
        _issue(
            f"issue-{index}",
            intent,
            f"{intent} giấy phép xây dựng nhà ở riêng lẻ",
            "dat_dai_xay_dung",
        )
        for index, intent in enumerate(intents, start=1)
    ]

    sections = _render_official_fallback(issues, question)
    answers = {section.issue_id: section.answer or "" for section in sections}

    assert all(
        section.status == "sufficiently_evidenced" for section in sections
    )
    assert "mục đích sử dụng đất" in answers["issue-1"]
    assert "bản vẽ thiết kế xây dựng" in answers["issue-2"]
    assert "Ủy ban nhân dân cấp xã" in answers["issue-3"]
    assert "07 ngày làm việc" in answers["issue-4"]
    assert "Mẫu số 01" in answers["issue-5"]
    assert "Điều kiện cấp giấy phép xây dựng mới." not in answers["issue-1"]


def test_birth_fallback_keeps_dossier_out_of_authority_section() -> None:
    question = "Đăng ký khai sinh cần giấy tờ gì và nộp ở đâu?"
    issues = [
        _issue(
            "issue-documents",
            "documents",
            "hồ sơ đăng ký khai sinh",
            "ho_tich_chung_thuc",
        ),
        _issue(
            "issue-authority",
            "authority",
            "nơi nộp đăng ký khai sinh",
            "ho_tich_chung_thuc",
        ),
    ]

    sections = _render_official_fallback(issues, question)
    answers = {section.issue_id: section.answer or "" for section in sections}

    assert all(
        section.status == "sufficiently_evidenced" for section in sections
    )
    assert "Giấy chứng sinh" in answers["issue-documents"]
    assert "Ủy ban nhân dân cấp xã" in answers["issue-authority"]
    assert "Giấy chứng sinh" not in answers["issue-authority"]


def test_temporary_residence_deadline_uses_processing_duration_not_reception_hours() -> None:
    issue = _issue(
        "issue-deadline",
        "deadline",
        "thời hạn đăng ký tạm trú",
        "cu_tru_an_ninh",
    )

    rows, _ = build_official_procedure_evidence(
        issues=[issue],
        question="Đăng ký tạm trú mất bao lâu?",
        legal_as_of="2026-08-09",
    )

    assert len(rows) == 1
    assert "3 ngày làm việc" in rows[0]["content"]
    assert "Thời gian tiếp nhận" not in rows[0]["content"]
    assert rows[0]["supported_facets"] == ["deadline"]


def test_social_pension_condition_fallback_keeps_all_material_age_branches() -> None:
    issue = _issue(
        "issue-condition",
        "condition",
        "điều kiện hưởng trợ cấp hưu trí xã hội",
        "an_sinh_y_te_giao_duc",
    )

    sections = _render_official_fallback(
        [issue], "Điều kiện hưởng trợ cấp hưu trí xã hội là gì?"
    )
    answer = sections[0].answer or ""

    assert "75 tuổi" in answer
    assert "70 tuổi" in answer
    assert "hộ nghèo" in answer
    assert "văn bản đề nghị" in answer


def test_release_confirmed_procedure_projects_all_required_snapshot_facets() -> None:
    issue = _issue(
        "issue-release-procedure",
        "form",
        "procedure code 1.115620",
        "dat_dai_xay_dung",
    )

    rows, trace = build_official_procedure_evidence(
        issues=[issue],
        question="Procedure code 1.115620 documents and authority",
        legal_as_of="2026-08-11",
        procedure_id="1.115620",
        required_facets_by_issue={issue.issue_id: ["documents", "authority"]},
    )

    assert trace["matched_procedure_codes"] == ["1.115620"]
    assert trace["identity_source"] == "release_confirmed_procedure_id"
    assert {tuple(row["supported_facets"]) for row in rows} == {
        ("documents",),
        ("authority",),
    }
    assert all(row["procedure_code"] == "1.115620" for row in rows)
    assert all(str(row["content"]).strip() for row in rows)


def test_release_confirmed_rule_uses_published_legal_basis_metadata() -> None:
    issue = _issue(
        "issue-release-rule",
        "rule",
        "procedure code 1.115620 legal basis",
        "dat_dai_xay_dung",
    )

    rows, _trace = build_official_procedure_evidence(
        issues=[issue],
        question="Procedure code 1.115620 legal basis",
        legal_as_of="2026-08-11",
        procedure_id="1.115620",
        required_facets_by_issue={issue.issue_id: ["rule"]},
    )

    assert len(rows) == 1
    assert rows[0]["supported_facets"] == ["rule"]
    assert "31/2024/QH15" in rows[0]["content"]
    assert "101/2024/N" in rows[0]["content"]


def test_reviewed_current_overlay_precedes_release_confirmed_snapshot() -> None:
    issue = _issue(
        "issue-building-documents",
        "documents",
        "h\u1ed3 s\u01a1 c\u1ea5p gi\u1ea5y ph\u00e9p x\u00e2y d\u1ef1ng nh\u00e0 \u1edf ri\u00eang l\u1ebb",
        "dat_dai_xay_dung",
    )

    rows, trace = build_official_procedure_evidence(
        issues=[issue],
        question="Xin gi\u1ea5y ph\u00e9p x\u00e2y d\u1ef1ng nh\u00e0 \u1edf ri\u00eang l\u1ebb.",
        legal_as_of="2026-08-11",
        procedure_id="1.115620",
        required_facets_by_issue={issue.issue_id: ["documents"]},
    )

    assert rows
    assert {row.get("source_type") for row in rows} == {
        "reviewed_current_legal_overlay"
    }
    assert len(trace["reviewed_source_ids"]) == 1
    assert str(trace["reviewed_source_ids"][0]).startswith("217/2026/")


def test_unconfirmed_generic_procedure_is_not_inferred_from_question() -> None:
    issue = _issue(
        "issue-unconfirmed-procedure",
        "documents",
        "procedure code 1.115620",
        "dat_dai_xay_dung",
    )

    rows, trace = build_official_procedure_evidence(
        issues=[issue],
        question="Procedure code 1.115620 documents",
        legal_as_of="2026-08-11",
    )

    assert rows == []
    assert trace["identity_source"] is None


def test_first_level_complaint_adapter_covers_each_requested_facet() -> None:
    question = (
        "Khiếu nại lần đầu quyết định hành chính cần điều kiện, hồ sơ, "
        "nộp ở đâu, thời hạn và mẫu đơn nào?"
    )
    intents = ("condition", "documents", "authority", "deadline", "form")
    issues = [
        _issue(
            f"issue-{intent}",
            intent,
            f"{intent} khiếu nại lần đầu quyết định hành chính",
            "khieu_nai_to_cao_xu_phat",
        )
        for intent in intents
    ]

    rows, trace = build_official_procedure_evidence(
        issues=issues,
        question=question,
        legal_as_of="2026-08-09",
    )
    sections = _render_official_fallback(issues, question)
    answers = {section.issue_id: section.answer or "" for section in sections}

    assert trace["reviewed_source_ids"] == ["02/2011/QH13", "124/2020/NĐ-CP"]
    assert len(rows) == 5
    for issue in issues:
        candidates = [row for row in rows if row["issue_id"] == issue.issue_id]
        decisions = select_eligible_evidence(
            request_id="request-test",
            issue=issue,
            candidates=candidates,
            legal_as_of="2026-08-09",
        )
        assert [decision.reason for decision in decisions] == [
            "eligible_current_issue_source"
        ]
    assert "căn cứ" in answers["issue-condition"].casefold()
    assert "ký tên hoặc điểm chỉ" in answers["issue-documents"]
    assert "người đã ra quyết định" in answers["issue-authority"]
    assert "30 ngày" in answers["issue-deadline"]
    assert "Mẫu số 01" in answers["issue-form"]
