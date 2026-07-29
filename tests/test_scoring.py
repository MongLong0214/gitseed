from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from gitseed.scoring import (
    ALL_FEATURES,
    WEIGHT_VERSION,
    Feature,
    Recommendation,
    RecommendationStatus,
    ScoreInputs,
    ScoreVersionMismatch,
    score,
)
from gitseed.evidence import ClaimBasis
from gitseed.screen.signals import HIGH, LOW


def test_m0_contributions_are_the_weight_set() -> None:
    # Given: every measured feature is present and positive.
    features = ScoreInputs(
        commit_cadence_30d=True,
        contributor_count=True,
        has_license=True,
    )

    # When: the M0 weight set is applied.
    result = score(features)

    # Then: the score is the exact sum of the three reported AUC decreases.
    assert result.value == Decimal("0.119096")
    assert result.version == WEIGHT_VERSION
    assert result.coverage == frozenset(ALL_FEATURES)
    assert result.complete
    assert result.incomplete_because == ()


def test_inv_008_version_changes_identity_and_blocks_ordering() -> None:
    # Given: equal numeric results produced by different weight-set versions.
    current = score(ScoreInputs(True, True, True))
    reweighted = replace(current, version="m0-contributions-v2")

    # When/Then: version remains part of identity and cross-version ordering fails.
    assert current != reweighted
    with pytest.raises(ScoreVersionMismatch):
        sorted((current, reweighted))


def test_inv_010_coverage_distinguishes_missing_from_false() -> None:
    # Given: one result lacks contributor evidence and one observes it as false.
    missing = score(ScoreInputs(True, None, False))
    observed_false = score(ScoreInputs(True, False, False))

    # When/Then: equal numeric values still carry different evidence claims.
    assert missing.value == observed_false.value
    assert missing != observed_false
    assert missing.coverage == frozenset(
        {Feature.COMMIT_CADENCE_30D, Feature.HAS_LICENSE}
    )
    assert observed_false.coverage == frozenset(ALL_FEATURES)
    assert not missing.complete
    assert missing.incomplete_because == ("contributor_count unavailable",)


def test_inv_002_high_risk_blocks_top_score() -> None:
    # Given: the highest possible measured score.
    top_score = score(ScoreInputs(True, True, True))

    # When: separate recommendation values receive different risk verdicts.
    high_risk = Recommendation(top_score, HIGH)
    low_risk = Recommendation(top_score, LOW)

    # Then: risk gates recommendation instead of reducing the numeric score.
    assert high_risk.status is RecommendationStatus.BLOCKED
    assert low_risk.status is RecommendationStatus.REVIEW
    assert high_risk.score == low_risk.score == top_score


def test_a_zero_score_is_not_recommended() -> None:
    # Given: complete evidence that every scored feature is false.
    recommendation = Recommendation(score(ScoreInputs(False, False, False)), LOW)

    # When/Then: zero merit is a negative decision, not an affirmative review.
    assert recommendation.status is RecommendationStatus.NOT_PRIORITY


def test_missing_score_evidence_is_distinct_from_not_priority() -> None:
    # Given: no metadata observation and no security verdict from unread files.
    recommendation = Recommendation(score(ScoreInputs(None, None, None)), "unknown")

    # When/Then: the caller can distinguish insufficient evidence from rejection.
    assert recommendation.status is RecommendationStatus.INSUFFICIENT_EVIDENCE


def test_recommendations_only_shrink_from_the_old_predicate() -> None:
    # Given: good, zero-score, missing-evidence, and blocking candidates.
    candidates = {
        "good": Recommendation(score(ScoreInputs(True, True, True)), LOW),
        "zero": Recommendation(score(ScoreInputs(False, False, False)), LOW),
        "missing": Recommendation(score(ScoreInputs(None, None, None)), "unknown"),
        "blocked": Recommendation(score(ScoreInputs(True, True, True)), HIGH),
    }

    # When: the old predicate and new affirmative status are compared.
    old_recommended = {name for name, item in candidates.items() if item.risk_verdict != HIGH}
    new_recommended = {
        name
        for name, item in candidates.items()
        if item.status is RecommendationStatus.REVIEW
    }

    # Then: no candidate becomes recommended as a side effect of the fix.
    assert new_recommended == {"good"}
    assert new_recommended <= old_recommended


def test_same_inputs_produce_identical_score() -> None:
    # Given: a partially observed feature set.
    features = ScoreInputs(False, True, None)

    # When: scoring runs twice.
    first = score(features)
    second = score(features)

    # Then: value, version, coverage, and incompleteness are identical.
    assert first == second
    assert first.incomplete_because == second.incomplete_because


def test_raw_metadata_preserves_the_existing_score_status_and_ranking() -> None:
    # The boolean fixture is the pre-issue-64 input shape. The measured fixture
    # contains the same observations without collapsing their values.
    before = {
        "org/four": ScoreInputs(True, True, True),
        "org/zero": ScoreInputs(False, False, False),
        "org/unavailable": ScoreInputs(None, None, None),
    }
    after = {
        "org/four": ScoreInputs.observed(4, 2, {"spdx_id": "MIT"}),
        "org/zero": ScoreInputs.observed(0, 0, None, license_basis=ClaimBasis.DETERMINISTIC),
        "org/unavailable": ScoreInputs.observed(None, None, None),
    }

    def rendered(inputs):
        recommendations = {
            repo: Recommendation(score(values), LOW)
            for repo, values in inputs.items()
        }
        return (
            {
                repo: (item.score.value, item.score.coverage, item.status)
                for repo, item in recommendations.items()
            },
            [
                repo
                for repo, _ in sorted(
                    recommendations.items(),
                    key=lambda item: (-item[1].score.value, item[0]),
                )
            ],
        )

    assert rendered(after) == rendered(before)


def test_scoring_path_has_no_external_or_time_dependent_imports() -> None:
    # Given: the complete scoring module source.
    source = (
        Path(__file__).parents[1] / "gitseed" / "scoring.py"
    ).read_text(encoding="utf-8")

    # When: its absolute imports are enumerated.
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    # Then: scoring cannot reach a clock, random source, model, or network.
    forbidden = {
        "datetime",
        "httpx",
        "ollama",
        "random",
        "requests",
        "socket",
        "subprocess",
        "time",
        "urllib",
    }
    assert imported & forbidden == set()
