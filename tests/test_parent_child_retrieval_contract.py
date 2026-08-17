from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "legal_search_server.py"
ASK = ROOT / "open_notebook" / "graphs" / "ask.py"
PUBLIC_SEARCH = ROOT / "api" / "routers" / "search.py"


def test_parent_hydration_is_after_final_hierarchy_selection():
    source = SERVER.read_text(encoding="utf-8")
    final_hierarchy = source.index(
        "deduplicated = rank_legal_evidence(",
        source.index("# Diversity selection"),
    )
    validity = source.index("validity_projection = apply_validity_overlay(")
    hydration = source.index("parent_rows = self._fetch_parent_contexts(")
    provenance = source.index("bind_request_provenance(item, request)", hydration)

    assert final_hierarchy < validity < hydration < provenance


def test_retrieval_uses_one_batch_parent_lookup_and_bounded_trace():
    source = SERVER.read_text(encoding="utf-8")

    assert "def _fetch_parent_contexts(" in source
    assert '"parent_hydration"' in source
    assert '"parent_context_reason"' in source
    trace_function = source[source.index("def _chunk_trace_item"):]
    assert 'item.get("parent_context")' not in trace_function.split(
        "def _source_trace_item", 1
    )[0]


def test_ask_propagates_parent_context_and_uses_it_for_grounding():
    source = ASK.read_text(encoding="utf-8")

    assert '"parent_context": item.get("parent_context")' in source
    assert "group_parent_child_evidence" in source
    source_text = source[source.index("def _source_text"):source.index(
        "def _has_unsupported_references"
    )]
    assert 'source.get("parent_context")' in source_text


def test_public_citation_projection_does_not_add_parent_bodies():
    source = PUBLIC_SEARCH.read_text(encoding="utf-8")
    citation_builder = source[source.index("def _build_citations_from_retrieval"):source.index(
        "def _format_sources_appendix"
    )]

    assert '"parent_context"' not in citation_builder
    assert '"matched_child_content"' not in citation_builder
