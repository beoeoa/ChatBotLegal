"""Project pending Feature 017 notifications from PostgreSQL to SurrealDB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_repository import PostgresFormGovernanceRepository
from api.form_notification_projection import project_pending_notifications


def _dotenv_value(key: str) -> str:
    path = ROOT / ".env"
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        text = raw.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


async def run(limit: int) -> dict[str, object]:
    database_url = str(
        os.getenv("LEGAL_RELEASE_DATABASE_URL")
        or _dotenv_value("LEGAL_RELEASE_DATABASE_URL")
        or os.getenv("LEGAL_DATABASE_URL")
    ).replace("@host.docker.internal:", "@127.0.0.1:")
    if not database_url.endswith("/legal_chatbot"):
        raise RuntimeError("FEATURE017_NOTIFICATION_LIVE_DATABASE_REQUIRED")
    os.environ.setdefault("SURREAL_URL", "ws://127.0.0.1:8000/rpc")
    os.environ.setdefault("SURREAL_USER", "root")
    os.environ.setdefault("SURREAL_PASSWORD", "root")
    os.environ.setdefault("SURREAL_NAMESPACE", "open_notebook")
    os.environ.setdefault("SURREAL_DATABASE", "open_notebook")
    repository = PostgresFormGovernanceRepository(database_url)
    result = await project_pending_notifications(repository, limit=limit)
    if result["failed"]:
        raise RuntimeError(f"FEATURE017_NOTIFICATION_PROJECTION_FAILED:{result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.limit)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
