"""The InterventionRequest: what replay hands a human when it cannot continue alone.

Owns: the request's shape and the vocabulary of reasons, and the decision a
human can send back. Everything the operator page shows and everything the
engine needs to resume is in here, so the two sides share one object.

Does not own: the state machine that moves control between engine and human
(control/) or the classification that decides a request is needed (replay/).

Governed by ADR-0004 (control transfer).
"""

from enum import StrEnum

from pydantic import Field

from bankbot.schemas.artifact import StrictModel


class InterventionReason(StrEnum):
    """Why the engine stopped, in words the operator page can show as-is."""

    UNKNOWN_DIALOG = "unknown_dialog"
    CANDIDATE_EXHAUSTED = "candidate_exhausted"
    CHECKPOINT_UNMET = "checkpoint_unmet"
    RISKY_NEEDS_APPROVAL = "risky_needs_approval"
    STUCK_IN_DISCOVERY = "stuck_in_discovery"


class InterventionDecision(StrEnum):
    """What a human can answer.

    RESUME means "I fixed the page, try the step again". STEP_DONE means "I
    did the step myself, carry on from the next one". ABORT ends the run as
    a Failure. Unattended runs answer ABORT.
    """

    RESUME = "resume"
    STEP_DONE = "step_done"
    ABORT = "abort"


class InterventionRequest(StrictModel):
    """Everything a person needs to decide, and nothing that would leak.

    Param names are listed, values never; the screenshot is already masked;
    the log tail is already redacted. The step index and count let the page
    say "step 4 of 6" without loading the artifact.
    """

    run_id: str
    capability_id: str
    capability_version: str
    goal: str
    step_id: str
    step_index: int
    step_count: int
    expected: str
    observed: str
    reason: InterventionReason
    screenshot: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    param_names: list[str] = Field(default_factory=list)
