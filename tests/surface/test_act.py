import pytest
from playwright.sync_api import Page

from bankbot.schemas.artifact import ActionType, StateAssertion
from bankbot.surface import ActionFailed, PlaywrightSurface
from tests.surface.conftest import arm_faults, bbox, css, label, main_frame, role, target, text

MAIN = ["main"]
MEMBER_ID = target(label("Member ID"), frame_path=MAIN)
SEARCH = target(role("button:Search"), frame_path=MAIN)


def test_type_and_click_inside_the_iframe_submit_the_search_and_navigate_the_top_page(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    typed = surface.act(ActionType.TYPE, MEMBER_ID, "M-100")
    clicked = surface.act(ActionType.CLICK, SEARCH, None)

    assert typed.candidate_index == 0
    assert clicked.candidate_index == 0
    assert clicked.element is not None
    assert (clicked.element.role, clicked.element.name) == ("button", "Search")
    assert clicked.element.frame_path == MAIN
    assert surface.holds(
        StateAssertion(
            description="results page",
            url_pattern="/members/results$",
            text_visible="Dana Whitfield",
        )
    )


def test_act_returns_element_facts_whose_css_path_resolves_back_to_the_same_element(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    result = surface.act(ActionType.TYPE, MEMBER_ID, "M-101")

    facts = result.element
    assert facts is not None
    assert facts.role == "textbox"
    assert facts.name == "Member ID"
    assert facts.label == "Member ID"
    assert facts.text is None
    assert facts.css_path.startswith("body > ")
    assert surface.resolve(target(css(facts.css_path), frame_path=MAIN)) == 0
    assert main_frame(signed_in_page).locator(facts.css_path).input_value() == "M-101"


def test_element_facts_bbox_is_in_top_document_pixels_even_inside_the_iframe(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    result = surface.act(ActionType.TYPE, MEMBER_ID, "M-100")

    assert result.element is not None
    x, y, w, h = result.element.bbox
    playwright_box = main_frame(signed_in_page).get_by_label("Member ID").bounding_box()
    assert playwright_box is not None
    assert (x, y, w, h) == pytest.approx(
        (
            playwright_box["x"],
            playwright_box["y"],
            playwright_box["width"],
            playwright_box["height"],
        ),
        abs=2,
    )


def test_click_on_a_link_reports_link_facts_with_its_text(
    surface: PlaywrightSurface, signed_in_page: Page, base_url: str
) -> None:
    signed_in_page.goto(f"{base_url}/members/results?member_id=M-100")

    result = surface.act(ActionType.CLICK, target(role("link:Dana Whitfield")), None)

    assert result.element is not None
    assert result.element.role == "link"
    assert result.element.text == "Dana Whitfield"
    assert result.element.label is None
    assert signed_in_page.url.endswith("/members/M-100")


def test_navigate_resolves_a_relative_url_against_the_current_page(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    result = surface.act(ActionType.NAVIGATE, None, "/members/M-100")

    assert result.candidate_index is None
    assert result.element is None
    assert signed_in_page.url.endswith("/members/M-100")
    assert signed_in_page.get_by_role("heading", name="Dana Whitfield").is_visible()


def test_read_returns_the_savings_balance_cell_text(
    surface: PlaywrightSurface, signed_in_page: Page, base_url: str
) -> None:
    signed_in_page.goto(f"{base_url}/members/M-100")

    result = surface.read(target(css("table.accounts > tbody > tr:nth-of-type(1) > td")))

    assert result.candidate_index == 0
    assert result.text == "$4,242.00"


def test_facts_for_a_table_cell_name_its_row_header(
    surface: PlaywrightSurface, signed_in_page: Page, base_url: str
) -> None:
    signed_in_page.goto(f"{base_url}/members/M-100")
    savings_cell = target(css("table.accounts > tbody > tr:nth-of-type(1) > td"))

    result = surface.act(ActionType.CLICK, savings_cell, None)

    assert result.element is not None
    assert result.element.role == "cell"
    assert result.element.text == "$4,242.00"
    assert result.element.row_header == "Savings balance"


def test_facts_in_a_row_without_a_header_cell_have_no_row_header(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    result = surface.act(ActionType.TYPE, MEMBER_ID, "M-100")

    assert result.element is not None
    assert result.element.row_header is None


def test_bbox_click_and_type_land_on_the_element_under_the_point(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    box = main_frame(signed_in_page).get_by_label("Member ID").bounding_box()
    assert box is not None
    point = bbox(f"{box['x']},{box['y']},{box['width']},{box['height']}")

    result = surface.act(ActionType.TYPE, target(text("nope"), point, frame_path=MAIN), "M-103")

    assert result.candidate_index == 1
    assert result.element is None
    assert main_frame(signed_in_page).get_by_label("Member ID").input_value() == "M-103"


def test_select_picks_an_option_by_label_and_falls_back_to_its_value(
    surface: PlaywrightSurface, page: Page
) -> None:
    page.set_content(
        "<label for=kind>Account type</label><select id=kind name=kind>"
        "<option value=sav>Savings</option><option value=chk>Checking</option>"
        "</select>"
    )
    kind = target(label("Account type"))

    by_label = surface.act(ActionType.SELECT, kind, "Checking")
    assert page.locator("select").input_value() == "chk"
    assert by_label.element is not None
    assert by_label.element.role == "combobox"

    surface.act(ActionType.SELECT, kind, "sav", timeout_ms=1000)
    assert page.locator("select").input_value() == "sav"


def test_click_blocked_by_the_unknown_dialog_overlay_raises_action_failed(
    surface: PlaywrightSurface, signed_in_page: Page, base_url: str, faults: None
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=1)
    signed_in_page.goto(f"{base_url}/members/M-100")

    with pytest.raises(ActionFailed) as raised:
        surface.act(ActionType.CLICK, target(role("link:New search")), None, timeout_ms=1000)

    assert raised.value.action == "click"
    assert "M-100" in raised.value.observed
    assert len(raised.value.reason) <= 200
    assert "\n" not in raised.value.reason
    assert signed_in_page.url.endswith("/members/M-100")


def test_actions_the_surface_does_not_perform_are_rejected_before_touching_the_page(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    with pytest.raises(ValueError, match="extract"):
        surface.act(ActionType.EXTRACT, MEMBER_ID, None)
    with pytest.raises(ValueError, match="needs a target"):
        surface.act(ActionType.CLICK, None, None)
