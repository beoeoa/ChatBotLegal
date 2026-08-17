"""Create an immutable V3 pointer revision for replacement shadow collections.

This operation never changes the live active pointer.  It preserves the legal
dataset/document partition from an already validated V3 manifest and rebinds
only the two vector collection paths plus their verified reports.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256, read_active_collection_pointer
from api.retrieval_serving_manifest_v3 import load_v3_serving_manifest


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--current-report", type=Path, required=True)
    parser.add_argument("--temporal-report", type=Path, required=True)
    parser.add_argument("--ann-readiness", type=Path, required=True)
    parser.add_argument("--active-pointer", type=Path, required=True)
    parser.add_argument("--expected-active-pointer", required=True)
    parser.add_argument("--manifest-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.source_manifest.resolve()
    current_path = args.current_report.resolve()
    temporal_path = args.temporal_report.resolve()
    ann_path = args.ann_readiness.resolve()
    source = dict(load_v3_serving_manifest(source_path).payload)
    current = _load(current_path)
    temporal = _load(temporal_path)
    ann = _load(ann_path)
    release_id = str(source["release_id"])
    source_sha = str(source["source_snapshot_sha256"])
    for label, report, expected_state in (
        ("current", current, "current_retrievable"),
        ("temporal", temporal, "all"),
    ):
        if report.get("valid") is not True:
            raise RuntimeError(f"{label}_collection_report_not_valid")
        if report.get("release_id") != release_id or report.get("source_snapshot_sha256") != source_sha:
            raise RuntimeError(f"{label}_collection_release_binding_mismatch")
        if report.get("document_state") != expected_state:
            raise RuntimeError(f"{label}_collection_state_mismatch")
    if ann.get("status") != "PASS" or ann.get("valid") is not True:
        raise RuntimeError("ann_readiness_required")
    if ann.get("release_id") != release_id or ann.get("source_snapshot_sha256") != source_sha:
        raise RuntimeError("ann_readiness_release_binding_mismatch")
    current_name = str(current.get("target_collection") or "")
    temporal_name = str(temporal.get("target_collection") or "")
    if ann.get("current", {}).get("collection") != current_name:
        raise RuntimeError("ann_current_collection_mismatch")
    if ann.get("temporal", {}).get("collection") != temporal_name:
        raise RuntimeError("ann_temporal_collection_mismatch")
    pointer = read_active_collection_pointer(args.active_pointer.resolve().parent)
    if pointer != args.expected_active_pointer:
        raise RuntimeError("active_pointer_changed")

    source["manifest_version"] = args.manifest_version
    source["current_collection"] = {
        **dict(source["current_collection"]),
        "path": f"chroma://{current_name}",
        "count": int(current["actual_vector_count"]),
    }
    source["temporal_collection"] = {
        **dict(source["temporal_collection"]),
        "path": f"chroma://{temporal_name}",
        "count": int(temporal["actual_vector_count"]),
    }
    source["vector_reports"] = {
        "current": _relative(current_path),
        "temporal": _relative(temporal_path),
    }
    source["vector_report_checksums"] = {
        "current": file_sha256(current_path),
        "temporal": file_sha256(temporal_path),
    }
    source["ann_readiness_report_path"] = _relative(ann_path)
    source["ann_readiness_report_sha256"] = file_sha256(ann_path)
    source["generated_at"] = datetime.now(timezone.utc).isoformat()
    source["activation_performed"] = False
    source["active_pointer_changed"] = False
    source.pop("manifest_sha256", None)
    source["manifest_sha256"] = canonical_sha256(source)

    output = args.output.resolve()
    if output.exists():
        if _load(output) != source:
            raise RuntimeError("serving_manifest_version_already_exists")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    loaded = load_v3_serving_manifest(
        output,
        project_root=ROOT,
        verify_file_artifacts=True,
    )
    report = {
        "status": "PASS",
        "manifest": str(output),
        "manifest_file_sha256": loaded.file_sha256,
        "manifest_sha256": loaded.manifest_sha256,
        "current_collection": loaded.current_collection,
        "temporal_collection": loaded.temporal_collection,
        "active_pointer": pointer,
        "active_pointer_changed": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
