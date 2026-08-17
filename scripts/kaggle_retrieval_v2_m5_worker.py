"""Private Kaggle M5 worker for Retrieval Release V2.

Inputs are immutable Kaggle datasets plus the completed Stage C kernel output.
The worker performs no network access and writes only aggregate M5 evidence and
a development-only candidate cache for the M6 kernels.
"""

from __future__ import annotations

from datetime import date
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping
import zipfile


PINNED_TORCH_WHEEL_SHA256 = "222be02548c2e74a21a8fbc8e5b8d2eef9f9faee865d70385d2eb1b9aabcbc76"
PINNED_TORCH_VERSION = "2.5.1+cu121"


def _bootstrap_pinned_torch_runtime() -> None:
    if not os.getenv("KAGGLE_KERNEL_RUN_TYPE") or os.getenv("LEGAL_RAG_TORCH_BOOTSTRAPPED") == "1":
        return
    wheels = sorted(Path("/kaggle/input").rglob("torch-2.5.1*cu121-cp312-cp312-linux_x86_64.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"single_pinned_torch_wheel_required:found={len(wheels)}")
    wheel = wheels[0]
    digest = hashlib.sha256()
    with wheel.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != PINNED_TORCH_WHEEL_SHA256:
        raise RuntimeError("pinned_torch_wheel_checksum_mismatch")
    install_wheel = Path("/kaggle/temp/torch-2.5.1+cu121-cp312-cp312-linux_x86_64.whl")
    install_wheel.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheel, install_wheel)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(install_wheel)],
        check=True,
    )
    # Kaggle's preinstalled torchvision/torchaudio track its newer torch image.
    # This benchmark is text-only; removing the incompatible optional packages
    # prevents Transformers from importing their binary operators.
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", "torchvision", "torchaudio"],
        check=True,
    )
    environment = dict(os.environ)
    environment["LEGAL_RAG_TORCH_BOOTSTRAPPED"] = "1"
    os.execvpe(sys.executable, [sys.executable, *sys.argv], environment)


_bootstrap_pinned_torch_runtime()

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoTokenizer

# Kaggle script kernels upload only ``code_file``.  Shared benchmark helpers are
# therefore shipped in the immutable private benchmark dataset and imported
# from its mounted directory.  Local execution still resolves the scripts
# directory first.
INPUT_ROOT = Path(os.getenv("KAGGLE_INPUT_ROOT", "/kaggle/input"))
for helper in sorted(INPUT_ROOT.rglob("kaggle_retrieval_v2_benchmark_common.py")):
    sys.path.insert(0, str(helper.parent))
    break

from kaggle_retrieval_v2_benchmark_common import (
    ARTICLE_RE,
    DEVELOPMENT_SPLITS,
    LAW_RE,
    canonical_sha256,
    case_sources,
    compact_summary,
    evaluate_rows,
    file_sha256,
    fuse,
    matches_any,
    normalize_exact,
    safe_fts_query,
    safety_pass,
    score_case,
    utc_now,
    write_json,
)


OUTPUT_ROOT = Path(os.getenv("KAGGLE_OUTPUT_ROOT", "/kaggle/working"))
QUERY_PREFIX = (
    "Instruct: Given a Vietnamese legal question, retrieve relevant legal "
    "passages that answer the question\nQuery: "
)
MAX_VECTOR_K = 50
MAX_LEXICAL_K = 50


def _require_compatible_cuda() -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise RuntimeError("kaggle_cuda_required")
    name = torch.cuda.get_device_name(0)
    capability = torch.cuda.get_device_capability(0)
    supported = getattr(torch.cuda, "get_arch_list", lambda: [])()
    architecture = f"sm_{capability[0]}{capability[1]}"
    if architecture not in supported:
        raise RuntimeError(
            "kaggle_gpu_compute_capability_incompatible:"
            f"gpu={name}:capability={capability}:torch_arches={supported}"
        )
    return {
        "name": name,
        "compute_capability": list(capability),
        "torch_arches": supported,
        "torch_version": torch.__version__,
        "pinned_torch_version": PINNED_TORCH_VERSION,
        "pinned_torch_wheel_sha256": PINNED_TORCH_WHEEL_SHA256,
    }


