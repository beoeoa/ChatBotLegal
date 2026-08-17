#!/usr/bin/env python3
"""Produce read-only acceptance evidence for M2 serving-manifest controls."""

from __future__ import annotations

import json
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import chromadb
import yaml
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_serving_scope import file_sha256, load_serving_manifest_pointer
from scripts.build_legal_corpus_candidate import _database_url

POINTER = ROOT / "release-data" / "legal" / "serving_manifests" / "active_serving_manifest.json"
OUTPUT = ROOT / "reports" / "m2-serving-manifest"


def _section(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    finish = source.index(end, begin)
    return source[begin:finish]


def main() -> int:
    scope = load_serving_manifest_pointer(
        POINTER, configured_collection="legal_chunks_vnlegal_lal_haiphong_unified_v1"
    )
    manifest_mode = scope.path.stat().st_mode
    pointer = json.loads(POINTER.read_text(encoding="utf-8"))
    server = (ROOT / "scripts" / "legal_search_server.py").read_text(encoding="utf-8")
    router = (ROOT / "api" / "routers" / "legal_search.py").read_text(encoding="utf-8")
    search = _section(server, "    def search(self, request:", "    def _shadow_filter_ids(")
    lexical = _section(server, "    def _fetch_lexical_chunks(", "    def document_detail(")
    parent = _section(server, "    def _fetch_parent_contexts(", "    def _fetch_neighbor_chunk_ids(")
    neighbor = _section(server, "    def _fetch_neighbor_chunk_ids(", "    def _fetch_fallback_chunks(")
    detail = _section(server, "    def document_detail(", "    def document_pdf(")
    load = _section(server, "    def _load(self)", "    def _compute_query_embedding(")

    active_pointer = (
        ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt"
    ).read_text(encoding="utf-8").strip()
    collection = chromadb.PersistentClient(
        path=str(ROOT / "release-data" / "legal" / "chroma_store")
    ).get_collection(scope.collection_name)
    vector_ids = {
        int(str(value).removeprefix("chunk-"))
        for value in collection.get(include=[]).get("ids") or []
    }

    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            database_document_ids = {
                int(row[0])
                for row in connection.execute(text(
                    "SELECT DISTINCT d.id FROM legal_documents d "
                    "JOIN legal_articles a ON a.document_id=d.id "
                    "JOIN legal_article_chunks c ON c.article_id=a.id "
                    "WHERE d.status='active'"
                ))
            }
            database_chunk_ids = {
                int(row[0])
                for row in connection.execute(text(
                    "SELECT c.id FROM legal_article_chunks c "
                    "JOIN legal_articles a ON a.id=c.article_id "
                    "JOIN legal_documents d ON d.id=a.document_id "
                    "WHERE d.status='active'"
                ))
            }
    finally:
        engine.dispose()

    compose = yaml.safe_load((ROOT / "docker-compose.release.yml").read_text(encoding="utf-8"))
    release_env = compose["services"]["legal_retrieval"]["environment"]
    try:
        with urlopen("http://127.0.0.1:8765/health", timeout=10) as response:
            runtime_health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        runtime_health = {"status": "unavailable", "error": exc.__class__.__name__}
    runtime_smoke: dict[str, Any] = {}
    for tier in ("core", "expanded"):
        body = json.dumps({
            "query": "thủ tục xác nhận tình trạng hôn nhân",
            "limit": 2,
            "candidate_count": 20,
            "lexical_candidate_count": 0,
            "retrieval_tier": tier,
            "audience": "citizen",
            "include_trace": True,
            "allow_broad_fallback": False,
        }, ensure_ascii=False).encode("utf-8")
        try:
            request = Request(
                "http://127.0.0.1:8765/search", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            rows = result.get("results") or []
            runtime_smoke[tier] = {
                "pass": bool(rows)
                and all(int(row["document_id"]) in scope.document_ids for row in rows)
                and all(int(row["chunk_id"]) in scope.chunk_ids for row in rows)
                and (result.get("trace") or {}).get("collection") == scope.collection_name,
                "result_count": len(rows),
                "document_ids": [int(row["document_id"]) for row in rows],
                "chunk_ids": [int(row["chunk_id"]) for row in rows],
                "collection": (result.get("trace") or {}).get("collection"),
            }
        except Exception as exc:
            runtime_smoke[tier] = {"pass": False, "error": exc.__class__.__name__}
    gates: dict[str, dict[str, Any]] = {
        "25_immutable_versioned_manifest": {
            "pass": not bool(manifest_mode & stat.S_IWRITE)
            and scope.dataset_version in scope.path.name
            and pointer["file_sha256"] == file_sha256(scope.path),
            "evidence": {"read_only": not bool(manifest_mode & stat.S_IWRITE), "path": str(scope.path)},
        },
        "26_lexical_retrieval_manifest_only": {
            "pass": "_serving_document_clause" in lexical
            and lexical.index("{serving_document_clause}") < lexical.index("LIMIT :limit")
            and release_env.get("LEGAL_SERVING_MANIFEST_REQUIRED") == "true",
        },
        "27_parent_neighbor_manifest_only": {
            "pass": "_serving_document_clause" in parent
            and "allowed_chunks = self._request_serving_chunk_ids()" in neighbor
            and "chunk_id not in allowed_chunks" in neighbor,
        },
        "28_backend_access_control": {
            "pass": "def _serving_audience(request: Request)" in router
            and 'request_payload["audience"] = _serving_audience(raw_request)' in router
            and "document_visibility" in (ROOT / "api" / "legal_serving_scope.py").read_text(encoding="utf-8"),
        },
        "vector_retrieval_manifest_only": {
            "pass": "self._source_collection = (" in load
            and "self._collection\n                        if self._serving_scope" in load
            and vector_ids == set(scope.chunk_ids),
            "observed_vectors": len(vector_ids),
        },
        "hydration_rerank_citation_manifest_only": {
            "pass": "ranked = self._shadow_filter_rows(ranked, request.retrieval_tier)" in search
            and search.index("ranked = self._shadow_filter_rows(ranked") < search.index("ranked = self._rerank_candidates(")
            and "self.assert_document_access" in detail
            and "serving_chunk_clause" in detail,
        },
        "manifest_database_vector_exact_match": {
            "pass": database_document_ids == set(scope.document_ids)
            and database_chunk_ids == set(scope.chunk_ids)
            and vector_ids == set(scope.chunk_ids),
            "database_documents": len(database_document_ids),
            "database_chunks": len(database_chunk_ids),
            "manifest_documents": len(scope.document_ids),
            "manifest_chunks": len(scope.chunk_ids),
        },
        "active_collection_unchanged_from_m1": {
            "pass": active_pointer == scope.collection_name,
            "active_collection": active_pointer,
        },
        "runtime_health_manifest_bound": {
            "pass": runtime_health.get("status") == "healthy"
            and runtime_health.get("ready") is True
            and (runtime_health.get("serving_manifest") or {}).get("manifest_sha256")
            == scope.manifest_sha256
            and runtime_health.get("indexed_records") == len(scope.chunk_ids),
            "runtime": runtime_health,
        },
        "runtime_core_expanded_smoke_manifest_bound": {
            "pass": all(item.get("pass") is True for item in runtime_smoke.values()),
            "tiers": runtime_smoke,
        },
    }
    status = "pass" if all(item["pass"] for item in gates.values()) else "fail"
    report = {
        "schema_version": "legal-m2-acceptance-v1",
        "status": status,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "serving_manifest": scope.public_status(),
        "gates": gates,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT / "m2_acceptance_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums = {
        path.name: file_sha256(path)
        for path in (scope.path, POINTER, OUTPUT / "m2_report.json", report_path)
    }
    (OUTPUT / "m2_checksums.json").write_text(
        json.dumps(checksums, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
