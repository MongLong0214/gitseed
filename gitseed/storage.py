from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

from .application import replay as replay_artifact
from .artifact import RunArtifact
from .storage_schema import migrate


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    corrects_run_id: str | None
    artifact: RunArtifact


@dataclass(frozen=True)
class StoredObservation:
    run_id: str
    repo: str
    observed_at: datetime
    stars: int


class ObservationWriteError(RuntimeError):
    """The run was stored but its derived observation could not be appended."""


class SQLiteRunStore:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        migrate(self._connection)

    def __enter__(self) -> SQLiteRunStore:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def save(
        self,
        run_id: str,
        artifact: RunArtifact,
        corrects_run_id: str | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO run_artifacts (run_id, corrects_run_id, artifact) "
                "VALUES (?, ?, ?)",
                (run_id, corrects_run_id, artifact.to_bytes()),
            )
        if artifact.started_at is not None:
            try:
                with self._connection:
                    self._connection.executemany(
                        "INSERT INTO repository_observations (run_id, repo, observed_at, stars) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            (run_id, candidate.repo, artifact.started_at.isoformat(), candidate.stars)
                            for candidate in artifact.collection.candidates
                        ),
                    )
            except sqlite3.Error as error:
                raise ObservationWriteError(str(error)) from error

    def load(self, run_id: str) -> RunArtifact:
        row = self._connection.execute(
            "SELECT artifact FROM run_artifacts WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunArtifact.from_bytes(bytes(row[0]))

    def history(self) -> tuple[StoredRun, ...]:
        return tuple(
            StoredRun(str(run_id), corrects_run_id, RunArtifact.from_bytes(bytes(artifact)))
            for run_id, corrects_run_id, artifact in self._connection.execute(
                "SELECT run_id, corrects_run_id, artifact FROM run_artifacts ORDER BY rowid"
            )
        )

    def observations(self) -> tuple[StoredObservation, ...]:
        return tuple(
            StoredObservation(
                str(run_id),
                str(repo),
                datetime.fromisoformat(str(observed_at)),
                int(stars),
            )
            for run_id, repo, observed_at, stars in self._connection.execute(
                "SELECT run_id, repo, observed_at, stars FROM repository_observations ORDER BY observation_id"
            )
        )

    def replay(self, run_id: str) -> RunArtifact:
        return replay_artifact(self.load(run_id).to_bytes())
