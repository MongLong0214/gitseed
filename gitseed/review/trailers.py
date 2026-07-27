"""승인·거부를 CommitLore 트레일러로 직렬화한다.

거부도 기록한다. 승인만 남기면 커밋 로그가 "한 일" 의 목록이 되고, 이 도구의
산출물은 "판단" 의 목록이다. 왜 3번은 별표하고 4번은 안 했는가 — 그 답은 4번을
기록해야만 남는다.

키 선택은 `commitlore` SPEC 을 따른다. 거부에 `Ruled-out:` 을 쓰는 것은 그 키가
"고려했고 아니라고 결정했다" 를 뜻하고 `|` 뒤에 이유를 요구하기 때문이다 —
이유 없는 거부를 문법이 거부한다.
"""

from __future__ import annotations

from .approval import Approval, Decision

#: `Ruled-out:` 은 `대안 | 이유` 를 요구한다. 분리자 없는 값은 파싱되지 않는다.
_SEPARATOR = " | "


def _fold(key: str, value: str) -> str:
    """트레일러 한 줄. 값 안의 줄바꿈은 접어 넣는다 — 여러 줄 값은 트레일러가 아니다."""
    flat = " ".join(value.split())
    return f"{key}: {flat}"


def approval_trailers(approval: Approval, *, reason: str = "") -> list[str]:
    """한 승인 → 트레일러 줄들.

    `reason` 이 비면 거부는 사람이 왜 아니라고 했는지 모른다는 사실 자체를
    적는다. 빈 이유를 조용히 넣으면 `Ruled-out:` 이 분리자만 남은 채로
    통과하고, 나중에 읽는 사람은 이유가 지워진 것인지 없던 것인지 모른다.
    """
    if approval.decision is Decision.REJECT:
        why = reason.strip() or "리뷰어가 이유를 남기지 않았다"
        return [
            _fold("Ruled-out", f"{approval.target}{_SEPARATOR}{why}"),
            _fold("Provenance", "authored"),
        ]

    lines = [
        _fold("Verified", f"{approval.target} 에 {approval.decision.value} — 사람이 화면에서 승인했다"),
        _fold("Blast", "external"),
        _fold("Undo", "easy"),
        _fold("Provenance", "authored"),
    ]
    if reason.strip():
        lines.insert(1, _fold("Evidence", reason))
    return lines


def render_block(approvals: list[Approval], *, reasons: dict[str, str] | None = None) -> str:
    """여러 결정을 한 커밋의 트레일러 블록으로.

    빈 목록은 빈 문자열을 낸다 — 아무 판단도 하지 않은 세션은 트레일러를
    남기지 않는다. 판단 없음을 판단으로 기록하면 로그가 거짓말을 한다.
    """
    if not approvals:
        return ""
    why = reasons or {}
    lines: list[str] = []
    for approval in approvals:
        lines.extend(approval_trailers(approval, reason=why.get(approval.target, "")))
    return "\n".join(lines) + "\n"
