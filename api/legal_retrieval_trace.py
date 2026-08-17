"""Machine-readable M3 retrieval trace contract and validator."""

from __future__ import annotations

from typing import Any, Mapping


M3_TRACE_SCHEMA_VERSION = "legal-retrieval-trace-m3-v1"
M3_REQUIRED_TRACE_FIELDS = (
    "schema_version",
    "raw_query",
    "normalized_query",
    "query_classification",
    "vector_candidates",
    "lexical_candidates",
    "fusion_candidates",
    "reranker_candidates",
    "expanded_evidence",
    "final_evidence",
    "stage_latency_ms",
)
M3_REQUIRED_LATENCY_STAGES = (
    "exact_lookup",
    "embedding",
    "ann_search",
    "lexical_sql",
    "hydrate_chunks",
    "neighbors",
    "relationships",
    "rank_filter",
    "validity_overlay",
    "parent_hydration",
    "total",
)


def validate_m3_retrieval_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless one trace contains every criterion-29 stage."""

    if not isinstance(trace, Mapping):
        raise ValueError("m3_trace_object_required")
    missing = [field for field in M3_REQUIRED_TRACE_FIELDS if field not in trace]
    if missing:
        raise ValueError(f"m3_trace_fields_missing:{','.join(missing)}")
    if trace.get("schema_version") != M3_TRACE_SCHEMA_VERSION:
        raise ValueError("m3_trace_schema_invalid")
    for field in ("raw_query", "normalized_query"):
        if not isinstance(trace.get(field), str) or not str(trace[field]).strip():
            raise ValueError(f"m3_trace_{field}_invalid")
    if not isinstance(trace.get("query_classification"), Mapping):
        raise ValueError("m3_trace_query_classification_invalid")
    for field in (
        "vector_candidates",
        "lexical_candidates",
        "fusion_candidates",
        "final_evidence",
    ):
        if not isinstance(trace.get(field), list):
            raise ValueError(f"m3_trace_{field}_invalid")
    reranker = trace.get("reranker_candidates")
    if not isinstance(reranker, Mapping):
        raise ValueError("m3_trace_reranker_candidates_invalid")
    if not isinstance(reranker.get("input"), list) or not isinstance(
        reranker.get("output"), list
    ):
        raise ValueError("m3_trace_reranker_input_output_required")
    expanded = trace.get("expanded_evidence")
    if not isinstance(expanded, Mapping):
        raise ValueError("m3_trace_expanded_evidence_invalid")
    latency = trace.get("stage_latency_ms")
    if not isinstance(latency, Mapping):
        raise ValueError("m3_trace_stage_latency_invalid")
    missing_latency = [
        field for field in M3_REQUIRED_LATENCY_STAGES if field not in latency
    ]
    if missing_latency:
        raise ValueError(
            f"m3_trace_latency_stages_missing:{','.join(missing_latency)}"
        )
    if any(float(latency[field]) < 0 for field in M3_REQUIRED_LATENCY_STAGES):
        raise ValueError("m3_trace_negative_latency")
    return {
        "status": "pass",
        "schema_version": M3_TRACE_SCHEMA_VERSION,
        "required_field_count": len(M3_REQUIRED_TRACE_FIELDS),
        "latency_stage_count": len(M3_REQUIRED_LATENCY_STAGES),
    }


__all__ = [
    "M3_REQUIRED_LATENCY_STAGES",
    "M3_REQUIRED_TRACE_FIELDS",
    "M3_TRACE_SCHEMA_VERSION",
    "validate_m3_retrieval_trace",
]
