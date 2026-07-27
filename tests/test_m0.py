from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from gitseed.m0 import FeatureVector, RecordedSample, analyze, features_at


def _git(repo: Path, *args: str, when: str, email: str = "one@example.com") -> None:
    environment = os.environ | {
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_NAME": "M0 Test",
        "GIT_COMMITTER_NAME": "M0 Test",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, env=environment, capture_output=True)


def _commit(repo: Path, path: str, content: str, when: str, email: str = "one@example.com") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(repo, "add", path, when=when, email=email)
    _git(repo, "commit", "-m", path, when=when, email=email)


def test_features_change_when_cutoff_moves_past_later_history(tmp_path: Path) -> None:
    # Given: the useful project structure arrived after the original cutoff.
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", when="2025-01-01T00:00:00+00:00")
    _commit(repo, "README.md", "tiny\n", "2025-01-02T00:00:00+00:00")
    _commit(repo, "tests/test_x.py", "pass\n", "2025-05-02T00:00:00+00:00", "two@example.com")
    _commit(repo, ".github/workflows/ci.yml", "on: push\n", "2025-05-03T00:00:00+00:00")
    _commit(repo, "LICENSE", "MIT\n", "2025-05-04T00:00:00+00:00")
    _commit(repo, "pyproject.toml", "[project]\nname='x'\n", "2025-05-05T00:00:00+00:00")

    # When: the cutoff moves from three months to after those commits.
    original = features_at(repo, datetime(2025, 4, 1, tzinfo=timezone.utc))
    moved = features_at(repo, datetime(2025, 6, 1, tzinfo=timezone.utc))

    # Then: at least one feature changes; using HEAD would make this fail.
    assert original is not None
    assert moved is not None
    assert original != moved
    assert not original.has_tests
    assert moved.has_tests


def test_fixture_analysis_is_deterministic_across_three_runs() -> None:
    # Given: the collected fixture is the complete input to offline analysis.
    fixture = json.loads((Path(__file__).parent / "fixtures/m0/samples.json").read_text())
    samples = tuple(
        RecordedSample(
            repo=row["repo"],
            category=row["category"],
            stars=row["stars"],
            features=FeatureVector(**row["features"]),
        )
        for row in fixture["accessible"]
    )

    # When: the registered calculation runs three times with that input.
    results = tuple(analyze(samples) for _ in range(3))

    # Then: all observable analysis values are identical.
    assert results[0] == results[1] == results[2]
