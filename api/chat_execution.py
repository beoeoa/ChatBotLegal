"""Request lifetime shared by routing, providers and answer delivery."""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnExecution:
    request_id: str
    budget: float
    started: float = field(default_factory=time.perf_counter)
    resolved_models: dict[str, Any] = field(default_factory=dict, repr=False)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    first_visible_ms: float | None = None

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def remaining(self, limit: float | None = None) -> float:
        remaining = max(0.0, self.started + self.budget - time.perf_counter())
        return remaining if limit is None else min(remaining, limit)


current_turn: ContextVar[TurnExecution | None] = ContextVar("chat_turn", default=None)


def remaining_timeout(timeout: float) -> float:
    turn = current_turn.get()
    return turn.remaining(timeout) if turn else timeout
