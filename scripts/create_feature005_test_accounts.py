"""Create disposable citizen, officer and admin accounts for Feature 005.

Passwords exist only in memory unless the explicit browser-login option is
used. The protected output is expected to live under the ignored reports tree.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OFFICER_DOMAINS = [
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
]


def _safe_suffix(value: str) -> str:
    cleaned = "".join(char for char in value if char.isalnum() or char in "_-")
    if not cleaned or len(cleaned) > 40:
        raise ValueError("suffix must contain 1-40 safe characters")
    return cleaned


async def create_accounts(
    output: Path,
    suffix: str,
    *,
    include_login_credentials: bool = False,
) -> None:
    from api.user_service import (
        authenticate_user_account,
        create_user_account,
        deactivate_user_account,
        get_user_by_identifier,
    )

    suffix = _safe_suffix(suffix)
    if output.exists():
        raise RuntimeError("Credential output already exists; refusing to overwrite")
    accounts = (
        ("citizen", "FEATURE005_CITIZEN_TOKEN", [], None),
        (
            "officer",
            "FEATURE005_OFFICER_TOKEN",
            OFFICER_DOMAINS,
            "Feature 005 legal acceptance",
        ),
        ("admin", "FEATURE005_ADMIN_TOKEN", [], "Feature 005 administration"),
    )
    issued: dict[str, str] = {}
    created_user_ids: list[str] = []
    try:
        for role, token_key, domains, department in accounts:
            username = f"feature005_{role}_{suffix}"
            if await get_user_by_identifier(username):
                raise RuntimeError("Feature 005 test account already exists")
            password = secrets.token_urlsafe(32)
            created = await create_user_account(
                {
                    "username": username,
                    "email": f"{username}@local.invalid",
                    "full_name": f"Feature 005 {role.title()}",
                    "password": password,
                    "role": role,
                    "department": department,
                    "allowed_domains": domains,
                    "ward": "Hải Phòng",
                    "ward_scope": "Hải Phòng",
                    "must_change_password": False,
                    "preferences": {"feature005_test_account": True},
                },
                actor_user_id=None,
                actor_role="system",
            )
            created_user_ids.append(str(created["id"]))
            auth = await authenticate_user_account(username, password, role)
            issued[token_key] = str(auth["token"])
            prefix = f"FEATURE005_{role.upper()}"
            issued[f"{prefix}_IDENTIFIER"] = username
            if include_login_credentials:
                issued[f"{prefix}_PASSWORD"] = password

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(f"{key}={value}\n" for key, value in issued.items()),
            encoding="utf-8",
        )
        output.chmod(0o600)
    except BaseException:
        output.unlink(missing_ok=True)
        for user_id in reversed(created_user_ids):
            try:
                await deactivate_user_account(
                    user_id,
                    actor_user_id=None,
                    actor_role="system",
                    reason="Feature 005 disposable account bootstrap rollback",
                )
            except Exception:
                # The caller treats bootstrap as failed. Any cleanup failure is
                # still visible through the user/audit store and blocks release.
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suffix", required=True)
    parser.add_argument(
        "--include-login-credentials",
        action="store_true",
        help="Store disposable usernames/passwords in the protected output for browser testing.",
    )
    args = parser.parse_args()
    asyncio.run(
        create_accounts(
            args.output,
            args.suffix,
            include_login_credentials=args.include_login_credentials,
        )
    )
    print("Created three isolated Feature 005 accounts; tokens were stored privately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
