"""Pilot feature-flag registry.

The registry is local-first, audit-controlled through Admin Control Center, and
keeps the unsafe automation flags permanently disabled in the pilot.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FLAGS_PATH = ROOT / "data" / "pilot" / "feature_flags.json"

FEATURE_ORDER = (
    "conversations",
    "document_viewer_pdf",
    "faq",
    "support_chat",
    "proposals_crawler_ocr",
)
LOCKED_DISABLED = ("auto_import", "auto_approve")


def default_flags() -> dict[str, bool]:
    return {**{name: False for name in FEATURE_ORDER}, **{name: False for name in LOCKED_DISABLED}}


def load_flags() -> dict[str, Any]:
    defaults = default_flags()
    try:
        data = json.loads(FLAGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    configured = data.get("flags") if isinstance(data, dict) else {}
    flags = {name: bool((configured or {}).get(name, defaults[name])) for name in defaults}
    flags.update({name: False for name in LOCKED_DISABLED})
    return {
        "flags": flags,
        "stage": int(data.get("stage") or 0) if isinstance(data, dict) else 0,
        "updated_at": data.get("updated_at") if isinstance(data, dict) else None,
        "updated_by": data.get("updated_by") if isinstance(data, dict) else None,
        "history": data.get("history") if isinstance(data, dict) else [],
    }


def save_flags(*, stage: int, updated_by: str | None = None) -> dict[str, Any]:
    if stage < 0 or stage > len(FEATURE_ORDER):
        raise ValueError(f"stage phải từ 0 đến {len(FEATURE_ORDER)}")
    flags = default_flags()
    for name in FEATURE_ORDER[:stage]:
        flags[name] = True
    payload = {
        "stage": stage,
        "flags": flags,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by or "admin",
        "safety": "Pilot không bật auto_import hoặc auto_approve.",
    }
    FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FLAGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
