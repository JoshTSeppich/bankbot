# ADR-0005: Policy model

Status: Proposed
Date: 2026-09-19

## Context

The system drives a browser against a banking application with an LLM in the
loop during discovery. Two classes of harm must be prevented by construction:
the agent wandering off the allowed application (other hosts, other routes,
unexpected action types), and the agent performing irreversible actions such
as transfers, closures or deletions without a human approving them. A third
concern is data handling: run logs and screenshots must never contain
credentials or member PII, because they are committed as evidence.

The policy must be declarative and reviewable by someone who is not a
programmer, applied at the same choke point for both discovery and replay,
and small enough that the interviewer can read the whole file.

## Decision

## Alternatives rejected

## Consequences
