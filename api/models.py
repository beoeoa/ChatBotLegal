from typing import Any, Dict, List, Literal, Optional

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Notebook models
class NotebookCreate(BaseModel):
    name: str = Field(..., description="Name of the notebook")
    description: str = Field(default="", description="Description of the notebook")


class NotebookUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Name of the notebook")
    description: Optional[str] = Field(None, description="Description of the notebook")
    archived: Optional[bool] = Field(
        None, description="Whether the notebook is archived"
    )


class NotebookResponse(BaseModel):
    id: str
    name: str
    description: str
    archived: bool
    created: str
    updated: str
    source_count: int
    note_count: int


# Search models
class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query")
    type: Literal["text", "vector"] = Field("text", description="Search type")
    limit: int = Field(100, description="Maximum number of results", le=1000)
    search_sources: bool = Field(True, description="Include sources in search")
    search_notes: bool = Field(True, description="Include notes in search")
    minimum_score: float = Field(
        0.2, description="Minimum score for vector search", ge=0, le=1
    )


class SearchResponse(BaseModel):
    results: List[Dict[str, Any]] = Field(..., description="Search results")
    total_count: int = Field(..., description="Total number of results")
    search_type: str = Field(..., description="Type of search performed")


class AskRequest(BaseModel):
    question: str = Field(..., description="Question to ask the knowledge base")
    role: Literal["officer", "citizen", "admin"] = Field(
        "citizen", description="Answer profile selected by the user"
    )
    strategy_model: str = Field("", description="Model ID for query strategy")
    answer_model: str = Field("", description="Model ID for individual answers")
    final_answer_model: str = Field("", description="Model ID for final answer")
    offline_mode: bool = Field(False, description="Use local Ollama instead of cloud graph")
    offline_model: str = Field("qwen2.5:3b", description="Ollama model name")
    domain: Optional[str] = Field(None, description="Commune task domain filter")
    ward_scope: str = Field("Phường Lê Chân, Hải Phòng", description="Display context only; not a retrieval scope filter")
    uploaded_context_ids: Optional[List[str]] = Field(None, description="Context IDs from uploaded files")
    allowed_domains: Optional[List[str]] = Field(None, description="List of domains allowed for user")
    show_rag_trace: bool = Field(False, description="Return RAG trace details (admin only)")
    session_id: Optional[str] = Field(None, description="Deprecated Ask session ID for legacy chat history context")
    conversation_id: Optional[str] = Field(None, description="Persistent owner-scoped conversation ID for multi-turn Ask context")
    event_date: Optional[date] = Field(None, description="Date the legal event occurred, when known")
    legal_as_of: Optional[date] = Field(None, description="Legal validity date; defaults to event_date then today")
    idempotency_key: Optional[str] = Field(None, min_length=8, max_length=128, description="Client-generated key preventing duplicate Ask execution")


class CitationDisplayItem(BaseModel):
    """Public, verified legal-source metadata for one answer section.

    This intentionally excludes retrieval chunk, packet, trace and source-record
    identifiers.  Those remain server-side evidence metadata.
    """

    document_title: str = Field(..., min_length=1, description="Readable legal document title")
    law_number: Optional[str] = Field(None, description="Verified document number")
    article_number: Optional[str] = Field(None, description="Verified article")
    clause_number: Optional[str] = Field(None, description="Verified clause")
    point_number: Optional[str] = Field(None, description="Verified point")
    effective_status: str = Field(..., min_length=1, description="Verified applicability status")
    legal_as_of: Optional[date] = Field(None, description="Date used for applicability")
    source_url: Optional[str] = Field(None, description="Official public source URL when available")


