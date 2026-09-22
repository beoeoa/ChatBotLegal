"""Bounded user-upload context stored with owner-scoped conversation messages."""
from pydantic import BaseModel, Field, model_validator

KIND = "uploaded_document_context_v1"


class SavedDocumentContext(BaseModel):
    file_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    name: str = Field(max_length=255)
    text: str = Field(default="", max_length=81000)
    sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    status: str = Field(default="complete", pattern=r'^(complete|partial)$')
    size: int = Field(ge=0, le=100 * 1024 * 1024)
    type: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def require_identity_or_legacy_text(self):
        if not self.file_id and not self.text.strip():
            raise ValueError("file_id_or_text_required")
        return self


def attachment_snapshot(value: SavedDocumentContext | None) -> dict:
    # Empty snapshot is intentional: removing a file must not resurrect the
    # preceding attachment when the conversation is reopened.
    return {"kind": KIND, "value": value.model_dump() if value else None}
