#!/usr/bin/env python3
"""Capture the local retrieval runtime and CUDA fingerprint read-only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any
import hashlib


ROOT = Path(__file__).resolve().parents[1]


def _nvidia_smi() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        rows = []
        for line in result.stdout.splitlines():
            values = [item.strip() for item in line.split(",")]
            if len(values) == 5:
                rows.append({"name": values[0], "driver_version": values[1], "memory_total_mib": values[2], "memory_used_mib": values[3], "utilization_percent": values[4]})
        return {"available": True, "gpus": rows}
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def capture(*, prewarm: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "legal-retrieval-runtime-fingerprint-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "requested_embedding_device": os.getenv("LEGAL_EMBED_DEVICE", "auto"),
        "nvidia_smi": _nvidia_smi(),
    }
    try:
        import torch
        report["torch"] = {
            "version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": int(torch.cuda.device_count()),
            "device_names": [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())],
        }
    except Exception as exc:
        report["torch"] = {"error": f"{type(exc).__name__}:{exc}"}
    if prewarm:
        import scripts.legal_search_server as legal_search_server
        retriever = legal_search_server.retriever
        retriever.prewarm()
        model = getattr(retriever, "_model", None)
        report["retriever"] = {
            "model_artifact_fingerprint": getattr(retriever, "_model_fingerprint", None),
            "requested_device": getattr(retriever, "_requested_device", None),
            "active_embedding_device": str(getattr(retriever, "_embedding_device", None)),
            "embedding_dtype": str(next(model.parameters()).dtype) if model is not None else None,
            "model_device": str(next(model.parameters()).device) if model is not None else None,
            "fallback_reason": getattr(retriever, "_embedding_fallback_reason", None),
        }
    report["staging_only"] = True
    report["database_mutated"] = False
    report["active_pointer_changed"] = False
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prewarm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = capture(prewarm=args.prewarm)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(json.dumps({"output": str(output), "sha256": digest, "torch": report.get("torch"), "retriever": report.get("retriever")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