class AnswerSection(BaseModel):
    """Optional public result for one independently grounded legal issue."""

    issue_id: str = Field(..., min_length=1, description="Opaque request-local issue identifier")
    title: str = Field(..., min_length=1, description="Plain-language issue title")
    status: Literal[
        "sufficiently_evidenced",
        "partially_evidenced",
        "insufficiently_evidenced",
    ] = Field(..., description="Evidence status for this issue")
    answer: Optional[str] = Field(None, description="Verified legal conclusion for a sufficient issue")
    guidance: Optional[str] = Field(None, description="Non-binding guidance for a partial issue")
    limitation: Optional[str] = Field(None, description="Evidence limitation for a partial or insufficient issue")
    citations: List[CitationDisplayItem] = Field(default_factory=list, description="Public citations for a sufficient issue only")
    clarifying_question: Optional[str] = Field(None, description="One optional follow-up for an insufficient issue")

    @model_validator(mode="after")
    def validate_status_content(self):
        if self.status == "sufficiently_evidenced":
            if not (self.answer or "").strip() or not self.citations:
                raise ValueError("Sufficiently evidenced sections require an answer and public citations")
            if self.guidance is not None:
                raise ValueError("Sufficiently evidenced sections cannot contain non-binding guidance")
        elif self.status == "partially_evidenced":
            if self.answer is not None or self.citations:
                raise ValueError("Partially evidenced sections cannot contain a legal answer or citations")
            if not (self.guidance or "").strip() or not (self.limitation or "").strip():
                raise ValueError("Partially evidenced sections require guidance and a limitation")
            if self.clarifying_question is not None:
                raise ValueError("Partially evidenced sections cannot ask a clarifying question")
        else:
            if self.answer is not None or self.guidance is not None or self.citations:
                raise ValueError("Insufficiently evidenced sections cannot contain answer, guidance or citations")
            if not (self.limitation or "").strip():
                raise ValueError("Insufficiently evidenced sections require a limitation")
        return self


class AskResponse(BaseModel):
    answer: str = Field(..., description="Final answer from the knowledge base")
    question: str = Field(..., description="Original question")
    rag_trace: Optional[Dict[str, Any]] = Field(
        None, description="Retrieval and grounding trace"
    )
    grounding_status: str = Field("unknown", description="Grounding status: fully_grounded|partially_grounded|ungrounded|insufficient_evidence|domain_mismatch")
    citations: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Extracted legal citations with structured source metadata",
    )
    recommended_forms: Optional[List[Dict[str, Any]]] = Field(None, description="Recommended administrative forms")
    procedure_summary: Optional[str] = Field(None, description="Brief summary of applicable administrative procedure")
    procedure_detail: Optional[Dict[str, Any]] = Field(None, description="Full procedural detail matching the question")
    faqs: Optional[List[Dict[str, Any]]] = Field(None, description="Matched FAQs for Ask UI")
    domain_mismatch: bool = Field(False, description="True when selected domain conflicts with detected question domain")
    selected_domain: Optional[str] = Field(None, description="Domain selected by user/UI")
    suggested_domain: Optional[str] = Field(None, description="Detected/suggested domain slug")
    suggested_agency: Optional[str] = Field(None, description="Suggested responsible agency/department")
    conversation_id: Optional[str] = Field(None, description="Persistent conversation identifier")
    latency_ms: Optional[int] = Field(None, description="End-to-end ask latency in milliseconds")
    trace_id: Optional[str] = Field(None, description="Request trace identifier")
    error: Optional[Dict[str, Any]] = Field(None, description="Structured non-fatal processing error")
    question_type: Optional[str] = Field(None, description="Deterministic question category")
    detected_domain: Optional[str] = Field(None, description="Detected legal domain")
    required_sections: List[str] = Field(default_factory=list, description="Sections expected for this question type")
    forms_unavailable: bool = Field(False, description="True when requested official forms are unavailable")
    source_gap: List[str] = Field(default_factory=list, description="Information not verified from current sources")
    evidence_coverage: Dict[str, Any] = Field(default_factory=dict, description="Verified/missing/conflicting evidence by answer section")
    claim_validation: List[Dict[str, Any]] = Field(default_factory=list, description="Claim-level grounding decisions")
    authority_status: str = Field("not_applicable", description="Authority evidence status")
    legal_as_of: Optional[date] = Field(None, description="Date used to filter legal validity")
    clarifying_questions: List[str] = Field(default_factory=list, description="Questions needed before a definitive conclusion")
    quality_flags: List[str] = Field(default_factory=list, description="Machine-detected answer quality risks")
    answer_score_preview: Optional[float] = Field(None, ge=0, le=10, description="Automated preview only; not expert legal approval")
    answer_sections: Optional[List[AnswerSection]] = Field(
        None,
        description="Optional independently grounded answer sections; omitted for legacy responses and when the feature is disabled",
    )


