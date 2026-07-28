"""T-202 — collection that refuses to go quiet.

The seed has no rate-limit handling at all: `rate`, `429`, `403`, `X-RateLimit`
and `backoff` return zero matches across its source. A search that hits the
limit there returns short and says nothing, and the caller writes a smaller
world into the database believing it is the whole one.

Every test here uses a fake transport. None of them touch the network.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from gitseed.collect.ratelimit import classify, parse
from gitseed.collect.search import collect


def page(names: list[str]) -> bytes:
    return json.dumps(
        {
            "items": [
                {
                    "full_name": n,
                    "html_url": f"https://github.com/{n}",
                    "stargazers_count": 7,
                    "pushed_at": "2026-07-01T00:00:00Z",
                }
                for n in names
            ]
        }
    ).encode()


class FakeTransport:
    """Replays a scripted sequence of responses and records the URLs asked for."""

    def __init__(self, responses: list[tuple[int, dict[str, str], bytes]]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self.urls.append(url)
        return self.responses[min(len(self.urls) - 1, len(self.responses) - 1)]


OK: dict[str, str] = {"X-RateLimit-Remaining": "29"}
EXHAUSTED = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"}


class TestRateLimitParsing:
    def test_reads_the_budget(self) -> None:
        limit = parse({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"})
        assert limit.remaining == 0
        assert limit.exhausted

    def test_header_casing_does_not_matter(self) -> None:
        # A proxy that lowercases headers must not look like unlimited budget.
        assert parse({"x-ratelimit-remaining": "5"}).remaining == 5

    def test_a_missing_header_is_unknown_not_zero(self) -> None:
        limit = parse({})
        assert limit.remaining is None
        assert not limit.exhausted

    def test_a_garbage_value_is_unknown_not_a_crash(self) -> None:
        assert parse({"X-RateLimit-Remaining": "soon"}).remaining is None

    def test_reset_is_never_zero_or_negative(self) -> None:
        # A clock a second fast would otherwise give a zero sleep and a hot loop.
        limit = parse({"X-RateLimit-Reset": "100"})
        assert limit.seconds_until_reset(now=1_000_000) >= 1.0


class TestForbiddenVersusExhausted:
    """GitHub says 403 for both. Confusing them means either waiting an hour for
    a permissions error or hammering an API that asked us to stop."""

    def test_403_with_an_empty_budget_is_a_rate_limit(self) -> None:
        assert classify(403, EXHAUSTED) == "rate-limited"

    def test_403_with_retry_after_is_a_rate_limit(self) -> None:
        assert classify(403, {"Retry-After": "60"}) == "rate-limited"

    def test_403_with_budget_left_is_a_permissions_problem(self) -> None:
        assert classify(403, {"X-RateLimit-Remaining": "27"}) == "forbidden"

    def test_429_is_always_a_rate_limit(self) -> None:
        assert classify(429, {}) == "rate-limited"

    def test_403_forbidden_is_never_classified_the_same_as_404_not_found(self) -> None:
        """Issue #6: GitHub hides a repository we cannot see behind a 404, and
        only ever answers 403 for one we can see but lack permission on -- the
        two are never the same status, so `classify` must never collapse them
        into the same outcome regardless of the headers attached."""
        budget_left = {"X-RateLimit-Remaining": "4900"}
        assert classify(403, budget_left) == "forbidden"
        assert classify(404, budget_left) != "forbidden"
        assert classify(404, budget_left) != classify(403, budget_left)


class TestTruncationIsReported:
    def test_search_timeout_is_recorded_alongside_its_reported_total(self) -> None:
        # Given: GitHub returns candidates from a search it says timed out.
        body = json.dumps(
            {
                "incomplete_results": True,
                "total_count": 4,
                "items": [
                    {
                        "full_name": "a/one",
                        "html_url": "https://github.com/a/one",
                        "stargazers_count": 7,
                        "pushed_at": "2026-07-01T00:00:00Z",
                    }
                ],
            }
        ).encode()

        # When: collection accepts the returned candidates.
        result = collect("q", transport=FakeTransport([(200, OK, body)]))

        # Then: the partial search and its count survive beside the candidate.
        assert result.search_incomplete is True
        assert result.total_count == 4
        assert result.complete_for_search is False

    def test_reported_total_larger_than_retrieved_is_partial(self) -> None:
        # Given: GitHub completes the response but its count exceeds the candidates returned.
        body = json.dumps(
            {
                "incomplete_results": False,
                "total_count": 2,
                "items": [
                    {
                        "full_name": "a/one",
                        "html_url": "https://github.com/a/one",
                        "stargazers_count": 7,
                        "pushed_at": "2026-07-01T00:00:00Z",
                    }
                ],
            }
        ).encode()

        # When: the requested page has been collected.
        result = collect("q", transport=FakeTransport([(200, OK, body)]))

        # Then: a complete transport response is not mistaken for a complete candidate set.
        assert result.complete is True
        assert result.total_count == 2
        assert result.complete_for_search is False

    def test_a_rate_limit_marks_the_result_incomplete(self) -> None:
        result = collect("q", transport=FakeTransport([(403, EXHAUSTED, b"{}")]))
        assert not result.complete
        assert "rate limited" in (result.stopped_because or "")

    def test_partial_results_are_kept_and_flagged(self) -> None:
        transport = FakeTransport(
            [(200, OK, page(["a/one", "a/two"])), (403, EXHAUSTED, b"{}")]
        )
        result = collect("q", transport=transport, pages=3, per_page=2)
        assert [c.repo for c in result.candidates] == ["a/one", "a/two"]
        assert not result.complete
        assert result.pages_fetched == 1

    def test_a_permissions_failure_says_so_and_does_not_wait(self) -> None:
        transport = FakeTransport([(403, {"X-RateLimit-Remaining": "27"}, b"{}")])
        slept: list[float] = []
        result = collect("q", transport=transport, wait=True, sleep=slept.append)
        assert not result.complete
        assert "permissions" in (result.stopped_because or "")
        assert slept == []

    def test_an_unexpected_status_is_reported_with_its_code(self) -> None:
        result = collect("q", transport=FakeTransport([(500, {}, b"")]))
        assert not result.complete
        assert "500" in (result.stopped_because or "")

    def test_a_live_captured_forbidden_response_is_a_permissions_problem_not_a_wait(self) -> None:
        """Issue #6 -- real evidence, not reconstructed.

        Captured 2026-07-28 through gitseed's own `UrllibTransport`, live against
        GET repos/torvalds/linux/collaborators with a valid token scoped for
        something else: HTTP 403, budget in the thousands remaining, no
        Retry-After header, body `{"message": "Must have push access to view
        repository collaborators.", ..., "status": "403"}`. The header values
        below are that response's real X-RateLimit-* trio; only the token itself
        (never present in a response) is withheld.
        """
        real_forbidden_headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4900",
            "X-RateLimit-Reset": "1785199522",
        }
        real_forbidden_body = (
            b'{"message": "Must have push access to view repository collaborators.", '
            b'"documentation_url": "https://docs.github.com/rest/collaborators/collaborators'
            b'#list-repository-collaborators", "status": "403"}'
        )
        transport = FakeTransport([(403, real_forbidden_headers, real_forbidden_body)])
        slept: list[float] = []
        result = collect("q", transport=transport, wait=True, sleep=slept.append)
        assert not result.complete
        assert "permissions" in (result.stopped_because or "")
        assert slept == []  # a permissions error is never waited out, even with wait=True
        assert len(transport.urls) == 1  # and never retried


class TestWaiting:
    def test_wait_sleeps_until_the_reset_then_retries(self) -> None:
        transport = FakeTransport(
            [(403, EXHAUSTED, b"{}"), (200, OK, page(["a/one"]))]
        )
        slept: list[float] = []
        result = collect(
            "q", transport=transport, wait=True, sleep=slept.append, now=lambda: 1_999_999_000
        )
        assert result.complete
        assert [c.repo for c in result.candidates] == ["a/one"]
        assert slept and slept[0] > 0

    def test_still_limited_after_waiting_gives_up_rather_than_looping(self) -> None:
        transport = FakeTransport([(403, EXHAUSTED, b"{}")])
        slept: list[float] = []
        result = collect("q", transport=transport, wait=True, sleep=slept.append)
        assert not result.complete
        assert len(slept) == 1


class TestPaging:
    def test_a_search_query_is_percent_encoded_and_round_trips(self) -> None:
        # Given: GitHub syntax uses reserved characters alongside a separating space.
        transport = FakeTransport([(200, OK, page([]))])
        query = "language:python stars:>100+useful"
        # When: collection builds its first request.
        collect(query, transport=transport, per_page=3)
        # Then: the wire URL is valid and decodes to the original GitHub query.
        assert transport.urls == [
            "https://api.github.com/search/repositories?q=language%3Apython+stars%3A%3E100%2Buseful&per_page=3&page=1"
        ]
        assert parse_qs(urlparse(transport.urls[0]).query)["q"] == [query]

    def test_a_short_page_ends_the_walk(self) -> None:
        transport = FakeTransport([(200, OK, page(["a/one"]))])
        result = collect("q", transport=transport, pages=5, per_page=30)
        assert result.pages_fetched == 1
        assert len(transport.urls) == 1

    def test_duplicates_across_pages_are_dropped(self) -> None:
        transport = FakeTransport([(200, OK, page(["a/one", "a/two"]))])
        result = collect("q", transport=transport, pages=2, per_page=2)
        assert [c.repo for c in result.candidates] == ["a/one", "a/two"]

    def test_a_malformed_item_is_skipped_not_fatal(self) -> None:
        body = json.dumps({"items": [{"full_name": "nope"}, {"full_name": "a/ok"}]}).encode()
        result = collect("q", transport=FakeTransport([(200, OK, body)]))
        assert [c.repo for c in result.candidates] == ["a/ok"]


@pytest.mark.parametrize("status", [200, 201, 299])
def test_any_2xx_counts_as_ok(status: int) -> None:
    assert classify(status, {}) == "ok"
