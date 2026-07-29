# ADR-0013: Search order is a collection policy, not a discovery claim

- Status: Accepted (2026-07-29)
- Scope: `gitseed/collect/search.py`, run artifact schema 8, issue #67

## Context

Issue #67 combines two different questions:

1. Which repositories should GitHub Search return first?
2. Are those repositories better candidates for a later increase in attention?

The first is a collection policy. Gitseed can choose and disclose an observable ordering such
as repository update time, creation time, or current star count. The second is a measurement
claim. ADR-0012 establishes that gitseed has neither an expected-attention baseline nor
time-separated outcomes that could validate such a claim. A repository with few stars and a
recent update is only a repository with few stars and a recent update.

The previous request sent `q`, `per_page`, and `page`, leaving GitHub Search to use its default
best-match order. The artifact retained the user query, limit, returned candidates,
`total_count`, and `incomplete_results`, but not the omitted `sort` and `order` parameters.
Changing those defaults would therefore change the candidate population while leaving old
and new runs looking like equivalent requests.

GitHub Search caps the accessible result set and can report `incomplete_results`. Ordering
determines which repositories occupy a capped or otherwise partial prefix. A partial
best-match result and a partial update-ordered result are different partial populations even
when their query text and candidate counts match.

## Decision

**Order repository search by GitHub's repository update field, descending. This is a
collection preference for recent updates, not evidence of quality, future attention, or any
other outcome.**

Each request uses these parameters:

```text
q=<operator-supplied query>
sort=updated
order=desc
per_page=min(limit, 100)
page=1..ceil(limit / 100)
```

The artifact continues to record `limit` in its input. Schema 8 also records the exact
`q`, `sort`, `order`, `pages`, and `per_page` values beside the collection result, and adds
`search=github-search-v1` to `EngineVersions`. The candidate snapshot remains the record of
what GitHub returned at that time. The parameters make the request reconstructable and runs
distinguishable; reissuing a request later cannot recreate GitHub's historical state.

No star threshold, creation-date threshold, language partition, topic partition, or
multi-query merge is added. Those would be additional collection policies with additional
exclusions. There is no visible outcome against which to optimize their values.

The existing completeness account remains independent:

- `incomplete_results` is retained exactly as reported;
- `total_count`, pages fetched, rate-limit stops, and permission failures remain visible;
- a truncated result is not rejected or relabeled as complete; and
- its recorded ordering states which partial prefix was requested.

The approval gate is also independent. Search determines the candidate input to the existing
review pipeline; it does not approve an action, bypass the terminal requirement, change the
dry-run default, or add an external-write path.

This policy is a judgment that recency of repository updates is the simplest observable
freshness preference. It is not an optimum. The judgment is falsified if a preregistered
offline comparison on fixed queries and captured responses shows that `updated desc` does
not increase the share of results whose recorded `updated_at` falls inside the declared
recency window relative to best match. That test would evaluate freshness only. Any claim
about later attention remains subject to ADR-0012's separate outcome-data requirements.

## Alternatives considered and rejected

**Keep GitHub's default best-match order.** Rejected: it leaves the collection preference
implicit and does not express the chosen preference for recent updates.

**Sort or filter by low current star count.** Rejected: a star threshold would be arbitrary,
and low observed attention alone says nothing about repository quality or later attention.
Under a result cap it would also select a different partial population.

**Sort by creation time.** Rejected: repository age is not repository update recency and
would exclude older repositories with recent updates from the front of the result set.

**Partition and merge multiple date, language, topic, or star buckets.** Rejected: merge
weights and bucket boundaries would introduce more unvalidated policy choices without a
visible outcome. A single native ordering is sufficient for the stated freshness policy.

**Describe the new order as improving early discovery.** Rejected: no attention baseline or
later outcome exists to measure that claim, as ADR-0012 records.

**Reject every capped or incomplete search.** Rejected by the issue #47 decision: completeness
must be visible, while refusal is a separate product policy. This decision preserves that
boundary.

## Consequences

- A candidate set collected under the previous best-match order is not population-comparable
  with one collected under this policy.
- Candidate sets collected on different dates are also not population-comparable merely
  because their parameters match: repository state and GitHub's index can change between
  requests.
- Artifacts make these differences inspectable by retaining both the candidate snapshot and
  exact search parameters.
- Changing any default search parameter requires a new search engine version and a new
  decision describing the policy and comparability break.
- Scoring, recommendation status, approval, external writes, and dry-run behavior do not
  change.
