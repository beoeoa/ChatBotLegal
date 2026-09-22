from __future__ import annotations

from typing import Any, Literal, cast
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.admin_config_history import record_config_revision
from api.auth import get_request_role, get_request_user_id
from api.chat_behavior_policy import admin_style_addendum, compile_admin_style
from api.models import (
    ChatModelPolicyConfig,
    LegalDomainConfig,
    ModelOptionResponse,
    OrganizationUnitConfig,
    SettingsResponse,
    SettingsUpdate,
)
from api.system_settings import (
    active_organization_units,
    active_settings,
    normalize_legal_domains,
    normalize_model_policy,
    normalize_units,
    public_model_options,
    validate_legal_domains,
    validate_model_policy,
    validate_units,
)
from api.organization_service import assignment_readiness, sync_unit_projection
from open_notebook.domain.content_settings import ContentSettings
from open_notebook.exceptions import InvalidInputError
from api.user_service import list_users_with_profiles

router = APIRouter()


def _require_admin(request: Request, *, message: str) -> None:
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail=message)


def _validate_commune_unit(
    unit: OrganizationUnitConfig,
    previous: OrganizationUnitConfig | None = None,
    legal_domains: list[LegalDomainConfig] | None = None,
) -> None:
    domains = validate_legal_domains(
        legal_domains if legal_domains is not None else normalize_legal_domains(None)
    )
    allowed = {item.code for item in domains if item.is_active}
    retained = set(previous.domain_codes) if previous else set()
    if previous:
        retained.update(item.domain_code for item in previous.domain_assignments)
    assigned = set(unit.domain_codes) | {item.domain_code for item in unit.domain_assignments}
    if assigned - allowed - retained:
        raise HTTPException(
            status_code=422,
            detail="Lĩnh vực chưa có trong danh mục đang hoạt động.",
        )


def _validate_unit_write(units: list[OrganizationUnitConfig]) -> list[OrganizationUnitConfig]:
    try:
        return validate_units(units)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _settings_response(settings: ContentSettings) -> SettingsResponse:
    domains = validate_legal_domains(
        normalize_legal_domains(getattr(settings, "legal_domains", None))
    )
    units = validate_units(normalize_units(settings.organization_units))
    policy = validate_model_policy(normalize_model_policy(settings.chat_model_policy))
    return SettingsResponse(
        default_content_processing_engine_doc=settings.default_content_processing_engine_doc,
        default_content_processing_engine_url=settings.default_content_processing_engine_url,
        default_embedding_option=settings.default_embedding_option,
        auto_delete_files=settings.auto_delete_files,
        youtube_preferred_languages=settings.youtube_preferred_languages,
        system_name=(settings.system_name or "Pháp luật Hải Phòng").strip(),
        organization_name=(settings.organization_name or "").strip(),
        system_prompt_addendum=(settings.system_prompt_addendum or "").strip(),
        active_prompt_revision=int(settings.active_prompt_revision or 1),
        config_revision=int(settings.config_revision or 1),
        organization_routing_mode=getattr(
            settings, "organization_routing_mode", "legacy"
        ),
        organization_hybrid_started_at=getattr(settings, "organization_hybrid_started_at", None),
        legal_domains=domains,
        organization_units=units,
        chat_model_policy=policy,
    )


def _bump_revision(settings: ContentSettings, *, prompt_changed: bool) -> None:
    settings.config_revision = int(settings.config_revision or 1) + 1
    if prompt_changed:
        settings.active_prompt_revision = int(settings.active_prompt_revision or 1) + 1


async def _assert_unit_deactivation_safe(before_units: list[OrganizationUnitConfig], after_units: list[OrganizationUnitConfig]) -> None:
    deactivated = {
        item.id for item in before_units if item.is_active
        and not next((candidate.is_active for candidate in after_units if candidate.id == item.id), False)
    }
    if not deactivated:
        return
    users = await list_users_with_profiles()
    if any(
        user.get("role") == "officer"
        and user.get("is_active", True)
        and _user_organization_unit_id(user) in deactivated
        for user in users
    ):
        raise HTTPException(status_code=409, detail="organization_unit_has_active_officers")


