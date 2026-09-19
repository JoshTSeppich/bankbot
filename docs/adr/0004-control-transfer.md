# ADR-0004: Control transfer between automation and human

Status: Proposed
Date: 2026-09-19

## Context

Some situations are outside what the engine can resolve: all locator
candidates exhausted, an unknown dialog, a step classed as risky without
approval, or discovery reporting no progress. The brief requires a
human-in-the-loop path in which an operator can take over, act, and hand
control back, with the automation resuming coherently rather than restarting.

The handoff must be over the same live browser the automation is driving, so
the human sees exactly the state the engine saw. While the human is in
control the engine must keep recording what happens, because those human
actions are the richest signal for repairing the artifact. On resume the
engine cannot assume the page is where it left it.

Constraints: single process, a minimal operator page, no realtime
infrastructure, and a state model small enough to draw on a whiteboard.

## Decision

## Alternatives rejected

## Consequences
