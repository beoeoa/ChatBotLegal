from __future__ import annotations

import pytest

from api.legal_adaptive_hop import execute_bounded_hops


def _doc(document_id: str, **overrides):
    return {
        "document_id": document_id,
        "law_number": f"{document_id}/2026/TEST",
        "effective_status": "effective",
        "jurisdiction": "central",
        "authority_eligible": True,
        "facets": ["procedure"],
        **overrides,
    }


@pytest.mark.asyncio
async def test_verified_graph_uses_previous_hop_result_and_stops_after_two_hops():
    documents = {key: _doc(key) for key in ("A", "B", "C", "D")}
    relationships = [
        {"source_document_id": "A", "target_document_id": "B", "relationship_type": "references", "verified": True},
        {"source_document_id": "B", "target_document_id": "C", "relationship_type": "amended_by", "verified": True},
        {"source_document_id": "C", "target_document_id": "D", "relationship_type": "references", "verified": True},
    ]
    calls: list[tuple[str, str, int]] = []

    async def retrieve(query, target_document_id, hop):
        calls.append((query, target_document_id, hop))
        return [{**documents[target_document_id], "content": f"evidence-{target_document_id}"}]

    result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["thủ tục liên quan"],
        relationships=relationships,
        documents=documents,
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        enabled=True,
    )

    assert [item[1:] for item in calls] == [("B", 1), ("C", 2)]
    assert "evidence-B" in calls[1][0]
    assert result["hop_count"] == 2
    assert result["query_count"] == 2
    assert result["stop_reason"] == "hop_budget_reached"
    assert [item["document_id"] for item in result["evidence"]] == ["B", "C"]


@pytest.mark.asyncio
async def test_unverified_edges_and_cycles_are_never_followed():
    documents = {key: _doc(key) for key in ("A", "B", "C")}
    relationships = [
        {"source_document_id": "A", "target_document_id": "B", "relationship_type": "references", "verified": True},
        {"source_document_id": "A", "target_document_id": "C", "relationship_type": "references", "verified": False},
        {"source_document_id": "B", "target_document_id": "A", "relationship_type": "references", "verified": True},
    ]
    calls = []

    async def retrieve(query, target_document_id, hop):
        calls.append(target_document_id)
        return [{**documents[target_document_id], "content": target_document_id}]

    result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["q"],
        relationships=relationships,
        documents=documents,
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        enabled=True,
    )

    assert calls == ["B"]
    assert result["visited_document_ids"] == ["A", "B"]
    assert result["stop_reason"] == "cycle_or_frontier_exhausted"
    assert result["ignored_unverified_edges"] == 1


@pytest.mark.asyncio
async def test_every_hop_reapplies_validity_hierarchy_jurisdiction_and_facet_gate():
    documents = {
        "A": _doc("A"),
        "EXPIRED": _doc("EXPIRED", effective_status="expired"),
        "LOW": _doc("LOW", authority_eligible=False),
        "LOCAL": _doc("LOCAL", jurisdiction="other_province"),
        "WRONGFACET": _doc("WRONGFACET", facets=["fee"]),
        "OK": _doc("OK"),
    }
    relationships = [
        {"source_document_id": "A", "target_document_id": target, "relationship_type": "references", "verified": True}
        for target in ("EXPIRED", "LOW", "LOCAL", "WRONGFACET", "OK")
    ]
    calls = []

    async def retrieve(query, target_document_id, hop):
        calls.append(target_document_id)
        return [documents[target_document_id]]

    result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["q"],
        relationships=relationships,
        documents=documents,
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        allowed_jurisdictions=["central", "haiphong"],
        enabled=True,
    )

    assert calls == ["OK"]
    assert result["filtered_reasons"] == {
        "expired_or_not_current": 1,
        "facet_mismatch": 1,
        "hierarchy_ineligible": 1,
        "jurisdiction_mismatch": 1,
    }


@pytest.mark.asyncio
async def test_query_budget_is_hard_capped_at_sixteen():
    targets = [f"D{index:02d}" for index in range(20)]
    documents = {"A": _doc("A"), **{target: _doc(target) for target in targets}}
    relationships = [
        {"source_document_id": "A", "target_document_id": target, "relationship_type": "references", "verified": True}
        for target in targets
    ]
    calls = []

    async def retrieve(query, target_document_id, hop):
        calls.append(target_document_id)
        return [documents[target_document_id]]

    result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["q"],
        relationships=relationships,
        documents=documents,
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        enabled=True,
    )

    assert len(calls) == result["query_count"] == 16
    assert result["stop_reason"] == "query_budget_reached"


@pytest.mark.asyncio
async def test_disabled_mode_is_a_zero_side_effect_single_hop_fallback():
    called = False

    async def retrieve(*_args):
        nonlocal called
        called = True
        return []

    result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["q"],
        relationships=[],
        documents={"A": _doc("A")},
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        enabled=False,
    )

    assert called is False
    assert result["status"] == "disabled"
    assert result["stop_reason"] == "disabled_by_config"


@pytest.mark.asyncio
async def test_retrieval_result_is_gated_again_and_vietnamese_relation_is_supported():
    documents = {"A": _doc("A"), "B": _doc("B")}
    relationships = [
        {
            "source_document_id": "A",
            "target_document_id": "B",
            "relationship_type": "Văn bản căn cứ",
            "verification_status": "verified",
        }
    ]

    async def retrieve(_query, _target_document_id, _hop):
        return [{**documents["B"], "expired_date": "2026-01-01"}]

    result = await execute_bounded_hops(
        seed_document_ids=["A"],
        initial_queries=["q"],
        relationships=relationships,
        documents=documents,
        retrieve=retrieve,
        legal_as_of="2026-08-10",
        required_facets=["procedure"],
        enabled=True,
    )

    assert result["evidence"] == []
    assert result["filtered_reasons"] == {"expired_or_not_current": 1}