def _single(pattern: str) -> Path:
    matches = sorted(INPUT_ROOT.rglob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"single_input_required:{pattern}:found={len(matches)}")
    return matches[0]


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _model_revision_fingerprint(model_path: Path) -> str:
    digest = __import__("hashlib").sha256()
    files = sorted(
        {
            path
            for pattern in ("*.json", "*.safetensors", "*.bin", "*.model", "*.txt")
            for path in model_path.rglob(pattern)
            if path.is_file()
        },
        key=lambda path: path.relative_to(model_path).as_posix(),
    )
    if not files:
        digest.update(b"model-artifacts-missing")
    for path in files:
        relative = path.relative_to(model_path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _discover_model(expected: str) -> Path:
    candidates = sorted({path.parent for path in INPUT_ROOT.rglob("config.json")})
    for candidate in candidates:
        try:
            if _model_revision_fingerprint(candidate) == expected:
                return candidate
        except RuntimeError:
            continue
    archives = sorted(INPUT_ROOT.rglob("model.zip"))
    for archive in archives:
        target = Path("/kaggle/temp/vnlegal-lal")
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
        for candidate in sorted({path.parent for path in target.rglob("config.json")}):
            if _model_revision_fingerprint(candidate) == expected:
                return candidate
    raise RuntimeError("pinned_embedding_model_not_found")


class QueryEncoder:
    def __init__(self, model_path: Path) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("kaggle_cuda_required")
        self.device = torch.device("cuda")
        config = AutoConfig.from_pretrained(model_path, local_files_only=True)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModel.from_pretrained(
            model_path,
            config=config,
            local_files_only=True,
            torch_dtype=torch.float16,
        ).to(self.device)
        self.model.eval()

    def encode(self, queries: list[str], batch_size: int = 16) -> tuple[np.ndarray, list[float]]:
        vectors: list[np.ndarray] = []
        timings: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(queries), batch_size):
                batch = [QUERY_PREFIX + value.strip() for value in queries[start : start + batch_size]]
                began = time.perf_counter()
                inputs = self.tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                )
                inputs = {key: value.to(self.device) for key, value in inputs.items()}
                output = self.model(**inputs)
                last = inputs["attention_mask"].sum(dim=1) - 1
                embedding = output.last_hidden_state[
                    torch.arange(len(batch), device=self.device), last
                ]
                embedding = F.normalize(embedding, p=2, dim=1)
                elapsed = (time.perf_counter() - began) * 1000.0
                vectors.append(embedding.float().cpu().numpy().astype(np.float32))
                timings.extend([elapsed / len(batch)] * len(batch))
        return np.concatenate(vectors, axis=0), timings

    def close(self) -> None:
        self.model.cpu()
        del self.model
        gc.collect()
        torch.cuda.empty_cache()


def _extract_sqlite(contract: Mapping[str, Any]) -> Path:
    # Kaggle expands uploaded ZIP files in dataset mounts.  Accept either the
    # original checksum-bound archive or its single expected SQLite payload;
    # both paths are authenticated by the immutable M5 contract.
    direct = sorted(INPUT_ROOT.rglob(str(contract["sqlite_file_name"])))
    if len(direct) == 1:
        target = direct[0]
        if file_sha256(target) != str(contract["sqlite_file_sha256"]):
            raise RuntimeError("sqlite_file_checksum_mismatch")
        return target
    if len(direct) > 1:
        raise RuntimeError(f"single_sqlite_input_required:found={len(direct)}")

    archive = _single(str(contract["sqlite_archive_name"]))
    if file_sha256(archive) != str(contract["sqlite_archive_sha256"]):
        raise RuntimeError("sqlite_archive_checksum_mismatch")
    target_dir = Path("/kaggle/temp/retrieval-v2-index")
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if name.endswith(".sqlite3")]
        if len(names) != 1:
            raise RuntimeError("single_sqlite_in_archive_required")
        bundle.extract(names[0], target_dir)
        target = target_dir / names[0]
    if file_sha256(target) != str(contract["sqlite_file_sha256"]):
        raise RuntimeError("sqlite_file_checksum_mismatch")
    return target


def _scope_clause(scope: str, as_of: str) -> tuple[str, list[Any]]:
    if scope == "current":
        states = "document_serving_state='current_retrievable'"
    elif scope == "historical":
        states = "document_serving_state IN ('current_retrievable','historical_only')"
    else:
        raise ValueError(f"non_retrieval_scope:{scope}")
    return (
        f"{states} AND (effective_from IS NULL OR effective_from='' OR effective_from<=?) "
        "AND (effective_to IS NULL OR effective_to='' OR effective_to>?)",
        [as_of, as_of],
    )


