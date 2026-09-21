"""PlaywrightSurface: the one Surface implementation, driving a Playwright page.

Owns: observe, resolve, act, read, holds, fingerprint and tracing against a
live browser page, and the masking of sensitive fields before a screenshot
is taken.

One page, and that is a limit worth naming: the native-dialog listener is
registered on the page handed in, so a dialog raised by a popup or a second
tab is never seen and never answered. The demo app opens neither, and a real
one that did would need a listener per page and a rule for which page the
step belongs to.

Does not own: deciding what to do, what an artifact means, or launching the
browser (the CLI opens the page and hands it in).

Governed by ADR-0006 (surface abstraction) and ADR-0002 (locator strategy).
"""

import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Dialog, Locator, Page
from playwright.sync_api import Error as PlaywrightError

from bankbot.schemas.artifact import ActionType, AppFingerprint, StateAssertion, TargetRef
from bankbot.surface.facts import element_facts
from bankbot.surface.fingerprint import screen_fingerprint
from bankbot.surface.frames import walk_frames
from bankbot.surface.human import HumanWatcher, OnHumanAction
from bankbot.surface.locators import Resolved, bbox_centre, first_line, resolve_target
from bankbot.surface.types import (
    ACT_TIMEOUT_MS,
    LOOK_TIMEOUT_MS,
    ActionFailed,
    ActResult,
    FrameNotFound,
    FrameSnapshot,
    Inspection,
    NativeDialog,
    Observation,
    ObservationUnavailable,
    ReadResult,
    SessionLost,
    TargetNotFound,
)

VERSION_META = "meta[name=application-version]"
DIALOG = "[role=dialog]:visible"
POLL_INTERVAL_MS = 100
# holds() probes a target once per poll; resolve_target's floor makes this the per-candidate wait.
TARGET_PROBE_MS = 250


