"""Seed five least-privilege officer accounts for Live Support.

Usage:
    python scripts/seed_officer_accounts.py

The script creates random initial passwords with ``secrets`` and writes them
once to ``data/private/bootstrap_officer_credentials.json``. The data directory
is gitignored. Restrict that file to the deployment owner/admin, deliver it by a
secure channel, then delete it after first-password changes are confirmed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

WARD_SCOPE = "Phường Lê Chân, Hải Phòng"
CREDENTIAL_FILE = PROJECT_ROOT / "data" / "private" / "bootstrap_officer_credentials.json"
LEGACY_USERNAME_MAP = {
    "officer_hanhchinh": "officer_cutru",
    "officer_trattu": "officer_khieunai",
}

OFFICERS: tuple[dict[str, str], ...] = (
    {
        "username": "officer_hotich",
        "email": "officer_hotich@lechan.local",
        "full_name": "Cán bộ Hộ tịch - Chứng thực",
        "department": "Hộ tịch - Chứng thực",
        "job_title": "Cán bộ phụ trách Hộ tịch - Chứng thực",
        "domain": "ho_tich_chung_thuc",
    },
    {
        "username": "officer_daidai",
        "email": "officer_daidai@lechan.local",
        "full_name": "Cán bộ Đất đai - Xây dựng",
        "department": "Đất đai - Xây dựng",
        "job_title": "Cán bộ phụ trách Đất đai - Xây dựng",
        "domain": "dat_dai_xay_dung",
    },
    {
        "username": "officer_ansinh",
        "email": "officer_ansinh@lechan.local",
        "full_name": "Cán bộ An sinh - Y tế - Giáo dục",
        "department": "An sinh - Y tế - Giáo dục",
        "job_title": "Cán bộ phụ trách An sinh - Y tế - Giáo dục",
        "domain": "an_sinh_y_te_giao_duc",
    },
    {
        "username": "officer_cutru",
        "email": "officer_cutru@lechan.local",
        "full_name": "Cán bộ Cư trú - An ninh",
        "department": "Cư trú - An ninh",
        "job_title": "Cán bộ phụ trách Cư trú - An ninh",
        "domain": "cu_tru_an_ninh",
    },
    {
        "username": "officer_khieunai",
        "email": "officer_khieunai@lechan.local",
        "full_name": "Cán bộ Khiếu nại - Tố cáo - Xử phạt",
        "department": "Khiếu nại - Tố cáo - Xử phạt",
        "job_title": "Cán bộ phụ trách Khiếu nại - Tố cáo - Xử phạt",
        "domain": "khieu_nai_to_cao_xu_phat",
    },
)


def generate_initial_password() -> str:
    """Return a high-entropy password without embedding a static secret."""
    return secrets.token_urlsafe(20)


def _private_file_permissions(path: Path) -> None:
    """Restrict bootstrap credentials to the local owner and Administrators."""
    try:
        if os.name == "nt":
            username = os.environ.get("USERNAME")
            if not username:
                raise RuntimeError("Không xác định được Windows owner để bảo vệ file credential")
            result = subprocess.run(
                [
                    "icacls", str(path), "/inheritance:r",
                    "/grant:r", f"{username}:(M)",
                    "/grant:r", "*S-1-5-32-544:(M)",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        else:
            path.chmod(0o600)
    except Exception as exc:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise RuntimeError(
                "Không thể thiết lập quyền owner/admin cho file credential; file đã được xóa."
            ) from exc


async def seed_officers(*, reconcile_legacy: bool = False, rotate_credentials: bool = False) -> tuple[list[dict[str, str]], list[str]]:
    from api.user_service import (
        admin_reset_user_password,
        create_user_account,
        get_user_by_identifier,
        upsert_user_profile,
    )

    if reconcile_legacy:
        for legacy_username, current_username in LEGACY_USERNAME_MAP.items():
            legacy = await get_user_by_identifier(legacy_username)
            current = await get_user_by_identifier(current_username)
            if legacy and current:
                raise RuntimeError(
                    f"C? ??ng th?i t?i kho?n legacy {legacy_username} v? t?i kho?n pilot {current_username}; "
                    "admin ph?i x? l? th? c?ng ?? kh?ng t? g?p t?i kho?n."
                )
            if legacy:
                target = next(spec for spec in OFFICERS if spec["username"] == current_username)
                await repo_update(
                    "user_account", str(legacy["id"]),
                    {"username": target["username"], "email": target["email"], "role": "officer"},
                )
                await upsert_user_profile(str(legacy["id"]), {
                    **target, "ward": WARD_SCOPE, "ward_scope": WARD_SCOPE,
                    "allowed_domains": [target["domain"]], "must_change_password": True,
                    "preferences": {"can_receive_live_support": True, "can_submit_document_candidates": True},
                })


    created_credentials: list[dict[str, str]] = []
    skipped: list[str] = []
    existing_by_username = {
        spec["username"]: await get_user_by_identifier(spec["username"])
        for spec in OFFICERS
    }
    missing = [spec["username"] for spec in OFFICERS if not existing_by_username[spec["username"]]]
    if missing and CREDENTIAL_FILE.exists():
        raise RuntimeError(
            "Credential file already exists while officer accounts are missing. "
            "Archive or securely remove the stale file before seeding: "
            + ", ".join(missing)
        )
    for spec in OFFICERS:
        existing = existing_by_username[spec["username"]]
        if existing and not rotate_credentials:
            skipped.append(spec["username"])
            continue
        password = generate_initial_password()
        if existing:
            await admin_reset_user_password(
                str(existing["id"]), new_password=password, actor_user_id=None,
            )
            await upsert_user_profile(str(existing["id"]), {
                **spec, "ward": WARD_SCOPE, "ward_scope": WARD_SCOPE,
                "allowed_domains": [spec["domain"]], "must_change_password": True,
                "preferences": {"can_receive_live_support": True, "can_submit_document_candidates": True},
            })
            created_credentials.append({"username": spec["username"], "password": password})
            continue
        account: dict[str, Any] = {
            **spec,
            "password": password,
            "role": "officer",
            "ward": WARD_SCOPE,
            "ward_scope": WARD_SCOPE,
            "allowed_domains": [spec["domain"]],
            "must_change_password": True,
            "preferences": {
                "can_receive_live_support": True,
                "can_submit_document_candidates": True,
            },
        }
        await create_user_account(account, actor_user_id=None, actor_role="system")
        created_credentials.append({"username": spec["username"], "password": password})
    return created_credentials, skipped


def archive_existing_credentials() -> None:
    if not CREDENTIAL_FILE.exists():
        return
    archive_dir = CREDENTIAL_FILE.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{CREDENTIAL_FILE.stem}.superseded{CREDENTIAL_FILE.suffix}"
    if archive_path.exists():
        raise RuntimeError(f"Credential archive ?? t?n t?i: {archive_path}")
    CREDENTIAL_FILE.replace(archive_path)
    _private_file_permissions(archive_path)


def write_one_time_credentials(credentials: list[dict[str, str]]) -> Path | None:
    if not credentials:
        return None
    if CREDENTIAL_FILE.exists():
        raise RuntimeError(
            f"Refusing to overwrite existing credential file: {CREDENTIAL_FILE}. "
            "Deliver/delete it before running the seed again."
        )
    CREDENTIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIAL_FILE.write_text(
        json.dumps(
            {
                "warning": "Mật khẩu khởi tạo. Chỉ owner/admin được xem; xóa file sau khi các cán bộ đổi mật khẩu.",
                "credentials": credentials,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _private_file_permissions(CREDENTIAL_FILE)
    return CREDENTIAL_FILE


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconcile-legacy", action="store_true", help="??i 2 t?i kho?n legacy sang 2 domain pilot c?n thi?u.")
    parser.add_argument("--rotate-credentials", action="store_true", help="Reset to?n b? 5 m?t kh?u v? t?o credential file m?i; c?n giao file c?/x?a an to?n.")
    args = parser.parse_args()
    from open_notebook.database.repository import db_connection, repo_update

    if args.rotate_credentials:
        archive_existing_credentials()
    async with db_connection():
        created, skipped = await seed_officers(
            reconcile_legacy=args.reconcile_legacy,
            rotate_credentials=args.rotate_credentials,
        )
    path = write_one_time_credentials(created)
    print(f"Created: {len(created)}; existing/skipped: {len(skipped)}")
    if path:
        print("Initial credentials were generated once for owner/admin.")
        print(f"Credential file: {path}")
        print("Require every officer to change password at first login, then securely delete this file.")
    if skipped:
        print("Skipped existing accounts: " + ", ".join(skipped))


if __name__ == "__main__":
    asyncio.run(main())
