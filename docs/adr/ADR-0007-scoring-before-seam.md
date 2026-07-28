# ADR-0007 — Build scoring before the core seam

Status: accepted · Scope: issues #8 and #12 · Date: 2026-07-27

## Context

The v0.2 sequence puts the Core Seam (#8) before scoring (#12). That would make
the seam's scoring boundary speculative: before M0, PRD §14 proposed roughly
forty components and several composite scores, but M0 found material signal in
only three inputs:

- `commit_cadence_30d`
- `contributor_count`
- `has_license`

M0 also limits the claim. Its positive class was 56 of 118 repositories (47.5%),
so the measured score separates small repositories from medium-sized ones. It
does not predict that an unknown repository will take off.

## Decision

Implement the deterministic three-input score and recommendation gate before
the Core Seam. Issue #8 will then design its port against the scorer's actual
inputs, versioned result, evidence coverage, and risk-gated recommendation
instead of a hypothetical forty-component payload.

This changes delivery order, not dependency direction. Scoring remains pure:
it imports no CLI, adapter, network, model, persistence, or clock.

## Consequences

- Issue #12 stays small and records the measured weight set as a version.
- Issue #8 gains a concrete boundary to connect to its application use case and
  run artifact.
- The other PRD §14 components remain rejected until new measurement licenses
  them.
- No user-facing output may describe this score as a breakout prediction.

## Falsification

This order was wrong if implementing #8 cannot consume the public scoring
inputs and outputs without a breaking change to them, or if the scorer must
import a concrete adapter, CLI type, persistence type, network client, model,
or clock to fit the real application use case. A future reader can check this
from the #8 diff and the scoring module's imports.