class PlaywrightSurface:
    """A Surface over one Playwright page.

    mask_selectors are blurred in every screenshot, top document and frames
    alike, because screenshots are committed as evidence and a password
    field must never be legible in one.
    """

    def __init__(self, page: Page, mask_selectors: Sequence[str]) -> None:
        self._page = page
        self._human = HumanWatcher(page)
        self._dialogs: list[NativeDialog] = []
        page.on("dialog", self._answer_dialog)
        self._mask_style = (
            ", ".join(mask_selectors) + " { filter: blur(8px) !important; }"
            if mask_selectors
            else None
        )

    def take_dialogs(self) -> list[NativeDialog]:
        """Hand back the dialogs raised since the last ask, and forget them."""
        taken = list(self._dialogs)
        self._dialogs.clear()
        return taken

    def _answer_dialog(self, dialog: Dialog) -> None:
        # Answered at once, always. While a native dialog is open page.title() and
        # locator.count() hang with no timeout of their own, and screenshot() and
        # aria_snapshot() time out, so holding one open for a person would freeze
        # every way this surface has of looking at the page.
        answer: Literal["accepted", "dismissed"] = (
            "accepted" if dialog.type == "alert" else "dismissed"
        )
        self._dialogs.append(NativeDialog(type=dialog.type, message=dialog.message, answer=answer))
        if answer == "accepted":
            dialog.accept()
        else:
            dialog.dismiss()

    @property
    def page(self) -> Page:
        """The live page. Exposed only so control/ can attach expose_binding and framenavigated."""
        return self._page

    def observe(self, screenshot_to: Path | None = None) -> Observation:
        """Snapshot every frame and, when asked, write a masked screenshot."""
        try:
            return self._observe(screenshot_to)
        except PlaywrightError as error:
            self._raise_if_gone(error)
            raise ObservationUnavailable(first_line(error)) from error

    def _observe(self, screenshot_to: Path | None) -> Observation:
        frames = [
            FrameSnapshot(frame_path=path, aria=frame.locator("body").aria_snapshot())
            for path, frame in walk_frames(self._page)
        ]
        screenshot = None
        if screenshot_to is not None:
            # Playwright applies the style sheet to the top document and every frame for the
            # duration of the capture and removes it afterwards, so nothing is left behind.
            self._page.screenshot(path=str(screenshot_to), style=self._mask_style)
            screenshot = str(screenshot_to)
        return Observation(
            url=self._page.url,
            title=self._page.title(),
            frames=frames,
            screenshot=screenshot,
            dialog_text=self._dialog_text(),
        )

    def resolve(self, target: TargetRef, timeout_ms: int = LOOK_TIMEOUT_MS) -> int:
        """Find the control and return only the winning index; the handle stays in here."""
        return self._resolve(target, timeout_ms).index

    def inspect(self, target: TargetRef, timeout_ms: int = LOOK_TIMEOUT_MS) -> Inspection:
        """Resolve and describe; the element is untouched."""
        resolved = self._resolve(target, timeout_ms)
        element = None
        if resolved.locator is not None:
            element = element_facts(resolved.locator, target.frame_path)
        return Inspection(candidate_index=resolved.index, element=element)

    def act(
        self,
        action: ActionType,
        target: TargetRef | None,
        value: str | None,
        timeout_ms: int = ACT_TIMEOUT_MS,
    ) -> ActResult:
        """Navigate, click, type or select; the other action types are not the surface's job."""
        if action is ActionType.NAVIGATE:
            if value is None:
                raise ValueError("navigate needs a URL")
            self._navigate(value, timeout_ms)
            return ActResult(candidate_index=None, element=None)
        if action not in (ActionType.CLICK, ActionType.TYPE, ActionType.SELECT):
            raise ValueError(f"{action.value} is not something the surface does")
        if target is None:
            raise ValueError(f"{action.value} needs a target")
        if action is not ActionType.CLICK and value is None:
            raise ValueError(f"{action.value} needs a value")

        resolved = self._resolve(target, timeout_ms)
        # Facts come first: a click on a submit button navigates away, and afterwards there is
        # no element left to describe.
        element = None
        if resolved.locator is not None:
            element = element_facts(resolved.locator, target.frame_path)
        try:
            if resolved.locator is not None:
                self._act_on_element(action, resolved.locator, value, timeout_ms)
            elif resolved.bbox is not None:
                self._act_on_point(action, resolved.bbox, value)
        except PlaywrightError as error:
            self._raise_if_gone(error)
            raise ActionFailed(
                action=action.value, reason=first_line(error), observed=self._page.url
            ) from error
        return ActResult(candidate_index=resolved.index, element=element)

    def read(self, target: TargetRef, timeout_ms: int = LOOK_TIMEOUT_MS) -> ReadResult:
        """Resolve, then take the element's visible text as the page shows it."""
        resolved = self._resolve(target, timeout_ms)
        if resolved.locator is None:
            # A point can be clicked but not read: there is no element behind it to take
            # text from. Reported the way any unresolved target is, not as a crash.
            box = target.candidates[resolved.index].value
            raise TargetNotFound(
                tried=[f"bbox {box!r}: a point has no text to read"], observed=self._page.url
            )
        try:
            text = resolved.locator.inner_text(timeout=timeout_ms).strip()
            element = element_facts(resolved.locator, target.frame_path)
        except PlaywrightError as error:
            self._raise_if_gone(error)
            raise ActionFailed(
                action="read", reason=first_line(error), observed=self._page.url
            ) from error
        return ReadResult(candidate_index=resolved.index, text=text, element=element)

    def holds(self, assertion: StateAssertion, timeout_ms: int = LOOK_TIMEOUT_MS) -> bool:
        """Poll until every condition holds at the same moment, or the time is up."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if self._holds_now(assertion):
                return True
            if time.monotonic() >= deadline:
                return False
            # Not time.sleep: the sync client only hears about navigations while it is talking
            # to the browser, so a plain sleep would leave page.url stale for the whole wait.
            self.idle(POLL_INTERVAL_MS)

    def fingerprint(self) -> AppFingerprint:
        """Title, the application-version meta tag, and the current screen's tab sequence."""
        meta = self._page.locator(VERSION_META)
        version = ""
        if meta.count() > 0:
            version = meta.first.get_attribute("content") or ""
        return AppFingerprint(
            title=self._page.title(),
            version=version,
            screen_fingerprints={"current": screen_fingerprint(self._page)},
        )

    def start_trace(self) -> None:
        """Record screenshots and DOM snapshots; evidence/ decides whether to keep them."""
        self._page.context.tracing.start(screenshots=True, snapshots=True)

    def stop_trace(self, path: Path) -> None:
        """Write trace.zip where the run directory says, or nothing when the browser is gone.

        Replay stops the trace in a finally. A closed browser has no trace
        left to write, and raising about it here would bury whatever ended
        the run.
        """
        if self._page.is_closed():
            return
        self._page.context.tracing.stop(path=str(path))

    def idle(self, ms: int) -> None:
        """Wait inside Playwright so events keep flowing; see Surface.idle for why not sleep."""
        try:
            self._page.wait_for_timeout(ms)
        except PlaywrightError as error:
            self._raise_if_gone(error)
            # A wait that fails on a page that is still open is a bug in bankbot, not a
            # condition of the world, so it travels as itself.
            raise

    def watch_human(self, on_action: OnHumanAction) -> None:
        """Hand the page to a person and keep the record going."""
        self._human.start(on_action)

    def unwatch_human(self) -> None:
        """The person handed back; stop attributing events to them."""
        self._human.stop()

    # --- private ------------------------------------------------------------

    def _resolve(self, target: TargetRef, timeout_ms: int) -> Resolved:
        """Resolve a target, but call a closed browser what it is.

        Once the page is gone every candidate fails to match, so resolution
        reports a control that moved. That answer sends whoever reads the
        run looking for a locator in a browser that is not there any more.
        """
        try:
            return resolve_target(self._page, target, timeout_ms)
        except (TargetNotFound, FrameNotFound, PlaywrightError) as error:
            self._raise_if_gone(error)
            raise

    def _raise_if_gone(self, error: Exception) -> None:
        """Separate "the app went away" from "the action went wrong", which is one question.

        Playwright fails every call once the page is closed, and each
        failure wears the shape of whatever was being attempted.
        is_closed() is the only thing that tells the two apart.
        """
        if self._page.is_closed():
            raise SessionLost(first_line(error)) from error

    def _navigate(self, url: str, timeout_ms: int) -> None:
        # Artifacts carry paths, not hosts, so the same capability runs against any deployment.
        absolute = urljoin(self._page.url, url)
        try:
            self._page.goto(absolute, wait_until="load", timeout=timeout_ms)
        except PlaywrightError as error:
            self._raise_if_gone(error)
            raise ActionFailed(
                action=ActionType.NAVIGATE.value, reason=first_line(error), observed=self._page.url
            ) from error

    def _act_on_element(
        self, action: ActionType, locator: Locator, value: str | None, timeout_ms: int
    ) -> None:
        if action is ActionType.CLICK:
            locator.click(timeout=timeout_ms)
        elif action is ActionType.TYPE:
            locator.fill(value or "", timeout=timeout_ms)
        elif action is ActionType.SELECT:
            # Label first because that is what a human saw; the value is the fallback for
            # options whose label changed. Each attempt gets half the budget.
            try:
                locator.select_option(label=value, timeout=timeout_ms // 2)
            except PlaywrightError:
                locator.select_option(value, timeout=timeout_ms // 2)

    def _act_on_point(
        self, action: ActionType, box: tuple[float, float, float, float], value: str | None
    ) -> None:
        x, y = bbox_centre(box)
        if action is ActionType.CLICK:
            self._page.mouse.click(x, y)
        elif action is ActionType.TYPE:
            self._page.mouse.click(x, y)
            self._page.keyboard.type(value or "")
        else:
            raise ValueError("select needs an element, not a point")

    def _holds_now(self, assertion: StateAssertion) -> bool:
        try:
            if assertion.url_pattern is not None and (
                re.search(assertion.url_pattern, urlparse(self._page.url).path) is None
            ):
                return False
            if assertion.text_visible is not None and not self._text_visible(
                assertion.text_visible
            ):
                return False
            if assertion.target_visible is not None:
                resolve_target(self._page, assertion.target_visible, TARGET_PROBE_MS)
            return True
        except (TargetNotFound, FrameNotFound):
            return False
        except PlaywrightError:
            # Mid-navigation the old document is gone and its frames throw; the next poll
            # sees the new one. That is "does not hold yet", not an error.
            return False

    def _text_visible(self, text: str) -> bool:
        for _, frame in walk_frames(self._page):
            if frame.get_by_text(text, exact=False).first.is_visible():
                return True
        return False

    def _dialog_text(self) -> str | None:
        for _, frame in walk_frames(self._page):
            dialogs = frame.locator(DIALOG)
            if dialogs.count() > 0:
                return dialogs.first.inner_text().strip()
        return None
