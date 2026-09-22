"""Admin backup orchestration with redacted manifests and offline verification.

The service is intentionally local-first.  It copies application data and
retrieval stores without mutating the source, invokes ``pg_dump`` when a
PostgreSQL URL is configured, and records missing optional tooling as a
partial backup instead of pretending that the result is a restore point.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import stat
import struct
import subprocess
import threading
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit

from api.data_paths import PROJECT_ROOT, notebook_data_dir


BACKUP_VERSION = "admin-backup-v1"
BACKUP_STATUSES = {
    "planned",
    "running",
    "verifying",
    "successful",
    "restore_drill_passed",
    "partial",
    "failed",
    "interrupted",
}
_SKIP_DIRS = {
    ".git",
    ".next",
    "node_modules",
    ".venv",
    ".venv-retrieval-cu126",
    "__pycache__",
    "restore-drills",
}
_SECRET_PARTS = {
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "private",
    "firebase-admin",
    "service-account",
    "apikey",
    "api-key",
}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt"}
_BACKUP_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()


class _BackupCipher:
    """Streaming AES-GCM envelope for files at rest in a backup package."""

    MAGIC = b"CBL-AESGCM-v1\n"
    CHUNK_SIZE = 4 * 1024 * 1024

    def __init__(self, secret: str) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aes = AESGCM(hashlib.sha256(secret.encode("utf-8")).digest())

    def encrypt_file(self, source: Path, destination: Path) -> None:
        with source.open("rb") as input_stream, destination.open("wb") as output_stream:
            output_stream.write(self.MAGIC)
            index = 0
            while True:
                block = input_stream.read(self.CHUNK_SIZE)
                if not block:
                    break
                nonce = os.urandom(12)
                associated_data = index.to_bytes(8, "big")
                encrypted = self._aes.encrypt(nonce, block, associated_data)
                output_stream.write(struct.pack(">I", len(encrypted)))
                output_stream.write(nonce)
                output_stream.write(encrypted)
                index += 1

    def decrypt_file(self, source: Path, destination: Path) -> None:
        with source.open("rb") as input_stream, destination.open("wb") as output_stream:
            if input_stream.read(len(self.MAGIC)) != self.MAGIC:
                raise ValueError("BACKUP_ENCRYPTION_HEADER_INVALID")
            index = 0
            while True:
                length_raw = input_stream.read(4)
                if not length_raw:
                    break
                if len(length_raw) != 4:
                    raise ValueError("BACKUP_ENCRYPTION_FRAME_INVALID")
                length = struct.unpack(">I", length_raw)[0]
                nonce = input_stream.read(12)
                encrypted = input_stream.read(length)
                if len(nonce) != 12 or len(encrypted) != length:
                    raise ValueError("BACKUP_ENCRYPTION_FRAME_INVALID")
                output_stream.write(
                    self._aes.decrypt(nonce, encrypted, index.to_bytes(8, "big"))
                )
                index += 1


def _backup_encryption_secret() -> tuple[str, str | None]:
    explicit = str(os.getenv("ADMIN_BACKUP_ENCRYPTION_KEY") or "").strip()
    if explicit:
        return explicit, _safe_text(os.getenv("ADMIN_BACKUP_ENCRYPTION_KEY_ID"), 120) or "admin-backup-explicit-v1"
    master = str(os.getenv("OPEN_NOTEBOOK_ENCRYPTION_KEY") or "").strip()
    if master:
        derived = hmac.new(master.encode("utf-8"), b"admin-backup-aesgcm-v1", hashlib.sha256).hexdigest()
        return derived, "derived-open-notebook-v1"
    return "", None


def _encryption_cipher() -> _BackupCipher | None:
    secret, _ = _backup_encryption_secret()
    if not secret:
        return None
    try:
        return _BackupCipher(secret)
    except Exception:
        return None


def _encryption_descriptor() -> dict[str, Any]:
    cipher = _encryption_cipher()
    _, key_id = _backup_encryption_secret()
    return {
        "status": "configured" if cipher else "not_configured",
        "format": "CBL-AESGCM-v1" if cipher else None,
        "key_id": key_id,
        "required": True,
    }


def _protect_file(path: Path, cipher: _BackupCipher | None) -> Path:
    if cipher is None:
        return path
    encrypted = path.with_name(path.name + ".enc")
    cipher.encrypt_file(path, encrypted)
    # ``copy2`` preserves the Windows read-only bit. Clear it only on the
    # temporary plaintext copy so it can be removed after encryption.
    try:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    path.unlink()
    return encrypted


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def backup_root() -> Path:
    configured = str(os.getenv("ADMIN_BACKUP_ROOT") or os.getenv("BACKUP_ROOT") or "").strip()
    # Keep the default outside the repository.  Deployments should set an
    # explicit path on a volume with an independent retention policy.
    return Path(configured) if configured else Path("D:/ChatBotLegal-backups")


def _safe_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())[:limit]
    lowered = text.casefold()
    for marker in ("password=", "token=", "secret=", "apikey=", "api_key="):
        if marker in lowered:
            index = lowered.index(marker) + len(marker)
            text = text[:index] + "[REDACTED]"
            break
    return text


def _secret_name(path: Path) -> bool:
    name = path.name.casefold()
    if name in {".env", ".env.local", ".env.production", ".env.development"}:
        return True
    if path.suffix.casefold() in _SECRET_SUFFIXES:
        return True
    return any(part in name for part in _SECRET_PARTS)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _label(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return f"external/{path.name}"


def _iter_files(root: Path) -> Iterable[tuple[Path, Path]]:
    if not root.is_dir():
        return
    root = root.resolve()
    for directory, dirs, files in os.walk(root, followlinks=False):
        current = Path(directory)
        dirs[:] = [
            item for item in dirs
            if item not in _SKIP_DIRS and not (current / item).is_symlink()
        ]
        for name in files:
            path = current / name
            if path.is_symlink() or _secret_name(path):
                continue
            try:
                path.stat()
            except OSError:
                continue
            yield path, path.relative_to(root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _configured_path(value: str | None, *, base: Path = PROJECT_ROOT) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else base / path


def _discover_sources() -> list[dict[str, Any]]:
    """Return copyable local sources, de-duplicated by resolved path."""

    candidates: list[tuple[str, Path, bool]] = []
    data = notebook_data_dir()
    if data.is_dir():
        candidates.append(("app_data", data, True))
    for label, relative in (
        ("release_manifests", "release-data/legal/serving_manifests"),
        ("application_data", "data"),
    ):
        path = PROJECT_ROOT / relative
        if path.is_dir():
            candidates.append((label, path, False))

    chroma = _configured_path(os.getenv("LEGAL_CHROMA_PATH"))
    if chroma is None:
        data_root = _configured_path(os.getenv("LEGAL_DATA_ROOT"))
        chroma = (data_root / "chroma_store") if data_root else None
    if chroma and chroma.is_dir():
        candidates.append(("chroma", chroma, True))
    else:
        # A local installation can have more than one collection store. Keep
        # the search within runtime data roots so test fixtures and log
        # snapshots are never silently promoted to a production backup.
        for base in (PROJECT_ROOT / "release-data", PROJECT_ROOT / "data", data):
            if not base.is_dir():
                continue
            for db in base.glob("**/chroma.sqlite3"):
                if db.is_file() and not _secret_name(db):
                    candidates.append(("chroma", db.parent, True))

    seen: set[str] = set()
    chroma_paths = [path.resolve() for label, path, _ in candidates if label == "chroma"]
    sources: list[dict[str, Any]] = []
    for label, path, required in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen or _inside(resolved, backup_root()):
            continue
        seen.add(key)
        entries = list(_iter_files(resolved))
        if label == "application_data" and chroma_paths:
            entries = [
                (file_path, relative)
                for file_path, relative in entries
                if not any(_inside(file_path, chroma_path) for chroma_path in chroma_paths)
            ]
        source_info: dict[str, Any] = {
            "name": label,
            "source": _label(resolved),
            "path": resolved,
            "required": required,
            "estimated_bytes": sum(item.stat().st_size for item, _ in entries if item.exists()),
            "file_count": len(entries),
        }
        if label == "application_data" and chroma_paths:
            source_info["exclude_paths"] = [str(item) for item in chroma_paths]
        sources.append(source_info)
    present_names = {str(item["name"]) for item in sources}
    for required_name in ("app_data", "chroma"):
        if required_name not in present_names:
            sources.append({
                "name": required_name,
                "source": "not_found",
                "path": None,
                "required": True,
                "estimated_bytes": 0,
                "file_count": 0,
                "unavailable_reason": "SOURCE_NOT_FOUND",
            })
    return sources


def _safe_env_snapshot() -> dict[str, str]:
    allowed_prefixes = ("LEGAL_", "FAQ_", "FORM_", "SURREAL_", "OPEN_NOTEBOOK_DATA_DIR")
    allowed_exact = {"APP_VERSION", "GIT_COMMIT", "PRODUCTION_MODE"}
    result: dict[str, str] = {}
    for key, value in os.environ.items():
        if key not in allowed_exact and not key.startswith(allowed_prefixes):
            continue
        if _secret_name(Path(key)):
            continue
        # Database URLs can contain a password even when the variable name is
        # not obviously secret. Preserve only the driver/host/database shape.
        if key.endswith("DATABASE_URL") or key.endswith("_URL"):
            parsed = urlsplit(value)
            if parsed.scheme and parsed.hostname:
                result[key] = f"{parsed.scheme}://{parsed.hostname}{parsed.path or ''}"
            else:
                result[key] = "[CONFIGURED]" if value else ""
        elif key.endswith(("_PATH", "_ROOT", "_DIR")):
            result[key] = "[CONFIGURED]" if value else ""
        else:
            result[key] = _safe_text(value, 300)
    result["backup_version"] = BACKUP_VERSION
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _manifest_path(backup_id: str) -> Path:
    root = backup_root().resolve()
    candidate = (root / backup_id / "manifest.json").resolve()
    if not _inside(candidate, root) or candidate.parent.name != backup_id:
        raise ValueError("BACKUP_ID_INVALID")
    return candidate


def _member_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not _inside(candidate, root):
        raise ValueError("BACKUP_MEMBER_INVALID")
    return candidate


def _load_manifest(backup_id: str) -> dict[str, Any]:
    path = _manifest_path(backup_id)
    if not path.is_file():
        raise FileNotFoundError(backup_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or str(payload.get("backup_id")) != backup_id:
        raise ValueError("BACKUP_MANIFEST_INVALID")
    return payload


def _save_manifest(manifest: dict[str, Any]) -> None:
    _write_json(_manifest_path(str(manifest["backup_id"])), manifest)


def _component_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": source["name"],
        "source": source["source"],
        "required": bool(source.get("required")),
        "status": "unavailable" if not source.get("path") else "planned",
        "file_count": int(source.get("file_count") or 0),
        "bytes": int(source.get("estimated_bytes") or 0),
        "warnings": [],
        **({"reason": source.get("unavailable_reason")} if source.get("unavailable_reason") else {}),
    }


def _source_selected_for_kind(name: str, kind: str) -> bool:
    if kind == "full":
        return True
    if kind == "retrieval":
        return name in {"chroma", "release_manifests"}
    return name not in {"chroma", "release_manifests"}


def preflight(kind: str = "full") -> dict[str, Any]:
    root = backup_root()
    sources = [item for item in _discover_sources() if _source_selected_for_kind(item["name"], kind)]
    target_parent = root.parent if root.parent.exists() else root
    try:
        usage = shutil.disk_usage(target_parent)
        free_bytes = usage.free
    except OSError:
        free_bytes = None
    estimated = sum(int(item.get("estimated_bytes") or 0) for item in sources)
    # Database exports are not known until the server is contacted; reserve a
    # conservative copy margin without presenting it as an exact size.
    reserve = estimated + max(5 * 1024 * 1024, int(estimated * 0.10))
    writable = True
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        writable = False
    writer_jobs = max(0, int(os.getenv("LEGAL_WRITE_JOBS_RUNNING", "0") or 0))
    warnings: list[str] = []
    if not writable:
        warnings.append("BACKUP_TARGET_NOT_WRITABLE")
    if free_bytes is not None and free_bytes < reserve:
        warnings.append("BACKUP_SPACE_LOW")
    if writer_jobs:
        warnings.append("LEGAL_WRITE_JOB_RUNNING")
    if _encryption_descriptor()["status"] != "configured":
        warnings.append("BACKUP_ENCRYPTION_NOT_CONFIGURED")
    for item in sources:
        if not item.get("path"):
            warnings.append(f"{str(item['name']).upper()}_SOURCE_NOT_FOUND")
    if kind != "retrieval":
        if not os.getenv("LEGAL_DATABASE_URL") and not os.getenv("FAQ_GOVERNANCE_DATABASE_URL"):
            warnings.append("POSTGRES_URL_NOT_CONFIGURED")
        if not os.getenv("SURREAL_URL"):
            warnings.append("SURREAL_URL_NOT_CONFIGURED")
    return {
        "backup_version": BACKUP_VERSION,
        "target_label": root.name or str(root),
        "target_configured": bool(os.getenv("ADMIN_BACKUP_ROOT") or os.getenv("BACKUP_ROOT")),
        "writable": writable,
        "free_bytes": free_bytes,
        "estimated_file_bytes": estimated,
        "reserved_bytes": reserve,
        "write_jobs": writer_jobs,
        "components": [_component_summary(item) for item in sources],
        "warnings": warnings,
        "can_start": writable and not writer_jobs and (free_bytes is None or free_bytes >= reserve),
    }


def create_backup_job(*, reason: str, actor_id: str | None, kind: str = "full") -> dict[str, Any]:
    reason = _safe_text(reason, 1000)
    if len(reason) < 3:
        raise ValueError("BACKUP_REASON_REQUIRED")
    if kind not in {"full", "application", "retrieval"}:
        raise ValueError("BACKUP_KIND_INVALID")
    check = preflight(kind)
    if not check["writable"]:
        raise RuntimeError("BACKUP_TARGET_NOT_WRITABLE")
    if check["write_jobs"]:
        raise RuntimeError("LEGAL_WRITE_JOB_RUNNING")
    with _STATE_LOCK:
        if any(item.get("status") in {"planned", "running", "verifying"} for item in list_backups()):
            raise RuntimeError("BACKUP_JOB_ALREADY_ACTIVE")
    backup_id = f"bkp-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
    sources = [item for item in _discover_sources() if _source_selected_for_kind(item["name"], kind)]
    manifest: dict[str, Any] = {
        "backup_version": BACKUP_VERSION,
        "backup_id": backup_id,
        "created_at": _now(),
        "completed_at": None,
        "actor_id": _safe_text(actor_id, 200) or None,
        "reason": reason,
        "kind": kind,
        "status": "planned",
        "app_version": _safe_text(os.getenv("APP_VERSION") or os.getenv("GIT_COMMIT") or "unknown", 120),
        "components": [_component_summary(item) for item in sources],
        "files": [],
        "warnings": list(check.get("warnings") or []),
        "verification": None,
        "restore_drill": None,
        "encryption": _encryption_descriptor(),
        "safe_config": _safe_env_snapshot(),
    }
    path = backup_root() / backup_id
    with _STATE_LOCK:
        path.mkdir(parents=True, exist_ok=False)
    _save_manifest(manifest)
    return manifest


def _copy_source(manifest: dict[str, Any], source: Mapping[str, Any]) -> None:
    backup_id = str(manifest["backup_id"])
    target_root = backup_root() / backup_id / "components" / str(source["name"])
    target_root.mkdir(parents=True, exist_ok=True)
    component = next(item for item in manifest["components"] if item["name"] == source["name"] and item["source"] == source["source"])
    if not source.get("path"):
        component.update({"status": "unavailable", "reason": source.get("unavailable_reason") or "SOURCE_NOT_FOUND"})
        manifest["warnings"].append(f"{source['name'].upper()}_SOURCE_NOT_FOUND")
        return
    component["status"] = "running"
    copied = 0
    bytes_total = 0
    cipher = _encryption_cipher()
    excluded_paths = [Path(item) for item in source.get("exclude_paths") or []]
    for path, relative in _iter_files(Path(source["path"])):
        if any(_inside(path, excluded) for excluded in excluded_paths):
            continue
        destination = target_root / relative
        storage_layout = "source_tree"
        # Windows still applies MAX_PATH to some file operations even when the
        # source itself can be read. Store only overlong members under a stable
        # content-addressed name and retain their original relative path in the
        # manifest for a future restore operation.
        if os.name == "nt" and len(str(destination.absolute())) >= 240:
            relative_digest = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()
            destination = target_root / "__long_paths__" / f"{relative_digest}{path.suffix}"
            storage_layout = "hashed_long_path"
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = path.stat()
        shutil.copy2(path, destination)
        protected_destination = _protect_file(destination, cipher)
        digest = _sha256(protected_destination)
        after = path.stat()
        # Compare the source only for plaintext copies. Encrypted files are
        # verified against their encrypted checksum and decrypted by the
        # restore drill.
        if cipher is None and digest != _sha256(path):
            raise RuntimeError(f"BACKUP_HASH_MISMATCH:{source['name']}")
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            component["warnings"].append(f"SOURCE_CHANGED:{relative.as_posix()}")
        record = {
            "component": source["name"],
            "source": f"{source['source']}/{relative.as_posix()}",
            "original_relative": relative.as_posix(),
            "storage_layout": storage_layout,
            "backup": str(protected_destination.relative_to(backup_root() / backup_id)).replace("\\", "/"),
            "bytes": protected_destination.stat().st_size,
            "source_bytes": before.st_size,
            "sha256": digest,
        }
        manifest["files"].append(record)
        copied += 1
        bytes_total += protected_destination.stat().st_size
    component.update({"status": "successful", "file_count": copied, "bytes": bytes_total})
    sqlite_files = list(target_root.glob("**/*.sqlite3"))
    for db in sqlite_files:
        try:
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()
            if check != ("ok",):
                component["warnings"].append("SQLITE_QUICK_CHECK_FAILED")
        except sqlite3.DatabaseError:
            component["warnings"].append("SQLITE_QUICK_CHECK_UNAVAILABLE")
    if component["warnings"]:
        manifest["warnings"].extend(component["warnings"])


def _run_pg_dump(manifest: dict[str, Any]) -> None:
    url = str(os.getenv("LEGAL_DATABASE_URL") or os.getenv("FAQ_GOVERNANCE_DATABASE_URL") or "").strip()
    component = {"name": "postgresql", "status": "planned", "required": True, "warnings": []}
    manifest["components"].append(component)
    if not url:
        component.update(status="unavailable", reason="POSTGRES_URL_NOT_CONFIGURED")
        manifest["warnings"].append("POSTGRES_URL_NOT_CONFIGURED")
        return
    executable = str(os.getenv("PG_DUMP_PATH") or shutil.which("pg_dump") or "").strip()
    if not executable:
        component.update(status="unavailable", reason="PG_DUMP_NOT_FOUND")
        manifest["warnings"].append("PG_DUMP_NOT_FOUND")
        return
    output = backup_root() / str(manifest["backup_id"]) / "postgres" / "legal.dump"
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Keep credentials out of the process argument list. The URL is parsed
    # into standard libpq variables for this child process only; it is never
    # persisted in the manifest or logs.
    env["PGCONNECT_TIMEOUT"] = "15"
    parsed = urlsplit(url)
    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    if parsed.path.lstrip("/"):
        env["PGDATABASE"] = parsed.path.lstrip("/")
    command = [executable, "--format=custom", "--no-password", "--file", str(output)]
    result = subprocess.run(command, env=env, capture_output=True, timeout=120)
    if result.returncode != 0 or not output.is_file():
        component.update(status="failed", reason="PG_DUMP_FAILED")
        manifest["warnings"].append("PG_DUMP_FAILED")
        return
    protected_output = _protect_file(output, _encryption_cipher())
    digest = _sha256(protected_output)
    backup_relative = str(protected_output.relative_to(backup_root() / str(manifest["backup_id"]))).replace("\\", "/")
    component.update(status="successful", bytes=protected_output.stat().st_size, sha256=digest, format="custom")
    manifest["files"].append({"component": "postgresql", "source": "database", "backup": backup_relative, "bytes": protected_output.stat().st_size, "sha256": digest})


def _run_surreal_export(manifest: dict[str, Any]) -> None:
    component = {"name": "surrealdb", "status": "planned", "required": True, "warnings": []}
    manifest["components"].append(component)
    url = str(os.getenv("SURREAL_URL") or "").strip()
    command_text = str(os.getenv("SURREAL_EXPORT_COMMAND") or "").strip()
    docker_container = str(os.getenv("SURREAL_EXPORT_DOCKER_CONTAINER") or "").strip()
    if not url:
        component.update(status="unavailable", reason="SURREAL_URL_NOT_CONFIGURED")
        manifest["warnings"].append("SURREAL_URL_NOT_CONFIGURED")
        return
    executable = shutil.which("surreal") if not command_text and not docker_container else None
    docker_executable = (
        shutil.which("docker.exe") or shutil.which("docker")
        if docker_container
        else None
    )
    if not command_text and not docker_container and not executable:
        component.update(status="unavailable", reason="SURREAL_EXPORT_TOOL_NOT_FOUND")
        manifest["warnings"].append("SURREAL_EXPORT_TOOL_NOT_FOUND")
        return
    if docker_container and not docker_executable:
        component.update(status="unavailable", reason="DOCKER_EXPORT_TOOL_NOT_FOUND")
        manifest["warnings"].append("DOCKER_EXPORT_TOOL_NOT_FOUND")
        return
    output = backup_root() / str(manifest["backup_id"]) / "surreal" / "export.surql"
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if env.get("SURREAL_PASSWORD") and not env.get("SURREAL_PASS"):
        env["SURREAL_PASS"] = env["SURREAL_PASSWORD"]
    stream_output = False
    if command_text:
        # A deployment-provided wrapper is trusted configuration and must use
        # the explicit output placeholder.
        if "{output}" not in command_text:
            component.update(status="failed", reason="SURREAL_EXPORT_OUTPUT_PLACEHOLDER_REQUIRED")
            manifest["warnings"].append("SURREAL_EXPORT_OUTPUT_PLACEHOLDER_REQUIRED")
            return
        command: list[str] | str = command_text.replace("{output}", str(output))
        use_shell = True
    elif docker_container:
        namespace = str(os.getenv("SURREAL_NAMESPACE") or "open_notebook").strip()
        database = str(os.getenv("SURREAL_DATABASE") or "open_notebook").strip()
        endpoint = str(os.getenv("SURREAL_EXPORT_DOCKER_ENDPOINT") or "http://localhost:8000").strip()
        # Pass credential names through Docker's environment inheritance. The
        # values stay out of the child command line and backup manifest.
        command = [
            str(docker_executable), "exec", "-e", "SURREAL_USER", "-e", "SURREAL_PASS",
            docker_container, "/surreal", "export", "--log", "none", "--endpoint", endpoint,
            "--namespace", namespace, "--database", database, "-",
        ]
        use_shell = False
        stream_output = True
    else:
        namespace = str(os.getenv("SURREAL_NAMESPACE") or "open_notebook").strip()
        database = str(os.getenv("SURREAL_DATABASE") or "open_notebook").strip()
        command = [
            str(executable), "export", "--endpoint", url,
            "--namespace", namespace, "--database", database, str(output),
        ]
        use_shell = False
    if stream_output:
        with output.open("wb") as output_stream:
            result = subprocess.run(
                command,
                shell=use_shell,
                env=env,
                stdout=output_stream,
                stderr=subprocess.PIPE,
                timeout=180,
            )
    else:
        result = subprocess.run(command, shell=use_shell, env=env, capture_output=True, timeout=120)
    if result.returncode != 0 or not output.is_file():
        component.update(status="failed", reason="SURREAL_EXPORT_FAILED")
        manifest["warnings"].append("SURREAL_EXPORT_FAILED")
        return
    try:
        exported = output.read_text(encoding="utf-8")
        if not exported.strip():
            raise ValueError("empty export")
    except (OSError, UnicodeDecodeError, ValueError):
        component.update(status="failed", reason="SURREAL_EXPORT_INVALID")
        manifest["warnings"].append("SURREAL_EXPORT_INVALID")
        return
    protected_output = _protect_file(output, _encryption_cipher())
    digest = _sha256(protected_output)
    backup_relative = str(protected_output.relative_to(backup_root() / str(manifest["backup_id"]))).replace("\\", "/")
    component.update(status="successful", bytes=protected_output.stat().st_size, sha256=digest, format="surql")
    manifest["files"].append({"component": "surrealdb", "source": "database", "backup": backup_relative, "bytes": protected_output.stat().st_size, "sha256": digest})


def run_backup(backup_id: str) -> dict[str, Any]:
    manifest = _load_manifest(backup_id)
    if manifest.get("status") not in {"planned", "interrupted"}:
        return manifest
    if not _BACKUP_LOCK.acquire(blocking=False):
        manifest.update(status="failed", completed_at=_now())
        manifest["warnings"].append("ANOTHER_BACKUP_RUNNING")
        _save_manifest(manifest)
        return manifest
    try:
        manifest.update(status="running", started_at=_now())
        _save_manifest(manifest)
        sources = _discover_sources()
        selected_names = {item["name"] for item in manifest.get("components", [])}
        for source in sources:
            if source["name"] not in selected_names:
                continue
            _copy_source(manifest, source)
            _save_manifest(manifest)
        if str(manifest.get("kind") or "full") == "retrieval":
            manifest["components"].extend([
                {"name": "postgresql", "status": "skipped", "required": False, "warnings": [], "reason": "RETRIEVAL_BACKUP"},
                {"name": "surrealdb", "status": "skipped", "required": False, "warnings": [], "reason": "RETRIEVAL_BACKUP"},
            ])
        else:
            _run_pg_dump(manifest)
            _save_manifest(manifest)
            _run_surreal_export(manifest)
        if manifest.get("encryption", {}).get("status") != "configured":
            if "BACKUP_ENCRYPTION_NOT_CONFIGURED" not in manifest["warnings"]:
                manifest["warnings"].append("BACKUP_ENCRYPTION_NOT_CONFIGURED")
        component_statuses = [str(item.get("status")) for item in manifest.get("components", [])]
        required_failures = any(
            item.get("required") and item.get("status") != "successful"
            for item in manifest.get("components", [])
        )
        encryption_block = manifest.get("encryption", {}).get("status") != "configured"
        manifest["status"] = "partial" if required_failures or encryption_block or manifest.get("warnings") else "successful"
        manifest["completed_at"] = _now()
        manifest["component_statuses"] = component_statuses
        _save_manifest(manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["completed_at"] = _now()
        manifest.setdefault("warnings", []).append(f"BACKUP_FAILED:{type(exc).__name__}")
        _save_manifest(manifest)
        return manifest
    finally:
        _BACKUP_LOCK.release()


def verify_backup(backup_id: str) -> dict[str, Any]:
    manifest = _load_manifest(backup_id)
    if manifest.get("status") in {"planned", "running"}:
        raise RuntimeError("BACKUP_NOT_COMPLETE")
    manifest["status"] = "verifying"
    _save_manifest(manifest)
    root = backup_root() / backup_id
    mismatches: list[str] = []
    checked = 0
    for item in manifest.get("files") or []:
        try:
            path = _member_path(root, str(item.get("backup") or ""))
        except ValueError:
            mismatches.append(str(item.get("backup") or "invalid"))
            continue
        try:
            digest = _sha256(path)
        except (OSError, ValueError):
            mismatches.append(str(item.get("backup") or "missing"))
            continue
        checked += 1
        if digest != str(item.get("sha256") or ""):
            mismatches.append(str(item.get("backup") or "mismatch"))
    dump_format_checked = False
    dump_format_passed = True
    cipher = _encryption_cipher() if manifest.get("encryption", {}).get("status") == "configured" else None
    encryption_checked = manifest.get("encryption", {}).get("status") != "configured"
    if manifest.get("encryption", {}).get("status") == "configured" and cipher is None:
        mismatches.append("BACKUP_ENCRYPTION_KEY_UNAVAILABLE")
        encryption_checked = False
    elif cipher is not None:
        encrypted_item = next(
            (item for item in manifest.get("files") or [] if str(item.get("backup") or "").endswith(".enc")),
            None,
        )
        if encrypted_item:
            try:
                encrypted_path = _member_path(root, str(encrypted_item.get("backup") or ""))
                with tempfile.TemporaryDirectory(prefix="chatbotlegal-backup-verify-") as temporary:
                    probe = Path(temporary) / "decrypted-probe"
                    cipher.decrypt_file(encrypted_path, probe)
                    encryption_checked = probe.is_file()
            except Exception:
                mismatches.append(str(encrypted_item.get("backup") or "encrypted-probe"))
                encryption_checked = False
    for item in manifest.get("files") or []:
        if item.get("component") != "postgresql":
            continue
        dump_path = _member_path(root, str(item.get("backup") or ""))
        restore_tool = str(os.getenv("PG_RESTORE_PATH") or shutil.which("pg_restore") or "").strip()
        if restore_tool:
            dump_format_checked = True
            try:
                with tempfile.TemporaryDirectory(prefix="chatbotlegal-backup-verify-") as temporary:
                    plain_dump = Path(temporary) / "legal.dump"
                    if cipher:
                        cipher.decrypt_file(dump_path, plain_dump)
                    else:
                        plain_dump = dump_path
                    result = subprocess.run([restore_tool, "--list", str(plain_dump)], capture_output=True, timeout=60)
                dump_format_passed = result.returncode == 0
            except Exception:
                dump_format_passed = False
            if not dump_format_passed:
                mismatches.append(str(item.get("backup") or "postgres/legal.dump"))
        else:
            dump_format_passed = False
            manifest.setdefault("warnings", []).append("PG_RESTORE_NOT_FOUND")
        break
    for item in manifest.get("files") or []:
        if item.get("component") != "surrealdb":
            continue
        export_path = _member_path(root, str(item.get("backup") or ""))
        try:
            with tempfile.TemporaryDirectory(prefix="chatbotlegal-backup-verify-") as temporary:
                plain_export = Path(temporary) / "export.surql"
                if cipher:
                    cipher.decrypt_file(export_path, plain_export)
                else:
                    plain_export = export_path
                if not plain_export.read_text(encoding="utf-8").strip():
                    raise ValueError("empty export")
        except Exception:
            mismatches.append(str(item.get("backup") or "surreal/export.surql"))
        break
    passed = (
        not mismatches
        and dump_format_passed
        and manifest.get("status") not in {"failed", "interrupted"}
    )
    verification = {
        "checked_at": _now(),
        "passed": passed,
        "checked_files": checked,
        "mismatches": mismatches,
        "dump_format_checked": dump_format_checked,
        "dump_format_passed": dump_format_passed,
        "encryption_checked": encryption_checked,
    }
    manifest["verification"] = verification
    if not passed:
        manifest["status"] = "failed"
    elif manifest.get("status") == "verifying":
        manifest["status"] = "successful" if not manifest.get("warnings") else "partial"
    _save_manifest(manifest)
    return verification | {"backup_id": backup_id, "status": manifest["status"]}


def restore_drill(backup_id: str) -> dict[str, Any]:
    manifest = _load_manifest(backup_id)
    if manifest.get("status") not in {"successful", "restore_drill_passed"}:
        return {
            "backup_id": backup_id,
            "passed": False,
            "status": "failed",
            "reason": "BACKUP_NOT_RESTORE_READY",
        }
    verification = verify_backup(backup_id)
    if not verification.get("passed"):
        return {"backup_id": backup_id, "passed": False, "status": "failed", "reason": "VERIFY_FAILED"}
    source_root = (backup_root() / backup_id).resolve()
    target = (backup_root() / "restore-drills" / f"{backup_id}-{uuid.uuid4().hex[:8]}").resolve()
    if _inside(target, source_root):
        raise RuntimeError("RESTORE_TARGET_INVALID")
    target.mkdir(parents=True, exist_ok=False)
    cipher = _encryption_cipher() if manifest.get("encryption", {}).get("status") == "configured" else None
    if manifest.get("encryption", {}).get("status") == "configured" and cipher is None:
        return {"backup_id": backup_id, "passed": False, "status": "failed", "reason": "BACKUP_ENCRYPTION_KEY_UNAVAILABLE"}
    for item in manifest.get("files") or []:
        source = _member_path(source_root, str(item["backup"]))
        relative = str(item["backup"])
        destination_relative = relative[:-4] if cipher and relative.endswith(".enc") else relative
        destination = _member_path(target, destination_relative)
        if os.name == "nt" and len(str(destination.absolute())) >= 240:
            drill_name = hashlib.sha256(destination_relative.encode("utf-8")).hexdigest()
            destination = _member_path(target, f"__long_paths__/{drill_name}{Path(destination_relative).suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _sha256(source) != str(item.get("sha256") or ""):
            manifest["restore_drill"] = {"passed": False, "reason": "RESTORE_HASH_MISMATCH"}
            _save_manifest(manifest)
            return {"backup_id": backup_id, "passed": False, "status": "failed", "reason": "RESTORE_HASH_MISMATCH"}
        if cipher:
            try:
                cipher.decrypt_file(source, destination)
            except Exception:
                manifest["restore_drill"] = {"passed": False, "reason": "RESTORE_DECRYPT_FAILED"}
                _save_manifest(manifest)
                return {"backup_id": backup_id, "passed": False, "status": "failed", "reason": "RESTORE_DECRYPT_FAILED"}
        else:
            shutil.copy2(source, destination)
    for db in target.glob("**/*.sqlite3"):
        try:
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    return {"backup_id": backup_id, "passed": False, "status": "failed", "reason": "RESTORE_SQLITE_CHECK_FAILED"}
        except sqlite3.DatabaseError:
            return {"backup_id": backup_id, "passed": False, "status": "failed", "reason": "RESTORE_SQLITE_CHECK_FAILED"}
    result = {"backup_id": backup_id, "passed": True, "status": "restore_drill_passed", "file_count": len(manifest.get("files") or [])}
    manifest["restore_drill"] = {**result, "completed_at": _now()}
    manifest["status"] = "restore_drill_passed"
    _save_manifest(manifest)
    return result


def get_backup(backup_id: str) -> dict[str, Any]:
    return _load_manifest(backup_id)


def list_backups() -> list[dict[str, Any]]:
    root = backup_root()
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name == "restore-drills":
            continue
        try:
            manifest = _load_manifest(directory.name)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        result.append({
            key: manifest.get(key)
            for key in (
                "backup_id", "created_at", "completed_at", "actor_id", "reason",
                "kind", "status", "warnings", "verification", "restore_drill",
                "components", "encryption",
            )
        })
    return sorted(result, key=lambda item: str(item.get("created_at") or ""), reverse=True)


__all__ = [
    "BACKUP_STATUSES",
    "BACKUP_VERSION",
    "backup_root",
    "create_backup_job",
    "get_backup",
    "list_backups",
    "preflight",
    "restore_drill",
    "run_backup",
    "verify_backup",
]
