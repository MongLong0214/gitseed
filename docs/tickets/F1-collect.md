# F1 — GitHub 후보 수집 (키스톤)

> 이 티켓은 Phase 4에서 누락되어 구현 후 작성되었으며, 아래 내용은 이미 구현된 코드와 테스트를 사실대로 기록한다.

F1은 GitHub 저장소 후보를 수집하고, 응답이 일부뿐이면 그 사실을 숨기지 않고
`CollectResult.complete`와 `stopped_because`로 다음 단계에 전달한다.

## 구현

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

`classify`는 `"ok"`, `"rate-limited"`, `"forbidden"`, `"error"` 중 하나를
반환한다. 403은 헤더의 잔여량과 `Retry-After`를 보고 제한 소진과 권한 오류를
구분한다.

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

내부 파서 `_parse_items(body: bytes) -> list[Candidate]`는 잘못된
`full_name` 항목을 건너뛴다. `collect`는 페이지 사이 중복 저장소를 제거하고,
짧은 페이지에서 종료한다. 제한 소진 시 기본값은 대기하지 않고 불완전 결과를
반환하며, `wait=True`일 때만 리셋까지 대기한 뒤 한 번 재시도한다.

## 현재 테스트

`tests/test_collect.py`는 네트워크 대신 `FakeTransport`를 사용하며 다음 테스트를
포함한다.

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

`tests/test_pipeline.py`의
`test_a_truncated_collection_makes_the_whole_run_incomplete`는 F1의 불완전 상태가
파이프라인 전체에 전달되는 것을 검증한다.

## AC (현재 코드로 기계 판정)

- [x] 헤더 이름의 대소문자와 무관하게 제한 정보를 읽고, 누락되거나 정수가 아닌
      값은 `None`으로 처리한다.
- [x] 리셋 시각이 있으면 `seconds_until_reset`은 최소 1초를 반환한다.
- [x] 모든 2xx와 429를 분류하고, 403 제한 소진과 403 권한 오류를 구분한다.
- [x] 검색 쿼리를 URL 인코딩하고, 페이지 수·페이지 크기를 요청에 반영한다.
- [x] 짧은 페이지에서 종료하고, 페이지 사이 중복을 제거하며, 잘못된 항목은
      전체 수집을 실패시키지 않는다.
- [x] 제한 소진 전까지 모은 후보를 보존하면서 `complete=False`, 중단 사유,
      가져온 페이지 수를 반환한다.
- [x] 권한 오류에는 대기하지 않고, 예기치 않은 HTTP 상태는 상태 코드를 기록한다.
- [x] `wait=True`는 리셋까지 대기한 뒤 한 번만 재시도하고, 계속 제한되면 종료한다.
- [x] 위 AC는 `FakeTransport`로 네트워크 없이 실행된다.

## 남은 실측 — issue #6

403 권한 오류 분기는 주입 응답으로만 검증됐다. issue #6을 닫으려면 실제 GitHub
응답에서 다음 근거를 함께 남겨야 한다.

- 상태 코드는 403이고 `X-RateLimit-Remaining`은 0보다 크며 `Retry-After`는 없다.
- 결과는 `complete=False`이고 중단 사유가 권한 문제를 가리킨다.
- 프로세스는 제한 리셋을 기다리거나 같은 요청을 재시도하지 않는다.

토큰은 기록하지 않는다. 원문 응답에서는 위 판정에 필요한 상태 코드와 헤더만
남긴다.
