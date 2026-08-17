"""Small process-local circuit breaker for structured answer providers.

The legal pipeline always prepares a deterministic, evidence-validated answer
before calling a language model.  Repeatedly waiting for a provider that has
already timed out or returned a rate limit only increases latency; it does not
improve legal grounding.  This module keeps that operational decision separate
from evidence selection and never changes citations, validity, or hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from threading import Lock
import time
from typing import Mapping


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    reason: str | None = None


class StructuredProviderCircuit:
    """Block repeated calls to one failing model for a bounded cooldown."""

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 120.0,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._states: dict[str, _CircuitState] = {}
        self._lock = Lock()

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "StructuredProviderCircuit":
        values = os.environ if environ is None else environ
        try:
            threshold = int(
                values.get("LEGAL_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "2")
            )
        except (TypeError, ValueError):
            threshold = 2
        try:
            cooldown = float(
                values.get("LEGAL_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "120")
            )
        except (TypeError, ValueError):
            cooldown = 120.0
        return cls(
            failure_threshold=threshold,
            cooldown_seconds=cooldown,
        )

    def allow(self, model_id: str, *, now: float | None = None) -> bool:
        key = str(model_id or "").strip() or "default"
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            state = self._states.get(key)
            if state is None or state.opened_at is None:
                return True
            if current - state.opened_at < self.cooldown_seconds:
                return False
            # Half-open: allow one new request after the cooldown. Resetting
            # here means its success closes the circuit and its failure opens
            # a fresh cooldown window through record_failure().
            self._states[key] = _CircuitState()
            return True

    def record_failure(
        self,
        model_id: str,
        reason: str,
        *,
        now: float | None = None,
    ) -> None:
        key = str(model_id or "").strip() or "default"
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.failures += 1
            state.reason = str(reason or "provider_failure")
            if state.failures >= self.failure_threshold:
                state.opened_at = current

    def record_success(self, model_id: str) -> None:
        key = str(model_id or "").strip() or "default"
        with self._lock:
            self._states.pop(key, None)

    def reason(self, model_id: str) -> str | None:
        key = str(model_id or "").strip() or "default"
        with self._lock:
            state = self._states.get(key)
            return state.reason if state and state.opened_at is not None else None

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


class StructuredProviderCircuitOpen(RuntimeError):
    """Internal control-flow signal; never rendered as a legal conclusion."""
