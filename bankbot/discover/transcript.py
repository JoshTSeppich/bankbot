"""The Transcript: typed steps of one discovery run, not raw model messages.

Owns: the record the compiler reads. Each step keeps what the model saw,
what it said, what the policy said, what the surface did, and the facts
about the element it touched. Provenance (model, SDK versions, request
ids, timestamps) rides along so the compiled artifact can carry it.

Does not own: producing steps (discover/loop.py) or turning them into a
Capability (compile/).

Governed by ADR-0001 (artifact schema: provenance) and ADR-0002 (locator
strategy: ElementFacts are the raw material for candidates).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from bankbot.discover.tools import ProposedAction
from bankbot.policy import Decision
from bankbot.schemas import AppFingerprint, StrictModel
from bankbot.surface import ElementFacts, Observation


class StopReason(StrEnum):
    """Why the loop ended; only DONE produces a compilable transcript."""

    DONE = "done"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    STUCK = "stuck"
    INTERVENTION_ABORTED = "intervention_aborted"


StepStatus = Literal["ok", "blocked", "failed", "rejected", "holds", "does_not_hold"]


class TranscriptStep(StrictModel):
    """One turn: observation in, proposed action out, what happened."""

    index: int
    observation: Observation
    action: ProposedAction
    policy: Decision | None = None
    status: StepStatus
    detail: str = ""
    element: ElementFacts | None = None
    extracted: dict[str, str] = Field(default_factory=dict)
    request_id: str | None = None


class Transcript(StrictModel):
    """A whole discovery run, ready for the compiler or for a reviewer."""

    run_id: str
    goal: str
    params: dict[str, str]
    base_url: str
    fingerprint: AppFingerprint | None = None
    model: str
    sdk_versions: dict[str, str]
    started_at: datetime
    finished_at: datetime
    steps: list[TranscriptStep]
    stop_reason: StopReason
    summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def request_ids(self) -> list[str]:
        """Every model call this run made, for the artifact's provenance."""
        return [step.request_id for step in self.steps if step.request_id is not None]
