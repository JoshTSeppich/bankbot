from pathlib import Path

from playwright.sync_api import Page

from bankbot.surface import PlaywrightSurface, Surface
from tests.surface.conftest import arm_faults, sign_in

DIALOG_TEXT = "Scheduled maintenance tonight"


def test_playwright_surface_satisfies_the_surface_protocol(surface: PlaywrightSurface) -> None:
    as_protocol: Surface = surface
    assert as_protocol is surface


def test_observe_top_frame_comes_first_with_an_empty_frame_path(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    observation = surface.observe()

    assert observation.url.endswith("/members/search")
    assert observation.title == "Legacy Core Teller"
    assert observation.frames[0].frame_path == []
    assert "Signed in as teller" in observation.frames[0].aria
    assert observation.screenshot is None
    assert observation.dialog_text is None


def test_observe_labels_the_iframe_snapshot_with_its_frame_path(
    surface: PlaywrightSurface, signed_in_page: Page
) -> None:
    observation = surface.observe()

    assert [frame.frame_path for frame in observation.frames] == [[], ["main"]]
    assert 'textbox "Member ID"' in observation.frames[1].aria
    assert 'button "Search"' in observation.frames[1].aria
    assert "Member ID" not in observation.frames[0].aria


def test_masked_screenshot_differs_from_an_unmasked_one_on_the_login_page(
    page: Page, base_url: str, tmp_path: Path
) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Password").fill("not-a-real-password")
    masked_path = tmp_path / "masked.png"
    unmasked_path = tmp_path / "unmasked.png"

    masked = PlaywrightSurface(page, mask_selectors=["input[type=password]"]).observe(masked_path)
    unmasked = PlaywrightSurface(page, mask_selectors=[]).observe(unmasked_path)

    assert masked.screenshot == str(masked_path)
    assert unmasked.screenshot == str(unmasked_path)
    assert masked_path.read_bytes() != unmasked_path.read_bytes()


def test_dialog_text_is_reported_when_the_unknown_dialog_fault_is_armed(
    surface: PlaywrightSurface, signed_in_page: Page, base_url: str, faults: None
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=1)
    signed_in_page.goto(f"{base_url}/members/search")

    observation = surface.observe()

    assert observation.dialog_text is not None
    assert DIALOG_TEXT in observation.dialog_text
    assert 'dialog "System notice"' in observation.frames[0].aria


def test_fingerprint_reports_title_version_and_the_same_tab_sequence_for_the_same_screen(
    surface: PlaywrightSurface, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    first = surface.fingerprint()
    page.goto(f"{base_url}/login")
    second = surface.fingerprint()
    assert first.title == "Legacy Core Teller"
    assert first.version == "7.2.1"
    assert first.screen_fingerprints["current"] == second.screen_fingerprints["current"]
    roles = [element.role for element in first.screen_fingerprints["current"]]
    assert roles == ["textbox", "textbox", "button"], "username, password, sign in, in tab order"


def test_variant_b_changes_the_fingerprint_version(
    surface: PlaywrightSurface, page: Page, base_url: str
) -> None:
    sign_in(page, base_url, prefix="/b")

    fingerprint = surface.fingerprint()

    assert fingerprint.title == "Legacy Core Teller"
    assert fingerprint.version == "7.3.0"


def test_trace_is_written_where_the_caller_asks(
    surface: PlaywrightSurface, signed_in_page: Page, tmp_path: Path
) -> None:
    surface.start_trace()
    signed_in_page.reload()
    trace = tmp_path / "trace.zip"

    surface.stop_trace(trace)

    assert trace.exists()
    assert trace.stat().st_size > 0
