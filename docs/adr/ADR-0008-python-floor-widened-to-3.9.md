# ADR-0008: widen the Python floor to 3.9

- Status: Accepted (2026-07-28)
- Supersedes: the Python 3.11+ floor recorded in `docs/adr/ADR-0003-language-runtime.md`; ADR-0003 remains the historical record, unchanged.

## Context

ADR-0003 set the language floor at Python 3.11+. `pyproject.toml` has since read
`requires-python = ">=3.9"`, and `.github/workflows/ci.yml` has tested `["3.9", "3.11",
"3.13"]`, for as long as either file has existed in this repository — this was not a drift
that crept in after ADR-0003; the floor was widened at build time.

The widening was not silent. `gitseed/ports.py` carries `# noqa: SLOTS_OK -- dataclass
slots require Python 3.10.` on every frozen dataclass, and the commit that added those
port types recorded the cost directly:

```
Name the core run ports
...
Limit: Python 3.9 support prevents dataclass slots
Record-Id: r-gsf501
```

The same `# noqa: SLOTS_OK` marker now appears on frozen dataclasses in `gitseed/scoring.py`,
`gitseed/artifact.py`, `gitseed/category.py`, `gitseed/application.py`, and
`gitseed/storage_schema.py` — every one of them a place where `@dataclass(frozen=True,
slots=True)` would otherwise have been the obvious choice, and was not, because `slots=True`
requires 3.10.

A later pass translating the ADRs to English noticed the mismatch and chose not to fix it:

```
Translate ADR records to English

Limit: ADR-0003 records Python 3.11+; current CI lists Python 3.9 as supported.
Ruled-out: correcting current-record differences | ADRs preserve the decisions made at the time
Record-Id: r-enadr17
```

That was correct for a translation pass — an ADR is a record of what was decided and why,
and a translation commit is not the place to change the decision it records. But the gap it
flagged was never closed with an actual decision record of its own. This ADR is that record.

## Decision

**Python 3.9+ is the floor**, not 3.11+. `pyproject.toml`'s `requires-python = ">=3.9"` and the
CI matrix's inclusion of `"3.9"` are the current, correct statement of support; ADR-0003's
"Python 3.11+" line was superseded by this widening and is retained only as history.

The floor was widened in exchange for a known, accepted cost: dataclasses in this codebase
cannot use `slots=True`, so every frozen dataclass that would otherwise declare it carries a
`# noqa: SLOTS_OK` comment recording why it does not. This trade was made deliberately, not
discovered after the fact — keep the comments; they are the record of the trade at each site
it applies, the same way this ADR is the record of the trade itself.

Standard-library-first and minimal-dependency choices in ADR-0003 are unaffected and remain in
force.

## Consequences

- No frozen dataclass in this codebase may add `slots=True` while the 3.9 floor holds; a
  reviewer who sees one without a `# noqa: SLOTS_OK` comment nearby has found either a Python
  version regression risk or a missed comment, not a false positive.
- `pyproject.toml` and `.github/workflows/ci.yml` are the operative record of supported
  versions from this ADR forward; `ADR-0003` is not edited and should not be read as current.

## Revisit condition

Revisit this ADR only when the project is prepared to drop Python 3.9 from both
`pyproject.toml` and the CI matrix, and a maintainer records a specific reason `slots=True`
is worth that cost (a measured memory or attribute-access benefit, not a hypothetical one —
the standard this project holds performance claims to elsewhere, e.g. ADR-0007's scoring
inputs and the model-tag-caching question in `docs/prd/PRD-F3-grade.md`). Widening again to
a higher floor without dropping 3.9 support does not reopen this decision.
