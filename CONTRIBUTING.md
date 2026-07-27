# Contributing to gitseed

gitseed collects GitHub repository candidates, screens and grades them, then
presents a ranking for human review.

Run the test suite with:

```sh
python3 -m pytest
```

`develop` is the default integration branch. Create a topic branch from
`develop` and merge it back through a pull request; `main` is reserved for
released work.

For decisions worth preserving, follow the CommitLore trailer convention in
[AGENTS.md](AGENTS.md).

A live run must never star or follow without a human decision. Approval requires
an interactive terminal; piped or non-interactive input is not human approval.
