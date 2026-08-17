"""Run a privacy-safe SurrealDB raw-backup restore drill.

The source database is read-only for this verifier. A RocksDB backup is copied
to a new isolated drill directory, opened by a separate SurrealDB process, and
compared with the source by database schema and per-table record counts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from surrealdb import AsyncSurreal


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_surreal_identifier(value: str) -> str:
    """Return a safely quoted SurrealDB schema identifier."""

    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe_surreal_identifier:{value}")
    return f"`{value}`"


def _stable_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _tree_inventory(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(block)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest.digest())
        file_count += 1
        total_bytes += size
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


async def collect_database_inventory(
    endpoint: str,
    *,
    namespace: str,
    database: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    """Collect schema digest and record counts without returning record data."""

    client = AsyncSurreal(endpoint)
    await client.connect()
    try:
        await client.signin({"username": username, "password": password})
        await client.use(namespace, database)
        info = await client.query("INFO FOR DB;")
        if not isinstance(info, Mapping):
            raise RuntimeError("unexpected_info_for_db_shape")
        tables = info.get("tables") or {}
        if not isinstance(tables, Mapping):
            raise RuntimeError("unexpected_table_schema_shape")

        counts: dict[str, int] = {}
        for table_name in sorted(str(name) for name in tables):
            quoted = quote_surreal_identifier(table_name)
            result = await client.query(
                f"SELECT count() AS count FROM {quoted} GROUP ALL;"
            )
            count = 0
            if isinstance(result, list) and result:
                row = result[0]
                if isinstance(row, Mapping):
                    count = int(row.get("count") or 0)
            counts[table_name] = count

        return {
            "schema_sha256": _stable_sha256(info),
            "table_count": len(counts),
            "total_records": sum(counts.values()),
            "table_counts": counts,
        }
    finally:
        await client.close()


def compare_database_inventories(
    source: Mapping[str, Any],
    restored: Mapping[str, Any],
) -> dict[str, Any]:
    source_counts = {
        str(key): int(value)
        for key, value in (source.get("table_counts") or {}).items()
    }
    restored_counts = {
        str(key): int(value)
        for key, value in (restored.get("table_counts") or {}).items()
    }
    mismatches: dict[str, dict[str, int | None]] = {}
    for table_name in sorted(set(source_counts) | set(restored_counts)):
        source_count = source_counts.get(table_name)
        restored_count = restored_counts.get(table_name)
        if source_count != restored_count:
            mismatches[table_name] = {
                "source": source_count,
                "restored": restored_count,
            }
    schema_match = source.get("schema_sha256") == restored.get("schema_sha256")
    return {
        "passed": schema_match and not mismatches,
        "schema_match": schema_match,
        "count_mismatches": mismatches,
    }


def _wait_for_health(url: str, process: subprocess.Popen[Any], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"restored_surreal_exited:{process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise TimeoutError("restored_surreal_health_timeout")


def _assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((host, port)) == 0:
            raise RuntimeError(f"restore_port_in_use:{port}")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    started = datetime.now(UTC)
    backup_dir = args.backup_dir.resolve()
    drill_dir = args.drill_dir.resolve()
    executable = args.surreal_executable.resolve()

    if not backup_dir.is_dir():
        raise FileNotFoundError(f"backup_dir_missing:{backup_dir}")
    if not (backup_dir / "mydatabase.db").is_dir():
        raise FileNotFoundError("backup_rocksdb_missing")
    if drill_dir.exists():
        raise FileExistsError(f"drill_dir_already_exists:{drill_dir}")
    if not executable.is_file():
        raise FileNotFoundError(f"surreal_executable_missing:{executable}")

    _assert_port_available(args.bind_host, args.restore_port)
    backup_files = _tree_inventory(backup_dir)
    shutil.copytree(backup_dir, drill_dir)
    drill_files = _tree_inventory(drill_dir)
    file_copy_match = backup_files == drill_files
    if not file_copy_match:
        raise RuntimeError("surreal_raw_copy_checksum_mismatch")

    restore_endpoint = f"ws://{args.bind_host}:{args.restore_port}/rpc"
    restore_health = f"http://{args.bind_host}:{args.restore_port}/health"
    datastore = f"rocksdb:{(drill_dir / 'mydatabase.db').as_posix()}"
    log_path = args.log_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            [
                str(executable),
                "start",
                "--no-banner",
                "--log",
                "warn",
                "--bind",
                f"{args.bind_host}:{args.restore_port}",
                datastore,
            ],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        try:
            _wait_for_health(restore_health, process, args.start_timeout_seconds)
            source, restored = await asyncio.gather(
                collect_database_inventory(
                    args.source_endpoint,
                    namespace=args.namespace,
                    database=args.database,
                    username=args.username,
                    password=args.password,
                ),
                collect_database_inventory(
                    restore_endpoint,
                    namespace=args.namespace,
                    database=args.database,
                    username=args.username,
                    password=args.password,
                ),
            )
            comparison = compare_database_inventories(source, restored)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    finished = datetime.now(UTC)
    return {
        "schema_version": 1,
        "status": "PASS" if comparison["passed"] and file_copy_match else "FAIL",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "source": {
            "endpoint": args.source_endpoint,
            "namespace": args.namespace,
            "database": args.database,
            "inventory": source,
        },
        "restored": {
            "endpoint": restore_endpoint,
            "namespace": args.namespace,
            "database": args.database,
            "inventory": restored,
        },
        "raw_copy": {
            "backup": backup_files,
            "drill": drill_files,
            "match": file_copy_match,
        },
        "comparison": comparison,
        "privacy": {
            "contains_record_content": False,
            "contains_credentials": False,
            "contains_internal_paths": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--drill-dir", type=Path, required=True)
    parser.add_argument("--surreal-executable", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument(
        "--source-endpoint",
        default=os.getenv("SURREAL_URL", "ws://127.0.0.1:8000/rpc"),
    )
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--restore-port", type=int, default=8010)
    parser.add_argument("--namespace", default="open_notebook")
    parser.add_argument("--database", default="open_notebook")
    parser.add_argument("--username", default=os.getenv("SURREAL_USER", "root"))
    parser.add_argument(
        "--password",
        default=os.getenv("SURREAL_PASSWORD") or os.getenv("SURREAL_PASS") or "root",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--start-timeout-seconds", type=float, default=60.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = asyncio.run(_run(args))
    _atomic_write_json(args.report.resolve(), report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "table_count": report["source"]["inventory"]["table_count"],
                "total_records": report["source"]["inventory"]["total_records"],
                "schema_match": report["comparison"]["schema_match"],
                "count_mismatch_count": len(
                    report["comparison"]["count_mismatches"]
                ),
                "raw_copy_match": report["raw_copy"]["match"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
