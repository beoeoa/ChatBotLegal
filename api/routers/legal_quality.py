from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from api.data_paths import notebook_data_dir

router = APIRouter(prefix="/legal/quality", tags=["legal-quality"])

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK_DATA = notebook_data_dir()
ROLE_EVAL_PATH = NOTEBOOK_DATA / "role-evaluation.json"
RETRIEVAL_EVAL_PATH = NOTEBOOK_DATA / "retrieval-evaluation.json"
FORMS_INDEX_PATH = NOTEBOOK_DATA / "forms" / "haiphong_official_form_index.json"
FORMS_CANDIDATE_PATH = NOTEBOOK_DATA / "forms" / "official_forms_candidates_classified.json"
FORMS_INVENTORY_PATH = NOTEBOOK_DATA / "forms" / "forms_inventory_report.json"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _role_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    results = payload.get("results", [])
    completed = [item for item in results if item.get("status") == "completed"]
    by_role: dict[str, dict[str, int]] = {}
    hallucination_flags = 0
    fallback_count = 0
    for item in completed:
        role = item.get("role") or "unknown"
        checks = item.get("checks") or {}
        bucket = by_role.setdefault(role, {"count": 0, "grounded": 0, "role_style": 0})
        bucket["count"] += 1
        if checks.get("citation_count", 0) > 0 and not checks.get("suspicious_phrases"):
            bucket["grounded"] += 1
        if checks.get("matches_role_style"):
            bucket["role_style"] += 1
        if checks.get("suspicious_phrases"):
            hallucination_flags += 1
        if checks.get("is_guardrail_fallback"):
            fallback_count += 1
    return {
        "available": True,
        "generated_at": payload.get("generated_at"),
        "total": len(results),
        "completed": len(completed),
        "hallucination_flags": hallucination_flags,
        "fallback_count": fallback_count,
        "by_role": by_role,
        "sample": completed[:10],
    }


def _retrieval_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    results = payload.get("results", [])
    with_sources = [item for item in results if item.get("source_count", 0) > 0]
    top_status: dict[str, int] = {}
    for item in results:
        first = (item.get("sources") or [{}])[0]
        status = first.get("document_status") or "unknown"
        top_status[status] = top_status.get(status, 0) + 1
    avg_sources = round(
        sum(item.get("source_count", 0) for item in results) / max(len(results), 1), 2
    )
    return {
        "available": True,
        "generated_at": payload.get("generated_at"),
        "total": len(results),
        "with_sources": len(with_sources),
        "avg_sources": avg_sources,
        "top_status_distribution": top_status,
        "sample": results[:10],
    }


def _forms_quality_summary() -> dict[str, Any]:
    """Return deterministic form coverage metrics for the admin quality view."""
    inventory = _load_json(FORMS_INVENTORY_PATH) or {}
    index = _load_json(FORMS_INDEX_PATH) or {}
    candidates = _load_json(FORMS_CANDIDATE_PATH) or {}
    records = list(index.get("forms") or []) if isinstance(index, dict) else []
    candidate_records = list(candidates.get("records") or []) if isinstance(candidates, dict) else []
    root = ROOT
    missing_file = 0
    missing_url = 0
    local_only = 0
    for record in records:
        path = str(record.get("source_package_path") or record.get("local_path") or record.get("priority_path") or "").replace("\\", "/")
        if not path or not (root / path).is_file():
            missing_file += 1
        source_url = (
            record.get("source_url")
            or record.get("full_url")
            or record.get("source_page_url")
            or record.get("source_download_url")
        )
        if not str(source_url or "").startswith(("http://", "https://")):
            missing_url += 1
            if path and (root / path).is_file():
                local_only += 1
    status_counts: dict[str, int] = {}
    for record in candidate_records:
        status = str(record.get("review_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "inventory": {
            "metadata_records": inventory.get("total_metadata_records", 0),
            "physical_files": inventory.get("total_physical_files", 0),
            "valid_official_forms": inventory.get("valid_official_forms_count", 0),
            "missing_files": inventory.get("missing_files_count", 0),
        },
        "official_index": {
            "total": len(records),
            "missing_file": missing_file,
            "missing_url": missing_url,
            "local_file_without_external_url": local_only,
            "domain_counts": (index.get("summary") or {}).get("domain_counts", {}),
        },
        "candidate_queue": {
            "total": len(candidate_records),
            "status_counts": status_counts,
        },
    }