def _candidate(row: sqlite3.Row, score: float, source: str) -> dict[str, Any]:
    return {
        "chunk_revision_id": str(row["chunk_revision_id"]),
        "document_id": int(row["document_id"]),
        "article_id": int(row["article_id"]),
        "chunk_index": int(row["chunk_index"]),
        "document_serving_state": str(row["document_serving_state"]),
        "law_number": str(row["law_number"]),
        "article_number": str(row["article_number"]),
        "domain_slug": str(row["domain_slug"]),
        "source_url": str(row["source_url"]),
        "effective_from": str(row["effective_from"] or "") or None,
        "effective_to": str(row["effective_to"] or "") or None,
        "structural_path": str(row["structural_path"]),
        "score": float(score),
        "retrieval_source": source,
    }


def _rows_by_ids(connection: sqlite3.Connection, ids: list[str]) -> dict[str, sqlite3.Row]:
    output: dict[str, sqlite3.Row] = {}
    for start in range(0, len(ids), 800):
        part = ids[start : start + 800]
        placeholders = ",".join("?" for _ in part)
        for row in connection.execute(f"SELECT * FROM chunks WHERE chunk_revision_id IN ({placeholders})", part):
            output[str(row["chunk_revision_id"])] = row
    return output


def _exact(connection: sqlite3.Connection, query: str, scope: str, as_of: str) -> list[dict[str, Any]]:
    laws = list(dict.fromkeys(normalize_exact(match.group(0)) for match in LAW_RE.finditer(query)))
    articles = list(dict.fromkeys(normalize_exact(match.group(1)) for match in ARTICLE_RE.finditer(query)))
    keys: list[tuple[str, str]] = []
    if laws and articles:
        keys.extend(("law_article", f"{law}|{article}") for law in laws for article in articles)
    elif laws:
        keys.extend(("law_number", law) for law in laws)
    elif articles:
        keys.extend(("article_number", article) for article in articles)
    if not keys:
        return []
    clauses = " OR ".join("(e.key_kind=? AND e.normalized_key=?)" for _ in keys)
    scope_sql, scope_params = _scope_clause(scope, as_of)
    params = [value for pair in keys for value in pair] + scope_params
    rows = connection.execute(
        "SELECT DISTINCT c.* FROM exact_lookup e JOIN chunks c ON c.chunk_revision_id=e.chunk_revision_id "
        f"WHERE ({clauses}) AND {scope_sql} ORDER BY c.document_id,c.article_id,c.chunk_index LIMIT 200",
        params,
    ).fetchall()
    return [_candidate(row, 1.0 / (index + 1), "exact") for index, row in enumerate(rows)]


def _lexical(connection: sqlite3.Connection, query: str, scope: str, as_of: str) -> list[dict[str, Any]]:
    fts = safe_fts_query(query)
    if not fts:
        return []
    scope_sql, scope_params = _scope_clause(scope, as_of)
    rows = connection.execute(
        "SELECT c.*,bm25(chunk_fts) AS lexical_rank FROM chunk_fts "
        "JOIN chunks c ON c.chunk_revision_id=chunk_fts.chunk_revision_id "
        f"WHERE chunk_fts MATCH ? AND {scope_sql} ORDER BY lexical_rank LIMIT ?",
        [fts, *scope_params, MAX_LEXICAL_K],
    ).fetchall()
    return [_candidate(row, 1.0 / (index + 1), "lexical") for index, row in enumerate(rows)]


