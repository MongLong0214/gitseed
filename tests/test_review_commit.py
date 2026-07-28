"""F4 — turning a review session's decisions into a commit.

`gitseed/review/commit.py` is the last step PRD-F4 requirement 4 needed:
`render_block` already produced a valid trailer block, and nothing turned it
into a commit. INV-006 (discovery history is immutable) does not hold for a
decision nobody committed — there is no history to be immutable.

Same discipline as `test_review.py`'s AC-1: a structural argument check proves
`record_decisions` cannot be reached without going through the real `Approval`
objects a person actually produced, the way `star`/`follow` cannot be reached
without an `Approval` at all.
"""

from __future__ import annotations

import inspect
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gitseed.review.approval import Approval, Decision
from gitseed.review.commit import CommitFailed, SubprocessGitCommitter, record_decisions
from gitseed.review.trailers import render_block

AT = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
VALIDATOR = Path.home() / "projects/annals/dist/commitlore.mjs"


def approved(target: str, decision: Decision) -> Approval:
    return Approval(target=target, decision=decision, prompt="p", answer="x", at=AT)


class RecordingCommitter:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def commit(self, message: str) -> str:
        self.messages.append(message)
        return f"fake-sha-{len(self.messages)}"


class FailingCommitter:
    def commit(self, message: str) -> str:
        raise CommitFailed("git refused")


class RacingCommitter(SubprocessGitCommitter):
    def __init__(self, repo: Path) -> None:
        super().__init__(repo)
        self.competing_sha = ""

    def _git(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        result = super()._git(*args, input=input)
        if args[0] == "commit-tree" and result.returncode == 0 and not self.competing_sha:
            competing = subprocess.run(
                ["git", "-C", str(self.repo), "commit-tree", args[1]],
                input=b"competing commit\n",
                check=True,
                capture_output=True,
            )
            self.competing_sha = competing.stdout.decode().strip()
            subprocess.run(
                ["git", "-C", str(self.repo), "update-ref", "HEAD", self.competing_sha],
                check=True,
            )
        return result


# --- structural: no path around the approval contract --------------------------


def test_record_decisions_has_no_default_for_approvals() -> None:
    """M6: giving `approvals` a default (e.g. `()`) would let a caller commit
    without ever having gone through `collect_approval`/`collect_bulk_approval`."""
    param = inspect.signature(record_decisions).parameters["approvals"]
    assert param.default is inspect.Parameter.empty


def test_record_decisions_has_no_default_for_committer() -> None:
    param = inspect.signature(record_decisions).parameters["committer"]
    assert param.default is inspect.Parameter.empty


def test_record_decisions_requires_both_positional_arguments() -> None:
    with pytest.raises(TypeError):
        record_decisions()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        record_decisions([approved("a", Decision.STAR)])  # type: ignore[call-arg]


def test_the_committed_message_is_derived_only_from_render_block() -> None:
    """`record_decisions` cannot be handed a free-text message — the committed
    content is always exactly `render_block`'s output for the same approvals,
    prefixed with a summary line. There is no parameter that accepts a
    different string."""
    approvals = [approved("octocat/hello", Decision.STAR), approved("octocat/two", Decision.REJECT)]
    committer = RecordingCommitter()
    record_decisions(approvals, committer)
    assert len(committer.messages) == 1
    assert committer.messages[0].endswith(render_block(approvals))


# --- empty and rejection-only sessions ------------------------------------------


def test_an_empty_session_commits_nothing() -> None:
    """Mirrors `render_block([]) == ""`: a session with no judgment is not a
    judgment that nothing changed, so nothing is committed."""
    committer = RecordingCommitter()
    result = record_decisions([], committer)
    assert result is None
    assert committer.messages == []


def test_a_rejection_only_session_still_commits() -> None:
    """AC-4 (F4 ticket): recording only approvals erases what was not done."""
    committer = RecordingCommitter()
    result = record_decisions([approved("octocat/hello", Decision.REJECT)], committer)
    assert result == "fake-sha-1"
    assert len(committer.messages) == 1
    assert "Ruled-out: octocat/hello" in committer.messages[0]


def test_an_approval_session_commits_once_for_every_decision_together() -> None:
    approvals = [
        approved("a", Decision.STAR),
        approved("b", Decision.REJECT),
        approved("c", Decision.FOLLOW),
    ]
    committer = RecordingCommitter()
    record_decisions(approvals, committer)
    assert len(committer.messages) == 1
    message = committer.messages[0]
    assert message.count("Verified:") == 2
    assert message.count("Ruled-out:") == 1


def test_the_commit_failure_from_the_committer_propagates() -> None:
    with pytest.raises(CommitFailed):
        record_decisions([approved("a", Decision.STAR)], FailingCommitter())


# --- the real adapter: an actual git repository ---------------------------------


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "gitseed tests"], cwd=repo, check=True)


def _log(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%B"], check=True, capture_output=True, text=True
    ).stdout


def test_subprocess_committer_creates_an_empty_commit_carrying_the_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    committer = SubprocessGitCommitter(repo)

    sha = record_decisions([approved("octocat/hello", Decision.STAR)], committer)

    assert sha is not None
    logged = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "--format=", sha], check=True, capture_output=True, text=True
    ).stdout
    assert logged.strip() == "", "an --allow-empty decision commit must change no tracked file"
    assert "Verified: octocat/hello" in _log(repo)


def test_subprocess_committer_makes_one_commit_per_call_not_per_approval(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    committer = SubprocessGitCommitter(repo)

    record_decisions([approved("a", Decision.STAR), approved("b", Decision.REJECT)], committer)

    count = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert count == "1"


def test_subprocess_committer_does_not_touch_unrelated_staged_changes(tmp_path: Path) -> None:
    """`--allow-empty` is the point: whatever else is staged in the working tree
    gitseed was invoked from must not end up inside the decision commit."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "unrelated.txt").write_text("do not commit me via the decision path\n")
    subprocess.run(["git", "-C", str(repo), "add", "unrelated.txt"], check=True, capture_output=True)

    committer = SubprocessGitCommitter(repo)
    record_decisions([approved("octocat/hello", Decision.STAR)], committer)

    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout
    assert status.strip() == "A  unrelated.txt", "the staged file must remain staged, not committed"


def test_subprocess_committer_fails_loudly_outside_a_git_repository(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain-directory"
    not_a_repo.mkdir()
    committer = SubprocessGitCommitter(not_a_repo)
    with pytest.raises(CommitFailed):
        committer.commit("gitseed review: 1 approved, 0 rejected\n\nVerified: a\n")


def test_subprocess_committer_does_not_overwrite_a_concurrent_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    committer = RacingCommitter(repo)

    with pytest.raises(CommitFailed):
        committer.commit("gitseed review intent\n\nVerified: a\n")

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == committer.competing_sha


@pytest.mark.skipif(not VALIDATOR.is_file(), reason="commitlore is unavailable at ~/projects/annals/dist/commitlore.mjs")
def test_the_real_committed_message_validates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    committer = SubprocessGitCommitter(repo)
    approvals = [approved("octocat/hello", Decision.STAR), approved("octocat/two", Decision.REJECT)]

    sha = record_decisions(approvals, committer)

    message_path = tmp_path / "message.txt"
    message_path.write_text(_log(repo))
    validated = subprocess.run(
        ["node", str(VALIDATOR), "validate", "--message-file", str(message_path)],
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0, validated.stderr
    assert sha is not None
