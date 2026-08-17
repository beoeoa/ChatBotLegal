from datetime import date

from api.form_router_v3 import resolve_forms, resolve_from_configured_release


def _manifest():
    return {"schema_version": "form-release-v1", "release_id": "r1", "procedures": [{"procedure_id": "dang_ky_cu_tru", "name": "Đăng ký thường trú", "domain": "cu_tru", "coverage_status": "released"}, {"procedure_id": "xoa_cu_tru", "name": "Xóa đăng ký thường trú", "domain": "cu_tru", "coverage_status": "released"}], "aliases": [{"procedure_id": "dang_ky_cu_tru", "alias": "làm thường trú", "alias_kind": "natural"}], "assets": [{"form_id": "ct01", "form_code": "CT01", "canonical_name": "Tờ khai thay đổi thông tin cư trú", "asset_kind": "file", "source_url": "https://vbpl.vn/ct01.pdf", "source_checksum": "a" * 64, "audiences": ["citizen"], "coverage_status": "released"}, {"form_id": "ct02", "form_code": "CT02", "canonical_name": "Tờ khai xác nhận chỗ ở", "asset_kind": "eform", "source_url": "https://dichvucong.gov.vn/ct02", "source_checksum": "b" * 64, "audiences": ["citizen"], "coverage_status": "released"}], "bindings": [{"binding_id": "b1", "procedure_id": "dang_ky_cu_tru", "form_id": "ct01", "requirement": "required", "audience": "citizen", "coverage_status": "released"}, {"binding_id": "b2", "procedure_id": "dang_ky_cu_tru", "form_id": "ct02", "requirement": "conditional", "condition": "Khi cần xác nhận chỗ ở hợp pháp", "audience": "citizen", "coverage_status": "released"}]}


def test_exact_alias_returns_complete_form_set_and_condition():
    result = resolve_forms(question="Tôi muốn làm thường trú", manifest=_manifest(), legal_as_of=date(2026, 8, 11))
    assert result["status"] == "resolved"
    assert [x["form_id"] for x in result["recommended_forms"]] == ["ct01", "ct02"]
    assert result["recommended_forms"][1]["condition"]
    assert result["recommended_forms"][1]["asset_kind"] == "eform"
    assert result["evidence_packet"]["provider_may_modify_form_identity"] is False
    assert result["evidence_packet"]["procedure"]["procedure_id"] == "dang_ky_cu_tru"
    assert {item["form_id"] for item in result["evidence_packet"]["forms"]} == {"ct01", "ct02"}
    assert all(item["has_official_resource"] is True for item in result["recommended_forms"])


def test_ambiguous_question_returns_clarification_without_forms():
    result = resolve_forms(question="thường trú", manifest=_manifest())
    assert result["status"] == "clarification_required"
    assert result["recommended_forms"] == []


def test_expired_or_bad_checksum_form_is_fail_closed():
    manifest = _manifest(); manifest["assets"][0]["source_checksum"] = "bad"; manifest["assets"][1]["effective_to"] = "2025-01-01"
    result = resolve_forms(question="làm thường trú", manifest=manifest, legal_as_of=date(2026, 8, 11))
    assert result["status"] == "source_gap"
    assert result["recommended_forms"] == []


def test_one_invalid_required_form_blocks_partial_form_set():
    manifest = _manifest(); manifest["assets"][0]["source_checksum"] = "bad"
    result = resolve_forms(
        question="làm thường trú",
        manifest=manifest,
        legal_as_of=date(2026, 8, 11),
    )
    assert result["status"] == "source_gap"
    assert result["recommended_forms"] == []


def test_generic_form_code_requires_procedure_context():
    result = resolve_forms(question="Cho tôi Mẫu 01", manifest=_manifest())
    assert result["status"] == "clarification_required"
    assert result["reason"] == "FORM_CODE_REQUIRES_PROCEDURE"
    assert result["recommended_forms"] == []


def test_form_code_with_exact_procedure_context_resolves_backend_binding():
    result = resolve_forms(
        question="Mẫu 01 cho làm thường trú",
        manifest=_manifest(),
    )
    assert result["status"] == "resolved"
    assert result["procedure_id"] == "dang_ky_cu_tru"
    assert [item["form_id"] for item in result["recommended_forms"]] == ["ct01", "ct02"]


