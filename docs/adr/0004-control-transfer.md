# ADR-0004: Control transfer between automation and human

Status: Accepted
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

One state machine, one object, one lock, same process, same browser.

```
AUTOMATION -> INTERVENTION_REQUESTED -> HUMAN -> RESUME_REQUESTED -> AUTOMATION
                                   \-> ABORTED   (from any live state)
AUTOMATION -> FINISHED  (the run ended)
```

`RunController` holds the state for one run and is shared between the
engine thread and the FastAPI operator app. The engine calls
`request()` and blocks inside it until a person answers. Blocking inside
the step loop is what lets the engine continue from the same step: on
resume it re-runs the step it stopped on, or, if the person marked the
step complete, checks the step's `wait_for` before trusting them.

| Decision | Choice | Why |
|---|---|---|
| Same browser | The person drives the page the engine opened, headed | They see exactly what the engine saw. There is no second session to get out of sync. |
| While waiting | The engine pumps Playwright, takes a masked screenshot every second, and records page events | The sync client only hears about events inside a Playwright call. A sleeping engine would record nothing. |
| Recording window | From the ask to the answer, not only while HUMAN | The engine is idle for exactly that span, so anything that happens on the page was a person. |
| What is recorded | Clicks, edits and navigations as `human_action` events: kind, element description, frame, URL | Typed values are dropped in the browser, before they reach Python. |
| Answers | Hand back (retry), mark step complete (check `wait_for`, then next step), abort | Three buttons. A stale button from a second tab is ignored and the page shows the real state. |
| Lease | The operator page pings every 10 s while HUMAN; three misses aborts with reason `operator_lost`. Nobody taking control within 15 minutes aborts with reason `nobody_came` | A bank session is never held open for nobody, before or after someone arrives. |
| Abort | Legal from any live state; replay asks the controller before every step, and a request on an aborted run is answered ABORT at once | An operator who sees a run going wrong can stop it before the next step, not only when it next asks. |
| Operator page | Two Jinja pages, one stylesheet, the heartbeat is the only script | The mechanism is the deliverable; the page is not. |

## Alternatives rejected

- A second browser for the human (or a remote desktop): two sessions,
  two cookies, and the engine cannot see what the person did.
- Restart the run after a human fixed the page: loses the state the
  person just created, and a restart on a bank page can repeat a
  side-effecting step.
- WebSockets or a queue for the operator page: a two-second meta
  refresh and a one-second screenshot are enough for one operator, and
  there is nothing to explain.
- Multi-operator, auth, a real console: out of scope. Mocked and named
  as such in the README.

A confirm-guarded step can only end in abort, even with a person driving.
The dialog listener is registered once, in the surface's constructor, and it
answers for whoever raised the dialog. The operator's own click on the
guarded control is dismissed the same way the engine's was. So hand back
loops into the same click and the same confirm, and mark step complete
cannot work either, because the person could not get past the confirm to
complete it. Today the only honest exit is abort.

The fix is not a longer-lived dialog: ADR-0003 has the measurement that says
a dialog held open freezes the surface. It is a single-use accept. A step
gains an `on_dialog` expectation holding the dialog type, a pattern the
message must match, and the answer to give. The policy treats a step with
one as risky, so an unattended run asks a person before the step rather than
after the dialog. The approval arms the listener to accept exactly once, for
exactly that step, and it is recorded like any other approval: one attempt,
cleared on a recovery rewind, in the log with who gave it. An artifact can
then carry "this vendor asks 'Restricted member. Continue?' and the answer
is yes", which is a reviewable fact about the app, rather than a person
saying yes into a browser where nothing records it.

## Consequences

Easier: the handoff is testable end to end in one process with a fake
person on the engine thread. Evidence run 5 is that test with a real
person.

Harder: the engine thread owns Playwright, so a person's actions can
only be observed, never scripted from the operator thread. Every test
that plays the person does so from the engine's pump.

To revisit: recorded human actions are stored, not used. Turning them
into a repaired step is the next build (REPORT §7).
