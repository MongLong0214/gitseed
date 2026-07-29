"""승인·거부를 CommitLore 트레일러로 직렬화한다.

거부도 기록한다. 승인만 남기면 커밋 로그가 "한 일" 의 목록이 되고, 이 도구의
산출물은 "판단" 의 목록이다. 왜 3번은 별표하고 4번은 안 했는가 — 그 답은 4번을
기록해야만 남는다.

키 선택은 `commitlore` SPEC 을 따른다. 거부에 `Ruled-out:` 을 쓰는 것은 그 키가
"고려했고 아니라고 결정했다" 를 뜻하고 `|` 뒤에 이유를 요구하기 때문이다 —
이유 없는 거부를 문법이 거부한다.

`Verified:`/`Ruled-out:`/`Evidence:` are repeatable, but `Blast:`/`Undo:`/
`Provenance:` are not. An intent commit is one record, so `render_block` emits
its session-wide keys once. `Undo:` belongs only to the later per-action outcome
record, where the action and its result are known.
"""

from __future__ import annotations

from .actions import ActionOutcome, OutcomeStatus
from .approval import Approval, Decision

#: `Ruled-out:` 은 `대안 | 이유` 를 요구한다. 분리자 없는 값은 파싱되지 않는다.
_SEPARATOR = " | "


def _fold(key: str, value: str) -> str:
    """트레일러 한 줄. 값 안의 줄바꿈은 접어 넣는다 — 여러 줄 값은 트레일러가 아니다."""
    flat = " ".join(value.split())
    return f"{key}: {flat}"


def approval_trailers(approval: Approval, *, reason: str = "") -> list[str]:
    """한 승인 → 그 결정만의 트레일러 줄들 (반복 가능한 키만).

    `reason` 이 비면 거부는 사람이 왜 아니라고 했는지 모른다는 사실 자체를
    적는다. 빈 이유를 조용히 넣으면 `Ruled-out:` 이 분리자만 남은 채로
    통과하고, 나중에 읽는 사람은 이유가 지워진 것인지 없던 것인지 모른다.

    `Blast:` and `Provenance:` are added once by `render_block`; `Undo:` is
    derived later by `render_outcome`, once the action result is known.
    """
    audit = (
        f"prompt={approval.prompt}; answer={approval.answer}; "
        f"at={approval.at.isoformat()}"
    )
    if approval.decision is Decision.REJECT:
        why = reason.strip() or "리뷰어가 이유를 남기지 않았다"
        return [_fold("Ruled-out", f"{approval.target}{_SEPARATOR}{why}; {audit}")]

    lines = [
        _fold(
            "Verified",
            f"{approval.target} {approval.decision.value} authorized by a human; "
            f"{audit}; actions pending and may already have run",
        )
    ]
    if reason.strip():
        lines.append(_fold("Evidence", reason))
    return lines


def render_block(approvals: list[Approval], *, reasons: dict[str, str] | None = None) -> str:
    """여러 결정을 한 커밋의 트레일러 블록으로.

    빈 목록은 빈 문자열을 낸다 — 아무 판단도 하지 않은 세션은 트레일러를
    남기지 않는다. 판단 없음을 판단으로 기록하면 로그가 거짓말을 한다.

    `Blast: system` marks an intent that authorized an external action. The
    intent does not claim reversibility because the action has not happened yet.
    `Provenance: authored` applies to every decision, including rejections.
    """
    if not approvals:
        return ""
    why = reasons or {}
    lines: list[str] = []
    for approval in approvals:
        lines.extend(approval_trailers(approval, reason=why.get(approval.target, "")))
    if any(approval.decision is not Decision.REJECT for approval in approvals):
        lines.append(_fold("Blast", "system"))
    lines.append(_fold("Provenance", "authored"))
    return "\n".join(lines) + "\n"


def render_outcome(outcome: ActionOutcome) -> str:
    detail = f"; detail={outcome.detail}" if outcome.detail else ""
    blast = "local" if outcome.status is OutcomeStatus.NOT_ATTEMPTED else "system"
    limit = ""
    if outcome.status is OutcomeStatus.NOT_ATTEMPTED:
        undo = "easy"
    elif outcome.status is OutcomeStatus.CALL_FAILED:
        undo = "permanent"
        limit = "the remote result is unknown, so a safe reversal cannot be proven"
    elif outcome.status is OutcomeStatus.COMPENSATED:
        undo = "permanent"
        limit = "compensation cannot recall external effects already emitted"
    elif outcome.status is OutcomeStatus.COMPENSATION_FAILED:
        undo = "permanent"
        limit = "compensation failed, leaving the remote state inconsistent"
    elif outcome.action == "star":
        undo = "easy"
    else:
        undo = "costly"
        limit = "a follow notification may already have been delivered and cannot be recalled"
    lines = [
        _fold(
            "Verified",
            f"{outcome.action} {outcome.approval.target} {outcome.status.value}; "
            f"approved-at={outcome.approval.at.isoformat()}{detail}",
        ),
    ]
    if limit:
        lines.append(_fold("Limit", limit))
    lines.extend(
        [
            _fold("Blast", blast),
            _fold("Undo", undo),
            _fold("Provenance", "authored"),
        ]
    )
    return "\n".join(lines) + "\n"