# Models API models
class ModelCreate(BaseModel):
    name: str = Field(..., description="Model name (e.g., gpt-5-mini, claude, gemini)")
    provider: str = Field(
        ..., description="Provider name (e.g., openai, anthropic, gemini)"
    )
    type: str = Field(
        ...,
        description="Model type (language, embedding, text_to_speech, speech_to_text)",
    )
    credential: Optional[str] = Field(
        None, description="Credential ID to link this model to"
    )


class ModelResponse(BaseModel):
    id: str
    name: str
    provider: str
    type: str
    credential: Optional[str] = None
    created: str
    updated: str


class DefaultModelsResponse(BaseModel):
    default_chat_model: Optional[str] = None
    default_transformation_model: Optional[str] = None
    large_context_model: Optional[str] = None
    default_text_to_speech_model: Optional[str] = None
    default_speech_to_text_model: Optional[str] = None
    default_embedding_model: Optional[str] = None
    default_tools_model: Optional[str] = None


class ProviderAvailabilityResponse(BaseModel):
    available: List[str] = Field(..., description="List of available providers")
    unavailable: List[str] = Field(..., description="List of unavailable providers")
    supported_types: Dict[str, List[str]] = Field(
        ..., description="Provider to supported model types mapping"
    )


# Transformations API models
class TransformationCreate(BaseModel):
    name: str = Field(..., description="Transformation name")
    title: str = Field(..., description="Display title for the transformation")
    description: str = Field(
        ..., description="Description of what this transformation does"
    )
    prompt: str = Field(..., description="The transformation prompt")
    apply_default: bool = Field(
        False, description="Whether to apply this transformation by default"
    )


class TransformationUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Transformation name")
    title: Optional[str] = Field(
        None, description="Display title for the transformation"
    )
    description: Optional[str] = Field(
        None, description="Description of what this transformation does"
    )
    prompt: Optional[str] = Field(None, description="The transformation prompt")
    apply_default: Optional[bool] = Field(
        None, description="Whether to apply this transformation by default"
    )


class TransformationResponse(BaseModel):
    id: str
    name: str
    title: str
    description: str
    prompt: str
    apply_default: bool
    created: str
    updated: str


class TransformationExecuteRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(
        ..., description="ID of the transformation to execute"
    )
    input_text: str = Field(..., description="Text to transform")
    model_id: str = Field(..., description="Model ID to use for the transformation")


class TransformationExecuteResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    output: str = Field(..., description="Transformed text")
    transformation_id: str = Field(..., description="ID of the transformation used")
    model_id: str = Field(..., description="Model ID used")


# Default Prompt API models
class DefaultPromptResponse(BaseModel):
    transformation_instructions: str = Field(
        ..., description="Default transformation instructions"
    )


class DefaultPromptUpdate(BaseModel):
    transformation_instructions: str = Field(
        ..., description="Default transformation instructions"
    )


# Notes API models
class NoteCreate(BaseModel):
    title: Optional[str] = Field(None, description="Note title")
    content: str = Field(..., description="Note content")
    note_type: Optional[str] = Field("human", description="Type of note (human, ai)")
    notebook_id: Optional[str] = Field(
        None, description="Notebook ID to add the note to"
    )


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Note title")
    content: Optional[str] = Field(None, description="Note content")
    note_type: Optional[str] = Field(None, description="Type of note (human, ai)")


class NoteResponse(BaseModel):
    id: str
    title: Optional[str]
    content: Optional[str]
    note_type: Optional[str]
    created: str
    updated: str
    command_id: Optional[str] = None


