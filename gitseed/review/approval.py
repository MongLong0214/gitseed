"""사람의 승인을 값으로 만든다.

이 모듈이 존재하는 이유는 `actions.py` 한 곳에 있다. 외부 쓰기 함수가
`Approval` 인스턴스를 **인자로 요구**하면, 승인 확인을 건너뛰는 코드 경로가
존재할 수 없다. `if approved:` 는 지울 수 있지만 필수 인자는 지울 수 없다.

GitHub AUP 가 금지하는 것은 star/follow 자체가 아니라 그것의 자동화다. 사람이
건건이 결정하면 이 도구는 UI 이지 자동화가 아니다 — 그 구분을 코드 구조가
지켜야지 규율이 지켜서는 안 된다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import IO, Callable, Sequence


class Decision(Enum):
    """사람이 한 결정. 거부도 결과다.

    승인만 기록하면 "무엇을 하지 않았는가" 가 사라진다. 이 도구의 산출물은
    한 행동의 목록이 아니라 판단의 목록이고, 판단의 절반은 거부다.
    """

    STAR = "star"
    FOLLOW = "follow"
    BOTH = "both"
    REJECT = "reject"

    @property
    def is_action(self) -> bool:
        return self is not Decision.REJECT


#: 한 글자 입력 → 결정. 목록에 없는 입력은 결정이 아니다(재질문).
_ANSWERS: dict[str, Decision] = {
    "s": Decision.STAR,
    "f": Decision.FOLLOW,
    "b": Decision.BOTH,
    "n": Decision.REJECT,
}

#: 대화를 끝내는 입력. 결정이 아니라 중단이므로 `Decision` 에 넣지 않는다.
QUIT = "q"
BULK_LISTING_ROW_LIMIT = 20


class NotInteractive(RuntimeError):
    """승인을 물을 사람이 없다.

    파이프로 밀어넣은 `y` 와 화면을 본 사람의 `y` 는 바이트로는 같고 의미로는
    반대다. 전자를 승인으로 받으면 이 도구는 `yes | gitseed review` 한 줄로
    완전 자동화되고, 그 순간 AUP 위반이 된다.
    """


@dataclass(frozen=True)
class Approval:
    """One human decision with the evidence from which it was made.

    `prompt` and `answer` are committed as trailers so a later reviewer can
    judge what the person saw, not only the resulting decision. Large bulk
    prompts retain the first rows plus an explicit omission count and digest.
    `at` is required for the same reason: an undated approval is not auditable.
    """

    target: str
    decision: Decision
    prompt: str
    answer: str
    at: datetime
    bulk: bool = False

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("Approval.target 이 비어 있다: 무엇에 대한 승인인지 알 수 없다")
        if not self.prompt:
            raise ValueError("Approval.prompt 가 비어 있다: 사람이 무엇을 보았는지 알 수 없다")
        if self.at.tzinfo is None:
            raise ValueError("Approval.at 에 타임존이 없다: 감사 가능한 시각이 아니다")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def collect_approval(
    target: str,
    summary: str,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    now: Callable[[], datetime] = _now,
) -> Approval | None:
    """Show one item and collect a decision, returning `None` for `q`.

    A non-TTY stdin raises :class:`NotInteractive`.
    """
    stream_in = sys.stdin if stdin is None else stdin
    stream_out = sys.stdout if stdout is None else stdout

    if not getattr(stream_in, "isatty", lambda: False)():
        raise NotInteractive(
            "승인을 물을 터미널이 없다. gitseed 는 사람이 보는 화면에서만 외부 행동을 승인한다."
        )

    suffix = "  [s]tar [f]ollow [b]oth [n]ext(거부) [q]uit > "
    prompt = f"{summary}\n{suffix}"
    while True:
        stream_out.write(prompt)
        stream_out.flush()
        raw = stream_in.readline()
        if raw == "":  # EOF: 사람이 사라졌다. 승인으로 해석하지 않는다.
            return None
        answer = raw.strip().lower()
        if answer == QUIT:
            return None
        decision = _ANSWERS.get(answer)
        if decision is None:
            stream_out.write(f"  '{answer}' 는 결정이 아니다. s/f/b/n/q 중 하나.\n")
            continue
        return Approval(
            target=target,
            decision=decision,
            prompt=prompt,
            answer=answer,
            at=now(),
        )


def collect_bulk_approval(
    targets: Sequence[str],
    listing: str,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    now: Callable[[], datetime] = _now,
) -> list[Approval]:
    """Show the complete bulk listing once and derive one approval per target.

    Every derived approval carries the same bounded listing snapshot and answer.
    Rejection (`n`) records every target; quitting (`q`) records none.
    """
    if not targets:
        return []

    bulk_notice = f"위 {len(targets)}건 전체에 대한 결정. 건별로 다시 묻지 않는다."
    bounded_listing = _bounded_listing(listing)
    stream_out = sys.stdout if stdout is None else stdout
    if bounded_listing != listing:
        stream_out.write(f"{listing}\n{bulk_notice}\n")

    approval = collect_approval(
        f"<{len(targets)} targets>",
        f"{bounded_listing}\n{bulk_notice}\n일괄 승인: {len(targets)}건",
        stdin=stdin,
        stdout=stream_out,
        now=now,
    )
    if approval is None:
        return []

    return [
        Approval(
            target=target,
            decision=approval.decision,
            prompt=approval.prompt,
            answer=approval.answer,
            at=approval.at,
            bulk=True,
        )
        for target in targets
    ]


def _bounded_listing(listing: str) -> str:
    lines = listing.splitlines()
    kept = BULK_LISTING_ROW_LIMIT + 1
    if len(lines) <= kept:
        return listing
    omitted = len(lines) - kept
    digest = sha256(listing.encode()).hexdigest()
    return "\n".join(
        [*lines[:kept], f"[bulk listing truncated: {omitted} rows omitted; sha256={digest}]"]
    )
