from playwright.sync_api import Page

from bankbot.surface import HumanAction
from bankbot.surface.human import HumanWatcher


def test_watcher_reports_clicks_inputs_and_navigations_without_the_typed_value(
    page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    seen: list[HumanAction] = []
    watcher = HumanWatcher(page)
    watcher.start(seen.append)
    page.get_by_label("Username").fill("teller")
    page.get_by_label("Password").fill("teller-demo-password")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/members/search")
    page.wait_for_timeout(300)

    described = [(action.kind, action.description) for action in seen]
    assert ("input", "input 'Username'") in described
    assert ("input", "input 'Password'") in described
    assert ("click", "button 'Sign in'") in described
    assert any(a.kind == "navigate" and a.frame_path == [] for a in seen), "the top page moved"
    assert any(a.kind == "navigate" and a.frame_path == ["main"] for a in seen), "the iframe loaded"
    assert "teller-demo-password" not in repr(seen)


def test_nothing_is_reported_after_the_watcher_stops(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    seen: list[HumanAction] = []
    watcher = HumanWatcher(page)
    watcher.start(seen.append)
    watcher.stop()
    page.get_by_label("Username").fill("teller")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_timeout(300)
    assert seen == []