# Embedding API models
class EmbedRequest(BaseModel):
    item_id: str = Field(..., description="ID of the item to embed")
    item_type: str = Field(..., description="Type of item (source, note)")
    async_processing: bool = Field(
        False, description="Process asynchronously in background"
    )


class EmbedResponse(BaseModel):
    success: bool = Field(..., description="Whether embedding was successful")
    message: str = Field(..., description="Result message")
    item_id: str = Field(..., description="ID of the item that was embedded")
    item_type: str = Field(..., description="Type of item that was embedded")
    command_id: Optional[str] = Field(
        None, description="Command ID for async processing"
    )


# Rebuild request/response models
class RebuildRequest(BaseModel):
    mode: Literal["existing", "all"] = Field(
        ...,
        description="Rebuild mode: 'existing' only re-embeds items with embeddings, 'all' embeds everything",
    )
    include_sources: bool = Field(True, description="Include sources in rebuild")
    include_notes: bool = Field(True, description="Include notes in rebuild")
    include_insights: bool = Field(True, description="Include insights in rebuild")


class RebuildResponse(BaseModel):
    command_id: str = Field(..., description="Command ID to track progress")
    total_items: int = Field(..., description="Estimated number of items to process")
    message: str = Field(..., description="Status message")


class RebuildProgress(BaseModel):
    processed: int = Field(..., description="Number of items processed")
    total: int = Field(..., description="Total items to process")
    percentage: float = Field(..., description="Progress percentage")


class RebuildStats(BaseModel):
    sources: int = Field(0, description="Sources processed")
    notes: int = Field(0, description="Notes processed")
    insights: int = Field(0, description="Insights processed")
    failed: int = Field(0, description="Failed items")


class RebuildStatusResponse(BaseModel):
    command_id: str = Field(..., description="Command ID")
    status: str = Field(..., description="Status: queued, running, completed, failed")
    progress: Optional[RebuildProgress] = None
    stats: Optional[RebuildStats] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


# Settings API models
class SettingsResponse(BaseModel):
    default_content_processing_engine_doc: Optional[str] = None
    default_content_processing_engine_url: Optional[str] = None
    default_embedding_option: Optional[str] = None
    auto_delete_files: Optional[str] = None
    youtube_preferred_languages: Optional[List[str]] = None


class SettingsUpdate(BaseModel):
    default_content_processing_engine_doc: Optional[str] = None
    default_content_processing_engine_url: Optional[str] = None
    default_embedding_option: Optional[str] = None
    auto_delete_files: Optional[str] = None
    youtube_preferred_languages: Optional[List[str]] = None


# Sources API models
class AssetModel(BaseModel):
    file_path: Optional[str] = None
    url: Optional[str] = None


class SourceCreate(BaseModel):
    # Backward compatibility: support old single notebook_id
    notebook_id: Optional[str] = Field(
        None, description="Notebook ID to add the source to (deprecated, use notebooks)"
    )
    # New multi-notebook support
    notebooks: Optional[List[str]] = Field(
        None, description="List of notebook IDs to add the source to"
    )
    # Required fields
    type: str = Field(..., description="Source type: link, upload, or text")
    url: Optional[str] = Field(None, description="URL for link type")
    file_path: Optional[str] = Field(None, description="File path for upload type")
    content: Optional[str] = Field(None, description="Text content for text type")
    title: Optional[str] = Field(None, description="Source title")
    transformations: Optional[List[str]] = Field(
        default_factory=list, description="Transformation IDs to apply"
    )
    embed: bool = Field(False, description="Whether to embed content for vector search")
    delete_source: bool = Field(
        False, description="Whether to delete uploaded file after processing"
    )
    # New async processing support
    async_processing: bool = Field(
        False, description="Whether to process source asynchronously"
    )
    # Legal document scope for filtering (Week 4-6)
    scope: Optional[Literal["central", "haiphong", "local"]] = Field(
        default=None, description="Scope of legal document (central=Trung uong, haiphong=TP Hai Phong, local=Xa/Phuong)"
    )
    # Domain for commune pilot filtering (Week 6)
    domain: Optional[str] = Field(
        None, description="Commune task domain for this source"
    )

    @model_validator(mode="after")
    def validate_notebook_fields(self):
        # Ensure only one of notebook_id or notebooks is provided
        if self.notebook_id is not None and self.notebooks is not None:
            raise ValueError(
                "Cannot specify both 'notebook_id' and 'notebooks'. Use 'notebooks' for multi-notebook support."
            )

        # Convert single notebook_id to notebooks array for internal processing
        if self.notebook_id is not None:
            self.notebooks = [self.notebook_id]
            # Keep notebook_id for backward compatibility in response

        # Set empty array if no notebooks specified (allow sources without notebooks)
        if self.notebooks is None:
            self.notebooks = []

        return self


class SourceUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Source title")
    topics: Optional[List[str]] = Field(None, description="Source topics")


class SourceResponse(BaseModel):
    id: str
    title: Optional[str]
    topics: Optional[List[str]]
    asset: Optional[AssetModel]
    full_text: Optional[str]
    source_scope: Optional[str] = None
    review_status: Optional[str] = None
    embedded: bool
    embedded_chunks: int
    file_available: Optional[bool] = None
    created: str
    updated: str
    # New fields for async processing
    command_id: Optional[str] = None
    status: Optional[str] = None
    processing_info: Optional[Dict] = None
    # Notebook associations
    notebooks: Optional[List[str]] = None


class SourceListResponse(BaseModel):
    id: str
    title: Optional[str]
    topics: Optional[List[str]]
    asset: Optional[AssetModel]
    source_scope: Optional[str] = None
    review_status: Optional[str] = None
    embedded: bool  # Boolean flag indicating if source has embeddings
    embedded_chunks: int  # Number of embedded chunks
    insights_count: int
    created: str
    updated: str
    file_available: Optional[bool] = None
    # Status fields for async processing
    command_id: Optional[str] = None
    status: Optional[str] = None
    processing_info: Optional[Dict[str, Any]] = None


# Context API models
class ContextConfig(BaseModel):
    sources: Dict[str, str] = Field(
        default_factory=dict, description="Source inclusion config {source_id: level}"
    )
    notes: Dict[str, str] = Field(
        default_factory=dict, description="Note inclusion config {note_id: level}"
    )


class ContextRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID to get context for")
    context_config: Optional[ContextConfig] = Field(
        None, description="Context configuration"
    )


class ContextResponse(BaseModel):
    notebook_id: str
    sources: List[Dict[str, Any]] = Field(..., description="Source context data")
    notes: List[Dict[str, Any]] = Field(..., description="Note context data")
    total_tokens: Optional[int] = Field(None, description="Estimated token count")


# Insights API models
class SourceInsightResponse(BaseModel):
    id: str
    source_id: str
    insight_type: str
    content: str
    created: str
    updated: str


class InsightCreationResponse(BaseModel):
    """Response for async insight creation."""

    status: Literal["pending"] = "pending"
    message: str = "Insight generation started"
    source_id: str
    transformation_id: str
    command_id: Optional[str] = None


class SaveAsNoteRequest(BaseModel):
    notebook_id: Optional[str] = Field(None, description="Notebook ID to add note to")


class CreateSourceInsightRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(..., description="ID of transformation to apply")
    model_id: Optional[str] = Field(
        None, description="Model ID (uses default if not provided)"
    )


# Source status response
class SourceStatusResponse(BaseModel):
    status: Optional[str] = Field(None, description="Processing status")
    message: str = Field(..., description="Descriptive message about the status")
    processing_info: Optional[Dict[str, Any]] = Field(
        None, description="Detailed processing information"
    )
    command_id: Optional[str] = Field(None, description="Command ID if available")


# Error response
class ErrorResponse(BaseModel):
    error: str
    message: str


