"""Turn a review session's decisions into a local commit.

PRD-F4 requirement 4 says approvals, rejections, and their evidence are recorded
in commits as CommitLore trailers. Everything up through `trailers.render_block`
already produced that block; nothing turned it into a commit. It existed on
stdout and a human had to paste it somewhere by hand. INV-006 (discovery
history is immutable) does not hold for a decision that was never committed —
there is no history to be immutable. This module is the missing step.

The commit this module makes never reads the index. `git commit --allow-empty`
looked like the obvious tool and is the wrong one: `--allow-empty` only waives
the "nothing to commit" refusal when the index matches `HEAD` exactly — if the
working tree gitseed was invoked from has anything else staged, a plain
`commit --allow-empty` commits that too, silently folding unrelated changes
into the decision record. `SubprocessGitCommitter` instead builds the commit
from plumbing: `commit-tree` against `HEAD`'s existing tree (or an empty tree
created in the repository's object format, when it has no commits yet), then
`update-ref` to move the branch. Neither command touches the index, so
whatever is staged stays staged and the working tree is not read at all — the
record is that the review happened, nothing else.

Same discipline as `actions.py`: `record_decisions` takes the actual list of
`Approval` objects as its first argument, not a message string. A function that
accepted free text instead would let a caller commit a "decision" a person
never made — the audit trail this module exists to build would be exactly as
trustworthy as the string handed to it, which is to say not at all. There is no
overload, no default, and no path that skips deriving the message from
`render_block`.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .actions import ActionOutcome
from .approval import BULK_LISTING_ROW_LIMIT, Approval, Decision
from .trailers import render_block, render_outcome


class CommitFailed(RuntimeError):
    """git refused the commit. The caller sees why instead of losing the record silently."""


class GitCommitter(Protocol):
    """The seam a fake slots into. Tests never touch a real repository."""

    def commit(self, message: str) -> str: ...


class SubprocessGitCommitter:
    """The real adapter: a plumbing-level commit in one working tree, never
    through the index.

    `repo` is the working tree the decision commit lands in — the directory
    gitseed was invoked from, unless the caller says otherwise. This class does
    not search for a repository or create one; a `repo` that is not inside a
    git working tree fails through `CommitFailed`, the same as any other git
    error.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def commit(self, message: str) -> str:
        head = self._git("rev-parse", "--verify", "HEAD")
        if head.returncode == 0:
            parent = head.stdout.decode().strip()
            tree = self._require(self._git("rev-parse", f"{parent}^{{tree}}"), "could not resolve HEAD's tree")
            args = ["commit-tree", tree, "-p", parent]
            expected_head = parent
        else:
            # No commits yet (or `repo` is not a repository at all — `commit-tree`
            # below reports that clearly rather than this guessing at the reason).
            tree = self._require(self._git("mktree", input=b""), "git mktree failed")
            args = ["commit-tree", tree]
            expected_head = "0" * len(tree)
        sha = self._require(self._git(*args, input=message.encode()), "git commit-tree failed")
        self._require(self._git("update-ref", "HEAD", sha, expected_head), "git update-ref failed")
        return sha

    def _git(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.repo), *args], input=input, capture_output=True
            )
        except OSError as error:
            raise CommitFailed(f"could not run git: {error}") from error

    @staticmethod
    def _require(result: subprocess.CompletedProcess[bytes], fallback: str) -> str:
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip()
            raise CommitFailed(detail or fallback)
        return result.stdout.decode().strip()


def record_decisions(
    approvals: Sequence[Approval],
    committer: GitCommitter,
    *,
    reasons: Mapping[str, str] | None = None,
) -> str | None:
    """Commit one review session's trailer block. `None` when nothing was decided.

    An empty `approvals` list — the reviewer quit before deciding anything —
    commits nothing, the same rule `render_block` already applies to the
    trailer block itself: a session with no judgment is not a judgment that
    nothing changed, and recording one would be a fabricated decision.

    A rejection-only session still commits. `render_block` already includes
    `Ruled-out:` trailers for rejections, and this function does not filter
    the list before rendering it — the commit is for whatever was actually
    decided, approved or not.
    """
    block = render_decisions(approvals, reasons=reasons)
    if not block:
        return None
    return committer.commit(f"{_summary(approvals)}\n\n{block}")


def render_decisions(
    approvals: Sequence[Approval],
    *,
    reasons: Mapping[str, str] | None = None,
) -> str:
    """Render the bounded trailer block used by both stdout and the commit."""
    recorded = list(approvals)
    if not reasons:
        recorded = _collapse_large_bulk_groups(recorded)
    return render_block(recorded, reasons=dict(reasons) if reasons else None)


def _collapse_large_bulk_groups(approvals: list[Approval]) -> list[Approval]:
    keys = [
        (approval.prompt, approval.answer, approval.at, approval.decision)
        for approval in approvals
    ]
    counts = Counter(keys)
    emitted = set()
    recorded: list[Approval] = []
    for approval, key in zip(approvals, keys):
        count = counts[key]
        if not approval.bulk or count <= BULK_LISTING_ROW_LIMIT:
            recorded.append(approval)
        elif key not in emitted:
            recorded.append(
                replace(approval, target=f"<{count} bulk targets; listing recorded in prompt>")
            )
            emitted.add(key)
    return recorded


def record_outcome(outcome: ActionOutcome, committer: GitCommitter) -> str:
    return committer.commit(
        f"gitseed action outcome: {outcome.action} {outcome.status.value}\n\n"
        f"{render_outcome(outcome)}"
    )


def _summary(approvals: Sequence[Approval]) -> str:
    approved = sum(1 for approval in approvals if approval.decision is not Decision.REJECT)
    rejected = len(approvals) - approved
    pending = "; actions pending and may already have run" if approved else ""
    return f"gitseed review intent: {approved} actions authorized, {rejected} rejected{pending}"
