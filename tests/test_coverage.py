"""SourceCoverage: distinguishing a scan that finished from one the caps cut short.

`complete_for_policy` and `complete_for_repository` are computed properties,
not constructor arguments -- deriving them from the counts they describe is
the only way they cannot drift out of sync with those counts.
"""

from __future__ import annotations

from gitseed.screen.coverage import SkippedFile, SourceCoverage


def test_a_full_policy_scan_is_not_automatically_a_full_repository_scan() -> None:
    # Given: every eligible file was scanned, but the repository holds more
    # (non-eligible) files than that.
    coverage = SourceCoverage(discovered_files=12, eligible_files=5, scanned_files=5)
    # Then: the policy promise is kept; the stronger, whole-repository claim is not.
    assert coverage.complete_for_policy is True
    assert coverage.complete_for_repository is False


def test_scanning_everything_discovered_is_complete_for_the_repository_too() -> None:
    coverage = SourceCoverage(discovered_files=5, eligible_files=5, scanned_files=5)
    assert coverage.complete_for_policy is True
    assert coverage.complete_for_repository is True


def test_a_count_capped_scan_is_incomplete_for_policy() -> None:
    """GS-P0-008's shape: 200 eligible, 20 scanned."""
    coverage = SourceCoverage(discovered_files=200, eligible_files=200, scanned_files=20)
    assert coverage.complete_for_policy is False
    assert coverage.complete_for_repository is False


def test_an_error_skip_makes_the_scan_incomplete_for_policy_even_if_the_counts_match() -> None:
    # Given: scanned_files equals eligible_files on paper, but a fetch error
    # is on record -- an inconsistency this property refuses to paper over
    # even though the raw count comparison alone would call it complete.
    coverage = SourceCoverage(
        discovered_files=5,
        eligible_files=5,
        scanned_files=5,
        skipped_error=(SkippedFile("flaky.py", "GitHub returned HTTP 500"),),
    )
    assert coverage.complete_for_policy is False


def test_skipped_files_are_paired_with_a_reason() -> None:
    skip = SkippedFile("big.py", "exceeds 100000-byte cap")
    assert skip.path == "big.py"
    assert skip.reason == "exceeds 100000-byte cap"