def test_verified_gap_never_creates_a_download_link():
    manifest = _manifest()
    manifest["procedures"].append({
        "procedure_id": "proc_no_form",
        "name": "Thủ tục không có mẫu",
        "domain": "cu_tru_an_ninh",
        "coverage_status": "verified_gap",
    })
    manifest["aliases"].append({
        "procedure_id": "proc_no_form",
        "alias": "làm thủ tục không có mẫu",
        "alias_kind": "natural",
    })
    result = resolve_forms(question="làm thủ tục không có mẫu", manifest=manifest)
    assert result["status"] == "source_gap"
    assert result["reason"] == "FORM_VERIFIED_GAP"
    assert result["recommended_forms"] == []


def test_owner_deferred_never_creates_a_download_link_or_verified_gap_label():
    manifest = _manifest()
    manifest["procedures"].append({
        "procedure_id": "proc_deferred",
        "name": "Thủ tục tạm bỏ qua",
        "domain": "cu_tru_an_ninh",
        "coverage_status": "owner_deferred",
    })
    manifest["aliases"].append({
        "procedure_id": "proc_deferred",
        "alias": "làm thủ tục tạm bỏ qua",
        "alias_kind": "natural",
    })
    result = resolve_forms(question="làm thủ tục tạm bỏ qua", manifest=manifest)
    assert result["status"] == "source_gap"
    assert result["reason"] == "FORM_OWNER_DEFERRED"
    assert result["reason"] != "FORM_VERIFIED_GAP"
    assert result["recommended_forms"] == []


def test_longer_specific_procedure_name_wins_over_contained_short_name():
    manifest = _manifest()
    manifest["procedures"][0]["name"] = "Đăng ký khai sinh có yếu tố nước ngoài"
    manifest["aliases"][0]["alias"] = "Đăng ký khai sinh có yếu tố nước ngoài"
    manifest["procedures"].append({
        "procedure_id": "p2",
        "name": "Đăng ký khai sinh có yếu tố nước ngoài tại khu vực biên giới",
        "domain": "ho_tich_chung_thuc",
        "official_source_url": "https://dichvucong.gov.vn/p2",
        "coverage_status": "verified_gap",
    })
    manifest["aliases"].append({
        "procedure_id": "p2",
        "alias": "Đăng ký khai sinh có yếu tố nước ngoài tại khu vực biên giới",
        "alias_kind": "natural",
    })

    result = resolve_forms(
        question="Tôi làm đăng ký khai sinh có yếu tố nước ngoài tại khu vực biên giới",
        manifest=manifest,
    )

    assert result["status"] == "source_gap"
    assert result["procedure_id"] == "p2"
    assert result["reason"] == "FORM_VERIFIED_GAP"


def test_packaged_asset_uses_internal_checksum_bound_download_endpoint():
    manifest = _manifest()
    manifest["assets"][0].update({
        "runtime_path": "feature017/r1/ct01.pdf",
        "download_url": "/api/procedures/forms-catalog/assets/ct01/download",
        "file_format": "pdf",
    })

    result = resolve_forms(question="làm thường trú", manifest=manifest)
    form = next(item for item in result["recommended_forms"] if item["form_id"] == "ct01")
    evidence = next(item for item in result["evidence_packet"]["forms"] if item["form_id"] == "ct01")

    assert form["download_url"] == "/api/procedures/forms-catalog/assets/ct01/download"
    assert evidence["download_url"] == form["download_url"]


def test_shadow_reads_explicit_validated_release_without_active_pointer(monkeypatch):
    class Repository:
        def active_release(self): return None
        def get_release(self, release_id):
            return {"status": "validated", "manifest": _manifest()} if release_id == "r1" else None

    service = type("Service", (), {"repository": Repository()})()
    monkeypatch.setenv("FORM_GOVERNANCE_ROUTER_MODE", "shadow")
    monkeypatch.setenv("FORM_GOVERNANCE_SHADOW_RELEASE_ID", "r1")
    monkeypatch.setattr(
        "api.form_governance_service.get_form_governance_service",
        lambda: service,
    )
    result = resolve_from_configured_release(
        question="làm thường trú",
        audience="citizen",
        legal_as_of=date(2026, 8, 11),
    )
    assert result and result["router_mode"] == "shadow"
    assert result["procedure_id"] == "dang_ky_cu_tru"


