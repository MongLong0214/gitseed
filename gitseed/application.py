from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .artifact import (
    PortFailure,
    RepositoryTrace,
    RunArtifact,
    ScoredCandidate,
)
from .collect.search import Candidate, CollectResult
from .grade.smoke import SmokeResult, run_smoke
from .grade.types import GradeResult
from .pipeline.run import FileFetchError, FetchedFiles, run
from .ports import RepositoryMetadata, RunPorts, RunRequest
from .scoring import Recommendation, ScoreInputs, score


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class MissingPortResponse(RuntimeError):
    port: str
    target: str

    def __str__(self) -> str:
        return f"no recorded {self.port} response for {self.target}"


def execute(request: RunRequest, ports: RunPorts, *, model_smoke: SmokeResult | None = None) -> RunArtifact:
    failures: list[PortFailure] = []
    trace_failures: dict[str, list[PortFailure]] = {}
    metadata: dict[str, RepositoryMetadata | None] = {}
    files: dict[str, FetchedFiles] = {}
    grades: dict[str, GradeResult] = {}

    try:
        started_at = ports.clock.now()
    except Exception as error:  # noqa: BROAD_EXCEPT_OK -- every port failure belongs in the artifact
        started_at = None
        failures.append(PortFailure("clock", "now", "run", str(error)))

    try:
        collected = ports.repository.search(request.query, request.limit)
    except Exception as error:  # noqa: BROAD_EXCEPT_OK -- every port failure belongs in the artifact
        failure = PortFailure("repository", "search", request.query, str(error))
        failures.append(failure)
        collected = CollectResult(
            complete=False,
            stopped_because=f"repository search failed: {error}",
        )

    for candidate in collected.candidates:
        trace_failures[candidate.repo] = []
        if started_at is None:
            metadata[candidate.repo] = None
            continue
        try:
            metadata[candidate.repo] = ports.repository.metadata(candidate, started_at)
        except Exception as error:  # noqa: BROAD_EXCEPT_OK -- every port failure belongs in the artifact
            failure = PortFailure(
                "repository",
                "metadata",
                candidate.repo,
                str(error),
            )
            failures.append(failure)
            trace_failures[candidate.repo].append(failure)
            metadata[candidate.repo] = None

    def read(candidate: Candidate) -> FetchedFiles:
        try:
            fetched = ports.files.read(candidate)
        except FileFetchError as error:
            failure = PortFailure(
                "files",
                "read",
                candidate.repo,
                str(error),
                error.run_reason,
                error.rate_limited,
            )
            failures.append(failure)
            trace_failures[candidate.repo].append(failure)
            raise
        except Exception as error:  # noqa: BROAD_EXCEPT_OK -- every port failure belongs in the artifact
            failure = PortFailure("files", "read", candidate.repo, str(error))
            failures.append(failure)
            trace_failures[candidate.repo].append(failure)
            raise
        files[candidate.repo] = fetched
        return fetched

    smoke = run_smoke(ports.model) if model_smoke is None else model_smoke
    model = _RecordingModel(ports, grades, failures, trace_failures) if smoke.passed else None
    result = run(collected, fetch_files=read, grader=model)

    if smoke.passed is False:
        result.mark_incomplete(
            "model smoke gate failed: "
            + ("; ".join(smoke.failures) or "model returned no usable answer")
        )

    if started_at is None:
        result.mark_incomplete("clock failed; repository metadata was not read")
    for candidate in collected.candidates:
        observed = metadata[candidate.repo]
        if observed is None:
            result.mark_incomplete(f"{candidate.repo}: repository metadata failed")
        else:
            for reason in observed.incomplete_because:
                result.mark_incomplete(reason)

    scored = []
    for reviewed in result.reviewed:
        observed = metadata[reviewed.candidate.repo]
        inputs = (
            ScoreInputs(None, None, None)
            if observed is None
            else observed.score_inputs
        )
        scored.append(
            ScoredCandidate(
                reviewed.candidate.repo,
                Recommendation(score(inputs), reviewed.severity),
            )
        )

    repositories = tuple(
        RepositoryTrace(
            candidate,
            metadata[candidate.repo],
            files.get(candidate.repo),
            grades.get(candidate.repo),
            tuple(trace_failures[candidate.repo]),
        )
        for candidate in collected.candidates
    )
    return RunArtifact(request, started_at, collected, repositories, result, tuple(scored), smoke, tuple(failures))


