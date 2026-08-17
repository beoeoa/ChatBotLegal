from scripts.build_feature017_coverage import DEFAULT, build
from api.form_governance_models import ActorContext, FormReviewSubmission
from api.form_governance_repository import InMemoryFormGovernanceRepository
from api.form_governance_service import FormGovernanceService
from api.form_procedure_scope import fixed_procedure_scope_lookup


def test_compat_coverage_is_read_only_and_exposes_scope_mismatch():
    result=build(DEFAULT)
    assert result["runtime_mutated"] is False and result["candidate_only"] is True
    assert result["pending_identity_count"] == 100
    assert result["released_identity_count"] == 31
    assert all(item["procedure_ids"] for item in result["review_queue"])
    assert "expected_commune_scope_baseline" in result
    assert result["observed_source_scope"] == {
        "procedures": 191,
        "identities": 131,
        "bindings": 229,
    }
    assert result["source_procedure_count_before_scope"] == 418
    assert result["scope_filter"] == {"executing_level": "commune"}
    assert result["baseline_matches"] is True
    assert result["warning"] is None


def test_runtime_coverage_deduplicates_one_asset_proposed_for_two_procedures():
    repository = InMemoryFormGovernanceRepository()
    service = FormGovernanceService(
        repository,
        procedure_scope_lookup=fixed_procedure_scope_lookup({
            "p1": "cu_tru_an_ninh",
            "p2": "cu_tru_an_ninh",
        }),
    )
    officer = ActorContext(user_id="o1", role="officer", domains=["cu_tru_an_ninh"])
    for procedure_id in ("p1", "p2"):
        service.submit(officer, FormReviewSubmission(
            procedure_id=procedure_id,
            domain="cu_tru_an_ninh",
            title="Cùng một biểu mẫu chính thức",
            source_url="https://vbpl.vn/shared-form.pdf",
            source_checksum="a" * 64,
        ))
    coverage = service.coverage(officer)
    assert coverage["procedure_total"] == 2
    assert coverage["identity_total"] == 1
    assert coverage["binding_total"] == 2
    assert coverage["identity_decided"] == 0
    assert coverage["complete"] is False
