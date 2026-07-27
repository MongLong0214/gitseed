"""collect → screen → grade → review, and what each stage refuses to hide.

The stages already exist and each is honest on its own. This module's whole job
is the seam between them, and the seam has one rule: **a stage that could not
finish must say so in the result, not in a log line**.

The reason is the same one that shaped every other decision here. A shortened
candidate list and a genuinely thin field look identical — both are "not many
good repositories today" — and the second is a finding while the first is a bug.
Rate limits shorten lists. So does a screening error. So does a model that
refuses. If any of those can reach the reviewer as an ordinary empty, the
reviewer approves against a picture that was never true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..collect.search import Candidate, CollectResult
from ..grade.types import GradeClient, GradeResult
from ..screen.signals import Signal, scan_files
from ..screen.verdict import severity_of


@dataclass(frozen=True)
class Reviewed:
    """One candidate, carried through every stage with what each stage found."""

    candidate: Candidate
    signals: list[Signal]
    severity: str
    grade: GradeResult | None
    #: Why this candidate never reached grading. `None` means it did.
    withheld: str | None = None
    skipped_files: tuple[str, ...] = ()

    @property
    def score(self) -> int | None:
        """The rank, or `None` when nothing graded this candidate.

        `None` rather than a sentinel below the real range. A sentinel has to be
        chosen against the range it sits under, and `GradeResult` enforces 1..10
        today — so -1 and 0 are both "safe", and both stop being safe the moment
        that range opens downward. `None` cannot be compared to a score by
        accident; the ordering has to say what it does with it, which is what
        `ranked` does.
        """
        return None if self.grade is None else self.grade.idea + self.grade.skill


@dataclass
class PipelineResult:
    reviewed: list[Reviewed] = field(default_factory=list)
    #: True only when every stage finished. Never inferred from a non-empty list.
    complete: bool = True
    #: One line per stage that stopped early, in the order they happened.
    incomplete_because: list[str] = field(default_factory=list)
    rate_limited: bool = False

    def mark_incomplete(self, why: str) -> None:
        self.complete = False
        if why not in self.incomplete_because:
            self.incomplete_because.append(why)


#: `high` never reaches a model. Sending a repository that scans as malicious to
#: a grader spends tokens deciding something already decided, and a model that
#: comes back enthusiastic is an argument to override a security signal.
BLOCKING_SEVERITY = "high"


@dataclass(frozen=True)
class FetchedFiles:
    files: tuple[tuple[str, str], ...]
    skipped: tuple[str, ...] = ()
    complete: bool = True
    incomplete_because: str | None = None
    rate_limited: bool = False


@dataclass(frozen=True)
class FileFetchError(RuntimeError):
    detail: str
    run_reason: str | None = None
    rate_limited: bool = False

    def __str__(self) -> str:
        return self.detail


def run(
    collected: CollectResult,
    *,
    fetch_files: Callable[[Candidate], FetchedFiles | Sequence[tuple[str, str]]],
    grader: GradeClient,
) -> PipelineResult:
    """Carry `collected` through screening and grading.

    Neither `fetch_files` nor `grader` is called for a candidate that screening
    has already rejected, and a failure in either is recorded against that one
    candidate rather than ending the run. One unreachable repository is not a
    reason to discard the nine that were fine.
    """
    result = PipelineResult()

    if not collected.complete:
        result.mark_incomplete(
            f"collection stopped early: {collected.stopped_because or 'reason not recorded'}"
        )

    for candidate in collected.candidates:
        try:
            fetched = fetch_files(candidate)
        except FileFetchError as error:
            result.mark_incomplete(error.run_reason or f"{candidate.repo}: could not read files ({error})")
            result.rate_limited = result.rate_limited or error.rate_limited
            result.reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=[],
                    severity="unknown",
                    grade=None,
                    withheld=f"files could not be read ({error})",
                )
            )
            continue
        except Exception as error:  # noqa: BLE001 — one repository, not the run
            result.mark_incomplete(f"{candidate.repo}: could not read files ({error})")
            result.reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=[],
                    severity="unknown",
                    grade=None,
                    withheld="files could not be read, so nothing was screened",
                )
            )
            continue

        if isinstance(fetched, FetchedFiles):
            files = fetched.files
            skipped = fetched.skipped
            source_complete = fetched.complete
        else:
            files = fetched
            skipped = ()
            source_complete = True

        if not source_complete:
            result.mark_incomplete(fetched.incomplete_because or f"{candidate.repo}: skipped unreadable files ({'; '.join(skipped)})")
            result.rate_limited = result.rate_limited or fetched.rate_limited

        if not files:
            reason = "no readable source files"
            if skipped:
                reason += f" ({'; '.join(skipped)})"
            result.reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=[],
                    severity="unknown",
                    grade=None,
                    withheld=reason,
                    skipped_files=skipped,
                )
            )
            continue

        signals = scan_files(files)
        severity = severity_of(signals)

        if severity == BLOCKING_SEVERITY:
            result.reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=signals,
                    severity=severity,
                    grade=None,
                    withheld=f"screening found {len(signals)} signal(s) at severity {severity}",
                    skipped_files=skipped,
                )
            )
            continue

        try:
            grade = grader.evaluate(_digest(candidate, files))
        except Exception as error:  # noqa: BLE001
            result.mark_incomplete(f"{candidate.repo}: grading failed ({error})")
            result.reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=signals,
                    severity=severity,
                    grade=None,
                    withheld=f"grading failed: {error}",
                    skipped_files=skipped,
                )
            )
            continue

        result.reviewed.append(
            Reviewed(candidate=candidate, signals=signals, severity=severity, grade=grade, skipped_files=skipped)
        )

    return result


def _digest(candidate: Candidate, files: Sequence[tuple[str, str]]) -> str:
    """What the grader sees. Paths and contents, nothing about popularity.

    Stars and push dates are deliberately withheld: a grader told a repository
    has 40k stars is being told the answer, and the whole point of grading is a
    judgement that does not already know it.
    """
    parts = [f"repository: {candidate.repo}"]
    for path, text in files:
        parts.append(f"--- {path}\n{text}")
    return "\n".join(parts)


def ranked(result: PipelineResult) -> list[Reviewed]:
    """Highest score first. Ungraded entries sort last but are **not dropped**.

    A reviewer who sees only the gradeable ones cannot tell a quiet day from a
    broken grader.

    The key is a pair whose first element is 0 for a graded entry and 1 for an
    ungraded one, so "ungraded goes last" is stated rather than arranged by
    picking a number. Ties break on the repository name, which makes the order
    total: two runs over the same candidates produce the same list.
    """

    def key(entry: Reviewed) -> tuple[int, int, str]:
        score = entry.score
        if score is None:
            return (1, 0, entry.candidate.repo)
        return (0, -score, entry.candidate.repo)

    return sorted(result.reviewed, key=key)
