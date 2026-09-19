# ADR-0003: Error taxonomy

Status: Proposed
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

## Alternatives rejected

## Consequences
