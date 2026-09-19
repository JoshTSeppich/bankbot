from typing import Any, ClassVar

import pytest

from bankbot.discover.model import ClaudeDecider, ModelGaveMalformedAction
from bankbot.discover.tools import ACT_TOOL


def test_the_act_tool_is_strict_so_a_reply_cannot_omit_the_action() -> None:
    assert ACT_TOOL["strict"] is True
    schema: dict[str, Any] = dict(ACT_TOOL["input_schema"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"reasoning", "action"}


class _Block:
    type = "tool_use"
    id = "toolu_1"
    input: ClassVar[dict[str, str]] = {
        "reasoning": "the model forgot the action",
        "name": "Close account",
    }


class _Usage:
    input_tokens = 1
    output_tokens = 1


class _Response:
    content: ClassVar[list[_Block]] = [_Block()]
    usage = _Usage()


class _Raw:
    headers: ClassVar[dict[str, str]] = {"request-id": "req_test"}

    def parse(self) -> _Response:
        return _Response()


class _Create:
    def create(self, **kwargs: Any) -> _Raw:
        return _Raw()


class _Messages:
    with_raw_response = _Create()


class _Client:
    messages = _Messages()


def test_an_input_that_does_not_fit_is_a_named_error_carrying_the_request_id() -> None:
    decider = ClaudeDecider(client=_Client())  # type: ignore[arg-type]
    with pytest.raises(ModelGaveMalformedAction, match="req_test"):
        decider.decide("system", [])
