"""Create two disposable, non-shared Feature 3 pilot accounts.

This script is intended to run *inside* the isolated pilot API container.  It
generates passwords and session tokens in memory, writes them exactly once to a
mode-0600 environment file, and never prints credentials, questions or user
profiles.  The file is deliberately outside source control and must be loaded
into the benchmark process through environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from pathlib import Path


DEFAULT_OUTPUT = Path("/app/data/private/feature3_pilot_test_credentials.env")
ACCOUNT_SUFFIX = "f3_20260717"


def _password() -> str:
    return secrets.token_urlsafe(24)


def _env_quote(value: str) -> str:
    """Write a conservative dotenv value without exposing it on stdout."""
    return value.replace("\\", "\\\\").replace("\n", "")


async def create_accounts(output: Path) -> None:
    from api.user_service import authenticate_user_account, create_user_account, get_user_by_identifier

    accounts = (
        {
            "username": f"pilot_citizen_{ACCOUNT_SUFFIX}",
            "email": f"pilot-citizen-{ACCOUNT_SUFFIX}@local.invalid",
            "full_name": "Feature 3 Pilot Citizen",
            "role": "citizen",
            "department": None,
            "allowed_domains": [],
        },
        {
            "username": f"pilot_officer_{ACCOUNT_SUFFIX}",
            "email": f"pilot-officer-{ACCOUNT_SUFFIX}@local.invalid",
            "full_name": "Feature 3 Pilot Officer",
            "role": "officer",
            "department": "Hộ tịch - Chứng thực",
            "allowed_domains": ["ho_tich_chung_thuc"],
        },
    )
    for account in accounts:
        if await get_user_by_identifier(account["username"]):
            raise RuntimeError("Pilot test account already exists; refusing to rotate credentials")

    issued: dict[str, str] = {}
    for account in accounts:
        password = _password()
        user = await create_user_account(
            {
                **account,
                "password": password,
                "ward": "Phường Lê Chân, Hải Phòng",
                "ward_scope": "Phường Lê Chân, Hải Phòng",
                "must_change_password": False,
                "preferences": {"pilot_test_account": True},
            },
            actor_user_id=None,
            actor_role="system",
        )
        auth = await authenticate_user_account(account["username"], password, account["role"])
        prefix = "CITIZEN" if account["role"] == "citizen" else "OFFICER"
        issued[f"PILOT_{prefix}_TOKEN"] = str(auth["token"])
        issued[f"PILOT_{prefix}_USER_ID"] = str(user["id"])

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError("Pilot credential file already exists; refusing to overwrite it")
    content = {
        "PILOT_CITIZEN_TOKEN": issued["PILOT_CITIZEN_TOKEN"],
        "PILOT_OFFICER_TOKEN": issued["PILOT_OFFICER_TOKEN"],
        "PILOT_ISOLATION_ACCOUNT_A_TOKEN": issued["PILOT_CITIZEN_TOKEN"],
        "PILOT_ISOLATION_ACCOUNT_B_TOKEN": issued["PILOT_OFFICER_TOKEN"],
        "PILOT_CITIZEN_USER_ID": issued["PILOT_CITIZEN_USER_ID"],
        "PILOT_OFFICER_USER_ID": issued["PILOT_OFFICER_USER_ID"],
    }
    output.write_text(
        "".join(f"{key}={_env_quote(value)}\n" for key, value in content.items()),
        encoding="utf-8",
    )
    output.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(create_accounts(args.output))
    print("Created two isolated pilot test accounts; credentials were written to the protected output file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
