from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote, urlencode

from .collect.ratelimit import classify
from .collect.search import Candidate, CollectResult, Transport, collect
from .pipeline.run import FetchedFiles
from .ports import RepositoryMetadata
from .scoring import ScoreInputs


class GitHubRepository:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def search(self, query: str, limit: int) -> CollectResult:
        result = collect(
            query,
            transport=self.transport,
            pages=(limit + 99) // 100,
            per_page=min(limit, 100),
        )
        result.candidates = result.candidates[:limit]
        return result

    def metadata(
        self,
        candidate: Candidate,
        at: datetime,
    ) -> RepositoryMetadata:
        repo = quote(candidate.repo, safe="/")
        reasons: list[str] = []

        commits_status, commits_headers, commits_body = self.transport.get(
            f"https://api.github.com/repos/{repo}/commits?"
            + urlencode({"since": (at - timedelta(days=30)).isoformat(), "per_page": 4})
        )
        commits_kind = classify(commits_status, commits_headers)
        if commits_kind == "ok" and commits_status == 200:
            commit_cadence = len(json.loads(commits_body)) >= 4
        else:
            commit_cadence = None
            reasons.append(_metadata_failure(candidate.repo, "commit", commits_status, commits_kind))

        contributors_status, contributors_headers, contributors_body = self.transport.get(
            f"https://api.github.com/repos/{repo}/contributors?per_page=2&anon=1"
        )
        contributors_kind = classify(contributors_status, contributors_headers)
        if contributors_kind == "ok" and contributors_status == 200:
            contributor_count = len(json.loads(contributors_body)) >= 2
        else:
            contributor_count = None
            reasons.append(
                _metadata_failure(
                    candidate.repo,
                    "contributor",
                    contributors_status,
                    contributors_kind,
                )
            )

        license_status, license_headers, _ = self.transport.get(
            f"https://api.github.com/repos/{repo}/license"
        )
        license_kind = classify(license_status, license_headers)
        if license_kind == "ok" and license_status == 200:
            has_license = True
        elif license_status == 404:
            has_license = False
        else:
            has_license = None
            reasons.append(_metadata_failure(candidate.repo, "license", license_status, license_kind))

        return RepositoryMetadata(
            ScoreInputs(commit_cadence, contributor_count, has_license),
            tuple(reasons),
        )


def _metadata_failure(repo: str, request: str, status: int, kind: str) -> str:
    if kind == "rate-limited":
        return f"{repo}: {request} metadata rate limited"
    if kind == "forbidden":
        return f"{repo}: {request} metadata forbidden"
    return f"{repo}: {request} metadata returned HTTP {status}"


class CallableFileReader:
    def __init__(self, read: Callable[[Candidate], FetchedFiles]) -> None:
        self._read = read

    def read(self, candidate: Candidate) -> FetchedFiles:
        return self._read(candidate)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
