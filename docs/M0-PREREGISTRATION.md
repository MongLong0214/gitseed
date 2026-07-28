# M0 Preregistration — Does a simple score predict actual growth?

Registered on: 2026-07-27

This document is append-only. Changes after collection or analysis add their date and
reason at the end of this document rather than editing existing content.

## Question and sample

Question: does a simple score made only from information in the Git history through
3 months after repository creation rank repositories with a current star breakout
above non-breakout repositories?

- Categories and fixed GitHub Search queries:
  - `coding-agents`: `topic:coding-agents created:2025-01-27..2025-07-27 fork:false archived:false`
  - `mcp`: `topic:mcp created:2025-01-27..2025-07-27 fork:false archived:false`
  - `local-ai`: `topic:local-ai created:2025-01-27..2025-07-27 fork:false archived:false`
- The first 40 returned by each query, ordered ascending by `created`, are the sample,
  for a total of 120. If a repository appears in two or more queries, it is kept only
  in the first category in which it appeared.
- Record the count and name of every category that returns fewer than 40, every API
  interruption, clone failure, and inaccessible repository. Do not change a query to
  replenish the sample.
- The label is breakout when `stargazers_count` at collection is 100 or more, and
  non-breakout otherwise. Read the creation date and current star count only from the
  GitHub Search API response.

## Cutoff and score

The cutoff is the same UTC time exactly 3 calendar months after the API's
`created_at`. Each feature is calculated only from the commit found by
`git rev-list -1 --before=<cutoff> HEAD`, that commit's tree, and
`git log --before=<cutoff>`. A repository with no commit before the cutoff is recorded
as accessible and non-scoreable, and excluded from analysis.

The score is the simple sum (0–7) of the following 7 binary features. Do not change
weights or features after analysis.

| Feature | Predefined condition for score 1 |
| --- | --- |
| `has_tests` | The cutoff tree has at least one path named `test` or `tests`. |
| `has_ci` | The cutoff tree has a file under `.github/workflows/`, `.gitlab-ci.yml`, or `Jenkinsfile`. |
| `has_license` | The cutoff tree root has a file whose name starts with `license`, case-insensitively. |
| `commit_cadence_30d` | At least 4 reachable commits occur in the 30 days immediately before the cutoff. |
| `contributor_count` | At least 2 Git commit author emails occur at or before the cutoff. |
| `readme_bytes` | The total of README-family files in the cutoff tree root is at least 1,000 bytes. |
| `manifest_present` | The cutoff tree has at least one of `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, or `Gemfile`. |

## Verdict

The primary verdict metric is ROC AUC of the score distinguishing the breakout label
across all scoreable samples. Ties use average ranks. An AUC of **0.65 or higher** is
judged meaningfully better ranking than random(0.50); below 0.65, the result under this
preregistration is null. precision, subsets, and post-hoc selected thresholds are not
used for the verdict.

Report contribution only as the AUC decrease when each feature is removed from the
score in turn. A positive decrease means that feature contributed to this simple score;
this value is evidence for limiting candidates to implement in v0.2, not exploration
for new features or weights.

## Reproduction rules

Store collected Search responses and cutoff feature results in `tests/fixtures/m0/`.
Analysis reads only that fixture, runs without a network, and must produce the same JSON
result on 3 runs with the same input. In a separate local Git fixture, a mutation
that moves the cutoff forward must change at least one feature value.

## Approaches already ruled out

- Do not expose star prediction as a product feature. M0 is an evaluation, not a product score.
- Do not increase performance by adding features beyond 7. Interpretability is the purpose.
- Do not hide a null result or search for other metrics or subsets to change the conclusion.
