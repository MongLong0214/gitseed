# gitseed

`gitseed` finds GitHub repositories, screens their visible files for deterministic
security signals, grades the remaining candidates, and presents the full ranking
for human review. It is a triage tool, not a bootstrap or scaffolding tool.

Run the included offline replay without making a network call:

```sh
python3 -m gitseed run --query x --fixtures tests/fixtures
```

The default is `--dry-run`: it prints the ranked table and exits without asking
for approval or writing to GitHub. This is deliberate. Starring or following is
an external account action, so an accidental invocation must never perform it.

Use `--json` when another program needs the result:

```sh
python3 -m gitseed run --query x --fixtures tests/fixtures --json
```

For a live run, set `GITHUB_TOKEN` for GitHub API access and `OLLAMA_MODEL` for
the local Ollama model (the default model is `qwen2.5-coder:7b`):

```sh
GITHUB_TOKEN=... OLLAMA_MODEL=... python3 -m gitseed run --query 'language:python topic:cli'
```

An incomplete collection or pipeline exits 2 after still printing its ranking;
the candidates shown are then only the best of what could be inspected. To make
review decisions, explicitly opt out of dry-run on a real terminal:

```sh
python3 -m gitseed run --query 'language:python topic:cli' --no-dry-run
```

`--approve-all` still requires one terminal decision and is refused on piped or
non-interactive input. The resulting CommitLore trailer block is printed for the
operator to paste into a commit.
