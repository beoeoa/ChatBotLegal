"""Package immutable Stage E/M5 inputs and a private Kaggle kernel bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import file_sha256


RELEASE_DIR = ROOT / "reports" / "retrieval-release-v2"


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(*, force: bool = False) -> dict:
    dataset_dir = ROOT / "kaggle" / "retrieval-v2" / "benchmark-dataset-v6r3"
    kernel_dir = ROOT / "kaggle" / "retrieval-v2" / "m5-kernel-v6r3"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    kernel_dir.mkdir(parents=True, exist_ok=True)

    sqlite_source = RELEASE_DIR / "legal-retrieval-v2-exact-lexical-v6r1.sqlite3"
    suite_source = RELEASE_DIR / "retrieval-eval-suite-v1-development-v6r3.json"
    stage_e_source = RELEASE_DIR / "stage-e-answer-enrichment-final-v6r3.json"
    serving_source = RELEASE_DIR / "legal-serving-manifest-v3-v6r2-ann.json"
    stage_c_manifest = ROOT / "kaggle" / "retrieval-v2" / "output-v6r1-final" / "embedding-output-manifest.json"
    active_pointer = ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt"
    required = [sqlite_source, suite_source, stage_e_source, serving_source, stage_c_manifest, active_pointer]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("m5_package_input_missing:" + ",".join(missing))

    archive = dataset_dir / "legal-retrieval-v2-exact-lexical-v6r1.sqlite3.zip"
    if force or not archive.is_file():
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            bundle.write(sqlite_source, arcname=sqlite_source.name)
    for source in (suite_source, stage_e_source, serving_source):
        shutil.copy2(source, dataset_dir / source.name)
    common_source = ROOT / "scripts" / "kaggle_retrieval_v2_benchmark_common.py"
    shutil.copy2(common_source, dataset_dir / common_source.name)

    stage_c = json.loads(stage_c_manifest.read_text(encoding="utf-8"))
    suite = json.loads(suite_source.read_text(encoding="utf-8"))
    serving = json.loads(serving_source.read_text(encoding="utf-8"))
    contract = {
        "schema_version": "legal-retrieval-v2-kaggle-m5-contract-v1",
        "release_id": serving["release_id"],
        "dataset_version": suite["dataset_version"],
        "suite_name": suite_source.name,
        "suite_sha256": file_sha256(suite_source),
        "source_snapshot_sha256": suite["source_snapshot_sha256"],
        "manifest_sha256": suite["manifest_sha256"],
        "sqlite_archive_name": archive.name,
        "sqlite_archive_sha256": file_sha256(archive),
        "sqlite_file_name": sqlite_source.name,
        "sqlite_file_sha256": file_sha256(sqlite_source),
        "benchmark_common_name": common_source.name,
        "benchmark_common_sha256": file_sha256(common_source),
        "stage_c_input_manifest_sha256": stage_c["input_manifest_sha256"],
        "stage_c_output_manifest_sha256": file_sha256(stage_c_manifest),
        "vector_content_sha256": stage_c["vector_content_sha256"],
        "vector_count": stage_c["vector_count"],
        "embedding_dimension": stage_c["embedding_dimension"],
        "model_artifact_fingerprint": stage_c["model_artifact_fingerprint"],
        "tokenizer_fingerprint": stage_c["tokenizer_fingerprint"],
        "embedding_recipe_fingerprint": stage_c["embedding_recipe_fingerprint"],
        "active_pointer_expected": active_pointer.read_text(encoding="utf-8").strip(),
        "holdout_mounted": False,
        "internet_enabled": False,
    }
    _write(dataset_dir / "kaggle-m5-contract.json", contract)
    _write(
        dataset_dir / "dataset-metadata.json",
        {
            "title": "Legal Retrieval V2 Benchmark V6R3 20260817",
            "id": "phconc/legal-retrieval-v2-benchmark-v6r3-20260817",
            "licenses": [{"name": "other"}],
            "isPrivate": True,
        },
    )

    for name in ("kaggle_retrieval_v2_benchmark_common.py", "kaggle_retrieval_v2_m5_worker.py"):
        shutil.copy2(ROOT / "scripts" / name, kernel_dir / name)
    _write(
        kernel_dir / "kernel-metadata.json",
        {
            "id": "phconc/legal-retrieval-v2-m5-v6r3-20260817",
            "title": "Legal Retrieval V2 M5 V6R3 20260817",
            "code_file": "kaggle_retrieval_v2_m5_worker.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": False,
            "accelerator": "NvidiaTeslaT4",
            "dataset_sources": [
                "phconc/legal-retrieval-v2-benchmark-v6r3-20260817",
                "phconc/vnlegal-lal-pinned-29020c",
                "phconc/legal-rag-torch-251-cu121-p100-t4",
            ],
            "kernel_sources": ["phconc/legal-retrieval-v2-embedding-v6r1-20260816"],
            "competition_sources": [],
        },
    )
    report = {
        "status": "PASS",
        "dataset_dir": str(dataset_dir),
        "kernel_dir": str(kernel_dir),
        "contract": contract,
        "dataset_files": {
            path.name: {"size_bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in sorted(dataset_dir.iterdir())
            if path.is_file()
        },
        "holdout_included": False,
    }
    _write(RELEASE_DIR / "kaggle-m5-package-v6r3.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = prepare(force=args.force)
    print(json.dumps({"status": report["status"], "dataset_files": report["dataset_files"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
