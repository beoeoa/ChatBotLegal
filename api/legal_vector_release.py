"""Immutable Vector Integrity V3 release and review contracts.

This module has no database or Chroma write path.  It centralizes the
fail-closed invariants that builders, migration rehearsals and serving-manifest
validation share.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


SHA256_PATTERN = r"^[a-f0-9]{64}$"
QUALITY_POLICY_VERSION = "legal-chunk-quality-v2"
MAX_PASSAGE_TOKENS = 512


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    """Hash canonical JSON while excluding the payload's self-hash."""

    canonical = dict(payload)
    canonical.pop("manifest_sha256", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LegalChunkRelease(_FrozenContract):
    release_id: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    source_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    model_artifact_fingerprint: str = Field(pattern=SHA256_PATTERN)
    tokenizer_fingerprint: str = Field(pattern=SHA256_PATTERN)
    embedding_recipe_fingerprint: str = Field(pattern=SHA256_PATTERN)
    passage_recipe_fingerprint: str = Field(pattern=SHA256_PATTERN)
    splitter_fingerprint: str = Field(pattern=SHA256_PATTERN)
    quality_policy_version: Literal[QUALITY_POLICY_VERSION]
    dependency_lock_fingerprint: str = Field(pattern=SHA256_PATTERN)
    build_state: Literal["building", "complete", "validated", "blocked"]
    inventory_document_count: int = Field(ge=1)
    retrievable_document_count: int = Field(ge=0)
    quarantined_document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    vector_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent_counts(self) -> "LegalChunkRelease":
        if self.inventory_document_count != (
            self.retrievable_document_count + self.quarantined_document_count
        ):
            raise ValueError(
                "inventory_document_count must equal retrievable plus quarantined"
            )
        if self.vector_count != self.chunk_count:
            raise ValueError("vector_count must equal eligible chunk_count")
        if self.retrievable_document_count and not self.chunk_count:
            raise ValueError("chunk_count required for retrievable documents")
        return self


class LegalChunkRevision(_FrozenContract):
    release_id: str = Field(min_length=1)
    document_id: int = Field(gt=0)
    article_id: int = Field(gt=0)
    chunk_index: int = Field(ge=0)
    structural_path: str = Field(min_length=1)
    child_kind: Literal["article", "appendix", "clause", "point", "fallback"]
    heading: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_start_offset: int | None = Field(default=None, ge=0)
    source_end_offset: int | None = Field(default=None, ge=0)
    source_content_sha256: str = Field(pattern=SHA256_PATTERN)
    passage_sha256: str = Field(pattern=SHA256_PATTERN)
    token_count: int = Field(ge=1, le=MAX_PASSAGE_TOKENS)
    quality_policy_version: Literal[QUALITY_POLICY_VERSION]
    quality_assessed: bool
    eligible: bool
    serving_state: Literal["retrievable", "quarantined"]
    quality_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _fail_closed_quality(self) -> "LegalChunkRevision":
        if not self.quality_assessed:
            raise ValueError("quality_assessed must be true before release")
        if self.eligible and self.serving_state != "retrievable":
            raise ValueError("serving_state must be retrievable when eligible")
        if not self.eligible and self.serving_state != "quarantined":
            raise ValueError("serving_state must be quarantined when ineligible")
        if self.eligible and self.quality_reasons:
            raise ValueError("quality_reasons must be empty when eligible")
        if not self.eligible and not self.quality_reasons:
            raise ValueError("quality_reasons required when quarantined")
        if (
            self.source_start_offset is not None
            and self.source_end_offset is not None
            and self.source_end_offset < self.source_start_offset
        ):
            raise ValueError("source_end_offset must not precede source_start_offset")
        return self


class LegalMetadataReviewCase(_FrozenContract):
    case_id: str = Field(min_length=1)
    document_id: int = Field(gt=0)
    field_name: Literal[
        "title",
        "law_number",
        "issuing_agency",
        "scope",
        "source_url",
        "domain",
        "effective_date",
        "expired_date",
        "status",
    ]
    old_value: Any = None
    proposed_value: Any = None
    evidence_url: HttpUrl | None = None
    evidence_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    state: Literal["proposed", "approved", "rejected", "superseded"]
    reviewer_user_id: str | None = None

    @model_validator(mode="after")
    def _approved_requires_attestation(self) -> "LegalMetadataReviewCase":
        if self.state == "approved":
            if self.evidence_url is None:
                raise ValueError("evidence_url required for approved review")
            if self.evidence_sha256 is None:
                raise ValueError("evidence_sha256 required for approved review")
            if not str(self.reviewer_user_id or "").strip():
                raise ValueError("reviewer_user_id required for approved review")
        return self


__all__ = [
    "MAX_PASSAGE_TOKENS",
    "QUALITY_POLICY_VERSION",
    "LegalChunkRelease",
    "LegalChunkRevision",
    "LegalMetadataReviewCase",
    "canonical_payload_sha256",
]
