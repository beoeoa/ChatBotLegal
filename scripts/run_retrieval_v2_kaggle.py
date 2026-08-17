#!/usr/bin/env python3
"""Prepare and operate the official Kaggle CLI workflow for V2 embedding."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "kaggle_retrieval_v2_worker.py"


def build_dataset_metadata(*, dataset_id: str, title: str) -> dict[str, Any]:
    if "/" not in dataset_id:
        raise ValueError("kaggle_ids_must_use_owner_slug")
    return {
        "id": dataset_id,
        "title": title,
        "isPrivate": True,
        "licenses": [{"name": "other"}],
    }


def build_kernel_metadata(
    *,
    kernel_id: str,
    title: str,
    input_dataset: str,
    model_dataset: str,
) -> dict[str, Any]:
    if "/" not in kernel_id or "/" not in input_dataset or "/" not in model_dataset:
        raise ValueError("kaggle_ids_must_use_owner_slug")
    return {
        "id": kernel_id,
        "title": title,
        "retrieval_release_contract": "legal-retrieval-kaggle-kernel-v2",
        "kernel_version": "retrieval-v2-kernel-bundle-v2",
        "code_file": WORKER.name,
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": False,
        "accelerator": "NvidiaTeslaT4",
        "dataset_sources": [input_dataset, model_dataset],
        "kernel_sources": [],
        "competition_sources": [],
    }


def _credential_state() -> dict[str, Any]:
    config = Path.home() / ".kaggle" / "kaggle.json"
    access_token = Path.home() / ".kaggle" / "access_token"
    return {
        "kaggle_cli": _kaggle_cli(),
        "config_file_present": config.is_file(),
        "access_token_file_present": access_token.is_file(),
        "api_token_env_present": bool(os.getenv("KAGGLE_API_TOKEN")),
        "legacy_env_present": bool(os.getenv("KAGGLE_USERNAME") and os.getenv("KAGGLE_KEY")),
    }


def _kaggle_cli() -> str | None:
    observed = shutil.which("kaggle")
    if observed:
        return observed
    sibling = Path(sys.executable).resolve().parent / (
        "kaggle.exe" if os.name == "nt" else "kaggle"
    )
    return str(sibling) if sibling.is_file() else None


def prepare_dataset_metadata(
    *, data_dir: Path, dataset_id: str, title: str
) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    metadata = build_dataset_metadata(dataset_id=dataset_id, title=title)
    path = data_dir / "dataset-metadata.json"
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def _copy_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def prepare_model_dataset_bundle(
    *,
    model_path: Path,
    bundle_dir: Path,
    dataset_id: str,
    title: str,
) -> dict[str, Any]:
    if not model_path.is_dir():
        raise RuntimeError(f"model_directory_missing:{model_path}")
    destination = bundle_dir / "model"
    if destination.exists():
        raise RuntimeError(f"model_bundle_exists:{destination}")
    for source in sorted(model_path.rglob("*")):
        if source.is_file():
            relative = source.relative_to(model_path)
            _copy_or_link(source, destination / relative)
    return prepare_dataset_metadata(
        data_dir=bundle_dir, dataset_id=dataset_id, title=title
    )


def _reject_credential_files(data_dir: Path) -> None:
    forbidden = {"kaggle.json", ".env", ".env.release"}
    matches = [
        path for path in data_dir.rglob("*") if path.is_file() and path.name.casefold() in forbidden
    ]
    if matches:
        raise RuntimeError(f"credential_file_in_upload_bundle:{matches[0]}")


def prepare_bundle(
    *,
    bundle_dir: Path,
    kernel_id: str,
    title: str,
    input_dataset: str,
    model_dataset: str,
) -> dict[str, Any]:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    metadata = build_kernel_metadata(
        kernel_id=kernel_id,
        title=title,
        input_dataset=input_dataset,
        model_dataset=model_dataset,
    )
    shutil.copy2(WORKER, bundle_dir / WORKER.name)
    (bundle_dir / "kernel-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def _run(command: list[str]) -> int:
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--bundle-dir", type=Path, required=True)
    prepare.add_argument("--kernel-id", required=True)
    prepare.add_argument("--title", default="Legal Retrieval V2 embedding")
    prepare.add_argument("--input-dataset", required=True)
    prepare.add_argument("--model-dataset", required=True)

    dataset_metadata = sub.add_parser("dataset-metadata")
    dataset_metadata.add_argument("--data-dir", type=Path, required=True)
    dataset_metadata.add_argument("--dataset-id", required=True)
    dataset_metadata.add_argument("--title", required=True)

    stage_model = sub.add_parser("stage-model-dataset")
    stage_model.add_argument("--model-path", type=Path, required=True)
    stage_model.add_argument("--bundle-dir", type=Path, required=True)
    stage_model.add_argument("--dataset-id", required=True)
    stage_model.add_argument("--title", required=True)

    publish_dataset = sub.add_parser("publish-dataset")
    publish_dataset.add_argument("--data-dir", type=Path, required=True)
    publish_dataset.add_argument("--version", action="store_true")
    publish_dataset.add_argument("--message", default="Retrieval V2 checksum-bound artifact")

    submit = sub.add_parser("submit")
    submit.add_argument("--bundle-dir", type=Path, required=True)
    submit.add_argument("--accelerator", default="NvidiaTeslaT4")

    status = sub.add_parser("status")
    status.add_argument("--kernel-id", required=True)

    download = sub.add_parser("download")
    download.add_argument("--kernel-id", required=True)
    download.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "preflight":
        state = _credential_state()
        state["ready"] = bool(
            state["kaggle_cli"]
            and (
                state["config_file_present"]
                or state["access_token_file_present"]
                or state["api_token_env_present"]
                or state["legacy_env_present"]
            )
        )
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state["ready"] else 2
    if args.command == "prepare":
        metadata = prepare_bundle(
            bundle_dir=args.bundle_dir.resolve(),
            kernel_id=args.kernel_id,
            title=args.title,
            input_dataset=args.input_dataset,
            model_dataset=args.model_dataset,
        )
        print(json.dumps({"status": "PREPARED", "metadata": metadata}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "dataset-metadata":
        metadata = prepare_dataset_metadata(
            data_dir=args.data_dir.resolve(),
            dataset_id=args.dataset_id,
            title=args.title,
        )
        print(json.dumps({"status": "DATASET_METADATA_PREPARED", "metadata": metadata}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "stage-model-dataset":
        metadata = prepare_model_dataset_bundle(
            model_path=args.model_path.resolve(),
            bundle_dir=args.bundle_dir.resolve(),
            dataset_id=args.dataset_id,
            title=args.title,
        )
        print(json.dumps({"status": "MODEL_DATASET_STAGED", "metadata": metadata}, ensure_ascii=False, indent=2))
        return 0

    kaggle_cli = _kaggle_cli()
    if kaggle_cli is None:
        print(json.dumps({"status": "BLOCKED", "reason": "kaggle_cli_missing"}))
        return 2
    if args.command == "publish-dataset":
        data_dir = args.data_dir.resolve()
        _reject_credential_files(data_dir)
        metadata_path = data_dir / "dataset-metadata.json"
        if not metadata_path.is_file():
            print(json.dumps({"status": "BLOCKED", "reason": "dataset_metadata_missing"}))
            return 2
        if args.version:
            return _run(
                [
                    kaggle_cli,
                    "datasets",
                    "version",
                    "-p",
                    str(data_dir),
                    "-m",
                    args.message,
                    "--dir-mode",
                    "zip",
                ]
            )
        return _run(
            [
                kaggle_cli,
                "datasets",
                "create",
                "-p",
                str(data_dir),
                "--dir-mode",
                "zip",
            ]
        )
    if args.command == "submit":
        return _run(
            [
                kaggle_cli,
                "kernels",
                "push",
                "-p",
                str(args.bundle_dir.resolve()),
                "--accelerator",
                args.accelerator,
            ]
        )
    if args.command == "status":
        return _run([kaggle_cli, "kernels", "status", args.kernel_id])
    if args.command == "download":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        return _run(
            [
                kaggle_cli,
                "kernels",
                "output",
                args.kernel_id,
                "-p",
                str(args.output_dir.resolve()),
            ]
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
