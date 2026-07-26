"""Rate limit accounting, kept apart from the HTTP call so it can be tested.

The seed has none of this — `rate`, `429`, `403`, `X-RateLimit` and `backoff`
all return zero matches in its source. A search that hits the limit there comes
back short and says nothing, and a collector that silently returns fewer
candidates than it found is worse than one that fails: the caller records a
smaller world and never learns it was truncated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RateLimit:
    """What the response headers said about the budget."""

    remaining: int | None
    reset_at: int | None
    limit: int | None

    @property
    def exhausted(self) -> bool:
        return self.remaining is not None and self.remaining <= 0

    def seconds_until_reset(self, now: float | None = None) -> float:
        """Never negative, and never zero when a reset is pending.

        A clock skewed a second fast would otherwise produce a zero-length sleep
        and a hot retry loop against an API that is already refusing us.
        """
        if self.reset_at is None:
            return 0.0
        current = time.time() if now is None else now
        return max(1.0, self.reset_at - current)


def parse(headers: Mapping[str, str]) -> RateLimit:
    """Reads the budget from response headers, case-insensitively.

    Header casing is not guaranteed across proxies, and a collector that reads
    `X-RateLimit-Remaining` but not `x-ratelimit-remaining` believes it has
    unlimited budget behind one.
    """
    lowered = {key.lower(): value for key, value in headers.items()}

    def number(name: str) -> int | None:
        raw = lowered.get(name)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    return RateLimit(
        remaining=number("x-ratelimit-remaining"),
        reset_at=number("x-ratelimit-reset"),
        limit=number("x-ratelimit-limit"),
    )


def classify(status: int, headers: Mapping[str, str]) -> str:
    """`ok` | `rate-limited` | `forbidden` | `error`.

    GitHub returns 403 both for "you are out of budget" and for "you may not do
    this". Treating them the same means either waiting an hour for a permission
    error or hammering an API that asked us to stop, so they are separated by
    the headers rather than by the status alone.
    """
    if 200 <= status < 300:
        return "ok"
    if status == 429:
        return "rate-limited"
    if status == 403:
        limit = parse(headers)
        lowered = {k.lower(): v for k, v in headers.items()}
        if limit.exhausted or "retry-after" in lowered:
            return "rate-limited"
        return "forbidden"
    return "error"
