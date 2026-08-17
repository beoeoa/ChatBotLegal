"""Typed, fail-closed evidence evaluation for production Go/No-Go."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator


REQUIRED_PRODUCTION_GATES: tuple[str, ...] = (
    "capability",
    "backend",
    "frontend",
    "legal_regression",
    "golden_294",
    "golden_v3_1000",
    "deepseek_live_1000",
    "browser_uat",
    "load",
    "security",
    "backup",
    "restore",
    "rollback",
    "smoke",
)


class ReleaseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    gate: str = Field(min_length=1)
    status: Literal["passed", "failed"]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    release_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: datetime
    command_or_scenario: str = Field(min_length=1)
    metrics: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("gate")
    @classmethod
    def _known_gate(cls, value: str) -> str:
        if value not in REQUIRED_PRODUCTION_GATES:
            raise ValueError("unknown production gate")
        return value


class GoNoGoResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["GO", "NO-GO"]
    release_id: str
    release_fingerprint: str
    required_gates: tuple[str, ...]
    passed_gates: tuple[str, ...]
    missing_gates: tuple[str, ...]
    failed_gates: tuple[str, ...]
    rejected_evidence_count: int = 0


def evaluate_release_evidence(
    evidence: Sequence[ReleaseEvidence],
    *,
    release_id: str,
    release_fingerprint: str,
    required_gates: Sequence[str] = REQUIRED_PRODUCTION_GATES,
) -> GoNoGoResult:
    required = tuple(dict.fromkeys(required_gates))
    matching: dict[str, list[ReleaseEvidence]] = defaultdict(list)
    rejected = 0
    for item in evidence:
        if (
            item.release_id != release_id
            or item.release_fingerprint != release_fingerprint
        ):
            rejected += 1
            continue
        matching[item.gate].append(item)

    passed: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    for gate in required:
        gate_evidence = matching.get(gate, [])
        if not gate_evidence:
            missing.append(gate)
        elif any(item.status == "failed" for item in gate_evidence):
            failed.append(gate)
        elif any(item.status == "passed" for item in gate_evidence):
            passed.append(gate)
        else:
            missing.append(gate)

    decision: Literal["GO", "NO-GO"] = (
        "GO" if not missing and not failed and not rejected else "NO-GO"
    )
    return GoNoGoResult(
        decision=decision,
        release_id=release_id,
        release_fingerprint=release_fingerprint,
        required_gates=required,
        passed_gates=tuple(passed),
        missing_gates=tuple(missing),
        failed_gates=tuple(failed),
        rejected_evidence_count=rejected,
    )

