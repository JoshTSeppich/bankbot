import pytest
from playwright.sync_api import Page

from bankbot.surface import FrameNotFound, PlaywrightSurface, TargetNotFound
from tests.surface.conftest import bbox, css, label, role, target, text

MAIN = ["main"]


def test_resolve_returns_index_zero_when_the_first_candidate_matches(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    assert surface.resolve(target(role("button:Search"), text("Search"), frame_path=MAIN)) == 0


def test_resolve_falls_through_to_a_later_candidate_and_reports_its_index(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    # "Find member" is the variant B name; on variant A only the weaker text candidate matches.
    drifted = target(role("button:Find member"), text("Search"), frame_path=MAIN)

    assert surface.resolve(drifted, timeout_ms=1000) == 1


def test_resolve_raises_target_not_found_listing_every_candidate_tried(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    missing = target(role("button:Find member"), label("Account number"), frame_path=MAIN)

    with pytest.raises(TargetNotFound) as raised:
        surface.resolve(missing, timeout_ms=600)

    assert raised.value.tried == [
        "role_name 'button:Find member': not visible within 300ms",
        "label 'Account number': not visible within 300ms",
    ]
    assert 'textbox "Member ID"' in raised.value.observed


def test_resolve_rejects_an_ambiguous_candidate_and_moves_on(
    surface: PlaywrightSurface, signed_in_page: Page, base_url: str
) -> None:
    signed_in_page.goto(f"{base_url}/members/M-100")

    with pytest.raises(TargetNotFound) as raised:
        surface.resolve(target(css("table.accounts td")), timeout_ms=300)
    assert raised.value.tried == ["css_structural 'table.accounts td': ambiguous: 2 matches"]

    assert surface.resolve(target(css("table.accounts td"), role("rowheader:Savings balance"))) == 1


def test_resolve_gives_each_candidate_at_least_a_quarter_second(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    with pytest.raises(TargetNotFound) as raised:
        surface.resolve(target(text("nope"), text("nor this"), frame_path=MAIN), timeout_ms=10)

    assert all(line.endswith("not visible within 250ms") for line in raised.value.tried)


def test_frame_path_that_does_not_exist_raises_frame_not_found(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    with pytest.raises(FrameNotFound) as raised:
        surface.resolve(target(role("button:Search"), frame_path=["sidebar"]))

    assert raised.value.frame_path == ["sidebar"]
    assert raised.value.available == ["main"]


def test_bbox_candidate_is_a_last_resort_that_only_needs_to_fit_the_viewport(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    assert surface.resolve(target(text("nope"), bbox("10,10,50,20"))) == 1

    with pytest.raises(TargetNotFound) as raised:
        surface.resolve(target(bbox("5000,5000,10,10"), bbox("not-a-box")))
    assert raised.value.tried == [
        "bbox '5000,5000,10,10': outside the viewport",
        "bbox 'not-a-box': malformed: expected x,y,w,h",
    ]
