# ADR-0010: `recommended: bool` is replaced by an evidence-aware status

- Status: Accepted (2026-07-28)
- Scope: `gitseed/scoring.py::Recommendation`, every consumer of `.recommended` (`gitseed/cli.py`, `gitseed/artifact.py`)

## Context

The 2026-07-28 deep review (GS-P0-006) found that `Recommendation.recommended` is defined as:

```python
@property
def recommended(self) -> bool:
    return self.risk_verdict != HIGH
```

This makes every one of the following read as `recommended = True`: a candidate with score 0,
score coverage 0/3, all metadata unavailable, `risk_verdict = "unknown"`, and the model
unavailable. The CLI renders the boolean as the string `"review"`, which a user reads as an
affirmative signal. What the property actually tests is narrower and purely negative: the
absence of a high-risk deterministic finding. Combined with GS-P0-008 (partial security
coverage not reflected in severity) and GS-P0-007 (search incompleteness not reflected in
completeness), a repository that was barely examined at all and one that was thoroughly
examined and came back clean currently present identically.

The review proposes replacing the boolean with a four-state enum:

```python
class RecommendationStatus(Enum):
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"
    REVIEW = "review"
    NOT_PRIORITY = "not-priority"
```

with the mapping: high risk → `BLOCKED`; security or score evidence absent →
`INSUFFICIENT_EVIDENCE` regardless of risk; sufficient evidence and a passed threshold →
`REVIEW`; sufficient evidence and a failed threshold → `NOT_PRIORITY`.

This ADR assesses that proposal on its own terms rather than adopting it by default.

## Decision

**The boolean is removed. Four states is the right design, and gitseed adopts the review's
proposed `RecommendationStatus` enum as specified**, because it separates two questions this
project has already, elsewhere, treated as genuinely independent, and does so with the
smallest state space that keeps them independent:

1. **Was there a blocking safety finding?** (`BLOCKED` vs. not) — this is the deterministic
   screen's question, and F2's own design principle (`docs/tickets/F2-screen.md`: "Do not
   collapse this into a boolean") already establishes that a security verdict must not be
   flattened to yes/no. `BLOCKED` is that principle applied one level up, at the
   recommendation boundary instead of only at `severity_of()`.
2. **Is there enough evidence to have an opinion at all?** (`INSUFFICIENT_EVIDENCE` vs. not) —
   this is new: nothing today distinguishes "we looked and it's fine" from "we couldn't tell."
   It is also exactly the gap GS-P0-007 and GS-P0-008 describe, so this state is not
   speculative scope creep; it is required to close two P0 findings this same review names.

Given those two independent yes/no questions, four states is not an arbitrary number chosen
for its own sake — it is what two independent binary axes produce, and collapsing either axis
back into the other is what produced GS-P0-006 in the first place. `REVIEW` and
`NOT_PRIORITY` are not new information beyond what the deterministic score already carries
(a repository with sufficient evidence that fails the score threshold was always
distinguishable from one that passes it); naming both states explicitly, rather than leaving
"passed threshold" as an implicit reading of a raw score number, is what makes the sufficient-
evidence branch self-describing at the same status-field granularity as the other branch,
instead of asking a reader to infer meaning from a number in one branch and a label in the
other.

The mapping is exactly as the review specifies: `high risk -> BLOCKED`; absent security or
score coverage -> `INSUFFICIENT_EVIDENCE`, evaluated before evidence-sufficient checks, so a
`high`-risk-but-evidence-absent candidate must still resolve deterministically to one status —
`BLOCKED` takes priority, since a blocking safety finding is authoritative regardless of how
much other evidence exists or is missing; `INSUFFICIENT_EVIDENCE` only applies when risk is
not already `BLOCKED`. Sufficient evidence plus a passed score threshold -> `REVIEW`;
sufficient evidence plus a failed threshold -> `NOT_PRIORITY`.

## Alternatives considered and rejected

**Keep a boolean, rename it.** (`recommended -> reviewable` or `not_blocked`, per the review's
"names to change" list, §18.) Rejected as insufficient on its own: renaming fixes the
overclaiming problem (a boolean called `not_blocked` no longer implies safety) but does
nothing about GS-P0-007/GS-P0-008 — a zero-coverage candidate still reads identically to a
fully-scanned clean one under any two-valued type. A rename is compatible with this ADR as a
transitional step but is not a substitute for it.

**Three states, folding `INSUFFICIENT_EVIDENCE` into `BLOCKED`.** Rejected: "we found a
malicious pattern" and "we couldn't examine enough of the repository to have an opinion" are
different findings that call for different next actions from a reviewer — the first says
"do not act," the second says "this needs more collection budget or manual inspection before
a decision is meaningful." Conflating them would make `BLOCKED` fire on two unrelated causes,
reintroducing exactly the ambiguity this ADR removes from `recommended`.

**More than four states** (e.g. separating "score-evidence-absent" from
"security-evidence-absent" as distinct statuses, or splitting `NOT_PRIORITY` by how far below
threshold). Rejected for now as unmeasured: no failure in this review turns on distinguishing
*which* evidence is missing at the status level — coverage detail (GS-P0-008's proposed
`SourceCoverage`) already carries that information at a finer grain, alongside the status,
without needing the top-level enum to grow. If a real consumer need for finer-grained status
emerges, it should be added the way GS-P0-006 itself was found: from a specific, evidenced
failure, not preemptively.

## Consequences

- `Recommendation.recommended: bool` is removed from the public type; every consumer
  (`gitseed/cli.py`'s radar/JSON/explain rendering, `gitseed/artifact.py`'s serialization, and
  the single ranking path established by ADR-0009) is updated in the same change, so no code
  path is left reading the removed meaning.
- `severity_of()`'s three-state discipline (`none`/`low`/`high`, F2's own rule) is unaffected;
  `RecommendationStatus.BLOCKED` is derived from it, not a replacement for it.
- Coverage detail (GS-P0-008) is what `INSUFFICIENT_EVIDENCE` is computed from; this ADR
  depends on that issue landing evidence/coverage fields to evaluate against, and should land
  in the same body of work.
- JSON/API consumers of the current boolean field face a breaking change. Given v0.2.0 is the
  first tagged release and the project has already made comparable breaking changes without a
  deprecation period (e.g. ADR-0006's removal of unattended writes), no deprecation shim is
  planned; this is recorded here so a future reader does not need to ask why one wasn't
  added.

## Falsification

This decision was wrong if a real consumer of `RecommendationStatus` needs to tell
`INSUFFICIENT_EVIDENCE` caused by absent security coverage apart from `INSUFFICIENT_EVIDENCE`
caused by absent score coverage *at the status level* (not the coverage-detail level) to make
a correct decision — that would mean four states under-specify the space this ADR claims they
cover, and the "unmeasured" call above was wrong. A future reader can check this against
`SourceCoverage`/score-coverage consumers once GS-P0-008 lands.
