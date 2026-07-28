from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from .evidence import ClaimBasis
from .screen.signals import HIGH
from .screen.verdict import NONE_FOUND_IN_SCANNED_FILES


class Feature(str, Enum):
    COMMIT_CADENCE_30D = "commit_cadence_30d"
    CONTRIBUTOR_COUNT = "contributor_count"
    HAS_LICENSE = "has_license"


class RecommendationStatus(str, Enum):
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"
    REVIEW = "review"
    NOT_PRIORITY = "not-priority"


ALL_FEATURES: Final = tuple(Feature)
WEIGHT_VERSION: Final = "m0-contributions-v1"

# Provenance: docs/M0-VERDICT.md, “Contributions and v0.2 scope” table.
# These are the measured AUC decreases, not invented product weights.
WEIGHTS: Final = (
    (Feature.COMMIT_CADENCE_30D, Decimal("0.093318")),
    (Feature.CONTRIBUTOR_COUNT, Decimal("0.016129")),
    (Feature.HAS_LICENSE, Decimal("0.009649")),
)


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ScoreInputs:
    commit_cadence_30d: bool | None
    contributor_count: bool | None
    has_license: bool | None


class ScoreVersionMismatch(TypeError):
    def __init__(self, left: str, right: str) -> None:
        self.left = left
        self.right = right
        super().__init__(
            f"scores from weight sets {left!r} and {right!r} cannot be ordered"
        )


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class Score:
    value: Decimal
    version: str
    coverage: frozenset[Feature]

    @property
    def basis(self) -> ClaimBasis:
        return ClaimBasis.DETERMINISTIC if self.coverage else ClaimBasis.ABSENT

    @property
    def complete(self) -> bool:
        return self.coverage == frozenset(ALL_FEATURES)

    @property
    def incomplete_because(self) -> tuple[str, ...]:
        return tuple(
            f"{feature.value} unavailable"
            for feature in ALL_FEATURES
            if feature not in self.coverage
        )

    def __lt__(self, other: Score) -> bool:
        if self.version != other.version:
            raise ScoreVersionMismatch(self.version, other.version)
        return self.value < other.value


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class Recommendation:
    score: Score
    risk_verdict: str

    @property
    def status(self) -> RecommendationStatus:
        if self.risk_verdict == HIGH:
            return RecommendationStatus.BLOCKED
        if (
            self.score.basis is ClaimBasis.ABSENT
            or not self.score.complete
            or self.risk_verdict in {"unknown", NONE_FOUND_IN_SCANNED_FILES}
        ):
            return RecommendationStatus.INSUFFICIENT_EVIDENCE
        if self.score.value > 0:
            return RecommendationStatus.REVIEW
        return RecommendationStatus.NOT_PRIORITY


def score(features: ScoreInputs) -> Score:
    observations = (
        (Feature.COMMIT_CADENCE_30D, features.commit_cadence_30d),
        (Feature.CONTRIBUTOR_COUNT, features.contributor_count),
        (Feature.HAS_LICENSE, features.has_license),
    )
    enabled = frozenset(feature for feature, value in observations if value is True)
    coverage = frozenset(
        feature for feature, value in observations if value is not None
    )
    value = sum(
        (weight for feature, weight in WEIGHTS if feature in enabled),
        start=Decimal(),
    )
    return Score(value=value, version=WEIGHT_VERSION, coverage=coverage)
