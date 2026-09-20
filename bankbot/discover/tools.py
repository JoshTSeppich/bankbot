"""The one tool the model gets, and the prompt that frames it.

Owns: the `act` tool definition, the ProposedAction model its input parses
into, and the system prompt. One tool with an action enum rather than seven
tools because every call then carries the same mandatory `reasoning`, and
that sentence is what the artifact later stores as locator reasoning.

Does not own: calling the model (discover/model.py) or acting on the
answer (discover/loop.py).

Governed by ADR-0002 (locator strategy: reasoning is recorded per action).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from bankbot.discover.spec import GoalSpec
from bankbot.schemas import StrictModel

if TYPE_CHECKING:
    from anthropic.types import ToolParam


class ToolAction(StrEnum):
    """What the model may ask for. `done` is a claim the loop checks, not an order."""

    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    NAVIGATE = "navigate"
    EXTRACT = "extract"
    ASSERT_STATE = "assert_state"
    DONE = "done"


class ProposedAction(StrictModel):
    """The model's answer, validated before anything touches the page."""

    reasoning: str
    action: ToolAction
    role: str | None = None
    name: str | None = None
    text: str | None = None
    value: str | None = None
    url: str | None = None
    output_name: str | None = None
    description: str | None = None
    summary: str | None = None


# strict makes the API guarantee the input matches the schema, so a reply can never omit
# the action or invent a field. Without it a malformed reply once ended a run with a crash.
ACT_TOOL: ToolParam = {
    "name": "act",
    "description": "Take exactly one action on the page toward the goal.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "One sentence: why this action and why this target.",
            },
            "action": {"type": "string", "enum": [action.value for action in ToolAction]},
            "role": {"type": "string", "description": "ARIA role of the target, as in the tree."},
            "name": {
                "type": "string",
                "description": "Accessible name of the target, exactly as in the tree.",
            },
            "text": {"type": "string", "description": "For type: the text to enter."},
            "value": {"type": "string", "description": "For select: the option to choose."},
            "url": {"type": "string", "description": "For navigate: an absolute URL."},
            "output_name": {
                "type": "string",
                "description": "For extract: which declared output this element's text is.",
            },
            "description": {
                "type": "string",
                "description": "For assert_state: the state you claim the page is in.",
            },
            "summary": {"type": "string", "description": "For done: what you found."},
        },
        "required": ["reasoning", "action"],
    },
}


def system_prompt(spec: GoalSpec, params: dict[str, str]) -> str:
    """Tell the model the goal, the inputs by name and value, and the rules the loop enforces.

    Values are shown so the model types them; the compiler later replaces
    each typed value with a reference to the input it came from.
    """
    inputs = "\n".join(f"- {name} = {value}" for name, value in params.items()) or "- none"
    outputs = "\n".join(f"- {name} ({kind})" for name, kind in spec.outputs.items()) or "- none"
    return (
        "You drive a web application to accomplish one goal. Each turn you receive a "
        "screenshot and the accessibility (ARIA) tree of every frame, labelled by frame path. "
        "Call `act` exactly once per turn, targeting elements by the role and accessible "
        "name shown in the tree.\n\n"
        f"Goal: {spec.goal}\n\n"
        f"Inputs (use these exact values):\n{inputs}\n\n"
        f"Outputs you must extract before finishing:\n{outputs}\n\n"
        "Rules. Use extract on the element whose text is the output. After your last "
        "action, call assert_state describing the state that proves the goal is reached. "
        "Only then call done. An action the policy blocks comes back as 'blocked'; do not "
        "repeat it, find another way or finish. An action that changes a member's account "
        "needs a person's approval: if the goal needs one, propose it and the policy will "
        "ask them."
    )
