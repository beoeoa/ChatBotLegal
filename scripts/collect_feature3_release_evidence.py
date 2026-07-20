"""Collect create-only, privacy-safe Feature 3 release-build evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHUNK_PATTERN = re.compile(
    r'<script\b(?![^>]*\bnoModule\b)[^>]*\bsrc="(?P<path>/_next/static/chunks/[^"?]+\.js)(?:\?[^"\s]*)?"',
    re.IGNORECASE,
)
SOURCE_ROOTS = ("api", "frontend/src", "open_notebook", "scripts")
SOURCE_FILES = ("Dockerfile", "pyproject.toml", "uv.lock", "frontend/package.json", "frontend/package-lock.json")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def chunk_paths(html: str) -> list[str]:
    return sorted({match.group("path") for match in CHUNK_PATTERN.finditer(html)})


def source_manifest(root: Path) -> str:
    entries: list[str] = []
    for relative in SOURCE_FILES:
        item = root / relative
        if item.is_file():
            entries.append(f"{relative}\0{sha256_bytes(item.read_bytes())}")
    for relative in SOURCE_ROOTS:
        directory = root / relative
        if not directory.is_dir():
            continue
        for item in sorted(path for path in directory.rglob("*") if path.is_file()):
            if "__pycache__" not in item.parts and item.suffix not in {".pyc", ".log"}:
                entries.append(f"{item.relative_to(root).as_posix()}\0{sha256_bytes(item.read_bytes())}")
    return sha256_bytes("\n".join(sorted(entries)).encode("utf-8"))


def fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - operator-supplied pilot URL
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""


def collect(base_url: str, api_base_url: str, image_id: str) -> dict:
    base = base_url.rstrip("/")
    search_status, html_bytes = fetch(f"{base}/search")
    if search_status != 200:
        raise RuntimeError(f"search_route_status_{search_status}")
    chunks = []
    for path in chunk_paths(html_bytes.decode("utf-8", errors="strict")):
        status, content = fetch(urllib.parse.urljoin(base, path))
        if status != 200:
            raise RuntimeError(f"chunk_status_{status}")
        chunks.append({"path": path, "raw_bytes": len(content), "gzip_bytes": len(gzip.compress(content)), "sha256": sha256_bytes(content)})
    ready_status, _ = fetch(f"{api_base_url.rstrip('/')}/ready")
    gzip_total = sum(item["gzip_bytes"] for item in chunks)
    return {
        "schema_version": 1,
        "evidence_scope": "isolated-pilot-release-image",
        "route": "/search",
        "image_id": image_id,
        "source_manifest_sha256": source_manifest(ROOT),
        "search_status": search_status,
        "ready_status": ready_status,
        "first_load_chunk_count": len(chunks),
        "gzip_bytes": gzip_total,
        "gzip_limit_bytes": 409600,
        "bundle_gate_pass": gzip_total <= 409600,
        "chunks": chunks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing_to_overwrite_evidence")
    report = collect(args.base_url, args.api_base_url, args.image_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "chunks"}, ensure_ascii=False))
    return 0 if report["bundle_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
