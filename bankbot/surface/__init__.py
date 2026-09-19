"""The surface: the only module in the repo that touches a browser.

Owns: the Surface protocol (observe, resolve, act, read, holds, fingerprint,
trace) and its one implementation, PlaywrightSurface. Frame traversal,
candidate resolution in ranked order, ARIA snapshots, screenshots with
sensitive fields blurred before capture, and the facts about an element the
compiler needs to describe it again later.

Does not own: deciding what to do, or anything about what an artifact means.
It is handed a TargetRef and returns which candidate found it; it is handed
an action and reports what it touched. Nothing Playwright-specific crosses
out of it: only the Pydantic models, ints, strings and bools in types.py.

Governed by ADR-0006 (surface abstraction) and ADR-0002 (locator strategy).
"""

from bankbot.surface.browser import headed_requested, open_page
from bankbot.surface.playwright_surface import PlaywrightSurface, hash_aria
from bankbot.surface.types import (
    ActionFailed,
    ActResult,
    ElementFacts,
    FrameNotFound,
    FrameSnapshot,
    Observation,
    ReadResult,
    Surface,
    TargetNotFound,
)

__all__ = [
    "ActResult",
    "ActionFailed",
    "ElementFacts",
    "FrameNotFound",
    "FrameSnapshot",
    "Observation",
    "PlaywrightSurface",
    "ReadResult",
    "Surface",
    "TargetNotFound",
    "hash_aria",
    "headed_requested",
    "open_page",
]
