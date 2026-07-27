from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import datetime, timezone

import pytest

from gitseed.application import execute, replay
from gitseed.artifact import RunArtifact
from gitseed.collect.search import Candidate, CollectResult
from gitseed.grade.types import GradeResult
from gitseed.pipeline.run import FetchedFiles
from gitseed.ports import RepositoryMetadata, RunPorts, RunRequest
from gitseed.scoring import ALL_FEATURES, ScoreInputs
from gitseed.storage import SQLiteRunStore
from gitseed.storage_schema import SCHEMA_VERSION, SchemaVersionError, migrate

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


def artifact(files: Files | None = None) -> RunArtifact:
    return execute(
        RunRequest("small tools", 1),
        RunPorts(Repository(), files or Files(), Model(), Clock()),
    )


def test_empty_database_migrates_to_current_schema() -> None:
    # Given: a new SQLite database has no schema version or user tables.
    connection = sqlite3.connect(":memory:")

    # When: the run-store migration is applied.
    migrate(connection)

    # Then: the schema reaches its current version with the immutable run table.
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'run_artifacts'"
    ).fetchone() == ("run_artifacts",)


def test_nonempty_older_schema_is_refused() -> None:
    # Given: a database claims an older, unknown nonempty schema.
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE legacy_runs (run_id TEXT PRIMARY KEY)")

    # When: the current run-store migration opens it.
    with pytest.raises(SchemaVersionError, match="older schema version 0"):
        migrate(connection)

    # Then: it was not silently treated as the current schema.
    assert connection.execute("PRAGMA user_version").fetchone() == (0,)


def test_stored_artifact_round_trips_with_score_provenance(tmp_path) -> None:
    # Given: execution produced a complete artifact with a versioned, covered score.
    recorded = artifact()
    direct_replay = replay(recorded.to_bytes())
    with SQLiteRunStore(tmp_path / "runs.db") as store:
        store.save("run-1", recorded)

        # When: the record is loaded and replayed from SQLite.
        loaded = store.load("run-1")
        replayed = store.replay("run-1")

    # Then: canonical bytes and the score's version and coverage survive.
    assert loaded.to_bytes() == recorded.to_bytes()
    assert replayed.to_bytes() == direct_replay.to_bytes() == recorded.to_bytes()
    assert loaded.scores[0].score.version == recorded.scores[0].score.version
    assert loaded.scores[0].score.coverage == frozenset(ALL_FEATURES)


def test_partial_artifact_and_correction_history_are_preserved(tmp_path) -> None:
    # Given: an incomplete run followed by a correction that references it.
    partial = artifact(Files(OSError("offline")))
    with SQLiteRunStore(tmp_path / "runs.db") as store:
        store.save("run-1", partial)
        store.save("run-2", artifact(), corrects_run_id="run-1")
        with pytest.raises(sqlite3.IntegrityError):
            store.save("run-3", artifact(), corrects_run_id="missing-run")

        # When: the original run is loaded and an overwrite is attempted.
        loaded = store.load("run-1")
        with pytest.raises(sqlite3.IntegrityError):
            store.save("run-1", artifact())

    # Then: partial status remains visible and the original was never replaced.
    assert loaded.result.complete is False
    assert loaded.to_bytes() == partial.to_bytes()


def test_replaying_a_stored_artifact_recomputes_recorded_port_responses(tmp_path) -> None:
    # Given: storage holds an artifact whose derived score was corrupted before saving.
    recorded = artifact()
    corrupted = RunArtifact.from_bytes(
        recorded.to_bytes().replace(b'"value":"0.119096"', b'"value":"999"')
    )
    with SQLiteRunStore(tmp_path / "runs.db") as store:
        store.save("run-1", corrupted)

        # When: the stored run is replayed offline.
        replayed = store.replay("run-1")

    # Then: replay restores output by executing the recorded port responses.
    assert replayed.to_bytes() == recorded.to_bytes()


def test_stored_replay_has_no_external_writer_port() -> None:
    # Given/When/Then: storage replay uses the existing read-only application seam.
    assert [field.name for field in fields(RunPorts)] == [
        "repository",
        "files",
        "model",
        "clock",
    ]
