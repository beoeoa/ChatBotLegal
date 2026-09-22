"""Keep Render's private retrieval service reachable while its reviewed seed is copied.

The bootstrap listener only returns 503. Once the operator has transferred the
core release and created ``/data/legal/.render-seed-complete``, the process
verifies the release, prepares a writable Chroma copy, and starts retrieval.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import os
from pathlib import Path
import shutil
import signal
import threading

from scripts.verify_core_288_release import verify_core_release


RELEASE_ROOT = Path(os.getenv("LEGAL_RENDER_RELEASE_ROOT", "/data"))
LEGAL_ROOT = RELEASE_ROOT / "legal"
MARKER = LEGAL_ROOT / ".render-seed-complete"
SOURCE_CHROMA = LEGAL_ROOT / "chroma_core_288_release_20260919"
RUNTIME_CHROMA = LEGAL_ROOT / "chroma_store"
PORT = int(os.getenv("PORT", "8765"))


def prepare_seed() -> None:
    if not MARKER.is_file():
        raise FileNotFoundError("reviewed_seed_transfer_incomplete")
    receipt = verify_core_release(RELEASE_ROOT, verify_postgres_dump=False)
    if receipt["status"] != "PASS":
        raise RuntimeError("reviewed_seed_verification_failed")
    model_root = LEGAL_ROOT / "vnlegal-lal-model"
    if not model_root.is_dir():
        raise FileNotFoundError("reviewed_embedding_model_missing")

    if not RUNTIME_CHROMA.exists():
        temporary = LEGAL_ROOT / ".render-chroma-copying"
        if temporary.exists():
            raise RuntimeError("incomplete_chroma_copy_requires_operator_review")
        shutil.copytree(SOURCE_CHROMA, temporary)
        temporary.rename(RUNTIME_CHROMA)
    if not (RUNTIME_CHROMA / "chroma.sqlite3").is_file():
        raise RuntimeError("runtime_chroma_sqlite_missing")
    logging.info("Verified legal release %s", receipt["release_id"])


class NotReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"status":"not_ready","reason":"reviewed_seed_required"}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        prepare_seed()
    except Exception as exc:
        logging.warning("Retrieval bootstrap: %s", str(exc))
    else:
        os.execvp(
            "uvicorn",
            ["uvicorn", "scripts.legal_search_server:app", "--host", "0.0.0.0", "--port", str(PORT)],
        )

    server = ThreadingHTTPServer(("0.0.0.0", PORT), NotReadyHandler)
    server.timeout = 3
    stopped = threading.Event()

    def stop(_signal: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped.is_set():
        server.handle_request()
        if not MARKER.is_file():
            continue
        try:
            prepare_seed()
        except Exception as exc:
            logging.warning("Retrieval seed still invalid: %s", str(exc))
            stopped.wait(30)
            continue
        server.server_close()
        os.execvp(
            "uvicorn",
            ["uvicorn", "scripts.legal_search_server:app", "--host", "0.0.0.0", "--port", str(PORT)],
        )
    server.server_close()


if __name__ == "__main__":
    main()
