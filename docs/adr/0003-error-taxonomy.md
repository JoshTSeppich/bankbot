# ADR-0003: Error taxonomy

Status: Accepted
Date: 2026-09-19

## Context

The brief lists concrete runtime conditions replay must handle: member not
found, validation errors, permission denied, unexpected interstitial dialogs,
session expiry, slow loads. These are not all failures. "No member found" is a
legitimate answer to the question the capability asks; a session expiry is a
detour with a known way back; an unrecognised modal is something the engine
cannot resolve alone.

Treating them uniformly as exceptions would hide the distinction the caller
needs: did the capability answer, did it recover, or did it stop. The replay
result type and the artifact's outcome/recovery sections must encode that
distinction explicitly, and errors must be named after the condition
(`MemberNotFound`) rather than the mechanism (`ElementTimeoutError`).

## Decision

A replay ends in one of three shapes, and the rules that decide which
live in the artifact.

```
replay(capability, params) -> Success | Outcome | Failure
```

`Success` carries the typed outputs. `Outcome` carries a code the
artifact declared (`member_not_found`): the capability reached a known
end state that is an answer, not a fault. `Failure` carries the step,
what was expected, what was observed, and the intervention request if a
human was asked. All three point at the run directory.

After every attempted step, replay asks five questions in a fixed order:

| Order | Question | Source | If yes |
|---|---|---|---|
| 1 | Does a known outcome match the page? | `outcomes[]` | End the run as `Outcome`. |
| 2 | Did the step raise a native dialog nobody recorded an answer to? | `surface.take_dialogs()` | Ask a human, reason `unknown_dialog`. The declared retries are not spent. |
| 3 | Does a known recovery match, or does the step name one? | `recoveries[]`, `on_fail: recover` | Run its steps, then retry, bounded by `max_attempts`. |
| 4 | Does the step allow another go? | `on_fail: retry(n)` | Retry. |
| 5 | Otherwise | | Ask a human. Unattended, the answer is abort and the run is a `Failure` carrying the request. |

Outcome is checked first so that a page saying "No member found" ends
the run even when the step that got there technically succeeded.

The dialog question sits at two, and the position is the decision. A page
that already says "no member found" is still an answer, so the outcome check
keeps its place above it. Everything below it assumes the click happened and
something about the page is wrong. A dismissed confirm means it did not
happen, so a recovery has nothing to recover from and a retry only asks the
same question again. It goes straight to a person and the step's retries are
left unspent.

Every native dialog is answered the moment it opens, by one listener in the
surface, and that is not a preference. While one is held open I measured
`page.title()` and `locator.count()` hanging with no timeout of their own,
and `screenshot()` and `aria_snapshot()` timing out. `observe()` uses all
four. Keeping a dialog on screen for a person to look at would freeze every
way this system has of looking at the page, including the screenshot the
operator page is made of.

So the answer is given first and classified afterwards. An alert is accepted
and logged as `native_dialog`, and the step carries on: it had one possible
answer and it was given. A confirm, a prompt or a beforeunload is dismissed,
which is the vendor's own "No", and escalated as `unknown_dialog` carrying
what was asked and what we answered.

Two things this does not do, each a cut with a design behind it:

- An outcome rule cannot match a dialog's message. `_matching_outcome` only
  asks `surface.holds()` about the page, so a legacy app that says "No member
  found" through `alert()` burns its retries and ends `checkpoint_unmet` with
  the answer sitting in one log line. The design is a `matches` field that
  can name a dialog message pattern as well as page state, checked against
  the dialogs the step raised.
- An operator cannot accept a confirm. ADR-0004 has the limit and the
  single-use accept that fixes it.

Errors are named after the condition: `TargetNotFound`, `MemberNotFound`
as an outcome code, `SecretMissing`. Never after the mechanism.

## Alternatives rejected

- Exceptions for everything: a caller cannot tell "no such member"
  from "the button moved" without parsing messages, and the first is
  the answer to the question the capability asks.
- Retries everywhere as the default: a retry on a page that says the
  session expired wastes the step budget and hides the real condition.
  The default `on_fail` is `fail`.
- Classification in engine code: a new vendor message would mean a
  code change. In the artifact it is one more `outcomes[]` entry.

## Consequences

Easier: the evidence runs show all three shapes from the same
capability with no engine change between them. The operator page has
the expected and observed text for every failure.

Harder: `Outcome` and `Recovery` matchers are `StateAssertion`s checked
with a short timeout after every step, which costs about 300 ms per
step on the happy path. I took the hit for the classification order.

Preconditions are the other use of the recoveries: the artifact says
what must be true before step one ("Signed in as" is visible), and a
fresh browser meets it by running the matching recovery before any step
is tried. Logging in is a recovery, and `recoveries_used` says so on
every run; nothing has to fail first.

To revisit: a compiled recovery restarts the run from the first step
(`resume_from_step`), because after a re-login the app is on its home
screen and the interrupted step has nothing to retry into. Approvals are
cleared on that rewind so an approved risky step asks again. A recovery
that could resume in place would need the compiler to know which steps
are idempotent, which it does not.
