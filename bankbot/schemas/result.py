"""The ReplayResult contract: what a replay reports back to its caller.

Owns: the three-way split I think an exception would hide. Did the
capability answer (Success), did it reach a recognised end state that is an
answer in its own right (Outcome), or did it stop (Failure)? Every variant
points at evidence so I can explain a result after the fact.

Does not own: how a page condition is classified into one of these (replay/).

Governed by ADR-0003 (error taxonomy).
"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from bankbot.schemas.artifact import StrictModel


class Evidence(StrictModel):
    """Where to look when a result needs explaining: the run and what was captured in it."""

    run_id: str
    screenshot: str | None = None
    trace: str | None = None


class Success(StrictModel):
    """The capability answered; outputs are keyed by the artifact's output names."""

    kind: Literal["success"] = "success"
    outputs: dict[str, str]
    evidence: Evidence


class Outcome(StrictModel):
    """A KnownOutcome from the artifact matched; the code is the caller's answer."""

    kind: Literal["outcome"] = "outcome"
    code: str
    message: str
    evidence: Evidence


class Failure(StrictModel):
    """Replay stopped at a step; expected versus observed is what a human needs to triage it."""

    kind: Literal["failure"] = "failure"
    step_id: str
    expected: str
    observed: str
    evidence: Evidence
    recovery_attempts: int = 0


ReplayResult = Annotated[Success | Outcome | Failure, Field(discriminator="kind")]

# A union has no .model_validate_json of its own; this adapter is the one
# entry point for parsing a result back from a run log.
REPLAY_RESULT_ADAPTER: TypeAdapter[ReplayResult] = TypeAdapter(ReplayResult)
