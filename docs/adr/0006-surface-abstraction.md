# ADR-0006: Surface abstraction

Status: Accepted
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

`bankbot/surface/` is the only module that imports Playwright. It
exposes one Protocol:

```
observe() -> Observation            frames, ARIA tree per frame, masked screenshot, dialog text
resolve(TargetRef) -> index         which candidate won, or TargetNotFound with what was tried
act(ActionType, TargetRef, value)   navigate, click, type, select
read(TargetRef) -> text
holds(StateAssertion) -> bool
fingerprint() -> AppFingerprint
start_trace / stop_trace
idle(ms), watch_human, unwatch_human
```

Discovery, compile, replay and control talk to that and nothing else.
The artifact stores only what the Protocol takes: semantic targets
(role, name, label, text, structural path, bounding box, frame path) and
state assertions (URL pattern, visible text, visible target). No
Playwright handle, selector object or snapshot ref is ever persisted.

`ElementFacts`, what the surface reports about an element it acted on,
is the compiler's only input for building candidates. It is computed
in one browser-side evaluation: role, accessible name, label, text,
row header, structural path, bounding box.

Test suites that need a browser share one Chromium and one demo app
per session, because the sync Playwright client allows one driver per
thread.

## Alternatives rejected

- A second implementation now (a fake surface for tests): the tests
  drive the real demo app, which is what the brief is about. A second
  implementation without a second real surface is an abstraction with
  one use.
- Letting replay import Playwright for the hard cases (dialogs,
  tracing): the moment one module reaches around the seam, the desktop
  story is gone.
- A generic "driver" interface with dozens of methods: the artifact
  only needs the nine above.

## Consequences

Easier: the desktop question has a concrete answer. An `AXSurface` over
a platform accessibility API resolves the same `TargetRef` candidates
(role and name map directly; structural path becomes an AX tree path;
bounding box is already screen space) and reports the same
`ElementFacts`. The artifact does not change.

Harder: everything the engine wants to know about the page has to be
expressed as an `Observation` or a `StateAssertion`. There is no escape
hatch, on purpose.

Not built: the desktop surface itself. The seam is the deliverable.
