# ADR-0006: Surface abstraction

Status: Proposed
Date: 2026-09-19

## Context

The brief asks how the system would extend from a browser to a desktop
application (a Java Swing or WinForms core banking client) without rewriting
the artifact. Today the only surface is a web page driven by Playwright, and
Playwright's own handles, selectors and snapshot references are ephemeral:
none of them can be stored in an artifact and still mean something next week,
let alone on another kind of surface.

Discovery, compile and replay all need to perceive a screen and act on it.
If each of them imports Playwright, the browser leaks into every module and
the desktop story is a rewrite. If the artifact stores anything Playwright
specific, the same is true of the data.

Constraints: one implementation only in this build, no speculative
abstraction beyond what the artifact already demands, and a seam small enough
to describe in one sentence.

## Decision

## Alternatives rejected

## Consequences