def test_active_rollout_can_be_limited_to_citizen(monkeypatch):
    service = type(
        "Service",
        (),
        {"repository": type("Repository", (), {"active_release": lambda self: {"manifest": _manifest()}})()},
    )()
    monkeypatch.setenv("FORM_GOVERNANCE_ROUTER_MODE", "active")
    monkeypatch.setenv("FORM_GOVERNANCE_ROLLOUT_ROLES", "citizen")
    monkeypatch.setattr(
        "api.form_governance_service.get_form_governance_service",
        lambda: service,
    )
    assert resolve_from_configured_release(
        question="làm thường trú",
        audience="officer",
        legal_as_of=date(2026, 8, 11),
    ) is None
    citizen = resolve_from_configured_release(
        question="làm thường trú",
        audience="citizen",
        legal_as_of=date(2026, 8, 11),
    )
    assert citizen and citizen["status"] == "resolved"


def test_released_form_projects_issuing_instrument_as_legal_basis():
    manifest = _manifest()
    asset = next(item for item in manifest["assets"] if item["form_id"] == "ct01")
    asset["issuing_instrument"] = "53/2024/TT-BCA"
    result = resolve_forms(question=manifest["aliases"][0]["alias"], manifest=manifest)
    form = next(item for item in result["recommended_forms"] if item["form_id"] == "ct01")
    assert form["legal_basis"] == ["53/2024/TT-BCA"]


def test_unknown_explicit_form_code_cannot_nominate_unrelated_procedure():
    manifest = _manifest()
    manifest["procedures"].append({
        "procedure_id": "education_foreign_investor",
        "name": "Chuyển trường mầm non tư thục do nhà đầu tư nước ngoài đầu tư",
        "domain": "an_sinh_y_te_giao_duc",
        "coverage_status": "released",
    })
    manifest["aliases"].append({
        "procedure_id": "education_foreign_investor",
        "alias": "nhà đầu tư nước ngoài",
        "alias_kind": "natural",
    })

    result = resolve_forms(
        question="Khách nước ngoài ở nhà tôi qua đêm thì dùng Mẫu NA17 nào để khai báo?",
        manifest=manifest,
        legal_as_of=date(2026, 8, 12),
    )

    assert result["status"] in {"unsupported", "clarification_required"}
    assert result["recommended_forms"] == []
    assert result.get("procedure_id") != "education_foreign_investor"
    assert result["reason"] == "FORM_CODE_NOT_IN_RELEASE"


def test_unique_explicit_form_code_confirms_procedure_before_projection():
    manifest = _manifest()
    manifest["assets"][0]["form_code"] = "CT01"

    result = resolve_forms(
        question="Nhà em đổi chỗ ở, cho xin Mẫu CT01 với ạ",
        manifest=manifest,
        legal_as_of=date(2026, 8, 12),
    )

    assert result["status"] == "resolved"
    assert result["procedure_id"] == "dang_ky_cu_tru"
    assert result["identity_confirmation"]["confirmed"] is True
    assert result["identity_confirmation"]["method"] == "exact_form_code"
    assert all(
        form["procedure_identity_confirmed"] is True
        and form["procedure_id"] == "dang_ky_cu_tru"
        for form in result["recommended_forms"]
    )


def test_bm25_candidate_with_conflicting_domain_is_not_confirmed():
    manifest = _manifest()
    manifest["procedures"].append({
        "procedure_id": "education_foreign_investor",
        "name": "Cho phép trường mầm non có nhà đầu tư nước ngoài hoạt động giáo dục",
        "domain": "an_sinh_y_te_giao_duc",
        "coverage_status": "released",
    })
    manifest["aliases"].append({
        "procedure_id": "education_foreign_investor",
        "alias": "người nước ngoài",
        "alias_kind": "natural",
    })

    result = resolve_forms(
        question="Người nước ngoài ngủ lại nhà tôi thì khai báo lưu trú thế nào?",
        manifest=manifest,
        legal_as_of=date(2026, 8, 12),
    )

    assert result["status"] != "resolved"
    assert result["recommended_forms"] == []
    assert result.get("procedure_id") != "education_foreign_investor"
