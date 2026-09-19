"""Recording what a person does in the browser while they hold control.

Owns: the script that reports clicks and inputs from every frame, and the
translation of those reports into HumanAction records. Typed values are
never carried: an input event reports only that a value changed and which
field it was. Navigations come from Playwright's own framenavigated event,
so a page the person moved to is recorded even when no script ran on it.

Does not own: when recording is on (the controller decides) or where the
records go (the controller writes them to evidence).

Governed by ADR-0004 (control transfer).
"""

from collections.abc import Callable
from typing import Any

from playwright.sync_api import Frame, Page

from bankbot.surface.frames import frame_segment, live_children
from bankbot.surface.types import HumanAction

BINDING_NAME = "__bankbotHumanAction"

# Installed in every frame. Reports a short description of what was clicked
# or edited; never the value typed. Guarded so a reloaded frame does not
# install a second pair of listeners.
HUMAN_ACTION_JS = f"""
(() => {{
  if (window.__bankbotWatching) return;
  window.__bankbotWatching = true;
  const describe = (el) => {{
    if (!el || !el.tagName) return "page";
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || tag;
    const fromLabel = el.labels && el.labels[0] && el.labels[0].innerText;
    const fromText = tag === "input" ? "" : (el.innerText || "");
    const label = (el.getAttribute("aria-label") || fromLabel || el.getAttribute("name")
      || fromText).trim();
    return role + (label ? " '" + label.slice(0, 60) + "'" : "");
  }};
  const report = (kind) => (e) => window.{BINDING_NAME}({{kind, description: describe(e.target)}});
  document.addEventListener("click", report("click"), true);
  document.addEventListener("change", report("input"), true);
}})();
"""

OnHumanAction = Callable[[HumanAction], None]


class HumanWatcher:
    """Turns browser events into HumanAction records while a person is in control.

    The binding and the init script are installed once per page and stay;
    a flag decides whether reports are passed on. Playwright offers no way
    to remove a binding, and re-installing on every handoff would double
    the listeners.
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self._callback: OnHumanAction | None = None
        self._installed = False

    def start(self, callback: OnHumanAction) -> None:
        """Begin passing reports on; installs the hooks on first use."""
        self._callback = callback
        if not self._installed:
            self._page.expose_binding(BINDING_NAME, self._on_report)
            self._page.add_init_script(HUMAN_ACTION_JS)
            self._page.on("framenavigated", self._on_navigated)
            self._installed = True
        # add_init_script only reaches documents loaded from now on; the ones
        # already open get the script directly.
        for frame in self._page.frames:
            try:
                frame.evaluate(HUMAN_ACTION_JS)
            except Exception:
                # A frame mid-navigation cannot be scripted; the init script reaches it on load.
                continue

    def stop(self) -> None:
        """Stop passing reports on; from here on the engine is acting, not a person."""
        self._callback = None

    def _on_report(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        if self._callback is None:
            return
        frame: Frame = source["frame"]
        self._callback(
            HumanAction(
                kind=payload.get("kind", "click"),
                description=str(payload.get("description", "")),
                frame_path=_frame_path(frame),
                url=frame.url,
            )
        )

    def _on_navigated(self, frame: Frame) -> None:
        if self._callback is None:
            return
        self._callback(
            HumanAction(
                kind="navigate",
                description="page changed" if frame.parent_frame is None else "frame changed",
                frame_path=_frame_path(frame),
                url=frame.url,
            )
        )


def _frame_path(frame: Frame) -> list[str]:
    """The frame_path observe() would give, so a person's action and the artifact agree."""
    path: list[str] = []
    current = frame
    while current.parent_frame is not None:
        siblings = live_children(current.parent_frame)
        path.insert(0, frame_segment(current, siblings.index(current)))
        current = current.parent_frame
    return path
