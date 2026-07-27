"""Severity from signals.

Three levels, not a boolean. The seed collapsed this to `security_flag`, and a
single bit cannot carry the difference between "this ships a payload" and "this
mentions an IP address" — so a user who wanted to act on the strong case had to
accept the weak one too.
"""

from __future__ import annotations

from typing import Final, Sequence

from ..evidence import ClaimBasis
from .signals import HIGH, LOW, Signal

NONE: Final = "none"


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