def _require_admin(request: Request) -> None:
    from api.auth import get_request_role

    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin được xem dashboard chất lượng.")


async def _operational_quality() -> dict[str, Any]:
    """Summarize operational signals without reading user content or identities."""
    from api.observability import telemetry
    from open_notebook.database.repository import repo_query

    runtime = telemetry.summary()
    ocr_failed = 0
    import_failed = 0
    try:
        rows = await repo_query(
            "SELECT count() AS count FROM legal_crawl_candidate "
            "WHERE raw_metadata.ocr_status IN ['failed', 'unavailable', 'empty'] GROUP ALL;"
        )
        ocr_failed = int(rows[0].get("count", 0)) if rows else 0
    except Exception:
        pass
    try:
        rows = await repo_query(
            "SELECT count() AS count FROM legal_import_job WHERE status = 'failed' GROUP ALL;"
        )
        import_failed = int(rows[0].get("count", 0)) if rows else 0
    except Exception:
        pass
    return {
        "runtime": runtime,
        "quality_issues": {
            "citation_dead": int((runtime.get("issue_counts") or {}).get("citation_dead", 0)),
            "pdf_export_failed": int((runtime.get("issue_counts") or {}).get("pdf_export_failed", 0)),
            "broken_form_url": int((runtime.get("issue_counts") or {}).get("broken_form_url", 0)),
            "slow_request": len(runtime.get("slow_requests") or []),
            "ocr_failed": ocr_failed + int((runtime.get("issue_counts") or {}).get("ocr_failed", 0)),
            "import_failed": import_failed,
            "unanswered_question": int((runtime.get("issue_counts") or {}).get("unanswered_question", 0)),
        },
    }


@router.get("/summary")
async def quality_summary(request: Request) -> dict[str, Any]:
    _require_admin(request)
    role_payload = _load_json(ROLE_EVAL_PATH)
    retrieval_payload = _load_json(RETRIEVAL_EVAL_PATH)
    return {
        "generated_at": datetime.now().isoformat(),
        "role_evaluation": _role_summary(role_payload),
        "retrieval_evaluation": _retrieval_summary(retrieval_payload),
        **await _operational_quality(),
        "forms": _forms_quality_summary(),
    }


@router.get("/runtime")
async def runtime_quality(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return await _operational_quality()


@router.post("/run/{evaluation_type}")
async def run_quality_evaluation(evaluation_type: str, request: Request) -> dict[str, Any]:
    _require_admin(request)
    mapping = {
        "retrieval": ROOT / "scripts" / "evaluate_legal_retrieval.py",
        "role": ROOT / "scripts" / "evaluate_role_answers.py",
        "live334": ROOT / "scripts" / "collect_334_live_answers.py",
        "gate334": ROOT / "scripts" / "audit_334_quality_results.py",
        "matrices": ROOT / "scripts" / "build_reviewed_legal_matrices.py",
        "assets334": ROOT / "scripts" / "check_334_asset_health.py",
    }
    script = mapping.get(evaluation_type)
    if not script:
        raise HTTPException(status_code=400, detail="evaluation_type không được hỗ trợ")
    if not script.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy script đánh giá")

    subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "started": True,
        "evaluation_type": evaluation_type,
        "script": str(script),
    }

@router.get("/stats/by-status")
async def stats_by_status(request: Request) -> dict[str, Any]:
    """Thong ke candidate theo trang thai"""
    _require_admin(request)
    try:
        from open_notebook.database.repository import repo_query
        query = """
            SELECT status, count() as count 
            FROM legal_crawl_candidate 
            GROUP BY status
        """
        results = await repo_query(query, {})
        total = sum(r.get("count", 0) for r in results) if results else 0
        stats = {}
        for r in results or []:
            status = r.get("status", "unknown")
            count = r.get("count", 0)
            stats[status] = {"count": count, "percentage": round(count / total * 100, 1) if total > 0 else 0}
        return {"total": total, "by_status": stats, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"error": str(e), "total": 0, "by_status": {}}

@router.get("/stats/by-domain")
async def stats_by_domain(request: Request) -> dict[str, Any]:
    """Thong ke theo linh vuc"""
    _require_admin(request)
    domains = ["ho_tich", "cu_tru", "dat_dai", "khieu_nai", "xu_phat"]
    return {"domains": {d: {"total": 0} for d in domains}, "timestamp": datetime.now().isoformat()}
