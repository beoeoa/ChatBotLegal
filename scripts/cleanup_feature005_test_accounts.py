"""Deactivate and revoke sessions for one exact disposable Feature 005 account set.

The credential file is treated only as a private identifier manifest. An
account is never touched unless its identifier has the ``feature005_`` prefix
and its persisted profile carries ``feature005_test_account=true``.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


GetUser = Callable[[str], Awaitable[dict[str, Any] | None]]
GetProfile = Callable[[str], Awaitable[dict[str, Any] | None]]
Deactivate = Callable[..., Awaitable[None]]


def _identifiers(path: Path) -> list[str]:
    from scripts.run_post_attestation_release_gates import _load_private_environment

    values = _load_private_environment(path)
    identifiers = [
        str(values.get(f"FEATURE005_{role}_IDENTIFIER") or "").strip()
        for role in ("CITIZEN", "OFFICER", "ADMIN")
    ]
    if any(not value for value in identifiers):
        raise RuntimeError("disposable credential manifest is missing role identifiers")
    if len(set(identifiers)) != 3:
        raise RuntimeError("disposable credential manifest contains duplicate identifiers")
    return identifiers


async def cleanup_accounts(
    credential_path: Path,
    *,
    get_user: GetUser | None = None,
    get_profile: GetProfile | None = None,
    deactivate: Deactivate | None = None,
) -> dict[str, int]:
    """Deactivate only the three exact, persisted disposable test accounts."""

    if get_user is None or get_profile is None or deactivate is None:
        from api.user_service import (
            deactivate_user_account,
            get_user_by_identifier,
            get_user_profile,
        )

        get_user = get_user or get_user_by_identifier
        get_profile = get_profile or get_user_profile
        deactivate = deactivate or deactivate_user_account

    deactivated = 0
    already_inactive = 0
    for identifier in _identifiers(credential_path):
        if not identifier.startswith("feature005_"):
            raise RuntimeError(
                f"refusing to deactivate non-disposable identifier: {identifier}"
            )
        user = await get_user(identifier)
        if not user:
            raise RuntimeError(f"disposable account is missing: {identifier}")
        user_id = str(user.get("id") or "")
        profile = await get_profile(user_id)
        preferences = dict((profile or {}).get("preferences") or {})
        if preferences.get("feature005_test_account") is not True:
            raise RuntimeError(
                f"refusing to deactivate unmarked account: {identifier}"
            )
        if not bool(user.get("is_active", True)):
            already_inactive += 1
            continue
        await deactivate(
            user_id,
            actor_user_id=None,
            actor_role="system",
            reason="Feature 005 post-attestation release gate cleanup",
        )
        deactivated += 1
    return {
        "deactivated": deactivated,
        "already_inactive": already_inactive,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(cleanup_accounts(args.input))
        print(
            "Feature 005 disposable account cleanup complete: "
            f"{result['deactivated']} deactivated, "
            f"{result['already_inactive']} already inactive."
        )
        return 0
    finally:
        # The file contains disposable passwords and tokens. It must not remain
        # on disk even when account validation or cleanup fails.
        args.input.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
