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

After every attempted step, replay asks four questions in a fixed order:

| Order | Question | Source | If yes |
|---|---|---|---|
| 1 | Does a known outcome match the page? | `outcomes[]` | End the run as `Outcome`. |
| 2 | Does a known recovery match, or does the step name one? | `recoveries[]`, `on_fail: recover` | Run its steps, then retry, bounded by `max_attempts`. |
| 3 | Does the step allow another go? | `on_fail: retry(n)` | Retry. |
| 4 | Otherwise | | Ask a human. Unattended, the answer is abort and the run is a `Failure` carrying the request. |

Outcome is checked first so that a page saying "No member found" ends
the run even when the step that got there technically succeeded.

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

To revisit: recoveries retry the interrupted step. A recovery that
should resume elsewhere uses `resume_from_step`, which nothing in this
build exercises.
