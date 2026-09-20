"""The one call to the model, and the messages it is made with.

Owns: the Decider protocol (perception in, one ProposedAction out) and the
ClaudeDecider that implements it over the Anthropic Messages API with the
`act` tool forced. Also the message builders, because the shape of what the
model sees is part of this decision. No provider abstraction: switching
models is an edit to this file, which is a better story than a Protocol
with one implementation.

Does not own: what to do with the action (discover/loop.py).

Governed by ADR-0002 (locator strategy: the model targets by role and name).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from bankbot.discover.tools import ACT_TOOL, ProposedAction
from bankbot.schemas import StrictModel
from bankbot.surface import Observation

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import (
        ImageBlockParam,
        MessageParam,
        TextBlockParam,
        ToolResultBlockParam,
        ToolUseBlockParam,
    )

DEFAULT_MODEL = "claude-opus-4-8"
MAX_OUTPUT_TOKENS = 1024
# An org-scoped key must name a workspace; a workspace-scoped key must not.
WORKSPACE_HEADER = "anthropic-workspace-id"
WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"
API_KEY_ENV = "ANTHROPIC_API_KEY"


class Decided(StrictModel):
    """One model answer plus what the provenance needs to know about the call."""

    action: ProposedAction
    tool_use_id: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class Decider(Protocol):
    """Whatever chooses the next action. Tests script it; production asks Claude."""

    def decide(self, system: str, messages: list[MessageParam]) -> Decided:
        """Return exactly one proposed action for the conversation so far."""
        ...

    @property
    def model(self) -> str:
        """The model id recorded in the transcript and the artifact."""
        ...


class ClaudeDecider:
    """Asks Claude for the next action with the `act` tool forced, so every answer is one action."""

    def __init__(
        self, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL
    ) -> None:
        self._client = client if client is not None else make_client()
        self._model = model

    @property
    def model(self) -> str:
        """The model id recorded in the transcript and the artifact."""
        return self._model

    def decide(self, system: str, messages: list[MessageParam]) -> Decided:
        """One call, one tool use. The raw response is kept only for its request id."""
        raw = self._client.messages.with_raw_response.create(
            model=self._model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            tools=[ACT_TOOL],
            tool_choice={"type": "tool", "name": "act"},
            messages=messages,
        )
        response = raw.parse()
        request_id = raw.headers.get("request-id")
        for block in response.content:
            if block.type == "tool_use":
                try:
                    action = ProposedAction.model_validate(block.input)
                except ValidationError as bad:
                    raise ModelGaveMalformedAction(
                        f"{self._model} called act with input that does not fit "
                        f"(request {request_id}): {bad.error_count()} errors"
                    ) from bad
                return Decided(
                    action=action,
                    tool_use_id=block.id,
                    request_id=request_id,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
        raise ModelGaveNoAction(f"{self._model} answered without calling act")


class ModelGaveNoAction(Exception):
    """The model replied without a tool call even though the tool was forced."""


class ModelGaveMalformedAction(Exception):
    """The tool input did not fit ProposedAction. The tool is strict, so this is the API's bug."""


class ModelKeyMissing(Exception):
    """There is no usable Anthropic key in the environment, so discovery cannot run."""


def check_model_key(environment: Mapping[str, str]) -> None:
    """Say a key is missing here, not one HTTP request into a run that already opened a browser.

    Blank counts as missing. `.env.example` ships `ANTHROPIC_API_KEY=` with
    nothing after it, so that is what a fresh clone has, and the SDK carries
    an empty string all the way to the first request before it complains.
    """
    if not environment.get(API_KEY_ENV, "").strip():
        raise ModelKeyMissing(f"{API_KEY_ENV} is not set; only discovery needs it")


def make_client() -> anthropic.Anthropic:
    """Build the client from the environment; the workspace header only when the key needs it."""
    # The SDK is imported here and nowhere else at module scope. REPORT.md says
    # a replay process never loads it, and replay reaches this module through
    # bankbot.discover, so that sentence is now a tested fact rather than a claim.
    import anthropic

    check_model_key(os.environ)
    workspace_id = os.environ.get(WORKSPACE_ENV)
    headers = {WORKSPACE_HEADER: workspace_id} if workspace_id else None
    return anthropic.Anthropic(default_headers=headers)


# --- what the model sees ------------------------------------------------------


def perception_message(
    prefix: str, observation: Observation, screenshot: Path | None
) -> MessageParam:
    """One user turn: a text header, the ARIA tree of every frame, and the masked screenshot.

    Frames are labelled by frame path so the model can tell the top document
    from the iframe holding the form; the compiler needs the same label.
    """
    trees = "\n\n".join(
        f"frame {frame.frame_path or 'top'} ({observation.url}):\n{frame.aria}"
        for frame in observation.frames
    )
    text: TextBlockParam = {"type": "text", "text": f"{prefix}\n\n{trees}"}
    if screenshot is None or not screenshot.exists():
        return {"role": "user", "content": [text]}
    image: ImageBlockParam = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(screenshot.read_bytes()).decode(),
        },
    }
    return {"role": "user", "content": [text, image]}


def tool_use_message(decided: Decided) -> MessageParam:
    """The assistant turn echoing the model's own call, as the API requires."""
    block: ToolUseBlockParam = {
        "type": "tool_use",
        "id": decided.tool_use_id,
        "name": ACT_TOOL["name"],
        "input": decided.action.model_dump(exclude_none=True),
    }
    return {"role": "assistant", "content": [block]}


def tool_result_message(decided: Decided, result: str) -> MessageParam:
    """What the loop tells the model happened, including 'blocked' and 'rejected'."""
    block: ToolResultBlockParam = {
        "type": "tool_result",
        "tool_use_id": decided.tool_use_id,
        "content": result,
    }
    return {"role": "user", "content": [block]}
