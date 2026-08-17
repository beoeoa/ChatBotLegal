from __future__ import annotations

import statistics
import time

from api.form_router_v3 import resolve_forms


def test_form_router_is_well_below_three_second_retrieval_gate():
    manifest = {
        "schema_version": "form-release-v1", "release_id": "perf-r1",
        "procedures": [{"procedure_id": f"p{i}", "name": f"Thủ tục hành chính số {i}", "domain": "cu_tru_an_ninh", "official_source_url": f"https://dichvucong.gov.vn/p{i}", "coverage_status": "released"} for i in range(191)],
        "aliases": [{"procedure_id": f"p{i}", "alias": f"làm hồ sơ số {i}", "alias_kind": "natural"} for i in range(191)],
        "assets": [{"form_id": f"f{i}", "canonical_name": f"Biểu mẫu {i}", "asset_kind": "file", "source_url": f"https://vbpl.vn/f{i}.pdf", "source_checksum": "a" * 64, "audiences": ["citizen"], "coverage_status": "released"} for i in range(131)],
        "bindings": [{"binding_id": f"b{i}", "procedure_id": f"p{i % 191}", "form_id": f"f{i % 131}", "requirement": "required", "audience": "citizen", "coverage_status": "released"} for i in range(229)],
    }
    expected = {}
    for binding in manifest["bindings"]:
        expected.setdefault(binding["procedure_id"], set()).add(binding["form_id"])
    timings=[]
    for i in range(1000):
        started=time.perf_counter(); result=resolve_forms(question=f"làm hồ sơ số {i % 191}", manifest=manifest); timings.append((time.perf_counter()-started)*1000)
        procedure_id = f"p{i % 191}"
        assert result["status"] == "resolved"
        assert result["procedure_id"] == procedure_id
        assert {item["form_id"] for item in result["recommended_forms"]} == expected[procedure_id]
        assert result["evidence_packet"]["provider_may_modify_form_identity"] is False
    p95=statistics.quantiles(timings,n=100)[94]
    assert p95 < 3000
