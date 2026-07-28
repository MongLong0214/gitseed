# M0 Verdict — Does a simple score predict actual growth?

Analysis input: `tests/fixtures/m0/samples.json`

Offline result: `tests/fixtures/m0/analysis.json`

## Sample and completeness

The three preregistered GitHub Search queries selected 40 repositories each, for a
total of 120. Without `GITHUB_TOKEN`, the fixture's `collection_complete` is `false`;
this records that the small sample must not be represented as complete collection. The
full Search API responses are also preserved in
`tests/fixtures/m0/search-responses.json`.

| Item | Count |
| --- | ---: |
| Selected repositories | 120 |
| Scoreable repositories | 118 |
| Repositories unavailable to query or clone | 0 |
| Not scoreable because no commit existed before the cutoff | 2 |
| Scoreable: coding-agents / mcp / local-ai | 39 / 40 / 39 |
| breakout (`stars >= 100`) | 56 |

Therefore the number of inaccessible repositories (AC-3) is **0**. The two
non-scoreable repositories were not inaccessible; they were `alexei-led/cc-thingz` and
`jan3dev/a1echos`, which had no commit at three months after creation, and were excluded
only from analysis under the preregistered rule.

## Preregistered verdict

Only the preregistered simple sum score (7 binary features), breakout threshold
`stars >= 100`, primary metric ROC AUC, and verdict threshold `0.65` were used.

| Metric | Preregistered threshold | Actual value | Verdict |
| --- | ---: | ---: | --- |
| ROC AUC | 0.65 or higher | 0.7432315668 | Pass |

The conditional numerical result is **not null**. However, because collection ran
without an authentication token, completeness for the full M0 sample is insufficient.
This result means “pass in the fixed stored sample,” not a confirmed product claim from
a complete sample of 120 or more repositories.

## Contributions and v0.2 scope

These are the AUC decreases when each feature is removed from the score in turn. Only a
positive value means the feature contributed to this simple score.

| Feature | AUC decrease |
| --- | ---: |
| `commit_cadence_30d` | +0.093318 |
| `contributor_count` | +0.016129 |
| `has_license` | +0.009649 |
| `has_ci` | +0.000144 |
| `readme_bytes` | -0.003312 |
| `has_tests` | -0.025778 |
| `manifest_present` | -0.031970 |

v0.2 score candidates are limited to `commit_cadence_30d`, `contributor_count`, and
`has_license`. `has_ci` made no material contribution, and the three negative features
are not included in the score on this measurement alone. This is neither exploration
for new weights nor a product star-prediction feature.

## What this verdict licenses

The positive-class base rate is 56 / 118 = 47.5% (approximately 47%). At that base
rate, the classifier separates small repositories from medium-sized repositories, not
unknown repositories from breakout repositories.

The stronger claim that “gitseed finds repositories before they take off” would require
a different label: a much higher star threshold, or growth since a past date rather than
an absolute star count today. That label requires historical star data, which the GitHub
API does not provide and which this run therefore could not use.

The three contributing features license v0.2 to build
`commit_cadence_30d`, `contributor_count`, and `has_license`, and to drop the remaining
forty components of PRD §14; that was ADR-0005's whole purpose. They do not license a
discovery claim in the README.

## Reproduction and cutoff verification

Run the following command without a network.

```bash
PYTHONPATH=. python3 scripts/m0_analyze.py --fixture tests/fixtures/m0/samples.json
```

The same command was run three times to confirm byte-identical JSON results, and
`tests/test_m0.py` also analyzes the same fixture three times (AC-5). That test proves
that `has_tests` and other features actually change when the cutoff in a local Git
repository moves from 2025-04-01 to 2025-06-01. The feature implementation reads only
the cutoff commit's tree and `git log --before=<cutoff>` (AC-2).

## Limits

- Survivorship bias: only currently accessible GitHub repositories and current star
  counts are observed; deleted repositories cannot appear in Search results.
- Sample size: 118 scoreable repositories is two short of the per-category target of
  40, and no-token collection cannot guarantee completeness.
- Category concentration: topic Search results depend on repositories that carry each
  topic and on GitHub's search ordering.
- A star is only a proxy for success, not product quality, maintainability, or user
  value itself.
