from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from gitseed.cli import main
from gitseed.review.commit import CommitFailed, SubprocessGitCommitter


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class RecordingWriter:
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


class InspectingWriter(RecordingWriter):
    def __init__(self, repo: Path) -> None:
        super().__init__()
        self.repo = repo
        self.intent_seen_before_write = ""

    def star(self, repo: str) -> None:
        self.intent_seen_before_write = subprocess.run(
            ["git", "-C", str(self.repo), "log", "-1", "--format=%B"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        super().star(repo)


class InjectedFailure(RuntimeError):
    pass


class FailingWriter(RecordingWriter):
    def __init__(
        self,
        *,
        fail_follow: bool = False,
        fail_star_number: int | None = None,
        fail_unstar_target: str | None = None,
    ) -> None:
        super().__init__()
        self.fail_follow = fail_follow
        self.fail_star_number = fail_star_number
        self.fail_unstar_target = fail_unstar_target
        self.star_count = 0

    def star(self, repo: str) -> None:
        self.star_count += 1
        super().star(repo)
        if self.star_count == self.fail_star_number:
            raise InjectedFailure(f"star failed for {repo}")

    def follow(self, user: str) -> None:
        super().follow(user)
        if self.fail_follow:
            raise InjectedFailure(f"follow failed for {user}")

    def unstar(self, repo: str) -> None:
        super().unstar(repo)
        if repo == self.fail_unstar_target:
            raise InjectedFailure(f"unstar failed for {repo}")


class SimulatedCrash(BaseException):
    pass


class IntentFailingCommitter:
    def commit(self, message: str) -> str:
        raise CommitFailed("intent storage unavailable")


class OutcomeFailingCommitter:
    def __init__(self, repo: Path) -> None:
        self.delegate = SubprocessGitCommitter(repo)

    def commit(self, message: str) -> str:
        if message.startswith("gitseed action outcome:"):
            raise CommitFailed("outcome storage unavailable")
        return self.delegate.commit(message)


def _command(*extra: str) -> list[str]:
    return ["run", "--query", "example", "--fixtures", "tests/fixtures", "--no-dry-run", *extra]


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "gitseed tests"], cwd=repo, check=True)


def _commit_messages(repo: Path) -> list[str]:
    logged = subprocess.run(
        ["git", "-C", str(repo), "log", "--reverse", "--format=%B%x00"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [message for message in logged.split("\x00") if message.strip()]


def test_intent_is_durably_recorded_before_the_first_external_call(tmp_path: Path) -> None:
    repo = tmp_path / "intent-repo"
    _init_repo(repo)
    writer = InspectingWriter(repo)

    exit_code = main(
        _command(),
        writer=writer,
        committer=SubprocessGitCommitter(repo),
        stdin=Tty("s\nq\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert writer.calls == [("star", "fixture/clean")]
    assert "gitseed review intent:" in writer.intent_seen_before_write
    assert "fixture/clean" in writer.intent_seen_before_write
    assert "actions pending and may already have run" in writer.intent_seen_before_write


def test_a_crash_after_intent_leaves_a_reconstructable_commit(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "crash-repo"
    _init_repo(repo)
    writer = RecordingWriter()

    def crash_before_action(*_args) -> None:
        raise SimulatedCrash

    monkeypatch.setattr("gitseed.cli.perform", crash_before_action)

    with pytest.raises(SimulatedCrash):
        main(
            _command(),
            writer=writer,
            committer=SubprocessGitCommitter(repo),
            stdin=Tty("s\nq\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    messages = _commit_messages(repo)
    assert writer.calls == []
    assert len(messages) == 1
    assert "gitseed review intent:" in messages[0]
    assert "fixture/clean" in messages[0]
    assert "prompt=" in messages[0] and "answer=s" in messages[0] and "at=" in messages[0]
    assert "actions pending and may already have run" in messages[0]


def test_intent_persistence_failure_issues_no_external_call() -> None:
    writer = RecordingWriter()
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        _command("--limit", "1"),
        writer=writer,
        committer=IntentFailingCommitter(),
        stdin=Tty("s\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert writer.calls == []
    assert "[s]tar [f]ollow [b]oth" in stdout.getvalue()
    assert "intent commit failed: intent storage unavailable" in stderr.getvalue()


def test_outcome_persistence_failure_leaves_the_pending_intent_marker(tmp_path: Path) -> None:
    repo = tmp_path / "outcome-failure-repo"
    _init_repo(repo)
    writer = RecordingWriter()
    stderr = io.StringIO()

    exit_code = main(
        _command("--limit", "1"),
        writer=writer,
        committer=OutcomeFailingCommitter(repo),
        stdin=Tty("s\n"),
        stdout=io.StringIO(),
        stderr=stderr,
    )

    messages = _commit_messages(repo)
    assert exit_code == 1
    assert writer.calls == [
        ("star", "fixture/clean"),
        ("unstar", "fixture/clean"),
    ]
    assert len(messages) == 1
    assert "gitseed review intent:" in messages[0]
    assert "actions pending and may already have run" in messages[0]
    assert "outcome commit failed; intent remains pending" in stderr.getvalue()


def test_both_failure_compensates_star_and_records_every_outcome(tmp_path: Path) -> None:
    repo = tmp_path / "both-repo"
    _init_repo(repo)
    writer = FailingWriter(fail_follow=True)

    exit_code = main(
        _command(),
        writer=writer,
        committer=SubprocessGitCommitter(repo),
        stdin=Tty("b\ns\nq\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    messages = "\n".join(_commit_messages(repo))
    assert exit_code == 1
    assert writer.calls == [
        ("star", "fixture/clean"),
        ("follow", "fixture"),
        ("unstar", "fixture/clean"),
    ]
    assert "Verified: star fixture/clean succeeded" in messages
    assert "Verified: follow fixture call failed; remote result unknown" in messages
    assert "Verified: star fixture/second not attempted" in messages
    assert "Verified: star fixture/clean compensated" in messages


def test_third_target_failure_compensates_prior_targets_and_records_compensation_failure(tmp_path: Path) -> None:
    repo = tmp_path / "targets-repo"
    _init_repo(repo)
    writer = FailingWriter(fail_star_number=3, fail_unstar_target="fixture/second")

    exit_code = main(
        _command("--approve-all"),
        writer=writer,
        committer=SubprocessGitCommitter(repo),
        stdin=Tty("s\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    committed = _commit_messages(repo)
    messages = "\n".join(committed)
    assert exit_code == 1
    assert writer.calls == [
        ("star", "fixture/clean"),
        ("star", "fixture/second"),
        ("star", "fixture/malicious"),
        ("unstar", "fixture/second"),
        ("unstar", "fixture/clean"),
    ]
    assert "Verified: star fixture/malicious call failed; remote result unknown" in messages
    assert "Verified: star fixture/second compensation failed" in messages
    assert "Verified: star fixture/clean compensated" in messages
    compensation_failure = next(
        message for message in committed
        if "Verified: star fixture/second compensation failed" in message
    )
    assert "Limit: compensation failed, leaving the remote state inconsistent" in compensation_failure
    assert "Undo: permanent" in compensation_failure
