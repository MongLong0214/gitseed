# gitseed

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="gitseed repository triage: find, screen, grade, then pass through a human approval gate">
</p>

gitseed finds GitHub repositories worth a look, screens them for deterministic signals, and presents a ranked queue for a human to decide one at a time.

It does not have a growth or undervaluation score yet: the data to compute one does not exist.

```sh
python3 -m pip install git+https://github.com/MongLong0214/gitseed.git
```

`--dry-run` is the default. An incomplete run is not presented as a quiet one: collection limits, file-reading failures, and model failures remain in the output. Any external write action requires interactive human approval.

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

For live grading, GitSeed sends **bounded evidence** to the local model: a deterministic digest of at most 23,000 UTF-8 bytes inside a complete prompt capped at 24,000 bytes. The digest declares sampled, included, and omitted source bytes, so a model grade is a judgment over representative evidence rather than a full-repository review. Deterministic screening still receives the complete selected source set.

`--grade-timeout` defaults to 240 seconds and applies per model response. A positive explicit value remains authoritative. A timeout is a visible incomplete candidate failure; increasing a timeout does not make an oversized unbounded prompt safe because GitSeed rejects such prompts before sending them.

## CLI

`radar` runs the review queue and defaults to `--dry-run`; external actions remain behind an interactive `Approval`. Its score measures small-versus-medium size; it does not predict that a repository will take off.

```sh
python3 -m gitseed radar --query "small tools" --artifact run.json
python3 -m gitseed explain owner/repo --artifact run.json
python3 -m gitseed export run.json > exported-run.json
python3 -m gitseed render run.json
python3 -m gitseed replay run.json
python3 -m gitseed re-evaluate run.json
```

`render` shows the stored output unchanged. `replay` re-runs the recorded inputs only when its pipeline, screening, source-selection, and category-pack versions match this release. `re-evaluate` runs stored `full-source` artifacts under the current engine. Artifacts default to `digest` source storage; choose `--source-mode full-source` only when re-evaluation is required. Engine versions are hand-maintained semantic identifiers, bumped when that engine's observable output changes.

Exit codes: `0` complete; `1` invalid invocation or operational failure; `2` incomplete run.

## Known limitations

- **Non-priority source-file selection still follows raw git-tree order, truncated at the
  20-file scan cap.** `GitHubClient.fetch_files` (`gitseed/cli.py`) selects manifests,
  lockfiles, and CI workflow files (`PRIORITY_FILENAMES`, matched by `_is_priority_path`)
  before the count cap is applied at all, so those specific files can no longer be hidden by
  their position in the tree. Every other eligible file is still taken in the order the GitHub
  tree API returns it: a repository can still be structured so that a non-manifest file past
  the 20th eligible position is never scanned. This is no longer silent, though — when the cap
  or a fetch error leaves eligible files unscanned, `risk_of` (`gitseed/screen/verdict.py`)
  reports `none-found-in-scanned-files` instead of a bare `none`, `Recommendation.status`
  (`gitseed/scoring.py`) treats that as `insufficient-evidence` rather than a positive
  recommendation, and `explain` prints the exact scanned/eligible/discovered file counts.
  ([issue #49](https://github.com/MongLong0214/gitseed/issues/49) — commit
  [`02d96b9`](https://github.com/MongLong0214/gitseed/commit/02d96b985945a67048432b1cb1a1dea1077a74d9)
  exempted priority filenames from the tree-order cap and deliberately left general-file
  ordering unchanged; see that commit's `Ruled-out` trailer)

This is not a security guarantee about the repository as a whole: it is "no high-risk pattern
was found in the files this run actually scanned," and `explain`'s file-coverage line says
whether that was every eligible file or a capped subset of them.

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
