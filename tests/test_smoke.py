"""T-301 — the contract smoke test.

Each of the four checks is failed in isolation by a fake client, because a smoke
test that has never been seen to fail is a smoke test nobody has tested. The
fakes reproduce behaviour actually measured against the seed: the 1.5b model's
false malware verdicts, and the field bleed observed at 7b as well.

No network. The point of the seam is that this runs anywhere.
"""

from __future__ import annotations

import pytest

from gitseed.grade.smoke import CLEAN_DIGEST, MALICIOUS_DIGEST, run_smoke
from gitseed.grade.types import GradeResult


class FakeClient:
    """A grader whose every failure mode can be switched on individually."""

    def __init__(
        self,
        *,
        flags_clean: bool = False,
        misses_malicious: bool = False,
        description: str = "A tiny structured logger.",
        drifting_scores: bool = False,
        raises: bool = False,
        model: str = "fake:1b",
    ) -> None:
        self.flags_clean = flags_clean
        self.misses_malicious = misses_malicious
        self.description = description
        self.drifting_scores = drifting_scores
        self.raises = raises
        self.model = model
        self._calls = 0

    def evaluate(self, digest: str) -> GradeResult:
        if self.raises:
            raise RuntimeError("ollama unreachable")
        self._calls += 1
        idea = 5 + (self._calls if self.drifting_scores else 0)
        return GradeResult(
            idea=min(idea, 10),
            skill=9,
            description=self.description,
            model=self.model,
            temperature=0.1,
            prompt_version="v1",
        )

    def flags_malicious(self, digest: str) -> bool:
        if digest == CLEAN_DIGEST:
            return self.flags_clean
        if digest == MALICIOUS_DIGEST:
            return not self.misses_malicious
        return False


class FlagsFailingClient(FakeClient):
    def flags_malicious(self, digest: str) -> bool:
        raise RuntimeError("malware check unavailable")


def test_a_good_model_passes() -> None:
    result = run_smoke(FakeClient())
    assert result.passed, result.failures
    assert result.failures == []
    assert result.model == "fake:1b"


def test_flagging_clean_code_fails() -> None:
    # What qwen2.5-coder:1.5b did on 9 of 14 runs against the seed.
    result = run_smoke(FakeClient(flags_clean=True))
    assert not result.passed
    assert any("clean" in f for f in result.failures)


def test_missing_obvious_malware_fails() -> None:
    result = run_smoke(FakeClient(misses_malicious=True))
    assert not result.passed
    assert any("missed" in f for f in result.failures)


def test_a_warning_in_the_description_fails() -> None:
    # Observed at 7b, not only at 1.5b — the boundary is checked at every size.
    result = run_smoke(FakeClient(description="⚠ SECURITY: the code is clean and well-structured"))
    assert not result.passed
    assert any("description" in f for f in result.failures)


def test_drifting_scores_fail() -> None:
    result = run_smoke(FakeClient(drifting_scores=True))
    assert not result.passed
    assert any("different scores" in f for f in result.failures)


def test_a_client_that_cannot_answer_fails_once() -> None:
    result = run_smoke(FakeClient(raises=True))
    assert not result.passed
    assert len(result.failures) == 1
    assert "could not produce a grade" in result.failures[0]


def test_a_client_that_cannot_check_malware_returns_a_failure_result() -> None:
    result = run_smoke(FlagsFailingClient())
    assert not result.passed
    assert len(result.failures) == 1
    assert "could not complete smoke checks" in result.failures[0]


def test_two_faults_are_reported_together() -> None:
    """Fixing one and rediscovering the other is a worse experience than being
    told both, so the checks do not short-circuit."""
    result = run_smoke(FakeClient(flags_clean=True, misses_malicious=True))
    assert not result.passed
    assert len(result.failures) >= 2


@pytest.mark.parametrize("prefix", ["⚠", "WARNING:", "SECURITY —"])
def test_every_warning_prefix_is_caught(prefix: str) -> None:
    result = run_smoke(FakeClient(description=f"{prefix} something"))
    assert not result.passed


class ProbabilisticClient(FakeClient):
    """Flags clean code some of the time, like a small model under a bad prompt.

    Measured: qwen2.5-coder:1.5b under the seed's prompt flagged a clean digest
    9 times in 12. A gate that asks once clears that model on roughly a quarter
    of attempts, so the gate has to sample.
    """

    def __init__(self, *, flag_every: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.flag_every = flag_every
        self._flag_calls = 0

    def flags_malicious(self, digest: str) -> bool:
        if digest == CLEAN_DIGEST:
            self._flag_calls += 1
            return self._flag_calls % self.flag_every == 0
        return super().flags_malicious(digest)


def test_a_model_that_only_sometimes_flags_clean_code_still_fails() -> None:
    # Fails on the last sample only: a single-sample gate would have passed it.
    result = run_smoke(ProbabilisticClient(flag_every=5))
    assert not result.passed
    assert any("samples" in f for f in result.failures)


def test_the_clean_check_reports_how_often_it_happened() -> None:
    result = run_smoke(ProbabilisticClient(flag_every=1))
    assert not result.passed
    assert any("5 of 5" in f for f in result.failures)


class LateWarningClient(FakeClient):
    """Writes the warning prefix on one sample out of several — the 7b behaviour."""

    def __init__(self, *, warn_on_call: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.warn_on_call = warn_on_call
        self._n = 0

    def evaluate(self, digest: str) -> GradeResult:
        self._n += 1
        base = super().evaluate(digest)
        if self._n != self.warn_on_call:
            return base
        return GradeResult(
            idea=base.idea,
            skill=base.skill,
            description="⚠ SECURITY: the code is clean and well-structured",
            model=base.model,
            temperature=base.temperature,
            prompt_version=base.prompt_version,
        )


def test_a_warning_on_a_later_sample_is_still_caught() -> None:
    # The first sample is clean; checking only `repeats[0]` would miss this.
    result = run_smoke(LateWarningClient(warn_on_call=4))
    assert not result.passed
    assert any("description" in f for f in result.failures)
