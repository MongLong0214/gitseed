from __future__ import annotations

import sqlite3

import pytest

from gitseed.storage_schema import SCHEMA_VERSION, SchemaVersionError, migrate


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
