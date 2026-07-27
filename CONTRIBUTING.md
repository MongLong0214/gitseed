# Contributing to gitseed

gitseed collects GitHub repository candidates, screens and grades them, then
presents a ranking for human review.

Run the test suite with:

```sh
python3 -m pytest
```

`dev` is the default integration branch. Create `feat-issue-<id>` from `dev`
and merge it back through a pull request; `main` is reserved for released
work.

Each ticket lands on `dev` as one commit, so pull requests are squash merged.
Before deleting the topic branch, use `commitlore squash-preserve` to carry its
records onto the squash result. Do not combine this workflow with `--no-ff`,
which creates a two-parent merge instead of the required squash commit.

For decisions worth preserving, follow the CommitLore trailer convention in
[AGENTS.md](AGENTS.md).

A live run must never star or follow without a human decision. Approval requires
an interactive terminal; piped or non-interactive input is not human approval.
