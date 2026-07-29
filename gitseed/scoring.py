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


@dataclass(frozen=True, init=False)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ScoreInputs:
    commit_count_30d: int | None
    contributor_total: int | None
    license: dict[str, object] | None
    commit_count_basis: ClaimBasis
    contributor_count_basis: ClaimBasis
    license_basis: ClaimBasis
    _legacy_commit_cadence_30d: bool | None
    _legacy_contributor_count: bool | None
    _legacy_has_license: bool | None

    def __init__(
        self,
        commit_cadence_30d: bool | None,
        contributor_count: bool | None,
        has_license: bool | None,
    ) -> None:
        """Keep the prior boolean construction form for fixture and artifact compatibility."""
        object.__setattr__(self, "commit_count_30d", None)
        object.__setattr__(self, "contributor_total", None)
        object.__setattr__(self, "license", None)
        object.__setattr__(self, "commit_count_basis", ClaimBasis.ABSENT)
        object.__setattr__(self, "contributor_count_basis", ClaimBasis.ABSENT)
        object.__setattr__(self, "license_basis", ClaimBasis.ABSENT)
        object.__setattr__(self, "_legacy_commit_cadence_30d", commit_cadence_30d)
        object.__setattr__(self, "_legacy_contributor_count", contributor_count)
        object.__setattr__(self, "_legacy_has_license", has_license)

    @classmethod
    def observed(
        cls,
        commit_count_30d: int | None,
        contributor_count: int | None,
        license: dict[str, object] | None,
        *,
        commit_count_basis: ClaimBasis | None = None,
        contributor_count_basis: ClaimBasis | None = None,
        license_basis: ClaimBasis | None = None,
    ) -> ScoreInputs:
        value = cls(None, None, None)
        object.__setattr__(value, "commit_count_30d", commit_count_30d)
        object.__setattr__(value, "contributor_total", contributor_count)
        object.__setattr__(value, "license", license)
        object.__setattr__(
            value,
            "commit_count_basis",
            ClaimBasis.DETERMINISTIC if commit_count_basis is None and commit_count_30d is not None else ClaimBasis.ABSENT if commit_count_basis is None else ClaimBasis(commit_count_basis),
        )
        object.__setattr__(
            value,
            "contributor_count_basis",
            ClaimBasis.DETERMINISTIC if contributor_count_basis is None and contributor_count is not None else ClaimBasis.ABSENT if contributor_count_basis is None else ClaimBasis(contributor_count_basis),
        )
        object.__setattr__(
            value,
            "license_basis",
            ClaimBasis.DETERMINISTIC if license_basis is None and license is not None else ClaimBasis.ABSENT if license_basis is None else ClaimBasis(license_basis),
        )
        return value

    @property
    def commit_cadence_30d(self) -> bool | None:
        if self.commit_count_basis is ClaimBasis.DETERMINISTIC:
            return self.commit_count_30d is not None and self.commit_count_30d >= 4
        return self._legacy_commit_cadence_30d

    @property
    def contributor_count(self) -> bool | None:
        if self.contributor_count_basis is ClaimBasis.DETERMINISTIC:
            return self.contributor_total is not None and self.contributor_total >= 2
        return self._legacy_contributor_count

    @property
    def has_license(self) -> bool | None:
        if self.license_basis is ClaimBasis.DETERMINISTIC:
            return self.license is not None
        return self._legacy_has_license

    def to_dict(self) -> dict[str, object]:
        if any(
            value is not None
            for value in (
                self._legacy_commit_cadence_30d,
                self._legacy_contributor_count,
                self._legacy_has_license,
            )
        ):
            return {
                "commit_cadence_30d": self._legacy_commit_cadence_30d,
                "contributor_count": self._legacy_contributor_count,
                "has_license": self._legacy_has_license,
            }
        return {
            "commit_count_30d": self.commit_count_30d,
            "commit_count_basis": self.commit_count_basis.value,
            "contributor_count": self.contributor_total,
            "contributor_count_basis": self.contributor_count_basis.value,
            "license": self.license,
            "license_basis": self.license_basis.value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ScoreInputs:
        if "commit_count_30d" not in payload:
            return cls(
                payload.get("commit_cadence_30d"),  # type: ignore[arg-type]
                payload.get("contributor_count"),  # type: ignore[arg-type]
                payload.get("has_license"),  # type: ignore[arg-type]
            )
        license_value = payload.get("license")
        return cls.observed(
            payload.get("commit_count_30d"),  # type: ignore[arg-type]
            payload.get("contributor_count"),  # type: ignore[arg-type]
            license_value if isinstance(license_value, dict) else None,
            commit_count_basis=ClaimBasis(str(payload["commit_count_basis"])),
            contributor_count_basis=ClaimBasis(str(payload["contributor_count_basis"])),
            license_basis=ClaimBasis(str(payload["license_basis"])),
        )


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
