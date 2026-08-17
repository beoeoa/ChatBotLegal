"""Deterministic Release Gate for Feature 017 form manifests.

The validator never changes the active pointer.  Network verification is an
optional injected step so unit tests and local shadow mode remain deterministic;
the configured PostgreSQL service enables it explicitly through environment.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID

from api.form_governance_models import canonical_sha256
from api.official_source_adapters import is_allowlisted_official_url


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OWNER_DEFERRED_REASONS = {
    "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
    "USER_EXCLUDED_PACKAGE_REVIEW_FROM_CURRENT_RELEASE",
}
SourceVerifier = Callable[[Mapping[str, Any]], str | None]
DVC_ATTACHMENT_ENDPOINT = "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment"
DVC_ORIGIN = "https://dichvucong.gov.vn"
DVC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _effective(item: Mapping[str, Any], legal_as_of: date) -> bool:
    start_raw = str(item.get("effective_from") or "").strip()
    end_raw = str(item.get("effective_to") or "").strip()
    start = _parse_date(start_raw)
    end = _parse_date(end_raw)
    if start_raw and start is None:
        return False
    if end_raw and end is None:
        return False
    return not ((start and legal_as_of < start) or (end and legal_as_of > end))


def validate_release_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_manifest_sha256: str,
    source_verifier: SourceVerifier | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if manifest.get("schema_version") != "form-release-v1":
        errors.append("FORM_RELEASE_MANIFEST_INVALID")
    if canonical_sha256(manifest) != expected_manifest_sha256:
        errors.append("FORM_RELEASE_MANIFEST_TAMPERED")
    if not SHA256_RE.fullmatch(str(manifest.get("source_snapshot_sha256") or "")):
        errors.append("FORM_SOURCE_SNAPSHOT_INVALID")
    legal_as_of = _parse_date(manifest.get("legal_as_of"))
    if legal_as_of is None:
        errors.append("FORM_LEGAL_AS_OF_INVALID")
        legal_as_of = date.min

    procedures = list(manifest.get("procedures") or [])
    assets = list(manifest.get("assets") or [])
    bindings = list(manifest.get("bindings") or [])
    aliases = list(manifest.get("aliases") or [])
    gaps = list(manifest.get("gaps") or [])
    exclusions = list(manifest.get("exclusions") or [])
    procedure_ids = [str(item.get("procedure_id") or "") for item in procedures]
    asset_ids = [str(item.get("form_id") or "") for item in assets]
    binding_ids = [str(item.get("binding_id") or "") for item in bindings]
    if not procedure_ids or any(not value for value in procedure_ids):
        errors.append("FORM_PROCEDURE_ID_MISSING")
    if len(procedure_ids) != len(set(procedure_ids)):
        errors.append("FORM_PROCEDURE_DUPLICATE")
    if len(asset_ids) != len(set(asset_ids)):
        errors.append("FORM_ASSET_DUPLICATE")
    asset_identity_hashes = [
        canonical_sha256({
            "form_code": str(item.get("form_code") or "").strip().casefold(),
            "canonical_name": str(item.get("canonical_name") or "").strip().casefold(),
            "issuing_instrument": str(item.get("issuing_instrument") or "").strip().casefold(),
            "source_checksum": str(item.get("source_checksum") or "").strip().casefold(),
        })
        for item in assets
    ]
    if len(asset_identity_hashes) != len(set(asset_identity_hashes)):
        errors.append("FORM_ASSET_IDENTITY_DUPLICATE")
    if len(binding_ids) != len(set(binding_ids)) or any(not value for value in binding_ids):
        errors.append("FORM_BINDING_ID_INVALID")

    procedure_set = set(procedure_ids)
    asset_set = set(asset_ids)
    for procedure in procedures:
        if procedure.get("coverage_status") not in {
            "released",
            "verified_gap",
            "owner_deferred",
        }:
            errors.append("FORM_PROCEDURE_UNDECIDED")
        if not is_allowlisted_official_url(str(procedure.get("official_source_url") or "")):
            errors.append("FORM_PROCEDURE_SOURCE_NOT_OFFICIAL")
        if not _effective(procedure, legal_as_of):
            errors.append("FORM_PROCEDURE_NOT_EFFECTIVE")

    source_checks = {"required": len(assets), "passed": 0, "mode": "metadata_only"}
    for asset in assets:
        if asset.get("coverage_status") != "released":
            errors.append("FORM_ASSET_NOT_RELEASED")
        if asset.get("asset_kind") not in {"file", "eform"}:
            errors.append("FORM_ASSET_KIND_INVALID")
        if not SHA256_RE.fullmatch(str(asset.get("source_checksum") or "")):
            errors.append("FORM_CHECKSUM_MISMATCH")
        if not is_allowlisted_official_url(str(asset.get("source_url") or "")):
            errors.append("FORM_SOURCE_NOT_OFFICIAL")
        audiences = set(asset.get("audiences") or [])
        if not audiences or not audiences.issubset({"citizen", "officer", "both"}):
            errors.append("FORM_ASSET_AUDIENCE_INVALID")
        if not _effective(asset, legal_as_of):
            errors.append("FORM_ASSET_NOT_EFFECTIVE")
        if source_verifier is not None:
            source_checks["mode"] = "remote"
            reason = source_verifier(asset)
            if reason:
                errors.append(reason)
            else:
                source_checks["passed"] += 1

    for binding in bindings:
        if str(binding.get("procedure_id") or "") not in procedure_set:
            errors.append("FORM_BINDING_PROCEDURE_MISSING")
        form_id = str(binding.get("form_id") or "")
        if form_id and form_id not in asset_set:
            errors.append("FORM_BINDING_ASSET_MISSING")
        if binding.get("coverage_status") not in {"released", "verified_gap", "not_applicable"}:
            errors.append("FORM_BINDING_UNDECIDED")
        if binding.get("coverage_status") == "released" and not form_id:
            errors.append("FORM_BINDING_ASSET_MISSING")
        if binding.get("requirement") not in {"required", "conditional"}:
            errors.append("FORM_BINDING_REQUIREMENT_INVALID")
        if binding.get("requirement") == "conditional" and not str(binding.get("condition") or "").strip():
            errors.append("FORM_CONDITION_REQUIRED")
        if binding.get("audience") not in {"citizen", "officer", "both"}:
            errors.append("FORM_BINDING_AUDIENCE_INVALID")
        if not _effective(binding, legal_as_of):
            errors.append("FORM_BINDING_NOT_EFFECTIVE")

    for alias in aliases:
        if str(alias.get("procedure_id") or "") not in procedure_set:
            errors.append("FORM_ALIAS_PROCEDURE_MISSING")
        if alias.get("alias_kind") not in {"exact", "natural", "exclude", "hard_negative"}:
            errors.append("FORM_ALIAS_KIND_INVALID")
        if not str(alias.get("alias") or "").strip():
            errors.append("FORM_ALIAS_EMPTY")

    gap_counts = Counter()
    for gap in gaps:
        target_type = str(gap.get("target_type") or "")
        if target_type not in {"procedure", "identity", "binding"}:
            errors.append("FORM_VERIFIED_GAP_TARGET_INVALID")
            continue
        gap_counts[target_type] += 1
        if not str(gap.get("target_id") or "").strip():
            errors.append("FORM_VERIFIED_GAP_TARGET_INVALID")
        if not SHA256_RE.fullmatch(str(gap.get("evidence_sha256") or "")):
            errors.append("FORM_CHECKSUM_MISMATCH")
        if not is_allowlisted_official_url(str(gap.get("evidence_source_url") or "")):
            errors.append("FORM_SOURCE_NOT_OFFICIAL")
        if _parse_date(gap.get("legal_as_of")) is None:
            errors.append("FORM_LEGAL_AS_OF_INVALID")

    exclusion_counts = Counter()
    exclusion_targets: set[tuple[str, str]] = set()
    for exclusion in exclusions:
        target_type = str(exclusion.get("target_type") or "")
        target_id = str(exclusion.get("target_id") or "").strip()
        target = (target_type, target_id)
        if target_type not in {"procedure", "identity", "binding"} or not target_id:
            errors.append("FORM_SCOPE_EXCLUSION_TARGET_INVALID")
            continue
        if target in exclusion_targets:
            errors.append("FORM_SCOPE_EXCLUSION_DUPLICATE")
        exclusion_targets.add(target)
        exclusion_counts[target_type] += 1
        if exclusion.get("reason_code") not in OWNER_DEFERRED_REASONS:
            errors.append("FORM_SCOPE_EXCLUSION_REASON_INVALID")
        if not SHA256_RE.fullmatch(
            str(exclusion.get("decision_fingerprint") or "")
        ):
            errors.append("FORM_SCOPE_EXCLUSION_FINGERPRINT_INVALID")
        if not str(exclusion.get("decided_by") or "").strip():
            errors.append("FORM_SCOPE_EXCLUSION_ACTOR_REQUIRED")
        if _parse_date(exclusion.get("decided_at")) is None:
            errors.append("FORM_SCOPE_EXCLUSION_DATE_INVALID")
        if exclusion.get("public_eligible") is not False:
            errors.append("FORM_SCOPE_EXCLUSION_PUBLIC_LEAK")
        if exclusion.get("router_eligible") is not False:
            errors.append("FORM_SCOPE_EXCLUSION_ROUTER_LEAK")

    deferred_procedures = {
        str(item.get("procedure_id") or "")
        for item in procedures
        if item.get("coverage_status") == "owner_deferred"
    }
    excluded_procedures = {
        target_id
        for target_type, target_id in exclusion_targets
        if target_type == "procedure"
    }
    if deferred_procedures != excluded_procedures:
        errors.append("FORM_SCOPE_EXCLUSION_PROCEDURE_MISMATCH")
    if any(
        ("identity", asset_id) in exclusion_targets for asset_id in asset_set
    ):
        errors.append("FORM_SCOPE_EXCLUSION_ASSET_LEAK")
    if any(
        ("binding", binding_id) in exclusion_targets for binding_id in binding_ids
    ):
        errors.append("FORM_SCOPE_EXCLUSION_BINDING_LEAK")

    coverage = manifest.get("coverage") or {}
    for total_key, decided_key in (
        ("procedure_total", "procedure_decided"),
        ("identity_total", "identity_decided"),
        ("binding_total", "binding_decided"),
    ):
        total = int(coverage.get(total_key) or 0)
        decided = int(coverage.get(decided_key) or 0)
        if total < 0 or decided < 0 or decided > total:
            errors.append("FORM_COVERAGE_INVALID")
    expected_decisions = {
        "procedure_decided": len(procedures),
        "identity_decided": (
            len(assets)
            + gap_counts["identity"]
            + exclusion_counts["identity"]
        ),
        "binding_decided": (
            len(bindings)
            + gap_counts["binding"]
            + exclusion_counts["binding"]
        ),
    }
    if any(int(coverage.get(key) or 0) != value for key, value in expected_decisions.items()):
        errors.append("FORM_COVERAGE_MANIFEST_MISMATCH")
    should_be_complete = all(
        int(coverage.get(done) or 0) == int(coverage.get(total) or 0)
        for done, total in (
            ("procedure_decided", "procedure_total"),
            ("identity_decided", "identity_total"),
            ("binding_decided", "binding_total"),
        )
    )
    if bool(coverage.get("complete")) != should_be_complete:
        errors.append("FORM_COVERAGE_COMPLETE_MISMATCH")

    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "source_checks": source_checks,
        "manifest_sha256": canonical_sha256(manifest),
    }


class HttpFormSourceVerifier:
    """Verify official source reachability and file bytes without redirects off-list."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_bytes: int = 50 * 1024 * 1024,
        transport: Any | None = None,
        runtime_root: Path | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.transport = transport
        self.runtime_root = (runtime_root or Path(__file__).resolve().parents[1] / "release-data").resolve()

    def _verify_runtime_file(self, asset: Mapping[str, Any]) -> str | None:
        runtime_path = str(asset.get("runtime_path") or "").strip()
        expected_url = (
            f"/api/procedures/forms-catalog/assets/{asset.get('form_id')}/download"
        )
        if not runtime_path or asset.get("download_url") != expected_url:
            return "FORM_RUNTIME_ASSET_MISSING"
        candidate = (self.runtime_root / runtime_path).resolve()
        try:
            candidate.relative_to(self.runtime_root)
        except ValueError:
            return "FORM_RUNTIME_ASSET_PATH_INVALID"
        if not candidate.is_file():
            return "FORM_RUNTIME_ASSET_MISSING"
        if candidate.stat().st_size > self.max_bytes:
            return "FORM_SOURCE_FILE_TOO_LARGE"
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != str(asset.get("source_checksum") or "").casefold():
            return "FORM_CHECKSUM_MISMATCH"
        return None

    def _digest_response(self, response: Any) -> tuple[str | None, str | None]:
        if not is_allowlisted_official_url(str(response.url)):
            return None, "FORM_SOURCE_REDIRECT_NOT_OFFICIAL"
        digest = hashlib.sha256()
        size = 0
        for block in response.iter_bytes():
            size += len(block)
            if size > self.max_bytes:
                return None, "FORM_SOURCE_FILE_TOO_LARGE"
            digest.update(block)
        return digest.hexdigest(), None

    def calculate_checksum(self, source_url: str) -> tuple[str | None, str | None]:
        """Download an allowlisted official source once and return its SHA-256.

        This is used by the explicit source-verification action. Redirects are
        accepted only when the final URL remains on the official allowlist.
        """
        import httpx
        from api.official_http import build_verified_ssl_context

        if not is_allowlisted_official_url(source_url):
            return None, "FORM_SOURCE_REQUIRED"
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                verify=build_verified_ssl_context(),
                transport=self.transport,
            ) as client:
                with client.stream("GET", source_url) as response:
                    response.raise_for_status()
                    return self._digest_response(response)
        except (httpx.HTTPError, OSError):
            return None, "FORM_SOURCE_UNAVAILABLE"

    def _verify_dvc_attachment(
        self,
        asset: Mapping[str, Any],
        artifact: Mapping[str, Any],
    ) -> str | None:
        import httpx
        from api.official_http import build_verified_ssl_context

        endpoint = str(artifact.get("official_endpoint") or "")
        attachment_id = str(artifact.get("attachment_id") or "")
        referer = str(artifact.get("referer_url") or asset.get("source_url") or "")
        if endpoint != DVC_ATTACHMENT_ENDPOINT:
            return "FORM_DVC_ATTACHMENT_ENDPOINT_INVALID"
        try:
            if str(UUID(attachment_id)) != attachment_id.casefold():
                return "FORM_DVC_ATTACHMENT_ID_INVALID"
        except (ValueError, AttributeError):
            return "FORM_DVC_ATTACHMENT_ID_INVALID"
        if (
            not referer.startswith(DVC_ORIGIN + "/")
            or not is_allowlisted_official_url(referer)
        ):
            return "FORM_DVC_ATTACHMENT_REFERER_INVALID"

        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                http1=True,
                http2=False,
                verify=build_verified_ssl_context(),
                transport=self.transport,
                headers={
                    "User-Agent": DVC_USER_AGENT,
                    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            ) as client:
                warmup = client.get(
                    referer,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Dest": "document",
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
                warmup.raise_for_status()
                if str(warmup.url) != referer:
                    return "FORM_DVC_ATTACHMENT_REFERER_REDIRECTED"
                with client.stream(
                    "POST",
                    DVC_ATTACHMENT_ENDPOINT,
                    json={"fileId": attachment_id},
                    headers={
                        "Accept": "application/json;odata=verbose",
                        "Content-Type": "application/json; charset=UTF-8",
                        "Origin": DVC_ORIGIN,
                        "Referer": referer,
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Dest": "empty",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                ) as response:
                    response.raise_for_status()
                    if str(response.url) != DVC_ATTACHMENT_ENDPOINT:
                        return "FORM_DVC_ATTACHMENT_REDIRECTED"
                    digest, reason = self._digest_response(response)
        except (httpx.HTTPError, OSError):
            return "FORM_SOURCE_UNAVAILABLE"
        if reason:
            return reason
        if digest != str(asset.get("source_checksum") or "").casefold():
            return "FORM_CHECKSUM_MISMATCH"
        return None

    def _verify_source_package(
        self,
        artifact: Mapping[str, Any],
    ) -> str | None:
        import httpx
        from api.official_http import build_verified_ssl_context

        url = str(artifact.get("source_download_url") or "")
        expected = str(artifact.get("source_package_sha256") or "").casefold()
        if not is_allowlisted_official_url(url) or not SHA256_RE.fullmatch(expected):
            return "FORM_SOURCE_PACKAGE_PROVENANCE_INVALID"
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                verify=build_verified_ssl_context(),
                transport=self.transport,
            ) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    digest, reason = self._digest_response(response)
        except (httpx.HTTPError, OSError):
            return "FORM_SOURCE_UNAVAILABLE"
        if reason:
            return reason
        if digest != expected:
            return "FORM_SOURCE_PACKAGE_CHECKSUM_DRIFT"
        return None

    def __call__(self, asset: Mapping[str, Any]) -> str | None:
        import httpx
        from api.official_http import build_verified_ssl_context

        url = str(asset.get("source_url") or "")
        artifact = (
            (asset.get("provenance") or {}).get("canonical_artifact") or {}
        )
        if asset.get("asset_kind") == "file" and artifact:
            runtime_reason = self._verify_runtime_file(asset)
            if runtime_reason:
                return runtime_reason
            if artifact.get("attachment_id") or artifact.get("official_endpoint"):
                return self._verify_dvc_attachment(asset, artifact)
            if artifact.get("source_download_url"):
                return self._verify_source_package(artifact)
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                verify=build_verified_ssl_context(),
                transport=self.transport,
            ) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    if asset.get("asset_kind") == "eform":
                        if not is_allowlisted_official_url(str(response.url)):
                            return "FORM_SOURCE_REDIRECT_NOT_OFFICIAL"
                        return None
                    digest, reason = self._digest_response(response)
        except (httpx.HTTPError, OSError):
            return "FORM_SOURCE_UNAVAILABLE"
        if reason:
            return reason
        if digest != str(asset.get("source_checksum") or "").casefold():
            return "FORM_CHECKSUM_MISMATCH"
        return None
