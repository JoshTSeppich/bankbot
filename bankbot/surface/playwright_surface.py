"""PlaywrightSurface: the one Surface implementation, driving a Playwright page.

Owns: observe, resolve, act, read, holds, fingerprint and tracing against a
live browser page, and the masking of sensitive fields before a screenshot
is taken.

Does not own: deciding what to do, what an artifact means, or launching the
browser (the CLI opens the page and hands it in).

Governed by ADR-0006 (surface abstraction) and ADR-0002 (locator strategy).
"""

import hashlib
import re
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from bankbot.schemas.artifact import ActionType, AppFingerprint, StateAssertion, TargetRef
from bankbot.surface.facts import element_facts
from bankbot.surface.frames import walk_frames
from bankbot.surface.human import HumanWatcher, OnHumanAction
from bankbot.surface.locators import bbox_centre, first_line, resolve_target
from bankbot.surface.types import (
    ActionFailed,
    ActResult,
    FrameNotFound,
    FrameSnapshot,
    Observation,
    ReadResult,
    TargetNotFound,
)

VERSION_META = "meta[name=application-version]"
DIALOG = "[role=dialog]:visible"
POLL_INTERVAL_MS = 100
# holds() probes a target once per poll; resolve_target's floor makes this the per-candidate wait.
TARGET_PROBE_MS = 250


def hash_aria(aria: str) -> str:
    """Hash a screen's ARIA snapshot the one way both the compiler and replay must agree on."""
    return "sha256:" + hashlib.sha256(aria.encode("utf-8")).hexdigest()


class PlaywrightSurface:
    """A Surface over one Playwright page.

    mask_selectors are blurred in every screenshot, top document and frames
    alike, because screenshots are committed as evidence and a password
    field must never be legible in one.
    """

    def __init__(self, page: Page, mask_selectors: Sequence[str]) -> None:
        self._page = page
        self._human = HumanWatcher(page)
        self._mask_style = (
            ", ".join(mask_selectors) + " { filter: blur(8px) !important; }"
            if mask_selectors
            else None
        )

    @property
    def page(self) -> Page:
        """The live page. Exposed only so control/ can attach expose_binding and framenavigated."""
        return self._page

    def observe(self, screenshot_to: Path | None = None) -> Observation:
        """Snapshot every frame and, when asked, write a masked screenshot."""
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

    def resolve(self, target: TargetRef, timeout_ms: int = 2000) -> int:
        """Find the control and return only the winning index; the handle stays in here."""
        return resolve_target(self._page, target, timeout_ms).index

    def act(
        self,
        action: ActionType,
        target: TargetRef | None,
        value: str | None,
        timeout_ms: int = 5000,
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

        resolved = resolve_target(self._page, target, timeout_ms)
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
            raise ActionFailed(
                action=action.value, reason=first_line(error), observed=self._page.url
            ) from error
        return ActResult(candidate_index=resolved.index, element=element)

    def read(self, target: TargetRef, timeout_ms: int = 2000) -> ReadResult:
        """Resolve, then take the element's visible text as the page shows it."""
        resolved = resolve_target(self._page, target, timeout_ms)
        if resolved.locator is None:
            raise ValueError("a bbox candidate has no element to read")
        try:
            text = resolved.locator.inner_text(timeout=timeout_ms).strip()
            element = element_facts(resolved.locator, target.frame_path)
        except PlaywrightError as error:
            raise ActionFailed(
                action="read", reason=first_line(error), observed=self._page.url
            ) from error
        return ReadResult(candidate_index=resolved.index, text=text, element=element)

    def holds(self, assertion: StateAssertion, timeout_ms: int = 2000) -> bool:
        """Poll until every condition holds at the same moment, or the time is up."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if self._holds_now(assertion):
                return True
            if time.monotonic() >= deadline:
                return False
            # Not time.sleep: the sync client only hears about navigations while it is talking
            # to the browser, so a plain sleep would leave page.url stale for the whole wait.
            self._page.wait_for_timeout(POLL_INTERVAL_MS)

    def fingerprint(self) -> AppFingerprint:
        """Title, the application-version meta tag, and a hash of the top document's ARIA."""
        meta = self._page.locator(VERSION_META)
        version = ""
        if meta.count() > 0:
            version = meta.first.get_attribute("content") or ""
        aria = self._page.locator("body").aria_snapshot()
        return AppFingerprint(
            title=self._page.title(),
            version=version,
            screen_hashes={"current": hash_aria(aria)},
        )

    def start_trace(self) -> None:
        """Record screenshots and DOM snapshots; evidence/ decides whether to keep them."""
        self._page.context.tracing.start(screenshots=True, snapshots=True)

    def stop_trace(self, path: Path) -> None:
        """Write trace.zip where the run directory says."""
        self._page.context.tracing.stop(path=str(path))

    def idle(self, ms: int) -> None:
        """Wait inside Playwright so events keep flowing; see Surface.idle for why not sleep."""
        self._page.wait_for_timeout(ms)

    def watch_human(self, on_action: OnHumanAction) -> None:
        """Hand the page to a person and keep the record going."""
        self._human.start(on_action)

    def unwatch_human(self) -> None:
        """The person handed back; stop attributing events to them."""
        self._human.stop()

    # --- private ------------------------------------------------------------

    def _navigate(self, url: str, timeout_ms: int) -> None:
        # Artifacts carry paths, not hosts, so the same capability runs against any deployment.
        absolute = urljoin(self._page.url, url)
        try:
            self._page.goto(absolute, wait_until="load", timeout=timeout_ms)
        except PlaywrightError as error:
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
                re.search(assertion.url_pattern, self._page.url) is None
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
