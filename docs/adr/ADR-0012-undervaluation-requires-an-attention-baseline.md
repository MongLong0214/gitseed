# ADR-0012: Undervaluation requires an expected-attention baseline

- Status: Accepted (2026-07-29)
- Scope: `gitseed/scoring.py`, `gitseed/adapters.py`, run artifact schema 6, repository star observations, issue #63

## Context

The current deterministic score is:

```text
0.093318 * (commits in 30 days >= 4)
+ 0.016129 * (total contributors >= 2)
+ 0.009649 * (a license is present)
```

Those coefficients are the leave-one-feature-out ROC AUC decreases measured by M0, not a
calibrated probability or an estimate of future growth. The inputs describe recent commit
activity, breadth of contribution, and basic project hygiene. The score is therefore an
activity signal. M0 associated it with the current label `stars >= 100` in a fixed,
incompletely collected sample whose positive-class rate was 56/118 (47.5%). As
`docs/M0-VERDICT.md` records, that separates small repositories from medium-sized ones; it
does not predict breakout.

An undervaluation claim has a different shape:

```text
undervaluation = expected attention - observed attention
```

The current score has no attention term or expected-attention baseline. Adding current stars
as a denominator would add popularity arithmetic, but it would not establish what attention
should be expected for a comparable repository or whether the result predicts future growth.

Issue #64 and PR #89 stopped discarding observations. Schema-6 artifacts now contain:

- the run query and observation time;
- each candidate's repository identity, current star count, and `pushed_at`;
- exact commits in the preceding 30 days, total contributor count, and the license payload,
  each with an evidence basis; and
- the existing score and recommendation result derived from the same observations.

PR #88 also began appending `(repository, observed_at, stars)` observations to the local run
store. It deliberately stores raw counts rather than deltas.

The artifact does not contain repository age, forks, a deterministic category assignment, or
a complete category population. A run query is not a category: it can be arbitrary, limited,
incomplete, and ordered by GitHub Search. The M0 fixture names categories, but its 39/40/39
scoreable repositories are an incomplete selected sample and it has no later outcome for
those repositories. It can support a sample-relative calculation, not a category-population
percentile or a future-breakout label.

As of this decision, this repository also contains no repeated-observation fixture for a
repository. A single `(time, stars)` point cannot produce star velocity. GitHub exposes the
current star count but not the historical star-count series, so observations before PR #88
cannot be recovered later.

## Decision

**Keep the current score unchanged and describe it only as an activity signal. Gitseed must
not emit an undervaluation, momentum, growth, or breakout score from the data available
today.**

Can gitseed compute an expected-attention baseline from what it collects today? **No.** It
has observed attention for individual repositories, but neither a category population from
which to estimate expected attention nor enough time-separated outcomes to validate the
estimate.

A future discovery score should express undervaluation, because discovery requires comparing
observed attention with attention expected for comparable repositories. It must be a
separate, versioned measurement rather than a silent reinterpretation of
`m0-contributions-v1`, and it may affect recommendation verdicts only through an explicit
product decision with before-and-after distributions.

The honest boundary is:

- **Computable today:** the existing activity score; current observed stars; raw commit,
  contributor, and license observations; and descriptive comparisons within a named,
  explicitly incomplete run. None is an undervaluation or growth estimate.
- **Computable after repeated observations accumulate:** star change and elapsed-time star
  velocity for repositories observed at least twice. In the coming weeks the local store can
  provide those post-PR-#88 observations for repositories that recur. That licenses the
  descriptive claim "observed star velocity over this recorded interval," not expected
  attention, category-relative undervaluation, or future breakout.
- **Still required for undervaluation:** a versioned category definition; a stated and
  sufficiently complete category cohort; a fixed feature time, outcome horizon, and breakout
  label; and enough later observations to evaluate whether an attention residual predicts
  that outcome out of sample. A percentile needs the cohort, and a velocity term needs at
  least two time-separated observations.
- **Not recoverable:** star history before this project first observed a repository, deleted
  repositories omitted by survivorship, and attention outside the chosen observable proxy.
  These remain stated limits of any later measurement rather than values to infer or
  backfill.

Before implementation, the proposed baseline, label, primary metric, comparison baseline, and
numeric pass threshold must be preregistered as required by ADR-0011. The repository must then
contain a fixed offline fixture with feature-time and outcome-time observations plus a
deterministic analysis command. A held-out example with known later outcomes must make the
ranking checkable. If the residual does not clear its preregistered threshold against simpler
baselines such as current stars and the existing activity score, it does not ship.

No scoring, adapter, artifact, storage-schema, or recommendation code changes are made by
this decision. Existing verdicts therefore do not move.

## Alternatives considered and rejected

**Divide or subtract the activity score by current stars.** Rejected: stars would be an
observed-attention denominator, but the numerator is not expected attention. The formula
would silently rename activity-per-star as undervaluation without a category baseline,
outcome label, or known-good example.

**Use a percentile within the current search results.** Rejected: a limited GitHub Search
result is not a category population. Its query, ordering, truncation, and completeness affect
the percentile, so identical repositories could receive different meanings in different
runs.

**Ship star velocity as soon as two observations exist.** Rejected as a score, but retained
as a descriptive future measurement. Two observations can establish what happened over one
recorded interval; they cannot establish what attention was expected or whether the velocity
predicts a later breakout.

## Consequences

- `m0-contributions-v1` and all recommendation statuses remain unchanged.
- Schema-6 raw metadata and post-PR-#88 star observations are retained as measurement inputs,
  not treated as evidence that the measurement has already passed.
- Future data collection must distinguish category membership and population completeness
  from an arbitrary search query before a category-relative baseline is possible.
- Any later discovery score needs its own version, offline evidence, claim boundary, and
  explicit verdict-impact decision.

## Falsification

The finding that undervaluation is not computable today is falsified when a repository-owned,
offline dataset contains all of the following for a versioned category cohort: deterministic
membership and population completeness, observations at the declared feature time, later
observations at the declared outcome horizon, and a preregistered analysis that reproduces an
expected-attention residual and tests it against known outcomes.

The future scoring decision is falsified if that residual fails its preregistered metric or
does not outperform the declared simpler baseline on held-out repositories. In either case,
gitseed keeps the activity score and rejects the undervaluation score; arithmetic alone is
not a ranking result.
