"""GitHub search that reports truncation instead of hiding it."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import urlencode
from warnings import warn

from .ratelimit import MAX_WAIT_SECONDS, RateLimit, classify, parse


@dataclass(frozen=True)
class Candidate:
    repo: str
    owner: str
    html_url: str
    stars: int
    pushed_at: str


@dataclass
class CollectResult:
    """Candidates, and an honest account of why there are not more.

    `complete` is the field that matters. The seed has no equivalent, so a
    caller cannot tell a search that found twelve repositories from one that
    found twelve and was then cut off.
    """

    candidates: list[Candidate] = field(default_factory=list)
    complete: bool = True
    stopped_because: str | None = None
    pages_fetched: int = 0
    total_count: int | None = None
    search_incomplete: bool = False

    @property
    def complete_for_search(self) -> bool:
        return (
            self.complete
            and not self.search_incomplete
            and (self.total_count is None or len(self.candidates) >= self.total_count)
        )


class Transport(Protocol):
    """The seam a fake slots into. Tests never touch the network."""

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]: ...


class UrllibTransport:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token
        self.timeout = timeout

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        return self.request("GET", url)

    def request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            # An HTTPError is a response, not an absence of one — the rate limit
            # headers live on it and are the whole reason we are here.
            return error.code, dict(error.headers), error.read()


def _parse_items(items: object) -> list[Candidate]:
    out: list[Candidate] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        full = item.get("full_name")
        if not isinstance(full, str) or "/" not in full:
            continue
        out.append(
            Candidate(
                repo=full,
                owner=full.split("/", 1)[0],
                html_url=str(item.get("html_url", "")),
                stars=int(item.get("stargazers_count", 0) or 0),
                pushed_at=str(item.get("pushed_at", "")),
            )
        )
    return out


def _parse_response(body: bytes) -> tuple[list[Candidate], bool, int | None]:
    payload = json.loads(body or b"{}")
    if not isinstance(payload, dict):
        return [], False, None
    total_count = payload.get("total_count")
    return (
        _parse_items(payload.get("items", [])),
        payload.get("incomplete" "_results") is True,
        total_count if isinstance(total_count, int) and not isinstance(total_count, bool) else None,
    )


def collect(
    query: str,
    *,
    transport: Transport,
    pages: int = 1,
    per_page: int = 30,
    wait: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> CollectResult:
    """Fetch up to `pages` pages, stopping loudly.

    `wait=False` is the default because sleeping for up to an hour inside a
    library call is a decision for the caller, not for us. Either way the result
    says what happened.
    """
    result = CollectResult()
    seen: set[str] = set()

    for page in range(1, pages + 1):
        url = "https://api.github.com/search/repositories?" + urlencode(
            {"q": query, "per_page": per_page, "page": page}
        )
        status, headers, body = transport.get(url)
        kind = classify(status, headers)

        if kind == "rate-limited":
            limit: RateLimit = parse(headers)
            reset_seconds = limit.seconds_until_reset(now())
            stopped_because = (
                f"rate limited after {result.pages_fetched} page(s); resets in {reset_seconds:.0f}s"
            )
            if reset_seconds > MAX_WAIT_SECONDS:
                stopped_because += f"; retry wait capped at {MAX_WAIT_SECONDS:.0f}s"
            if not wait:
                result.complete = False
                result.stopped_because = stopped_because
                return result
            if reset_seconds > MAX_WAIT_SECONDS:
                warn(
                    f"rate limit wait capped at {MAX_WAIT_SECONDS:.0f}s; "
                    f"server requested {reset_seconds:.0f}s",
                    RuntimeWarning,
                    stacklevel=2,
                )
            sleep(min(reset_seconds, MAX_WAIT_SECONDS))
            status, headers, body = transport.get(url)
            if classify(status, headers) != "ok":
                result.complete = False
                result.stopped_because = f"{stopped_because}; still rate limited after waiting"
                return result

        elif kind == "forbidden":
            result.complete = False
            result.stopped_because = "forbidden — a permissions problem, not a budget one"
            return result

        elif kind == "error":
            result.complete = False
            result.stopped_because = f"HTTP {status}"
            return result

        items, search_incomplete, total_count = _parse_response(body)
        for candidate in items:
            if candidate.repo not in seen:
                seen.add(candidate.repo)
                result.candidates.append(candidate)
        result.pages_fetched += 1
        result.search_incomplete = result.search_incomplete or search_incomplete
        if total_count is not None:
            result.total_count = total_count

        if len(items) < per_page:
            break  # last page

    return result
