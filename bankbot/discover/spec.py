"""The goal spec: what the engineer declares before the model sees the app.

Owns: GoalSpec and its loader. The model discovers the happy path; it does
not see the failure modes in one run, so the error taxonomy (known
outcomes, recoveries such as logging back in) and the typed inputs and
outputs are declared here by someone who knows the app. The compiler
merges the two.

Which half is which, so the "the model discovered it" claim stays honest:
the steps and every candidate in them are discovered, and so is each step's
wait_for. The inputs, the outputs' declared types, the known outcomes and the
recoveries are written here first and merged in by the compiler.

Does not own: the loop that uses it (discover/loop.py) or the artifact it
becomes (compile/).

Governed by ADR-0003 (error taxonomy) and ADR-0001 (artifact schema).
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from bankbot.schemas import AppRef, InputSpec, KnownOutcome, Recovery, StateAssertion, StrictModel

OutputType = Literal["string", "money", "number"]


class GoalSpec(StrictModel):
    """One capability's contract before it exists: the ask, its shape, and what can go wrong."""

    capability_id: str
    name: str
    goal: str
    app: AppRef
    start_path: str
    # What must be true before step one, such as being signed in. Replay checks these
    # first and uses the recoveries to make them true, so a fresh browser logs in
    # without a step having to fail first.
    preconditions: list[StateAssertion] = Field(default_factory=list)
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    outputs: dict[str, OutputType] = Field(default_factory=dict)
    outcomes: list[KnownOutcome] = Field(default_factory=list)
    recoveries: list[Recovery] = Field(default_factory=list)


def load_goal_spec(path: Path) -> GoalSpec:
    """Read a spec file; a bad spec fails here, before a model call costs anything."""
    return GoalSpec.model_validate(json.loads(path.read_text()))
