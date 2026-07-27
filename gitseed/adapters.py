from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote, urlencode

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

        commits_status, _, commits_body = self.transport.get(
            f"https://api.github.com/repos/{repo}/commits?"
            + urlencode({"since": (at - timedelta(days=30)).isoformat(), "per_page": 4})
        )
        if commits_status == 200:
            commit_cadence = len(json.loads(commits_body)) >= 4
        else:
            commit_cadence = None
            reasons.append(f"{candidate.repo}: commit metadata returned HTTP {commits_status}")

        contributors_status, _, contributors_body = self.transport.get(
            f"https://api.github.com/repos/{repo}/contributors?per_page=2&anon=1"
        )
        if contributors_status == 200:
            contributor_count = len(json.loads(contributors_body)) >= 2
        else:
            contributor_count = None
            reasons.append(
                f"{candidate.repo}: contributor metadata returned HTTP {contributors_status}"
            )

        license_status, _, _ = self.transport.get(
            f"https://api.github.com/repos/{repo}/license"
        )
        if license_status == 200:
            has_license = True
        elif license_status == 404:
            has_license = False
        else:
            has_license = None
            reasons.append(f"{candidate.repo}: license metadata returned HTTP {license_status}")

        return RepositoryMetadata(
            ScoreInputs(commit_cadence, contributor_count, has_license),
            tuple(reasons),
        )


class CallableFileReader:
    def __init__(self, read: Callable[[Candidate], FetchedFiles]) -> None:
        self._read = read

    def read(self, candidate: Candidate) -> FetchedFiles:
        return self._read(candidate)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
