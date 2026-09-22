"""Firebase Admin token verification for the browser-to-API auth bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException
from loguru import logger

_FIREBASE_APP_LOCK = Lock()
_SERVICE_ACCOUNT_ENV_VARS = (
    "FIREBASE_SERVICE_ACCOUNT_JSON",
    "FIREBASE_SERVICE_ACCOUNT_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def _credentials_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": code, "message": message})


def _read_service_account_info() -> dict[str, Any] | None:
    """Read a service account from a file or inline JSON secret."""
    for env_name in _SERVICE_ACCOUNT_ENV_VARS:
        raw_value = str(os.getenv(env_name) or "").strip()
        if not raw_value:
            continue
        if raw_value.startswith("{"):
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise _credentials_error(
                    "FIREBASE_CREDENTIALS_INVALID",
                    "Nội dung service account Firebase không phải JSON hợp lệ.",
                ) from exc
        else:
            path = Path(raw_value).expanduser()
            if not path.is_file():
                raise _credentials_error(
                    "FIREBASE_CREDENTIALS_NOT_FOUND",
                    "Không tìm thấy file service account Firebase ở cấu hình backend.",
                )
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise _credentials_error(
                    "FIREBASE_CREDENTIALS_INVALID",
                    "Không đọc được file service account Firebase.",
                ) from exc
        if not isinstance(value, dict) or not value.get("project_id") or not value.get("private_key"):
            raise _credentials_error(
                "FIREBASE_CREDENTIALS_INVALID",
                "Service account Firebase thiếu project_id hoặc private_key.",
            )
        return value
    return None


def _build_firebase_credential(credentials, project_id: str):
    service_account_info = _read_service_account_info()
    if service_account_info:
        if str(service_account_info.get("project_id")) != project_id:
            raise _credentials_error(
                "FIREBASE_PROJECT_MISMATCH",
                "Service account Firebase không thuộc đúng project đang cấu hình.",
            )
        return credentials.Certificate(service_account_info)
    return credentials.ApplicationDefault()


def _get_firebase_app(firebase_admin, credentials, project_id: str):
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass
    with _FIREBASE_APP_LOCK:
        try:
            return firebase_admin.get_app()
        except ValueError:
            try:
                credential = _build_firebase_credential(credentials, project_id)
                return firebase_admin.initialize_app(credential, {"projectId": project_id})
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("Firebase Admin credential initialization failed: {}", type(exc).__name__)
                raise _credentials_error(
                    "FIREBASE_CREDENTIALS_NOT_CONFIGURED",
                    "Backend chưa có service account Firebase hoặc Google ADC hợp lệ.",
                ) from exc


def _firebase_admin_modules():
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "FIREBASE_ADMIN_NOT_INSTALLED",
                "message": "Firebase Admin SDK chưa được cài đặt trên backend.",
            },
        ) from exc
    return firebase_admin, auth, credentials


def verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    """Verify a Firebase ID token and return trusted claims only.

    Credentials are loaded from a configured service-account file/JSON secret,
    or from the default Google application credentials chain. No
    service-account material belongs in source control or in the frontend
    bundle.
    """

    if not id_token or len(id_token) < 100:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "FIREBASE_TOKEN_INVALID",
                "message": "Phiên Firebase không hợp lệ.",
            },
        )

    firebase_admin, auth, credentials = _firebase_admin_modules()
    project_id = str(os.getenv("FIREBASE_PROJECT_ID") or "chatbotle-2b109").strip()
    try:
        app = _get_firebase_app(firebase_admin, credentials, project_id)
        return auth.verify_id_token(id_token, app=app, check_revoked=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Firebase ID token verification failed: {}", type(exc).__name__)
        raise HTTPException(
            status_code=401,
            detail={
                "code": "FIREBASE_TOKEN_INVALID",
                "message": "Phiên Firebase không hợp lệ hoặc đã bị thu hồi.",
            },
        ) from exc