def _assert_domain_deactivation_safe(
    before_domains: list[LegalDomainConfig],
    after_domains: list[LegalDomainConfig],
    units: list[OrganizationUnitConfig],
) -> None:
    after_by_code = {item.code: item for item in after_domains}
    deactivated = {
        item.code
        for item in before_domains
        if item.is_active
        and not after_by_code.get(item.code, item).is_active
    }
    assigned = {
        code
        for unit in units
        if unit.is_active
        for code in {
            *unit.domain_codes,
            *(assignment.domain_code for assignment in unit.domain_assignments),
        }
    }
    blocked = sorted(deactivated & assigned)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legal_domain_in_use",
                "domains": blocked,
                "message": "Hãy bỏ lĩnh vực khỏi các phòng ban đang hoạt động trước khi ngừng lĩnh vực.",
            },
        )


def _user_organization_unit_id(user: dict) -> str | None:
    profile = user.get("profile") or {}
    value = user.get("organization_unit_id") or (
        profile.get("organization_unit_id") if isinstance(profile, dict) else None
    )
    return str(value).strip() if value else None


def _canonicalize_model_policy(
    body: list[ChatModelPolicyConfig],
    language_models: list[Any],
) -> list[ChatModelPolicyConfig]:
    """Resolve model IDs from both current and legacy client formats."""

    by_id = {
        str(model.id): model
        for model in language_models
        if getattr(model, "id", None)
    }
    by_short_id = {
        key.split(":", 1)[1]: model
        for key, model in by_id.items()
        if ":" in key
    }
    by_name = {
        str(model.name).strip().casefold(): model
        for model in language_models
        if getattr(model, "name", None)
    }
    canonical_policy: list[ChatModelPolicyConfig] = []
    for item in body:
        requested_id = str(item.model_id or "").strip()
        model = (
            by_id.get(requested_id)
            or by_short_id.get(requested_id)
            or by_name.get(requested_id.casefold())
        )
        if model is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MODEL_NOT_FOUND",
                    "message": "Một model trong chính sách không còn tồn tại. Hãy tải lại danh sách model rồi lưu lại.",
                },
            )
        if str(getattr(model, "type", "")).casefold() != "language":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MODEL_NOT_LANGUAGE",
                    "message": "Chính sách chỉ được áp dụng cho model ngôn ngữ.",
                },
            )
        canonical_policy.append(item.model_copy(update={"model_id": str(model.id)}))
    return canonical_policy


async def _record_and_return(
    settings: ContentSettings,
    *,
    before: dict,
    request: Request,
) -> SettingsResponse:
    response = _settings_response(settings)
    await settings.update()
    await record_config_revision(
        config_type="settings",
        before=before,
        after=response.model_dump(),
        actor_user_id=get_request_user_id(request),
        reason=(request.headers.get("X-Business-Reason") or "Cập nhật cấu hình hệ thống").strip(),
    )
    return response


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request) -> SettingsResponse:
    """Get all application settings (admin only)."""
    _require_admin(request, message="Chỉ Admin được xem cấu hình hệ thống.")
    try:
        return _settings_response(await active_settings())
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error fetching settings: {}", exc)
        raise HTTPException(status_code=500, detail="Error fetching settings") from exc


