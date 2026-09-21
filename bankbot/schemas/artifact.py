"""The Capability artifact: the contract between discovery, replay and the operator.

Owns: the shape of a recorded capability. Typed inputs and outputs so it can
be invoked like a function; ordered steps; ranked candidates for finding each
control; and the error taxonomy (known outcomes, recoveries, per-step failure
policy) so that replay behaviour lives in data rather than engine code.

Does not own: resolving a target against a live page (surface/), executing
steps (replay/), or reporting what a run produced (schemas/result.py).

Governed by ADR-0001 (artifact schema), ADR-0002 (locator strategy) and
ADR-0003 (error taxonomy).
"""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
ENV_VAR_PATTERN = r"^[A-Z][A-Z0-9_]*$"


class StrictModel(BaseModel):
    """Base for every boundary object. I forbid unknown fields.

    Artifacts are hand-edited and machine-compiled, so a misspelled key must
    fail at load time rather than silently disable a step or a recovery.
    """

    model_config = ConfigDict(extra="forbid")


# --- Targeting -------------------------------------------------------------


class LocatorStrategy(StrEnum):
    """Ways a candidate can name a control, from most to least durable across UI drift."""

    ROLE_NAME = "role_name"
    LABEL = "label"
    TEXT = "text"
    CSS_STRUCTURAL = "css_structural"
    BBOX = "bbox"


class Candidate(StrictModel):
    """One way to find a control, with the reasoning a reviewer can audit."""

    strategy: LocatorStrategy
    value: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: str


class TargetRef(StrictModel):
    """Ranked candidates for one control, tried in order until one resolves.

    I use a ranked list rather than a single selector because the page may
    change between recording and replay. A later candidate winning is my
    drift signal (ADR-0002).

    frame_path is the chain of frame names from the top document down to
    the one holding the control. Empty means the top document. Legacy apps
    put forms inside iframes and framesets, and a locator that ignores the
    frame never resolves.
    """

    candidates: Annotated[list[Candidate], Field(min_length=1)]
    frame_path: list[str] = []


class StateAssertion(StrictModel):
    """A checkable claim about the page.

    Used for preconditions, step waits, the success checkpoint, and the
    matchers of known outcomes and recoveries. Every listed condition must
    hold. I reject an assertion with no condition because it would pass
    vacuously and hide an artifact bug.

    url_pattern is matched against the URL's path, never the whole URL. The
    rest of the design already draws that line: navigate steps store paths
    and the policy matches paths. The query string is the record being
    looked at, not the screen it is on.
    """

    description: str
    url_pattern: str | None = None
    text_visible: str | None = None
    target_visible: TargetRef | None = None

    @model_validator(mode="after")
    def _require_a_condition(self) -> Self:
        if self.url_pattern is None and self.text_visible is None and self.target_visible is None:
            raise ValueError("a StateAssertion needs at least one condition to check")
        return self


# --- Steps -----------------------------------------------------------------


class ActionType(StrEnum):
    """The complete vocabulary a step may use; the surface implements exactly these."""

    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    WAIT = "wait"
    EXTRACT = "extract"
    ASSERT = "assert"


class ParamRef(StrictModel):
    """Points a step at a named input, so artifacts hold parameter names and never values."""

    param: str


class SecretRef(StrictModel):
    """Points a step at a credential by the name of the environment variable holding it.

    I keep this separate from ParamRef so a secret can never be a caller
    supplied input. The surface resolves it from the environment at run
    time. The artifact and the run log only ever see the name.
    """

    secret: Annotated[str, Field(pattern=ENV_VAR_PATTERN)]


class OutputRef(StrictModel):
    """Points an extract step at the named output whose declared target it should read."""

    output: str


class LiteralValue(StrictModel):
    """A value fixed at recording time, such as a URL path or a dropdown option."""

    literal: str


class Retry(StrictModel):
    """On failure, try the same step again, up to `retries` more times (slow loads).

    retries counts the extra tries, not the total: retries 2 means the
    step runs at most three times.
    """

    kind: Literal["retry"] = "retry"
    retries: Annotated[int, Field(ge=1)]


class Recover(StrictModel):
    """On failure, run a named recovery from the artifact and then continue (session expiry)."""

    kind: Literal["recover"] = "recover"
    recovery_id: str


class Fail(StrictModel):
    """On failure, stop and report.

    I made this the default so an unhandled case stops the run instead of
    being retried or recovered silently.
    """

    kind: Literal["fail"] = "fail"


OnFail = Annotated[Retry | Recover | Fail, Field(discriminator="kind")]


class Step(StrictModel):
    """One action in the recorded flow.

    I made extract steps name an output instead of carrying their own target,
    so one place declares where a value is read. The output says where, the
    step says when.
    """

    id: str
    action: ActionType
    target: TargetRef | None = None
    value: ParamRef | SecretRef | OutputRef | LiteralValue | None = None
    wait_for: StateAssertion | None = None
    on_fail: OnFail = Field(default_factory=Fail)


def inputs_never_used(inputs: Iterable[str], steps: Iterable[Step]) -> list[str]:
    """Name the declared inputs that no step references.

    An input nothing reads is a capability that ignores its caller: it walks
    the recorded flow and answers about the recorded record whatever it was
    asked for. The schema rejects one, and the compiler asks the same
    question first so discovery can name the input instead of printing a
    validation dump.
    """
    used = {step.value.param for step in steps if isinstance(step.value, ParamRef)}
    return [name for name in inputs if name not in used]


