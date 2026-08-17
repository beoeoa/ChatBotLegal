"""Candidate-only official form crawler for the canonical catalog.

The crawler never approves a form and never mutates the canonical catalogs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES, fold_text


FORMS_DIR = ROOT / "notebook_data" / "forms"
REPORT_DIR = ROOT / "reports" / "feature005" / "forms-completion-20260724"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "canonical_candidates_v1"
USER_AGENT = "ChatBotLegal-FormCatalog/1.0 (+local legal data maintenance)"
ALLOWED_EXTENSIONS = {".doc", ".docx", ".pdf", ".xls", ".xlsx"}
MACRO_EXTENSIONS = {".docm", ".xlsm", ".pptm"}


def is_allowed_official_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.casefold().rstrip(".")
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in OFFICIAL_HOST_SUFFIXES
    )


def validate_download(
    content: bytes,
    content_type: str | None,
    url: str,
) -> tuple[bool, str, str | None]:
    if len(content) < 256:
        return False, "FILE_TOO_SMALL", None
    extension = Path(urlparse(url).path).suffix.casefold()
    mime = str(content_type or "").split(";", 1)[0].strip().casefold()
    if extension in MACRO_EXTENSIONS or "macroenabled" in mime:
        return False, "MACRO_FILE_QUARANTINED", extension.lstrip(".") or None
    head = content[:512]
    lower = head.lower()
    if b"<html" in lower or b"<!doctype html" in lower:
        return False, "HTML_DISGUISED_AS_FILE", extension.lstrip(".") or None
    if extension not in ALLOWED_EXTENSIONS:
        return False, "UNSUPPORTED_FILE_TYPE", extension.lstrip(".") or None
    fmt = extension.lstrip(".")
    if fmt == "pdf" and not head.startswith(b"%PDF"):
        return False, "MAGIC_BYTES_MISMATCH", fmt
    if fmt == "doc" and not head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return False, "MAGIC_BYTES_MISMATCH", fmt
    if fmt in {"docx", "xlsx"}:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return False, "MAGIC_BYTES_MISMATCH", fmt
        required = "word/" if fmt == "docx" else "xl/"
        if "[Content_Types].xml" not in names or not any(
            name.startswith(required) for name in names
        ):
            return False, "MAGIC_BYTES_MISMATCH", fmt
    return True, "VALID", fmt


def candidate_from_download(
    *,
    requirement_id: str,
    procedure_id: str,
    canonical_name: str,
    source_page: str,
    download_url: str,
    content: bytes,
    content_type: str | None,
    download_dir: Path = DOWNLOAD_DIR,
) -> dict[str, Any]:
    valid, reason, file_format = validate_download(
        content,
        content_type,
        download_url,
    )
    if not valid:
        return {
            "requirement_id": requirement_id,
            "procedure_id": procedure_id,
            "status": "BLOCKED_EXTERNAL",
            "reason_code": reason,
            "source_page": source_page,
            "download_url": download_url,
            "review_status": "candidate_pending_review",
            "approved": False,
        }
    digest = hashlib.sha256(content).hexdigest()
    extension = f".{file_format}" if file_format else ".bin"
    filename = f"{digest[:24]}{extension}"
    download_dir.mkdir(parents=True, exist_ok=True)
    path = download_dir / filename
    if not path.exists():
        path.write_bytes(content)
    return {
        "requirement_id": requirement_id,
        "procedure_id": procedure_id,
        "canonical_name": canonical_name,
        "status": "AVAILABLE_OFFICIAL_FILE",
        "reason_code": "DOWNLOADED_PENDING_REVIEW",
        "source_page": source_page,
        "download_url": download_url,
        "file_name": filename,
        "local_path": str(path.relative_to(ROOT)).replace("\\", "/")
        if path.is_relative_to(ROOT)
        else None,
        "file_format": file_format,
        "sha256": digest,
        "size_bytes": len(content),
        "review_status": "candidate_pending_review",
        "approved": False,
    }


def _extract_links(html: str, base_url: str) -> list[str]:
    links: set[str] = set()
    for match in re.finditer(
        r"(?:href|src)=[\"']([^\"']+)[\"']",
        html,
        flags=re.IGNORECASE,
    ):
        link = urljoin(base_url, match.group(1))
        suffix = Path(urlparse(link).path).suffix.casefold()
        if suffix in ALLOWED_EXTENSIONS | MACRO_EXTENSIONS:
            links.add(link)
    return sorted(link for link in links if is_allowed_official_url(link))


def _score_link(name: str, code: str | None, link: str) -> tuple[int, str]:
    folded_link = fold_text(urlparse(link).path)
    if code and fold_text(code) in folded_link:
        return (1000 + len(fold_text(code)), link)
    name_tokens = set(fold_text(name).split())
    link_tokens = set(folded_link.split())
    overlap = len(name_tokens & link_tokens)
    return (overlap * 10, link)


def _robots_allowed(client: httpx.Client, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = client.get(robots_url, timeout=10)
        if response.status_code >= 400:
            return True
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except httpx.HTTPError:
        return True


def crawl(
    *,
    max_items: int | None = None,
    network: bool = True,
    rate_limit_seconds: float = 0.25,
) -> dict[str, Any]:
    requirements_payload = json.loads(
        (FORMS_DIR / "canonical_form_requirements_v1.json").read_text(
            encoding="utf-8"
        )
    )
    procedures_payload = json.loads(
        (FORMS_DIR / "canonical_procedures_v1.json").read_text(
            encoding="utf-8"
        )
    )
    forms_payload = json.loads(
        (FORMS_DIR / "canonical_forms_catalog_v1.json").read_text(
            encoding="utf-8"
        )
    )
    procedures = {
        item["procedure_id"]: item
        for item in procedures_payload["procedures"]
    }
    forms_by_requirement = {
        requirement_id: form
        for form in forms_payload["forms"]
        for requirement_id in form.get("source_requirement_ids") or []
    }
    requirements = list(requirements_payload["requirements"])
    if max_items is not None:
        requirements = requirements[: max(0, int(max_items))]

    results: list[dict[str, Any]] = []
    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=20,
    )
    try:
        for requirement in requirements:
            if not requirement.get("runtime_eligible"):
                results.append(
                    {
                        "requirement_id": requirement["requirement_id"],
                        "procedure_id": requirement.get("procedure_id"),
                        "canonical_name": requirement.get("form_title"),
                        "status": "OUT_OF_SCOPE",
                        "reason_code": requirement.get("classification"),
                        "review_status": "not_applicable",
                        "approved": False,
                    }
                )
                continue
            procedure = procedures.get(requirement["procedure_id"], {})
            form = forms_by_requirement.get(requirement["requirement_id"], {})
            source_page = str(
                form.get("official_source_page")
                or procedure.get("official_procedure_url")
                or ""
            )
            base = {
                "requirement_id": requirement["requirement_id"],
                "procedure_id": requirement["procedure_id"],
                "canonical_name": form.get("canonical_name")
                or requirement["form_title"],
                "review_status": "candidate_pending_review",
                "approved": False,
            }
            if not source_page:
                results.append(
                    {
                        **base,
                        "status": "NEEDS_SOURCE_MAPPING",
                        "reason_code": "OFFICIAL_PROCEDURE_URL_MISSING",
                    }
                )
                continue
            if not is_allowed_official_url(source_page):
                results.append(
                    {
                        **base,
                        "status": "OUT_OF_SCOPE",
                        "reason_code": "SOURCE_HOST_NOT_ALLOWED",
                        "source_page": source_page,
                    }
                )
                continue
            if not network:
                results.append(
                    {
                        **base,
                        "status": "OFFICIAL_PACKAGE_PAGE",
                        "reason_code": "NETWORK_NOT_PROBED",
                        "source_page": source_page,
                    }
                )
                continue
            if not _robots_allowed(client, source_page):
                results.append(
                    {
                        **base,
                        "status": "BLOCKED_EXTERNAL",
                        "reason_code": "ROBOTS_DISALLOWED",
                        "source_page": source_page,
                    }
                )
                continue
            try:
                response = client.get(source_page)
            except httpx.HTTPError as exc:
                results.append(
                    {
                        **base,
                        "status": "BLOCKED_EXTERNAL",
                        "reason_code": type(exc).__name__,
                        "source_page": source_page,
                    }
                )
                continue
            body_folded = fold_text(response.text[:20000])
            if response.status_code in {401, 403, 429} or any(
                marker in body_folded
                for marker in ("captcha", "access denied", "dang nhap")
            ):
                results.append(
                    {
                        **base,
                        "status": "BLOCKED_EXTERNAL",
                        "reason_code": f"HTTP_{response.status_code}_OR_ANTI_BOT",
                        "source_page": source_page,
                    }
                )
                continue
            links = _extract_links(response.text, str(response.url))
            if not links:
                results.append(
                    {
                        **base,
                        "status": "NO_PUBLIC_DOWNLOAD_VERIFIED",
                        "reason_code": "OFFICIAL_PAGE_HAS_NO_FILE_LINK",
                        "source_page": source_page,
                    }
                )
                continue
            best_link = max(
                links,
                key=lambda link: _score_link(
                    str(base["canonical_name"]),
                    form.get("form_code"),
                    link,
                ),
            )
            try:
                downloaded = client.get(best_link)
                downloaded.raise_for_status()
                result = candidate_from_download(
                    requirement_id=base["requirement_id"],
                    procedure_id=base["procedure_id"],
                    canonical_name=str(base["canonical_name"]),
                    source_page=source_page,
                    download_url=str(downloaded.url),
                    content=downloaded.content,
                    content_type=downloaded.headers.get("content-type"),
                )
            except httpx.HTTPError as exc:
                result = {
                    **base,
                    "status": "BLOCKED_EXTERNAL",
                    "reason_code": type(exc).__name__,
                    "source_page": source_page,
                    "download_url": best_link,
                }
            results.append(result)
            time.sleep(max(0.0, rate_limit_seconds))
    finally:
        client.close()

    status_counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_only": True,
        "auto_approved_count": 0,
        "requirement_count": len(requirements),
        "result_count": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "unknown_count": status_counts.get("UNKNOWN", 0),
        "network_enabled": network,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "f2-crawl-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (REPORT_DIR / "f2-download-results.jsonl").open(
        "w",
        encoding="utf-8",
    ) as stream:
        for item in results:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (REPORT_DIR / "f2-unavailable-sources.jsonl").open(
        "w",
        encoding="utf-8",
    ) as stream:
        for item in results:
            if item["status"] != "AVAILABLE_OFFICIAL_FILE":
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    return {"manifest": manifest, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crawl official canonical form candidates"
    )
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--rate-limit", type=float, default=0.25)
    args = parser.parse_args()
    result = crawl(
        max_items=args.max_items,
        network=not args.no_network,
        rate_limit_seconds=args.rate_limit,
    )
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0 if result["manifest"]["unknown_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
