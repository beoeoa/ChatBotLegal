"""Shared runtime configuration helpers.

This module keeps user-facing configuration in one place.  It deliberately
does not contain legal truth: domain labels are routing labels only and the
admin prompt is an additive instruction layered around the immutable legal
answer contract.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from api.models import (
    ChatModelPolicyConfig,
    LegalDomainConfig,
    OrganizationUnitConfig,
    SupportRoutingOption,
)
from api.organization_service import (
    effective_domain_assignments,
    effective_units,
    validate_operating_units,
)
from open_notebook.domain.content_settings import ContentSettings
from open_notebook.ai.models import DefaultModels


DOMAIN_LABELS: dict[str, str] = {
    "ho_tich_chung_thuc": "Hộ tịch - Chứng thực",
    "dat_dai_xay_dung": "Đất đai - Xây dựng",
    "an_sinh_y_te_giao_duc": "An sinh - Y tế - Giáo dục",
    "hanh_chinh_cong": "Hành chính công",
    "trat_tu_do_thi": "Trật tự đô thị",
}


def default_legal_domains() -> list[dict[str, Any]]:
    """Seed editable routing labels from the checked-in locality catalog."""
    from api.commune_catalog import commune_catalog

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in commune_catalog():
        code = str(group["field_code"]).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append(
            {
                "code": code,
                "name": str(group["name"]).strip(),
                "aliases": list(
                    dict.fromkeys(
                        str(value).strip()
                        for value in group.get("field_names", [])
                        if str(value).strip()
                    )
                ),
                "is_active": True,
                "sort_order": len(rows) + 1,
            }
        )
    return rows


def normalize_legal_domains(
    raw: Iterable[Mapping[str, Any]] | None,
) -> list[LegalDomainConfig]:
    rows = list(raw or [])
    if not rows:
        rows = default_legal_domains()
    return [LegalDomainConfig.model_validate(row) for row in rows]


def validate_legal_domains(
    domains: Iterable[LegalDomainConfig],
) -> list[LegalDomainConfig]:
    rows = list(domains)
    codes = [row.code for row in rows]
    if len(codes) != len(set(codes)):
        raise ValueError("legal_domain_code_duplicate")
    active_names = [row.name.casefold() for row in rows if row.is_active]
    if len(active_names) != len(set(active_names)):
        raise ValueError("legal_domain_name_duplicate")
    return sorted(rows, key=lambda row: (row.sort_order, row.name.casefold(), row.code))


def legal_domain_labels(
    domains: Iterable[LegalDomainConfig] | None = None,
) -> dict[str, str]:
    rows = list(domains) if domains is not None else normalize_legal_domains(None)
    return {**DOMAIN_LABELS, **{row.code: row.name for row in rows}}


def default_organization_units() -> list[dict[str, Any]]:
    """Build bootstrap units from the locality catalog, never a fixed room list."""
    from api.commune_catalog import commune_catalog
    units: list[dict[str, Any]] = []
    for group in commune_catalog():
        units.append({
            "id": "org-" + group["code"].replace("_", "-"),
            "code": group["code"], "name": group["department"],
            "short_name": group["name"], "aliases": [],
            "domain_codes": [group["field_code"]],
            "domain_assignments": [{"domain_code": group["field_code"], "responsibility": "primary"}],
            "support_enabled": True, "is_active": True, "sort_order": len(units) + 1,
        })
    return units


def normalize_units(raw: Iterable[Mapping[str, Any]] | None) -> list[OrganizationUnitConfig]:
    rows = list(raw or [])
    if not rows:
        rows = default_organization_units()
    return [OrganizationUnitConfig.model_validate(row) for row in rows]


def validate_units(units: Iterable[OrganizationUnitConfig]) -> list[OrganizationUnitConfig]:
    return validate_operating_units(units)


def normalize_model_policy(raw: Iterable[Mapping[str, Any]] | None) -> list[ChatModelPolicyConfig]:
    return [ChatModelPolicyConfig.model_validate(row) for row in (raw or [])]


def validate_model_policy(policy: Iterable[ChatModelPolicyConfig]) -> list[ChatModelPolicyConfig]:
    rows = list(policy)
    option_ids = [row.option_id for row in rows]
    if len(option_ids) != len(set(option_ids)):
        raise ValueError("chat_model_option_duplicate")
    model_ids = [row.model_id for row in rows]
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("chat_model_duplicate")
    for row in rows:
        if not set(row.default_for).issubset(set(row.audiences)):
            raise ValueError("chat_model_default_audience_mismatch")
    for audience in ("citizen", "officer"):
        defaults = [row for row in rows if row.is_active and audience in row.default_for]
        if len(defaults) > 1:
            raise ValueError(f"chat_model_multiple_defaults:{audience}")
    return rows


def routing_options(
    units: Iterable[OrganizationUnitConfig],
    domains: Iterable[LegalDomainConfig] | None = None,
) -> list[SupportRoutingOption]:
    labels = legal_domain_labels(domains)
    result: list[SupportRoutingOption] = []
    for unit in sorted(units, key=lambda item: (item.sort_order, item.name.casefold())):
        if not unit.is_active or not unit.support_enabled:
            continue
        for assignment in effective_domain_assignments(unit):
            domain = assignment.domain_code
            result.append(
                SupportRoutingOption(
                    domain=domain,
                    domain_name=labels.get(domain, domain.replace("_", " ").title()),
                    unit_id=unit.id,
                    unit_name=unit.name,
                    unit_short_name=unit.short_name,
                    responsibility=assignment.responsibility,
                )
            )
    return result


async def active_settings() -> ContentSettings:
    settings = await ContentSettings.get_instance()  # type: ignore[assignment]
    # Older singleton records do not have the additive defaults in storage.
    if not getattr(settings, "organization_units", None):
        settings.organization_units = default_organization_units()
    if not getattr(settings, "legal_domains", None):
        settings.legal_domains = default_legal_domains()
    return settings


async def active_organization_units(
    settings: ContentSettings | None = None,
) -> list[OrganizationUnitConfig]:
    current = settings or await active_settings()
    configured = validate_units(
        normalize_units(getattr(current, "organization_units", None))
    )
    return await effective_units(
        configured,
        mode=getattr(current, "organization_routing_mode", "legacy"),
    )


async def resolve_model_option(option_id: str | None, role: str) -> str | None:
    """Resolve a role-scoped public option to a configured model ID."""

    if not option_id:
        return None
    settings = await active_settings()
    # Admin previews the officer model allow-list on the search screen. Apply
    # the same audience mapping when executing the selection; otherwise every
    # option shown to Admin is rejected as CHAT_MODEL_NOT_ALLOWED.
    effective_role = "officer" if str(role).casefold() == "admin" else role
    for raw in settings.chat_model_policy or []:
        policy = ChatModelPolicyConfig.model_validate(raw)
        if (
            policy.option_id == option_id
            and policy.is_active
            and effective_role in policy.audiences
        ):
            return policy.model_id
    raise PermissionError("CHAT_MODEL_NOT_ALLOWED")


async def public_model_options(role: str) -> list[dict[str, Any]]:
    """Project the Admin allow-list exactly for the requested public role.

    Runtime/provider health belongs to generation error handling. Silently
    removing an Admin-approved option here made the chat selector disagree
    with Settings and left users unable to tell whether configuration was
    saved.
    """

    settings = await active_settings()
    result: list[dict[str, Any]] = []
    policies: list[ChatModelPolicyConfig] = []
    for raw in settings.chat_model_policy or []:
        policy = ChatModelPolicyConfig.model_validate(raw)
        if not policy.is_active or role not in policy.audiences:
            continue
        policies.append(policy)
        result.append(
            {
                "option_id": policy.option_id,
                "display_name": policy.display_name,
                "audiences": list(policy.audiences),
                "is_default": role in policy.default_for,
                "is_active": policy.is_active,
            }
        )
    if result and not any(item["is_default"] for item in result):
        # Older policy rows did not require an audience default. Honour the
        # global chat default when it is part of the allow-list; otherwise use
        # the first visible option deterministically. This avoids a selector
        # that always appears to be stuck on whichever model happened to be
        # first without changing the stored policy behind the Admin's back.
        try:
            default_model_id = str(
                (await DefaultModels.get_instance()).default_chat_model or ""
            )
        except Exception:
            default_model_id = ""
        default_index = next(
            (
                index
                for index, policy in enumerate(policies)
                if str(policy.model_id) == default_model_id
            ),
            0,
        )
        result[default_index]["is_default"] = True
    return result
