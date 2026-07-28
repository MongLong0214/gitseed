"""외부 쓰기. 전부 `Approval` 을 필수 인자로 요구한다.

이 모듈의 모든 공개 함수는 첫 인자로 :class:`~gitseed.review.approval.Approval`
을 받는다. 편의 오버로드도, `force=True` 도, 기본값도 없다 — 그런 것이 하나라도
있으면 승인 없는 경로가 생기고, 그 경로는 언젠가 반드시 호출된다.

호출 자체는 주입된 클라이언트가 한다. 이 모듈은 "승인이 있는가" 와 "승인이 이
행동을 가리키는가" 만 판정한다. 네트워크는 F1 의 클라이언트가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .approval import Approval, Decision


class GitHubWriter(Protocol):
    """F1 클라이언트가 만족해야 하는 최소 면. 테스트는 호출을 기록하는 가짜를 준다."""

    def star(self, repo: str) -> None: ...
    def unstar(self, repo: str) -> None: ...
    def follow(self, user: str) -> None: ...
    def unfollow(self, user: str) -> None: ...


class ApprovalMismatch(RuntimeError):
    """승인이 이 행동을 가리키지 않는다.

    `Decision.FOLLOW` 승인으로 star 를 부르는 것은 승인 없는 star 다. 대상이
    다른 것도 마찬가지 — 목록의 3번을 승인받고 4번을 별표하는 오프바이원이
    이 예외가 막는 실제 사고다.
    """


@dataclass(frozen=True)
class Performed:
    """실제로 일어난 외부 행동 한 건. 되돌림의 입력이기도 하다."""

    target: str
    action: str
    approval: Approval


class OutcomeStatus(Enum):
    SUCCEEDED = "succeeded"
    CALL_FAILED = "call failed; remote result unknown"
    NOT_ATTEMPTED = "not attempted"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation failed"


@dataclass(frozen=True)  # noqa: SLOTS_OK — Python 3.9 lacks dataclass slots.
class ActionOutcome:
    approval: Approval
    status: OutcomeStatus
    detail: str = ""

    @property
    def action(self) -> str:
        return self.approval.decision.value


def _check(approval: Approval, target: str, wanted: Decision) -> None:
    if approval.target != target:
        raise ApprovalMismatch(
            f"승인 대상은 {approval.target!r} 인데 {target!r} 에 대해 행동하려 한다"
        )
    if approval.decision is Decision.REJECT:
        raise ApprovalMismatch(f"{target!r} 는 거부되었다: 거부는 행동의 근거가 될 수 없다")
    if approval.decision is not Decision.BOTH and approval.decision is not wanted:
        raise ApprovalMismatch(
            f"{target!r} 에 대한 승인은 {approval.decision.value} 인데 "
            f"{wanted.value} 를 실행하려 한다"
        )


def star(client: GitHubWriter, repo: str, approval: Approval) -> Performed:
    _check(approval, repo, Decision.STAR)
    client.star(repo)
    return Performed(target=repo, action="star", approval=approval)


def follow(client: GitHubWriter, user: str, approval: Approval) -> Performed:
    _check(approval, user, Decision.FOLLOW)
    client.follow(user)
    return Performed(target=user, action="follow", approval=approval)


def undo(client: GitHubWriter, performed: Performed) -> None:
    """되돌림. 승인을 다시 묻지 않는다.

    비대칭이 의도적이다. AUP 가 통제하려는 것은 늘어나는 방향이고, 되돌림은
    이 도구가 만든 상태를 지우는 것뿐이다. 되돌리는 데 승인이 필요하면 잘못
    누른 사람이 되돌릴 수 없게 된다.

    되돌릴 근거는 `Performed` 자체다 — 실제로 일어났다고 기록된 행동만
    되돌릴 수 있고, 그 기록은 승인을 품고 있다.
    """
    if performed.action == "star":
        client.unstar(performed.target)
    elif performed.action == "follow":
        client.unfollow(performed.target)
    else:
        raise ValueError(f"되돌릴 수 없는 행동: {performed.action!r}")


def perform(client: GitHubWriter, approval: Approval) -> list[Performed]:
    """한 승인이 가리키는 행동 전부를 실행한다. 거부면 아무것도 하지 않는다."""
    if approval.decision is Decision.REJECT:
        return []
    done: list[Performed] = []
    if approval.decision in (Decision.STAR, Decision.BOTH):
        done.append(star(client, approval.target, approval))
    if approval.decision in (Decision.FOLLOW, Decision.BOTH):
        done.append(follow(client, approval.target, approval))
    return done
