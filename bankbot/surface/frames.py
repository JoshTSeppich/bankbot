"""Frame traversal: a frame_path to a live frame, and every frame with its path.

Owns: the naming rule for a frame_path segment (the frame's name, or its
index among its siblings when it has none), the depth-first walk observe()
uses, and the lookup resolve() uses.

Does not own: anything inside a frame.

Governed by ADR-0002: frame_path is part of a target because legacy apps put
the form in an iframe, and a locator that ignores the frame never resolves.
"""

from playwright.sync_api import Frame, Page

from bankbot.surface.types import FrameNotFound


def frame_segment(frame: Frame, index: int) -> str:
    """Name one frame the way a frame_path spells it.

    Unnamed frames get "frame[<index>]" so they are still addressable. That
    is positional and fragile, which is exactly what an artifact should
    reveal about a target inside an unnamed frame.
    """
    return frame.name or f"frame[{index}]"


def walk_frames(page: Page) -> list[tuple[list[str], Frame]]:
    """Every frame on the page with its path, top document first, depth-first."""
    found: list[tuple[list[str], Frame]] = [([], page.main_frame)]
    _collect_children(page.main_frame, [], found)
    return found


def _collect_children(frame: Frame, path: list[str], into: list[tuple[list[str], Frame]]) -> None:
    for index, child in enumerate(live_children(frame)):
        child_path = [*path, frame_segment(child, index)]
        into.append((child_path, child))
        _collect_children(child, child_path, into)


def live_children(frame: Frame) -> list[Frame]:
    """The frame's current children.

    After a reload Playwright's child_frames still lists the previous
    document's iframe next to the new one, and a snapshot of it fails with
    "frame was detached". Only live frames are addressable.
    """
    return [child for child in frame.child_frames if not child.is_detached()]


def frame_for_path(page: Page, frame_path: list[str]) -> Frame:
    """Walk a frame_path from the top document down; FrameNotFound names what exists instead."""
    frame = page.main_frame
    for segment in frame_path:
        children = live_children(frame)
        match = None
        for index, child in enumerate(children):
            if frame_segment(child, index) == segment:
                match = child
                break
        if match is None:
            available = ["/".join(path) for path, _ in walk_frames(page) if path]
            raise FrameNotFound(frame_path=frame_path, available=available)
        frame = match
    return frame
