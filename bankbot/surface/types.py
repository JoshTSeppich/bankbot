"""What crosses out of the surface: result models, the Surface protocol, the two errors.

Owns: the Pydantic models every Surface method returns, the Surface
Protocol, and the typed errors. Nothing here imports Playwright. A second
implementation (a desktop accessibility surface, ADR-0006) would import this
file unchanged and return the same shapes.

Does not own: any behaviour, and nothing about what an artifact means.

Governed by ADR-0006 (surface abstraction) and ADR-0002 (locator strategy).
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from bankbot.schemas.artifact import (
    ActionType,
    AppFingerprint,
    StateAssertion,
    StrictModel,
    TargetRef,
)

BBox = tuple[float, float, float, float]


class FrameSnapshot(StrictModel):
    """One frame's ARIA snapshot, labelled by where it sits so a target can name it later."""

    frame_path: list[str]
    aria: str


class Observation(StrictModel):
    """Everything the model or the replay engine may know about the page right now.

    frames[0] is always the top document. screenshot is the path written, or
    None when none was asked for. dialog_text is the text of a visible
    role=dialog in any frame: the signal replay uses to tell "an unknown
    modal is in the way" from "the control drifted" (ADR-0003).
    """

    url: str
    title: str
    frames: list[FrameSnapshot]
    screenshot: str | None
    dialog_text: str | None


class ElementFacts(StrictModel):
    """What the surface saw about the element it just acted on.

    The compiler turns these into ranked candidates (role+name first, bbox
    last), so every field is a possible locator and none of them is a
    Playwright handle. row_header is the text of the first th in the same
    table row when the element is a data cell: a balance cell is anchored on
    "Savings balance", never on the amount it happens to hold today.
    """

    role: str | None
    name: str | None
    label: str | None
    text: str | None
    row_header: str | None
    css_path: str
    bbox: BBox
    frame_path: list[str]


class ActResult(StrictModel):
    """Which candidate found the control and what the control was.

    Both are None for navigate, which has no target. element is also None
    when a bbox candidate won: a point on the screen has no element behind it
    that the surface can describe.
    """

    candidate_index: int | None
    element: ElementFacts | None


class ReadResult(StrictModel):
    """The text of a resolved control, the candidate that found it, and the element's facts.

    element is what the compiler ranks into candidates for an output, the
    same way it does for an acted control.
    """

    candidate_index: int
    text: str
    element: ElementFacts | None = None


class HumanAction(StrictModel):
    """One thing a person did while in control: what kind, on what, where. Never what they typed."""

    kind: Literal["click", "input", "navigate"]
    description: str
    frame_path: list[str]
    url: str


class TargetNotFound(Exception):
    """No candidate of a TargetRef resolved to exactly one visible element.

    tried has one line per candidate saying why it lost, in order. observed
    is the ARIA snapshot of the frame searched, truncated. Together they let
    an operator see why replay stopped without opening a browser (ADR-0002).
    """

    def __init__(self, tried: list[str], observed: str) -> None:
        self.tried = tried
        self.observed = observed
        super().__init__("no candidate resolved: " + "; ".join(tried))


class ActionFailed(Exception):
    """The control was found but the browser could not carry out the action on it.

    Playwright's own exceptions never leave the surface, because replay does
    not import Playwright and could not catch them (ADR-0006). reason is the
    first line of the browser's message: an overlay intercepting the click, a
    detached element, a navigation that never loaded, a missing option.
    observed is the page URL at the moment of failure.
    """

    def __init__(self, action: str, reason: str, observed: str) -> None:
        self.action = action
        self.reason = reason
        self.observed = observed
        super().__init__(f"{action} failed at {observed}: {reason}")


class FrameNotFound(Exception):
    """A TargetRef's frame_path names a frame the page does not have right now."""

    def __init__(self, frame_path: list[str], available: list[str]) -> None:
        self.frame_path = frame_path
        self.available = available
        super().__init__(f"no frame at {'/'.join(frame_path)!r}; page has {available}")


class Surface(Protocol):
    """The only door between the engine and a live application.

    discover/ and replay/ are written against this protocol, never against a
    browser. It is deliberately small: perceive, find, act, read, check,
    identify, trace. Anything about deciding what to do stays outside.
    """

    def observe(self, screenshot_to: Path | None = None) -> Observation:
        """Perceive the page: the model needs it to decide, the evidence log needs it to prove."""
        ...

    def resolve(self, target: TargetRef, timeout_ms: int = 2000) -> int:
        """Find a control by its ranked candidates and say which one won.

        The winning index is the drift signal: index 0 means the page still
        looks the way it did at recording time; anything later means it
        changed and a weaker locator caught it (ADR-0002).
        """
        ...

    def act(
        self,
        action: ActionType,
        target: TargetRef | None,
        value: str | None,
        timeout_ms: int = 5000,
    ) -> ActResult:
        """Do one step and report what was touched, so the compiler can describe it later."""
        ...

    def read(self, target: TargetRef, timeout_ms: int = 2000) -> ReadResult:
        """Read a control's text: how a capability produces its outputs."""
        ...

    def holds(self, assertion: StateAssertion, timeout_ms: int = 2000) -> bool:
        """Say whether a claim about the page becomes true within the timeout.

        This is a question, not a wait that fails, because the replay engine
        asks it of outcome and recovery rules as well as of checkpoints, and a
        rule that does not match is not an error.
        """
        ...

    def fingerprint(self) -> AppFingerprint:
        """Identify the app build, so replay can refuse a variant it was not recorded on."""
        ...

    def start_trace(self) -> None:
        """Begin recording a trace: evidence for a run that ends in outcome or failure."""
        ...

    def stop_trace(self, path: Path) -> None:
        """Write the trace to disk at the path the evidence module chose."""
        ...

    def idle(self, ms: int) -> None:
        """Let the browser talk for a while; the engine calls this instead of sleeping.

        The sync client only hears about navigations and human actions while
        it is inside a Playwright call, so a paused engine that slept would
        record nothing.
        """
        ...

    def watch_human(self, on_action: Callable[[HumanAction], None]) -> None:
        """Start reporting what a person does in the live page, values masked."""
        ...

    def unwatch_human(self) -> None:
        """Stop reporting; from here on the engine is acting again."""
        ...
