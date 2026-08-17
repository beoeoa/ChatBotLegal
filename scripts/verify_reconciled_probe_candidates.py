"""Verify reconciled Feature 006 probe candidates without promoting them."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable
import zipfile

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = (
    ROOT / "reports" / "feature006"
    / "form-gap-research-queue-probes-reconciled.json"
)
DEFAULT_REPORT = (
    ROOT / "reports" / "feature006"
    / "reconciled-probe-candidate-verification.json"
)
OFFICIAL_HOST_SUFFIXES = (
    "dichvucong.gov.vn",
    "haiphong.gov.vn",
    "vbpl.vn",
    "moj.gov.vn",
    "bocongan.gov.vn",
    "mps.gov.vn",
    "chinhphu.vn",
    "cdnchinhphu.vn",
    "datafiles.chinhphu.vn",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _is_official_url(value: Any) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(str(value or ""))
    host = str(parsed.hostname or "").casefold().rstrip(".")
    return bool(
        parsed.scheme == "https"
        and host
        and any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in OFFICIAL_HOST_SUFFIXES
        )
    )


def _resolve_local_path(value: Any, *, root: Path) -> Path | None:
    relative = Path(str(value or ""))
    if not str(value or "").strip() or relative.is_absolute():
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _find_soffice(explicit: Path | None = None) -> Path | None:
    candidates = [
        explicit,
        Path(shutil.which("soffice") or ""),
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ]
    return next(
        (
            candidate
            for candidate in candidates
            if candidate is not None
            and str(candidate)
            and candidate.is_file()
        ),
        None,
    )


def _verify_openable(
    path: Path,
    *,
    soffice_path: Path | None = None,
) -> tuple[bool, str, str]:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".pdf":
            if not path.read_bytes()[:5] == b"%PDF-":
                return False, "PDF_MAGIC_INVALID", "pdf"
            if len(PdfReader(str(path), strict=False).pages) < 1:
                return False, "PDF_EMPTY", "pdf"
            return True, "OPENABLE", "pdf"
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            if (
                "[Content_Types].xml" not in names
                or "word/document.xml" not in names
            ):
                return False, "DOCX_STRUCTURE_INVALID", "docx"
            from docx import Document

            Document(str(path))
            return True, "OPENABLE", "docx"
        if suffix == ".doc":
            if path.read_bytes()[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
                return False, "DOC_MAGIC_INVALID", "doc"
            soffice = _find_soffice(soffice_path)
            if soffice is None:
                return False, "DOC_OPENABILITY_TOOL_UNAVAILABLE", "doc"
            with tempfile.TemporaryDirectory(
                prefix="feature006-doc-openability-"
            ) as temporary:
                completed = subprocess.run(
                    [
                        str(soffice),
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        temporary,
                        str(path),
                    ],
                    capture_output=True,
                    timeout=45,
                    check=False,
                )
                output = Path(temporary) / f"{path.stem}.pdf"
                if (
                    completed.returncode != 0
                    or not output.is_file()
                    or len(PdfReader(str(output), strict=False).pages) < 1
                ):
                    return False, "DOC_OPENABILITY_FAILED", "doc"
            return True, "OPENABLE", "doc"
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ):
        return False, "ARTIFACT_OPENABILITY_FAILED", suffix.lstrip(".")
    return False, "ARTIFACT_FORMAT_UNSUPPORTED", suffix.lstrip(".")


def verify_candidates(
    *,
    queue_path: Path,
    probe_dirs: Iterable[Path],
    root: Path = ROOT,
    soffice_path: Path | None = None,
) -> dict[str, Any]:
    queue = _read(queue_path)
    candidates = [
        item
        for item in queue.get("records") or []
        if item.get("research_action")
        == "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION"
    ]
    probe_items: list[dict[str, Any]] = []
    probe_checksums: list[dict[str, str]] = []
    for probe_dir in probe_dirs:
        resolution_path = probe_dir / "code-resolution.json"
        payload = _read(resolution_path)
        probe_checksums.append(
            {
                "probe": probe_dir.name,
                "code_resolution_sha256": _sha256(resolution_path),
            }
        )
        probe_items.extend(
            item
            for item in payload.get("pending_records") or []
            if isinstance(item, dict)
        )

    by_sha: dict[str, list[dict[str, Any]]] = {}
    for item in probe_items:
        sha = str(item.get("sha256") or "").casefold()
        if len(sha) == 64:
            by_sha.setdefault(sha, []).append(item)

    checks: list[dict[str, Any]] = []
    format_counts: Counter[str] = Counter()
    for candidate in candidates:
        evidence = candidate.get("technical_evidence") or {}
        identity_id = str(candidate.get("requirement_identity_id") or "")
        expected_sha = str(evidence.get("sha256") or "").casefold()
        matching = by_sha.get(expected_sha, [])
        failures: list[str] = []
        if not matching:
            failures.append("PROBE_RECORD_NOT_FOUND")
        if (
            candidate.get("approved") is not False
            or candidate.get("runtime_eligible") is not False
            or evidence.get("approved") is not False
            or evidence.get("runtime_eligible") is not False
        ):
            failures.append("CANDIDATE_PROMOTION_FORBIDDEN")
        if evidence.get("human_attestation_required") is not True:
            failures.append("HUMAN_ATTESTATION_GATE_MISSING")
        if not _is_official_url(evidence.get("source_page_url")):
            failures.append("OFFICIAL_SOURCE_PAGE_INVALID")
        if not _is_official_url(evidence.get("source_download_url")):
            failures.append("OFFICIAL_DOWNLOAD_URL_INVALID")
        effectivity_source_ok = _is_official_url(
            evidence.get("effectivity_source_url")
        )
        if (
            not effectivity_source_ok
            and not (
                evidence.get("effectivity_reason_code") == "CURRENT_AS_OF_DATE"
                and _is_official_url(evidence.get("source_page_url"))
                and bool(str(evidence.get("effective_from") or "").strip())
            )
        ):
            failures.append("EFFECTIVITY_SOURCE_URL_INVALID")

        valid_artifacts: list[tuple[Path, str, int]] = []
        artifact_failures: set[str] = set()
        for item in matching:
            local_path = _resolve_local_path(item.get("local_path"), root=root)
            if local_path is None or not local_path.is_file():
                artifact_failures.add("LOCAL_ARTIFACT_MISSING")
                continue
            if _sha256(local_path) != expected_sha:
                artifact_failures.add("ARTIFACT_CHECKSUM_MISMATCH")
                continue
            expected_size = int(item.get("size_bytes") or 0)
            if expected_size and local_path.stat().st_size != expected_size:
                artifact_failures.add("ARTIFACT_SIZE_MISMATCH")
                continue
            openable, reason, file_format = _verify_openable(
                local_path,
                soffice_path=soffice_path,
            )
            if not openable:
                artifact_failures.add(reason)
                continue
            valid_artifacts.append(
                (local_path, file_format, local_path.stat().st_size)
            )
        if not valid_artifacts:
            failures.extend(sorted(artifact_failures or {"ARTIFACT_NOT_VERIFIED"}))
            file_format = "unknown"
            size_bytes = 0
        else:
            _, file_format, size_bytes = valid_artifacts[0]
            format_counts[file_format] += 1
        checks.append(
            {
                "requirement_identity_id": identity_id,
                "sha256": expected_sha,
                "file_format": file_format,
                "size_bytes": size_bytes,
                "status": "PASS" if not failures else "FAIL",
                "reason_codes": sorted(set(failures)),
            }
        )

    failed = sum(item["status"] == "FAIL" for item in checks)
    report = {
        "schema_version": "feature006-reconciled-probe-verification-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": queue.get("legal_as_of"),
        "queue_sha256": _sha256(queue_path),
        "probe_checksums": sorted(
            probe_checksums,
            key=lambda item: item["probe"],
        ),
        "candidate_count": len(checks),
        "passed_count": len(checks) - failed,
        "failed_count": failed,
        "file_format_counts": dict(sorted(format_counts.items())),
        "checks": checks,
        "candidate_only": queue.get("candidate_only") is True,
        "automated_approval": queue.get("automated_approval") is True,
        "human_attestation_created": (
            queue.get("human_attestation_created") is True
        ),
        "runtime_eligible_count": (
            (queue.get("summary") or {}).get("runtime_eligible_count")
        ),
        "feature_flag_enabled": queue.get("feature_flag_enabled") is True,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }
    report["technical_pass"] = bool(
        checks
        and failed == 0
        and report["candidate_only"] is True
        and report["automated_approval"] is False
        and report["human_attestation_created"] is False
        and report["runtime_eligible_count"] == 0
        and report["feature_flag_enabled"] is False
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--probe-dir", type=Path, action="append", required=True)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--soffice", type=Path)
    args = parser.parse_args()
    report = verify_candidates(
        queue_path=args.queue.resolve(),
        probe_dirs=[path.resolve() for path in args.probe_dir],
        soffice_path=args.soffice.resolve() if args.soffice else None,
    )
    _write_atomic(args.report.resolve(), report)
    print(
        json.dumps(
            {
                "status": "PASS" if report["technical_pass"] else "FAIL",
                "candidate_count": report["candidate_count"],
                "passed_count": report["passed_count"],
                "failed_count": report["failed_count"],
                "file_format_counts": report["file_format_counts"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["technical_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
