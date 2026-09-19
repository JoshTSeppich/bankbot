"""Turning artifact references into values, and page text into typed outputs.

Owns: checking the caller's params against the artifact's inputs before the
browser is touched, resolving ParamRef and SecretRef to the string a step
types, and parsing an extracted cell into the output's declared type.

Does not own: where a value is read from (the artifact) or logging it
(evidence/). Secret values pass through here and are never returned to a
caller except as the value to type.

Governed by ADR-0001 (artifact schema).
"""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from bankbot.schemas.artifact import (
    InputSpec,
    LiteralValue,
    OutputRef,
    OutputSpec,
    ParamRef,
    SecretRef,
    Step,
)


class ParamMissing(Exception):
    """A required input was not supplied; found before the browser opens."""


class ParamInvalid(Exception):
    """An input does not match the pattern the artifact declares for it."""


class SecretMissing(Exception):
    """A SecretRef names an environment variable that is not set."""


class OutputUnreadable(Exception):
    """The extracted text does not parse as the output's declared type."""


def check_params(inputs: Mapping[str, InputSpec], params: Mapping[str, str]) -> None:
    """Fail fast on bad inputs so a typo never costs a browser session."""
    for name, spec in inputs.items():
        if name not in params:
            if spec.required:
                raise ParamMissing(f"input {name!r} is required")
            continue
        if spec.pattern is not None and not re.fullmatch(spec.pattern, params[name]):
            raise ParamInvalid(f"input {name!r} does not match {spec.pattern!r}")


def check_secrets(steps: Sequence[Step], secrets: Mapping[str, str]) -> None:
    """Fail fast when a credential is missing, before any step runs."""
    for step in steps:
        if isinstance(step.value, SecretRef) and step.value.secret not in secrets:
            raise SecretMissing(f"step {step.id!r} needs {step.value.secret} in the environment")


def value_for(step: Step, params: Mapping[str, str], secrets: Mapping[str, str]) -> str | None:
    """The string a step types, selects or navigates to; None for steps without a value."""
    if step.value is None:
        return None
    if isinstance(step.value, LiteralValue):
        return step.value.literal
    if isinstance(step.value, ParamRef):
        return params[step.value.param]
    if isinstance(step.value, SecretRef):
        return secrets[step.value.secret]
    if isinstance(step.value, OutputRef):
        return None
    raise TypeError(f"unknown value kind on step {step.id!r}")


def describe_value(step: Step) -> str:
    """How a value appears in the log: names for params and secrets, never their contents."""
    if step.value is None:
        return ""
    if isinstance(step.value, LiteralValue):
        return step.value.literal
    if isinstance(step.value, ParamRef):
        return f"param:{step.value.param}"
    if isinstance(step.value, SecretRef):
        return f"secret:{step.value.secret}"
    return f"output:{step.value.output}"


def parse_output(name: str, spec: OutputSpec, text: str) -> str:
    """Normalise what the screen shows into the declared type; callers never parse "$4,242.00"."""
    if spec.type == "string":
        return text.strip()
    digits = re.sub(r"[^0-9.\-]", "", text)
    try:
        amount = Decimal(digits)
    except InvalidOperation as error:
        raise OutputUnreadable(f"output {name!r}: {text!r} is not a {spec.type}") from error
    if spec.type == "money":
        return f"{amount:.2f}"
    return str(amount.normalize())
