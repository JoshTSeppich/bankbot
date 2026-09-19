# ADR-0001: Artifact schema

Status: Proposed
Date: 2026-09-19

## Context

The brief grades system design first, and the artifact is the system's
central contract: discovery produces it, replay consumes it, the operator
approves it, and a future desktop surface must be able to execute it
unchanged. It must record how each control is identified together with the
reasoning for that choice, expose typed inputs and outputs so a capability
can be invoked like a function, and carry the error taxonomy (known outcomes,
recoveries, per-step failure policy) so that replay behaviour is data rather
than engine code.

The schema is also the multi-tenant hook: an `app.variant` plus a semver
version is the seam through which a per-credit-union override could later be
applied without re-recording.

Constraints: Pydantic v2 models with exported JSON Schema, strict on unknown
fields so hand-written artifacts fail loudly, and every field explainable in
one sentence.

## Decision

## Alternatives rejected

## Consequences
