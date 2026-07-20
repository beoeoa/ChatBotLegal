import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Optional

from fastapi import HTTPException, Request
from loguru import logger

from api.observability import telemetry
from open_notebook.database.repository import ensure_record_id, repo_create, repo_query, repo_update
from open_notebook.utils.encryption import get_secret_from_env

UserRole = Literal["citizen", "officer", "admin"]
ALLOWED_ROLES: tuple[UserRole, ...] = ("citizen", "officer", "admin")
DEFAULT_WARD_SCOPE = "Phường Lê Chân, Hải Phòng"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DATA_DIR = PROJECT_ROOT / "data" / "private"
PASSWORD_RESET_STORE = PRIVATE_DATA_DIR / "password_reset_tokens.json"

# Override any legacy mojibake default that may exist above after old imports.
DEFAULT_WARD_SCOPE = "Phường Lê Chân, Hải Phòng"


def normalize_user_role(value: str | None) -> UserRole:
    if value in ALLOWED_ROLES:
        return value
    return "citizen"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_identifier(value: str) -> str:
    return value.strip().lower()


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_ttl_hours() -> int:
    raw = os.getenv("OPEN_NOTEBOOK_SESSION_TTL_HOURS", "720").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 720


def _session_touch_interval_seconds() -> int:
    raw = os.getenv("OPEN_NOTEBOOK_SESSION_TOUCH_SECONDS", "600").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 600


def _session_touch_due(last_seen_at: Any, *, now: datetime | None = None) -> bool:
    current = now or _utcnow()
    seen: datetime | None = None
    if isinstance(last_seen_at, datetime):
        seen = last_seen_at
    elif isinstance(last_seen_at, str) and last_seen_at.strip():
        try:
            seen = datetime.fromisoformat(last_seen_at.strip().replace("Z", "+00:00"))
        except ValueError:
            seen = None
    if seen is None:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return current - seen >= timedelta(seconds=_session_touch_interval_seconds())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _record_id_str(value: Any) -> Optional[str]:
    if not value:
        return None
    return str(value)


def _record_ref(value: str | None) -> Any:
    if not value:
        return None
    return ensure_record_id(value)


