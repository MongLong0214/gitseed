from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .evidence import ClaimBasis

COLLECTOR_EVIDENCE: Final = frozenset(
    {"files", "manifest_entries", "dependencies", "metadata"}
)


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class EvidenceRequirement:
    evidence: str
    value: str


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class Evidence:
    evidence: str
    values: frozenset[str]
    basis: ClaimBasis


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class UnavailableEvidence(ValueError):
    evidence: tuple[str, ...]

    def __str__(self) -> str:
        return f"category pack names unavailable evidence: {', '.join(self.evidence)}"


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class CategoryVersionMismatch(TypeError):
    left: str
    right: str

    def __str__(self) -> str:
        return f"categories from pack versions {self.left!r} and {self.right!r} cannot be ordered"


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class CategoryPack:
    name: str
    version: str
    evidence: tuple[EvidenceRequirement, ...]

    def __post_init__(self) -> None:
        validate_pack(self)


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class Categorization:
    category: str | None
    pack_version: str
    basis: ClaimBasis
    missing_evidence: tuple[str, ...] = ()

    def __lt__(self, other: Categorization) -> bool:
        if self.pack_version != other.pack_version:
            raise CategoryVersionMismatch(self.pack_version, other.pack_version)
        return (self.category is None, self.category or "") < (
            other.category is None,
            other.category or "",
    )

    def render(self) -> str:
        rendered = {
            ClaimBasis.ABSENT: (
                "category: absent\n"
                f"pack version: {self.pack_version}\n"
                f"unavailable evidence: {', '.join(self.missing_evidence)}\n"
            ),
            ClaimBasis.DETERMINISTIC: (
                f"category: {self.category or 'uncategorized'}\n"
                f"pack version: {self.pack_version}\n"
                "evidence basis: deterministic\n"
            ),
            ClaimBasis.MODEL: (
                "category: uncategorized\n"
                f"pack version: {self.pack_version}\n"
                "evidence basis: model\n"
            ),
        }
        return rendered[self.basis]


def validate_pack(pack: CategoryPack) -> None:
    missing = tuple(
        requirement.evidence
        for requirement in pack.evidence
        if requirement.evidence not in COLLECTOR_EVIDENCE
    )
    if missing:
        raise UnavailableEvidence(missing)
    if not pack.evidence:
        raise ValueError("category pack requires deterministic evidence")


CATEGORY_PACKS: Final = (
    CategoryPack("coding-agents", "v1", (EvidenceRequirement("files", "AGENTS.md"),)),
    CategoryPack("mcp", "v1", (EvidenceRequirement("manifest_entries", "mcp"),)),
    CategoryPack("local-ai", "v1", (EvidenceRequirement("dependencies", "ollama"),)),
)


def classify(pack: CategoryPack, evidence: tuple[Evidence, ...]) -> Categorization:
    missing = tuple(
        requirement.evidence
        for requirement in pack.evidence
        if not _available(requirement, evidence)
    )
    if missing:
        return Categorization(None, pack.version, ClaimBasis.ABSENT, _unique(missing))
    if _matches(pack.evidence, evidence, ClaimBasis.DETERMINISTIC):
        return Categorization(pack.name, pack.version, ClaimBasis.DETERMINISTIC)
    if _matches(pack.evidence, evidence, ClaimBasis.MODEL):
        return Categorization(None, pack.version, ClaimBasis.MODEL)
    return Categorization(None, pack.version, ClaimBasis.DETERMINISTIC)


def _available(requirement: EvidenceRequirement, evidence: tuple[Evidence, ...]) -> bool:
    return any(item.evidence == requirement.evidence and item.basis is not ClaimBasis.ABSENT for item in evidence)


def _matches(
    requirements: tuple[EvidenceRequirement, ...],
    evidence: tuple[Evidence, ...],
    basis: ClaimBasis,
) -> bool:
    return all(
        any(
            item.evidence == requirement.evidence
            and requirement.value in item.values
            and item.basis is basis
            for item in evidence
        )
        for requirement in requirements
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
