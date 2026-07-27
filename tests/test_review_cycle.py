"""Real-terminal coverage for the review queue's approval boundary."""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

from gitseed.cli import main


ROOT = Path(__file__).parent.parent
VALIDATOR = Path.home() / "projects/annals/dist/cli.js"
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


def _run_under_pty(tmp_path: Path, answers: Sequence[bytes], *extra: str) -> tuple[int, list[list[str]], str]:
    result_path = tmp_path / "review-result.json"
    pid, master_fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        _bind_standard_streams()
        writer = RecordingWriter()
        exit_code = main(_command(*extra), writer=writer)
        result_path.write_text(json.dumps({"exit_code": exit_code, "calls": writer.calls}))
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
    return result["exit_code"], result["calls"], transcript.decode()


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
    exit_code, calls, transcript = _run_under_pty(tmp_path, [b"s\n", b"n\n", b"q\n"])
    # Then: only the star is written, and ranking precedes prompts, actions, and recorded decisions.
    assert exit_code == 0
    assert calls == [["star", "fixture/clean"]]
    assert "Ruled-out: fixture/second | \ub9ac\ubdf0\uc5b4\uac00 \uc774\uc720\ub97c \ub0a8\uae30\uc9c0 \uc54a\uc558\ub2e4" in transcript
    assert transcript.index("fixture/malicious") < transcript.index("[s]tar") < transcript.index("Verified:")


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
    _, _, transcript = _run_under_pty(tmp_path, [b"s\n", b"n\n", b"q\n"])
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


def test_approve_all_asks_once_and_derives_one_approval_per_target(tmp_path: Path) -> None:
    # Given: the same three ranked fixture candidates in bulk-review mode.
    # When: the reviewer gives the one bulk approval answer.
    exit_code, calls, transcript = _run_under_pty(tmp_path, [b"s\n"], "--approve-all")
    # Then: one prompt authorises exactly one star per target.
    assert exit_code == 0
    assert calls == [["star", "fixture/clean"], ["star", "fixture/second"], ["star", "fixture/malicious"]]
    assert transcript.count("[s]tar [f]ollow [b]oth [n]ext(거부) [q]uit > ") == 1
    assert transcript.count("Verified:") == 3
