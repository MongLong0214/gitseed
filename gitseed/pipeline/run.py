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

from dataclasses import dataclass
from typing import Callable, Sequence

from ..collect.search import Candidate, CollectResult
from ..evidence import ClaimBasis
from ..grade.types import GradeClient, GradeResult
from ..screen.coverage import SourceCoverage
from ..screen.signals import Signal, scan_files
from ..screen.verdict import findings, risk_of, unverified


@dataclass(frozen=True)
class Reviewed:
    """One candidate, carried through every stage with what each stage found."""

    candidate: Candidate
    signals: tuple[Signal, ...]
    severity: str
    grade: GradeResult | None
    #: Why this candidate never reached grading. `None` means it did.
    withheld: str | None = None
    skipped_files: tuple[str, ...] = ()
    screening_basis: ClaimBasis = ClaimBasis.ABSENT
    screened_files: tuple[str, ...] = ()
    #: `None` when the file source did not model coverage (fixtures, or no
    #: files were ever fetched). Present for every live GitHub adapter read.
    coverage: SourceCoverage | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))

    @property
    def findings(self) -> tuple[Signal, ...]:
        return findings(self.signals)

    @property
    def unverified(self) -> tuple[Signal, ...]:
        return unverified(self.signals)

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


@dataclass(frozen=True)
class PipelineResult:
    reviewed: tuple[Reviewed, ...] = ()
    #: True only when every stage finished. Never inferred from a non-empty list.
    complete: bool = True
    #: One line per stage that stopped early, in the order they happened.
    incomplete_because: tuple[str, ...] = ()
    rate_limited: bool = False
    grading_basis: ClaimBasis = ClaimBasis.MODEL

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewed", tuple(self.reviewed))
        object.__setattr__(self, "incomplete_because", tuple(self.incomplete_because))

    def with_incomplete(self, why: str) -> PipelineResult:
        # A metadata-phase quota exhaustion has to reach `rate_limited`, or the
        # token guidance stays silent for it while firing for a search-phase one.
        rate_limited = self.rate_limited or why.endswith(" metadata rate limited")
        if why in self.incomplete_because:
            if rate_limited == self.rate_limited:
                return self
            return PipelineResult(
                self.reviewed,
                self.complete,
                self.incomplete_because,
                rate_limited,
                self.grading_basis,
            )
        return PipelineResult(
            self.reviewed,
            False,
            (*self.incomplete_because, why),
            rate_limited,
            self.grading_basis,
        )


#: `high` never reaches a model. Sending a repository that scans as malicious to
#: a grader spends tokens deciding something already decided, and a model that
#: comes back enthusiastic is an argument to override a security signal.
BLOCKING_SEVERITY = "high"

#: The grading model sees bounded, representative evidence. Deterministic
#: screening deliberately continues to use its separate, larger source budget.
MODEL_DIGEST_BYTE_CAP = 23_000
MODEL_DIGEST_FILE_CAP = 16


class ModelInputTooLarge(ValueError):
    """The required model-evidence structure cannot fit without hiding it."""


@dataclass(frozen=True)
class FetchedFiles:
    files: tuple[tuple[str, str], ...]
    skipped: tuple[str, ...] = ()
    complete: bool = True
    incomplete_because: str | None = None
    rate_limited: bool = False
    #: `None` from a source that does not model policy caps at all (fixture
    #: replay reads a directory directly, with no allow-list or budget).
    coverage: SourceCoverage | None = None


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
    grader: GradeClient | None,
    on_survivor: Callable[[Candidate], None] | None = None,
) -> PipelineResult:
    """Carry `collected` through screening and grading.

    Neither `fetch_files` nor `grader` is called for a candidate that screening
    has already rejected, and a failure in either is recorded against that one
    candidate rather than ending the run. One unreachable repository is not a
    reason to discard the nine that were fine.
    """
    reviewed: list[Reviewed] = []
    incomplete_because: list[str] = []
    rate_limited = False
    grading_basis = ClaimBasis.MODEL if grader is not None else ClaimBasis.ABSENT

    def mark_incomplete(why: str) -> None:
        if why not in incomplete_because:
            incomplete_because.append(why)

    if not collected.complete:
        mark_incomplete(
            f"collection stopped early: {collected.stopped_because or 'reason not recorded'}"
        )

    for candidate in collected.candidates:
        try:
            fetched = fetch_files(candidate)
        except FileFetchError as error:
            mark_incomplete(error.run_reason or f"{candidate.repo}: could not read files ({error})")
            rate_limited = rate_limited or error.rate_limited
            if on_survivor is not None:
                on_survivor(candidate)
            reviewed.append(
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
            mark_incomplete(f"{candidate.repo}: could not read files ({error})")
            if on_survivor is not None:
                on_survivor(candidate)
            reviewed.append(
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
            coverage = fetched.coverage
        else:
            files = fetched
            skipped = ()
            source_complete = True
            coverage = None

        if not source_complete:
            mark_incomplete(fetched.incomplete_because or f"{candidate.repo}: skipped unreadable files ({'; '.join(skipped)})")
            rate_limited = rate_limited or fetched.rate_limited

        if not files:
            reason = "no readable source files"
            if skipped:
                reason += f" ({'; '.join(skipped)})"
            if on_survivor is not None:
                on_survivor(candidate)
            reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=[],
                    severity="unknown",
                    grade=None,
                    withheld=reason,
                    skipped_files=skipped,
                    coverage=coverage,
                )
            )
            continue

        signals = scan_files(files)
        severity = risk_of(signals, coverage)
        screened_files = tuple(path for path, _ in files)

        if severity == BLOCKING_SEVERITY:
            reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=signals,
                    severity=severity,
                    grade=None,
                    withheld=f"screening found {len(signals)} signal(s) at severity {severity}",
                    skipped_files=skipped,
                    screening_basis=ClaimBasis.DETERMINISTIC,
                    screened_files=screened_files,
                    coverage=coverage,
                )
            )
            continue

        if on_survivor is not None:
            on_survivor(candidate)

        if grader is None:
            reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=signals,
                    severity=severity,
                    grade=None,
                    withheld="model unavailable: deterministic-only",
                    skipped_files=skipped,
                    screening_basis=ClaimBasis.DETERMINISTIC,
                    screened_files=screened_files,
                    coverage=coverage,
                )
            )
            continue

        try:
            grade = grader.evaluate(_digest(candidate, files))
        except Exception as error:  # noqa: BLE001
            mark_incomplete(f"{candidate.repo}: grading failed ({error})")
            reviewed.append(
                Reviewed(
                    candidate=candidate,
                    signals=signals,
                    severity=severity,
                    grade=None,
                    withheld=f"grading failed: {error}",
                    skipped_files=skipped,
                    screening_basis=ClaimBasis.DETERMINISTIC,
                    screened_files=screened_files,
                    coverage=coverage,
                )
            )
            continue

        reviewed.append(
            Reviewed(
                candidate=candidate,
                signals=signals,
                severity=severity,
                grade=grade,
                skipped_files=skipped,
                screening_basis=ClaimBasis.DETERMINISTIC,
                screened_files=screened_files,
                coverage=coverage,
            )
        )

    return PipelineResult(tuple(reviewed), not incomplete_because, tuple(incomplete_because), rate_limited, grading_basis)


