# F2 — Deterministic security screen (keystone)

It is the **keystone** because it is the only layer that works without an LLM. Even if F3
(model grading) fails the smoke test, the pipeline must complete with F2 alone (ADR-0002 invariant 1).

## T-201 · Signal extractor

**Module**: `gitseed/screen/signals.py`

```python
@dataclass(frozen=True)
class Signal:
    kind: str          # "install-script" | "obfuscation" | "network" | "postinstall" | "dep"
    severity: str      # "low" | "high"
    path: str          # citation: file path
    line: int          # citation: 1-based line number
    excerpt: str       # citation: that line (maximum 120 characters)

def scan_text(path: str, text: str) -> list[Signal]: ...
```

**Rules** (at least 1 test each):
| kind | Detection | severity |
|---|---|---|
| `install-script` | `curl`/`wget` output piped to `sh`/`bash` | high |
| `obfuscation` | base64 blob at least 200 characters, or hex literal at least 200 characters | high |
| `postinstall` | `postinstall`/`preinstall` in `package.json` | high |
| `network` | Hard-coded IP, or `.onion`/shortened-URL domain | low |
| `dep` | Dependency name matches the known typosquatting list | high |

> **Record status (2026-07-28)**: `dep` is ruled out, not implemented — see
> `docs/prd/PRD-F2-screen.md` and commit trailers. `signals.py` has no `dep` `Signal.kind` and no
> test names one; this row records the original ticket, not current scope.

**AC (mechanical decision)**
- [ ] Every `Signal` has `path`, `line`, and `excerpt`. Constructing a signal without a citation
      raises `ValueError`. Test verifies this
- [ ] 0 signals across 10 clean samples (`tests/fixtures/clean/`)
- [ ] At least 1 of each kind across 5 malicious-pattern samples (`tests/fixtures/malicious/`)
- [ ] `scan_text` does not call a network, model, or subprocess (import-inspection test)
- [ ] 2 calls with the same input return the same list (determinism)

## T-202 · Severity aggregation

**Module**: `gitseed/screen/verdict.py`

```python
def severity_of(signals: Sequence[Signal]) -> str:  # "none" | "low" | "high"
```

At least 1 `high` signal → `high`. Only `low` signals → `low`. None → `none`.
**Do not collapse this into a boolean** — the seed's `security_flag` was that failure.

**AC**
- [ ] Test each of the three paths
- [ ] An empty list is `none` (not an exception)

## Correction — 2026-07-28 deep review

T-201's own AC are satisfied: every implemented `Signal` kind is tested against the fixtures
named above, at the `scan_text()` level. What this ticket does not cover, and what a deep
review of `dev` at `d0e1ecd` found missing, is whether a real GitHub file selection ever hands
this module the files its rules are written for:

- **The live file selector excludes `package.json` and every other manifest, lockfile, and
  workflow file** — `SOURCE_EXTENSIONS` in `gitseed/cli.py` has no `.json` entry, so the
  `postinstall` rule above is implemented, unit-tested against a fixture directory, and never
  reached in a live run. Tracked as
  [issue #45](https://github.com/MongLong0214/gitseed/issues/45) (GS-P0-001). No E2E test
  exists that starts from `GitHubClient.fetch_files()` rather than a fixture directory read —
  that gap is exactly why this was not caught by `tests/`.
- **Files excluded by the 20-file/500KB caps do not affect `severity_of()`'s output or
  `recommendation`.** `severity_of()` describes only the files that were scanned; nothing
  distinguishes "scanned everything eligible, found nothing" from "scanned 20 of 200 eligible
  files, found nothing in those 20." Tracked as
  [issue #48](https://github.com/MongLong0214/gitseed/issues/48) (GS-P0-008).
- **Selection order is raw git-tree order**, not prioritized by filename risk — a repository
  can be structured with 20+ innocuous files sorted ahead of a malicious one, pushing it past
  the count cap with no signal that this happened. Tracked as
  [issue #49](https://github.com/MongLong0214/gitseed/issues/49) (GS-P1-018).

None of this is a defect in `signals.py` or `verdict.py` themselves — `scan_text()` and
`severity_of()` do exactly what T-201/T-202 specify, on the input they are given. The gap is
upstream of this module, in what live input selection ever gives it to scan.
