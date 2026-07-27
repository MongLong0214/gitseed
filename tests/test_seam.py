from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

from gitseed.application import execute, replay
from gitseed.collect.search import Candidate, CollectResult
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
        return False


class Clock:
    def now(self) -> datetime:
        return AT


def ports(files: Files | None = None) -> RunPorts:
    return RunPorts(Repository(), files or Files(), Model(), Clock())


def test_replaying_an_artifact_offline_is_byte_identical() -> None:
    # Given: one live run records every response from its four ports.
    artifact = execute(RunRequest("small tools", 1), ports())
    live_bytes = artifact.to_bytes()

    # When: only that artifact is replayed, with no live adapters available.
    replayed_bytes = replay(live_bytes).to_bytes()

    # Then: the complete record and output are identical bytes.
    assert replayed_bytes == live_bytes
    assert artifact.scores[0].score.version
    assert artifact.scores[0].score.coverage == frozenset(ALL_FEATURES)


def test_a_failed_port_replays_the_same_partial_result() -> None:
    # Given: file reading fails after collection and metadata succeeded.
    artifact = execute(RunRequest("small tools", 1), ports(Files(OSError("offline"))))
    live_bytes = artifact.to_bytes()

    # When: the failed run is replayed from its recorded responses.
    replayed = replay(live_bytes)

    # Then: failure remains visible and the replay is byte-identical.
    assert artifact.result.complete is False
    assert artifact.result.reviewed[0].withheld == "files could not be read, so nothing was screened"
    assert replayed.to_bytes() == live_bytes
    assert replayed.result.complete is False


def test_replay_recomputes_output_from_recorded_port_responses() -> None:
    # Given: a valid artifact whose recorded output score was corrupted.
    live_bytes = execute(RunRequest("small tools", 1), ports()).to_bytes()
    corrupted = live_bytes.replace(b'"value":"0.119096"', b'"value":"999"')

    # When: replay executes from the port responses.
    replayed = replay(corrupted)

    # Then: the derived output returns to the live run's bytes.
    assert corrupted != live_bytes
    assert replayed.to_bytes() == live_bytes


def test_the_run_seam_has_no_external_writer_port() -> None:
    # Given/When/Then: reads and computation are the entire application seam.
    assert [field.name for field in fields(RunPorts)] == [
        "repository",
        "files",
        "model",
        "clock",
    ]
