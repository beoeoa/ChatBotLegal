from collections import Counter

import pytest

from scripts.build_feature017_golden_v3 import CATEGORIES, DOMAINS, build_cases


def _release(complete=True):
    procedures=[]; assets=[]; bindings=[]
    for i, domain in enumerate(DOMAINS):
        pid=f"p{i}"; fid=f"f{i}"; procedures.append({"procedure_id":pid,"name":f"Thủ tục {i}","domain":domain,"coverage_status":"released","official_source_url":f"https://dichvucong.gov.vn/p{i}"}); assets.append({"form_id":fid,"canonical_name":f"Mẫu {i}","asset_kind":"file","source_url":f"https://vbpl.vn/f{i}.pdf","source_checksum":str(i)*64,"coverage_status":"released","audiences":["citizen"]}); bindings.append({"binding_id":f"b{i}","procedure_id":pid,"form_id":fid,"requirement":"required","audience":"citizen","coverage_status":"released"})
    return {"schema_version":"form-release-v1","release_id":"r1","version":1,"legal_as_of":"2026-08-11","source_snapshot_sha256":"a"*64,"previous_release_id":None,"procedures":procedures,"assets":assets,"bindings":bindings,"aliases":[],"coverage":{"procedure_total":5,"procedure_decided":5,"identity_total":5,"identity_decided":5,"binding_total":5,"binding_decided":5,"complete":complete},"build":{"pipeline_version":"test"}}


def test_golden_builder_requires_complete_coverage():
    with pytest.raises(ValueError, match="FEATURE017_COVERAGE_INCOMPLETE"): build_cases(_release(False))


def test_golden_builder_produces_exact_distribution_and_manifest_truth():
    cases=build_cases(_release()); assert len(cases)==1000
    assert Counter(x["category"] for x in cases)==Counter(dict(CATEGORIES))
    assert all(sum(x["domain"]==d for x in cases)==200 for d in DOMAINS)
    assert all(x["review_status"]=="proposed" and x["generation_type"]=="template" for x in cases)
    assert all(x["expected_form_ids"]==[] or x["expected_form_ids"]==[f"f{DOMAINS.index(x['domain'])}"] for x in cases)


def test_golden_builder_keeps_owner_deferred_as_source_view_only():
    release = _release()
    release["procedures"][0]["coverage_status"] = "owner_deferred"
    release["assets"] = release["assets"][1:]
    release["bindings"] = release["bindings"][1:]
    release["exclusions"] = [
        {
            "target_type": "procedure",
            "target_id": "p0",
            "reason_code": "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
        },
        {
            "target_type": "identity",
            "target_id": "identity-p0",
            "reason_code": "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
        },
        {
            "target_type": "binding",
            "target_id": "binding-p0",
            "reason_code": "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
        },
    ]
    cases = build_cases(release)
    deferred = [item for item in cases if item["procedure_id"] == "p0"]
    assert deferred
    assert all(item["expected_form_ids"] == [] for item in deferred)
    assert all(item["expected_state"] == "owner_deferred" for item in deferred)
    assert all(item["expected_answer_mode"] == "source_view_only" for item in deferred)


def test_duplicate_normalized_names_are_disambiguated_with_procedure_code():
    release = _release()
    release["procedures"].append({
        "procedure_id": "p-duplicate",
        "procedure_code": "p-duplicate",
        "name": release["procedures"][0]["name"] + ".",
        "domain": DOMAINS[0],
        "coverage_status": "verified_gap",
        "official_source_url": "https://dichvucong.gov.vn/p-duplicate",
    })
    release["coverage"]["procedure_total"] = 6
    release["coverage"]["procedure_decided"] = 6

    cases = build_cases(release)
    duplicate_cases = [
        item for item in cases
        if item["procedure_id"] in {"p0", "p-duplicate"}
        and not item["expected_clarification"]
    ]

    assert duplicate_cases
    assert all(str(item["procedure_id"]) in item["question"] for item in duplicate_cases)
