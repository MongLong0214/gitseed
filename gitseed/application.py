from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .category import absent_evidence, classify_all, selected_packs
from .artifact import (
    ENGINE_VERSIONS,
    ArtifactCollection,
    ArtifactFiles,
    ArtifactPipelineResult,
    ArtifactSmokeResult,
    EngineVersionMismatch,
    PortFailure,
    RepositoryTrace,
    RunArtifact,
    ScoredCandidate,
    SourceMode,
)
from .collect.search import Candidate, CollectResult
from .grade.smoke import SmokeResult, run_smoke
from .grade.types import GradeResult
from .pipeline.run import BLOCKING_SEVERITY, FileFetchError, FetchedFiles, run
from .ports import RepositoryMetadata, RunPorts, RunRequest
from .scoring import Recommendation, ScoreInputs, score


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class MissingPortResponse(RuntimeError):
    port: str
    target: str

    def __str__(self) -> str:
        return f"no recorded {self.port} response for {self.target}"


def execute(
    request: RunRequest,
    ports: RunPorts,
    *,
    model_smoke: SmokeResult | None = None,
    source_mode: SourceMode = "digest",
) -> RunArtifact:
    packs = selected_packs(request.categories)
    failures: list[PortFailure] = []
    trace_failures: dict[str, list[PortFailure]] = {}
    metadata: dict[str, RepositoryMetadata | None] = {}
    metadata_attempted: set[str] = set()
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
        metadata[candidate.repo] = None

    def observe_metadata(candidate: Candidate) -> None:
        if started_at is None:
            return
        metadata_attempted.add(candidate.repo)
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
        if not isinstance(fetched, FetchedFiles):
            fetched = FetchedFiles(tuple(fetched))
        files[candidate.repo] = fetched
        return fetched

    smoke = run_smoke(ports.model) if model_smoke is None else model_smoke
    model = _RecordingModel(ports, grades, failures, trace_failures) if smoke.passed else None
    result = run(collected, fetch_files=read, grader=model, on_survivor=observe_metadata)

    if smoke.passed is False:
        result = result.with_incomplete(
            "model smoke gate failed: "
            + ("; ".join(smoke.failures) or "model returned no usable answer")
        )

    if started_at is None:
        result = result.with_incomplete("clock failed; repository metadata was not read")
    for candidate in collected.candidates:
        if candidate.repo not in metadata_attempted:
            continue
        observed = metadata[candidate.repo]
        if observed is None:
            result = result.with_incomplete(f"{candidate.repo}: repository metadata failed")
        else:
            for reason in observed.incomplete_because:
                result = result.with_incomplete(reason)

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

    reviewed_by_repo = {reviewed.candidate.repo: reviewed for reviewed in result.reviewed}
    category_evidence = {}
    categories = {}
    for candidate in collected.candidates:
        try:
            evidence = (
                absent_evidence()
                if candidate.repo not in files
                else ports.evidence.read_evidence(candidate, files[candidate.repo], metadata[candidate.repo])
            )
        except Exception as error:  # noqa: BROAD_EXCEPT_OK -- category evidence must not reach approval
            failure = PortFailure("category", "read", candidate.repo, str(error))
            failures.append(failure)
            trace_failures[candidate.repo].append(failure)
            evidence = absent_evidence()
        category_evidence[candidate.repo] = evidence
        categories[candidate.repo] = classify_all(packs, evidence)
    repositories = tuple(
        RepositoryTrace(
            candidate,
            metadata[candidate.repo],
            None
            if candidate.repo not in files
            else ArtifactFiles.from_fetched(
                files[candidate.repo],
                source_mode,
                tuple(reviewed_by_repo[candidate.repo].signals),
            ),
            grades.get(candidate.repo),
            tuple(trace_failures[candidate.repo]),
            category_evidence[candidate.repo],
            categories[candidate.repo],
        )
        for candidate in collected.candidates
    )
    return RunArtifact(
        request=request,
        started_at=started_at,
        collection=ArtifactCollection.from_collected(collected),
        repositories=repositories,
        result=ArtifactPipelineResult.from_result(result),
        scores=tuple(scored),
        model_smoke=ArtifactSmokeResult.from_smoke(smoke),
        failures=tuple(failures),
        engines=ENGINE_VERSIONS,
        source_mode=source_mode,
        category_packs=packs,
    )


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


def render(data: bytes) -> RunArtifact:
    """Load stored output without executing any artifact inputs."""
    return RunArtifact.from_bytes(data)


def replay(data: bytes) -> RunArtifact:
    """Recompute recorded responses only when engine versions match."""
    source = render(data)
    mismatches = engine_version_mismatches(source)
    if mismatches:
        raise mismatches[0]
    return re_evaluate(data)


def engine_version_mismatches(artifact: RunArtifact) -> tuple[EngineVersionMismatch, ...]:
    return tuple(
        EngineVersionMismatch(engine, recorded, current)
        for engine in ("pipeline", "screening", "source_selection", "category_packs", "search")
        if (recorded := getattr(artifact.engines, engine)) != (current := getattr(ENGINE_VERSIONS, engine))
    )


def re_evaluate(data: bytes) -> RunArtifact:
    """Recompute stored full-source port responses with current code."""
    source = render(data)
    _require_full_source(source)
    return execute(
        source.request,
        RunPorts(
            _ReplayRepository(source),
            _ReplayFiles(source),
            _ReplayModel(source),
            _ReplayClock(source),
        ),
        model_smoke=SmokeResult(source.model_smoke.passed, source.model_smoke.model, source.model_smoke.failures),
        source_mode="full-source",
    )


def _require_full_source(artifact: RunArtifact) -> None:
    for trace in artifact.repositories:
        if trace.files is not None and trace.files.mode != "full-source":
            raise ValueError(
                f"cannot re-evaluate a {trace.files.mode} artifact; record with --source-mode full-source"
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
            recorded.total_count,
            recorded.incomplete_results,
            recorded.search,
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
        return trace.files.to_fetched()


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