def _digest(candidate: Candidate, files: Sequence[tuple[str, str]]) -> str:
    """What the grader sees: bounded, declared evidence without popularity.

    Stars and push dates are deliberately withheld: a grader told a repository
    has 40k stars is being told the answer, and the whole point of grading is a
    judgement that does not already know it.
    """
    original_sizes = tuple(_utf8_size(text) for _, text in files)
    full = _render_digest(
        candidate,
        files,
        tuple(range(len(files))),
        original_sizes,
        describe_files=False,
    )
    if _utf8_size(full) <= MODEL_DIGEST_BYTE_CAP:
        return full

    sampled_indices = _evenly_spaced_indices(len(files))
    sampled_sizes = tuple(original_sizes[index] for index in sampled_indices)
    empty = _render_digest(
        candidate,
        files,
        sampled_indices,
        (0,) * len(sampled_indices),
        describe_files=True,
    )
    empty_size = _utf8_size(empty)
    if empty_size > MODEL_DIGEST_BYTE_CAP:
        raise ModelInputTooLarge(
            f"model grading input structure exceeds {MODEL_DIGEST_BYTE_CAP} UTF-8 bytes"
        )

    content_budget = MODEL_DIGEST_BYTE_CAP - empty_size
    while True:
        requested_sizes = _divide_content_budget(content_budget, sampled_sizes)
        included_sizes = tuple(
            _utf8_size(_utf8_prefix(files[file_index][1], requested_size))
            for file_index, requested_size in zip(sampled_indices, requested_sizes)
        )
        digest = _render_digest(
            candidate,
            files,
            sampled_indices,
            included_sizes,
            describe_files=True,
        )
        excess = _utf8_size(digest) - MODEL_DIGEST_BYTE_CAP
        if excess <= 0:
            return digest
        content_budget = max(0, content_budget - excess)


def _evenly_spaced_indices(file_count: int) -> tuple[int, ...]:
    if file_count <= MODEL_DIGEST_FILE_CAP:
        return tuple(range(file_count))
    return tuple(
        index * (file_count - 1) // (MODEL_DIGEST_FILE_CAP - 1)
        for index in range(MODEL_DIGEST_FILE_CAP)
    )


def _divide_content_budget(content_budget: int, original_sizes: Sequence[int]) -> tuple[int, ...]:
    if not original_sizes:
        return ()
    per_file, remainder = divmod(content_budget, len(original_sizes))
    return tuple(
        min(size, per_file + (1 if index < remainder else 0))
        for index, size in enumerate(original_sizes)
    )


def _render_digest(
    candidate: Candidate,
    files: Sequence[tuple[str, str]],
    sampled_indices: Sequence[int],
    included_sizes: Sequence[int],
    *,
    describe_files: bool,
) -> str:
    original_sizes = tuple(_utf8_size(text) for _, text in files)
    included_total = sum(included_sizes)
    parts = [f"repository: {candidate.repo}"]
    if len(sampled_indices) != len(included_sizes):
        raise ValueError("model digest sampled-file accounting is inconsistent")
    for file_index, included_size in zip(sampled_indices, included_sizes):
        path, text = files[file_index]
        original_size = original_sizes[file_index]
        marker = ""
        if describe_files:
            shortened = "true" if included_size < original_size else "false"
            marker = (
                f" [content_bytes original={original_size} included={included_size} "
                f"shortened={shortened}]"
            )
        parts.append(f"--- {path}{marker}\n{_utf8_prefix(text, included_size)}")
    selected_bytes = sum(original_sizes)
    parts.append(
        "model-evidence: "
        f"selected_files={len(files)} sampled_files={len(sampled_indices)} "
        f"selected_bytes={selected_bytes} included_bytes={included_total} "
        f"omitted_bytes={selected_bytes - included_total}"
    )
    return "\n".join(parts)


def _utf8_prefix(text: str, maximum_bytes: int) -> str:
    return text.encode("utf-8")[:maximum_bytes].decode("utf-8", errors="ignore")


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


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
