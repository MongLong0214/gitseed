from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from .collect.search import Candidate, CollectResult
from .grade.types import GradeResult
from .pipeline.run import FetchedFiles, PipelineResult, Reviewed
from .ports import RepositoryMetadata, RunRequest
from .scoring import Feature, Recommendation, Score, ScoreInputs
from .screen.signals import Signal

SCHEMA_VERSION = 1


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactVersionError(ValueError):
    version: int | str | None

    def __str__(self) -> str:
        return f"unsupported run artifact schema: {self.version!r}"


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class PortFailure:
    port: str
    operation: str
    target: str
    detail: str
    run_reason: str | None = None
    rate_limited: bool = False


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class RepositoryTrace:
    candidate: Candidate
    metadata: RepositoryMetadata | None
    files: FetchedFiles | None
    grade: GradeResult | None
    failures: tuple[PortFailure, ...] = ()


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ScoredCandidate:
    repo: str
    recommendation: Recommendation

    @property
    def score(self) -> Score:
        return self.recommendation.score


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class RunArtifact:
    request: RunRequest
    started_at: datetime | None
    collection: CollectResult
    repositories: tuple[RepositoryTrace, ...]
    result: PipelineResult
    scores: tuple[ScoredCandidate, ...]
    failures: tuple[PortFailure, ...] = ()

    def to_bytes(self) -> bytes:
        payload = {
            "schema": SCHEMA_VERSION,
            "input": asdict(self.request),
            "ports": {
                "started_at": None if self.started_at is None else self.started_at.isoformat(),
                "collection": asdict(self.collection),
                "repositories": [_trace_to_dict(trace) for trace in self.repositories],
                "failures": [asdict(failure) for failure in self.failures],
            },
            "output": {
                "result": asdict(self.result),
                "scores": [_scored_to_dict(scored) for scored in self.scores],
            },
        }
        return (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> RunArtifact:
        payload = json.loads(data)
        if payload.get("schema") != SCHEMA_VERSION:
            raise ArtifactVersionError(payload.get("schema"))
        ports = payload["ports"]
        output = payload["output"]
        started_at = ports["started_at"]
        return cls(
            request=RunRequest(**payload["input"]),
            started_at=None if started_at is None else datetime.fromisoformat(started_at),
            collection=_collection_from_dict(ports["collection"]),
            repositories=tuple(
                _trace_from_dict(trace) for trace in ports["repositories"]
            ),
            result=_result_from_dict(output["result"]),
            scores=tuple(_scored_from_dict(scored) for scored in output["scores"]),
            failures=tuple(PortFailure(**failure) for failure in ports["failures"]),
        )


def _candidate_from_dict(payload: dict[str, str | int]) -> Candidate:
    return Candidate(
        repo=str(payload["repo"]),
        owner=str(payload["owner"]),
        html_url=str(payload["html_url"]),
        stars=int(payload["stars"]),
        pushed_at=str(payload["pushed_at"]),
    )


def _collection_from_dict(payload: dict) -> CollectResult:
    return CollectResult(
        candidates=[_candidate_from_dict(candidate) for candidate in payload["candidates"]],
        complete=bool(payload["complete"]),
        stopped_because=payload["stopped_because"],
        pages_fetched=int(payload["pages_fetched"]),
    )


def _metadata_from_dict(payload: dict | None) -> RepositoryMetadata | None:
    if payload is None:
        return None
    return RepositoryMetadata(
        ScoreInputs(**payload["score_inputs"]),
        tuple(payload["incomplete_because"]),
    )


def _files_from_dict(payload: dict | None) -> FetchedFiles | None:
    if payload is None:
        return None
    return FetchedFiles(
        tuple((path, text) for path, text in payload["files"]),
        tuple(payload["skipped"]),
        bool(payload["complete"]),
        payload["incomplete_because"],
        bool(payload["rate_limited"]),
    )


def _grade_from_dict(payload: dict | None) -> GradeResult | None:
    return None if payload is None else GradeResult(**payload)


def _trace_to_dict(trace: RepositoryTrace):
    return {
        "candidate": asdict(trace.candidate),
        "metadata": None if trace.metadata is None else asdict(trace.metadata),
        "files": None if trace.files is None else asdict(trace.files),
        "grade": None if trace.grade is None else asdict(trace.grade),
        "failures": [asdict(failure) for failure in trace.failures],
    }


def _trace_from_dict(payload: dict) -> RepositoryTrace:
    return RepositoryTrace(
        _candidate_from_dict(payload["candidate"]),
        _metadata_from_dict(payload["metadata"]),
        _files_from_dict(payload["files"]),
        _grade_from_dict(payload["grade"]),
        tuple(PortFailure(**failure) for failure in payload["failures"]),
    )


def _result_from_dict(payload: dict) -> PipelineResult:
    reviewed = []
    for entry in payload["reviewed"]:
        reviewed.append(
            Reviewed(
                candidate=_candidate_from_dict(entry["candidate"]),
                signals=[Signal(**signal) for signal in entry["signals"]],
                severity=entry["severity"],
                grade=_grade_from_dict(entry["grade"]),
                withheld=entry["withheld"],
                skipped_files=tuple(entry["skipped_files"]),
            )
        )
    return PipelineResult(
        reviewed=reviewed,
        complete=bool(payload["complete"]),
        incomplete_because=list(payload["incomplete_because"]),
        rate_limited=bool(payload["rate_limited"]),
    )


def _scored_to_dict(scored: ScoredCandidate):
    score = scored.score
    return {
        "repo": scored.repo,
        "score": {
            "value": str(score.value),
            "version": score.version,
            "coverage": sorted(feature.value for feature in score.coverage),
        },
        "risk_verdict": scored.recommendation.risk_verdict,
    }


def _scored_from_dict(payload: dict) -> ScoredCandidate:
    score = payload["score"]
    value = Score(
        value=Decimal(score["value"]),
        version=score["version"],
        coverage=frozenset(Feature(feature) for feature in score["coverage"]),
    )
    return ScoredCandidate(
        payload["repo"],
        Recommendation(value, payload["risk_verdict"]),
    )
