"""Apply the approved candidate→review→embed workflow for 116/2026/TT-BCA."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


LAW_NUMBER = "116/2026/TT-BCA"
APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"


def next_action(status: str, import_status: str | None = None) -> str:
    status = str(status or "").strip().lower()
    import_status = str(import_status or "").strip().lower()
    if status in {"pending", "changes_requested"}:
        return "review"
    if status in {"import_queued"} or import_status in {"queued", "running"}:
        return "poll"
    if status in {"approved", "import_failed"}:
        return "enqueue"
    if status == "imported" and import_status == "completed":
        return "verify"
    return "fail"


async def _candidate(client: httpx.AsyncClient, base_url: str, candidate_id: str) -> dict[str, Any]:
    response = await client.get(
        f"{base_url}/api/legal/crawl/candidates/{quote(candidate_id, safe=':')}"
    )
    response.raise_for_status()
    return response.json()["candidate"]


async def apply_import(
    *,
    payload_path: Path,
    output_path: Path,
    api_url: str,
    retrieval_url: str,
    approval: str,
) -> dict[str, Any]:
    if approval != APPROVAL_PHRASE:
        raise ValueError("GOLDEN294_LIVE_APPROVAL_PHRASE_REQUIRED")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if payload.get("law_number") != LAW_NUMBER:
        raise ValueError("GOLDEN294_LIVE_PAYLOAD_IDENTITY_MISMATCH")
    settings = dotenv_values(ROOT / ".env")
    password = str(
        settings.get("OPEN_NOTEBOOK_ADMIN_PASSWORD")
        or settings.get("OPEN_NOTEBOOK_PASSWORD")
        or ""
    ).strip()
    if not password:
        raise RuntimeError("OPEN_NOTEBOOK_ADMIN_PASSWORD_NOT_CONFIGURED")
    base_url = api_url.rstrip("/")
    started_at = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        login = await client.post(
            f"{base_url}/api/auth/login",
            json={"identifier": "admin", "password": password, "role": "admin"},
        )
        if login.status_code == 401:
            login = await client.post(
                f"{base_url}/api/auth/login",
                json={"password": password, "role": "admin"},
            )
        login.raise_for_status()
        login_payload = login.json()
        if login_payload.get("authenticated") is not True or login_payload.get("role") != "admin":
            raise RuntimeError("ADMIN_SESSION_NOT_ESTABLISHED")
        token = login_payload.get("token")
        headers = {"X-User-Role": "admin"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        client.headers.update(headers)

        ready = await client.get(f"{base_url}/ready/import")
        ready.raise_for_status()
        ready_payload = ready.json()
        if ready_payload.get("status") != "ready":
            raise RuntimeError(f"IMPORT_PLANE_NOT_READY:{ready_payload!r}")

        preview = await client.post(f"{base_url}/api/legal/import/preview", json=payload)
        preview.raise_for_status()
        preview_payload = preview.json()
        if preview_payload.get("valid") is not True:
            raise RuntimeError(f"IMPORT_PREVIEW_REJECTED:{preview_payload!r}")

        submit = await client.post(f"{base_url}/api/legal/import", json=payload)
        submit.raise_for_status()
        submit_payload = submit.json()
        candidate_id = str(submit_payload.get("candidate_id") or "").strip()
        if not candidate_id:
            raise RuntimeError("IMPORT_CANDIDATE_ID_MISSING")

        candidate = await _candidate(client, base_url, candidate_id)
        deadline = asyncio.get_running_loop().time() + 15 * 60
        action_history: list[dict[str, Any]] = []
        while True:
            if str(candidate.get("law_number") or "").strip() != LAW_NUMBER:
                raise RuntimeError("IMPORT_CANDIDATE_IDENTITY_MISMATCH")
            action = next_action(
                str(candidate.get("status") or ""),
                str(candidate.get("import_status") or ""),
            )
            action_history.append(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "status": candidate.get("status"),
                    "import_status": candidate.get("import_status"),
                    "action": action,
                }
            )
            if action == "review":
                review = await client.post(
                    f"{base_url}/api/legal/crawl/candidates/{quote(candidate_id, safe=':')}/review",
                    json={
                        "decision": "approved",
                        "review_note": (
                            "Đã đối chiếu nguồn VBPL, identity, hiệu lực và preview "
                            "theo Phase 9 Golden-294 ngày 11/08/2026."
                        ),
                    },
                )
                review.raise_for_status()
                candidate = review.json()["candidate"]
            elif action == "enqueue":
                enqueue = await client.post(
                    f"{base_url}/api/legal/crawl/candidates/{quote(candidate_id, safe=':')}/import"
                )
                enqueue.raise_for_status()
                candidate = await _candidate(client, base_url, candidate_id)
            elif action == "poll":
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("IMPORT_116_TIMEOUT")
                await asyncio.sleep(5)
                candidate = await _candidate(client, base_url, candidate_id)
            elif action == "verify":
                break
            else:
                raise RuntimeError(
                    "IMPORT_116_FAILED_STATE:"
                    f"{candidate.get('status')}:{candidate.get('import_status')}"
                )

    imported = candidate.get("imported_document") or {}
    document_id = str(imported.get("document_id") or "").strip()
    if not document_id or int(imported.get("chunk_count") or 0) < 1:
        raise RuntimeError("IMPORT_116_COMPLETION_EVIDENCE_MISSING")
    async with httpx.AsyncClient(timeout=60) as retrieval:
        detail_response = await retrieval.get(
            f"{retrieval_url.rstrip('/')}/management/documents/{document_id}"
        )
        detail_response.raise_for_status()
        detail = detail_response.json()
        health_response = await retrieval.get(f"{retrieval_url.rstrip('/')}/health")
        health_response.raise_for_status()
        health = health_response.json()
    report = {
        "schema_version": "golden-294-live-import-116-v1",
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "approval_phrase_verified": True,
        "auth_mode": login_payload.get("auth_mode"),
        "actor_user_id": login_payload.get("user_id"),
        "law_number": LAW_NUMBER,
        "candidate_id": candidate_id,
        "candidate_status": candidate.get("status"),
        "import_status": candidate.get("import_status"),
        "document_id": document_id,
        "imported_document": imported,
        "action_history": action_history,
        "retrieval_detail": detail,
        "retrieval_health": {
            key: health.get(key)
            for key in (
                "status",
                "ready",
                "embedding_device",
                "model_fingerprint",
                "collection",
                "indexed_records",
                "database_chunks",
            )
        },
        "credentials_persisted": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:5055")
    parser.add_argument("--retrieval-url", default="http://127.0.0.1:8765")
    parser.add_argument("--approval", required=True)
    args = parser.parse_args()
    report = asyncio.run(
        apply_import(
            payload_path=args.payload.resolve(),
            output_path=args.output.resolve(),
            api_url=args.api_url,
            retrieval_url=args.retrieval_url,
            approval=args.approval,
        )
    )
    print(
        json.dumps(
            {
                "status": report["candidate_status"],
                "import_status": report["import_status"],
                "document_id": report["document_id"],
                "article_count": report["imported_document"].get("article_count"),
                "chunk_count": report["imported_document"].get("chunk_count"),
                "indexed_records": report["retrieval_health"].get("indexed_records"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
