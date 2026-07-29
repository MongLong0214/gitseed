from __future__ import annotations

import json
import sqlite3
from dataclasses import fields, replace
from datetime import datetime, timezone

import pytest

from gitseed.application import execute, re_evaluate, render
from gitseed.artifact import ArtifactVersionError, RunArtifact
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
    def __init__(
        self,
        candidate: Candidate = CANDIDATE,
        score_inputs: ScoreInputs | None = None,
    ) -> None:
        self.candidate = candidate
        self.score_inputs = score_inputs or ScoreInputs(True, True, True)

    def search(self, query: str, limit: int) -> CollectResult:
        return CollectResult(candidates=[self.candidate], pages_fetched=1)

    def metadata(self, candidate: Candidate, at: datetime) -> RepositoryMetadata:
        return RepositoryMetadata(self.score_inputs)


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
    def __init__(self, at: datetime = AT) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def artifact(
    files: Files | None = None,
    *,
    source_mode: str = "digest",
    stars: int = CANDIDATE.stars,
    at: datetime = AT,
) -> RunArtifact:
    return execute(
        RunRequest("small tools", 1),
        RunPorts(Repository(replace(CANDIDATE, stars=stars)), files or Files(), Model(), Clock(at)),
        source_mode=source_mode,
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
    direct_render = render(recorded.to_bytes())
    with SQLiteRunStore(tmp_path / "runs.db") as store:
        store.save("run-1", recorded)

        # When: the record is loaded and rendered from SQLite.
        loaded = store.load("run-1")
        rendered = render(store.load("run-1").to_bytes())

    # Then: canonical bytes and the score's version and coverage survive.
    assert loaded.to_bytes() == recorded.to_bytes()
    assert rendered.to_bytes() == direct_render.to_bytes() == recorded.to_bytes()
    assert loaded.scores[0].score.version == recorded.scores[0].score.version
    assert loaded.scores[0].score.coverage == frozenset(ALL_FEATURES)


def test_raw_metadata_reaches_the_versioned_artifact_without_rounding() -> None:
    inputs = ScoreInputs.observed(137, 109, {"spdx_id": "MIT", "name": "MIT License"})
    recorded = execute(
        RunRequest("small tools", 1),
        RunPorts(Repository(score_inputs=inputs), Files(), Model(), Clock()),
    )

    payload = json.loads(recorded.to_bytes())
    fragment = payload["ports"]["repositories"][0]["metadata"]["score_inputs"]
    restored = RunArtifact.from_bytes(recorded.to_bytes()).repositories[0].metadata

    assert fragment == {
        "commit_count_30d": 137,
        "commit_count_basis": "deterministic",
        "contributor_count": 109,
        "contributor_count_basis": "deterministic",
        "license": {"name": "MIT License", "spdx_id": "MIT"},
        "license_basis": "deterministic",
    }
    assert restored is not None
    assert restored.score_inputs == inputs


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


def test_observations_append_without_moving_first_seen(tmp_path) -> None:
    # Given: the store records the same repository again with a later count.
    later = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    with SQLiteRunStore(tmp_path / "runs.db") as store:
        store.save("first", artifact(stars=4, at=AT))
        first = store.observations()[0]

        # When: a later run observes more stars.
        store.save("second", artifact(stars=9, at=later))
        observations = store.observations()

    # Then: the new raw observation is appended without changing the first one.
    assert observations == (
        first,
        replace(first, run_id="second", observed_at=later, stars=9),
    )


def test_previous_store_schema_opens_and_migrates_additively(tmp_path) -> None:
    # Given: a store written by schema version 1, before observations existed.
    path = tmp_path / "runs.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE run_artifacts (
            run_id TEXT PRIMARY KEY,
            corrects_run_id TEXT REFERENCES run_artifacts(run_id),
            artifact BLOB NOT NULL
        );
        CREATE TRIGGER run_artifacts_no_update
        BEFORE UPDATE ON run_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'run artifacts are immutable');
        END;
        CREATE TRIGGER run_artifacts_no_delete
        BEFORE DELETE ON run_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'run artifacts are immutable');
        END;
        PRAGMA user_version = 1;
        """
    )
    connection.execute(
        "INSERT INTO run_artifacts (run_id, corrects_run_id, artifact) VALUES (?, ?, ?)",
        ("old-run", None, artifact().to_bytes()),
    )
    connection.commit()
    connection.close()

    # When: the current store opens it.
    with SQLiteRunStore(path) as store:
        loaded = store.load("old-run")
        observations = store.observations()

    # Then: the prior artifact survives and only future saves add observations.
    assert loaded.to_bytes() == artifact().to_bytes()
    assert observations == ()


def test_re_evaluating_a_full_source_stored_artifact_recomputes_recorded_port_responses(tmp_path) -> None:
    # Given: storage holds an artifact whose derived score was corrupted before saving.
    recorded = artifact(source_mode="full-source")
    corrupted = RunArtifact.from_bytes(
        recorded.to_bytes().replace(b'"value":"0.119096"', b'"value":"999"')
    )
    with SQLiteRunStore(tmp_path / "runs.db") as store:
        store.save("run-1", corrupted)

        # When: the stored run is explicitly re-evaluated offline.
        replayed = re_evaluate(store.load("run-1").to_bytes())

    # Then: replay restores output by executing the recorded port responses.
    assert replayed.to_bytes() == recorded.to_bytes()


def test_pre_change_artifact_fails_with_a_named_schema_version_mismatch() -> None:
    # Given: bytes recorded by the schema immediately before this change.
    previous_schema = artifact().to_bytes().replace(b'"schema":6', b'"schema":4')

    # When/Then: loading refuses to silently reinterpret the old source shape.
    with pytest.raises(ArtifactVersionError, match="artifact schema version mismatch: recorded 4, current 6"):
        RunArtifact.from_bytes(previous_schema)


def test_pre_raw_metadata_artifact_still_parses() -> None:
    previous = (
        artifact().to_bytes()
        .replace(b'"schema":6', b'"schema":5')
        .replace(b'"pipeline":"pipeline-v2"', b'"pipeline":"pipeline-v1"')
    )

    restored = RunArtifact.from_bytes(previous)

    assert restored.engines.pipeline == "pipeline-v1"
    assert restored.repositories[0].metadata is not None
    assert restored.repositories[0].metadata.score_inputs == ScoreInputs(True, True, True)


def test_stored_replay_has_no_external_writer_port() -> None:
    # Given/When/Then: storage replay uses the existing read-only application seam.
    assert [field.name for field in fields(RunPorts)] == [
        "repository",
        "files",
        "model",
        "clock",
    ]
