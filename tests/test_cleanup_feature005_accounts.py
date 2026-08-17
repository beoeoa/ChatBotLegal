from __future__ import annotations

from pathlib import Path

import pytest

from scripts.cleanup_feature005_test_accounts import cleanup_accounts


@pytest.mark.asyncio
async def test_cleanup_deactivates_only_marked_disposable_accounts(
    tmp_path: Path,
) -> None:
    credential_path = tmp_path / "roles.env"
    credential_path.write_text(
        "\n".join(
            [
                "FEATURE005_CITIZEN_IDENTIFIER=feature005_citizen_gate_1",
                "FEATURE005_OFFICER_IDENTIFIER=feature005_officer_gate_1",
                "FEATURE005_ADMIN_IDENTIFIER=feature005_admin_gate_1",
                "FEATURE005_ADMIN_TOKEN=do-not-print",
            ]
        ),
        encoding="utf-8",
    )
    users = {
        identifier: {
            "id": f"user_account:{identifier}",
            "username": identifier,
            "is_active": True,
        }
        for identifier in (
            "feature005_citizen_gate_1",
            "feature005_officer_gate_1",
            "feature005_admin_gate_1",
        )
    }
    deactivated: list[str] = []

    async def get_user(identifier: str):
        return users.get(identifier)

    async def get_profile(_user_id: str):
        return {"preferences": {"feature005_test_account": True}}

    async def deactivate(user_id: str, **_kwargs):
        deactivated.append(user_id)

    result = await cleanup_accounts(
        credential_path,
        get_user=get_user,
        get_profile=get_profile,
        deactivate=deactivate,
    )

    assert result == {"deactivated": 3, "already_inactive": 0}
    assert len(deactivated) == 3


@pytest.mark.asyncio
async def test_cleanup_refuses_unmarked_or_non_feature005_account(
    tmp_path: Path,
) -> None:
    credential_path = tmp_path / "roles.env"
    credential_path.write_text(
        "\n".join(
            [
                "FEATURE005_CITIZEN_IDENTIFIER=feature005_citizen_gate_2",
                "FEATURE005_OFFICER_IDENTIFIER=feature005_officer_gate_2",
                "FEATURE005_ADMIN_IDENTIFIER=admin",
            ]
        ),
        encoding="utf-8",
    )

    async def get_user(identifier: str):
        return {
            "id": f"user_account:{identifier}",
            "username": identifier,
            "is_active": True,
        }

    async def get_profile(_user_id: str):
        return {"preferences": {}}

    async def deactivate(_user_id: str, **_kwargs):
        raise AssertionError("must not deactivate an unmarked account")

    with pytest.raises(RuntimeError, match="refusing to deactivate"):
        await cleanup_accounts(
            credential_path,
            get_user=get_user,
            get_profile=get_profile,
            deactivate=deactivate,
        )