# API Key Configuration models
class SetApiKeyRequest(BaseModel):
    """Request to set an API key for a provider."""

    api_key: Optional[str] = Field(None, description="API key for the provider")
    base_url: Optional[str] = Field(
        None, description="Base URL for URL-based providers (Ollama, OpenAI-compatible)"
    )
    endpoint: Optional[str] = Field(
        None, description="Endpoint URL for Azure OpenAI"
    )
    api_version: Optional[str] = Field(
        None, description="API version for Azure OpenAI"
    )
    endpoint_llm: Optional[str] = Field(
        None, description="Service-specific endpoint for LLM (Azure)"
    )
    endpoint_embedding: Optional[str] = Field(
        None, description="Service-specific endpoint for embedding (Azure)"
    )
    endpoint_stt: Optional[str] = Field(
        None, description="Service-specific endpoint for STT (Azure)"
    )
    endpoint_tts: Optional[str] = Field(
        None, description="Service-specific endpoint for TTS (Azure)"
    )
    service_type: Optional[Literal["llm", "embedding", "stt", "tts"]] = Field(
        None,
        description="Service type for OpenAI-compatible providers (llm, embedding, stt, tts)",
    )
    # Vertex AI specific fields
    vertex_project: Optional[str] = Field(
        None, description="Google Cloud Project ID for Vertex AI"
    )
    vertex_location: Optional[str] = Field(
        None, description="Google Cloud Region for Vertex AI (e.g., us-central1)"
    )
    vertex_credentials_path: Optional[str] = Field(
        None, description="Path to Google Cloud service account JSON file"
    )

    @field_validator(
        "api_key",
        "base_url",
        "endpoint",
        "api_version",
        "endpoint_llm",
        "endpoint_embedding",
        "endpoint_stt",
        "endpoint_tts",
        "vertex_project",
        "vertex_location",
        "vertex_credentials_path",
        mode="before",
    )
    @classmethod
    def validate_not_empty_string(cls, v: Optional[str]) -> Optional[str]:
        """Reject empty strings - convert to None or raise error."""
        if v is not None:
            stripped = v.strip()
            if not stripped:
                return None  # Treat empty/whitespace-only as None
            return stripped
        return v


class ApiKeyStatusResponse(BaseModel):
    """Response showing which providers are configured and their source."""

    configured: Dict[str, bool] = Field(
        ..., description="Map of provider name to whether it is configured"
    )
    source: Dict[str, Literal["database", "environment", "none"]] = Field(
        ...,
        description="Map of provider name to configuration source (database, environment, or none)",
    )
    encryption_configured: bool = Field(
        ...,
        description="Whether OPEN_NOTEBOOK_ENCRYPTION_KEY is set (required to store keys in database)",
    )


class TestConnectionResponse(BaseModel):
    """Response from testing a provider connection."""

    provider: str = Field(..., description="Provider name that was tested")
    success: bool = Field(..., description="Whether connection test succeeded")
    message: str = Field(..., description="Result message with details")


class MigrateFromEnvRequest(BaseModel):
    """Request to migrate API keys from environment variables to database."""

    force: bool = Field(
        False, description="Force overwrite existing database configurations"
    )


class MigrationResult(BaseModel):
    """Response from migrating API keys from environment to database."""

    message: str = Field(..., description="Summary message")
    migrated: List[str] = Field(
        default_factory=list, description="Providers successfully migrated"
    )
    skipped: List[str] = Field(
        default_factory=list, description="Providers skipped (already in DB)"
    )
    errors: List[str] = Field(
        default_factory=list, description="Migration errors by provider"
    )


