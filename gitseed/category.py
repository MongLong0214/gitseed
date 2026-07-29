from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .evidence import ClaimBasis

if TYPE_CHECKING:
    from .collect.search import Candidate
    from .pipeline.run import FetchedFiles
    from .ports import RepositoryMetadata


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class EvidenceRequirement:
    evidence: str
    value: str


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class Evidence:
    evidence: str
    values: frozenset[str]
    basis: ClaimBasis


class FileEvidenceReader:
    """Extract the small, deterministic evidence vocabulary category packs use."""

    @property
    def evidence_names(self) -> frozenset[str]:
        return frozenset(self._producer_name(producer) for producer in self._producers)

    @property
    def _producers(self):
        return (self._files, self._manifest_entries, self._dependencies, self._source)

    def read_evidence(
        self,
        candidate: Candidate,
        files: FetchedFiles,
        metadata: RepositoryMetadata | None,
    ) -> tuple[Evidence, ...]:
        basis = ClaimBasis.DETERMINISTIC if files.complete else ClaimBasis.ABSENT
        return tuple(producer(files, basis) for producer in self._producers)

    def _files(self, files: FetchedFiles, basis: ClaimBasis) -> Evidence:
        return Evidence(
            self._producer_name(self._files),
            frozenset({"AGENTS.md"} if any(path == "AGENTS.md" for path, _ in files.files) else ()),
            basis,
        )

    def _manifest_entries(self, files: FetchedFiles, basis: ClaimBasis) -> Evidence:
        return Evidence(
            self._producer_name(self._manifest_entries),
            frozenset({"mcp"} if "mcp" in self._manifest(files) else ()),
            basis,
        )

    def _dependencies(self, files: FetchedFiles, basis: ClaimBasis) -> Evidence:
        return Evidence(
            self._producer_name(self._dependencies),
            frozenset({"ollama"} if "ollama" in self._manifest(files) else ()),
            basis,
        )

    def _source(self, files: FetchedFiles, basis: ClaimBasis) -> Evidence:
        contents = "\n".join(text.lower() for path, text in files.files if path != "AGENTS.md")
        source_signal = "agent" in contents and bool(re.search(r"\b(tool|planner|executor)\b", contents))
        return Evidence(
            self._producer_name(self._source),
            frozenset({"agent-runtime"} if source_signal else ()),
            basis,
        )

    def _manifest(self, files: FetchedFiles) -> str:
        return "\n".join(
            text.lower()
            for path, text in files.files
            if path.rsplit("/", 1)[-1] in {"package.json", "pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt"}
        )

    def _producer_name(self, producer) -> str:
        return producer.__name__.removeprefix("_")


DEFAULT_EVIDENCE_READER: Final = FileEvidenceReader()


def satisfiable_evidence(reader: FileEvidenceReader = DEFAULT_EVIDENCE_READER) -> frozenset[str]:
    return reader.evidence_names


def absent_evidence() -> tuple[Evidence, ...]:
    return tuple(Evidence(name, frozenset(), ClaimBasis.ABSENT) for name in satisfiable_evidence())


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class UnavailableEvidence(ValueError):
    pack: str
    evidence: tuple[str, ...]

    def __str__(self) -> str:
        return f"category pack {self.pack!r} names unavailable evidence: {', '.join(self.evidence)}"


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class PackId:
    name: str
    version: str


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class CategoryVersionMismatch(TypeError):
    left: PackId
    right: PackId

    def __str__(self) -> str:
        return f"categories from pack versions {self.left!r} and {self.right!r} cannot be ordered"


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class CategoryPack:
    name: str
    version: str
    evidence: tuple[EvidenceRequirement, ...]

    def __post_init__(self) -> None:
        validate_pack(self)

    @property
    def pack(self) -> PackId:
        return PackId(self.name, self.version)


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class CategoryMatch:
    category: str | None
    pack: PackId
    basis: ClaimBasis
    missing_evidence: tuple[str, ...] = ()

    @property
    def pack_version(self) -> str:
        return self.pack.version

    def __lt__(self, other: CategoryMatch) -> bool:
        if self.pack != other.pack:
            raise CategoryVersionMismatch(self.pack, other.pack)
        return (self.category is None, self.category or "") < (other.category is None, other.category or "")

    def render(self) -> str:
        prefix = f"pack: {self.pack.name} {self.pack.version}\n"
        if self.basis is ClaimBasis.ABSENT:
            return "category: absent\n" + prefix + f"unavailable evidence: {', '.join(self.missing_evidence)}\n"
        return f"category: {self.category or 'uncategorized'}\n" + prefix + f"evidence basis: {self.basis.value}\n"


Categorization = CategoryMatch


def validate_pack(pack: CategoryPack, reader: FileEvidenceReader = DEFAULT_EVIDENCE_READER) -> None:
    missing = tuple(
        requirement.evidence
        for requirement in pack.evidence
        if requirement.evidence not in satisfiable_evidence(reader)
    )
    if missing:
        raise UnavailableEvidence(pack.name, missing)
    if not pack.evidence:
        raise ValueError("category pack requires deterministic evidence")


CATEGORY_PACKS: Final = (
    CategoryPack("coding-agents", "v2", (EvidenceRequirement("files", "AGENTS.md"), EvidenceRequirement("source", "agent-runtime"))),
    CategoryPack("mcp", "v1", (EvidenceRequirement("manifest_entries", "mcp"),)),
    CategoryPack("local-ai", "v1", (EvidenceRequirement("dependencies", "ollama"),)),
)


def selected_packs(names: tuple[str, ...]) -> tuple[CategoryPack, ...]:
    wanted = set(names)
    available = {pack.name for pack in CATEGORY_PACKS}
    unknown = wanted - available
    if unknown:
        raise ValueError(f"unknown category: {sorted(unknown)[0]}")
    return tuple(pack for pack in CATEGORY_PACKS if not wanted or pack.name in wanted)


def classify(pack: CategoryPack, evidence: tuple[Evidence, ...]) -> CategoryMatch:
    missing = tuple(requirement.evidence for requirement in pack.evidence if not _available(requirement, evidence))
    if missing:
        return CategoryMatch(None, pack.pack, ClaimBasis.ABSENT, _unique(missing))
    if _matches(pack.evidence, evidence, ClaimBasis.DETERMINISTIC):
        return CategoryMatch(pack.name, pack.pack, ClaimBasis.DETERMINISTIC)
    if _matches(pack.evidence, evidence, ClaimBasis.MODEL):
        return CategoryMatch(None, pack.pack, ClaimBasis.MODEL)
    return CategoryMatch(None, pack.pack, ClaimBasis.DETERMINISTIC)


def classify_all(packs: tuple[CategoryPack, ...], evidence: tuple[Evidence, ...]) -> tuple[CategoryMatch, ...]:
    return tuple(classify(pack, evidence) for pack in packs)


def _available(requirement: EvidenceRequirement, evidence: tuple[Evidence, ...]) -> bool:
    return any(item.evidence == requirement.evidence and item.basis is not ClaimBasis.ABSENT for item in evidence)


def _matches(requirements: tuple[EvidenceRequirement, ...], evidence: tuple[Evidence, ...], basis: ClaimBasis) -> bool:
    return all(any(item.evidence == requirement.evidence and requirement.value in item.values and item.basis is basis for item in evidence) for requirement in requirements)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
