"""Optional local cross-encoder reranker with deterministic fail-degraded behavior.

The adapter never downloads a model.  It can only load an existing local path,
is bounded to a small candidate window, and preserves the caller's heuristic
order whenever loading or inference fails.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Sequence

PairScorer = Callable[[list[tuple[str, str]]], Sequence[float]]
_TRUE = {"1", "true", "yes", "on"}
# The Vietnamese model is deliberately opt-in.  The adapter remains local-only
# and never downloads a mutable Hugging Face revision at request time.
VIETNAMESE_RERANKER_MODEL_ID = "AITeamVN/Vietnamese_Reranker"
VIETNAMESE_RERANKER_MODEL_LABEL = "vietnamese-reranker"


def _safe_label(value: str | None, fallback: str) -> str:
    text = str(value or "").strip().replace("\\", "/").split("/")[-1]
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", text).strip("-.")
    return text[:80] or fallback


def _passage(candidate: Mapping[str, Any]) -> str:
    return "\n".join(
        str(candidate.get(field) or "").strip()
        for field in (
            "document_title",
            "law_number",
            "article_number",
            "article_title",
            "chunk_heading",
            "content",
        )
        if str(candidate.get(field) or "").strip()
    )[:12000]


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-min(value, 60.0)))
    exp_value = math.exp(max(value, -60.0))
    return exp_value / (1.0 + exp_value)


def _load_pinned_custom_sequence_classifier(
    *, code_path: Path, model_path: Path
) -> Any:
    """Load a local, checksum-verified Transformers implementation package.

    Some Hugging Face models point ``auto_map`` at a second repository.  The
    benchmark must not resolve that repository's mutable ``main`` branch at
    runtime, so the caller verifies a pinned local snapshot and this helper
    imports only its local ``configuration.py`` and ``modeling.py`` files.
    """

    configuration_path = code_path / "configuration.py"
    modeling_path = code_path / "modeling.py"
    if not configuration_path.is_file() or not modeling_path.is_file():
        raise FileNotFoundError("pinned_custom_code_missing")
    package_name = "_legal_reranker_impl_" + hashlib.sha256(
        str(code_path.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(code_path.resolve())]
        package.__package__ = package_name
        sys.modules[package_name] = package

    def load_module(module_name: str, path: Path) -> Any:
        qualified_name = f"{package_name}.{module_name}"
        existing = sys.modules.get(qualified_name)
        if existing is not None:
            return existing
        spec = importlib.util.spec_from_file_location(qualified_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"pinned_custom_code_import_failed:{module_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        spec.loader.exec_module(module)
        return module

    configuration = load_module("configuration", configuration_path)
    modeling = load_module("modeling", modeling_path)
    config = configuration.NewConfig.from_pretrained(
        model_path, local_files_only=True
    )
    model = modeling.NewForSequenceClassification.from_pretrained(
        model_path,
        config=config,
        local_files_only=True,
    )
    # Transformers 5 initializes non-persistent buffers on ``meta`` while
    # loading this legacy custom architecture. They are intentionally absent
    # from the checkpoint and otherwise retain invalid values. Rebuild only
    # those deterministic buffers from the pinned config (never model weights).
    embeddings = model.new.embeddings
    import torch

    embeddings.register_buffer(
        "position_ids",
        torch.arange(config.max_position_embeddings),
        persistent=False,
    )
    embeddings._init_rope(config)
    return model


@dataclass(frozen=True)
class RerankOutcome:
    candidates: list[dict[str, Any]]
    mode: str
    reason_code: str | None
    version: str
    degraded: bool
    latency_ms: float
    candidate_count: int
    scored_count: int
    device: str = "unloaded"
    max_length: int = 512
    batch_size: int = 1

    def public_status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason_code": self.reason_code,
            "version": self.version,
            "degraded": self.degraded,
            "candidate_count": self.candidate_count,
            "scored_count": self.scored_count,
            "latency_ms": round(self.latency_ms, 3),
            "device": self.device,
            "max_length": self.max_length,
            "batch_size": self.batch_size,
        }


class OptionalCrossEncoderReranker:
    """Lazy local-only cross encoder; deterministic heuristic fallback."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        model_path: str | Path | None = None,
        scorer: PairScorer | None = None,
        model_label: str | None = None,
        max_candidates: int = 40,
        batch_size: int = 8,
        max_length: int = 512,
        learned_weight: float = 1.0,
        custom_code_path: str | Path | None = None,
    ) -> None:
        self.enabled = (
            str(os.getenv("LEGAL_LEARNED_RERANKER_ENABLED", "false")).strip().casefold()
            in _TRUE
            if enabled is None
            else bool(enabled)
        )
        configured_path = model_path or os.getenv("LEGAL_RERANKER_MODEL_PATH", "")
        self.model_path = Path(configured_path) if configured_path else None
        configured_code_path = custom_code_path or os.getenv(
            "LEGAL_RERANKER_CUSTOM_CODE_PATH", ""
        )
        self.custom_code_path = (
            Path(configured_code_path) if configured_code_path else None
        )
        # M6 benchmarks the bounded window at 20/30/50/100. Keep the adapter
        # hard-capped at 100 even when configuration is malformed or larger.
        self.max_candidates = min(max(1, int(max_candidates)), 100)
        self.batch_size = min(max(1, int(batch_size)), 32)
        self.max_length = min(max(128, int(max_length)), 1024)
        self.learned_weight = max(0.0, min(float(learned_weight), 2.0))
        self.model_label = _safe_label(
            model_label or (str(self.model_path) if self.model_path else ""),
            "bge-reranker-v2-m3",
        )
        self._scorer = scorer
        self._load_attempted = scorer is not None
        self._load_reason: str | None = None
        self._device = "injected" if scorer is not None else "unloaded"
        self._load_lock = Lock()
        fingerprint_seed = (
            f"{self.model_label}|{self.model_path or ''}|window={self.max_candidates}"
            f"|batch={self.batch_size}|max_length={self.max_length}"
            f"|weight={self.learned_weight}"
            f"|custom_code={self.custom_code_path or ''}"
        )
        self._fingerprint = hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest()

    @classmethod
    def from_environment(cls) -> "OptionalCrossEncoderReranker":
        return cls(
            max_candidates=int(os.getenv("LEGAL_RERANKER_WINDOW", "40")),
            batch_size=int(os.getenv("LEGAL_RERANKER_BATCH_SIZE", "8")),
            max_length=int(os.getenv("LEGAL_RERANKER_MAX_LENGTH", "512")),
            learned_weight=float(os.getenv("LEGAL_RERANKER_WEIGHT", "1.0")),
            model_label=os.getenv("LEGAL_RERANKER_MODEL_LABEL") or None,
        )

    @property
    def version(self) -> str:
        if self._scorer is None:
            return "heuristic-v1"
        return f"cross-encoder:{self._fingerprint[:12]}"

    def _ensure_scorer(self) -> PairScorer | None:
        if self._scorer is not None:
            return self._scorer
        if self._load_attempted:
            return None
        with self._load_lock:
            if self._scorer is not None:
                return self._scorer
            if self._load_attempted:
                return None
            self._load_attempted = True
            if self.model_path is None or not self.model_path.is_dir():
                self._load_reason = "model_path_missing"
                return None
            try:
                import torch
                from transformers import (
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                requested = os.getenv("LEGAL_RERANKER_DEVICE", "auto").strip().lower()
                if requested not in {"auto", "cpu", "cuda"}:
                    self._load_reason = "invalid_device_config"
                    return None
                if requested == "cuda" and not torch.cuda.is_available():
                    self._load_reason = "cuda_unavailable"
                    return None
                cuda_usable = False
                if requested != "cpu" and torch.cuda.is_available():
                    free_bytes, _ = torch.cuda.mem_get_info()
                    minimum_free_mb = max(
                        512,
                        int(os.getenv("LEGAL_RERANKER_MIN_CUDA_FREE_MB", "2800")),
                    )
                    cuda_usable = free_bytes >= minimum_free_mb * 1024 * 1024
                    if requested == "cuda" and not cuda_usable:
                        self._load_reason = "cuda_memory_insufficient"
                        return None
                device = "cuda" if cuda_usable else "cpu"
                tokenizer = AutoTokenizer.from_pretrained(
                    self.model_path,
                    local_files_only=True,
                    trust_remote_code=False,
                )
                if self.custom_code_path is not None:
                    model = _load_pinned_custom_sequence_classifier(
                        code_path=self.custom_code_path,
                        model_path=self.model_path,
                    )
                else:
                    model = AutoModelForSequenceClassification.from_pretrained(
                        self.model_path,
                        local_files_only=True,
                        trust_remote_code=False,
                    )
                if device == "cuda":
                    model.half()
                model.to(device)
                model.eval()

                def score_pairs(pairs: list[tuple[str, str]]) -> list[float]:
                    encoded = tokenizer(
                        [item[0] for item in pairs],
                        [item[1] for item in pairs],
                        padding=True,
                        truncation=True,
                        max_length=self.max_length,
                        return_tensors="pt",
                    )
                    encoded = {key: value.to(device) for key, value in encoded.items()}
                    with torch.inference_mode():
                        logits = model(**encoded).logits
                    if logits.ndim > 1 and logits.shape[-1] > 1:
                        logits = logits[..., -1]
                    return [float(value) for value in logits.reshape(-1).cpu().tolist()]

                self._scorer = score_pairs
                self._device = device
            except (ImportError, ModuleNotFoundError):
                self._load_reason = "dependency_unavailable"
            except Exception:
                self._load_reason = "model_load_failed"
            return self._scorer

    def _heuristic_outcome(
        self,
        candidates: list[dict[str, Any]],
        *,
        started: float,
        reason_code: str,
    ) -> RerankOutcome:
        return RerankOutcome(
            candidates=candidates,
            mode="heuristic",
            reason_code=reason_code,
            version="heuristic-v1",
            degraded=True,
            latency_ms=(time.perf_counter() - started) * 1000,
            candidate_count=len(candidates),
            scored_count=0,
            device=self._device,
            max_length=self.max_length,
            batch_size=self.batch_size,
        )

    def rerank(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        top_n: int | None = None,
    ) -> RerankOutcome:
        started = time.perf_counter()
        ordered = sorted(
            (dict(item) for item in candidates),
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("chunk_id") or ""),
            ),
        )
        if not ordered:
            return RerankOutcome(
                [],
                "heuristic",
                None,
                "heuristic-v1",
                False,
                0.0,
                0,
                0,
                self._device,
                self.max_length,
                self.batch_size,
            )
        if not self.enabled:
            return self._heuristic_outcome(
                ordered, started=started, reason_code="disabled_by_config"
            )
        scorer = self._ensure_scorer()
        if scorer is None:
            return self._heuristic_outcome(
                ordered,
                started=started,
                reason_code=self._load_reason or "model_unavailable",
            )

        requested_top_n = self.max_candidates if top_n is None else int(top_n)
        window_size = min(max(1, requested_top_n), self.max_candidates, 100)
        window = ordered[:window_size]
        pairs = [(str(query or ""), _passage(item)) for item in window]
        raw_scores: list[float] = []
        try:
            for offset in range(0, len(pairs), self.batch_size):
                batch = pairs[offset : offset + self.batch_size]
                values = list(scorer(batch))
                if len(values) != len(batch) or not all(
                    math.isfinite(float(value)) for value in values
                ):
                    raise ValueError("invalid_score_shape")
                raw_scores.extend(float(value) for value in values)
        except Exception as exc:
            reason_code = "inference_failed"
            if isinstance(exc, MemoryError) or "out of memory" in str(exc).casefold():
                reason_code = "inference_oom"
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except (ImportError, RuntimeError):
                    pass
            return self._heuristic_outcome(
                ordered, started=started, reason_code=reason_code
            )

        rescored: list[dict[str, Any]] = []
        for original_rank, (item, raw_score) in enumerate(
            zip(window, raw_scores, strict=True)
        ):
            normalized = _sigmoid(raw_score)
            base_score = float(item.get("score") or 0.0)
            rescored.append(
                {
                    **item,
                    "pre_rerank_score": round(base_score, 6),
                    "learned_rerank_score": round(normalized, 6),
                    "rerank_score": round(raw_score, 6),
                    "score": round(base_score + self.learned_weight * normalized, 6),
                    "reranker_version": self.version,
                    "_stable_pre_rerank_rank": original_rank,
                }
            )
        rescored.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                int(item.get("_stable_pre_rerank_rank") or 0),
                str(item.get("chunk_id") or ""),
            )
        )
        for item in rescored:
            item.pop("_stable_pre_rerank_rank", None)
        rescored.extend(ordered[window_size:])
        return RerankOutcome(
            candidates=rescored,
            mode="learned",
            reason_code=None,
            version=self.version,
            degraded=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            candidate_count=len(ordered),
            scored_count=len(window),
            device=self._device,
            max_length=self.max_length,
            batch_size=self.batch_size,
        )