# --- Inputs, outputs and the error taxonomy --------------------------------


class InputSpec(StrictModel):
    """A named parameter the capability takes; the example lets a reviewer run it by hand."""

    type: Literal["string"] = "string"
    pattern: str | None = None
    required: bool = True
    example: str | None = None


class OutputSpec(StrictModel):
    """A named value the capability returns and the control it is read from."""

    type: Literal["string", "money", "number"]
    extract: TargetRef


class KnownOutcome(StrictModel):
    """A recognised end state that is an answer, not a failure (e.g. member_not_found)."""

    code: str
    matches: StateAssertion
    message: str


class Recovery(StrictModel):
    """A recognised detour with a known way back (e.g. session expired: log in, retry).

    resume_from_step names where to continue after the recovery steps; omitted
    means retry the step that was interrupted. max_attempts bounds loops.
    """

    id: str
    matches: StateAssertion
    steps: Annotated[list[Step], Field(min_length=1)]
    resume_from_step: str | None = None
    max_attempts: Annotated[int, Field(ge=1)] = 1


# --- The artifact ----------------------------------------------------------


class ScreenElement(StrictModel):
    """One focusable control as a person tabbing through the screen would meet it.

    role is the ARIA role, state is Lantern's eight-bit bitmap (expanded,
    haspopup, selected, checked, disabled, required, invalid, readonly), and
    landmark is the nearest enclosing landmark or "none". Names and values
    are left out on purpose: the shape of a screen is what it lets you do,
    not whose record is on it.
    """

    role: str
    state: int
    landmark: str

    def key(self) -> tuple[str, int, str]:
        """The hashable form the edit distance compares."""
        return (self.role, self.state, self.landmark)


class AppFingerprint(StrictModel):
    """What the app looked like at recording time, so replay can tell it is on the wrong variant.

    screen_fingerprints maps a key screen's path to its tab sequence. Replay
    measures the edit distance to the live screen and reports it as a
    number, so "how different" is a fact in the log rather than a yes or no.
    """

    title: str
    version: str
    screen_fingerprints: dict[str, list[ScreenElement]] = {}


class AppRef(StrictModel):
    """Which application, and which tenant variant of it, the artifact was recorded against.

    variant is the multi-tenant hook: a per-credit-union override would target
    the same vendor/app_id with a different variant (ADR-0001). fingerprint is
    optional because hand-written artifacts have none; compiled ones always do.
    """

    vendor: str
    app_id: str
    variant: str = "default"
    fingerprint: AppFingerprint | None = None


class RunProvenance(StrictModel):
    """Which discovery run produced the artifact, with enough detail to audit it.

    request_ids are the model API request ids. I record them so the
    evidence in the repo can be tied to real model calls, not asserted.
    """

    run_id: str
    model: str
    sdk_versions: dict[str, str]
    started_at: datetime
    finished_at: datetime
    request_ids: list[str] = []


class Risk(StrEnum):
    """Whether unattended replay may run this capability without a human approving it."""

    SAFE = "safe"
    RISKY = "risky"


class Approval(StrEnum):
    """Whether a human has reviewed the artifact since it was compiled."""

    DRAFT = "draft"
    APPROVED = "approved"


class Capability(StrictModel):
    """A recorded workflow that can be invoked like a function: inputs in, outputs out.

    I check every reference a step makes (input, output, recovery, step id)
    at load time, so a broken artifact is rejected before it touches a live
    bank rather than mid-replay.
    """

    id: str
    name: str
    version: Annotated[str, Field(pattern=SEMVER_PATTERN)]
    app: AppRef
    created_from_run: RunProvenance | None = None
    inputs: dict[str, InputSpec] = {}
    outputs: dict[str, OutputSpec] = {}
    preconditions: list[StateAssertion] = []
    steps: Annotated[list[Step], Field(min_length=1)]
    checkpoint: StateAssertion
    outcomes: list[KnownOutcome] = []
    recoveries: list[Recovery] = []
    risk: Risk = Risk.SAFE
    approval: Approval = Approval.DRAFT

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        recovery_steps = [step for recovery in self.recoveries for step in recovery.steps]
        all_steps = self.steps + recovery_steps
        step_ids = [step.id for step in all_steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique across steps and recoveries")
        recovery_ids = {recovery.id for recovery in self.recoveries}

        for step in all_steps:
            if isinstance(step.value, ParamRef) and step.value.param not in self.inputs:
                raise ValueError(
                    f"step {step.id!r} references undeclared input {step.value.param!r}"
                )
            if isinstance(step.value, OutputRef) and step.value.output not in self.outputs:
                raise ValueError(
                    f"step {step.id!r} references undeclared output {step.value.output!r}"
                )
            if step.action is ActionType.EXTRACT and not isinstance(step.value, OutputRef):
                raise ValueError(f"extract step {step.id!r} must name an output to store into")
            if isinstance(step.on_fail, Recover) and step.on_fail.recovery_id not in recovery_ids:
                raise ValueError(
                    f"step {step.id!r} references undeclared recovery {step.on_fail.recovery_id!r}"
                )

        unused = inputs_never_used(self.inputs, all_steps)
        if unused:
            raise ValueError(f"input {unused[0]!r} is never used by any step")

        for recovery in self.recoveries:
            if recovery.resume_from_step is not None and recovery.resume_from_step not in step_ids:
                raise ValueError(
                    f"recovery {recovery.id!r} resumes from unknown step "
                    f"{recovery.resume_from_step!r}"
                )
        return self
