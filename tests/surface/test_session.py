import threading
from pathlib import Path

import pytest
from playwright.sync_api import Page

from bankbot.schemas.artifact import ActionType, StateAssertion
from bankbot.surface import PlaywrightSurface, SessionLost, open_page
from tests.surface.conftest import label, role, target

SHORT_MS = 400


def test_every_way_of_touching_a_closed_page_reports_session_lost(
    surface: PlaywrightSurface, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    page.close()
    with pytest.raises(SessionLost):
        surface.observe()
    with pytest.raises(SessionLost):
        surface.resolve(target(label("Username")), SHORT_MS)
    with pytest.raises(SessionLost):
        surface.read(target(label("Username")), SHORT_MS)
    with pytest.raises(SessionLost):
        surface.act(ActionType.CLICK, target(role("button:Sign in")), None, SHORT_MS)
    with pytest.raises(SessionLost):
        surface.idle(SHORT_MS)


def test_a_state_assertion_on_a_closed_page_reports_session_lost_rather_than_false(
    surface: PlaywrightSurface, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    page.close()
    with pytest.raises(SessionLost):
        surface.holds(StateAssertion(description="the search screen", url_pattern="/members"))


def test_stopping_a_trace_on_a_closed_page_writes_nothing_and_does_not_raise(
    surface: PlaywrightSurface, page: Page, base_url: str, tmp_path: Path
) -> None:
    page.goto(f"{base_url}/login")
    surface.start_trace()
    page.context.close()
    surface.stop_trace(tmp_path / "trace.zip")
    assert not (tmp_path / "trace.zip").exists()


def test_leaving_open_page_does_not_raise_when_the_browser_is_already_gone() -> None:
    # On its own thread because the session browser holds this one's Playwright driver.
    escaped: list[BaseException] = []

    def close_the_browser_from_inside() -> None:
        try:
            with open_page(headed=False) as page:
                browser = page.context.browser
                assert browser is not None
                browser.close()
        except BaseException as error:
            escaped.append(error)

    thread = threading.Thread(target=close_the_browser_from_inside)
    thread.start()
    thread.join()
    assert escaped == []
