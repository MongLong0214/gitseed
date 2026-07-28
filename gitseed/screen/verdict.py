"""Severity from signals.

Three levels, not a boolean. The seed collapsed this to `security_flag`, and a
single bit cannot carry the difference between "this ships a payload" and "this
mentions an IP address" — so a user who wanted to act on the strong case had to
accept the weak one too.
"""

from __future__ import annotations

from typing import Final, Sequence

from ..evidence import ClaimBasis
from .coverage import SourceCoverage
from .signals import HIGH, LOW, Signal

NONE: Final = "none"
#: Zero deterministic findings, but the scan that produced them was not
#: everything policy called eligible -- caps or fetch errors left files
#: unlooked-at. Distinct from `NONE` on purpose: `NONE` is a claim that
#: nothing worth seeing was missed, and this value is available exactly when
#: that claim cannot be made.
NONE_FOUND_IN_SCANNED_FILES: Final = "none-found-in-scanned-files"


def findings(signals: Sequence[Signal]) -> tuple[Signal, ...]:
    return tuple(signal for signal in signals if signal.basis is ClaimBasis.DETERMINISTIC)


def unverified(signals: Sequence[Signal]) -> tuple[Signal, ...]:
    return tuple(signal for signal in signals if signal.basis is ClaimBasis.MODEL)


def severity_of(signals: Sequence[Signal]) -> str:
    """`high` if anything is high, else `low` if anything is low, else `none`."""
    deterministic = findings(signals)
    if any(signal.severity == HIGH for signal in deterministic):
        return HIGH
    if any(signal.severity == LOW for signal in deterministic):
        return LOW
    return NONE


def risk_of(signals: Sequence[Signal], coverage: SourceCoverage | None) -> str:
    """`severity_of`, refined by what was actually scanned to produce it.

    A bare `none` asserts that everything policy judged worth scanning was
    scanned, and none of it was suspicious. When `coverage` says the scan was
    cut short by caps or fetch errors before it covered every eligible file,
    that assertion is not available -- so this returns
    `NONE_FOUND_IN_SCANNED_FILES` instead: still zero findings, but not a
    clean bill of health. A `high` or `low` verdict is unaffected: a finding
    that was seen does not become less true because other files were not.
    """
    severity = severity_of(signals)
    if severity == NONE and coverage is not None and not coverage.complete_for_policy:
        return NONE_FOUND_IN_SCANNED_FILES
    return severity