@router.get("/settings/system", response_model=SettingsResponse)
async def get_system_settings(request: Request) -> SettingsResponse:
    """Explicit alias for the product-level configuration panel."""
    return await get_settings(request)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(settings_update: SettingsUpdate, request: Request) -> SettingsResponse:
    """Update technical and product settings with optimistic concurrency."""
    _require_admin(request, message="Chỉ Admin được sửa cấu hình hệ thống.")
    try:
        settings: ContentSettings = await active_settings()  # type: ignore[assignment]
        current = _settings_response(settings)
        if (
            settings_update.expected_config_revision is not None
            and settings_update.expected_config_revision != current.config_revision
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CONFIG_REVISION_CONFLICT",
                    "current_revision": current.config_revision,
                },
            )
        before = current.model_dump()
        prompt_changed = False

        if settings_update.default_content_processing_engine_doc is not None:
            settings.default_content_processing_engine_doc = cast(
                Literal["auto", "docling", "simple"],
                settings_update.default_content_processing_engine_doc,
            )
        if settings_update.default_content_processing_engine_url is not None:
            settings.default_content_processing_engine_url = cast(
                Literal["auto", "firecrawl", "jina", "simple"],
                settings_update.default_content_processing_engine_url,
            )
        if settings_update.default_embedding_option is not None:
            settings.default_embedding_option = cast(
                Literal["ask", "always", "never"],
                settings_update.default_embedding_option,
            )
        if settings_update.auto_delete_files is not None:
            settings.auto_delete_files = cast(
                Literal["yes", "no"], settings_update.auto_delete_files
            )
        if settings_update.youtube_preferred_languages is not None:
            settings.youtube_preferred_languages = settings_update.youtube_preferred_languages
        if settings_update.system_name is not None:
            settings.system_name = settings_update.system_name.strip()
        if settings_update.organization_name is not None:
            settings.organization_name = settings_update.organization_name.strip()
        if settings_update.system_prompt_addendum is not None:
            next_prompt = settings_update.system_prompt_addendum.strip()
            prompt_changed = next_prompt != (settings.system_prompt_addendum or "").strip()
            settings.system_prompt_addendum = next_prompt
        if settings_update.organization_routing_mode is not None:
            requested_mode = settings_update.organization_routing_mode
            current_mode = getattr(settings, "organization_routing_mode", "legacy")
            if requested_mode == "unit_primary" and current_mode != "unit_primary":
                readiness = await assignment_readiness(settings=settings)
                if not readiness["ready_for_unit_primary"]:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "organization_unit_cutover_not_ready",
                            "message": "Chưa thể chuyển hoàn toàn sang phòng ban: cần hoàn tất và đồng bộ toàn bộ dữ liệu phân công.",
                            "readiness": readiness,
                        },
                    )
            settings.organization_routing_mode = requested_mode
            if requested_mode != current_mode:
                if requested_mode == "hybrid":
                    settings.organization_hybrid_started_at = datetime.now(timezone.utc).isoformat()
                elif requested_mode != "unit_primary":
                    settings.organization_hybrid_started_at = None
        next_domains = list(current.legal_domains)
        if settings_update.legal_domains is not None:
            next_domains = validate_legal_domains(settings_update.legal_domains)
            removed_domain_codes = {
                item.code for item in current.legal_domains
            } - {item.code for item in next_domains}
            if removed_domain_codes:
                raise HTTPException(
                    status_code=409,
                    detail="legal_domain_hard_delete_forbidden",
                )
            prospective_units = (
                settings_update.organization_units
                if settings_update.organization_units is not None
                else current.organization_units
            )
            _assert_domain_deactivation_safe(
                current.legal_domains,
                next_domains,
                list(prospective_units),
            )
            settings.legal_domains = [item.model_dump() for item in next_domains]
        if settings_update.organization_units is not None:
            next_units = validate_units(settings_update.organization_units)
            for unit in next_units:
                _validate_commune_unit(
                    unit,
                    next(
                        (
                            item
                            for item in current.organization_units
                            if item.id == unit.id
                        ),
                        None,
                    ),
                    next_domains,
                )
            removed_ids = {item.id for item in current.organization_units} - {item.id for item in next_units}
            if removed_ids:
                raise HTTPException(status_code=409, detail="organization_unit_hard_delete_forbidden")
            await _assert_unit_deactivation_safe(
                current.organization_units,
                next_units,
            )
            settings.organization_units = [item.model_dump() for item in next_units]
            if getattr(settings, "organization_routing_mode", "legacy") != "legacy":
                await sync_unit_projection(next_units)
        if settings_update.chat_model_policy is not None:
            settings.chat_model_policy = [
                item.model_dump() for item in validate_model_policy(settings_update.chat_model_policy)
            ]

        if before != _settings_response(settings).model_dump():
            _bump_revision(settings, prompt_changed=prompt_changed)
        return await _record_and_return(settings, before=before, request=request)
    except HTTPException:
        raise
    except (InvalidInputError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error updating settings: {}", exc)
        raise HTTPException(status_code=500, detail="Error updating settings") from exc


@router.put("/settings/system", response_model=SettingsResponse)
async def update_system_settings(settings_update: SettingsUpdate, request: Request) -> SettingsResponse:
    return await update_settings(settings_update, request)


@router.post("/settings/system/prompt/check")
async def check_system_prompt(settings_update: SettingsUpdate, request: Request) -> dict:
    """Validate an additive prompt without saving or activating it."""
    _require_admin(request, message="Chỉ Admin được kiểm tra system prompt.")
    prompt = (settings_update.system_prompt_addendum or "").strip()
    warnings: list[str] = []
    lowered = prompt.casefold()
    if any(marker in lowered for marker in ("bỏ qua nguồn", "không cần trích dẫn", "ignore evidence", "ignore citations")):
        warnings.append("Prompt có dấu hiệu yêu cầu bỏ qua quy tắc nguồn hoặc trích dẫn.")
    accepted_style = admin_style_addendum(prompt)
    if prompt and not accepted_style:
        warnings.append("Hướng dẫn quản trị chỉ được cấu hình giọng điệu, cách xưng hô, độ dài hoặc cách trình bày.")
    elif prompt and accepted_style != prompt:
        warnings.append("Một phần hướng dẫn nằm ngoài phạm vi phong cách và sẽ không được đưa vào prompt runtime.")
    from api.direct_legal_answer_service import build_prompt as build_direct_prompt
    from api.legal_structured_answer import build_unified_chat_answer_prompt

    previews = {
        "legal_direct": build_direct_prompt(
            question="[Câu hỏi hiện tại]",
            role="citizen",
            context="[Nguồn pháp luật đã truy xuất]",
            system_prompt_addendum=prompt,
        ),
        "natural_chat": build_unified_chat_answer_prompt(
            question="[Tin nhắn hiện tại]",
            role="citizen",
            route="chat_meta",
            conversation_context="[Lịch sử hội thoại đã phân bổ]",
            system_prompt_addendum=prompt,
        ),
    }
    return {
        "valid": len(prompt) <= 8000 and not warnings,
        "length": len(prompt),
        "warnings": warnings,
        "applied_style": accepted_style,
        "style_compilation": compile_admin_style(prompt),
        "answer_prompt_revision": "chat-answer-v2",
        "runtime_prompt_previews": previews,
        "branches_using_prompt": ["legal_direct", "natural_chat", "legal_compatibility"],
        "immutable_legal_contract_preserved": True,
        "message": "Prompt chỉ được bổ sung hướng dẫn diễn đạt; hợp đồng nguồn, hiệu lực và từ chối vẫn được giữ nguyên.",
    }


@router.get("/settings/organization-units", response_model=list[OrganizationUnitConfig])
async def list_organization_units(request: Request) -> list[OrganizationUnitConfig]:
    _require_admin(request, message="Chỉ Admin được quản lý cơ cấu tổ chức.")
    settings = await active_settings()
    return await active_organization_units(settings)


@router.get("/settings/legal-domains", response_model=list[LegalDomainConfig])
async def list_legal_domains(request: Request) -> list[LegalDomainConfig]:
    _require_admin(request, message="Chỉ Admin được quản lý lĩnh vực.")
    settings = await active_settings()
    return validate_legal_domains(
        normalize_legal_domains(getattr(settings, "legal_domains", None))
    )


@router.post(
    "/settings/legal-domains",
    response_model=LegalDomainConfig,
    status_code=201,
)
async def create_legal_domain(
    body: LegalDomainConfig,
    request: Request,
) -> LegalDomainConfig:
    _require_admin(request, message="Chỉ Admin được quản lý lĩnh vực.")
    settings = await active_settings()
    domains = validate_legal_domains(
        normalize_legal_domains(getattr(settings, "legal_domains", None))
    )
    if any(item.code == body.code for item in domains):
        raise HTTPException(status_code=409, detail="legal_domain_code_duplicate")
    if any(item.is_active and item.name.casefold() == body.name.casefold() for item in domains):
        raise HTTPException(status_code=409, detail="legal_domain_name_duplicate")
    created = body.model_copy(
        update={"sort_order": body.sort_order or len(domains) + 1}
    )
    before = _settings_response(settings).model_dump()
    domains = validate_legal_domains([*domains, created])
    settings.legal_domains = [item.model_dump() for item in domains]
    _bump_revision(settings, prompt_changed=False)
    await _record_and_return(settings, before=before, request=request)
    return created


@router.put(
    "/settings/legal-domains/{domain_code}",
    response_model=LegalDomainConfig,
)
async def update_legal_domain(
    domain_code: str,
    body: LegalDomainConfig,
    request: Request,
) -> LegalDomainConfig:
    _require_admin(request, message="Chỉ Admin được quản lý lĩnh vực.")
    if body.code != domain_code:
        raise HTTPException(status_code=400, detail="legal_domain_code_immutable")
    settings = await active_settings()
    domains = validate_legal_domains(
        normalize_legal_domains(getattr(settings, "legal_domains", None))
    )
    current = next((item for item in domains if item.code == domain_code), None)
    if current is None:
        raise HTTPException(status_code=404, detail="legal_domain_not_found")
    if any(
        item.code != domain_code
        and item.is_active
        and body.is_active
        and item.name.casefold() == body.name.casefold()
        for item in domains
    ):
        raise HTTPException(status_code=409, detail="legal_domain_name_duplicate")
    updated = [body if item.code == domain_code else item for item in domains]
    units = validate_units(normalize_units(settings.organization_units))
    _assert_domain_deactivation_safe(domains, updated, units)
    before = _settings_response(settings).model_dump()
    settings.legal_domains = [
        item.model_dump() for item in validate_legal_domains(updated)
    ]
    _bump_revision(settings, prompt_changed=False)
    await _record_and_return(settings, before=before, request=request)
    return body


@router.delete(
    "/settings/legal-domains/{domain_code}",
    response_model=LegalDomainConfig,
)
async def delete_legal_domain(
    domain_code: str,
    request: Request,
) -> LegalDomainConfig:
    """Retire a routing label while preserving stored legal references."""
    _require_admin(request, message="Chỉ Admin được quản lý lĩnh vực.")
    settings = await active_settings()
    domains = validate_legal_domains(
        normalize_legal_domains(getattr(settings, "legal_domains", None))
    )
    current = next((item for item in domains if item.code == domain_code), None)
    if current is None:
        raise HTTPException(status_code=404, detail="legal_domain_not_found")
    retired = current.model_copy(update={"is_active": False})
    updated = [retired if item.code == domain_code else item for item in domains]
    units = validate_units(normalize_units(settings.organization_units))
    _assert_domain_deactivation_safe(domains, updated, units)
    before = _settings_response(settings).model_dump()
    settings.legal_domains = [item.model_dump() for item in updated]
    _bump_revision(settings, prompt_changed=False)
    await _record_and_return(settings, before=before, request=request)
    return retired


@router.post("/settings/organization-units", response_model=OrganizationUnitConfig, status_code=201)
async def create_organization_unit(body: OrganizationUnitConfig, request: Request) -> OrganizationUnitConfig:
    _require_admin(request, message="Chỉ Admin được quản lý cơ cấu tổ chức.")
    settings = await active_settings()
    units = validate_units(normalize_units(settings.organization_units))
    if any(item.id == body.id or item.code == body.code for item in units):
        raise HTTPException(status_code=409, detail="organization_unit_duplicate")
    before = _settings_response(settings).model_dump()
    _validate_commune_unit(
        body,
        legal_domains=normalize_legal_domains(
            getattr(settings, "legal_domains", None)
        ),
    )
    units.append(body)
    settings.organization_units = [item.model_dump() for item in _validate_unit_write(units)]
    if getattr(settings, "organization_routing_mode", "legacy") != "legacy":
        await sync_unit_projection(validate_units(units))
    _bump_revision(settings, prompt_changed=False)
    await _record_and_return(settings, before=before, request=request)
    return body


@router.put("/settings/organization-units/{unit_id}", response_model=OrganizationUnitConfig)
async def update_organization_unit(unit_id: str, body: OrganizationUnitConfig, request: Request) -> OrganizationUnitConfig:
    _require_admin(request, message="Chỉ Admin được quản lý cơ cấu tổ chức.")
    if body.id != unit_id:
        raise HTTPException(status_code=400, detail="organization_unit_id_immutable")
    settings = await active_settings()
    units = validate_units(normalize_units(settings.organization_units))
    if not any(item.id == unit_id for item in units):
        raise HTTPException(status_code=404, detail="organization_unit_not_found")
    if any(item.id != unit_id and item.code == body.code for item in units):
        raise HTTPException(status_code=409, detail="organization_unit_code_duplicate")
    before = _settings_response(settings).model_dump()
    _validate_commune_unit(
        body,
        next(item for item in units if item.id == unit_id),
        normalize_legal_domains(getattr(settings, "legal_domains", None)),
    )
    updated = [body if item.id == unit_id else item for item in units]
    await _assert_unit_deactivation_safe(units, updated)
    settings.organization_units = [item.model_dump() for item in _validate_unit_write(updated)]
    if getattr(settings, "organization_routing_mode", "legacy") != "legacy":
        await sync_unit_projection(validate_units(updated))
    _bump_revision(settings, prompt_changed=False)
    await _record_and_return(settings, before=before, request=request)
    return body


@router.delete("/settings/organization-units/{unit_id}", response_model=OrganizationUnitConfig)
async def delete_organization_unit(unit_id: str, request: Request) -> OrganizationUnitConfig:
    """Retire a unit without breaking historical document/procedure references."""
    _require_admin(request, message="Chỉ Admin được quản lý cơ cấu tổ chức.")
    settings = await active_settings()
    units = validate_units(normalize_units(settings.organization_units))
    target = next((item for item in units if item.id == unit_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="organization_unit_not_found")
    # This explicit retirement action preserves all references. Request-time
    # officer scopes exclude inactive units; accounts and history are not deleted.
    before = _settings_response(settings).model_dump()
    mode = str(getattr(settings, "organization_routing_mode", "legacy"))
    target = target.model_copy(update={"is_active": False, "support_enabled": False})
    next_units = [target if item.id == unit_id else item for item in units]
    await _assert_unit_deactivation_safe(units, next_units)
    settings.organization_units = [item.model_dump() for item in next_units]
    if mode != "legacy":
        await sync_unit_projection(next_units)
    _bump_revision(settings, prompt_changed=False)
    await _record_and_return(settings, before=before, request=request)
    return target


@router.get("/settings/organization-units/readiness")
async def organization_unit_readiness(request: Request) -> dict[str, Any]:
    _require_admin(request, message="Chỉ Admin được xem trạng thái chuyển đổi phòng ban.")
    settings = await active_settings()
    units = await active_organization_units(settings)
    readiness = await assignment_readiness(settings=settings)
    return {
        "mode": getattr(settings, "organization_routing_mode", "legacy"),
        "unit_count": len(units),
        "active_unit_count": sum(1 for unit in units if unit.is_active),
        **readiness,
    }


@router.get("/chat/model-options", response_model=list[ModelOptionResponse])
async def get_chat_model_options(request: Request) -> list[ModelOptionResponse]:
    role = str(get_request_role(request) or "citizen")
    if role == "admin":
        role = "officer"
    return [ModelOptionResponse.model_validate(item) for item in await public_model_options(role)]


@router.put("/settings/model-policy", response_model=list[ChatModelPolicyConfig])
async def update_chat_model_policy(body: list[ChatModelPolicyConfig], request: Request) -> list[ChatModelPolicyConfig]:
    _require_admin(request, message="Chỉ Admin được cấu hình model cho người dùng.")
    try:
        from open_notebook.ai.models import Model

        # The model list endpoint returns canonical Surreal IDs (for example
        # ``model:abc``), but older clients/configurations may retain the
        # record key without its table prefix. Resolve both forms once and
        # persist the canonical ID so a stale UI payload cannot make saving
        # fail with a misleading generic error.
        language_models = await Model.get_models_by_type("language")
        canonical_policy = _canonicalize_model_policy(body, language_models)
        policy = validate_model_policy(canonical_policy)
        settings = await active_settings()
        before = _settings_response(settings).model_dump()
        settings.chat_model_policy = [item.model_dump() for item in policy]
        _bump_revision(settings, prompt_changed=False)
        await _record_and_return(settings, before=before, request=request)
        return policy
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error updating model policy: {}", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "MODEL_POLICY_SAVE_FAILED",
                "message": "Không thể lưu chính sách model do lỗi lưu cấu hình. Hãy thử lại sau khi tải lại trang.",
                "retryable": True,
            },
        ) from exc