def _user_account_ref(value: str | None) -> Any:
    """Return a user_account record reference, tolerating legacy bare/user ids."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("user_account:"):
        return ensure_record_id(text)
    if ":" in text:
        text = text.split(":", 1)[1]
    return ensure_record_id(f"user_account:{text}")


def _sanitize_user_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _record_id_str(row.get("id")),
        "username": row.get("username"),
        "email": row.get("email"),
        "role": normalize_user_role(row.get("role")),
        "is_active": bool(row.get("is_active", True)),
        "legacy_password_login": bool(row.get("legacy_password_login", False)),
        "created": str(row.get("created", "")),
        "updated": str(row.get("updated", "")),
        "last_login_at": str(row.get("last_login_at", "")) if row.get("last_login_at") else None,
    }


DEPARTMENT_TO_DOMAINS = {
    "Tư pháp - Hộ tịch": ["ho_tich", "chung_thuc"],
    "Địa chính - Xây dựng": ["dat_dai", "xay_dung"],
    "Văn hóa - Xã hội": ["an_sinh_y_te_giao_duc", "khieu_nai", "xu_phat"]
}

DEPARTMENT_TO_DOMAINS = {
    "Tư pháp - Hộ tịch": ["ho_tich_chung_thuc"],
    "Địa chính - Xây dựng": ["dat_dai_xay_dung"],
    "Văn hóa - Xã hội": ["an_sinh_y_te_giao_duc"],
    "Hành chính công": ["hanh_chinh_cong"],
    "Trật tự đô thị": ["trat_tu_do_thi"],
    "Khiếu nại - Tố cáo - Xử phạt": ["khieu_nai_to_cao_xu_phat"],
}


def get_domains_for_department(dept: str | None) -> list[str]:
    if not dept:
        return []
    canonical_departments = {
        "Tư pháp - Hộ tịch": ["ho_tich_chung_thuc"],
        "Địa chính - Xây dựng": ["dat_dai_xay_dung"],
        "Văn hóa - Xã hội": ["an_sinh_y_te_giao_duc"],
        "Hành chính công": ["hanh_chinh_cong"],
        "Trật tự đô thị": ["trat_tu_do_thi"],
        "Khiếu nại - Tố cáo - Xử phạt": ["khieu_nai_to_cao_xu_phat"],
    }
    if dept in canonical_departments:
        return canonical_departments[dept]
    normalized_departments = {
        "Tư pháp - Hộ tịch": ["ho_tich_chung_thuc"],
        "Địa chính - Xây dựng": ["dat_dai_xay_dung"],
        "Văn hóa - Xã hội": ["an_sinh_y_te_giao_duc"],
        "Hành chính công": ["hanh_chinh_cong"],
        "Trật tự đô thị": ["trat_tu_do_thi"],
        "Khiếu nại - Tố cáo - Xử phạt": ["khieu_nai_to_cao_xu_phat"],
    }
    return normalized_departments.get(dept) or DEPARTMENT_TO_DOMAINS.get(dept, [])


def _sanitize_profile_record(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    if not (row.get("ward") or row.get("ward_scope")):
        row = {**row, "ward": DEFAULT_WARD_SCOPE}
    ward = row.get("ward") or row.get("ward_scope") or "Phường Lê Chân, Hải Phòng"
    return {
        "full_name": row.get("full_name"),
        "phone": row.get("phone"),
        "ward": ward,
        "ward_scope": row.get("ward_scope") or ward,
        "department": row.get("department"),
        "allowed_domains": row.get("allowed_domains") or [],
        "must_change_password": bool(row.get("must_change_password", False)),
        "job_title": row.get("job_title"),
        "notes": row.get("notes"),
        "preferences": row.get("preferences") or {},
    }


def merge_user_profile(
    user_row: dict[str, Any], profile_row: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **_sanitize_user_record(user_row),
        "profile": _sanitize_profile_record(profile_row),
    }


async def has_real_users() -> bool:
    try:
        result = await repo_query("SELECT count() AS count FROM user_account GROUP ALL;")
        return bool(result and result[0].get("count", 0) > 0)
    except Exception:
        return False


async def get_user_by_identifier(identifier: str) -> Optional[dict[str, Any]]:
    normalized = _normalize_identifier(identifier)
    result = await repo_query(
        """
        SELECT * FROM user_account
        WHERE string::lowercase(username) = $identifier
           OR string::lowercase(email) = $identifier
        LIMIT 1;
        """,
        {"identifier": normalized},
    )
    return result[0] if result else None


async def get_user_profile(user_id: str) -> Optional[dict[str, Any]]:
    result = await repo_query(
        "SELECT * FROM user_profile WHERE user = type::record($user_id) LIMIT 1;",
        {"user_id": user_id},
    )
    return result[0] if result else None


async def get_user_with_profile(user_id: str) -> Optional[dict[str, Any]]:
    result = await repo_query("SELECT * FROM type::record($user_id);", {"user_id": user_id})
    if not result:
        return None
    return merge_user_profile(result[0], await get_user_profile(user_id))


async def ensure_bootstrap_admin_user() -> Optional[dict[str, Any]]:
    await has_real_users()
    existing = await get_user_by_identifier("admin")
    password = get_secret_from_env("OPEN_NOTEBOOK_ADMIN_PASSWORD") or get_secret_from_env(
        "OPEN_NOTEBOOK_PASSWORD"
    )
    if existing or not password:
        return existing

    try:
        created = await repo_create(
            "user_account",
            {
                "username": "admin",
                "email": "admin@local",
                "role": "admin",
                "password_hash": hash_password(password),
                "is_active": True,
                "legacy_password_login": True,
                "last_login_at": None,
            },
        )
        user = created[0] if isinstance(created, list) else created
        await repo_create(
            "user_profile",
            {
                "user": _record_ref(_record_id_str(user.get("id"))),
                "full_name": "System Administrator",
                "department": "Administration",
                "ward": "Phường Lê Chân, Hải Phòng",
                "allowed_domains": ["ho_tich", "chung_thuc", "dat_dai", "xay_dung", "an_sinh_y_te_giao_duc", "khieu_nai", "xu_phat"],
                "job_title": "Admin",
                "preferences": {},
            },
        )
        return user
    except Exception as e:
        error_str = str(e)
        if "already contains" in error_str or "Database index" in error_str or "duplicate" in error_str.lower():
            logger.info("Admin user was created concurrently, retrieving existing admin user.")
            existing = await get_user_by_identifier("admin")
            if existing:
                return existing
        raise



async def authenticate_user_account(
    identifier: str,
    password: str,
    requested_role: str | None = None,
) -> dict[str, Any]:
    await ensure_bootstrap_admin_user()
    user = await get_user_by_identifier(identifier)
    if not user or not verify_password(password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập/email hoặc mật khẩu")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    actual_role = normalize_user_role(user.get("role"))
    normalized_requested_role = normalize_user_role(requested_role)
    if requested_role and normalized_requested_role != actual_role:
        raise HTTPException(
            status_code=403,
            detail="Role đăng nhập không khớp với tài khoản",
        )

    now = _utcnow()
    await repo_update(
        "user_account",
        str(user["id"]),
        {
            "last_login_at": now,
            "updated": now,
        },
    )

    refreshed = await get_user_with_profile(str(user["id"]))
    if not refreshed:
        raise HTTPException(status_code=500, detail="Không đọc lại được tài khoản sau đăng nhập")

    token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(hours=_session_ttl_hours())
    await repo_create(
        "user_session",
        {
            "user": _record_ref(refreshed["id"]),
            "role": refreshed["role"],
            "session_token_hash": _hash_session_token(token),
            "expires_at": expires_at,
            "revoked_at": None,
            "last_seen_at": now,
        },
    )
    return {
        "token": token,
        "user": refreshed,
        "role": refreshed["role"],
        "expires_at": expires_at.isoformat(),
    }


async def _revoke_active_sessions(user_id: str) -> None:
    sessions = await repo_query(
        "SELECT * FROM user_session WHERE user = type::record($user_id) AND revoked_at = NONE;",
        {"user_id": user_id},
    )
    now = _utcnow()
    for session in sessions:
        await repo_update("user_session", str(session["id"]), {"revoked_at": now})


async def change_own_password(
    user_id: str,
    *,
    current_password: str,
    new_password: str,
    request: Request | None = None,
) -> None:
    """Change a user password and clear the first-login requirement."""
    if len(new_password) < 12:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 12 ký tự")
    rows = await repo_query("SELECT * FROM type::record($user_id);", {"user_id": user_id})
    raw_user = rows[0] if rows else None
    user = await get_user_with_profile(user_id)
    if not raw_user or not user or not verify_password(current_password, raw_user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Mật khẩu hiện tại không đúng")
    if current_password == new_password:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải khác mật khẩu hiện tại")
    await repo_update("user_account", user_id, {"password_hash": hash_password(new_password), "updated": _utcnow()})
    await upsert_user_profile(user_id, {**user.get("profile", {}), "must_change_password": False})
    await _revoke_active_sessions(user_id)
    await write_audit_log(
        action="user.password.change",
        entity_type="user_account",
        entity_id=user_id,
        actor_user_id=user_id,
        actor_role=user.get("role"),
        target_user_id=user_id,
        details={"must_change_password": False},
        request=request,
    )


async def admin_reset_user_password(
    target_user_id: str,
    *,
    new_password: str,
    actor_user_id: str | None,
    reason: str | None = None,
    request: Request | None = None,
) -> None:
    """Admin reset: make the temporary password valid once and force a change."""
    if len(new_password) < 12:
        raise HTTPException(status_code=400, detail="Mật khẩu tạm phải có ít nhất 12 ký tự")
    target = await get_user_with_profile(target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    await repo_update("user_account", target_user_id, {"password_hash": hash_password(new_password), "updated": _utcnow()})
    await upsert_user_profile(target_user_id, {**target.get("profile", {}), "must_change_password": True})
    await _revoke_active_sessions(target_user_id)
    await write_audit_log(
        action="user.password.reset_by_admin",
        entity_type="user_account",
        entity_id=target_user_id,
        actor_user_id=actor_user_id,
        actor_role="admin",
        target_user_id=target_user_id,
        details={"must_change_password": True, "reason": reason},
        request=request,
    )


async def get_user_from_session_token(token: str) -> Optional[dict[str, Any]]:
    token_hash = _hash_session_token(token)
    result = await repo_query(
        """
        SELECT * FROM user_session
        WHERE session_token_hash = $token_hash
          AND revoked_at = NONE
          AND expires_at > time::now()
        LIMIT 1;
        """,
        {"token_hash": token_hash},
    )
    if not result:
        return None

    session = result[0]
    user_id = _record_id_str(session.get("user"))
    if not user_id:
        return None
    user = await get_user_with_profile(user_id)
    if not user or not user.get("is_active", True):
        return None

    if _session_touch_due(session.get("last_seen_at")):
        try:
            await repo_update(
                "user_session",
                str(session["id"]),
                {"last_seen_at": _utcnow()},
            )
        except Exception as exc:
            # Session activity is telemetry, not part of authentication. A
            # transient write conflict must never turn a valid API call into 500.
            logger.warning(
                "Could not update session last_seen_at for {}: {}",
                session.get("id"),
                exc,
            )

    return {
        "session_id": _record_id_str(session.get("id")),
        "role": normalize_user_role(session.get("role") or user.get("role")),
        "user": user,
    }


def _profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    dept = payload.get("department")
    allowed_domains = payload.get("allowed_domains")

    # Auto map department to allowed_domains if not explicitly provided
    if not allowed_domains and dept:
        allowed_domains = get_domains_for_department(dept)

    ward = payload.get("ward") or payload.get("ward_scope") or "Phường Lê Chân, Hải Phòng"
    return {
        "full_name": payload.get("full_name"),
        "phone": payload.get("phone"),
        "ward": ward,
        "ward_scope": payload.get("ward_scope") or ward,
        "department": dept,
        "allowed_domains": allowed_domains or [],
        "must_change_password": bool(payload.get("must_change_password", False)),
        "job_title": payload.get("job_title"),
        "notes": payload.get("notes"),
        "preferences": payload.get("preferences") or {},
    }


async def upsert_user_profile(user_id: str, payload: dict[str, Any]) -> None:
    existing = await get_user_profile(user_id)
    profile_data = {
        "user": _record_ref(user_id),
        **_profile_payload(payload),
    }
    if existing:
        await repo_update("user_profile", str(existing["id"]), profile_data)
    else:
        await repo_create("user_profile", profile_data)


def _request_meta(request: Request | None) -> dict[str, Any]:
    if not request:
        return {"ip_address": None, "user_agent": None}
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def write_audit_log(
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    target_user_id: str | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    meta = _request_meta(request)
    await repo_create(
        "user_audit_log",
        {
            "actor_user": _user_account_ref(actor_user_id),
            "actor_role": actor_role,
            "target_user": _user_account_ref(target_user_id),
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": details or {},
            "ip_address": meta["ip_address"],
            "user_agent": meta["user_agent"],
        },
    )


async def list_users_with_profiles() -> list[dict[str, Any]]:
    await ensure_bootstrap_admin_user()
    users = await repo_query("SELECT * FROM user_account ORDER BY created DESC;")
    profiles = await repo_query("SELECT * FROM user_profile;")
    profile_map = {_record_id_str(profile.get("user")): profile for profile in profiles}
    return [
        merge_user_profile(user, profile_map.get(_record_id_str(user.get("id"))))
        for user in users
    ]


async def create_user_account(
    payload: dict[str, Any],
    *,
    actor_user_id: str | None,
    actor_role: str | None,
    request: Request | None = None,
) -> dict[str, Any]:
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    role = normalize_user_role(payload.get("role"))

    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username phải có ít nhất 3 ký tự")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Email không hợp lệ")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự")
    if await get_user_by_identifier(username):
        raise HTTPException(status_code=409, detail="Username đã tồn tại")
    existing_email = await get_user_by_identifier(email)
    if existing_email:
        raise HTTPException(status_code=409, detail="Email đã tồn tại")

    created = await repo_create(
        "user_account",
        {
            "username": username,
            "email": email,
            "role": role,
            "password_hash": hash_password(password),
            "is_active": payload.get("is_active", True),
            "legacy_password_login": False,
            "last_login_at": None,
        },
    )
    user = created[0] if isinstance(created, list) else created
    await upsert_user_profile(str(user["id"]), payload)
    await write_audit_log(
        action="user.create",
        entity_type="user_account",
        entity_id=_record_id_str(user.get("id")),
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target_user_id=_record_id_str(user.get("id")),
        details={"username": username, "email": email, "role": role},
        request=request,
    )
    result = await get_user_with_profile(str(user["id"]))
    if not result:
        raise HTTPException(status_code=500, detail="Không đọc lại được user vừa tạo")
    return result


async def register_citizen_account(
    payload: dict[str, Any],
    *,
    request: Request | None = None,
) -> dict[str, Any]:
    """Public self-registration. A public user can only become a citizen."""
    clean_payload = {
        **payload,
        "role": "citizen",
        "is_active": True,
        "department": None,
        "allowed_domains": [],
        "ward": payload.get("ward") or DEFAULT_WARD_SCOPE,
        "ward_scope": payload.get("ward_scope") or payload.get("ward") or DEFAULT_WARD_SCOPE,
        "must_change_password": False,
    }
    return await create_user_account(
        clean_payload,
        actor_user_id=None,
        actor_role="self_register",
        request=request,
    )


def _load_password_reset_store() -> list[dict[str, Any]]:
    if not PASSWORD_RESET_STORE.exists():
        return []
    try:
        with PASSWORD_RESET_STORE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_password_reset_store(records: list[dict[str, Any]]) -> None:
    PRIVATE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with PASSWORD_RESET_STORE.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _password_reset_return_token_enabled() -> bool:
    # Local pilot default: return the one-use token because no email service is
    # configured. Production can set this false after SMTP delivery is added.
    raw = os.getenv("OPEN_NOTEBOOK_RETURN_RESET_TOKEN", "true").strip().lower()
    return raw not in {"0", "false", "no"}


async def create_password_reset_request(
    identifier: str,
    *,
    request: Request | None = None,
) -> dict[str, Any]:
    """Create a one-use reset token without revealing account existence."""
    generic = {
        "success": True,
        "message": "Nếu tài khoản tồn tại, hệ thống đã tạo hướng dẫn đặt lại mật khẩu.",
    }
    user = await get_user_by_identifier(identifier)
    if not user or not user.get("is_active", True):
        return generic

    token = secrets.token_urlsafe(32)
    now = _utcnow()
    expires_at = now + timedelta(minutes=15)
    records = [
        item for item in _load_password_reset_store()
        if item.get("used_at") is None and item.get("expires_at", "") > now.isoformat()
    ]
    user_id = _record_id_str(user.get("id"))
    records.append(
        {
            "user_id": user_id,
            "token_hash": _hash_session_token(token),
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "used_at": None,
        }
    )
    _save_password_reset_store(records)
    await write_audit_log(
        action="user.password.reset_requested",
        entity_type="user_account",
        entity_id=user_id,
        actor_user_id=None,
        actor_role="public",
        target_user_id=user_id,
        details={"delivery": "local_token" if _password_reset_return_token_enabled() else "out_of_band"},
        request=request,
    )
    if _password_reset_return_token_enabled():
        return {**generic, "reset_token": token, "expires_in_minutes": 15}
    return generic


async def reset_password_with_token(
    token: str,
    new_password: str,
    *,
    request: Request | None = None,
) -> None:
    if len(new_password) < 12:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 12 ký tự")

    token_hash = _hash_session_token(token)
    now = _utcnow()
    records = _load_password_reset_store()
    matched: dict[str, Any] | None = None
    for item in records:
        if item.get("token_hash") == token_hash:
            matched = item
            break

    if not matched or matched.get("used_at") is not None or matched.get("expires_at", "") <= now.isoformat():
        raise HTTPException(status_code=400, detail="Mã đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")

    user_id = matched.get("user_id")
    user = await get_user_with_profile(user_id)
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=400, detail="Mã đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")

    await repo_update("user_account", user_id, {"password_hash": hash_password(new_password), "updated": now})
    await upsert_user_profile(user_id, {**user.get("profile", {}), "must_change_password": False})
    await _revoke_active_sessions(user_id)
    matched["used_at"] = now.isoformat()
    _save_password_reset_store(records)
    await write_audit_log(
        action="user.password.reset_by_token",
        entity_type="user_account",
        entity_id=user_id,
        actor_user_id=user_id,
        actor_role=user.get("role"),
        target_user_id=user_id,
        details={"self_service": True},
        request=request,
    )


async def update_user_account(
    user_id: str,
    payload: dict[str, Any],
    *,
    actor_user_id: str | None,
    actor_role: str | None,
    request: Request | None = None,
) -> dict[str, Any]:
    existing = await get_user_with_profile(user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")

    update_data: dict[str, Any] = {}
    if payload.get("username") is not None:
        username = payload["username"].strip()
        if len(username) < 3:
            raise HTTPException(status_code=400, detail="Username phải có ít nhất 3 ký tự")
        other = await get_user_by_identifier(username)
        if other and _record_id_str(other.get("id")) != user_id:
            raise HTTPException(status_code=409, detail="Username đã tồn tại")
        update_data["username"] = username
    if payload.get("email") is not None:
        email = payload["email"].strip().lower()
        if "@" not in email:
            raise HTTPException(status_code=400, detail="Email không hợp lệ")
        other = await get_user_by_identifier(email)
        if other and _record_id_str(other.get("id")) != user_id:
            raise HTTPException(status_code=409, detail="Email đã tồn tại")
        update_data["email"] = email
    if payload.get("role") is not None:
        update_data["role"] = normalize_user_role(payload["role"])
    if payload.get("is_active") is not None:
        update_data["is_active"] = bool(payload["is_active"])
    if payload.get("password"):
        if len(payload["password"]) < 6:
            raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự")
        update_data["password_hash"] = hash_password(payload["password"])

    if update_data:
        await repo_update("user_account", user_id, update_data)

    await upsert_user_profile(user_id, {**existing.get("profile", {}), **payload})
    await write_audit_log(
        action="user.update",
        entity_type="user_account",
        entity_id=user_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target_user_id=user_id,
        details={
            "updated_fields": sorted(key for key in payload.keys() if key != "_business_reason"),
            "reason": payload.get("_business_reason"),
        },
        request=request,
    )
    result = await get_user_with_profile(user_id)
    if not result:
        raise HTTPException(status_code=500, detail="Không đọc lại được user sau cập nhật")
    return result


async def deactivate_user_account(
    user_id: str,
    *,
    actor_user_id: str | None,
    actor_role: str | None,
    reason: str | None = None,
    request: Request | None = None,
) -> None:
    existing = await get_user_with_profile(user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    await repo_update("user_account", user_id, {"is_active": False})
    sessions = await repo_query(
        "SELECT * FROM user_session WHERE user = type::record($user_id) AND revoked_at = NONE;",
        {"user_id": user_id},
    )
    for session in sessions:
        await repo_update("user_session", str(session["id"]), {"revoked_at": _utcnow()})
    await write_audit_log(
        action="user.deactivate",
        entity_type="user_account",
        entity_id=user_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target_user_id=user_id,
        details={"is_active": False, "reason": reason},
        request=request,
    )



_ASK_SOURCE_SNAPSHOT_FIELDS = (
    "chunk_id",
    "document_id",
    "article_id",
    "law_number",
    "document_title",
    "document_type",
    "article_number",
    "article_title",
    "clause_number",
    "point_number",
    "effective_status",
    "effective_date",
    "expired_date",
    "issuing_agency",
    "scope",
    "source_url",
    "score",
)
_ASK_SOURCE_SNAPSHOT_MAX = 25


def _source_snapshot_value(source: dict[str, Any], field: str) -> Any:
    value = source.get(field)
    if value not in (None, "", [], {}):
        return value
    if field == "effective_status":
        for alias in ("document_status", "status"):
            value = source.get(alias)
            if value not in (None, "", [], {}):
                return value
    if field == "score":
        value = source.get("final_score")
        if value not in (None, "", [], {}):
            return value
    return None


def _normalize_ask_source_snapshot(source: Any) -> dict[str, Any] | None:
    """Keep only source/legal metadata needed to reproduce an audit decision."""
    if not isinstance(source, dict):
        return None
    snapshot: dict[str, Any] = {}
    for field in _ASK_SOURCE_SNAPSHOT_FIELDS:
        value = _source_snapshot_value(source, field)
        if value is not None:
            snapshot[field] = value
    if "chunk_id" not in snapshot:
        source_id = str(source.get("id") or "").strip()
        if source_id:
            snapshot["chunk_id"] = source_id.split(":", 1)[-1]
    return snapshot or None


def _compact_ask_sources(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for source in (sources or [])[:_ASK_SOURCE_SNAPSHOT_MAX]:
        snapshot = _normalize_ask_source_snapshot(source)
        if snapshot:
            compact.append(snapshot)
    return compact


def _deserialize_ask_source_item(item: Any) -> list[Any]:
    if isinstance(item, dict):
        return [item]
    if isinstance(item, (list, tuple)):
        result: list[Any] = []
        for nested in item:
            result.extend(_deserialize_ask_source_item(nested))
        return result
    if isinstance(item, str):
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            return [{"raw": item}]
        if isinstance(parsed, (dict, list, tuple)):
            return _deserialize_ask_source_item(parsed)
        return [{"raw": item}]
    return [item]


def _deserialize_ask_sources(raw_sources: Any) -> list[Any]:
    """Normalize Ask audit source snapshots for API consumers.

    Migration 24 stores compact JSON strings under SCHEMAFULL, but older rows may
    already contain dicts. Keep both shapes readable without raising.
    """
    if not raw_sources:
        return []
    return _deserialize_ask_source_item(raw_sources)


async def log_ask_history(
    *,
    owner_user_id: str | None,
    owner_role: str | None,
    question: str,
    answer: str | None,
    domain: str | None,
    department: str | None = None,
    strategy_model: str | None,
    answer_model: str | None,
    final_answer_model: str | None,
    offline_mode: bool,
    offline_model: str | None,
    rag_trace: dict[str, Any] | None,
    sources: list[dict[str, Any]] | None = None,
    grounding_status: str = "unknown",
    duration_ms: int | None,
    file_upload_metadata: dict[str, Any] | None = None,
) -> None:
    audit_started = perf_counter()
    try:
        await repo_create(
            "user_ask_history",
            {
                "owner_user": _record_ref(owner_user_id),
                "owner_role": owner_role,
                "question": question,
                "answer": answer,
                "domain": domain,
                "department": department,
                "strategy_model": strategy_model,
                "answer_model": answer_model,
                "final_answer_model": final_answer_model,
                "offline_mode": offline_mode,
                "offline_model": offline_model,
                "rag_trace": rag_trace or {},
                # Content and retrieval traces are deliberately excluded. The
                # allowlisted metadata is sufficient to reproduce grounding.
                "sources": _compact_ask_sources(sources),
                "grounding_status": grounding_status,
                "duration_ms": duration_ms,
                "file_upload_metadata": file_upload_metadata or {},
            },
        )
    except Exception as exc:
        telemetry.record_ask_stage(
            "persistence",
            duration_ms=(perf_counter() - audit_started) * 1000,
            outcome="failed",
        )
        telemetry.record_ask_outcome("audit", "failed")
        telemetry.record_issue(
            "ask_audit_write_failed",
            category="ask.persistence",
            error_class=exc.__class__.__name__,
        )
        raise
    telemetry.record_ask_stage(
        "persistence",
        duration_ms=(perf_counter() - audit_started) * 1000,
    )
    telemetry.record_ask_outcome("audit", "success")


async def list_ask_history(
    limit: int = 100,
    role: str | None = None,
    domain: str | None = None,
    department: str | None = None,
    grounding_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    summary_only: bool = False,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
    if role:
        filters.append("owner_role = $role")
        params["role"] = role
    if domain:
        filters.append("domain = $domain")
        params["domain"] = domain
    if date_from:
        filters.append("created >= <datetime>$date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("created <= <datetime>$date_to")
        params["date_to"] = date_to
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    projection = (
        "id, owner_user, owner_role, question, domain, department, "
        "grounding_status, duration_ms, created"
        if summary_only
        else "*"
    )
    rows = await repo_query(
        f"SELECT {projection} FROM user_ask_history {where_clause} "
        "ORDER BY created DESC LIMIT $limit;",
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = {
                "id": _record_id_str(row.get("id")),
                "owner_user": _record_id_str(row.get("owner_user")),
                "owner_role": row.get("owner_role"),
                "question": row.get("question"),
                "domain": row.get("domain"),
                "department": row.get("department"),
                "grounding_status": row.get("grounding_status", "unknown"),
                "duration_ms": row.get("duration_ms"),
                "created": str(row.get("created", "")),
        }
        if not summary_only:
            item.update(
                {
                    "answer": row.get("answer"),
                    "strategy_model": row.get("strategy_model"),
                    "answer_model": row.get("answer_model"),
                    "final_answer_model": row.get("final_answer_model"),
                    "offline_mode": bool(row.get("offline_mode", False)),
                    "offline_model": row.get("offline_model"),
                    "rag_trace": row.get("rag_trace"),
                    "sources": _deserialize_ask_sources(row.get("sources")),
                    "file_upload_metadata": row.get("file_upload_metadata"),
                }
            )
        result.append(item)
    return result


async def list_audit_logs(
    limit: int = 100,
    actor_role: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    reason: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
    if actor_role:
        filters.append("actor_role = $actor_role")
        params["actor_role"] = actor_role
    if action:
        filters.append("action = $action")
        params["action"] = action
    if resource_type:
        filters.append("entity_type = $resource_type")
        params["resource_type"] = resource_type
    if resource_id:
        filters.append("entity_id = $resource_id")
        params["resource_id"] = resource_id
    if reason:
        filters.append("string::lowercase(string(details.reason)) CONTAINS string::lowercase($reason)")
        params["reason"] = reason
    if date_from:
        filters.append("created >= <datetime>$date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("created <= <datetime>$date_to")
        params["date_to"] = date_to
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = await repo_query(
        f"SELECT * FROM user_audit_log {where_clause} ORDER BY created DESC LIMIT $limit;",
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": _record_id_str(row.get("id")),
                "actor_user": _record_id_str(row.get("actor_user")),
                "actor_role": row.get("actor_role"),
                "target_user": _record_id_str(row.get("target_user")),
                "action": row.get("action"),
                "entity_type": row.get("entity_type"),
                "entity_id": row.get("entity_id"),
                "details": row.get("details") or {},
                "ip_address": row.get("ip_address"),
                "user_agent": row.get("user_agent"),
                "created": str(row.get("created", "")),
            }
        )
    return result
