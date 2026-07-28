"""Coverage: how much of a repository a run actually looked at.

`severity_of` in `verdict.py` answers "what did the files we scanned contain."
That question is silent about how many files that was, so a 20-of-200 scan and
a 200-of-200 scan can produce the identical answer. `SourceCoverage` is the
missing half: the counts that justify -- or refuse to justify -- reading "no
findings" as "clean."

`eligible_files` is a policy target, not a resource limit: every file this
run's own selection rules (priority filenames plus the extension allow-list)
judged worth scanning, before size or count caps are applied. `scanned_files`
is what those caps and any fetch errors actually let through. The two being
equal is what `complete_for_policy` means -- the run did everything it set out
to do. `complete_for_repository` is the stricter claim that nothing in the
repository at all -- including files the extension policy itself excludes --
went unexamined, which is almost never true and is not the same question.
Conflating the two is exactly how a 20-of-200 scan gets reported as clean.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class SkippedFile:
    """One file a run did not scan, and why."""

    path: str
    reason: str


@dataclass(frozen=True)  # noqa: SLOTS_OK -- dataclass slots require Python 3.10.
class SourceCoverage:
    discovered_files: int
    eligible_files: int
    scanned_files: int
    skipped_policy: tuple[SkippedFile, ...] = ()
    skipped_error: tuple[SkippedFile, ...] = ()

    @property
    def complete_for_policy(self) -> bool:
        """Every file this run's own rules judged worth scanning was scanned.

        A computed property, not a constructor argument: deriving it from the
        counts it describes is the only way it cannot drift out of sync with
        them.
        """
        return self.scanned_files >= self.eligible_files and not self.skipped_error

    @property
    def complete_for_repository(self) -> bool:
        """Nothing in the repository -- including extension-excluded files -- was left unexamined."""
        return self.complete_for_policy and self.eligible_files >= self.discovered_files
