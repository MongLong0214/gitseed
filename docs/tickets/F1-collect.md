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

## <a id="remaining-live-evidence-issue-6"></a> Live evidence — issue #6 (closed)

Closed 2026-07-28. The 403 permissions-error branch had been verified only with injected
responses. The evidence below is from an actual GitHub response, captured live through
gitseed's own `UrllibTransport.get()` — not reconstructed.

**Capture.** `GET repos/torvalds/linux/collaborators` and `GET
repos/torvalds/linux/actions/secrets` — a repository the token can see, on two endpoints its
scope does not cover. GitHub hides a repository you cannot see at all behind a 404, never a
403, so a repo we cannot see does not exercise this branch; a repo we can see but lack
permission on does.

- Status: `403`
- `X-RateLimit-Remaining`: `4900` / `4982` (budget left, of a `5000` limit)
- `Retry-After`: absent
- Body: `{"message": "Must have push access to view repository collaborators.", ...,
  "status": "403"}`

No token was recorded; only the status, the `X-RateLimit-*` trio, and the body are kept.

**Result.** Feeding that captured response into `classify()` returns `"forbidden"`. Feeding
it into `collect()` (with `wait=True`, to make the negative provable) returns
`CollectResult(complete=False, stopped_because="forbidden — a permissions problem, not a
budget one", candidates=[])`; `sleep` was never called and the transport was called exactly
once — no wait, no retry. `gitseed/cli.py`'s file-fetch mapping (`_github_response_error`)
turns the same response into `FileFetchError("GitHub access is forbidden; waiting will not
help")`, distinct from the rate-limited path's "quota resets at ..." / "retry at ..."
messages verified live in issue T-209 (commit 874bde0).

**ABSENT, not false, not clean.** Carried through `pipeline.run.run()`, a forbidden read
produces `Reviewed(severity="unknown", screening_basis=ClaimBasis.ABSENT, score=None,
withheld="files could not be read (...)")` — the same shape F11 uses for any unreadable
source, never the shape of a clean scan (`severity="none"`,
`screening_basis=ClaimBasis.DETERMINISTIC`). Run through `execute()` and the CLI, the run
artifact and `gitseed explain` both carry it: `security coverage: absent (0 files)`,
`--json`'s `"severity": "unknown"` with a non-null `"withheld"`, and exit code `2`.

Confirmed by mutation: changing `severity="unknown"` to `severity="none"` in the
`FileFetchError` branch of `gitseed/pipeline/run.py` passed the full suite that existed
before this ticket closed — nothing checked that specific branch's severity value (the
sibling generic-`Exception` branch was already covered by
`test_unreadable_source_is_absent_not_a_clean_security_claim`, but the `FileFetchError`
branch a real 403 actually raises was not). `tests/test_pipeline.py::test_a_forbidden_resource_is_absent_not_a_clean_or_false_result`
and the now-strengthened `tests/test_cli.py::test_github_names_forbidden_access_as_not_waitable`
both catch it.

**Three-way distinction.** 404 not-found, 403 forbidden, and 403 rate-limited are all kept
apart in code and in output:

| case | status | key header(s) | `classify()` | message |
|---|---|---|---|---|
| not-found | 404 | — | `"error"` | `GitHub returned HTTP 404` |
| forbidden | 403 | budget left, no `Retry-After` | `"forbidden"` | `GitHub access is forbidden; waiting will not help` |
| rate-limited (exhausted) | 403 | `X-RateLimit-Remaining: 0` | `"rate-limited"` | `GitHub rate limit exhausted; quota resets at ...` |
| rate-limited (secondary) | 403 | `Retry-After` present | `"rate-limited"` | `GitHub secondary rate limit; retry at ...` |

**Finding, not a defect in the above.** `classify()`'s taxonomy has four buckets (`ok`,
`rate-limited`, `forbidden`, `error`), not five. A 404 is never confused with either 403
cause — different status code, different `kind` — but it is not given its own `kind`
either; it falls into the same generic `"error"` bucket as a 500 or any other unexpected
status. Nothing downstream can branch on "not found" specifically without re-reading the raw
status code; only the propagated message string (`GitHub returned HTTP {status}`) still
names it. This does not weaken the distinction issue #6 asked about, but it is a real gap if
a future ticket wants not-found-specific handling (e.g., a renamed or deleted repository
treated differently from a transient 500).

Regression tests added, closing the loop:
`tests/test_collect.py::TestForbiddenVersusExhausted::test_403_forbidden_is_never_classified_the_same_as_404_not_found`,
`tests/test_collect.py::TestTruncationIsReported::test_a_live_captured_forbidden_response_is_a_permissions_problem_not_a_wait`
(uses the real captured header and body values above),
`tests/test_pipeline.py::test_a_forbidden_resource_is_absent_not_a_clean_or_false_result`, and
a strengthened `tests/test_cli.py::test_github_names_forbidden_access_as_not_waitable` (now
uses budget-left headers matching the live capture instead of empty ones, and asserts
`severity`, `screening_basis`, and `score` alongside the withheld message).

## <a id="correction-2026-07-28-deep-review"></a> Correction — 2026-07-28 deep review

This ticket's opening line claims: "when a response is partial, [F1] does not hide that
fact." That claim holds for rate-limit truncation (`CollectResult.complete`,
`stopped_because`) but does not hold for a second, distinct kind of partial response GitHub's
Search API itself reports: `incomplete_results`.

**`incomplete_results` is read nowhere in this codebase.** `gitseed/collect/search.py`'s
`_parse_items()` reads only the response's `items` array; `incomplete_results` and
`total_count` are discarded. A GitHub Search response can set `incomplete_results: true` to
say its own search timed out internally and did not fully execute — a different fact than "we
hit our rate limit" or "the query genuinely had few matches," and today indistinguishable
from either in `CollectResult`. Tracked as
[issue #47](https://github.com/MongLong0214/gitseed/issues/47) (GS-P0-007).

Separately, `TestWaiting`'s two documented cases (wait-and-retry-once) are correct for the
primary rate limit, communicated via `X-RateLimit-Reset`, which `parse()` reads. They do not
hold for GitHub's *secondary* rate limit, communicated via `Retry-After`: `classify()`
recognizes `Retry-After` as evidence of rate-limiting, but `parse()` never reads its value, so
`seconds_until_reset` computes `0` and `collect(wait=True)` retries immediately instead of
waiting. Tracked as [issue #51](https://github.com/MongLong0214/gitseed/issues/51)
(GS-P1-002).

Neither of these is a regression of the AC this ticket already checked off — both are gaps the
original AC list never named. This section records them as new, not as a failure of the
existing `[x]` items above.
