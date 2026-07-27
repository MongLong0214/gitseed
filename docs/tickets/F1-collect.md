# F1 — Collect GitHub candidates (keystone)

> This ticket was omitted in Phase 4 and written after implementation; the content below truthfully records code and tests already implemented.

F1 collects GitHub repository candidates and, when a response is partial, does not hide that fact;
it passes it to the next stage through `CollectResult.complete` and `stopped_because`.

## Implementation

**`gitseed/collect/ratelimit.py`**

```python
@dataclass(frozen=True)
class RateLimit:
    remaining: int | None
    reset_at: int | None
    limit: int | None

    @property
    def exhausted(self) -> bool: ...

    def seconds_until_reset(self, now: float | None = None) -> float: ...

def parse(headers: Mapping[str, str]) -> RateLimit: ...
def classify(status: int, headers: Mapping[str, str]) -> str: ...
```

`classify` returns one of `"ok"`, `"rate-limited"`, `"forbidden"`, or `"error"`.
For a 403, it uses the remaining quota in the headers and `Retry-After` to distinguish
rate-limit exhaustion from a permissions error.

**`gitseed/collect/search.py`**

```python
@dataclass(frozen=True)
class Candidate:
    repo: str
    owner: str
    html_url: str
    stars: int
    pushed_at: str

@dataclass
class CollectResult:
    candidates: list[Candidate] = field(default_factory=list)
    complete: bool = True
    stopped_because: str | None = None
    pages_fetched: int = 0

class Transport(Protocol):
    def get(self, url: str) -> tuple[int, dict[str, str], bytes]: ...

class UrllibTransport:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None: ...
    def get(self, url: str) -> tuple[int, dict[str, str], bytes]: ...
    def request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]: ...

def collect(
    query: str,
    *,
    transport: Transport,
    pages: int = 1,
    per_page: int = 30,
    wait: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> CollectResult: ...
```

The internal parser `_parse_items(body: bytes) -> list[Candidate]` skips items with an invalid
`full_name`. `collect` removes duplicate repositories across pages and stops on a short page.
When the rate limit is exhausted, it returns an incomplete result without waiting by default;
only with `wait=True` does it wait until reset and retry once.

## Current tests

`tests/test_collect.py` uses `FakeTransport` instead of a network and includes the following
tests.

- `TestRateLimitParsing`: `test_reads_the_budget`,
  `test_header_casing_does_not_matter`,
  `test_a_missing_header_is_unknown_not_zero`,
  `test_a_garbage_value_is_unknown_not_a_crash`,
  `test_reset_is_never_zero_or_negative`
- `TestForbiddenVersusExhausted`:
  `test_403_with_an_empty_budget_is_a_rate_limit`,
  `test_403_with_retry_after_is_a_rate_limit`,
  `test_403_with_budget_left_is_a_permissions_problem`,
  `test_429_is_always_a_rate_limit`
- `TestTruncationIsReported`: `test_a_rate_limit_marks_the_result_incomplete`,
  `test_partial_results_are_kept_and_flagged`,
  `test_a_permissions_failure_says_so_and_does_not_wait`,
  `test_an_unexpected_status_is_reported_with_its_code`
- `TestWaiting`: `test_wait_sleeps_until_the_reset_then_retries`,
  `test_still_limited_after_waiting_gives_up_rather_than_looping`
- `TestPaging`: `test_a_search_query_is_percent_encoded_and_round_trips`,
  `test_a_short_page_ends_the_walk`,
  `test_duplicates_across_pages_are_dropped`,
  `test_a_malformed_item_is_skipped_not_fatal`
- `test_any_2xx_counts_as_ok` (`200`, `201`, `299`)

In `tests/test_pipeline.py`,
`test_a_truncated_collection_makes_the_whole_run_incomplete` verifies that F1's incomplete state
propagates through the entire pipeline.

## AC (mechanically decided by current code)

- [x] Read rate-limit information regardless of header-name case; treat missing or non-integer
      values as `None`.
- [x] When a reset time exists, `seconds_until_reset` returns at least 1 second.
- [x] Classify every 2xx and 429; distinguish rate-limit-exhausted 403 from forbidden 403.
- [x] URL-encode the search query and reflect page count and page size in the request.
- [x] Stop on a short page, remove duplicates across pages, and do not fail the full collection
      on an invalid item.
- [x] Preserve candidates collected before rate-limit exhaustion while returning `complete=False`,
      the stop reason, and the number of pages fetched.
- [x] Do not wait on a permissions error; record the status code for an unexpected HTTP status.
- [x] `wait=True` waits until reset and retries only once, then stops if still limited.
- [x] The AC above runs without a network using `FakeTransport`.

## <a id="remaining-live-evidence-issue-6"></a> Remaining live evidence — issue #6

The 403 permissions-error branch has been verified only with injected responses. To close issue #6,
retain the following evidence together from an actual GitHub response.

- The status code is 403, `X-RateLimit-Remaining` is greater than 0, and `Retry-After` is absent.
- The result is `complete=False`, and the stop reason points to a permissions problem.
- The process does not wait for the rate-limit reset or retry the same request.

Do not record the token. From the original response, retain only the status code and headers
needed for the decisions above.
