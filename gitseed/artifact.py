from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from .category import CategoryMatch, CategoryPack, Evidence, EvidenceRequirement, PackId, classify_all
from .collect.search import Candidate, CollectResult, SearchParameters
from .evidence import ClaimBasis
from .grade.smoke import SmokeResult
from .grade.types import GradeResult
from .pipeline.run import FetchedFiles, PipelineResult, Reviewed
from .ports import RepositoryMetadata, RunRequest
from .scoring import Feature, Recommendation, Score, ScoreInputs
from .screen.coverage import SkippedFile, SourceCoverage
from .screen.signals import Signal
from .screen.verdict import findings, unverified

SCHEMA_VERSION = 8
SourceMode = Literal["metadata-only", "digest", "full-source"]


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class EngineVersions:
    """Semantic identifiers bumped when an engine's observable result changes."""

    pipeline: str = "pipeline-v2"
    screening: str = "screening-v1"
    source_selection: str = "source-selection-v1"
    category_packs: str = "category-packs-v2"
    search: str = "github-search-v1"


ENGINE_VERSIONS = EngineVersions()


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactVersionError(ValueError):
    version: int | str | None

    def __str__(self) -> str:
        return f"artifact schema version mismatch: recorded {self.version!r}, current {SCHEMA_VERSION}"


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class EngineVersionMismatch(ValueError):
    engine: str
    recorded: str
    current: str

    def __str__(self) -> str:
        return f"{self.engine} engine changed: recorded {self.recorded}, current {self.current}"


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
    files: ArtifactFiles | None
    grade: GradeResult | None
    failures: tuple[PortFailure, ...] = ()
    category_evidence: tuple[Evidence, ...] = ()
    categories: tuple[CategoryMatch, ...] = ()


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ScoredCandidate:
    repo: str
    recommendation: Recommendation

    @property
    def score(self) -> Score:
        return self.recommendation.score


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactCollection:
    candidates: tuple[Candidate, ...]
    complete: bool
    stopped_because: str | None
    pages_fetched: int
    total_count: int | None
    incomplete_results: bool
    search: SearchParameters | None

    @property
    def complete_for_search(self) -> bool:
        return (
            self.complete
            and not self.incomplete_results
            and (self.total_count is None or len(self.candidates) >= self.total_count)
        )

    @classmethod
    def from_collected(cls, collected: CollectResult) -> ArtifactCollection:
        return cls(
            tuple(collected.candidates),
            collected.complete,
            collected.stopped_because,
            collected.pages_fetched,
            collected.total_count,
            collected.search_incomplete,
            collected.search,
        )


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactReviewed:
    candidate: Candidate
    signals: tuple[Signal, ...]
    severity: str
    grade: GradeResult | None
    withheld: str | None
    skipped_files: tuple[str, ...]
    screening_basis: ClaimBasis
    screened_files: tuple[str, ...]
    coverage: SourceCoverage | None

    @classmethod
    def from_reviewed(cls, reviewed: Reviewed) -> ArtifactReviewed:
        return cls(
            reviewed.candidate,
            tuple(reviewed.signals),
            reviewed.severity,
            reviewed.grade,
            reviewed.withheld,
            reviewed.skipped_files,
            reviewed.screening_basis,
            reviewed.screened_files,
            reviewed.coverage,
        )

    @property
    def findings(self) -> tuple[Signal, ...]:
        return findings(self.signals)

    @property
    def unverified(self) -> tuple[Signal, ...]:
        return unverified(self.signals)

    @property
    def score(self) -> int | None:
        return None if self.grade is None else self.grade.idea + self.grade.skill


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactPipelineResult:
    reviewed: tuple[ArtifactReviewed, ...]
    complete: bool
    incomplete_because: tuple[str, ...]
    rate_limited: bool
    grading_basis: ClaimBasis

    @classmethod
    def from_result(cls, result: PipelineResult) -> ArtifactPipelineResult:
        return cls(
            tuple(ArtifactReviewed.from_reviewed(reviewed) for reviewed in result.reviewed),
            result.complete,
            tuple(result.incomplete_because),
            result.rate_limited,
            result.grading_basis,
        )


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactSmokeResult:
    passed: bool
    model: str
    failures: tuple[str, ...]

    @classmethod
    def from_smoke(cls, smoke: SmokeResult) -> ArtifactSmokeResult:
        return cls(smoke.passed, smoke.model, tuple(smoke.failures))


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactSourceFile:
    path: str
    sha256: str | None
    size: int
    excerpts: tuple[str, ...] = ()
    content: str | None = None


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class ArtifactFiles:
    mode: SourceMode
    files: tuple[ArtifactSourceFile, ...]
    skipped: tuple[str, ...] = ()
    complete: bool = True
    incomplete_because: str | None = None
    rate_limited: bool = False
    coverage: SourceCoverage | None = None

    @classmethod
    def from_fetched(
        cls,
        fetched: FetchedFiles,
        mode: SourceMode,
        signals: tuple[Signal, ...],
    ) -> ArtifactFiles:
        excerpts = _signal_excerpts(fetched.files, signals) if mode == "digest" else {}
        return cls(
            mode,
            tuple(
                ArtifactSourceFile(
                    path,
                    None if mode == "metadata-only" else sha256(text.encode()).hexdigest(),
                    len(text.encode()),
                    excerpts.get(path, ()),
                    text if mode == "full-source" else None,
                )
                for path, text in fetched.files
            ),
            fetched.skipped,
            fetched.complete,
            fetched.incomplete_because,
            fetched.rate_limited,
            fetched.coverage,
        )

    def to_fetched(self) -> FetchedFiles:
        if self.mode != "full-source":
            raise ValueError(f"cannot re-evaluate a {self.mode} artifact; record with --source-mode full-source")
        return FetchedFiles(
            tuple((file.path, file.content or "") for file in self.files),
            self.skipped,
            self.complete,
            self.incomplete_because,
            self.rate_limited,
            self.coverage,
        )


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class RunArtifact:
    request: RunRequest
    started_at: datetime | None
    collection: ArtifactCollection
    repositories: tuple[RepositoryTrace, ...]
    result: ArtifactPipelineResult
    scores: tuple[ScoredCandidate, ...]
    model_smoke: ArtifactSmokeResult
    engines: EngineVersions = ENGINE_VERSIONS
    source_mode: SourceMode = "digest"
    failures: tuple[PortFailure, ...] = ()
    category_packs: tuple[CategoryPack, ...] = ()

    def rederive_categories(self, repo: str) -> tuple[CategoryMatch, ...]:
        trace = next(trace for trace in self.repositories if trace.candidate.repo == repo)
        return classify_all(self.category_packs, trace.category_evidence)

    def to_bytes(self) -> bytes:
        payload = {
            "schema": SCHEMA_VERSION,
            "engines": asdict(self.engines),
            "source_mode": self.source_mode,
            "category_packs": [_pack_to_dict(pack) for pack in self.category_packs],
            "input": asdict(self.request),
            "ports": {
                "started_at": None if self.started_at is None else self.started_at.isoformat(),
                "collection": asdict(self.collection),
                "repositories": [_trace_to_dict(trace) for trace in self.repositories],
                "failures": [asdict(failure) for failure in self.failures],
                "model_smoke": asdict(self.model_smoke),
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
        if payload.get("schema") not in (5, 6, 7, SCHEMA_VERSION):
            raise ArtifactVersionError(payload.get("schema"))
        ports = payload["ports"]
        output = payload["output"]
        started_at = ports["started_at"]
        engines = dict(payload["engines"])
        engines.setdefault("search", "unrecorded")
        return cls(
            request=RunRequest(**payload["input"]),
            started_at=None if started_at is None else datetime.fromisoformat(started_at),
            collection=_collection_from_dict(ports["collection"]),
            repositories=tuple(
                _trace_from_dict(trace) for trace in ports["repositories"]
            ),
            result=_result_from_dict(output["result"]),
            scores=tuple(_scored_from_dict(scored) for scored in output["scores"]),
            model_smoke=_smoke_from_dict(ports["model_smoke"]),
            engines=EngineVersions(**engines),
            source_mode=payload["source_mode"],
            failures=tuple(PortFailure(**failure) for failure in ports["failures"]),
            category_packs=tuple(_pack_from_dict(pack) for pack in payload.get("category_packs", ())),
        )


def _candidate_from_dict(payload: dict[str, str | int]) -> Candidate:
    return Candidate(
        repo=str(payload["repo"]),
        owner=str(payload["owner"]),
        html_url=str(payload["html_url"]),
        stars=int(payload["stars"]),
        pushed_at=str(payload["pushed_at"]),
    )


def _collection_from_dict(payload: dict) -> ArtifactCollection:
    search = payload.get("search")
    return ArtifactCollection(
        candidates=tuple(_candidate_from_dict(candidate) for candidate in payload["candidates"]),
        complete=bool(payload["complete"]),
        stopped_because=payload["stopped_because"],
        pages_fetched=int(payload["pages_fetched"]),
        total_count=payload["total_count"],
        incomplete_results=bool(payload["incomplete_results"]),
        search=None if search is None else SearchParameters(**search),
    )


def _metadata_from_dict(payload: dict | None) -> RepositoryMetadata | None:
    if payload is None:
        return None
    return RepositoryMetadata(
        ScoreInputs.from_dict(payload["score_inputs"]),
        tuple(payload["incomplete_because"]),
    )


def _files_from_dict(payload: dict | None) -> ArtifactFiles | None:
    if payload is None:
        return None
    return ArtifactFiles(
        payload["mode"],
        tuple(ArtifactSourceFile(**file) for file in payload["files"]),
        tuple(payload["skipped"]),
        bool(payload["complete"]),
        payload["incomplete_because"],
        bool(payload["rate_limited"]),
        _coverage_from_dict(payload.get("coverage")),
    )


def _coverage_from_dict(payload: dict | None) -> SourceCoverage | None:
    """`.get("coverage")` so a schema-3 artifact recorded before this field
    existed still replays -- it simply carries no coverage claim, the same
    as a fixture read today."""
    if payload is None:
        return None
    return SourceCoverage(
        discovered_files=int(payload["discovered_files"]),
        eligible_files=int(payload["eligible_files"]),
        scanned_files=int(payload["scanned_files"]),
        skipped_policy=tuple(SkippedFile(**item) for item in payload["skipped_policy"]),
        skipped_error=tuple(SkippedFile(**item) for item in payload["skipped_error"]),
    )


def _grade_from_dict(payload: dict | None) -> GradeResult | None:
    return None if payload is None else GradeResult(**payload)


def _smoke_from_dict(payload: dict) -> ArtifactSmokeResult:
    return ArtifactSmokeResult(bool(payload["passed"]), str(payload["model"]), tuple(payload["failures"]))


def _trace_to_dict(trace: RepositoryTrace):
    return {
        "candidate": asdict(trace.candidate),
        "metadata": None
        if trace.metadata is None
        else {
            "score_inputs": trace.metadata.score_inputs.to_dict(),
            "incomplete_because": list(trace.metadata.incomplete_because),
        },
        "files": None if trace.files is None else asdict(trace.files),
        "grade": None if trace.grade is None else asdict(trace.grade),
        "failures": [asdict(failure) for failure in trace.failures],
        "category_evidence": [_evidence_to_dict(item) for item in trace.category_evidence],
        "categories": [_category_to_dict(item) for item in trace.categories],
    }


def _trace_from_dict(payload: dict) -> RepositoryTrace:
    return RepositoryTrace(
        _candidate_from_dict(payload["candidate"]),
        _metadata_from_dict(payload["metadata"]),
        _files_from_dict(payload["files"]),
        _grade_from_dict(payload["grade"]),
        tuple(PortFailure(**failure) for failure in payload["failures"]),
        tuple(_evidence_from_dict(item) for item in payload.get("category_evidence", ())),
        tuple(_category_from_dict(item) for item in payload.get("categories", ())),
    )


def _pack_to_dict(pack: CategoryPack) -> dict:
    return {
        "name": pack.name,
        "version": pack.version,
        "evidence": [asdict(requirement) for requirement in pack.evidence],
    }


def _pack_from_dict(payload: dict) -> CategoryPack:
    return CategoryPack(
        str(payload["name"]),
        str(payload["version"]),
        tuple(EvidenceRequirement(**requirement) for requirement in payload["evidence"]),
    )


def _evidence_to_dict(evidence: Evidence) -> dict:
    return {"evidence": evidence.evidence, "values": sorted(evidence.values), "basis": evidence.basis.value}


def _evidence_from_dict(payload: dict) -> Evidence:
    return Evidence(str(payload["evidence"]), frozenset(payload["values"]), ClaimBasis(payload["basis"]))


def _category_to_dict(category: CategoryMatch) -> dict:
    return {
        "category": category.category,
        "pack": asdict(category.pack),
        "basis": category.basis.value,
        "missing_evidence": list(category.missing_evidence),
    }


def _category_from_dict(payload: dict) -> CategoryMatch:
    return CategoryMatch(
        payload["category"],
        PackId(**payload["pack"]),
        ClaimBasis(payload["basis"]),
        tuple(payload["missing_evidence"]),
    )


def _result_from_dict(payload: dict) -> ArtifactPipelineResult:
    reviewed = []
    for entry in payload["reviewed"]:
        reviewed.append(
            ArtifactReviewed(
                candidate=_candidate_from_dict(entry["candidate"]),
                signals=tuple(Signal(**signal) for signal in entry["signals"]),
                severity=entry["severity"],
                grade=_grade_from_dict(entry["grade"]),
                withheld=entry["withheld"],
                skipped_files=tuple(entry["skipped_files"]),
                screening_basis=ClaimBasis(entry["screening_basis"]),
                screened_files=tuple(entry["screened_files"]),
                coverage=_coverage_from_dict(entry.get("coverage")),
            )
        )
    return ArtifactPipelineResult(
        reviewed=tuple(reviewed),
        complete=bool(payload["complete"]),
        incomplete_because=tuple(payload["incomplete_because"]),
        rate_limited=bool(payload["rate_limited"]),
        grading_basis=ClaimBasis(payload["grading_basis"]),
    )


def _signal_excerpts(
    files: tuple[tuple[str, str], ...],
    signals: tuple[Signal, ...],
) -> dict[str, tuple[str, ...]]:
    by_path: dict[str, list[str]] = {}
    for path, text in files:
        lines = text.splitlines()
        for signal in signals:
            if signal.path == path and signal.line <= len(lines):
                by_path.setdefault(path, []).append(lines[signal.line - 1][:240])
    return {path: tuple(excerpts) for path, excerpts in by_path.items()}


def _scored_to_dict(scored: ScoredCandidate):
    score = scored.score
    return {
        "repo": scored.repo,
        "score": {
            "value": str(score.value),
            "version": score.version,
            "coverage": sorted(feature.value for feature in score.coverage),
            "basis": score.basis.value,
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
