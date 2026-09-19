from playwright.sync_api import Page

from bankbot.policy import Policy
from bankbot.schemas import ScreenElement
from bankbot.surface import distance, screen_fingerprint


def signed_in(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill("teller")
    page.get_by_label("Password").fill("teller-demo-password")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/members/search")


def test_the_search_screen_lists_the_iframe_form_in_tab_order(page: Page, base_url: str) -> None:
    signed_in(page, base_url)
    sequence = screen_fingerprint(page)
    roles = [element.role for element in sequence]
    # The top document has no focusable control; the form inside the iframe has two.
    assert roles == ["textbox", "button"]
    assert all(element.landmark == "none" for element in sequence)


def test_variant_b_is_within_the_policy_distance_on_the_search_screen(
    page: Page, base_url: str, policy: Policy
) -> None:
    signed_in(page, base_url)
    recorded = screen_fingerprint(page)
    page.goto(f"{base_url}/b/members/search")
    page.wait_for_url("**/b/members/search")
    actual = screen_fingerprint(page)
    edits = distance(recorded, actual)
    # The button is renamed, which a tab sequence does not see: same role, same state.
    assert edits <= policy.fingerprint.max_distance_ratio * max(len(recorded), len(actual))


def test_a_wholly_different_screen_is_far_outside_the_policy_distance(
    page: Page, base_url: str, policy: Policy
) -> None:
    page.goto(f"{base_url}/login")
    login = screen_fingerprint(page)
    signed_in(page, base_url)
    page.goto(f"{base_url}/members/M-100")
    detail = screen_fingerprint(page)
    edits = distance(login, detail)
    assert edits > policy.fingerprint.max_distance_ratio * max(len(login), len(detail))


def test_distance_counts_insertions_deletions_and_substitutions() -> None:
    a = [ScreenElement(role="textbox", state=0, landmark="none")]
    b = [
        ScreenElement(role="textbox", state=0, landmark="none"),
        ScreenElement(role="button", state=0, landmark="none"),
    ]
    c = [ScreenElement(role="link", state=0, landmark="none")]
    assert distance(a, a) == 0
    assert distance(a, b) == 1 and distance(b, a) == 1
    assert distance(a, c) == 1
    assert distance([], b) == 2


def test_a_control_state_changes_the_sequence_not_its_length() -> None:
    plain = [ScreenElement(role="checkbox", state=0, landmark="main")]
    checked = [ScreenElement(role="checkbox", state=1 << 3, landmark="main")]
    assert distance(plain, checked) == 1
