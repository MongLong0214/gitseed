from __future__ import annotations

import calendar
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Sequence

FEATURE_NAMES: Final = (
    "has_tests",
    "has_ci",
    "has_license",
    "commit_cadence_30d",
    "contributor_count",
    "readme_bytes",
    "manifest_present",
)
MANIFESTS: Final = {
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Gemfile",
}


@dataclass(frozen=True)
class FeatureVector:
    has_tests: bool
    has_ci: bool
    has_license: bool
    commit_cadence_30d: bool
    contributor_count: bool
    readme_bytes: bool
    manifest_present: bool

    def values(self) -> tuple[bool, ...]:
        return (
            self.has_tests,
            self.has_ci,
            self.has_license,
            self.commit_cadence_30d,
            self.contributor_count,
            self.readme_bytes,
            self.manifest_present,
        )

    def score(self) -> int:
        return sum(self.values())


@dataclass(frozen=True)
class RecordedSample:
    repo: str
    category: str
    stars: int
    features: FeatureVector


@dataclass(frozen=True)
class Analysis:
    sample_count: int
    breakout_count: int
    auc: float | None
    contributions: tuple[tuple[str, float | None], ...]


def three_month_cutoff(created_at: datetime) -> datetime:
    """Return the UTC calendar-date cutoff used by the preregistration."""
    month = created_at.month + 3
    year = created_at.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(created_at.day, calendar.monthrange(year, month)[1])
    return created_at.replace(year=year, month=month, day=day)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


def features_at(repo: Path, cutoff: datetime) -> FeatureVector | None:
    """Compute the seven preregistered features at one historical Git cutoff."""
    when = cutoff.isoformat()
    sha = _git(repo, "rev-list", "-1", f"--before={when}", "HEAD").decode().strip()
    if not sha:
        return None
    paths = tuple(
        path.decode()
        for path in _git(repo, "ls-tree", "-rz", "--name-only", sha).split(b"\0")
        if path
    )
    root_paths = tuple(path for path in paths if "/" not in path)
    readmes = tuple(path for path in root_paths if path.casefold().startswith("readme"))
    readme_total = sum(len(_git(repo, "show", f"{sha}:{path}")) for path in readmes)
    recent = _git(
        repo,
        "log",
        "--format=%H",
        f"--after={cutoff.timestamp() - 30 * 24 * 60 * 60}",
        f"--before={when}",
        sha,
    ).splitlines()
    authors = {
        email
        for email in _git(repo, "log", "--format=%ae", f"--before={when}", sha).splitlines()
        if email
    }
    return FeatureVector(
        has_tests=any(part.casefold() in {"test", "tests"} for path in paths for part in path.split("/")),
        has_ci=any(
            path.startswith(".github/workflows/")
            or path in {".gitlab-ci.yml", "Jenkinsfile"}
            for path in paths
        ),
        has_license=any(path.casefold().startswith("license") for path in root_paths),
        commit_cadence_30d=len(recent) >= 4,
        contributor_count=len(authors) >= 2,
        readme_bytes=readme_total >= 1_000,
        manifest_present=any(path in MANIFESTS for path in paths),
    )


def _auc(scores: Sequence[int], labels: Sequence[bool]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ranks = [0.0] * len(scores)
    ordered = sorted(range(len(scores)), key=scores.__getitem__)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and scores[ordered[end]] == scores[ordered[start]]:
            end += 1
        rank = (start + 1 + end) / 2
        for index in ordered[start:end]:
            ranks[index] = rank
        start = end
    positive_rank_sum = sum(rank for rank, label in zip(ranks, labels) if label)
    return (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def analyze(samples: Sequence[RecordedSample], breakout_stars: int = 100) -> Analysis:
    """Evaluate the preregistered unit-weight score and leave-one-feature-out drops."""
    values = tuple(sample.features.values() for sample in samples)
    labels = tuple(sample.stars >= breakout_stars for sample in samples)
    auc = _auc(tuple(sum(row) for row in values), labels)
    contributions = tuple(
        (
            name,
            None if auc is None else auc - (_auc(tuple(sum(row) - row[index] for row in values), labels) or 0),
        )
        for index, name in enumerate(FEATURE_NAMES)
    )
    return Analysis(len(samples), sum(labels), auc, contributions)
