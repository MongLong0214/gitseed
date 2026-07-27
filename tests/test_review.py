"""F4 리뷰 큐 — AC-1..AC-6.

핵심 주장 하나: 승인 없는 외부 쓰기는 검사로 막히는 게 아니라 호출이 성립하지
않아서 막힌다. AC-1 은 그것을 `TypeError` 로 증명한다.
"""

from __future__ import annotations

import io
import inspect
from datetime import datetime, timezone

import pytest

from gitseed.review.actions import (
    ApprovalMismatch,
    Performed,
    follow,
    perform,
    star,
    undo,
)
from gitseed.review.approval import (
    Approval,
    Decision,
    NotInteractive,
    collect_approval,
    collect_bulk_approval,
)
from gitseed.review.trailers import approval_trailers, render_block

AT = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)


class FakeClient:
    """호출을 기록만 한다. 네트워크가 없으므로 테스트가 AUP 를 건드리지 않는다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def star(self, repo: str) -> None:
        self.calls.append(("star", repo))

    def unstar(self, repo: str) -> None:
        self.calls.append(("unstar", repo))

    def follow(self, user: str) -> None:
        self.calls.append(("follow", user))

    def unfollow(self, user: str) -> None:
        self.calls.append(("unfollow", user))


class Tty(io.StringIO):
    """사람이 보는 터미널을 흉내 낸다. 진짜 tty 여부가 아니라 주입으로 판정한다."""

    def isatty(self) -> bool:
        return True


def approved(target: str, decision: Decision) -> Approval:
    return Approval(target=target, decision=decision, prompt="p", answer="x", at=AT)


# --- AC-1: 승인 없는 호출은 성립하지 않는다 -------------------------------------


@pytest.mark.parametrize("fn", [star, follow])
def test_ac1_external_write_requires_an_approval_argument(fn) -> None:
    """M1: `approval` 인자를 지우면 이 테스트가 통과해버린다 → 그래서 시그니처를 본다."""
    client = FakeClient()
    with pytest.raises(TypeError):
        fn(client, "octocat/hello")  # type: ignore[call-arg]
    assert client.calls == [], "인자가 모자란 호출이 네트워크까지 갔다"


@pytest.mark.parametrize("fn", [star, follow])
def test_ac1_approval_parameter_has_no_default(fn) -> None:
    """기본값이 붙는 순간 `TypeError` 가 사라지고 승인 없는 경로가 생긴다."""
    param = inspect.signature(fn).parameters["approval"]
    assert param.default is inspect.Parameter.empty
    assert param.annotation in (Approval, "Approval")


def test_ac1_rejection_cannot_authorise_an_action() -> None:
    client = FakeClient()
    with pytest.raises(ApprovalMismatch):
        star(client, "octocat/hello", approved("octocat/hello", Decision.REJECT))
    assert client.calls == []


def test_ac1_an_approval_for_another_target_does_not_authorise_this_one() -> None:
    """목록 3번을 승인받고 4번을 별표하는 오프바이원."""
    client = FakeClient()
    with pytest.raises(ApprovalMismatch):
        star(client, "octocat/four", approved("octocat/three", Decision.STAR))
    assert client.calls == []


def test_ac1_a_follow_approval_does_not_authorise_a_star() -> None:
    client = FakeClient()
    with pytest.raises(ApprovalMismatch):
        star(client, "octocat/hello", approved("octocat/hello", Decision.FOLLOW))
    assert client.calls == []


def test_both_authorises_exactly_two_actions() -> None:
    client = FakeClient()
    done = perform(client, approved("octocat", Decision.BOTH))
    assert [c[0] for c in client.calls] == ["star", "follow"]
    assert len(done) == 2


def test_reject_performs_nothing_at_all() -> None:
    client = FakeClient()
    assert perform(client, approved("octocat", Decision.REJECT)) == []
    assert client.calls == []


# --- AC-2: Approval 은 사람 입력을 읽은 경로에서만 만들어진다 --------------------


def test_ac2_non_interactive_stdin_is_refused() -> None:
    """M2: `isatty` 검사를 지우면 `yes | gitseed review` 가 전건 승인이 된다."""
    with pytest.raises(NotInteractive):
        collect_approval("octocat/hello", "요약", stdin=io.StringIO("s\n"), stdout=io.StringIO())


def test_ac2_a_refused_collection_reaches_no_action() -> None:
    client = FakeClient()
    with pytest.raises(NotInteractive):
        collect_approval("octocat/hello", "요약", stdin=io.StringIO("s\n"), stdout=io.StringIO())
    assert client.calls == []


def test_eof_is_not_consent() -> None:
    """사람이 사라진 것과 사람이 승인한 것은 다르다."""
    assert collect_approval("octocat/hello", "요약", stdin=Tty(""), stdout=io.StringIO()) is None


def test_quit_stops_without_deciding() -> None:
    assert collect_approval("octocat/hello", "요약", stdin=Tty("q\n"), stdout=io.StringIO()) is None


def test_unrecognised_input_is_asked_again_not_guessed() -> None:
    out = io.StringIO()
    result = collect_approval("octocat/hello", "요약", stdin=Tty("yes\ns\n"), stdout=out, now=lambda: AT)
    assert result is not None and result.decision is Decision.STAR
    assert "결정이 아니다" in out.getvalue()


@pytest.mark.parametrize(
    "answer,decision",
    [("s", Decision.STAR), ("f", Decision.FOLLOW), ("b", Decision.BOTH), ("n", Decision.REJECT)],
)
def test_each_answer_maps_to_its_decision(answer: str, decision: Decision) -> None:
    result = collect_approval("t", "요약", stdin=Tty(f"{answer}\n"), stdout=io.StringIO(), now=lambda: AT)
    assert result is not None and result.decision is decision
    assert result.answer == answer


def test_the_approval_records_what_the_person_saw() -> None:
    """감사하는 사람은 결정이 아니라 무엇을 보고 결정했는지를 확인한다."""
    result = collect_approval(
        "octocat/hello", "별 12개 · 신호 없음", stdin=Tty("s\n"), stdout=io.StringIO(), now=lambda: AT
    )
    assert result is not None
    assert "별 12개 · 신호 없음" in result.prompt


# --- Approval 자체의 불변식 (M4) -----------------------------------------------


def test_m4_an_approval_without_a_timezone_is_not_auditable() -> None:
    with pytest.raises(ValueError):
        Approval(target="t", decision=Decision.STAR, prompt="p", answer="s", at=datetime(2026, 7, 27))


def test_an_approval_without_a_prompt_cannot_be_built() -> None:
    with pytest.raises(ValueError):
        Approval(target="t", decision=Decision.STAR, prompt="", answer="s", at=AT)


def test_an_approval_without_a_target_cannot_be_built() -> None:
    with pytest.raises(ValueError):
        Approval(target="", decision=Decision.STAR, prompt="p", answer="s", at=AT)


def test_an_approval_is_frozen() -> None:
    """승인을 만든 뒤 대상을 바꿔치기할 수 있으면 대상 검사가 무의미하다."""
    a = approved("octocat/hello", Decision.STAR)
    with pytest.raises(Exception):
        a.target = "someone/else"  # type: ignore[misc]


# --- AC-3: 되돌림 ---------------------------------------------------------------


def test_ac3_undo_calls_unstar() -> None:
    client = FakeClient()
    done = star(client, "octocat/hello", approved("octocat/hello", Decision.STAR))
    undo(client, done)
    assert client.calls == [("star", "octocat/hello"), ("unstar", "octocat/hello")]


def test_ac3_undo_calls_unfollow() -> None:
    client = FakeClient()
    done = follow(client, "octocat", approved("octocat", Decision.FOLLOW))
    undo(client, done)
    assert client.calls == [("follow", "octocat"), ("unfollow", "octocat")]


def test_undo_refuses_an_action_it_cannot_reverse() -> None:
    client = FakeClient()
    with pytest.raises(ValueError):
        undo(client, Performed(target="t", action="sponsor", approval=approved("t", Decision.STAR)))
    assert client.calls == []


# --- AC-4/AC-5: 트레일러 --------------------------------------------------------


def test_ac4_a_rejection_is_recorded() -> None:
    """M3: 거부를 트레일러에서 빼면 '무엇을 안 했는지' 가 사라진다."""
    lines = approval_trailers(approved("octocat/hello", Decision.REJECT), reason="스팸 신호")
    assert any(line.startswith("Ruled-out: ") for line in lines)
    assert "스팸 신호" in "\n".join(lines)


def test_a_rejection_without_a_reason_says_so_rather_than_leaving_it_blank() -> None:
    lines = approval_trailers(approved("octocat/hello", Decision.REJECT))
    ruled = next(line for line in lines if line.startswith("Ruled-out: "))
    assert " | " in ruled
    assert ruled.split(" | ", 1)[1].strip() != ""


def test_a_ruled_out_line_always_carries_the_separator() -> None:
    """`|` 없는 `Ruled-out:` 은 commitlore 가 이유 없음으로 읽는다."""
    for reason in ["", "  ", "왜냐하면"]:
        lines = approval_trailers(approved("t", Decision.REJECT), reason=reason)
        assert " | " in lines[0]


def test_an_approval_trailer_names_the_action_taken() -> None:
    lines = approval_trailers(approved("octocat/hello", Decision.STAR))
    joined = "\n".join(lines)
    assert "octocat/hello" in joined and "star" in joined


def test_a_multiline_reason_is_folded_into_one_trailer_line() -> None:
    lines = approval_trailers(approved("t", Decision.REJECT), reason="첫 줄\n둘째 줄")
    assert len(lines[0].splitlines()) == 1
    assert "첫 줄 둘째 줄" in lines[0]


def test_no_decisions_render_no_block() -> None:
    """판단 없음을 판단으로 기록하면 로그가 거짓말을 한다."""
    assert render_block([]) == ""


def test_a_block_carries_every_decision_including_the_rejections() -> None:
    block = render_block(
        [approved("a", Decision.STAR), approved("b", Decision.REJECT), approved("c", Decision.FOLLOW)],
        reasons={"b": "포크만 있다"},
    )
    assert "a" in block and "c" in block
    assert "Ruled-out: b | 포크만 있다" in block


# --- AC-6: --approve-all -------------------------------------------------------


def test_ac6_bulk_approval_requires_one_human_answer() -> None:
    """M5: 입력 없이 전건 승인하면 이 테스트의 stdin 소비가 사라진다."""
    stdin = Tty("s\n")
    out = io.StringIO()
    approvals = collect_bulk_approval(["a", "b", "c"], "목록", stdin=stdin, stdout=out, now=lambda: AT)
    assert [a.target for a in approvals] == ["a", "b", "c"]
    assert all(a.decision is Decision.STAR for a in approvals)
    assert stdin.read() == "", "사람 입력을 실제로 소비하지 않았다"


def test_ac6_bulk_approval_is_refused_when_nobody_is_watching() -> None:
    with pytest.raises(NotInteractive):
        collect_bulk_approval(["a"], "목록", stdin=io.StringIO("s\n"), stdout=io.StringIO())


def test_bulk_approval_shows_the_whole_listing_before_asking() -> None:
    out = io.StringIO()
    collect_bulk_approval(["a", "b"], "1. a\n2. b", stdin=Tty("s\n"), stdout=out, now=lambda: AT)
    written = out.getvalue()
    assert "1. a" in written and "2. b" in written
    assert written.index("2. b") < written.index("[s]tar")


def test_bulk_approvals_are_marked_as_bulk_in_what_the_person_saw() -> None:
    """건별로 본 것과 일괄로 본 것을 트레일러만 보고 구분할 수 있어야 한다."""
    approvals = collect_bulk_approval(["a", "b"], "목록", stdin=Tty("s\n"), stdout=io.StringIO(), now=lambda: AT)
    assert all("일괄 승인: 2건" in a.prompt for a in approvals)


def test_quitting_a_bulk_prompt_approves_nothing() -> None:
    assert collect_bulk_approval(["a", "b"], "목록", stdin=Tty("q\n"), stdout=io.StringIO()) == []


def test_rejecting_in_bulk_is_recorded_for_every_target() -> None:
    """전건 거부와 아무 판단 없음은 다른 사실이다."""
    approvals = collect_bulk_approval(["a", "b"], "목록", stdin=Tty("n\n"), stdout=io.StringIO(), now=lambda: AT)
    assert len(approvals) == 2
    assert all(a.decision is Decision.REJECT for a in approvals)


def test_an_empty_queue_asks_nothing() -> None:
    assert collect_bulk_approval([], "목록", stdin=io.StringIO(), stdout=io.StringIO()) == []
