import asyncio
import time
import tomllib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from loguru import logger

from open_notebook.domain.content_settings import ContentSettings

from open_notebook.database.repository import repo_query
from open_notebook.utils.version_utils import (
    compare_versions,
    get_version_from_github_async,
)

router = APIRouter()

# In-memory cache for version check results
_version_cache: dict = {
    "latest_version": None,
    "has_update": False,
    "timestamp": 0,
    "check_failed": False,
}

# Cache TTL in seconds (24 hours)
VERSION_CACHE_TTL = 24 * 60 * 60
_version_refresh_task: asyncio.Task | None = None


def get_version() -> str:
    """Read version from pyproject.toml"""
    try:
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
            return pyproject.get("project", {}).get("version", "unknown")
    except Exception as e:
        logger.warning(f"Could not read version from pyproject.toml: {e}")
        return "unknown"


async def get_latest_version_cached(current_version: str) -> tuple[Optional[str], bool]:
    """
    Check for the latest version from GitHub with caching.

    Returns:
        tuple: (latest_version, has_update)
        - latest_version: str or None if check failed
        - has_update: bool indicating if update is available
    """
    global _version_cache

    # Check if cache is still valid (within TTL)
    cache_age = time.time() - _version_cache["timestamp"]
    if _version_cache["timestamp"] > 0 and cache_age < VERSION_CACHE_TTL:
        logger.debug(f"Using cached version check result (age: {cache_age:.0f}s)")
        return _version_cache["latest_version"], _version_cache["has_update"]

    # Cache expired or not yet set
    if _version_cache["timestamp"] > 0:
        logger.info(f"Version cache expired (age: {cache_age:.0f}s), refreshing...")

    # Perform version check with strict error handling
    try:
        logger.info("Checking for latest version from GitHub...")

        # Fetch latest version from GitHub with 10-second timeout
        latest_version = await asyncio.wait_for(
            get_version_from_github_async("https://github.com/lfnovo/open-notebook", "main"),
            timeout=10.0,
        )

        logger.info(
            f"Latest version from GitHub: {latest_version}, Current version: {current_version}"
        )

        # Compare versions
        has_update = compare_versions(current_version, latest_version) < 0

        # Cache the result
        _version_cache["latest_version"] = latest_version
        _version_cache["has_update"] = has_update
        _version_cache["timestamp"] = time.time()
        _version_cache["check_failed"] = False

        logger.info(f"Version check complete. Update available: {has_update}")

        return latest_version, has_update

    except Exception as e:
        logger.warning(f"Version check failed: {e}")

        # Cache the failure to avoid repeated attempts
        _version_cache["latest_version"] = None
        _version_cache["has_update"] = False
        _version_cache["timestamp"] = time.time()
        _version_cache["check_failed"] = True

        return None, False


def schedule_version_refresh(current_version: str) -> None:
    """Single-flight refresh; optional update checks never gate app startup."""
    global _version_refresh_task
    if _version_cache["timestamp"] > 0 and time.time() - _version_cache["timestamp"] < VERSION_CACHE_TTL:
        return
    if _version_refresh_task is None or _version_refresh_task.done():
        _version_refresh_task = asyncio.create_task(get_latest_version_cached(current_version))


async def get_public_branding() -> tuple[str, str]:
    system_name = "Pháp luật Hải Phòng"
    organization_name = ""
    try:
        settings = await asyncio.wait_for(ContentSettings.get_instance(), timeout=2.0)
        system_name = str(getattr(settings, "system_name", "") or system_name).strip() or system_name
        organization_name = str(getattr(settings, "organization_name", "") or "").strip()
    except Exception as exc:
        logger.warning("Could not load public system branding: {}", exc)
    return system_name, organization_name


async def check_database_health() -> dict:
    """
    Check if database is reachable using a lightweight query.

    Returns:
        dict with 'status' ("online" | "offline") and optional 'error'
    """
    try:
        # 2-second timeout for database health check
        result = await asyncio.wait_for(repo_query("RETURN 1"), timeout=2.0)
        if result:
            return {"status": "online"}
        return {"status": "offline", "error": "Empty result"}
    except asyncio.TimeoutError:
        logger.warning("Database health check timed out after 2 seconds")
        return {"status": "offline", "error": "Health check timeout"}
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return {"status": "offline", "error": str(e)}


@router.get("/config")
@router.get("/config/public")
async def get_config(request: Request):
    """
    Get frontend configuration.

    Returns version information and health status.
    Note: The frontend determines the API URL via its own runtime-config endpoint,
    so this endpoint no longer returns apiUrl.

    Version updates refresh in the background; only local data gates this response.
    """
    # Get current version
    current_version = get_version()

    schedule_version_refresh(current_version)
    latest_version = _version_cache["latest_version"]
    has_update = _version_cache["has_update"]

    # Independent local reads should not form a serial startup waterfall.
    db_health, (system_name, organization_name) = await asyncio.gather(
        check_database_health(), get_public_branding()
    )
    db_status = db_health["status"]

    if db_status == "offline":
        logger.warning(f"Database offline: {db_health.get('error', 'Unknown error')}")

    return {
        "version": current_version,
        "latestVersion": latest_version,
        "hasUpdate": has_update,
        "dbStatus": db_status,
        "systemName": system_name,
        "organizationName": organization_name,
    }
