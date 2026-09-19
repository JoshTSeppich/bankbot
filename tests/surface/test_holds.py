from playwright.sync_api import Page

from bankbot.schemas.artifact import StateAssertion
from bankbot.surface import PlaywrightSurface
from tests.surface.conftest import role, target


def test_holds_waits_for_url_and_text_together(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    signed_in_page.evaluate("setTimeout(() => { location.href = '/members/M-100'; }, 400)")
    detail = StateAssertion(
        description="member detail",
        url_pattern=r"/members/M-100$",
        text_visible="Savings balance",
    )

    assert surface.holds(detail, timeout_ms=3000)


def test_holds_is_false_when_only_one_of_two_conditions_is_met(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    half = StateAssertion(
        description="right url, wrong text",
        url_pattern=r"/members/search$",
        text_visible="Savings balance",
    )

    assert not surface.holds(half, timeout_ms=300)


def test_holds_returns_false_instead_of_raising_on_timeout(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    missing_control = StateAssertion(
        description="a button that is not there",
        target_visible=target(role("button:Find member"), frame_path=["main"]),
    )
    missing_frame = StateAssertion(
        description="a frame that is not there",
        target_visible=target(role("button:Search"), frame_path=["sidebar"]),
    )

    assert not surface.holds(missing_control, timeout_ms=300)
    assert not surface.holds(missing_frame, timeout_ms=300)


def test_holds_sees_a_control_inside_the_iframe(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    search_form = StateAssertion(
        description="search form is up",
        text_visible="Member ID",
        target_visible=target(role("button:Search"), frame_path=["main"]),
    )

    assert surface.holds(search_form)
