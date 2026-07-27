"""What a grader returns, and what it must say about itself."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..evidence import ClaimBasis


@dataclass(frozen=True)
class GradeResult:
    """One model's opinion, with the provenance needed to reproduce it.

    `model`, `temperature` and `prompt_version` are not decoration. A score
    without them cannot be re-derived six months later, and a grading pipeline
    whose outputs cannot be re-derived is a pipeline whose regressions cannot be
    found.
    """

    idea: int
    skill: int
    description: str
    model: str
    temperature: float
    prompt_version: str
    basis: ClaimBasis = ClaimBasis.MODEL

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis", ClaimBasis(self.basis))
        for name, value in (("idea", self.idea), ("skill", self.skill)):
            if not 1 <= value <= 10:
                raise ValueError(f"GradeResult.{name} must be 1..10, got {value}")
        if not self.model:
            raise ValueError("GradeResult.model is required — a score without a model is not reproducible")
        if not self.prompt_version:
            raise ValueError("GradeResult.prompt_version is required")


class GradeClient(Protocol):
    """The seam a fake slots into, so the smoke test needs no network."""

    def evaluate(self, digest: str) -> GradeResult: ...

    def flags_malicious(self, digest: str) -> bool: ...
