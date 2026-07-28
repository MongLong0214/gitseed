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

For live grading, use `--grade-timeout` (in seconds, default 120) to control how long to wait for the model to respond to each file digest. Smaller models or slower machines may need a higher timeout; if grading times out, the tool will print a fix message with the current value and how to increase it.

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

## Known limitations (as of the 2026-07-28 review)

A static review of `dev` at `d0e1ecd` found gaps between what the security-screening and
recommendation machinery is built to do and what a live run actually does. None of these are
addressed yet; each is tracked as its own issue rather than fixed silently.

- **The live scanner does not read `package.json`, or any other manifest, lockfile, or
  workflow file.** `SOURCE_EXTENSIONS` in `gitseed/cli.py` is an allow-list of source-code
  extensions only — no `.json` entry — so a live run's file selector never hands
  `package.json` to the scanner. The `postinstall`/`preinstall` detection rule in
  `gitseed/screen/signals.py` is implemented and passes its unit tests, but those tests read a
  fixture directory directly, bypassing the live selector; against a real GitHub repository,
  a malicious `postinstall` hook in `package.json` is currently invisible to gitseed.
  ([issue #45](https://github.com/MongLong0214/gitseed/issues/45))
- **`recommendation: review` means "not blocked by a high-risk deterministic finding," not
  "safe" or "worth reviewing."** A candidate with zero readable files, zero score coverage,
  and an `unknown` risk verdict currently renders identically to one that was fully scanned
  and came back clean. ([issue #46](https://github.com/MongLong0214/gitseed/issues/46))
- **Files skipped by the 20-file/500KB screening caps do not affect severity or
  recommendation**, and selection currently follows raw file-tree order — a repository can be
  structured so a malicious file past the 20th position is never scanned, with no signal that
  this happened. ([issue #48](https://github.com/MongLong0214/gitseed/issues/48),
  [issue #49](https://github.com/MongLong0214/gitseed/issues/49))
- **The radar table, `--json`/`explain` output, and the interactive approval queue currently
  rank candidates by two different scores** — the table by the deterministic metadata score,
  the approval prompt by the local model's `idea + skill` grade — so the order a person
  reviews on screen is not guaranteed to be the order they are asked to approve.
  ([issue #36](https://github.com/MongLong0214/gitseed/issues/36))

None of this is "safe": read it as "no high-risk pattern was found in the files that were
actually scanned," not as a security guarantee about the repository as a whole.

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
