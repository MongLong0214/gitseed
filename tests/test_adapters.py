from __future__ import annotations

import json
from datetime import datetime, timezone

from gitseed.adapters import GitHubRepository
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
