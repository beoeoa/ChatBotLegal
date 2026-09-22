from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from concurrent.futures import Future
from queue import Empty, Queue
from threading import Event, Lock, Semaphore, Thread
from time import perf_counter

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer


QUERY_PREFIX = (
    "Instruct: Given a Vietnamese legal question, retrieve relevant legal "
    "passages that answer the question\nQuery: "
)
QUERY_MAX_LENGTH = 512


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# This is the immutable recipe recorded by the v6r20 release manifest.  The
# manifest builder encoded ``\\n`` literally, while the Kaggle worker executes
# a real newline in QUERY_PREFIX.  Keep both facts explicit until a new vector
# release can correct the attestation without rewriting the active release.
EMBEDDING_RECIPE_FINGERPRINT = _canonical_sha256(
    {
        "model": "VNLegal-LAL",
        "query_instruction": (
            "Instruct: Given a Vietnamese legal question, retrieve relevant legal "
            "passages that answer the question\\nQuery: "
        ),
        "pooling": "last_token",
        "normalize": "l2",
        "dtype": "float32_persisted",
        "dimension": 1024,
    }
)


def _model_revision_fingerprint(model_path: Path) -> str:
    """Match the artifact fingerprint used by the pinned Kaggle worker."""

    digest = hashlib.sha256()
    files = sorted(
        {
            path
            for pattern in ("*.json", "*.safetensors", "*.bin", "*.model", "*.txt")
            for path in model_path.rglob(pattern)
            if path.is_file()
        },
        key=lambda path: path.relative_to(model_path).as_posix(),
    )
    if not files:
        digest.update(b"model-artifacts-missing")
    for path in files:
        relative = path.relative_to(model_path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


class _EncodeBatchRequest:
    __slots__ = ("query", "future")

    def __init__(self, query: str) -> None:
        self.query = query
        self.future: Future = Future()


class _EncodeBatcher:
    """Opt-in micro-batcher for immutable query-encoder inference."""

    def __init__(self, owner: "VNLegalEncoder", *, window_ms: float, max_batch: int, timeout_seconds: float) -> None:
        self.owner = owner
        self.window_seconds = max(0.0, float(window_ms)) / 1000.0
        self.max_batch = max(1, int(max_batch))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._queue: Queue[_EncodeBatchRequest | None] = Queue()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="legal-encoder-batcher", daemon=True)
        self._thread.start()
        self._stats_lock = Lock()
        self._submitted = 0
        self._batches = 0
        self._max_observed_batch = 0
        self._errors = 0

    @classmethod
    def from_environment(cls, owner: "VNLegalEncoder") -> "_EncodeBatcher | None":
        enabled = str(
            os.getenv("LEGAL_EMBED_BATCHING") or "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        if not enabled:
            return None
        return cls(
            owner,
            window_ms=float(os.getenv("LEGAL_EMBED_BATCH_WINDOW_MS") or 5),
            max_batch=int(os.getenv("LEGAL_EMBED_BATCH_MAX") or 32),
            timeout_seconds=float(os.getenv("LEGAL_EMBED_BATCH_TIMEOUT_SECONDS") or 60),
        )

    def submit(self, query: str) -> np.ndarray:
        request = _EncodeBatchRequest(query)
        with self._stats_lock:
            self._submitted += 1
        self._queue.put(request)
        return request.future.result(timeout=self.timeout_seconds)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if first is None:
                break
            batch = [first]
            deadline = perf_counter() + self.window_seconds
            while len(batch) < self.max_batch:
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except Empty:
                    break
                if item is None:
                    self._stop.set()
                    break
                batch.append(item)
            with self._stats_lock:
                self._batches += 1
                self._max_observed_batch = max(self._max_observed_batch, len(batch))
            try:
                vectors = self.owner._encode_queries([item.query for item in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError("encoder_batch_result_count_mismatch")
                for item, vector in zip(batch, vectors):
                    item.future.set_result(vector)
            except Exception as exc:
                with self._stats_lock:
                    self._errors += len(batch)
                for item in batch:
                    item.future.set_exception(exc)

    def stats(self) -> dict[str, int | float | bool]:
        with self._stats_lock:
            return {
                "enabled": True,
                "window_ms": round(self.window_seconds * 1000.0, 3),
                "max_batch": self.max_batch,
                "submitted": self._submitted,
                "batches": self._batches,
                "max_observed_batch": self._max_observed_batch,
                "errors": self._errors,
            }

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

class VNLegalEncoder:
    def __init__(self, model_path: str):
        requested = str(os.getenv("LEGAL_EMBED_DEVICE") or "auto").strip().casefold()
        if requested not in {"auto", "cuda", "cpu"}:
            raise ValueError("LEGAL_EMBED_DEVICE must be one of: auto, cuda, cpu")
        cuda_available = bool(torch.cuda.is_available())
        if requested == "cuda" and not cuda_available:
            raise RuntimeError("LEGAL_EMBED_DEVICE=cuda but CUDA is unavailable")
        self.requested_device = requested
        self.fallback_reason = None
        # The historical runtime serialized every encode behind one lock.
        # Keep that safe default, but allow an explicitly bounded staging
        # concurrency so a burst of independent queries does not queue for a
        # full model forward pass.  A semaphore is used instead of removing
        # synchronization entirely; the model remains read-only and the
        # operator chooses the concurrency budget per host.
        configured_threads = int(os.getenv("LEGAL_EMBED_TORCH_THREADS") or 0)
        if configured_threads > 0:
            torch.set_num_threads(configured_threads)
        configured_concurrency = int(
            os.getenv("LEGAL_EMBED_MAX_CONCURRENCY") or 1
        )
        self.encode_concurrency = max(1, configured_concurrency)
        active = "cuda" if requested == "cuda" or (requested == "auto" and cuda_available) else "cpu"
        self.device = torch.device(active)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.dtype = torch.float16 if self.device.type == "cuda" else torch.float32
        try:
            self.model = AutoModel.from_pretrained(
                model_path, torch_dtype=self.dtype, local_files_only=True
            )
            self.model.to(self.device)
        except RuntimeError as exc:
            if requested != "auto" or self.device.type != "cuda" or "out of memory" not in str(exc).casefold():
                raise
            self.fallback_reason = "cuda_oom_during_model_load"
            torch.cuda.empty_cache()
            self.device = torch.device("cpu")
            self.dtype = torch.float32
            self.model = AutoModel.from_pretrained(
                model_path, torch_dtype=self.dtype, local_files_only=True
            )
            self.model.to(self.device)
        self.model.eval()
        resolved_model_path = Path(model_path).resolve()
        self.model_path = str(resolved_model_path)
        configured_fingerprint = str(os.getenv("VNLEGAL_LAL_MODEL_FINGERPRINT") or "").strip()
        self.model_fingerprint = configured_fingerprint or _model_revision_fingerprint(
            resolved_model_path
        )
        self.embedding_recipe_fingerprint = EMBEDDING_RECIPE_FINGERPRINT
        self._encode_lock = Lock()
        self._encode_gate = Semaphore(self.encode_concurrency)
        self._encode_batcher = _EncodeBatcher.from_environment(self)
        self._warmup_ms = None

    @property
    def device_name(self) -> str:
        return str(self.device)

    @property
    def dtype_name(self) -> str:
        return str(self.dtype).replace("torch.", "")

    def _encode_queries(self, queries: list[str]) -> list[np.ndarray]:
        # Lightweight parity probes construct the encoder shell without
        # running ``__init__``; retain the old lock as a compatibility
        # fallback for those probes and downstream test doubles.
        gate = getattr(self, "_encode_gate", self._encode_lock)
        with gate:
            encoded_input = self.tokenizer(
                [QUERY_PREFIX + query.strip() for query in queries],
                padding=True,
                truncation=True,
                max_length=QUERY_MAX_LENGTH,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                outputs = self.model(**encoded_input)
                last = encoded_input["attention_mask"].sum(dim=1) - 1
                sentence_embeddings = outputs.last_hidden_state[
                    torch.arange(len(encoded_input["input_ids"]), device=self.device),
                    last,
                ]
                sentence_embeddings = F.normalize(sentence_embeddings, p=2, dim=1)
            return [
                row.detach().cpu().to(torch.float32).numpy()
                for row in sentence_embeddings
            ]

    def encode_query(self, query: str) -> np.ndarray:
        batcher = getattr(self, "_encode_batcher", None)
        if batcher is not None:
            return batcher.submit(query)
        return self._encode_queries([query])[0]

    def warmup(self) -> float:
        started = perf_counter()
        self.encode_query("Truy vấn kiểm tra khởi động hệ thống.")
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        self._warmup_ms = round((perf_counter() - started) * 1000, 3)
        return self._warmup_ms

    def close(self) -> None:
        batcher = getattr(self, "_encode_batcher", None)
        if batcher is not None:
            batcher.close()
            self._encode_batcher = None

    def stats(self) -> dict[str, str | float | None]:
        return {
            "requested_device": self.requested_device,
            "active_device": self.device_name,
            "dtype": self.dtype_name,
            "model_fingerprint": self.model_fingerprint,
            "embedding_recipe_fingerprint": self.embedding_recipe_fingerprint,
            "warmup_ms": self._warmup_ms,
            "fallback_reason": self.fallback_reason,
            "encode_concurrency": getattr(self, "encode_concurrency", 1),
            "torch_num_threads": torch.get_num_threads(),
            "batching": (
                self._encode_batcher.stats()
                if getattr(self, "_encode_batcher", None) is not None
                else {"enabled": False}
            ),
        }

def load_encoder(model_path: str):
    return VNLegalEncoder(model_path)
