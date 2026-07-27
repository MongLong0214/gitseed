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


class NotInteractive(RuntimeError):
    """승인을 물을 사람이 없다.

    파이프로 밀어넣은 `y` 와 화면을 본 사람의 `y` 는 바이트로는 같고 의미로는
    반대다. 전자를 승인으로 받으면 이 도구는 `yes | gitseed review` 한 줄로
    완전 자동화되고, 그 순간 AUP 위반이 된다.
    """


@dataclass(frozen=True)
class Approval:
    """한 번의 사람 결정. 자기가 무엇으로부터 만들어졌는지를 들고 있다.

    `prompt`/`answer` 를 보존하는 이유: 이 값이 트레일러로 커밋에 남고, 나중에
    "정말 사람이 봤는가" 를 묻는 사람은 결정 자체가 아니라 무엇을 보고
    결정했는지를 확인해야 한다. `at` 이 필수인 것도 같은 이유다 — 시각 없는
    승인은 감사할 수 없다.
    """

    target: str
    decision: Decision
    prompt: str
    answer: str
    at: datetime

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
    """항목 하나를 보여주고 결정을 받는다. `q` 면 `None`(중단).

    `stdin.isatty()` 가 거짓이면 :class:`NotInteractive`. 테스트는 tty 를 흉내
    내는 가짜 스트림을 주입한다 — 검사를 우회하는 플래그를 두지 않는 이유는,
    그 플래그가 존재하는 순간 CI 에서 켜지고 그게 곧 자동화이기 때문이다.
    """
    stream_in = sys.stdin if stdin is None else stdin
    stream_out = sys.stdout if stdout is None else stdout

    if not getattr(stream_in, "isatty", lambda: False)():
        raise NotInteractive(
            "승인을 물을 터미널이 없다. gitseed 는 사람이 보는 화면에서만 외부 행동을 승인한다."
        )

    prompt = f"{summary}\n  [s]tar [f]ollow [b]oth [n]ext(거부) [q]uit > "
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
    """`--approve-all`: 전체를 보여주고 **1회** 결정을 받아 항목 수만큼 파생한다.

    파생된 `Approval` 들은 모두 같은 `prompt`(전체 목록)와 같은 `answer` 를
    들고 있다. 감사하는 사람이 "이건 건건이 본 게 아니라 일괄 승인이다" 를
    트레일러만 보고 판별할 수 있어야 하기 때문이다.

    거부(`n`)와 중단(`q`)은 다르다. 거부는 전건 거부로 기록되고, 중단은 빈
    목록을 남긴다 — 아무 판단도 하지 않은 것과 전부 아니라고 판단한 것은
    다른 사실이다.
    """
    if not targets:
        return []

    stream_out = sys.stdout if stdout is None else stdout
    stream_out.write(listing)
    stream_out.write(f"\n위 {len(targets)}건 전체에 대한 결정. 건별로 다시 묻지 않는다.\n")

    approval = collect_approval(
        f"<{len(targets)} targets>",
        f"일괄 승인: {len(targets)}건",
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
        )
        for target in targets
    ]
