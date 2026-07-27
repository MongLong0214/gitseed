from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType

from .application import replay as replay_artifact
from .artifact import RunArtifact
from .storage_schema import migrate


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

    def load(self, run_id: str) -> RunArtifact:
        row = self._connection.execute(
            "SELECT artifact FROM run_artifacts WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunArtifact.from_bytes(bytes(row[0]))

    def replay(self, run_id: str) -> RunArtifact:
        return replay_artifact(self.load(run_id).to_bytes())
