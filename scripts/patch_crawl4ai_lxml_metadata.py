"""Apply the reviewed Crawl4AI 0.8.9/lxml 6 metadata compatibility patch.

Crawl4AI 0.8.9 still publishes ``lxml~=5.3`` while this project also needs
``lxml-html-clean>=0.4.5``, whose current wheel requires lxml 6.1.1 or newer.
The crawler adapter is covered by the repository smoke/regression tests with
lxml 6. This script changes only Crawl4AI's dependency metadata and updates
the wheel RECORD so ``pip check`` can remain a fail-closed image gate.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import site
from typing import Any


EXPECTED_VERSION = "0.8.9"
OLD_REQUIREMENT = "Requires-Dist: lxml~=5.3"
NEW_REQUIREMENT = "Requires-Dist: lxml<7,>=6.1.1"


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return f"sha256={digest.decode('ascii').rstrip('=')}"


def patch_crawl4ai_metadata(site_packages: Path) -> dict[str, Any]:
    site_packages = site_packages.resolve()
    candidates = sorted(site_packages.glob("crawl4ai-*.dist-info"))
    if len(candidates) != 1:
        raise RuntimeError("expected exactly one Crawl4AI dist-info directory")
    dist_info = candidates[0]
    metadata = dist_info / "METADATA"
    record = dist_info / "RECORD"
    if not metadata.is_file() or not record.is_file():
        raise RuntimeError("Crawl4AI wheel metadata or RECORD is missing")

    original_text = metadata.read_text(encoding="utf-8")
    if f"Version: {EXPECTED_VERSION}" not in original_text:
        raise RuntimeError("Crawl4AI version does not match reviewed compatibility patch")
    old_count = original_text.count(OLD_REQUIREMENT)
    new_count = original_text.count(NEW_REQUIREMENT)
    if old_count == 1 and new_count == 0:
        updated_text = original_text.replace(OLD_REQUIREMENT, NEW_REQUIREMENT, 1)
        metadata.write_text(updated_text, encoding="utf-8", newline="")
        status = "patched"
    elif old_count == 0 and new_count == 1:
        status = "already_patched"
    else:
        raise RuntimeError("Crawl4AI lxml dependency metadata has unexpected shape")

    metadata_bytes = metadata.read_bytes()
    rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
    relative_metadata = metadata.relative_to(site_packages).as_posix()
    matches = [row for row in rows if row and row[0] == relative_metadata]
    if len(matches) != 1:
        raise RuntimeError("Crawl4AI RECORD does not contain one METADATA entry")
    matches[0][1:] = [_record_hash(metadata_bytes), str(len(metadata_bytes))]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    record.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    return {
        "status": status,
        "crawl4ai_version": EXPECTED_VERSION,
        "lxml_constraint": ">=6.1.1,<7",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site-packages",
        type=Path,
        default=Path(site.getsitepackages()[0]),
    )
    args = parser.parse_args()
    result = patch_crawl4ai_metadata(args.site_packages)
    print(
        "Crawl4AI lxml metadata compatibility: "
        f"{result['status']} ({result['crawl4ai_version']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
