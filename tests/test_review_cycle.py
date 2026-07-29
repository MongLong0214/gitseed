"""Real-terminal coverage for the review queue's approval boundary."""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

from gitseed.cli import main
from gitseed.storage import SQLiteRunStore


ROOT = Path(__file__).parent.parent
VALIDATOR = Path.home() / "projects/annals/dist/cli.js"
COMMITLORE_MJS = Path.home() / "projects/annals/dist/commitlore.mjs"
PROMPT = b"[s]tar [f]ollow [b]oth [n]ext(\xea\xb1\xb0\xeb\xb6\x80) [q]uit > "


class RecordingWriter:
    """A writer fake whose only effect is retaining the requested actions."""

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


class RecordingCommitter:
    """A committer fake whose only effect is retaining the message it would have
    committed. These tests exercise the real terminal and a real subprocess-run
    CLI in the gitseed repository's own working tree (`os.chdir(ROOT)` below); a
    real `SubprocessGitCommitter` here would create actual commits in this
    repository every time the suite runs. This fake is why it does not."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def commit(self, message: str) -> str:
        self.messages.append(message)
        return f"fake-sha-{len(self.messages)}"


def _command(*extra: str) -> list[str]:
    return ["run", "--query", "example", "--fixtures", "tests/fixtures", "--no-dry-run", *extra]


def _read_terminal(fd: int) -> bytes:
    try:
        return os.read(fd, 4096)
    except OSError as error:
        if error.errno == errno.EIO:
            return b""
        raise


def _bind_standard_streams() -> None:
    sys.stdin = os.fdopen(0, "r", closefd=False)
    sys.stdout = os.fdopen(1, "w", closefd=False)
    sys.stderr = os.fdopen(2, "w", closefd=False)


def _run_under_pty(
    tmp_path: Path, answers: Sequence[bytes], *extra: str
) -> tuple[int, list[list[str]], str, list[str]]:
    result_path = tmp_path / "review-result.json"
    pid, master_fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        _bind_standard_streams()
        writer = RecordingWriter()
        committer = RecordingCommitter()
        exit_code = main(_command(*extra), writer=writer, committer=committer)
        result_path.write_text(
            json.dumps({"exit_code": exit_code, "calls": writer.calls, "commits": committer.messages})
        )
        os._exit(exit_code)

    transcript = bytearray()
    prompts_seen = 0
    try:
        for answer in answers:
            while transcript.count(PROMPT) == prompts_seen:
                readable, _, _ = select.select([master_fd], [], [], 5)
                if not readable:
                    raise AssertionError("CLI did not reach the expected approval prompt")
                chunk = _read_terminal(master_fd)
                if not chunk:
                    raise AssertionError("CLI closed its terminal before the expected approval prompt")
                transcript.extend(chunk)
            os.write(master_fd, answer)
            prompts_seen += 1

        while True:
            readable, _, _ = select.select([master_fd], [], [], 5)
            if not readable:
                raise AssertionError("CLI did not finish after the supplied approval answers")
            chunk = _read_terminal(master_fd)
            if not chunk:
                break
            transcript.extend(chunk)
    finally:
        os.close(master_fd)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status)
    result = json.loads(result_path.read_text())
    return result["exit_code"], result["calls"], transcript.decode(), result["commits"]


def _run_over_pipe(tmp_path: Path) -> tuple[int, list[list[str]]]:
    result_path = tmp_path / "pipe-result.json"
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(write_fd)
        os.dup2(read_fd, 0)
        os.close(read_fd)
        os.chdir(ROOT)
        _bind_standard_streams()
        writer = RecordingWriter()
        exit_code = main(_command(), writer=writer)
        result_path.write_text(json.dumps({"exit_code": exit_code, "calls": writer.calls}))
        os._exit(exit_code)

    os.close(read_fd)
    os.write(write_fd, b"s\n")
    os.close(write_fd)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status)
    result = json.loads(result_path.read_text())
    return result["exit_code"], result["calls"]


def test_review_cycle_uses_a_real_pty_and_records_only_authorised_writes(tmp_path: Path) -> None:
    # Given: a real terminal with three ranked fixture candidates.
    # When: the reviewer stars one, rejects one, then quits before the remainder.
    exit_code, calls, transcript, commits = _run_under_pty(tmp_path, [b"s\n", b"n\n", b"q\n"])
    # Then: only the star is written, and ranking precedes prompts, actions, and recorded decisions.
    assert exit_code == 0
    assert calls == [["star", "fixture/clean"]]
    assert "Ruled-out: fixture/second | \ub9ac\ubdf0\uc5b4\uac00 \uc774\uc720\ub97c \ub0a8\uae30\uc9c0 \uc54a\uc558\ub2e4" in transcript
    assert transcript.index("fixture/malicious") < transcript.index("[s]tar") < transcript.index("Verified:")
    # And: the decision reached a commit, not only the terminal.
    assert len(commits) == 2
    assert "Ruled-out: fixture/second |" in commits[0]
    assert "Verified: fixture/clean" in commits[0]
    assert "Verified: star fixture/clean succeeded" in commits[1]


def test_review_cycle_refuses_an_os_pipe_so_removing_the_tty_guard_fails_this_test(tmp_path: Path) -> None:
    # Given: the same CLI receives a real pipe rather than a pseudo-terminal.
    # When: that pipe provides an approval-looking answer.
    exit_code, calls = _run_over_pipe(tmp_path)
    # Then: no approval or write is possible; removing collect_approval's TTY guard makes this fail.
    assert exit_code == 1
    assert calls == []


@pytest.mark.skipif(not VALIDATOR.is_file(), reason="commitlore validator is unavailable at ~/projects/annals/dist/cli.js")
def test_printed_review_trailer_block_validates(tmp_path: Path) -> None:
    # Given: a completed real-PTY review cycle containing an approval and a rejection.
    # When: its printed trailer block is checked by CommitLore.
    _, _, transcript, _ = _run_under_pty(tmp_path, [b"s\n", b"n\n", b"q\n"])
    trailer_path = tmp_path / "trailers.txt"
    trailer_path.write_text(transcript[transcript.index("Verified:"):])
    validated = subprocess.run(
        ["node", str(VALIDATOR), "validate", "--message-file", str(trailer_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    # Then: the exact block the reviewer saw is a valid CommitLore trailer block.
    assert validated.returncode == 0, validated.stderr


@pytest.mark.skipif(not COMMITLORE_MJS.is_file(), reason="commitlore is unavailable at ~/projects/annals/dist/commitlore.mjs")
def test_the_committed_decision_message_validates(tmp_path: Path) -> None:
    # Given: a completed real-PTY review cycle containing an approval and a rejection.
    # When: the message actually handed to git is checked by CommitLore.
    _, _, _, commits = _run_under_pty(tmp_path, [b"s\n", b"n\n", b"q\n"])
    assert len(commits) == 2
    for index, message in enumerate(commits):
        message_path = tmp_path / f"commit-message-{index}.txt"
        message_path.write_text(message)
        validated = subprocess.run(
            ["node", str(COMMITLORE_MJS), "validate", "--message-file", str(message_path)],
            capture_output=True,
            check=False,
            text=True,
        )
        # Then: every exact message handed to git is a valid CommitLore message.
        assert validated.returncode == 0, validated.stderr


def test_approve_all_asks_once_and_derives_one_approval_per_target(tmp_path: Path) -> None:
    # Given: the same three ranked fixture candidates in bulk-review mode.
    # When: the reviewer gives the one bulk approval answer.
    exit_code, calls, transcript, commits = _run_under_pty(tmp_path, [b"s\n"], "--approve-all")
    # Then: one prompt authorises exactly one star per target.
    assert exit_code == 0
    assert calls == [["star", "fixture/clean"], ["star", "fixture/second"], ["star", "fixture/malicious"]]
    assert transcript[:transcript.index("Verified:")].count(
        "[s]tar [f]ollow [b]oth [n]ext(거부) [q]uit > "
    ) == 1
    assert transcript.count("Verified:") == 3
    # And: one intent commit carries all decisions, followed by one outcome per action.
    assert len(commits) == 4
    assert commits[0].count("Verified:") == 3


def test_quitting_before_any_decision_commits_nothing(tmp_path: Path) -> None:
    # Given: the same three ranked fixture candidates.
    # When: the reviewer quits before deciding on the first one.
    exit_code, calls, _, commits = _run_under_pty(tmp_path, [b"q\n"])
    # Then: no external write and no decision commit — a session with no judgment
    # is not a judgment that nothing changed.
    assert exit_code == 0
    assert calls == []
    assert commits == []


def test_a_rejection_only_session_still_commits(tmp_path: Path) -> None:
    # Given: the same three ranked fixture candidates.
    # When: the reviewer rejects every one instead of quitting.
    exit_code, calls, _, commits = _run_under_pty(tmp_path, [b"n\n", b"n\n", b"n\n"])
    # Then: no external write happened, but the rejections still reached a commit —
    # recording only approvals would erase what was not done, and why.
    assert exit_code == 0
    assert calls == []
    assert len(commits) == 1
    assert commits[0].count("Ruled-out:") == 3


def test_broken_store_still_reaches_approval_and_accepts_rejection(tmp_path: Path) -> None:
    # Given: run history cannot be opened because the requested database path is a directory.
    # When: the reviewer rejects every proposed GitHub write.
    exit_code, calls, transcript, _ = _run_under_pty(
        tmp_path, [b"n\n", b"n\n", b"n\n"], "--store", str(tmp_path)
    )
    # Then: history failure is reported only after the approval gate, never instead of it.
    assert exit_code == 1
    assert transcript.count(PROMPT.decode()) >= 3
    assert calls == []


def test_broken_observation_write_still_reaches_approval(tmp_path: Path) -> None:
    # Given: the artifact store opens, but its new observation insert fails.
    store_path = tmp_path / "runs.db"
    with SQLiteRunStore(store_path):
        pass
    connection = sqlite3.connect(store_path)
    connection.execute(
        "CREATE TRIGGER observations_fail BEFORE INSERT ON repository_observations "
        "BEGIN SELECT RAISE(ABORT, 'observation write unavailable'); END;"
    )
    connection.commit()
    connection.close()

    # When: the reviewer rejects every proposed GitHub write.
    exit_code, calls, transcript, _ = _run_under_pty(
        tmp_path, [b"n\n", b"n\n", b"n\n"], "--store", str(store_path)
    )

    # Then: the observation failure arrives only after every approval prompt.
    assert exit_code == 1
    assert transcript.count(PROMPT.decode()) >= 3
    assert calls == []


def test_dry_run_never_creates_a_decision_commit(tmp_path: Path) -> None:
    # Given: the default dry-run invocation (no --no-dry-run), so no approval is possible.
    result_path = tmp_path / "dry-run-result.json"
    pid, master_fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        _bind_standard_streams()
        committer = RecordingCommitter()
        exit_code = main(
            ["run", "--query", "example", "--fixtures", "tests/fixtures"],
            committer=committer,
        )
        result_path.write_text(json.dumps({"exit_code": exit_code, "commits": committer.messages}))
        os._exit(exit_code)
    try:
        while True:
            readable, _, _ = select.select([master_fd], [], [], 5)
            if not readable:
                raise AssertionError("dry-run CLI did not finish")
            if not _read_terminal(master_fd):
                break
    finally:
        os.close(master_fd)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status)
    result = json.loads(result_path.read_text())
    # Then: the default dry-run path never reaches the commit step at all — a mutation
    # that moved the commit call above the `args.dry_run` check would make this fail.
    assert result["exit_code"] == 0
    assert result["commits"] == []
