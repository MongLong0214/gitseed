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
