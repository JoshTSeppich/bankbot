"""Opening and closing the browser: the one place Chromium is launched.

Owns: open_page, a context manager that yields a Playwright Page and tears
everything down afterwards. The CLI and the operator process use it so that
neither imports Playwright (ADR-0006). HEADED=1 shows the window, which the
handoff needs because a human takes over the same window.

Does not own: what happens on the page (PlaywrightSurface).

Governed by ADR-0006 (surface abstraction).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import Page, sync_playwright

HEADED_ENV = "HEADED"


def headed_requested() -> bool:
    """Read HEADED once so the CLI, the tests and the docs agree on how a window is asked for."""
    return os.environ.get(HEADED_ENV, "") not in ("", "0", "false")


@contextmanager
def open_page(headed: bool | None = None) -> Iterator[Page]:
    """Yield one page in a fresh context and close the browser on the way out, even on error."""
    show = headed_requested() if headed is None else headed
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not show)
        try:
            yield browser.new_context().new_page()
        finally:
            browser.close()
