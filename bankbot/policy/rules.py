"""The policy: where the automation may go, what it may do, and what needs a human.

Owns: loading policy.yaml into a typed Policy, the allowlist checks (host,
route, action type) and the risk classification (route or control name).

Does not own: enforcement. discover/, replay/ and evidence/ call check() and
is_risky() and decide for themselves what a block or a risky flag means.
Masking of text lives in policy/redaction.py.

Governed by ADR-0005 (policy model).
"""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

import yaml
from pydantic import PrivateAttr, ValidationError, model_validator

from bankbot.policy.redaction import Redactor
from bankbot.schemas.artifact import ActionType, StrictModel

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "policy.yaml"

ALLOWED_REASON = "host, route and action are allowed"


class PolicyFileMissing(Exception):
    """There is no policy file where load_policy was told to look."""


class PolicyFileInvalid(Exception):
    """The policy file exists but does not describe a Policy; the message names the key."""


class Decision(StrictModel):
    """What the policy says about one proposed action.

    allowed and risky are independent on purpose. allowed says the action is
    inside the application the agent may drive; risky says a human must
    approve it. The caller combines them (ADR-0005).
    """

    allowed: bool
    risky: bool
    reason: str


class RiskRules(StrictModel):
    """Regexes naming irreversible territory: URL paths and control names."""

    routes: list[str]
    controls: list[str]


class RedactionRules(StrictModel):
    """What the evidence boundary and the screenshot capture must hide."""

    secret_env_suffixes: list[str]
    mask_selectors: list[str]


class Policy(StrictModel):
    """The whole of policy.yaml, typed, with its regexes compiled once at load.

    allowed_actions is typed as ActionType so a misspelled action in the file
    fails at load instead of silently blocking every step of that kind.
    """

    allowed_hosts: list[str]
    allowed_routes: list[str]
    allowed_actions: list[ActionType]
    risky: RiskRules
    redaction: RedactionRules

    _allowed_route_patterns: list[re.Pattern[str]] = PrivateAttr(default_factory=list)
    _risky_route_patterns: list[re.Pattern[str]] = PrivateAttr(default_factory=list)
    _risky_control_patterns: list[re.Pattern[str]] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _compile_patterns(self) -> Self:
        self._allowed_route_patterns = _compile_all(self.allowed_routes, "allowed_routes")
        self._risky_route_patterns = _compile_all(self.risky.routes, "risky.routes")
        # Control names are matched case-insensitively because the policy is
        # written by a human, and "close account" and "Close Account" are the
        # same button to a human.
        self._risky_control_patterns = _compile_all(
            self.risky.controls, "risky.controls", re.IGNORECASE
        )
        return self

    def check(self, url: str, action: ActionType, control_name: str | None = None) -> Decision:
        """Answer the one question every enforcement point asks before acting.

        The reason is a sentence because it is shown to the model during
        discovery and to the operator during replay; neither should have to
        read the policy file to understand a block.
        """
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path or "/"
        risky = self.is_risky(url, control_name)
        if host not in self.allowed_hosts:
            return Decision(
                allowed=False, risky=risky, reason=f"host {host!r} is not in allowed_hosts"
            )
        if not _any_match(self._allowed_route_patterns, path):
            return Decision(
                allowed=False, risky=risky, reason=f"route {path!r} matches no allowed_routes entry"
            )
        if action not in self.allowed_actions:
            return Decision(
                allowed=False,
                risky=risky,
                reason=f"action {action.value!r} is not in allowed_actions",
            )
        return Decision(allowed=True, risky=risky, reason=ALLOWED_REASON)

    def is_risky(self, url: str, control_name: str | None = None) -> bool:
        """Say whether an action is irreversible territory, independent of whether it is allowed.

        Two signals, either is enough: the page is on a risky route, or the
        control has a risky name. A "Close account" button sits on an allowed
        member page, so the route alone would miss it.
        """
        path = urlparse(url).path or "/"
        if _any_match(self._risky_route_patterns, path):
            return True
        if control_name is None:
            return False
        return _any_match(self._risky_control_patterns, control_name)

    @property
    def mask_selectors(self) -> list[str]:
        """The CSS selectors the surface blurs before every screenshot."""
        return self.redaction.mask_selectors

    def redactor(self, extra_values: Iterable[str] = ()) -> Redactor:
        """Build the Redactor with the env suffixes from this policy rather than the defaults."""
        return Redactor.from_environment(
            extra_values=extra_values, suffixes=self.redaction.secret_env_suffixes
        )


def load_policy(path: Path | None = None) -> Policy:
    """Read the policy once at start-up and fail there if it is wrong.

    A bad policy must never surface mid-run as an unexplained block, so YAML
    errors, unknown keys and invalid regexes all raise here with the path and
    the offending key in the message.
    """
    policy_path = DEFAULT_POLICY_PATH if path is None else path
    if not policy_path.is_file():
        raise PolicyFileMissing(f"no policy file at {policy_path}")
    try:
        data = yaml.safe_load(policy_path.read_text())
    except yaml.YAMLError as error:
        raise PolicyFileInvalid(f"{policy_path} is not valid YAML: {error}") from error
    try:
        return Policy.model_validate(data)
    except ValidationError as error:
        raise PolicyFileInvalid(f"{policy_path}: {_describe(error)}") from error


def _compile_all(patterns: list[str], key: str, flags: int = 0) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for index, pattern in enumerate(patterns):
        try:
            compiled.append(re.compile(pattern, flags))
        except re.error as error:
            raise ValueError(
                f"{key}[{index}] is not a valid regular expression: {error}"
            ) from error
    return compiled


def _any_match(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _describe(error: ValidationError) -> str:
    lines: list[str] = []
    for problem in error.errors():
        location = ".".join(str(part) for part in problem["loc"])
        message: Any = problem["msg"]
        lines.append(f"{location}: {message}" if location else str(message))
    return "; ".join(lines)
