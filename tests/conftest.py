"""Shared browser fixtures: one demo app and one Chromium per test session.

Playwright's sync API allows one running driver per thread, so every suite
that needs a page shares these instead of starting its own.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from bankbot.policy import Policy, load_policy
from bankbot.target import ServerHandle, create_app, start_server


@pytest.fixture(scope="session")
def server() -> Iterator[ServerHandle]:
    handle = start_server(create_app())
    yield handle
    handle.stop()


@pytest.fixture(scope="session")
def base_url(server: ServerHandle) -> str:
    return server.base_url


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch()
    yield browser
    browser.close()
    playwright.stop()


@pytest.fixture
def page(browser: Browser, base_url: str) -> Iterator[Page]:
    # Faults are process-wide on the shared server; clearing them here keeps tests independent.
    httpx.delete(f"{base_url}/admin/faults").raise_for_status()
    context = browser.new_context()
    yield context.new_page()
    context.close()


@pytest.fixture
def policy() -> Policy:
    return load_policy()


FIXTURE = Path(__file__).parent / "fixtures" / "lookup_savings_balance.json"


@pytest.fixture
def capability_json() -> dict[str, Any]:
    """The hand-written capability as a plain dict, so a test can break one field on purpose."""
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data
