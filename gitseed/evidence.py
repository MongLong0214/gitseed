from __future__ import annotations

from enum import Enum


class ClaimBasis(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    ABSENT = "absent"
