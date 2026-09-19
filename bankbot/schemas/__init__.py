"""Typed boundary objects shared across the package's seams.

Owns: the Capability artifact (what discovery produces and replay executes)
and the ReplayResult contract (what a replay reports back). Both are Pydantic
models so the contract is enforced at load time and exportable as JSON Schema.

Does not own: any behaviour. Nothing here touches a browser, a file system
or a model API.

Governed by ADR-0001 (artifact schema) and ADR-0003 (error taxonomy).
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
    SecretRef,
    StateAssertion,
    Step,
    TargetRef,
)
from bankbot.schemas.result import (
    REPLAY_RESULT_ADAPTER,
    Evidence,
    Failure,
    Outcome,
    ReplayResult,
    Success,
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
    "Retry",
    "Risk",
    "RunProvenance",
    "SecretRef",
    "StateAssertion",
    "Step",
    "Success",
    "TargetRef",
]
