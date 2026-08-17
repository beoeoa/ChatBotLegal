from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.models import SettingsResponse, SettingsUpdate
from api.admin_config_history import record_config_revision
from api.auth import get_request_role, get_request_user_id
from open_notebook.domain.content_settings import ContentSettings
from open_notebook.exceptions import InvalidInputError

router = APIRouter()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request):
    """Get all application settings."""
    try:
        if get_request_role(request) != "admin":
            raise HTTPException(status_code=403, detail="Chỉ Admin được xem cấu hình hệ thống.")
        settings: ContentSettings = await ContentSettings.get_instance()  # type: ignore[assignment]

        return SettingsResponse(
            default_content_processing_engine_doc=settings.default_content_processing_engine_doc,
            default_content_processing_engine_url=settings.default_content_processing_engine_url,
            default_embedding_option=settings.default_embedding_option,
            auto_delete_files=settings.auto_delete_files,
            youtube_preferred_languages=settings.youtube_preferred_languages,
        )
    except Exception as e:
        logger.error(f"Error fetching settings: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error fetching settings"
        )


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(settings_update: SettingsUpdate, request: Request):
    """Update application settings."""
    try:
        if get_request_role(request) != "admin":
            raise HTTPException(status_code=403, detail="Chỉ Admin được sửa cấu hình hệ thống.")
        settings: ContentSettings = await ContentSettings.get_instance()  # type: ignore[assignment]
        before = SettingsResponse(
            default_content_processing_engine_doc=settings.default_content_processing_engine_doc,
            default_content_processing_engine_url=settings.default_content_processing_engine_url,
            default_embedding_option=settings.default_embedding_option,
            auto_delete_files=settings.auto_delete_files,
            youtube_preferred_languages=settings.youtube_preferred_languages,
        ).model_dump()

        # Update only provided fields
        if settings_update.default_content_processing_engine_doc is not None:
            # Cast to proper literal type
            from typing import Literal, cast

            settings.default_content_processing_engine_doc = cast(
                Literal["auto", "docling", "simple"],
                settings_update.default_content_processing_engine_doc,
            )
        if settings_update.default_content_processing_engine_url is not None:
            from typing import Literal, cast

            settings.default_content_processing_engine_url = cast(
                Literal["auto", "firecrawl", "jina", "simple"],
                settings_update.default_content_processing_engine_url,
            )
        if settings_update.default_embedding_option is not None:
            from typing import Literal, cast

            settings.default_embedding_option = cast(
                Literal["ask", "always", "never"],
                settings_update.default_embedding_option,
            )
        if settings_update.auto_delete_files is not None:
            from typing import Literal, cast

            settings.auto_delete_files = cast(
                Literal["yes", "no"], settings_update.auto_delete_files
            )
        if settings_update.youtube_preferred_languages is not None:
            settings.youtube_preferred_languages = (
                settings_update.youtube_preferred_languages
            )

        await settings.update()

        response = SettingsResponse(
            default_content_processing_engine_doc=settings.default_content_processing_engine_doc,
            default_content_processing_engine_url=settings.default_content_processing_engine_url,
            default_embedding_option=settings.default_embedding_option,
            auto_delete_files=settings.auto_delete_files,
            youtube_preferred_languages=settings.youtube_preferred_languages,
        )
        await record_config_revision(
            config_type="settings",
            before=before,
            after=response.model_dump(),
            actor_user_id=get_request_user_id(request),
            reason=(request.headers.get("X-Business-Reason") or "Cập nhật cấu hình hệ thống").strip(),
        )
        return response
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating settings: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error updating settings"
        )
