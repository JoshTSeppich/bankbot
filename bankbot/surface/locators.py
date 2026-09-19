"""Candidate resolution: try a TargetRef's candidates in order and report which one won.

Owns: strategy to Playwright locator, each candidate's share of the timeout,
the win condition (visible, exactly one match), and the wording of why a
candidate lost.

Does not own: acting on what was found (playwright_surface.py) or the frame
lookup (frames.py).

Governed by ADR-0002 (locator strategy).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from bankbot.schemas.artifact import Candidate, LocatorStrategy, TargetRef
from bankbot.surface.frames import frame_for_path
from bankbot.surface.types import BBox, TargetNotFound

if TYPE_CHECKING:
    # Playwright types the role argument as a Literal of every ARIA role. The artifact stores
    # it as a string, so I cast at the call rather than copy the list of roles into this repo.
    from playwright._impl._api_structures import AriaRole

# A candidate with less than this cannot be told apart from a slow page, so a 3-candidate
# target with a 500 ms budget still gives each candidate a fair look.
MIN_SHARE_MS = 250
# Enough ARIA to see what the page showed instead, not enough to bloat every failure record.
OBSERVED_LIMIT = 2000


@dataclass(frozen=True)
class Resolved:
    """The candidate that won and what it points at.

    locator is None only when a bbox candidate won: a point has no element
    behind it that Playwright can hand back, which is why bbox is the last
    resort and only click and type accept it.
    """

    index: int
    locator: Locator | None
    bbox: BBox | None


def resolve_target(page: Page, target: TargetRef, timeout_ms: int) -> Resolved:
    """Try the candidates in order; the first that is visible and unique wins.

    The timeout is split evenly so a target with more fallbacks does not
    take longer to fail. A later index winning is the drift signal.
    """
    frame = frame_for_path(page, target.frame_path)
    share_ms = max(MIN_SHARE_MS, timeout_ms // len(target.candidates))
    tried: list[str] = []
    for index, candidate in enumerate(target.candidates):
        if candidate.strategy is LocatorStrategy.BBOX:
            box = parse_bbox(candidate.value)
            loss = bbox_loss(page, box)
            if loss is None:
                return Resolved(index=index, locator=None, bbox=box)
        else:
            locator = locator_for(frame, candidate)
            loss = locator_loss(locator, share_ms)
            if loss is None:
                return Resolved(index=index, locator=locator, bbox=None)
        tried.append(f"{candidate.strategy.value} {candidate.value!r}: {loss}")
    raise TargetNotFound(tried=tried, observed=observed_aria(frame))


def locator_for(frame: Frame, candidate: Candidate) -> Locator:
    """Build the Playwright locator a candidate describes, one branch per strategy."""
    if candidate.strategy is LocatorStrategy.ROLE_NAME:
        role, _, name = candidate.value.partition(":")
        return frame.get_by_role(cast("AriaRole", role), name=name, exact=True)
    if candidate.strategy is LocatorStrategy.LABEL:
        return frame.get_by_label(candidate.value, exact=True)
    if candidate.strategy is LocatorStrategy.TEXT:
        return frame.get_by_text(candidate.value, exact=True)
    if candidate.strategy is LocatorStrategy.CSS_STRUCTURAL:
        return frame.locator(candidate.value)
    raise ValueError(f"{candidate.strategy} does not describe an element")


def locator_loss(locator: Locator, share_ms: int) -> str | None:
    """Why a locator does not win, or None when it does.

    Visible first, then unique: waiting on .first sidesteps Playwright's own
    strict-mode error so an ambiguous match is reported as ambiguous rather
    than as a timeout.
    """
    try:
        locator.first.wait_for(state="visible", timeout=share_ms)
    except PlaywrightTimeout:
        return f"not visible within {share_ms}ms"
    except PlaywrightError as error:
        return f"invalid: {first_line(error)}"
    count = locator.count()
    if count != 1:
        return f"ambiguous: {count} matches"
    return None


def parse_bbox(value: str) -> BBox | None:
    """Read "x,y,w,h" in CSS pixels of the top document; None when it is not that shape."""
    parts = value.split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (float(part) for part in parts)
    except ValueError:
        return None
    return (x, y, w, h)


def bbox_loss(page: Page, box: BBox | None) -> str | None:
    """A box "matches" when it lies inside the viewport; that is all a point can promise."""
    if box is None:
        return "malformed: expected x,y,w,h"
    viewport = page.viewport_size
    if viewport is None:
        return "no viewport to place the box in"
    x, y, w, h = box
    if x < 0 or y < 0 or x + w > viewport["width"] or y + h > viewport["height"]:
        return "outside the viewport"
    return None


def bbox_centre(box: BBox) -> tuple[float, float]:
    """Where a point action lands: the middle of the box."""
    x, y, w, h = box
    return (x + w / 2, y + h / 2)


def observed_aria(frame: Frame) -> str:
    """What the frame showed when nothing matched, truncated for the failure record."""
    try:
        return frame.locator("body").aria_snapshot()[:OBSERVED_LIMIT]
    except PlaywrightError as error:
        return f"<no snapshot: {first_line(error)}>"


def first_line(error: Exception) -> str:
    """Playwright messages run to many lines of call log; the first line is the condition."""
    return str(error).splitlines()[0][:200] if str(error) else type(error).__name__
