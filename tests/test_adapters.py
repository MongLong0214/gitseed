from __future__ import annotations

import json
from datetime import datetime, timezone

from gitseed.adapters import GitHubRepository
from gitseed.collect.search import Candidate
from gitseed.scoring import ScoreInputs


class Transport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        if "/search/repositories?" in url:
            return 200, {}, json.dumps(
                {
                    "items": [
                        {
                            "full_name": "org/repo",
                            "html_url": "https://github.com/org/repo",
                            "stargazers_count": 4,
                            "pushed_at": "2026-07-27T00:00:00Z",
                        }
                    ]
                }
            ).encode()
        if "/commits?" in url:
            return 200, {}, json.dumps([{}, {}, {}, {}]).encode()
        if "/contributors?" in url:
            return 200, {}, json.dumps([{}, {}]).encode()
        return 200, {}, b'{"license":{"spdx_id":"MIT"}}'


class MetadataTransport(Transport):
    def __init__(
        self,
        *,
        commits: tuple[int, dict[str, str], bytes] = (200, {}, b"[{}, {}, {}, {}]"),
        contributors: tuple[int, dict[str, str], bytes] = (200, {}, b"[{}, {}]"),
        license: tuple[int, dict[str, str], bytes] = (200, {}, b'{"license":{"spdx_id":"MIT"}}'),
    ) -> None:
        super().__init__()
        self.commits = commits
        self.contributors = contributors
        self.license = license

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        if "/commits?" in url:
            return self.commits
        if "/contributors?" in url:
            return self.contributors
        return self.license


def test_github_repository_supplies_search_and_score_metadata() -> None:
    # Given: GitHub has one repository with every measured scoring signal.
    transport = Transport()
    repository = GitHubRepository(transport)

    # When: the repository port searches and reads metadata at a fixed instant.
    collected = repository.search("small tools", 1)
    metadata = repository.metadata(
        collected.candidates[0],
        datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    # Then: the domain receives its existing types, not HTTP payloads.
    assert collected.complete
    assert metadata.score_inputs == ScoreInputs(True, True, True)
    assert metadata.incomplete_because == ()
    assert any("since=2026-06-27" in url for url in transport.urls)


def test_metadata_403_with_an_exhausted_quota_is_rate_limited() -> None:
    # Given: only the commit request reaches an exhausted GitHub quota.
    transport = MetadataTransport(
        commits=(403, {"X-RateLimit-Remaining": "0"}, b""),
    )

    # When: metadata reads every scoring input in its established order.
    metadata = GitHubRepository(transport).metadata(
        Candidate("org/repo", "org", "", 0, ""),
        datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    # Then: the endpoint and rate-limit remedy survive without skipping requests.
    assert metadata.score_inputs == ScoreInputs(None, True, True)
    assert metadata.incomplete_because == ("org/repo: commit metadata rate limited",)
    assert ["commits" in url or "contributors" in url or "license" in url for url in transport.urls] == [True, True, True]


def test_metadata_403_with_budget_remaining_is_forbidden() -> None:
    # Given: GitHub rejects the contributor request while quota remains.
    transport = MetadataTransport(
        contributors=(403, {"X-RateLimit-Remaining": "27"}, b""),
    )

    # When: metadata records the failed input.
    metadata = GitHubRepository(transport).metadata(
        Candidate("org/repo", "org", "", 0, ""),
        datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    # Then: permissions remain distinct from rate limiting.
    assert metadata.score_inputs == ScoreInputs(True, None, True)
    assert metadata.incomplete_because == ("org/repo: contributor metadata forbidden",)


def test_metadata_429_is_rate_limited_and_a_missing_license_is_absent() -> None:
    # Given: contributors are throttled and this valid repository has no license.
    transport = MetadataTransport(
        contributors=(429, {}, b""),
        license=(404, {}, b""),
    )

    # When: metadata processes all three responses.
    metadata = GitHubRepository(transport).metadata(
        Candidate("org/repo", "org", "", 0, ""),
        datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    # Then: 429 names its endpoint and 404 remains a scored absence.
    assert metadata.score_inputs == ScoreInputs(True, None, False)
    assert metadata.incomplete_because == ("org/repo: contributor metadata rate limited",)
