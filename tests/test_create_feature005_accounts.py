from pathlib import Path
import subprocess
import sys

import pytest

from scripts.create_feature005_test_accounts import create_accounts

def test_feature005_accounts_cover_role_matrix_domains_without_static_credentials():
    source = Path("scripts/create_feature005_test_accounts.py").read_text(
        encoding="utf-8"
    )

    for domain in (
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat",
        "an_sinh_y_te_giao_duc",
    ):
        assert domain in source
    assert "secrets.token_urlsafe" in source
    assert "FEATURE005_CITIZEN_TOKEN" in source
    assert "FEATURE005_OFFICER_TOKEN" in source
    assert "FEATURE005_ADMIN_TOKEN" in source
    assert "print(password" not in source
    assert "print(token" not in source
    assert "--include-login-credentials" in source
    assert "Hải Phòng" in source
    assert "\ufffd" not in source


def test_feature005_account_launcher_runs_directly_from_repository_root():
    result = subprocess.run(
        [sys.executable, "scripts/create_feature005_test_accounts.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--output" in result.stdout


@pytest.mark.asyncio
async def test_feature005_account_bootstrap_rolls_back_partial_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.user_service as user_service

    created = 0
    deactivated: list[str] = []

    async def get_user(_identifier: str):
        return None

    async def create_user(_payload, **_kwargs):
        nonlocal created
        created += 1
        if created == 2:
            raise RuntimeError("injected bootstrap failure")
        return {"id": "user_account:first"}

    async def authenticate(_identifier: str, _password: str, _role: str):
        return {"token": "private-token"}

    async def deactivate(user_id: str, **_kwargs):
        deactivated.append(user_id)

    monkeypatch.setattr(user_service, "get_user_by_identifier", get_user)
    monkeypatch.setattr(user_service, "create_user_account", create_user)
    monkeypatch.setattr(user_service, "authenticate_user_account", authenticate)
    monkeypatch.setattr(user_service, "deactivate_user_account", deactivate)
    output = tmp_path / "private.env"

    with pytest.raises(RuntimeError, match="injected bootstrap failure"):
        await create_accounts(
            output,
            "rollback",
            include_login_credentials=True,
        )

    assert deactivated == ["user_account:first"]
    assert not output.exists()


@pytest.mark.asyncio
async def test_default_manifest_keeps_identifiers_required_for_safe_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.user_service as user_service

    async def get_user(_identifier: str):
        return None

    async def create_user(payload, **_kwargs):
        return {"id": f"user_account:{payload['username']}"}

    async def authenticate(identifier: str, _password: str, _role: str):
        return {"token": f"token-for-{identifier}"}

    async def deactivate(_user_id: str, **_kwargs):
        raise AssertionError("successful bootstrap must not roll back")

    monkeypatch.setattr(user_service, "get_user_by_identifier", get_user)
    monkeypatch.setattr(user_service, "create_user_account", create_user)
    monkeypatch.setattr(user_service, "authenticate_user_account", authenticate)
    monkeypatch.setattr(user_service, "deactivate_user_account", deactivate)
    output = tmp_path / "private.env"

    await create_accounts(output, "cleanup", include_login_credentials=False)

    manifest = output.read_text(encoding="utf-8")
    for role in ("CITIZEN", "OFFICER", "ADMIN"):
        assert f"FEATURE005_{role}_IDENTIFIER=feature005_{role.casefold()}_cleanup" in manifest
        assert f"FEATURE005_{role}_PASSWORD=" not in manifest
