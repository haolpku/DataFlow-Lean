"""Serializable contracts shared by the M2F operators."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class Budget:
    planning_rounds: int = 21
    executor_attempts: int = 10
    statement_repairs: int = 10
    timeout_seconds: int = 600


@dataclasses.dataclass(frozen=True, order=True)
class Objective:
    """Lexicographic VeriRefine objective; lower is always better."""

    global_errors: int
    local_errors_or_holes: int


@dataclasses.dataclass
class Verification:
    success: bool
    exit_code: int | None
    global_errors: int
    holes: int
    output: str
    seconds: float
    forbidden: list[str] = dataclasses.field(default_factory=list)

    @property
    def proof_objective(self) -> Objective:
        return Objective(self.global_errors, self.holes)

    def asdict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)

