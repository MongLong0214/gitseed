# gitseed

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="gitseed repository triage: find, screen, grade, then pass through a human approval gate">
</p>

gitseed finds GitHub repositories, screens the files it can read for deterministic security signals, and grades survivors with a local model.

It presents a ranked review queue for a human to decide one item at a time. `--dry-run` is the default.

An incomplete run is not presented as a quiet one: collection limits, file-reading failures, and model failures remain in the output.

[![CI](https://github.com/MongLong0214/gitseed/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/MongLong0214/gitseed/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9, 3.11, 3.13](https://img.shields.io/badge/Python-3.9%20%7C%203.11%20%7C%203.13-3776ab)](.github/workflows/ci.yml)

## Approval is an argument, not a check

The external-write functions in [`gitseed/review/actions.py`](gitseed/review/actions.py) require an `Approval` value:

```python
def star(client: GitHubWriter, repo: str, approval: Approval) -> Performed:
def follow(client: GitHubWriter, user: str, approval: Approval) -> Performed:
```

The production CLI gets that value from `collect_approval()`, which refuses non-TTY input and reads a terminal decision. A branch such as `if approved:` can disappear; the required argument remains at every `star()` and `follow()` call. The review path records the decision as CommitLore trailers.

No live star or follow has been performed by this code.

## Quick start: replay the fixture

This command needs no network and no GitHub token. It replays the checked-in candidates and grades:

```sh
python3 -m gitseed run --query x --fixtures tests/fixtures
```

The replay prints the ranked clean fixture and withholds the malicious fixture after deterministic screening. For a live run, model selection uses `OLLAMA_MODEL` when set; otherwise it chooses the first installed preferred model in this order: `qwen2.5-coder:32b`, `qwen2.5-coder:7b`, `qwen2.5-coder:1.5b`.

## CLI

`radar` runs the review queue and defaults to `--dry-run`; external actions remain behind an interactive `Approval`. Its score measures small-versus-medium size; it does not predict that a repository will take off.

```sh
python3 -m gitseed radar --query "small tools" --artifact run.json
python3 -m gitseed explain owner/repo --artifact run.json
python3 -m gitseed export run.json > exported-run.json
python3 -m gitseed radar --replay run.json
```

`explain` shows feature contributions, the weight-set version, and unavailable inputs. `export` writes the canonical versioned artifact, so a consumer can round-trip it with the same schema. A replayed artifact is labelled as its source in CLI status output.

Exit codes: `0` complete; `1` invalid invocation or operational failure; `2` incomplete run.

## What it does not do yet

- The review queue's full offline fixture cycle runs under a real PTY; production still refuses piped approval.
- The forbidden-resource 403 branch has only been exercised with injected responses, not a live API response. See the [required live evidence](docs/tickets/F1-collect.md#remaining-live-evidence-issue-6) and [Backlog #6](https://github.com/MongLong0214/gitseed/issues/6).

## How the work is structured

The pipeline is [F1 collection](docs/tickets/F1-collect.md) → [F2 deterministic screening](docs/tickets/F2-screen.md) → [F3 local-model grading](docs/tickets/F3-grade.md) → [F4 human review](docs/tickets/F4-review.md). The [ticket index](docs/tickets/TICKETS.md) records that order and its dependencies.

Decisions worth keeping are stored as [CommitLore trailers](AGENTS.md). The observed branch layout is `dev` for current work and `main` alongside it for released work.

`phase-gate.py` is not present in this checkout or its Git history, so this README does not claim a phase-gate workflow that cannot be inspected.

## Development checks

CI runs the test suite, the fixture replay, and bytecode compilation on Python 3.9, 3.11, and 3.13.

```sh
python3 -m pytest tests/ -q
python3 -m gitseed run --query x --fixtures tests/fixtures
python3 -m compileall gitseed
```

## License

[MIT](LICENSE)