# Notebook delete cascade models
# Credential models
class CreateCredentialRequest(BaseModel):
    """Request to create a new credential."""

    name: str = Field(..., description="Credential name")
    provider: str = Field(..., description="Provider name (openai, anthropic, etc.)")
    modalities: List[str] = Field(
        default_factory=list,
        description="Supported modalities (language, embedding, text_to_speech, speech_to_text)",
    )
    api_key: Optional[str] = Field(None, description="API key (stored encrypted)")
    base_url: Optional[str] = Field(None, description="Base URL")
    endpoint: Optional[str] = Field(None, description="Endpoint URL (Azure)")
    api_version: Optional[str] = Field(None, description="API version (Azure)")
    endpoint_llm: Optional[str] = Field(None, description="LLM endpoint")
    endpoint_embedding: Optional[str] = Field(None, description="Embedding endpoint")
    endpoint_stt: Optional[str] = Field(None, description="STT endpoint")
    endpoint_tts: Optional[str] = Field(None, description="TTS endpoint")
    project: Optional[str] = Field(None, description="Project ID (Vertex)")
    location: Optional[str] = Field(None, description="Location (Vertex)")
    credentials_path: Optional[str] = Field(
        None, description="Credentials file path (Vertex)"
    )
    num_ctx: Optional[int] = Field(
        None, description="Context window size (Ollama only; defaults to 8192)"
    )


class UpdateCredentialRequest(BaseModel):
    """Request to update an existing credential."""

    name: Optional[str] = Field(None, description="Credential name")
    modalities: Optional[List[str]] = Field(None, description="Supported modalities")
    api_key: Optional[str] = Field(None, description="API key (stored encrypted)")
    base_url: Optional[str] = Field(None, description="Base URL")
    endpoint: Optional[str] = Field(None, description="Endpoint URL")
    api_version: Optional[str] = Field(None, description="API version")
    endpoint_llm: Optional[str] = Field(None, description="LLM endpoint")
    endpoint_embedding: Optional[str] = Field(None, description="Embedding endpoint")
    endpoint_stt: Optional[str] = Field(None, description="STT endpoint")
    endpoint_tts: Optional[str] = Field(None, description="TTS endpoint")
    project: Optional[str] = Field(None, description="Project ID")
    location: Optional[str] = Field(None, description="Location")
    credentials_path: Optional[str] = Field(None, description="Credentials path")
    num_ctx: Optional[int] = Field(
        None, description="Context window size (Ollama only; defaults to 8192)"
    )


class CredentialResponse(BaseModel):
    """Response for a credential (never includes api_key)."""

    id: str
    name: str
    provider: str
    modalities: List[str]
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    endpoint_llm: Optional[str] = None
    endpoint_embedding: Optional[str] = None
    endpoint_stt: Optional[str] = None
    endpoint_tts: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    credentials_path: Optional[str] = None
    num_ctx: Optional[int] = None
    has_api_key: bool = False
    created: str
    updated: str
    model_count: int = 0
    decryption_error: Optional[str] = None


class CredentialDeleteResponse(BaseModel):
    """Response for credential deletion."""

    message: str
    deleted_models: int = 0


class DiscoveredModelResponse(BaseModel):
    """A model discovered from a provider."""

    name: str
    provider: str
    model_type: Optional[str] = None
    description: Optional[str] = None


class DiscoverModelsResponse(BaseModel):
    """Response from model discovery."""

    credential_id: str
    provider: str
    discovered: List[DiscoveredModelResponse]


class RegisterModelData(BaseModel):
    """A model to register with user-specified type."""

    name: str
    provider: str
    model_type: str  # Required: user specifies the type


class RegisterModelsRequest(BaseModel):
    """Request to register discovered models."""

    models: List[RegisterModelData]


class RegisterModelsResponse(BaseModel):
    """Response from model registration."""

    created: int
    existing: int


class NotebookDeletePreview(BaseModel):
    notebook_id: str = Field(..., description="ID of the notebook")
    notebook_name: str = Field(..., description="Name of the notebook")
    note_count: int = Field(..., description="Number of notes that will be deleted")
    exclusive_source_count: int = Field(
        ..., description="Number of sources only in this notebook"
    )
    shared_source_count: int = Field(
        ..., description="Number of sources shared with other notebooks"
    )


class NotebookDeleteResponse(BaseModel):
    message: str = Field(..., description="Success message")
    deleted_notes: int = Field(..., description="Number of notes deleted")
    deleted_sources: int = Field(..., description="Number of exclusive sources deleted")
    unlinked_sources: int = Field(
        ..., description="Number of sources unlinked from notebook"
    )