class _RecordingModel:
    def __init__(
        self,
        ports: RunPorts,
        grades: dict[str, GradeResult],
        failures: list[PortFailure],
        trace_failures: dict[str, list[PortFailure]],
    ) -> None:
        self._model = ports.model
        self._grades = grades
        self._failures = failures
        self._trace_failures = trace_failures

    def evaluate(self, digest: str) -> GradeResult:
        repo = _repo_from_digest(digest)
        try:
            grade = self._model.evaluate(digest)
        except Exception as error:  # noqa: BROAD_EXCEPT_OK -- every port failure belongs in the artifact
            failure = PortFailure("model", "evaluate", repo, str(error))
            self._failures.append(failure)
            self._trace_failures[repo].append(failure)
            raise
        self._grades[repo] = grade
        return grade

    def flags_malicious(self, digest: str) -> bool:
        return self._model.flags_malicious(digest)


def replay(data: bytes) -> RunArtifact:
    source = RunArtifact.from_bytes(data)
    return execute(
        source.request,
        RunPorts(
            _ReplayRepository(source),
            _ReplayFiles(source),
            _ReplayModel(source),
            _ReplayClock(source),
        ),
        model_smoke=source.model_smoke,
    )


class _ReplayRepository:
    def __init__(self, artifact: RunArtifact) -> None:
        self._artifact = artifact
        self._traces = {trace.candidate.repo: trace for trace in artifact.repositories}

    def search(self, query: str, limit: int) -> CollectResult:
        failure = _failure(self._artifact.failures, "repository", "search", query)
        if failure is not None:
            raise OSError(failure.detail)
        recorded = self._artifact.collection
        return CollectResult(
            list(recorded.candidates),
            recorded.complete,
            recorded.stopped_because,
            recorded.pages_fetched,
        )

    def metadata(
        self,
        candidate: Candidate,
        at: datetime,
    ) -> RepositoryMetadata:
        trace = self._traces[candidate.repo]
        failure = _failure(trace.failures, "repository", "metadata", candidate.repo)
        if failure is not None:
            raise OSError(failure.detail)
        if trace.metadata is None:
            raise MissingPortResponse("metadata", candidate.repo)
        return trace.metadata


class _ReplayFiles:
    def __init__(self, artifact: RunArtifact) -> None:
        self._traces = {trace.candidate.repo: trace for trace in artifact.repositories}

    def read(self, candidate: Candidate) -> FetchedFiles:
        trace = self._traces[candidate.repo]
        failure = _failure(trace.failures, "files", "read", candidate.repo)
        if failure is not None:
            if failure.run_reason is not None or failure.rate_limited:
                raise FileFetchError(
                    failure.detail,
                    failure.run_reason,
                    failure.rate_limited,
                )
            raise OSError(failure.detail)
        if trace.files is None:
            raise MissingPortResponse("files", candidate.repo)
        return trace.files


class _ReplayModel:
    def __init__(self, artifact: RunArtifact) -> None:
        self._traces = {trace.candidate.repo: trace for trace in artifact.repositories}

    def evaluate(self, digest: str) -> GradeResult:
        repo = _repo_from_digest(digest)
        trace = self._traces[repo]
        failure = _failure(trace.failures, "model", "evaluate", repo)
        if failure is not None:
            raise RuntimeError(failure.detail)
        if trace.grade is None:
            raise MissingPortResponse("grade", repo)
        return trace.grade

    def flags_malicious(self, digest: str) -> bool:
        return False


class _ReplayClock:
    def __init__(self, artifact: RunArtifact) -> None:
        self._artifact = artifact

    def now(self) -> datetime:
        failure = _failure(self._artifact.failures, "clock", "now", "run")
        if failure is not None:
            raise RuntimeError(failure.detail)
        if self._artifact.started_at is None:
            raise MissingPortResponse("clock", "run")
        return self._artifact.started_at


def _failure(
    failures: tuple[PortFailure, ...],
    port: str,
    operation: str,
    target: str,
) -> PortFailure | None:
    return next(
        (
            failure
            for failure in failures
            if (failure.port, failure.operation, failure.target)
            == (port, operation, target)
        ),
        None,
    )


def _repo_from_digest(digest: str) -> str:
    return digest.splitlines()[0].removeprefix("repository: ")
