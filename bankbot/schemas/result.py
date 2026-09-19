"""The ReplayResult contract: what a replay reports back to its caller.

Owns: the three-way split I think an exception would hide. Did the
capability answer (Success), did it reach a recognised end state that is an
answer in its own right (Outcome), or did it stop (Failure)? Every variant
points at evidence so I can explain a result after the fact.

Does not own: how a page condition is classified into one of these (replay/).

Governed by ADR-0003 (error taxonomy).
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from bankbot.schemas.artifact import StrictModel
from bankbot.schemas.intervention import InterventionRequest


class Evidence(StrictModel):
    """Where to look when a result needs explaining: the run and what was captured in it."""

    run_id: str
    screenshot: str | None = None
    trace: str | None = None


class WarningCode(StrEnum):
    """Things that did not stop the run but a maintainer should hear about."""

    DRIFT = "drift_warning"
    VARIANT_MISMATCH = "variant_mismatch"


class ReplayWarning(StrictModel):
    """A non-fatal signal: the run went on, but the page is not quite what was recorded."""

    code: WarningCode
    step_id: str | None = None
    detail: str


class ResultBase(StrictModel):
    """What every result carries: where the evidence is, what was recovered from, what drifted."""

    evidence: Evidence
    recoveries_used: list[str] = Field(default_factory=list)
    warnings: list[ReplayWarning] = Field(default_factory=list)


class Success(ResultBase):
    """The capability answered; outputs are keyed by the artifact's output names."""

    kind: Literal["success"] = "success"
    outputs: dict[str, str]


class Outcome(ResultBase):
    """A KnownOutcome from the artifact matched; the code is the caller's answer."""

    kind: Literal["outcome"] = "outcome"
    code: str
    message: str


class Failure(ResultBase):
    """Replay stopped at a step; expected versus observed is what a human needs to triage it.

    intervention is set when the stop went through a human (or would have,
    had one been there), so a Failure says which kind of help it needed.
    """

    kind: Literal["failure"] = "failure"
    step_id: str
    expected: str
    observed: str
    intervention: InterventionRequest | None = None


ReplayResult = Annotated[Success | Outcome | Failure, Field(discriminator="kind")]

# A union has no .model_validate_json of its own; this adapter is the one
# entry point for parsing a result back from a run log.
REPLAY_RESULT_ADAPTER: TypeAdapter[ReplayResult] = TypeAdapter(ReplayResult)
