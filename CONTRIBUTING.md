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

### Local CommitLore setup

CommitLore is not available from a package registry. Configure the distributed
bundle locally, then reinstall the commit hook:

```sh
COMMITLORE=/absolute/path/to/commitlore/dist/commitlore.mjs
git config commitlore.bin "$COMMITLORE"
node "$COMMITLORE" hooks install
node "$COMMITLORE" inject install-claude-hook \
  --command "node \"$COMMITLORE\" inject --hook-input # commitlore-inject-hook"
```

`dist/cli.js` is a development entry point that requires `node_modules`; use
the bundled `dist/commitlore.mjs` instead. Both the local Git config and
`.claude/settings.json` contain machine-specific paths and must not be
committed.

A live run must never star or follow without a human decision. Approval requires
an interactive terminal; piped or non-interactive input is not human approval.
