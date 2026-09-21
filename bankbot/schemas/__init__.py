"""Typed boundary objects shared across the package's seams.

Owns: the Capability artifact (what discovery produces and replay executes)
and the ReplayResult contract (what a replay reports back). Both are Pydantic
models so the contract is enforced at load time and exportable as JSON Schema.

Does not own: any behaviour. Nothing here touches a browser, a file system
or a model API.

Governed by ADR-0001 (artifact schema), ADR-0003 (error taxonomy) and
ADR-0004 (control transfer, for the InterventionRequest).
"""

from bankbot.schemas.artifact import (
    ActionType,
    AppFingerprint,
    AppRef,
    Approval,
    Candidate,
    Capability,
    Fail,
    InputSpec,
    KnownOutcome,
    LiteralValue,
    LocatorStrategy,
    OnFail,
    OutputRef,
    OutputSpec,
    ParamRef,
    Recover,
    Recovery,
    Retry,
    Risk,
    RunProvenance,
    ScreenElement,
    SecretRef,
    StateAssertion,
    Step,
    StrictModel,
    TargetRef,
    inputs_named_in,
    inputs_never_used,
)
from bankbot.schemas.intervention import (
    InterventionDecision,
    InterventionReason,
    InterventionRequest,
)
from bankbot.schemas.result import (
    REPLAY_RESULT_ADAPTER,
    Evidence,
    Failure,
    Outcome,
    ReplayResult,
    ReplayWarning,
    Success,
    WarningCode,
)

__all__ = [
    "REPLAY_RESULT_ADAPTER",
    "ActionType",
    "AppFingerprint",
    "AppRef",
    "Approval",
    "Candidate",
    "Capability",
    "Evidence",
    "Fail",
    "Failure",
    "InputSpec",
    "InterventionDecision",
    "InterventionReason",
    "InterventionRequest",
    "KnownOutcome",
    "LiteralValue",
    "LocatorStrategy",
    "OnFail",
    "Outcome",
    "OutputRef",
    "OutputSpec",
    "ParamRef",
    "Recover",
    "Recovery",
    "ReplayResult",
    "ReplayWarning",
    "Retry",
    "Risk",
    "RunProvenance",
    "ScreenElement",
    "SecretRef",
    "StateAssertion",
    "Step",
    "StrictModel",
    "Success",
    "TargetRef",
    "WarningCode",
    "inputs_named_in",
    "inputs_never_used",
]
