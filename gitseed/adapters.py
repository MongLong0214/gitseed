from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote, urlencode

from .collect.ratelimit import classify
from .collect.search import Candidate, CollectResult, Transport, collect
from .evidence import ClaimBasis
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
        return replace(result, candidates=result.candidates[:limit])

    def metadata(
        self,
        candidate: Candidate,
        at: datetime,
    ) -> RepositoryMetadata:
        repo = quote(candidate.repo, safe="/")
        reasons: list[str] = []

        commit_count = self._count_metadata(
            f"https://api.github.com/repos/{repo}/commits?"
            + urlencode({"since": (at - timedelta(days=30)).isoformat(), "per_page": 100}),
            candidate.repo,
            "commit",
            reasons,
        )

        contributor_count = self._count_metadata(
            f"https://api.github.com/repos/{repo}/contributors?per_page=100&anon=1",
            candidate.repo,
            "contributor",
            reasons,
        )

        license_status, license_headers, license_body = self.transport.get(
            f"https://api.github.com/repos/{repo}/license"
        )
        license_kind = classify(license_status, license_headers)
        if license_kind == "ok" and license_status == 200:
            license = json.loads(license_body).get("license")
            license_basis = ClaimBasis.DETERMINISTIC
        elif license_status == 404:
            license = None
            license_basis = ClaimBasis.DETERMINISTIC
        else:
            license = None
            license_basis = ClaimBasis.ABSENT
            reasons.append(_metadata_failure(candidate.repo, "license", license_status, license_kind))

        return RepositoryMetadata(
            ScoreInputs.observed(
                commit_count,
                contributor_count,
                license,
                commit_count_basis=(
                    ClaimBasis.DETERMINISTIC
                    if commit_count is not None
                    else ClaimBasis.ABSENT
                ),
                contributor_count_basis=(
                    ClaimBasis.DETERMINISTIC
                    if contributor_count is not None
                    else ClaimBasis.ABSENT
                ),
                license_basis=license_basis,
            ),
            tuple(reasons),
        )

    def _count_metadata(
        self,
        url: str,
        repo: str,
        request: str,
        reasons: list[str],
    ) -> int | None:
        total = 0
        while url:
            status, headers, body = self.transport.get(url)
            kind = classify(status, headers)
            if kind != "ok" or status != 200:
                reasons.append(_metadata_failure(repo, request, status, kind))
                return None
            total += len(json.loads(body))
            url = _next_page(headers)
        return total


def _metadata_failure(repo: str, request: str, status: int, kind: str) -> str:
    if kind == "rate-limited":
        return f"{repo}: {request} metadata rate limited"
    if kind == "forbidden":
        return f"{repo}: {request} metadata forbidden"
    return f"{repo}: {request} metadata returned HTTP {status}"


def _next_page(headers: dict[str, str]) -> str | None:
    link = next((value for name, value in headers.items() if name.lower() == "link"), "")
    for item in link.split(","):
        url, *parameters = item.split(";")
        if any(parameter.strip() == 'rel="next"' for parameter in parameters):
            return url.strip().removeprefix("<").removesuffix(">")
    return None


class CallableFileReader:
    def __init__(self, read: Callable[[Candidate], FetchedFiles]) -> None:
        self._read = read

    def read(self, candidate: Candidate) -> FetchedFiles:
        return self._read(candidate)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