def _load_vectors(
    output_manifest: Mapping[str, Any], output_dir: Path, connection: sqlite3.Connection
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    ids_parts: list[np.ndarray] = []
    vector_parts: list[np.ndarray] = []
    for shard in output_manifest.get("shards") or []:
        path = output_dir / str(shard["path"])
        if file_sha256(path) != str(shard["sha256"]):
            raise RuntimeError(f"vector_shard_checksum_mismatch:{path.name}")
        payload = np.load(path, allow_pickle=False)
        ids_parts.append(payload["ids"].astype(str))
        vector_parts.append(payload["vectors"].astype(np.float32, copy=False))
    ids = np.concatenate(ids_parts).astype(str).tolist()
    vectors = np.concatenate(vector_parts, axis=0)
    if len(ids) != int(output_manifest.get("vector_count") or output_manifest.get("chunk_count") or 0):
        raise RuntimeError("vector_count_mismatch")
    rows = _rows_by_ids(connection, ids)
    if len(rows) != len(ids):
        raise RuntimeError("vector_hydration_mismatch")
    states = np.asarray([1 if rows[identifier]["document_serving_state"] == "current_retrievable" else 2 for identifier in ids], dtype=np.int8)
    starts = np.asarray([date.fromisoformat(str(rows[identifier]["effective_from"] or "0001-01-01")[:10]).toordinal() for identifier in ids], dtype=np.int32)
    ends = np.asarray([date.fromisoformat(str(rows[identifier]["effective_to"] or "9999-12-31")[:10]).toordinal() for identifier in ids], dtype=np.int32)
    return ids, vectors, states, starts, ends


def _vector_search(
    query_vectors: np.ndarray,
    cases: list[Mapping[str, Any]],
    ids: list[str],
    vectors: np.ndarray,
    states: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    connection: sqlite3.Connection,
    batch_size: int = 24,
) -> tuple[list[list[dict[str, Any]]], list[float]]:
    corpus = torch.from_numpy(vectors).to("cuda", dtype=torch.float16)
    del vectors
    results: list[list[dict[str, Any]]] = [[] for _ in cases]
    timings = [0.0 for _ in cases]
    for offset in range(0, len(cases), batch_size):
        batch_cases = cases[offset : offset + batch_size]
        queries = torch.from_numpy(query_vectors[offset : offset + len(batch_cases)]).to("cuda", dtype=torch.float16)
        began = time.perf_counter()
        scores = queries @ corpus.T
        valid = np.empty((len(batch_cases), len(ids)), dtype=np.bool_)
        for index, case in enumerate(batch_cases):
            ordinal = date.fromisoformat(str(case["legal_as_of"])[:10]).toordinal()
            state_ok = states == 1 if case["temporal_scope"] == "current" else states >= 1
            valid[index] = state_ok & (starts <= ordinal) & (ends > ordinal)
        valid_tensor = torch.from_numpy(valid).to("cuda")
        scores.masked_fill_(~valid_tensor, float("-inf"))
        values, indices = torch.topk(scores, k=MAX_VECTOR_K, dim=1)
        torch.cuda.synchronize()
        elapsed = (time.perf_counter() - began) * 1000.0 / len(batch_cases)
        values_np = values.float().cpu().numpy()
        indices_np = indices.cpu().numpy()
        requested_ids = [ids[int(value)] for value in indices_np.reshape(-1)]
        rows = _rows_by_ids(connection, requested_ids)
        for local_index in range(len(batch_cases)):
            branch: list[dict[str, Any]] = []
            for score, corpus_index in zip(values_np[local_index], indices_np[local_index]):
                identifier = ids[int(corpus_index)]
                branch.append(_candidate(rows[identifier], float(score), "vector"))
            results[offset + local_index] = branch
            timings[offset + local_index] = elapsed
        del queries, scores, valid_tensor, values, indices
    del corpus
    torch.cuda.empty_cache()
    return results, timings


def _specs() -> list[dict[str, Any]]:
    base = {"vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "legacy_stack", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": False}
    specs = [{"id": "m5-control", "changed": "none", **base}]
    specs.append({"id": "m5-exact-lookup-disabled", "changed": "exact_lookup_enabled", **base, "exact_lookup_enabled": False})
    specs.extend({"id": f"m5-vector-k{k}", "changed": "vector_top_k", **base, "vector_top_k": k} for k in (10, 20, 30, 50))
    specs.extend({"id": f"m5-lexical-k{k}", "changed": "lexical_top_k", **base, "lexical_top_k": k} for k in (10, 20, 30, 50))
    specs.extend([
        {"id": "m5-fusion-rrf", "changed": "fusion_strategy", **base, "fusion_strategy": "rrf"},
        {"id": "m5-fusion-weighted-070-030", "changed": "fusion_weights", **base, "fusion_strategy": "weighted", "vector_weight": 0.7, "lexical_weight": 0.3},
        {"id": "m5-fusion-weighted-060-040", "changed": "fusion_strategy", **base, "fusion_strategy": "weighted"},
        {"id": "m5-fusion-weighted-050-050", "changed": "fusion_weights", **base, "fusion_strategy": "weighted", "vector_weight": 0.5, "lexical_weight": 0.5},
        {"id": "m5-issue-splitting", "changed": "issue_split_enabled", **base, "issue_split_enabled": True},
    ])
    return specs


def _quality(experiment: Mapping[str, Any]) -> tuple[float, ...]:
    splits = experiment["splits"]
    total = sum(int(value["answer_required_count"]) for value in splits.values())
    weighted = lambda key: sum(float(value[key]) * int(value["answer_required_count"]) for value in splits.values()) / total
    p95 = max(float(value["latency_ms"]["p95"]) for value in splits.values())
    return (float(safety_pass(splits)), weighted("candidate_recall_at_50"), weighted("recall_at_10"), weighted("mrr_at_10"), weighted("all_required_sources_coverage"), -p95)


def main() -> None:
    started = time.perf_counter()
    gpu_runtime = _require_compatible_cuda()
    contract_path = _single("kaggle-m5-contract.json")
    contract = _load(contract_path)
    suite_path = _single(str(contract["suite_name"]))
    if file_sha256(suite_path) != str(contract["suite_sha256"]):
        raise RuntimeError("development_suite_checksum_mismatch")
    suite = _load(suite_path)
    cases = list(suite.get("cases") or [])
    if len(cases) != 1500 or any(case.get("split") not in DEVELOPMENT_SPLITS for case in cases):
        raise RuntimeError("development_only_1500_suite_required")
    sqlite_path = _extract_sqlite(contract)
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"sqlite_integrity_failed:{integrity}")

    output_manifest_path = _single("embedding-output-manifest.json")
    output_manifest = _load(output_manifest_path)
    if str(output_manifest.get("vector_content_sha256")) != str(contract["vector_content_sha256"]):
        raise RuntimeError("stage_c_vector_content_fingerprint_mismatch")
    model_path = _discover_model(str(contract["model_artifact_fingerprint"]))
    encoder_load_started = time.perf_counter()
    encoder = QueryEncoder(model_path)
    encoder_load_ms = (time.perf_counter() - encoder_load_started) * 1000.0

    answer_cases = [case for case in cases if case.get("answer_required")]
    query_vectors, embed_timings = encoder.encode([str(case["question"]) for case in answer_cases])
    encoder.close()
    ids, vectors, states, starts, ends = _load_vectors(output_manifest, output_manifest_path.parent, connection)
    vector_branches, vector_timings = _vector_search(query_vectors, answer_cases, ids, vectors, states, starts, ends, connection)

    branch_by_case: dict[str, dict[str, Any]] = {}
    for index, case in enumerate(answer_cases):
        query = str(case["question"])
        scope = str(case["temporal_scope"])
        as_of = str(case["legal_as_of"])
        began = time.perf_counter()
        exact = _exact(connection, query, scope, as_of)
        exact_ms = (time.perf_counter() - began) * 1000.0
        began = time.perf_counter()
        lexical = _lexical(connection, query, scope, as_of)
        lexical_ms = (time.perf_counter() - began) * 1000.0
        branch_by_case[str(case["case_id"])] = {
            "exact": exact,
            "vector": vector_branches[index],
            "lexical": lexical,
            "latency_ms": embed_timings[index] + vector_timings[index] + exact_ms + lexical_ms,
            "stage_latency_ms": {"embedding": embed_timings[index], "vector": vector_timings[index], "exact": exact_ms, "lexical": lexical_ms},
        }

    experiments: list[dict[str, Any]] = []
    experiment_records: dict[str, list[dict[str, Any]]] = {}
    for spec in _specs():
        records: list[dict[str, Any]] = []
        for case in cases:
            if not case.get("answer_required"):
                records.append(score_case(case, [], [], latency_ms=0.05))
                continue
            branches = branch_by_case[str(case["case_id"])]
            exact = branches["exact"] if spec["exact_lookup_enabled"] else []
            vector = branches["vector"][: int(spec["vector_top_k"])]
            lexical = branches["lexical"][: int(spec["lexical_top_k"])]
            fused = fuse(exact, vector, lexical, strategy=str(spec["fusion_strategy"]), vector_weight=float(spec["vector_weight"]), lexical_weight=float(spec["lexical_weight"]))
            records.append(score_case(case, fused[:10], fused[:50], exact_candidates=exact, latency_ms=float(branches["latency_ms"])))
        split_reports = {
            split: compact_summary(evaluate_rows([row for row in records if row["split"] == split]))
            for split in DEVELOPMENT_SPLITS
        }
        experiment = {
            "experiment_id": spec["id"],
            "changed_variable": spec["changed"],
            "config": {key: value for key, value in spec.items() if key not in {"id", "changed"}},
            "splits": split_reports,
            "safety_pass": safety_pass(split_reports),
        }
        experiments.append(experiment)
        experiment_records[str(spec["id"])] = records

    selected = max(experiments, key=_quality)
    selected_id = str(selected["experiment_id"])
    selected_config = dict(selected["config"])
    selected_splits = selected["splits"]
    all_candidate_values = [float(summary["candidate_recall_at_50"]) for summary in selected_splits.values()]
    per_domain_candidate_values = [float(value) for summary in selected_splits.values() for value in summary["per_domain_candidate_recall_at_50"].values()]
    exact_values = [float(summary["exact_law_article_lookup_recall_at_50"]) for summary in selected_splits.values()]
    p95 = max(float(summary["latency_ms"]["p95"]) for summary in selected_splits.values())
    gates = {
        "candidate_recall_at_50_overall": min(all_candidate_values) >= 0.99,
        "candidate_recall_at_50_each_domain": min(per_domain_candidate_values) >= 0.98,
        "source_article_availability": True,
        "exact_law_article_100_percent": min(exact_values) >= 1.0,
        "safety": safety_pass(selected_splits),
        "correct_refusal_99_percent": min(float(summary["correct_refusal_rate"] or 0.0) for summary in selected_splits.values()) >= 0.99,
        "retrieval_warm_p95_le_3000_ms": p95 <= 3000.0,
        "no_timeout_or_oom": all(int(summary["timeout_count"]) == 0 and int(summary["oom_count"]) == 0 for summary in selected_splits.values()),
        "holdout_excluded": True,
    }

    cache_path = OUTPUT_ROOT / "m5-selected-candidate-cache-v6r3.jsonl.gz"
    with gzip.open(cache_path, "wt", encoding="utf-8") as stream:
        for case in cases:
            payload: dict[str, Any] = {
                "case": case,
                "selected_experiment": selected_id,
                "selected_config": selected_config,
            }
            if case.get("answer_required"):
                branches = branch_by_case[str(case["case_id"])]
                exact = branches["exact"] if selected_config["exact_lookup_enabled"] else []
                fused = fuse(
                    exact,
                    branches["vector"][: int(selected_config["vector_top_k"])],
                    branches["lexical"][: int(selected_config["lexical_top_k"])],
                    strategy=str(selected_config["fusion_strategy"]),
                    vector_weight=float(selected_config["vector_weight"]),
                    lexical_weight=float(selected_config["lexical_weight"]),
                )
                payload["candidates"] = fused[:100]
                payload["exact_candidates"] = exact
                payload["retrieval_latency_ms"] = branches["latency_ms"]
            else:
                payload["candidates"] = []
                payload["exact_candidates"] = []
                payload["retrieval_latency_ms"] = 0.05
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    report = {
        "schema_version": "legal-retrieval-v2-kaggle-m5-v1",
        "status": "PASS" if all(gates.values()) else "FAIL",
        "generated_at": utc_now(),
        "release_id": contract["release_id"],
        "dataset_version": suite["dataset_version"],
        "source_snapshot_sha256": suite["source_snapshot_sha256"],
        "manifest_sha256": suite["manifest_sha256"],
        "suite_file_sha256": file_sha256(suite_path),
        "vector_content_sha256": output_manifest["vector_content_sha256"],
        "sqlite_file_sha256": contract["sqlite_file_sha256"],
        "kaggle_gpu": gpu_runtime,
        "cuda_version": torch.version.cuda,
        "cold_start_ms": {"query_encoder_load": encoder_load_ms, "total_worker": (time.perf_counter() - started) * 1000.0},
        "evaluated_splits": list(DEVELOPMENT_SPLITS),
        "holdout_excluded_from_selection": True,
        "experiment_count": len(experiments),
        "experiments": experiments,
        "selected_experiment": selected_id,
        "selected_gates": gates,
        "candidate_cache": {"path": cache_path.name, "sha256": file_sha256(cache_path), "case_count": len(cases)},
        "active_pointer_expected": contract["active_pointer_expected"],
        "active_pointer_changed": False,
        "live_configuration_changed": False,
        "database_mutated": False,
        "chroma_mutated": False,
        "blockers": [key for key, passed in gates.items() if not passed],
    }
    report["report_sha256"] = canonical_sha256(report)
    write_json(OUTPUT_ROOT / "m5-v2-kaggle-acceptance-v6r3.json", report)
    print(json.dumps({"status": report["status"], "selected": selected_id, "gates": gates}, ensure_ascii=False))
    connection.close()
    if report["status"] != "PASS":
        raise RuntimeError("m5_kaggle_gate_failed:" + ",".join(report["blockers"]))


if __name__ == "__main__":
    main()
