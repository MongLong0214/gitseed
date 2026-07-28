from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .collect.search import Candidate, CollectResult
from .grade.types import GradeClient
from .pipeline.run import FetchedFiles
from .scoring import ScoreInputs


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class RunRequest:
    query: str
    limit: int


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class RepositoryMetadata:
    score_inputs: ScoreInputs
    incomplete_because: tuple[str, ...] = ()


class RepositoryReader(Protocol):
    def search(self, query: str, limit: int) -> CollectResult: ...

    def metadata(
        self,
        candidate: Candidate,
        at: datetime,
    ) -> RepositoryMetadata: ...


class FileReader(Protocol):
    def read(self, candidate: Candidate) -> FetchedFiles: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class RunPorts:
    repository: RepositoryReader
    files: FileReader
    model: GradeClient
    clock: Clock
