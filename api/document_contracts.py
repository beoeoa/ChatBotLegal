"""Canonical document contracts shared by uploads, crawlers and notebooks.

The contracts are deliberately storage-neutral.  They provide one stable
shape for an original asset, its extracted/layout-aware representation and a
legal document identity.  Adapters may add fields in their private payloads,
but public routing and persistence should use these objects.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from api.data_paths import notebook_data_dir

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


@dataclass(frozen=True)
class DocumentAsset:
    asset_id: str
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int
    owner_id: str | None
    stored_at: datetime
    storage_uri: str | None = None

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        *,
        filename: str,
        mime_type: str = "application/octet-stream",
        owner_id: str | None = None,
        asset_id: str | None = None,
        storage_uri: str | None = None,
    ) -> "DocumentAsset":
        digest = hashlib.sha256(content).hexdigest()
        return cls(
            asset_id=asset_id or uuid.uuid4().hex,
            filename=Path(str(filename or "document").replace("\\", "/")).name,
            mime_type=_clean(mime_type).lower() or "application/octet-stream",
            sha256=digest,
            size_bytes=len(content),
            owner_id=_clean(owner_id) or None,
            stored_at=_utc(),
            storage_uri=storage_uri,
        )

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{32,64}", str(self.asset_id)):
            raise ValueError("invalid_asset_id")
        if not _SHA256.fullmatch(str(self.sha256).lower()):
            raise ValueError("invalid_asset_sha256")
        if int(self.size_bytes) < 0:
            raise ValueError("invalid_asset_size")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "owner_id": self.owner_id,
            "stored_at": _utc(self.stored_at).isoformat(),
            "storage_uri": self.storage_uri,
        }

    @property
    def size(self) -> int:
        """Compatibility alias used by upload/notebook adapters."""
        return self.size_bytes


@dataclass(frozen=True)
class ExtractionArtifact:
    asset_sha256: str
    text: str
    pages: tuple[dict[str, Any], ...] = ()
    blocks: tuple[dict[str, Any], ...] = ()
    tables: tuple[dict[str, Any], ...] = ()
    images: tuple[dict[str, Any], ...] = ()
    confidence: float | None = None
    warnings: tuple[str, ...] = ()
    extractor: str = "native"
    version: str = "1"
    status: str = "complete"
    artifact_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=_utc)

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(str(self.asset_sha256).lower()):
            raise ValueError("invalid_artifact_asset_sha256")
        if self.confidence is not None and not 0 <= float(self.confidence) <= 100:
            raise ValueError("invalid_artifact_confidence")
        if self.status not in {"processing", "complete", "partial", "error"}:
            raise ValueError("invalid_artifact_status")

    def is_valid_for(self, asset: DocumentAsset | str) -> bool:
        digest = asset.sha256 if isinstance(asset, DocumentAsset) else str(asset)
        return (
            digest.casefold() == self.asset_sha256.casefold()
            and self.status in {"complete", "partial"}
            and bool(self.text.strip())
        )

    @property
    def valid(self) -> bool:
        return self.status in {"complete", "partial"} and bool(self.text.strip())

    def relevant_chunks(self, query: str = "", *, max_chars: int = 96000) -> list[str]:
        """Return bounded evidence chunks, keeping both head and tail context."""
        limit = max(1000, int(max_chars))
        body = self.text.strip()
        if len(body) <= limit:
            return [body] if body else []
        # Deterministic lexical selection keeps upload turns bounded without
        # inventing a second retrieval pipeline.  The first and last windows
        # preserve document identity and closing clauses.
        query_terms = [term.casefold() for term in re.findall(r"\w{3,}", query or "")]
        windows: list[str] = [body[: limit // 3]]
        if query_terms:
            for match in re.finditer("|".join(map(re.escape, query_terms)), body, re.IGNORECASE):
                start = max(0, match.start() - limit // 12)
                windows.append(body[start : start + limit // 4])
                if sum(len(item) for item in windows) >= limit * 0.75:
                    break
        windows.append(body[-limit // 3 :])
        output: list[str] = []
        used = 0
        for window in windows:
            if not window or window in output:
                continue
            remaining = limit - used
            if remaining <= 0:
                break
            output.append(window[:remaining])
            used += min(len(window), remaining)
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "asset_sha256": self.asset_sha256,
            "text": self.text,
            "pages": list(self.pages),
            "blocks": list(self.blocks),
            "tables": list(self.tables),
            "images": list(self.images),
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "extractor": self.extractor,
            "version": self.version,
            "status": self.status,
            "created_at": _utc(self.created_at).isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExtractionArtifact":
        created = value.get("created_at")
        parsed = datetime.fromisoformat(str(created).replace("Z", "+00:00")) if created else _utc()
        return cls(
            artifact_id=str(value.get("artifact_id") or uuid.uuid4().hex),
            asset_sha256=str(value.get("asset_sha256") or ""),
            text=str(value.get("text") or ""),
            pages=tuple(value.get("pages") or ()),
            blocks=tuple(value.get("blocks") or ()),
            tables=tuple(value.get("tables") or ()),
            images=tuple(value.get("images") or ()),
            confidence=value.get("confidence"),
            warnings=tuple(str(item) for item in (value.get("warnings") or ())),
            extractor=str(value.get("extractor") or "native"),
            version=str(value.get("version") or "1"),
            status=str(value.get("status") or "error"),
            created_at=parsed,
        )

    @classmethod
    def from_result(cls, asset_sha256: str, result: Mapping[str, Any]) -> "ExtractionArtifact":
        """Adapt existing extractor dictionaries without creating a new schema."""
        blocks = tuple(result.get("extraction_blocks") or result.get("blocks") or ())
        tables = tuple(result.get("tables") or ()) or tuple(
            item
            for item in blocks
            if isinstance(item, Mapping) and item.get("block_type") == "table"
        )
        pages = tuple(result.get("pages") or ()) or tuple(
            {"page_number": page, "text": text}
            for page, text in (result.get("page_texts") or {}).items()
        )
        status = str(result.get("status") or "error")
        if status == "ok":
            status = "complete"
        elif status not in {"processing", "complete", "partial", "error"}:
            status = "partial" if result.get("text") else "error"
        return cls(
            asset_sha256=asset_sha256,
            text=str(result.get("text") or ""),
            pages=pages,
            blocks=blocks,
            tables=tables,
            images=tuple(result.get("images") or ()),
            confidence=result.get("confidence", result.get("ocr_confidence")),
            warnings=tuple(str(item) for item in (result.get("warnings") or ()) if str(item)),
            extractor=str(result.get("extractor_used") or result.get("extractor") or "native"),
            version=str(result.get("extractor_version") or result.get("version") or "1"),
            status=status,
        )


@dataclass(frozen=True)
class LegalDocumentIdentity:
    document_id: str
    law_number: str | None = None
    title: str | None = None
    issuing_agency: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        if not _clean(self.document_id):
            raise ValueError("document_id_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "law_number": _clean(self.law_number) or None,
            "title": _clean(self.title) or None,
            "issuing_agency": _clean(self.issuing_agency) or None,
            "source_url": _clean(self.source_url) or None,
        }


@dataclass(frozen=True)
class ValidityObservation:
    document_id: str
    status: str
    source_kind: str
    source_url: str | None = None
    verified_at: datetime = field(default_factory=_utc)
    verified_by: str | None = None
    warning: str | None = None

    def __post_init__(self) -> None:
        if not _clean(self.document_id):
            raise ValueError("validity_document_id_required")
        if not _clean(self.source_kind):
            raise ValueError("validity_source_kind_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "status": self.status,
            "source_kind": self.source_kind,
            "source_url": self.source_url,
            "verified_at": _utc(self.verified_at).isoformat(),
            "verified_by": self.verified_by,
            "warning": self.warning,
        }


class ExtractionArtifactStore:
    """Small local artifact cache keyed only by source SHA-256."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (notebook_data_dir() / "extraction_artifacts")

    def _path(self, digest: str) -> Path:
        if not _SHA256.fullmatch(str(digest).casefold()):
            raise ValueError("invalid_artifact_sha256")
        return self.root / f"{digest.casefold()}.json"

    def get_valid(
        self, digest: str, *, required_version: str | None = None
    ) -> ExtractionArtifact | None:
        try:
            value = json.loads(self._path(digest).read_text(encoding="utf-8"))
            artifact = ExtractionArtifact.from_dict(value)
            if required_version and artifact.version != required_version:
                return None
            return artifact if artifact.is_valid_for(digest) else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def put(self, artifact: ExtractionArtifact) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(artifact.asset_sha256)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact.to_dict(), ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)


__all__ = [
    "DocumentAsset",
    "ExtractionArtifact",
    "ExtractionArtifactStore",
    "LegalDocumentIdentity",
    "ValidityObservation",
]
