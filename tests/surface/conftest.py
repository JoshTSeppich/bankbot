from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Frame, Page

from bankbot.schemas.artifact import Candidate, LocatorStrategy, TargetRef
from bankbot.surface import PlaywrightSurface

USERNAME = "teller"
PASSWORD = "teller-demo-password"
MASK_SELECTORS = ["input[type=password]"]


def candidate(strategy: LocatorStrategy, value: str) -> Candidate:
    return Candidate(strategy=strategy, value=value, confidence=0.5, reasoning="test")


def target(*candidates: Candidate, frame_path: list[str] | None = None) -> TargetRef:
    return TargetRef(candidates=list(candidates), frame_path=frame_path or [])


def role(value: str) -> Candidate:
    return candidate(LocatorStrategy.ROLE_NAME, value)


def label(value: str) -> Candidate:
    return candidate(LocatorStrategy.LABEL, value)


def text(value: str) -> Candidate:
    return candidate(LocatorStrategy.TEXT, value)


def css(value: str) -> Candidate:
    return candidate(LocatorStrategy.CSS_STRUCTURAL, value)


def bbox(value: str) -> Candidate:
    return candidate(LocatorStrategy.BBOX, value)


def main_frame(page: Page) -> Frame:
    frame = page.frame("main")
    assert frame is not None
    return frame


def sign_in(page: Page, base_url: str, prefix: str = "") -> None:
    page.goto(f"{base_url}{prefix}/login")
    page.get_by_label("Username").fill(USERNAME)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"**{prefix}/members/search")


def arm_faults(base_url: str, **faults: int) -> None:
    httpx.post(f"{base_url}/admin/faults", json=faults).raise_for_status()


@pytest.fixture
def surface(page: Page) -> PlaywrightSurface:
    return PlaywrightSurface(page, mask_selectors=MASK_SELECTORS)


@pytest.fixture
def signed_in_page(page: Page, base_url: str) -> Page:
    sign_in(page, base_url)
    return page


@pytest.fixture
def faults(base_url: str) -> Iterator[None]:
    # Disarm after the test as well as before (tests/conftest.py), so a failing test
    # never leaves a fault armed for a suite that does not use this fixture.
    yield
    httpx.delete(f"{base_url}/admin/faults").raise_for_status()
