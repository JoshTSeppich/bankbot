# ADR-0002: Locator strategy

Status: Proposed
Date: 2026-09-19

## Context

The target is a hostile legacy web app: table layouts, an iframe, no ids or
test ids, and markup that may shift between visits. Replay must find the same
control a human would find, without an LLM, and must degrade predictably
rather than silently clicking the wrong thing.

The brief asks explicitly for "how each control is identified" and
"robustness reasoning". Discovery observes the page through a Playwright
accessibility snapshot whose element references are ephemeral, so whatever
the artifact persists must be reconstructed into durable, human-meaningful
targets at compile time.

Drift is the long-term risk: a target that resolves today may resolve
differently after a vendor UI change, and the system needs an early signal
before a hard failure.

## Decision

## Alternatives rejected

## Consequences
