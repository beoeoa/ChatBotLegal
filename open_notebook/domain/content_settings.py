from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import Field

from open_notebook.domain.base import RecordModel


class ContentSettings(RecordModel):
    record_id: ClassVar[str] = "open_notebook:content_settings"
    default_content_processing_engine_doc: Optional[
        Literal["auto", "docling", "simple"]
    ] = Field("auto", description="Default Content Processing Engine for Documents")
    default_content_processing_engine_url: Optional[
        Literal["auto", "firecrawl", "jina", "simple"]
    ] = Field("auto", description="Default Content Processing Engine for URLs")
    default_embedding_option: Optional[Literal["ask", "always", "never"]] = Field(
        "ask", description="Default Embedding Option for Vector Search"
    )
    auto_delete_files: Optional[Literal["yes", "no"]] = Field(
        "yes", description="Auto Delete Uploaded Files"
    )
    youtube_preferred_languages: Optional[List[str]] = Field(
        ["en", "pt", "es", "de", "nl", "en-GB", "fr", "de", "hi", "ja"],
        description="Preferred languages for YouTube transcripts",
    )
    # Runtime product configuration. These fields are additive so existing
    # installations can load the singleton without a migration.
    system_name: str = Field("Pháp luật Hải Phòng", max_length=160)
    organization_name: str = Field("", max_length=200)
    system_prompt_addendum: str = Field("", max_length=8000)
    active_prompt_revision: int = Field(1, ge=1)
    config_revision: int = Field(1, ge=1)
    organization_routing_mode: Literal[
        "legacy", "shadow", "hybrid", "unit_primary"
    ] = "legacy"
    # Server-owned rollout clock; legacy rows default to no proven observation.
    organization_hybrid_started_at: Optional[str] = None
    legal_domains: List[Dict[str, Any]] = Field(default_factory=list)
    organization_units: List[Dict[str, Any]] = Field(default_factory=list)
    chat_model_policy: List[Dict[str, Any]] = Field(default_factory=list)
