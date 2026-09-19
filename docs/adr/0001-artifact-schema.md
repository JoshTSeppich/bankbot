# ADR-0001: Artifact schema

Status: Accepted
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

One Pydantic model, `Capability`, is the whole contract. Discovery
produces it, replay consumes it, the operator page reads it, and the
verifier validates it. It lives in `bankbot/schemas/artifact.py` and is
exported as JSON Schema under `docs/schema/`.

| Decision | Choice | Why |
|---|---|---|
| Unknown fields | `extra="forbid"` on every model | Artifacts are hand-edited and machine-compiled. A misspelled key fails at load, not by silently disabling a recovery. |
| References | Validated at load | Every `ParamRef`, `OutputRef`, `Recover` and `resume_from_step` must point at something declared, and step ids must be unique across steps and recoveries. A broken artifact is rejected before it touches a bank. |
| Where a value is read | The output declares the target; the extract step names the output | One place per value. The step says when, the output says where. |
| Inputs vs secrets | `ParamRef` and `SecretRef` are different types | A secret is resolved from the environment by name at run time and can never be a caller-supplied input. The artifact and the log only ever hold the name. |
| Finding a control | `TargetRef` is an ordered list of `Candidate` with strategy, value, confidence and reasoning, plus `frame_path` | ADR-0002. |
| Error taxonomy in data | `outcomes[]`, `recoveries[]`, per-step `on_fail` | ADR-0003. Replay's behaviour on a known condition is in the artifact, not in engine code. |
| Provenance | `created_from_run` carries model id, SDK versions, timestamps and the API request ids | The evidence in the repo can be tied to real model calls. |
| Drift detection | `app.fingerprint`: title, version string, hash of the start screen's ARIA tree | Replay warns `variant_mismatch` before acting. |
| Multi-tenant hook | `app.variant` plus semver `version` | The seam a per-tenant overlay would key on. Nothing in this build reads it. |
| Unattended use | `approval: draft \| approved`; `risk: safe \| risky` | Replay asks a human before a risky step unless the artifact is approved. |

## Alternatives rejected

- A recorded Playwright script (codegen) as the artifact: it stores
  selectors that only Playwright can run, cannot carry reasoning or a
  candidate list, and the desktop story becomes a rewrite.
- Raw model messages as the artifact: not reviewable, not diffable,
  and the values the model typed (including parameters) would be in it.
- A loose dict with a JSON Schema on the side: two sources of truth.
  Pydantic gives me the schema, the validation and the types from one
  definition.
- `extra="ignore"`: friendlier to hand editing, but a typo in
  `recoveries` would silently turn a recoverable run into a failure.

## Consequences

Easier: replay is a small interpreter over data; a reviewer can read
`capability.json` and know exactly what will happen; the compiler's
output is testable by equality.

Harder: every new behaviour needs a schema change first, and the schema
is strict enough that hand-written fixtures are tedious to get right.
I took that cost because the alternative is behaviour that lives in
engine code where nobody reviewing an artifact can see it.

To revisit: `OutputSpec.type` is three literals. A real catalogue needs
dates and enumerations, and a way to say an output is optional.
