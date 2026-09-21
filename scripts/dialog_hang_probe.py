"""What an unanswered native dialog does to Playwright, measured rather than assumed.

This produced the table in ADR-0003. Each call gets its own process, because
a call that never returns takes its process with it and there is no timeout
argument to shorten the wait; the parent kills the child at 20 seconds and
reports that as the answer. Run it with no arguments. The one-argument form
is the child.

Not part of the package, the tests or CI. It is the working I kept so the
table can be re-measured on another Playwright.
"""

import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

BUDGET_S = 20
SCREENSHOT_TIMEOUT_MS = 2000
DIALOG_SETTLE_S = 1.0
# "control" makes the same call with no dialog open, so a slow answer cannot
# be blamed on the dialog.
PROBES = ("control", "title", "count", "screenshot", "aria")


def probe(which: str) -> None:
    """Hold an alert open with a listener that answers nothing, then make exactly one call."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content("<html><body><h1>hi</h1></body></html>")
        if which != "control":
            page.on("dialog", lambda dialog: None)
            page.evaluate("setTimeout(() => alert('held'), 50)")
            time.sleep(DIALOG_SETTLE_S)
        calls = {
            "control": page.title,
            "title": page.title,
            "count": page.locator("h1").count,
            "screenshot": lambda: page.screenshot(timeout=SCREENSHOT_TIMEOUT_MS),
            "aria": page.locator("body").aria_snapshot,
        }
        started = time.monotonic()
        try:
            calls[which]()
        except Exception as raised:
            print(f"RAISED after {time.monotonic() - started:.2f}s: {type(raised).__name__}")
        else:
            print(f"RETURNED after {time.monotonic() - started:.2f}s")
        browser.close()


def drive() -> None:
    """One child per call, killed at the budget; one line each, saying what happened and when."""
    for which in PROBES:
        started = time.monotonic()
        try:
            child = subprocess.run(
                [sys.executable, "-u", __file__, which],
                capture_output=True,
                text=True,
                timeout=BUDGET_S,
                check=False,
            )
            said = (child.stdout or child.stderr).strip().splitlines()[-1]
        except subprocess.TimeoutExpired:
            said = f"NEVER RETURNED (killed at {BUDGET_S}s wall clock)"
        print(f"{which:11} {time.monotonic() - started:5.1f}s  {said}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        probe(sys.argv[1])
    else:
        drive()
