from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def notebook_data_dir() -> Path:
    """Return the persistent notebook-data directory for host or Docker.

    Docker mounts ``./notebook_data`` at ``/app/data`` while host development
    keeps it under the repository. One resolver prevents routers from silently
    reading the stale image copy at ``/app/notebook_data``.
    """
    configured = os.getenv("OPEN_NOTEBOOK_DATA_DIR", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path("/app/data"),
        PROJECT_ROOT / "notebook_data",
        PROJECT_ROOT / "data",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    return PROJECT_ROOT / "notebook_data"
