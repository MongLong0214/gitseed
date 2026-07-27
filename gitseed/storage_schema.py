from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

SCHEMA_VERSION: Final = 1


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class SchemaVersionError(RuntimeError):
    version: int
    direction: str

    def __str__(self) -> str:
        return (
            f"run store has {self.direction} schema version {self.version}; "
            f"this release supports version {SCHEMA_VERSION}"
        )


def migrate(connection: sqlite3.Connection) -> None:
    version = _version(connection)
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(version, "newer")
    if version == 0 and _has_user_tables(connection):
        raise SchemaVersionError(version, "older")

    with connection:
        while version < SCHEMA_VERSION:
            _migrate_from(version, connection)
            version += 1
            connection.execute(f"PRAGMA user_version = {version}")


def _version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def _has_user_tables(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT EXISTS("
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ")"
    ).fetchone() == (1,)


def _migrate_from(version: int, connection: sqlite3.Connection) -> None:
    if version == 0:
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
            """
        )
        return
    raise SchemaVersionError(version, "older")
